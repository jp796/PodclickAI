"""Release safety tests: isolated files and fake providers; never publish live."""

import asyncio
import copy
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from services.podcast_autopilot import PodcastAutopilot, PlanError, PlanStore, iso


class AutopilotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project_id = str(uuid.uuid4())
        self.now = 1_800_000_000
        self.store = PlanStore(Path(self.temp.name) / "plans")
        self.project = {"id": self.project_id, "status": "review", "title": "A finished episode",
                        "show_notes": "Useful show notes", "audio_path": "episode.mp3",
                        "video_path": "episode.mp4", "audio_version": "v1", "video_version": "v1",
                        "audio_ready": True, "video_ready": True, "legacy_queued": False,
                        "connections": {"buzzsprout": True, "youtube": True}, "existing": {}}
        self.calls = []
        self.receipts = []
        self.results = {}
        self.env = patch.dict(os.environ, {"PODCLICK_AUTOMATION_DISABLED": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

        async def inspect(project_id):
            snapshot = copy.deepcopy(self.project)
            snapshot["id"] = project_id
            return snapshot

        async def perform(project, action):
            self.calls.append(action["id"])
            return self.results.get(action["id"], {"ok": True,
                "provider_id": "provider-" + action["destination"], "url": "https://example.test/episode"})

        async def record(project_id, action, plan):
            self.receipts.append(action["id"])
            if action["kind"] == "upload":
                self.project["existing"][action["destination"]] = action["provider_id"]

        self.service = PodcastAutopilot(self.store, inspect, perform, record, clock=lambda: self.now)

    async def configure(self, mode="automatic", destinations=None):
        return await self.service.configure(self.project_id, {"mode": mode,
            "destinations": destinations or ["buzzsprout", "youtube"], "scheduled_at": iso(self.now + 3600)})

    def plan(self):
        return self.store.load(self.project_id)

    async def test_no_plan_means_no_automation(self):
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.plan())

    async def test_review_requires_approval_and_future_release_is_honored(self):
        await self.configure(mode="review")
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        await self.service.approve(self.project_id)
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, ["upload_buzzsprout", "upload_youtube"])
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertEqual(self.calls[-2:], ["publish_buzzsprout", "publish_youtube"])
        self.assertEqual(self.plan()["status"], "completed")
        await self.service.process(self.project_id)
        self.assertEqual(len(self.calls), 4)

    async def test_only_selected_destination_runs(self):
        await self.configure(destinations=["youtube"])
        await self.service.process(self.project_id)
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, ["upload_youtube", "publish_youtube"])

    async def test_automatic_waits_for_preparation_then_recovers(self):
        self.project.update(status="processing", video_ready=False)
        await self.configure()
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plan()["status"], "needs_attention")
        self.project.update(status="review", video_ready=True)
        await self.service.process(self.project_id)
        self.assertEqual(len(self.calls), 2)

    async def test_ambiguous_upload_never_blindly_retries(self):
        self.results["upload_youtube"] = {"ok": False, "error": "Network timeout"}
        await self.configure(destinations=["youtube"])
        await self.service.process(self.project_id)
        self.now += 7200
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, ["upload_youtube"])
        self.assertEqual(self.plan()["actions"][0]["status"], "needs_attention")
        with self.assertRaises(PlanError):
            await self.service.control(self.project_id, "resume")

    async def test_no_provider_id_is_not_treated_as_success(self):
        self.results["upload_youtube"] = {"ok": True}
        await self.configure(destinations=["youtube"])
        await self.service.process(self.project_id)
        self.assertEqual(self.plan()["actions"][0]["status"], "needs_attention")
        self.assertEqual(self.receipts, [])

    async def test_publication_retries_have_backoff_and_limit(self):
        self.results["publish_youtube"] = {"ok": False, "error": "Temporary outage"}
        await self.configure(destinations=["youtube"])
        self.now += 3600
        await self.service.process(self.project_id)
        await self.service.process(self.project_id)
        self.assertEqual(self.calls.count("publish_youtube"), 1)
        self.now += 60
        await self.service.process(self.project_id)
        self.now += 120
        await self.service.process(self.project_id)
        self.now += 600
        await self.service.process(self.project_id)
        self.assertEqual(self.calls.count("publish_youtube"), 3)
        self.assertEqual(self.plan()["actions"][1]["status"], "failed")

    async def test_resume_retries_failed_publish_without_uploading_again(self):
        await self.test_publication_retries_have_backoff_and_limit()
        self.results.pop("publish_youtube")
        await self.service.control(self.project_id, "resume")
        await self.service.process(self.project_id)
        self.assertEqual(self.calls.count("upload_youtube"), 1)
        self.assertEqual(self.plan()["status"], "completed")

    async def test_pause_during_upload_prevents_subsequent_actions(self):
        await self.configure()
        original = self.service.perform

        async def pausing_provider(project, action):
            result = await original(project, action)
            await self.service.control(self.project_id, "pause")
            return result

        self.service.perform = pausing_provider
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, ["upload_buzzsprout"])
        self.assertEqual(self.plan()["status"], "paused")
        self.service.perform = original
        await self.service.control(self.project_id, "resume")
        await self.service.process(self.project_id)
        self.assertEqual(self.calls.count("upload_buzzsprout"), 1)
        self.assertEqual(self.plan()["status"], "completed")

    async def test_cancel_is_durable_after_restart(self):
        await self.configure()
        await self.service.control(self.project_id, "cancel")
        self.service.store = PlanStore(self.store.directory)
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plan()["status"], "cancelled")

    async def test_content_change_after_approval_requires_new_review(self):
        await self.configure(mode="review")
        await self.service.approve(self.project_id)
        self.project["show_notes"] = "Edited since approval"
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plan()["status"], "awaiting_approval")

    async def test_content_change_after_upload_blocks_both_modes_and_reapproval(self):
        for mode in ("automatic", "review"):
            with self.subTest(mode=mode):
                self.project_id = str(uuid.uuid4())
                self.project["existing"] = {}
                self.project["video_version"] = "v1"
                await self.configure(mode=mode, destinations=["youtube"])
                if mode == "review":
                    await self.service.approve(self.project_id)
                await self.service.process(self.project_id)
                self.project["video_version"] = "v2"
                self.now += 3600
                await self.service.process(self.project_id)
                self.assertEqual(self.plan()["status"], "needs_attention")
                self.assertNotIn("publish_youtube", self.calls)
                with self.assertRaises(PlanError):
                    await self.service.approve(self.project_id)

    async def test_content_change_during_upload_blocks_same_pass_publication_in_both_modes(self):
        original = self.service.perform

        async def edited_during_upload(project, action):
            result = await original(project, action)
            if action["kind"] == "upload":
                self.project["video_version"] = "edited-while-uploading"
            return result

        self.service.perform = edited_during_upload
        for mode in ("automatic", "review"):
            with self.subTest(mode=mode):
                self.project_id = str(uuid.uuid4())
                self.project["existing"] = {}
                self.project["video_version"] = "approved-cut"
                self.calls.clear()
                await self.configure(mode=mode, destinations=["youtube"])
                if mode == "review":
                    await self.service.approve(self.project_id)
                self.now += 3600
                await self.service.process(self.project_id)
                self.assertEqual(self.calls, ["upload_youtube"])
                self.assertEqual(self.plan()["status"], "needs_attention")
                self.assertEqual(self.plan()["actions"][1]["attempts"], 0)
                self.assertTrue(any("changed after private upload" in b for b in self.plan()["blockers"]))

    async def test_completed_receipts_retry_database_sync_without_republishing(self):
        original = self.service.record_outcome

        async def broken_record(*args):
            raise RuntimeError("database unavailable")

        self.service.record_outcome = broken_record
        await self.configure(destinations=["youtube"])
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertEqual(self.plan()["status"], "completed")
        self.assertEqual(len(self.calls), 2)
        self.service.record_outcome = original
        await self.service.process(self.project_id)
        self.assertTrue(all(a.get("projected") for a in self.plan()["actions"]))
        self.assertEqual(len(self.calls), 2)

    async def test_interrupted_upload_requires_attention(self):
        await self.configure(destinations=["youtube"])
        plan = self.plan()
        plan["actions"][0].update(status="running", attempts=1)
        self.store.save(plan)
        await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plan()["status"], "needs_attention")

    async def test_interrupted_third_publish_does_not_exceed_retry_budget(self):
        await self.configure(destinations=["youtube"])
        await self.service.process(self.project_id)
        plan = self.plan()
        plan["actions"][1].update(status="running", attempts=3, provider_id="provider-youtube")
        self.store.save(plan)
        self.now += 3600
        await self.service.process(self.project_id)
        self.assertNotIn("publish_youtube", self.calls)
        self.assertEqual(self.plan()["actions"][1]["status"], "failed")

    async def test_concurrent_workers_cannot_claim_same_upload(self):
        await self.configure(destinations=["youtube"])
        original = self.service.perform
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_provider(project, action):
            entered.set()
            await release.wait()
            return await original(project, action)

        self.service.perform = slow_provider
        first = asyncio.create_task(self.service.process(self.project_id))
        await entered.wait()
        await self.service.process(self.project_id)
        release.set()
        await first
        self.assertEqual(self.calls, ["upload_youtube"])

    async def test_preflight_candidate_does_not_save_or_publish(self):
        self.project["connections"]["youtube"] = False
        result = await self.service.describe(self.project_id, {"mode": "automatic",
            "destinations": ["youtube"], "scheduled_at": iso(self.now + 3600)})
        self.assertFalse(result["readiness"]["ready"])
        self.assertIsNone(self.plan())
        self.assertEqual(self.calls, [])

    async def test_existing_upload_or_legacy_queue_cannot_get_second_owner(self):
        self.project["existing"]["youtube"] = "already-uploaded"
        with self.assertRaises(PlanError):
            await self.configure()
        self.project["existing"] = {}
        self.project["legacy_queued"] = True
        with self.assertRaises(PlanError):
            await self.configure()

    async def test_legacy_scheduled_episode_cannot_enable_plan_before_upload_id_exists(self):
        self.project["status"] = "scheduled"
        with self.assertRaises(PlanError):
            await self.configure()
        self.assertIsNone(self.plan())

    async def test_blocked_upload_does_not_block_other_due_project(self):
        first_id = self.project_id
        await self.configure(destinations=["youtube"])
        self.project_id = str(uuid.uuid4())
        second_id = self.project_id
        await self.configure(destinations=["youtube"])
        await self.service.process(second_id)
        self.project["existing"] = {}  # fake inspection represents each project independently
        self.now += 3600
        original = self.service.perform
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_upload(project, action):
            if action["kind"] == "upload":
                entered.set()
                await release.wait()
            return await original(project, action)

        self.service.perform = slow_upload
        blocked = asyncio.create_task(self.service.process(first_id))
        await entered.wait()
        await self.service.process(second_id)
        self.assertEqual(self.store.load(second_id)["status"], "completed")
        self.assertFalse(blocked.done())
        release.set()
        await blocked

    async def test_master_disable_prevents_all_provider_calls(self):
        await self.configure()
        with patch.dict(os.environ, {"PODCLICK_AUTOMATION_DISABLED": "1"}):
            self.now += 3600
            await self.service.process(self.project_id)
        self.assertEqual(self.calls, [])

    async def test_loop_rescans_due_releases_during_long_upload_and_cleans_up_on_shutdown(self):
        slow_id = self.project_id
        await self.configure(destinations=["youtube"])
        self.project_id = str(uuid.uuid4())
        due_id = self.project_id
        await self.configure(destinations=["youtube"])
        await self.service.process(due_id)  # privately prepared, release is not yet due
        self.project["existing"] = {}
        entered, scanned, published, cancelled = (asyncio.Event() for _ in range(4))
        release_upload, park_loop = asyncio.Event(), asyncio.Event()
        original_perform = self.service.perform
        original_inspect = self.service.inspect_project
        original_sleep = asyncio.sleep
        scans = 0

        async def inspect(project_id):
            snapshot = await original_inspect(project_id)
            if project_id == due_id:
                scanned.set()
            return snapshot

        async def perform(project, action):
            if project["id"] == slow_id and action["kind"] == "upload":
                entered.set()
                try:
                    await release_upload.wait()
                finally:
                    cancelled.set()
            result = await original_perform(project, action)
            if project["id"] == due_id and action["kind"] == "publish":
                published.set()
            return result

        async def advance_scan(delay):
            nonlocal scans
            if delay != 30:
                return await original_sleep(delay)
            scans += 1
            if scans == 1:
                await entered.wait()
                await scanned.wait()
                await original_sleep(0)
                self.now += 3600  # release becomes due while the first upload is still blocked
                return
            await park_loop.wait()

        self.service.inspect_project = inspect
        self.service.perform = perform
        with patch("services.podcast_autopilot.asyncio.sleep", side_effect=advance_scan):
            loop = asyncio.create_task(self.service.loop())
            try:
                await asyncio.wait_for(published.wait(), timeout=1)
                self.assertEqual(self.store.load(due_id)["status"], "completed")
                self.assertFalse(release_upload.is_set())
                self.assertEqual(self.store.load(slow_id)["actions"][0]["attempts"], 1)
            finally:
                loop.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await loop
        self.assertTrue(cancelled.is_set())

    async def test_validation_rejects_empty_destinations_naive_dates_and_path_traversal(self):
        for config in ({"mode": "automatic", "destinations": [], "scheduled_at": iso(self.now + 3600)},
                       {"mode": "automatic", "destinations": ["youtube"], "scheduled_at": "2027-01-01T10:00"}):
            with self.assertRaises(PlanError):
                await self.service.configure(self.project_id, config)
        with self.assertRaises(PlanError):
            self.store.load("../../outside")


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_episode_number_lock_precedes_row_and_max_query_and_commits_before_upload(self):
        from main import _autopilot_perform
        from types import SimpleNamespace
        events = []
        episode = SimpleNamespace(episode_number=None)
        session = MagicMock()

        async def execute(statement, parameters=None):
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                events.append("reservation_lock")
                self.assertEqual(parameters, {"key": 0x504F4443415354})
                return None
            if "max(" in sql:
                events.append("max_number")
                return SimpleNamespace(scalar=lambda: 120)
            events.append("project_row")
            self.assertIn("FOR UPDATE", sql)
            return SimpleNamespace(scalar_one=lambda: episode)

        async def commit():
            events.append("commit")

        async def upload(**kwargs):
            events.append("provider_upload")
            self.assertEqual(kwargs["episode_number"], 121)
            return {"success": True, "episode_id": "receipt"}

        session.execute = AsyncMock(side_effect=execute)
        session.commit = AsyncMock(side_effect=commit)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        project = {"id": str(uuid.uuid4()), "episode_number": 0, "audio_path": "test.mp3", "title": "Test", "show_notes": "Notes"}
        with patch("db.engine.async_session", return_value=context), patch("pipeline.upload.upload_episode", side_effect=upload):
            await _autopilot_perform(project, {"kind": "upload", "destination": "buzzsprout"})
        self.assertEqual(events, ["reservation_lock", "project_row", "max_number", "commit", "provider_upload"])

    async def test_future_legacy_youtube_release_stays_private_until_scheduled(self):
        from main import _youtube_release_settings
        with patch.dict(os.environ, {"PODCLICK_YOUTUBE_PRIVACY": "public"}):
            future = _youtube_release_settings(1_800_003_600, now=1_800_000_000)
            self.assertEqual(future["privacy_status"], "private")
            self.assertEqual(future["publish_at"], iso(1_800_003_600))
            self.assertEqual(_youtube_release_settings(1_800_000_000, now=1_800_000_000),
                             {"privacy_status": "public", "publish_at": ""})
        with patch.dict(os.environ, {"PODCLICK_YOUTUBE_PRIVACY": "unlisted"}):
            self.assertEqual(_youtube_release_settings(1_800_003_600, now=1_800_000_000),
                             {"privacy_status": "unlisted", "publish_at": ""})

    async def test_legacy_reschedule_moves_private_schedule_without_unpublishing_live_video(self):
        from main import _reschedule_youtube_release
        from datetime import datetime, timezone
        new_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for privacy in ("private", "public"):
            with self.subTest(privacy=privacy), patch("pipeline.youtube.get_credentials"), patch("googleapiclient.discovery.build") as builder:
                videos = builder.return_value.videos.return_value
                videos.list.return_value.execute.return_value = {"items": [{"status": {
                    "privacyStatus": privacy, "publishAt": "2029-01-01T00:00:00Z"}}]}
                result = await _reschedule_youtube_release("existing-video", new_date)
                self.assertTrue(result["ok"])
                if privacy == "private":
                    self.assertEqual(videos.update.call_args.kwargs["body"]["status"]["publishAt"], new_date.isoformat())
                else:
                    videos.update.assert_not_called()

    async def test_buzzsprout_upload_is_always_private(self):
        from main import _autopilot_perform
        project = {"episode_number": 123, "audio_path": "test.mp3", "title": "Test", "show_notes": "Notes"}
        uploader = AsyncMock(return_value={"success": True, "episode_id": "123", "url": "https://example.test/audio"})
        with patch("pipeline.upload.upload_episode", uploader):
            result = await _autopilot_perform(project, {"kind": "upload", "destination": "buzzsprout"})
        self.assertTrue(result["ok"])
        self.assertTrue(uploader.call_args.kwargs["private"])

    async def test_youtube_upload_is_private_without_remote_autopublish(self):
        from main import _autopilot_perform
        project = {"episode_number": 123, "video_path": "test.mp4", "title": "Test", "show_notes": "Notes", "chapters": []}
        with patch("pipeline.youtube.upload_video", return_value={"ok": True, "video_id": "abc"}) as uploader:
            result = await _autopilot_perform(project, {"kind": "upload", "destination": "youtube"})
        self.assertTrue(result["ok"])
        self.assertEqual(uploader.call_args.kwargs["privacy_status"], "private")
        self.assertNotIn("publish_at", uploader.call_args.kwargs)

    async def test_youtube_publication_updates_only_known_provider_id(self):
        from main import _autopilot_perform
        with patch("pipeline.youtube.get_credentials"), patch("googleapiclient.discovery.build") as builder:
            result = await _autopilot_perform({}, {"kind": "publish", "destination": "youtube", "provider_id": "existing-video"})
        self.assertTrue(result["ok"])
        self.assertEqual(builder.return_value.videos.return_value.update.call_args.kwargs["body"],
                         {"id": "existing-video", "status": {"privacyStatus": "public"}})


if __name__ == "__main__":
    unittest.main()
