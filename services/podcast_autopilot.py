"""Persistent, opt-in podcast release plans with injectable publishing adapters.

Uploads are private; publication is a separate, due-time action. A failed or
interrupted upload is never retried blindly because providers do not offer a
deduplication key. Updating privacy on a known provider ID is safe to retry.
No provider calls or database imports happen when this module is imported.
"""

import asyncio
import copy
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DESTINATIONS = {"buzzsprout": "Podcast feeds · Buzzsprout", "youtube": "YouTube"}
STOPPED = {"paused", "cancelled", "completed", "awaiting_approval"}
MAX_ATTEMPTS = 3


class PlanError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise PlanError("Choose a valid release date with a timezone.")


def validate_config(config):
    if not isinstance(config, dict):
        raise PlanError("A release plan must be an object.")
    if config.get("mode") not in ("review", "automatic"):
        raise PlanError("Choose review or automatic publishing.")
    destinations = config.get("destinations")
    if (not isinstance(destinations, list) or not destinations
            or any(not isinstance(d, str) or d not in DESTINATIONS for d in destinations)):
        raise PlanError("Select Buzzsprout, YouTube, or both.")
    return {
        "mode": config["mode"],
        "destinations": sorted(set(destinations)),
        "scheduled_at": iso(timestamp(config.get("scheduled_at"))),
    }


class PlanStore:
    """Atomic JSON files and per-project advisory locks, including across workers."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, project_id, suffix=".json"):
        try:
            identifier = str(uuid.UUID(str(project_id)))
        except (ValueError, TypeError, AttributeError):
            raise PlanError("Invalid project ID.")
        return self.directory / (identifier + suffix)

    def load(self, project_id):
        path = self.path(project_id)
        return json.loads(path.read_text()) if path.exists() else None

    def _write(self, path, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".release-", dir=str(self.directory))
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, plan):
        self._write(self.path(plan["project_id"]), plan)

    def control(self, project_id):
        path = self.path(project_id, ".control.json")
        return json.loads(path.read_text()) if path.exists() else None

    def set_control(self, project_id, status):
        self._write(self.path(project_id, ".control.json"), {"status": status})

    def clear_control(self, project_id):
        self.path(project_id, ".control.json").unlink(missing_ok=True)

    def project_ids(self):
        if not self.directory.exists():
            return []
        return [p.stem for p in self.directory.glob("*.json") if not p.name.endswith(".control.json")]

    @contextmanager
    def lock(self, project_id):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path(project_id, ".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PlanError("A release action is in progress. Try again shortly.", 409)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class PodcastAutopilot:
    def __init__(self, store, inspect_project, perform, record_outcome, clock=time.time):
        self.store = store
        self.inspect_project = inspect_project
        self.perform = perform
        self.record_outcome = record_outcome
        self.clock = clock

    def event(self, plan, message):
        plan["updated_at"] = iso(self.clock())
        plan["activity"] = (plan.get("activity", []) + [{"at": plan["updated_at"], "message": message}])[-40:]

    def readiness(self, project, config, plan=None):
        destinations = config.get("destinations", [])
        checks = []

        def check(key, label, ok, detail):
            checks.append({"id": key, "label": label, "ok": bool(ok), "detail": detail})

        check("processing", "Episode prepared", project.get("status") in ("review", "scheduled", "closing", "closed"),
              "Finish episode preparation before the release can run.")
        check("title", "Episode title", bool((project.get("title") or "").strip()) and project.get("title") != "Untitled Episode",
              "Give this episode a finished title.")
        check("show_notes", "Show notes", bool((project.get("show_notes") or "").strip()),
              "Generate or write show notes before publishing.")
        for destination in destinations:
            check(destination + "_connection", DESTINATIONS[destination], project.get("connections", {}).get(destination),
                  "Connect {} in Settings.".format(DESTINATIONS[destination]))
            existing = project.get("existing", {}).get(destination)
            owned = any(a.get("destination") == destination and a.get("provider_id") == existing
                        for a in (plan or {}).get("actions", [])) if existing else False
            check(destination + "_ownership", "No duplicate " + destination + " release", not existing or owned,
                  "This episode already has a {} upload. Use its existing release controls.".format(destination))
            check(destination + "_media", "Podcast audio" if destination == "buzzsprout" else "Episode video",
                  project.get("audio_ready" if destination == "buzzsprout" else "video_ready") or owned,
                  "Prepare the assembled MP3." if destination == "buzzsprout" else "Prepare an actual video file for YouTube.")
        check("legacy_queue", "One release owner", not project.get("legacy_queued"),
              "This episode is already in the existing release queue. Cancel that release before enabling a new plan.")
        if plan:
            check("prepared_content", "Prepared upload matches episode", not self.prepared_content_changed(project, plan),
                  "Episode content changed after private upload. Reconcile the uploaded episode in the provider before releasing it.")
        blockers = [c["detail"] for c in checks if not c["ok"]]
        return {"ready": not blockers, "checks": checks, "blockers": blockers}

    def fingerprint(self, project):
        content = {k: project.get(k) for k in ("title", "show_notes", "audio_path", "video_path", "audio_version", "video_version")}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def prepared_content_changed(self, project, plan):
        current = self.fingerprint(project)
        return any(a["kind"] == "upload" and a["status"] == "succeeded"
                   and a.get("content_fingerprint") != current for a in plan["actions"])

    async def reconcile(self, project_id, plan):
        for action in plan["actions"]:
            if action["status"] == "succeeded" and not action.get("projected"):
                try:
                    await self.record_outcome(project_id, copy.deepcopy(action), plan)
                    action["projected"] = True
                    self.store.save(plan)
                except Exception:
                    # The receipt stays durable and is retried even after the
                    # release completes. Never repeat a provider call to sync DB.
                    pass

    async def describe(self, project_id, candidate=None):
        project = await self.inspect_project(project_id)
        plan = self.store.load(project_id)
        config = validate_config(candidate) if candidate is not None else (plan or {"destinations": []})
        view = copy.deepcopy(plan)
        if view and self.store.control(project_id):
            view["status"] = self.store.control(project_id)["status"]
        return {"project_id": project_id, "plan": view,
                "readiness": self.readiness(project, config, plan),
                "available_destinations": [{"id": key, "label": label,
                    "connected": bool(project.get("connections", {}).get(key))} for key, label in DESTINATIONS.items()]}

    async def configure(self, project_id, config):
        config = validate_config(config)
        if timestamp(config["scheduled_at"]) <= self.clock():
            raise PlanError("Choose a release time in the future.")
        with self.store.lock(project_id):
            project = await self.inspect_project(project_id)
            old = self.store.load(project_id)
            if (not old or old["status"] == "cancelled") and project.get("status") in ("scheduled", "closing", "closed"):
                raise PlanError("This episode already has a scheduled or completed release. Use its existing release controls.", 409)
            if old and any(a["status"] in ("running", "succeeded", "needs_attention") for a in old["actions"]):
                raise PlanError("This plan has begun uploading. Pause or resume it; uploaded releases cannot be replaced safely.", 409)
            if any(project.get("existing", {}).get(d) for d in config["destinations"]) or project.get("legacy_queued"):
                raise PlanError("This episode already has an upload or release queue entry. Use its existing release controls.", 409)
            plan = {"id": str(uuid.uuid4()), "project_id": project_id, **config,
                    "status": "scheduled" if config["mode"] == "automatic" else "awaiting_approval",
                    "approved_fingerprint": None, "created_at": iso(self.clock()), "actions": [], "activity": []}
            for kind in ("upload", "publish"):
                for destination in config["destinations"]:
                    plan["actions"].append({"id": kind + "_" + destination, "destination": destination,
                        "kind": kind, "status": "pending", "attempts": 0, "error": None,
                        "provider_id": None, "url": None, "next_attempt_at": None})
            self.event(plan, "Automatic release enabled." if config["mode"] == "automatic" else "Review plan saved. Approve when the episode is ready.")
            self.store.clear_control(project_id)
            self.store.save(plan)
        return await self.describe(project_id)

    async def approve(self, project_id):
        project = await self.inspect_project(project_id)
        with self.store.lock(project_id):
            plan = self.store.load(project_id)
            if not plan:
                raise PlanError("Save a release plan first.", 404)
            if plan["status"] in ("cancelled", "completed"):
                raise PlanError("This release plan is already {}.".format(plan["status"]), 409)
            readiness = self.readiness(project, plan, plan)
            if not readiness["ready"]:
                raise PlanError(" ".join(readiness["blockers"]), 409)
            if any(a["status"] == "needs_attention" for a in plan["actions"]):
                raise PlanError("Check the uncertain upload in the provider before retrying. Automatic re-upload could duplicate it.", 409)
            if self.prepared_content_changed(project, plan):
                raise PlanError("Episode content changed after private upload. Reconcile the uploaded episode in the provider before releasing it.", 409)
            plan["approved_fingerprint"] = self.fingerprint(project)
            plan["status"] = "scheduled"
            self.store.clear_control(project_id)
            self.event(plan, "Release approved. Private preparation can start; publishing waits for the release time.")
            self.store.save(plan)
        return await self.describe(project_id)

    async def control(self, project_id, command):
        plan = self.store.load(project_id)
        if not plan:
            raise PlanError("Save a release plan first.", 404)
        if plan["status"] in ("completed", "cancelled"):
            raise PlanError("This release plan is already {}.".format(plan["status"]), 409)
        if command in ("pause", "cancel"):
            self.store.set_control(project_id, "paused" if command == "pause" else "cancelled")
            # A running provider call may complete. The durable control is checked
            # before every subsequent action, including all publish calls.
            try:
                with self.store.lock(project_id):
                    plan = self.store.load(project_id)
                    plan["status"] = self.store.control(project_id)["status"]
                    self.event(plan, "Release {}. Already completed provider actions are retained.".format(plan["status"]))
                    self.store.save(plan)
            except PlanError as exc:
                if exc.status_code != 409:
                    raise
            return await self.describe(project_id)
        if command != "resume":
            raise PlanError("Unknown release command.")
        with self.store.lock(project_id):
            plan = self.store.load(project_id)
            if any(a["status"] == "needs_attention" for a in plan["actions"]):
                raise PlanError("An upload needs provider review before this release can resume.", 409)
            for action in plan["actions"]:
                if action["status"] == "failed" and action["kind"] == "publish":
                    action.update(status="pending", attempts=0, error=None, next_attempt_at=None)
            plan["status"] = "awaiting_approval" if plan["mode"] == "review" and not plan.get("approved_fingerprint") else "scheduled"
            self.store.clear_control(project_id)
            self.event(plan, "Release resumed. Completed uploads will be reused.")
            self.store.save(plan)
        return await self.describe(project_id)

    async def process(self, project_id):
        if os.getenv("PODCLICK_AUTOMATION_DISABLED", "").lower() in ("1", "true", "yes"):
            return
        try:
            with self.store.lock(project_id):
                plan = self.store.load(project_id)
                if not plan:
                    return
                await self.reconcile(project_id, plan)
                if plan["status"] in STOPPED or self.store.control(project_id):
                    return
                # A previous process died after claiming work. Privacy updates on
                # a saved ID are repeatable, but an interrupted upload is uncertain.
                for action in plan["actions"]:
                    if action["status"] == "running":
                        action["status"] = ("failed" if action["attempts"] >= MAX_ATTEMPTS else "pending") if action["kind"] == "publish" else "needs_attention"
                        action["error"] = "Interrupted action; {}.".format("retrying saved provider ID" if action["kind"] == "publish" else "check the provider for an existing upload")
                        self.event(plan, action["error"])
                project = await self.inspect_project(project_id)
                if self.prepared_content_changed(project, plan):
                    plan["status"] = "needs_attention"
                    plan["blockers"] = ["Episode content changed after private upload. Reconcile the uploaded episode in the provider before releasing it."]
                    self.store.save(plan)
                    return
                readiness = self.readiness(project, plan, plan)
                if not readiness["ready"]:
                    plan["status"] = "needs_attention"
                    plan["blockers"] = readiness["blockers"]
                    self.store.save(plan)
                    return
                plan["blockers"] = []
                if plan["mode"] == "review" and plan.get("approved_fingerprint") != self.fingerprint(project):
                    plan["status"] = "awaiting_approval"
                    plan["approved_fingerprint"] = None
                    self.event(plan, "Episode content changed after approval. Review the updated episode before releasing it.")
                    self.store.save(plan)
                    return
                for action in plan["actions"]:
                    control = self.store.control(project_id)
                    if control:
                        plan["status"] = control["status"]
                        self.event(plan, "Release {} after the current action finished.".format(plan["status"]))
                        self.store.save(plan)
                        return
                    if action["status"] not in ("pending", "retry_wait"):
                        continue
                    if action.get("next_attempt_at") and timestamp(action["next_attempt_at"]) > self.clock():
                        continue
                    if action["kind"] == "publish":
                        if timestamp(plan["scheduled_at"]) > self.clock():
                            continue
                        upload = next(a for a in plan["actions"] if a["id"] == "upload_" + action["destination"])
                        if upload["status"] != "succeeded" or not upload.get("provider_id"):
                            continue
                        # Uploads can take minutes. Never release from the
                        # snapshot taken before those uploads: an editor may
                        # have changed the episode during this same pass.
                        latest_project = await self.inspect_project(project_id)
                        latest_readiness = self.readiness(latest_project, plan, plan)
                        review_changed = (plan["mode"] == "review" and
                                          plan.get("approved_fingerprint") != self.fingerprint(latest_project))
                        if not latest_readiness["ready"] or review_changed:
                            plan["status"] = "needs_attention"
                            plan["blockers"] = latest_readiness["blockers"] or [
                                "Episode content changed after approval. Review the prepared upload before releasing it."]
                            self.event(plan, "Publication stopped by the final readiness check. " + " ".join(plan["blockers"]))
                            self.store.save(plan)
                            return
                        project = latest_project
                        # A pause/cancel request may arrive while the fresh
                        # project read is awaiting its database response.
                        control = self.store.control(project_id)
                        if control:
                            plan["status"] = control["status"]
                            self.event(plan, "Release {} before publication.".format(plan["status"]))
                            self.store.save(plan)
                            return
                        action.update(provider_id=upload["provider_id"], url=upload.get("url"))
                    # Reconcile saved success into the project before making any
                    # next external call. A database outage must not lose upload IDs.
                    action["status"] = "running"
                    action["attempts"] += 1
                    action["error"] = None
                    action["content_fingerprint"] = self.fingerprint(project)
                    plan["status"] = "running"
                    self.event(plan, "{} {} (attempt {}).".format("Preparing" if action["kind"] == "upload" else "Publishing", action["destination"], action["attempts"]))
                    self.store.save(plan)
                    try:
                        result = await self.perform(project, copy.deepcopy(action))
                    except Exception:
                        result = {"ok": False, "error": "Provider request did not return a confirmed result."}
                    if result.get("ok") and (action["kind"] != "upload" or result.get("provider_id")):
                        action.update(status="succeeded", provider_id=result.get("provider_id") or action.get("provider_id"),
                                      url=result.get("url") or action.get("url"), error=None, next_attempt_at=None)
                        self.event(plan, "{} {}.".format(action["destination"], "uploaded privately" if action["kind"] == "upload" else "published"))
                        self.store.save(plan)  # durable provider receipt BEFORE DB projection
                        await self.reconcile(project_id, plan)
                    else:
                        action["error"] = str(result.get("error") or "Provider did not confirm an upload ID.")[:500]
                        safe_retry = action["kind"] == "publish" or result.get("safe_to_retry") is True
                        if safe_retry and action["attempts"] < MAX_ATTEMPTS:
                            action.update(status="retry_wait", next_attempt_at=iso(self.clock() + 60 * (2 ** (action["attempts"] - 1))))
                        else:
                            action["status"] = "failed" if safe_retry else "needs_attention"
                        self.event(plan, "{}: {}".format(action["destination"], action["error"]))
                    self.store.save(plan)
                statuses = [a["status"] for a in plan["actions"]]
                if all(s == "succeeded" for s in statuses):
                    plan["status"] = "completed"
                elif any(s in ("failed", "needs_attention") for s in statuses):
                    plan["status"] = "needs_attention"
                else:
                    plan["status"] = "scheduled"
                if self.store.control(project_id):
                    plan["status"] = self.store.control(project_id)["status"]
                self.store.save(plan)
        except PlanError as exc:
            if exc.status_code != 409:
                raise

    async def loop(self):
        concurrency = asyncio.Semaphore(2)
        active = {}

        async def check_project(project_id):
            async with concurrency:
                try:
                    await self.process(project_id)
                except Exception:
                    logging.getLogger(__name__).exception("Release plan check failed for %s", project_id)

        try:
            while True:
                # A long upload must not hold the next release-time scan. Keep
                # one task per project, with at most two processing concurrently.
                active = {project_id: task for project_id, task in active.items() if not task.done()}
                if os.getenv("PODCLICK_AUTOMATION_DISABLED", "").lower() not in ("1", "true", "yes"):
                    for project_id in self.store.project_ids():
                        if project_id not in active:
                            active[project_id] = asyncio.create_task(check_project(project_id))
                await asyncio.sleep(30)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
