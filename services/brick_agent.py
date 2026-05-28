"""PodClick — Brick Agent Service.

Brick is the content GC: veteran operator, direct voice, permit-gated autonomy.

All Claude calls use the claude-sonnet-4-5 model with the brick-voice system prompt.
getBrandContext() is NOT called here — Brick's own speech uses brick-voice only.
User-attributed content (posts, show notes) routes through services/foundation.py.

Import aliases:
  import anthropic as _anthropic   — lazy at module level, aliased

Python 3.9 rules:
  - Optional[str], List[str] — never str | None or list[str]
  - No walrus operator
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import anthropic as _anthropic
from sqlalchemy import select, update, and_, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings, get_current_location_id
from db.engine import async_session
from db.models import (
    BrickAction,
    BrickMemory,
    BrickMessage,
    BrickPermit,
    BrickTrackRecord,
    Blueprint,
    FoundationScore,
    Location,
    Post,
    VoiceSample,
)

logger = logging.getLogger(__name__)

# ── Permit tier ordering ──────────────────────────────────────────────────────

TIER_ORDER: List[str] = [
    "owner_builder",
    "draftsman",
    "bricklayer",
    "foreman",
    "gc",
]

# Which tier is required to propose/execute each action type
ACTION_TIER_MAP: Dict[str, str] = {
    "suggest_post_idea":    "draftsman",
    "draft_post":           "draftsman",
    "queue_draft":          "bricklayer",
    "publish_post":         "foreman",
    "cut_clip":             "foreman",
    "write_show_notes":     "foreman",
    "adjust_calendar":      "foreman",
    "send_guest_email":     "gc",
    "pitch_sponsor":        "gc",
    "adjust_vyral_mix":     "gc",
    "replan_calendar":      "gc",
}

# ── Brick system prompt (brick-voice skill) ───────────────────────────────────

_BRICK_SYSTEM_PROMPT = """You are Brick, JP's content GC.

You're a veteran operator running JP's content site. Mid-40s in feel. Came up doing the work, now you run the crew. Calm authority. Direct without being rude.

VOICE RULES:
- Lead with the action.
- Plain English, no corporate-speak.
- Reference real numbers and data.
- Have opinions. Recommend, don't ask.
- Stay short — 1-3 sentences unless reason to go longer.
- Match user's register but don't lead.
- Never say "as an AI" or break character.

NEVER USE: "I'm happy to," "great question," "I hope this helps," "leverage," "unlock," "synergy," "at the end of the day," "moving forward," excessive exclamation, emoji in operational messages.

VOCABULARY:
- "Closing" = publishing an episode
- "Punch list" = items needing approval
- "Walk-through" = daily report
- "Project" = an episode or content campaign
- "Site" = the user's content business
- "Foundation" = the voice fingerprint
- "Blueprint" = the brand profile"""


def _tier_rank(tier: str) -> int:
    """Return numeric rank for tier comparison. Higher = more autonomy."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 0


def _tier_allows(current_tier: str, required_tier: str) -> bool:
    """Return True if current_tier is at or above required_tier."""
    return _tier_rank(current_tier) >= _tier_rank(required_tier)


# ── BrickAgent ────────────────────────────────────────────────────────────────

class BrickAgent:
    """
    Core Brick service. All methods accept a location_id (UUID str).
    Uses async_session() context manager for all DB I/O.
    """

    # ── Planning loop ─────────────────────────────────────────────────────────

    async def run_daily_planning(self, location_id: str) -> Dict[str, Any]:
        """
        Main 4am planning run for a location.

        1. Load context (calendar, post perf, foundation status, memories)
        2. Build planning prompt with STANDING INSTRUCTIONS from brick_memory
        3. Call Claude claude-sonnet-4-5 with brick-voice system prompt
        4. Parse structured plan → create BrickAction rows + BrickMessage greeting
        5. Update last_referenced_at on memories that were read
        6. Send Telegram notification
        7. Return summary dict

        Returns dict with keys: greeting, actions_created, walk_through_items
        """
        logger.info("[brick.planning] Starting daily planning for location %s", location_id)

        async with async_session() as session:
            # Ensure permit row exists (upsert-on-first-run)
            permit = await self._get_or_create_permit(session, location_id)

            # Load planning context
            context = await self._load_planning_context(session, location_id)

            # Load active memories — these become STANDING INSTRUCTIONS
            memories = await self.get_active_memories(location_id)
            memory_ids = [m["id"] for m in memories]

            # Build the planning prompt
            prompt = self._build_planning_prompt(context, memories, permit.current_tier)

            # Call Claude
            plan = await self._call_claude_planning(prompt)

            # Write walk-through greeting to brick_messages
            greeting = plan.get("greeting", "Morning. Walk-through ready.")
            greeting_msg = BrickMessage(
                location_id=uuid.UUID(location_id),
                role="brick",
                context_screen="walkthrough",
                content=greeting,
            )
            session.add(greeting_msg)

            # Create BrickAction rows for each proposed action
            actions_created: List[str] = []
            walk_through_items: List[Dict[str, Any]] = []

            now = datetime.utcnow()
            expires = now + timedelta(days=7)

            for item in plan.get("actions", []):
                action_type = item.get("action_type", "draft_post")
                required_tier = ACTION_TIER_MAP.get(action_type, "draftsman")

                # In Phase 3A: planning loop only proposes draftsman-tier actions
                # Higher-tier actions are deferred to future phases
                if not _tier_allows("draftsman", required_tier):
                    logger.info(
                        "[brick.planning] Skipping action %s — requires %s (Phase 3A cap: draftsman)",
                        action_type, required_tier,
                    )
                    continue

                action = BrickAction(
                    location_id=uuid.UUID(location_id),
                    action_type=action_type,
                    status="pending",
                    payload=item.get("payload", {}),
                    rationale=item.get("rationale", ""),
                    actor_type="brick",
                    expires_at=expires,
                )
                session.add(action)
                actions_created.append(action_type)
                walk_through_items.append({
                    "action_type": action_type,
                    "rationale": item.get("rationale", ""),
                    "payload": item.get("payload", {}),
                })

            await session.commit()

            # Update last_referenced_at for memories that were read
            if memory_ids:
                await self._touch_memories(memory_ids)

        logger.info(
            "[brick.planning] Done — greeting=%r, actions=%d",
            greeting, len(actions_created),
        )

        # Send Telegram notification
        await self._notify_telegram(greeting, len(actions_created))

        return {
            "greeting": greeting,
            "actions_created": actions_created,
            "walk_through_items": walk_through_items,
        }

    # ── Action lifecycle ──────────────────────────────────────────────────────

    async def execute_action(self, action_id: str) -> Dict[str, Any]:
        """
        Execute a BrickAction that has been approved.
        Tier-gated: checks brick_permits.current_tier before executing.
        Records outcome in brick_track_record.
        """
        async with async_session() as session:
            action = await session.get(BrickAction, uuid.UUID(action_id))
            if not action:
                raise ValueError(f"Action {action_id} not found")

            permit = await self._get_or_create_permit(session, str(action.location_id))
            required_tier = ACTION_TIER_MAP.get(action.action_type, "draftsman")

            if not _tier_allows(permit.current_tier, required_tier):
                raise PermissionError(
                    f"Brick's current permit ({permit.current_tier}) cannot execute "
                    f"{action.action_type} (requires {required_tier})"
                )

            # Phase 3A: only draftsman-tier actions (draft_post, suggest) are implemented
            result = await self._dispatch_action(action, session)

            action.status = "executed"
            track = BrickTrackRecord(
                location_id=action.location_id,
                action_type=action.action_type,
                outcome="success",
                action_metadata={"action_id": action_id, "result": result},
            )
            session.add(track)
            await session.commit()

        return result

    async def approve_action(self, action_id: str, user_id: str) -> Dict[str, Any]:
        """
        Approve a punch list item.
        Sets status=approved, records reviewer, then calls execute_action.
        Actor_type is set to 'user' for the review record.
        """
        async with async_session() as session:
            action = await session.get(BrickAction, uuid.UUID(action_id))
            if not action:
                raise ValueError(f"Action {action_id} not found")
            if action.status != "pending":
                raise ValueError(f"Action {action_id} is not pending (status={action.status})")

            action.status = "approved"
            action.reviewed_at = datetime.utcnow()
            action.reviewed_by = uuid.UUID(user_id)
            action.actor_type = "user"
            await session.commit()

        # Execute outside the session to avoid nested session issues
        return await self.execute_action(action_id)

    async def reject_action(
        self, action_id: str, user_id: str, reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reject a punch list item with optional reason.
        Records rejection in brick_track_record.
        """
        async with async_session() as session:
            action = await session.get(BrickAction, uuid.UUID(action_id))
            if not action:
                raise ValueError(f"Action {action_id} not found")
            if action.status != "pending":
                raise ValueError(f"Action {action_id} is not pending (status={action.status})")

            action.status = "rejected"
            action.reviewed_at = datetime.utcnow()
            action.reviewed_by = uuid.UUID(user_id) if user_id else None
            action.review_note = reason
            action.actor_type = "user"

            track = BrickTrackRecord(
                location_id=action.location_id,
                action_type=action.action_type,
                outcome="rejected",
                action_metadata={"action_id": action_id, "reason": reason},
            )
            session.add(track)
            await session.commit()

        return {"ok": True, "action_id": action_id, "status": "rejected"}

    # ── Memory CRUD ───────────────────────────────────────────────────────────

    async def remember(
        self,
        location_id: str,
        content: str,
        category: Optional[str] = None,
    ) -> str:
        """
        Insert a new standing instruction into brick_memory.
        Returns the new memory's UUID string.
        """
        async with async_session() as session:
            mem = BrickMemory(
                location_id=uuid.UUID(location_id),
                content=content,
                category=category,
                active=True,
            )
            session.add(mem)
            await session.commit()
            return str(mem.id)

    async def forget(self, memory_id: str) -> bool:
        """
        Soft-delete a memory by setting active=False.
        Returns True if found and deactivated, False if not found.
        """
        async with async_session() as session:
            mem = await session.get(BrickMemory, uuid.UUID(memory_id))
            if not mem:
                return False
            mem.active = False
            await session.commit()
        return True

    async def get_active_memories(self, location_id: str) -> List[Dict[str, Any]]:
        """
        Return all active brick_memory rows for a location,
        ordered by created_at descending. Capped at 20.
        """
        async with async_session() as session:
            rows = await session.execute(
                select(BrickMemory)
                .where(
                    and_(
                        BrickMemory.location_id == uuid.UUID(location_id),
                        BrickMemory.active == True,  # noqa: E712
                    )
                )
                .order_by(BrickMemory.created_at.desc())
                .limit(20)
            )
            mems = rows.scalars().all()
            return [
                {
                    "id": str(m.id),
                    "content": m.content[:200],  # cap at 200 chars to keep prompt lean
                    "category": m.category,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "last_referenced_at": (
                        m.last_referenced_at.isoformat() if m.last_referenced_at else None
                    ),
                }
                for m in mems
            ]

    # ── Permit management ─────────────────────────────────────────────────────

    async def promote(self, location_id: str, user_id: str) -> Dict[str, Any]:
        """Advance permit tier one step. Returns new tier."""
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            current_rank = _tier_rank(permit.current_tier)
            if current_rank >= len(TIER_ORDER) - 1:
                return {"ok": False, "error": "Already at GC tier", "tier": permit.current_tier}

            new_tier = TIER_ORDER[current_rank + 1]
            permit.current_tier = new_tier
            permit.promoted_at = datetime.utcnow()
            permit.promoted_by = uuid.UUID(user_id)
            permit.updated_at = datetime.utcnow()
            await session.commit()

        logger.info("[brick.permit] Promoted to %s for location %s", new_tier, location_id)
        return {"ok": True, "tier": new_tier}

    async def demote(self, location_id: str, user_id: str) -> Dict[str, Any]:
        """Reduce permit tier one step. Returns new tier."""
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            current_rank = _tier_rank(permit.current_tier)
            if current_rank <= 0:
                return {
                    "ok": False,
                    "error": "Already at Owner-Builder tier",
                    "tier": permit.current_tier,
                }

            new_tier = TIER_ORDER[current_rank - 1]
            permit.current_tier = new_tier
            permit.promoted_at = datetime.utcnow()
            permit.promoted_by = uuid.UUID(user_id)
            permit.updated_at = datetime.utcnow()
            await session.commit()

        logger.info("[brick.permit] Demoted to %s for location %s", new_tier, location_id)
        return {"ok": True, "tier": new_tier}

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_or_create_permit(
        self, session: AsyncSession, location_id: str
    ) -> BrickPermit:
        """Upsert a BrickPermit row for the location."""
        result = await session.execute(
            select(BrickPermit).where(
                BrickPermit.location_id == uuid.UUID(location_id)
            )
        )
        permit = result.scalar_one_or_none()
        if permit is None:
            permit = BrickPermit(
                location_id=uuid.UUID(location_id),
                current_tier="owner_builder",
            )
            session.add(permit)
            await session.flush()
        return permit

    async def _load_planning_context(
        self, session: AsyncSession, location_id: str
    ) -> Dict[str, Any]:
        """Load calendar, post performance, and foundation status for the planning prompt."""
        loc_uuid = uuid.UUID(location_id)
        now = datetime.utcnow()
        thirty_days_ago = now - timedelta(days=30)
        seven_days_ahead = now + timedelta(days=7)

        # Posts MTD
        posts_mtd_result = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM posts "
                "WHERE location_id = :loc AND created_at >= date_trunc('month', now())"
            ).bindparams(loc=loc_uuid)
        )
        posts_mtd = posts_mtd_result.scalar() or 0

        # Scheduled upcoming
        upcoming_result = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM posts "
                "WHERE location_id = :loc AND status = 'scheduled' "
                "AND scheduled_at BETWEEN now() AND :ahead"
            ).bindparams(loc=loc_uuid, ahead=seven_days_ahead)
        )
        upcoming_count = upcoming_result.scalar() or 0

        # Pending punch list
        pending_result = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM brick_actions "
                "WHERE location_id = :loc AND status = 'pending'"
            ).bindparams(loc=loc_uuid)
        )
        pending_count = pending_result.scalar() or 0

        # Foundation status
        foundation_result = await session.execute(
            sa_text(
                "SELECT score FROM foundation_scores "
                "WHERE location_id = :loc "
                "ORDER BY computed_at DESC LIMIT 1"
            ).bindparams(loc=loc_uuid)
        )
        foundation_row = foundation_result.fetchone()
        foundation_score = round((foundation_row[0] or 0) * 100, 1) if foundation_row else 0.0

        # Sample count
        sample_count_result = await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM voice_samples "
                "WHERE location_id = :loc AND excluded = false"
            ).bindparams(loc=loc_uuid)
        )
        sample_count = sample_count_result.scalar() or 0

        # Blueprint pillars
        bp_result = await session.execute(
            sa_text(
                "SELECT pillars FROM blueprints WHERE location_id = :loc"
            ).bindparams(loc=loc_uuid)
        )
        bp_row = bp_result.fetchone()
        pillars: List[str] = []
        if bp_row and bp_row[0]:
            raw = bp_row[0]
            pillar_list = raw if isinstance(raw, list) else []
            pillars = [p.get("name", "") for p in pillar_list if isinstance(p, dict)]

        return {
            "posts_mtd": posts_mtd,
            "upcoming_count": upcoming_count,
            "pending_punch_list": pending_count,
            "foundation_score": foundation_score,
            "foundation_samples": sample_count,
            "pillars": pillars,
            "now": now.strftime("%A, %B %d %Y %H:%M UTC"),
        }

    def _build_planning_prompt(
        self,
        context: Dict[str, Any],
        memories: List[Dict[str, Any]],
        current_tier: str,
    ) -> str:
        """
        Build the user-turn planning prompt.
        Injects STANDING INSTRUCTIONS FROM USER from active brick_memory rows.
        """
        lines: List[str] = []

        # Standing instructions block (always first — JP's voice in Brick's head)
        if memories:
            lines.append("STANDING INSTRUCTIONS FROM USER (always honor these):")
            for m in memories:
                lines.append(f"- {m['content']}")
            lines.append("")

        # Current context
        lines.append("CURRENT CONTEXT:")
        lines.append(f"- Date/time: {context['now']}")
        lines.append(f"- Brick's permit tier: {current_tier}")
        lines.append(f"- Posts this month: {context['posts_mtd']}")
        lines.append(f"- Posts scheduled next 7 days: {context['upcoming_count']}")
        lines.append(f"- Pending punch list items: {context['pending_punch_list']}")
        lines.append(
            f"- Foundation: {context['foundation_score']}% match, "
            f"{context['foundation_samples']} samples"
        )
        if context["pillars"]:
            lines.append(f"- Content pillars: {', '.join(context['pillars'])}")
        lines.append("")

        # Task
        lines.append(
            "TASK: Generate today's walk-through. "
            "Respond with a JSON object (no markdown fences) in this exact shape:\n"
            '{\n'
            '  "greeting": "Morning walk-through line (max 12 words)",\n'
            '  "actions": [\n'
            '    {\n'
            '      "action_type": "draft_post",\n'
            '      "rationale": "1 sentence, 15-25 words, Brick voice",\n'
            '      "payload": {"topic": "...", "pillar": "...", "bucket": "viral"}\n'
            '    }\n'
            '  ]\n'
            '}\n'
            "Propose 2-4 actions. Only action_types allowed at Draftsman tier: "
            "suggest_post_idea, draft_post. "
            "Rationale must pass the foreman test — no corporate-speak, reference real data."
        )

        return "\n".join(lines)

    async def _call_claude_planning(self, user_prompt: str) -> Dict[str, Any]:
        """Call claude-sonnet-4-5 with brick-voice system prompt. Returns parsed plan dict."""
        if not settings.anthropic_api_key:
            logger.warning("[brick.planning] No ANTHROPIC_API_KEY — returning fallback plan")
            return {
                "greeting": "Morning. Walk-through ready.",
                "actions": [
                    {
                        "action_type": "draft_post",
                        "rationale": "Foundation has samples ready. Draft one post to keep the calendar moving.",
                        "payload": {"topic": "local market update", "bucket": "brand"},
                    }
                ],
            }

        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=_BRICK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = message.content[0].text.strip()
        # Strip markdown fences if Claude wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[brick.planning] Claude returned non-JSON: %s", raw[:200])
            return {
                "greeting": "Morning. Walk-through ready.",
                "actions": [],
                "_raw": raw,
            }

    async def _dispatch_action(
        self, action: BrickAction, session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Execute a BrickAction. Phase 3A supports draft_post and suggest_post_idea.
        Future tiers add publish_post, cut_clip, send_guest_email, etc.
        """
        action_type = action.action_type
        payload = action.payload or {}

        if action_type in ("draft_post", "suggest_post_idea"):
            # Phase 3A: create a draft Post with the proposed topic
            topic = payload.get("topic", "content post")
            bucket = payload.get("bucket", "brand")
            pillar = payload.get("pillar", "")

            post = Post(
                location_id=action.location_id,
                bucket=bucket,
                base_caption=f"[Brick draft — {topic}]",
                status="draft",
                source="brick_proposed",
            )
            session.add(post)
            await session.flush()

            logger.info(
                "[brick.action] Created draft post %s for topic=%r",
                post.id, topic,
            )
            return {"post_id": str(post.id), "topic": topic, "status": "draft"}

        logger.warning("[brick.action] Unknown action_type %r — no-op", action_type)
        return {"action_type": action_type, "status": "no_op"}

    async def _touch_memories(self, memory_ids: List[str]) -> None:
        """Update last_referenced_at for all memories read during this planning run."""
        if not memory_ids:
            return
        async with async_session() as session:
            uuids = [uuid.UUID(mid) for mid in memory_ids]
            await session.execute(
                update(BrickMemory)
                .where(BrickMemory.id.in_(uuids))
                .values(last_referenced_at=datetime.utcnow())
            )
            await session.commit()

    async def _notify_telegram(self, greeting: str, action_count: int) -> None:
        """Send walk-through ready notification via Telegram."""
        try:
            from pipeline.telegram import send as tg_send
            msg = (
                f"🔨 *Walk-through ready*\n"
                f"{greeting}\n"
                f"{action_count} item{'s' if action_count != 1 else ''} on the punch list.\n"
                f"http://localhost:8765/walkthrough"
            )
            await tg_send(msg)
        except Exception as exc:
            logger.warning("[brick.notify] Telegram notification failed: %s", exc)


# ── Cron handler ──────────────────────────────────────────────────────────────

async def expire_stale_actions() -> int:
    """
    Mark brick_actions older than 7 days as expired.
    Called daily by APScheduler alongside run_daily_planning.
    Returns count of expired rows.
    """
    cutoff = datetime.utcnow() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            update(BrickAction)
            .where(
                and_(
                    BrickAction.status == "pending",
                    BrickAction.requested_at < cutoff,
                )
            )
            .values(status="expired")
        )
        await session.commit()
        expired = result.rowcount
    if expired:
        logger.info("[brick.expire] Expired %d stale actions", expired)
    return expired


async def run_planning_for_default_location() -> Dict[str, Any]:
    """
    Convenience wrapper called by APScheduler.
    Uses settings.titan_location_id (single-tenant Phase 3A pattern).
    """
    location_id = get_current_location_id()
    agent = BrickAgent()
    await expire_stale_actions()
    return await agent.run_daily_planning(location_id)


# ── Module-level singleton ────────────────────────────────────────────────────

brick_agent = BrickAgent()
