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

import asyncio
import json
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
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
    Clip,
    FoundationScore,
    Location,
    Post,
    PostVariant,
    Project,
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

# Dispatch results that mean "nothing actually happened."
#
# These earn no brick_track_record row. The distinction matters because Wave 1
# made total_actions and success_rate the promotion gate: counting a no-op or a
# skipped precondition as a success would let Brick climb the ladder on work he
# never did, and counting it as a failure would punish him for a missing TikTok
# token. Neither is true, so neither is recorded.
NON_WORK_STATUSES = frozenset({
    "no_op",          # unknown action_type fell through the dispatch table
    "skipped",        # precondition absent (no rendered clip, no linked guest)
    "needs_tiktok",   # a human must reconnect an account
    "needs_gmail",
    "needs_account",
})


# What a tier costs to reach.
#
# Before Wave 1, promote() was a bare one-step increment: three clicks took Brick
# from Owner-Builder to GC with zero completed work behind him, which meant the
# "trust ladder" had no trust mechanism at all — just a label and a ceremony modal.
#
# Draftsman is deliberately free. It only suggests and drafts; nothing executes,
# so there is no track record to earn first and gating it would be a deadlock —
# Brick cannot build a record without being allowed to act. Everything above it
# is earned from brick_track_record.
#
# `clean_days` is a recency gate: a tier that lets Brick reach an audience should
# not be handed over days after a failure.
TIER_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "owner_builder": {
        "min_actions": 0,
        "min_success_rate": 0.0,
        "clean_days": 0,
        "rationale": "The floor — Brick does nothing without you. Always reachable.",
    },
    "draftsman": {
        "min_actions": 0,
        "min_success_rate": 0.0,
        "clean_days": 0,
        "rationale": "Suggests and drafts only. Nothing executes, so nothing is owed first.",
    },
    "bricklayer": {
        "min_actions": 5,
        "min_success_rate": 80.0,
        "clean_days": 0,
        "rationale": "Queues drafts for review. A handful of clean drafts first.",
    },
    "foreman": {
        "min_actions": 15,
        "min_success_rate": 85.0,
        "clean_days": 7,
        "rationale": "Publishes and cuts clips — the first tier that reaches an audience.",
    },
    "gc": {
        "min_actions": 40,
        "min_success_rate": 90.0,
        "clean_days": 14,
        "rationale": "Emails guests, pitches sponsors, moves the calendar. Spends your name.",
    },
}

# Which tier is required to propose/execute each action type
ACTION_TIER_MAP: Dict[str, str] = {
    "suggest_post_idea":    "draftsman",
    "draft_post":           "draftsman",
    "queue_draft":          "bricklayer",
    "publish_post":         "foreman",
    "cut_clip":             "foreman",
    "write_show_notes":     "foreman",
    "adjust_calendar":      "foreman",
    "guest_asset_package":  "draftsman",
    "send_guest_email":     "gc",
    "pitch_sponsor":        "gc",
    "adjust_vyral_mix":     "gc",
    "replan_calendar":      "gc",
    # The Crew (AGENTS_HUB_SPEC §2.3). A human's approval IS the gate, so a
    # Draftsman-tier user can approve — the guest_asset_package precedent. The
    # agent's own commit_tier only governs unattended (Brick-initiated) commits.
    "agent_commit":         "draftsman",
}

# ── Wave 4: the generators Brick could not reach ──────────────────────────────
#
# PodClick ships ~20 content generators behind /api/yt/*, /api/brand/* and
# /api/social/*. None appeared in ACTION_TIER_MAP, so promoting Brick to GC gave
# him authority over twelve action types while the app's twenty best capabilities
# stayed out of his reach entirely.
#
# These are reached by calling the app's OWN route in-process (httpx
# ASGITransport against studio_app) rather than reimplementing them. Every
# generator's logic lives inline in a main.py route handler that takes a Request,
# so it is not importable as a plain function, and copying twenty prompt bodies
# into this file would guarantee drift. The in-process call reuses the exact
# route — same validation, same Foundation gate, same model, same output shape —
# with no network hop and no self-HTTP.
#
# It targets studio_app, the INNER app, deliberately: DeploymentBoundary governs
# external HTTP, and this is the owner's own agent acting internally, not a
# request from outside. That makes this registry a security boundary in its own
# right, so it is a strict ALLOWLIST — never a prefix or a wildcard. Nothing that
# publishes, disconnects an account, uploads a file or changes configuration is
# listed here; publishing goes through publish_post and SocialService (contract #5).
#
# Tier rationale: a generator produces text for review and reaches no audience, so
# draftsman is the honest floor. The two that spend a real external resource —
# competitor_spy burns YouTube Data API quota (10k units/day, search.list costs
# 100 a call) and cover_forge generates images — sit at bricklayer.
GENERATOR_ACTIONS: Dict[str, Dict[str, Any]] = {
    # ── YouTube / Click Studio ──
    "yt_script_formula":  {"path": "/api/yt/script-formula",  "tier": "draftsman",
                           "label": "Script Lab outline"},
    "yt_script":          {"path": "/api/yt/script",          "tier": "draftsman",
                           "label": "YouTube script"},
    "yt_seo_package":     {"path": "/api/yt/seo-package",     "tier": "draftsman",
                           "label": "title/description/tags"},
    "yt_content_calendar":{"path": "/api/yt/content-calendar","tier": "draftsman",
                           "label": "Trend Radar topics"},
    "yt_pillar_plan":     {"path": "/api/yt/pillar-plan",     "tier": "draftsman",
                           "label": "pillar content plan"},
    "yt_adapt_concept":   {"path": "/api/yt/adapt-concept",   "tier": "draftsman",
                           "label": "concept remixed for the market"},
    "yt_scout_remix":     {"path": "/api/yt/scout-remix",     "tier": "draftsman",
                           "label": "competitor concept in your voice"},
    "yt_video_advisor":   {"path": "/api/yt/video-advisor",   "tier": "draftsman",
                           "label": "video strategy advice"},
    "yt_repurpose":       {"path": "/api/yt/repurpose",       "tier": "draftsman",
                           "label": "repurposed shorts/IG/TikTok/blog"},
    "yt_lead_page":       {"path": "/api/yt/lead-page",       "tier": "draftsman",
                           "label": "lead page copy"},
    "yt_competitor_spy":  {"path": "/api/yt/competitor-spy",  "tier": "bricklayer",
                           "label": "Market Scout run",
                           "cost": "spends YouTube Data API quota"},
    "yt_cover_forge":     {"path": "/api/yt/cover-forge",     "tier": "bricklayer",
                           "label": "thumbnail variants",
                           "cost": "generates images"},

    # ── Brand Studio ──
    "brand_intake":        {"path": "/api/brand/intake",        "tier": "draftsman",
                            "label": "brand brief"},
    "brand_bio_pack":      {"path": "/api/brand/bio-pack",      "tier": "draftsman",
                            "label": "platform bios"},
    "brand_content_plan":  {"path": "/api/brand/content-plan",  "tier": "draftsman",
                            "label": "12-month content plan"},
    "brand_conversion":    {"path": "/api/brand/conversion",    "tier": "draftsman",
                            "label": "lead magnet + VSL + emails"},
    "brand_profile_audit": {"path": "/api/brand/profile-audit", "tier": "draftsman",
                            "label": "profile audit + bio rewrite"},

    # ── Social Studio ──
    "social_forge":     {"path": "/api/social/forge",     "tier": "draftsman",
                         "label": "4-platform social posts"},
    "social_hashtags":  {"path": "/api/social/hashtags",  "tier": "draftsman",
                         "label": "hashtag sets"},
    "social_repurpose": {"path": "/api/social/repurpose", "tier": "draftsman",
                         "label": "post angles from a URL or transcript"},
}

# Generators join the same gate the twelve core actions use, so a tier change
# moves everything at once and nothing needs a second permission check.
for _gen_action, _gen_spec in GENERATOR_ACTIONS.items():
    ACTION_TIER_MAP[_gen_action] = _gen_spec["tier"]

# The Crew: Brick may START an agent on his own at max(run_tier, spend_tier).
# Registered from the registry the same way generators are, so adding an agent
# spec gates it automatically. The registry is pure data (no import of this
# module), so this top-level import cannot cycle. Teaching the planning loop to
# propose these is wave 3; wave 2 only makes the permit machinery aware of them.
from services.agents.registry import REGISTRY as _AGENT_REGISTRY, agent_run_tier as _agent_run_tier  # noqa: E402

for _agent_id, _agent_spec in _AGENT_REGISTRY.items():
    ACTION_TIER_MAP["agent_run:" + _agent_id] = _agent_run_tier(_agent_spec)

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

# ── Chat tool definitions ─────────────────────────────────────────────────────

BRICK_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "propose_action",
        "description": (
            "Propose or execute a content action on behalf of the user. "
            "Creates a BrickAction row. If the action is within the current permit tier, "
            "it executes immediately and returns the result. If it requires a higher tier, "
            "it is queued to the punch list for user approval. "
            "Use when user asks you to draft, suggest, publish, or perform any content task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": [
                        "draft_post", "suggest_post_idea", "queue_draft",
                        "publish_post", "cut_clip", "write_show_notes",
                        "adjust_calendar", "send_guest_email",
                        "pitch_sponsor", "adjust_vyral_mix", "replan_calendar",
                    ],
                    "description": "The type of action to propose.",
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters. For draft_post: "
                        "{topic: str, pillar: str, bucket: str, platform: str}. "
                        "All fields optional."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": "1-2 sentence Brick-voice explanation of why this action now.",
                },
            },
            "required": ["action_type", "payload", "rationale"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Save a standing instruction to Brick's permanent memory. "
            "These instructions are injected into every future planning prompt. "
            "Use when user says 'remember', 'never', 'always', 'from now on', "
            "or any phrasing that indicates a persistent preference or rule."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The exact instruction to remember, verbatim.",
                },
                "category": {
                    "type": "string",
                    "description": "Category tag. One of: rule, preference, schedule, tone, topic.",
                    "default": "rule",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "forget",
        "description": (
            "Remove a standing instruction from Brick's memory. "
            "Use when user says to forget, ignore, or remove a previous instruction. "
            "Requires the memory_id — ask user to confirm which one if ambiguous."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "UUID string of the brick_memory row to soft-delete.",
                },
            },
            "required": ["memory_id"],
        },
    },
]

# Banned phrases from brick-voice skill — checked after each response
_BANNED_PHRASES: List[str] = [
    "I'm happy to",
    "I'd be glad to",
    "Great question",
    "That's a great",
    "Absolutely!",
    "Certainly!",
    "Of course!",
    "As an AI",
    "as a language model",
    "I apologize for",
    "I hope this helps",
    "Please let me know if",
    "Feel free to",
    "I've gone ahead and",
    "Just a heads-up",
    "Quick note that",
    "Unfortunately",
    "Moving forward",
    "At the end of the day",
    "Diving into",
    "Circling back",
    "Touching base",
    "Leverage",
    "Synergy",
    "Unlock",
    "Empower",
    "Cutting-edge",
    "State-of-the-art",
    "Best-in-class",
]


def _tier_rank(tier: str) -> int:
    """Return numeric rank for tier comparison. Higher = more autonomy."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 0


def _tier_allows(current_tier: str, required_tier: str) -> bool:
    """
    Return True if current_tier is at or above required_tier.

    Fails closed on an unrecognized `required_tier`. `_tier_rank` maps anything
    unknown to 0 (owner_builder), so without this guard a typo'd or newly added
    action tier would rank 0 and be permitted at *every* permit level — the gate
    silently inverting from "most restrictive" to "none at all". An unknown
    requirement is a programming error, and the safe answer to it is no.
    """
    if required_tier not in TIER_ORDER:
        logger.error(
            "[brick.permit] Unknown required tier %r — refusing. Valid tiers: %s",
            required_tier, ", ".join(TIER_ORDER),
        )
        return False
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

            # Idempotency: expire today's pending actions before creating new ones.
            # Prevents accumulation from multiple same-day planning runs.
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            await session.execute(
                sa_text(
                    "UPDATE brick_actions SET status = 'expired' "
                    "WHERE location_id = :loc AND status = 'pending' "
                    "AND requested_at >= :today"
                ),
                {"loc": uuid.UUID(location_id), "today": today_start},
            )
            await session.commit()

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

                # The planning loop proposes up to the permit the user has actually
                # granted. Before this, the cap was hard-coded to "draftsman", so
                # promoting Brick on the /permit screen changed a label and nothing
                # else. Proposing is still not executing: every action lands on the
                # punch list and execute_action re-checks the tier at approval time.
                if not _tier_allows(permit.current_tier, required_tier):
                    logger.info(
                        "[brick.planning] Skipping action %s — requires %s (current permit: %s)",
                        action_type, required_tier, permit.current_tier,
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

            try:
                result = await self._dispatch_action(action, session)
            except Exception as dispatch_err:
                # A raised dispatch used to write NO track record at all, which
                # made success_rate structurally 100% — a failure could not
                # lower it. Wave 1 turned that rate into the promotion gate, so
                # the omission stopped being cosmetic. Record, then re-raise so
                # the caller still surfaces the error.
                action.status = "failed"
                session.add(BrickTrackRecord(
                    location_id=action.location_id,
                    action_type=action.action_type,
                    outcome="failure",
                    action_metadata={
                        "action_id": action_id,
                        "error": f"{type(dispatch_err).__name__}: {dispatch_err}"[:500],
                    },
                ))
                await session.commit()
                logger.error(
                    "[brick.action] %s failed for action %s: %s",
                    action.action_type, action_id, dispatch_err,
                )
                raise

            action.status = "executed"

            # Only real work earns a track record. `outcome` was hardcoded
            # "success" regardless of what dispatch returned, so an unknown
            # action_type that fell through to no_op — or a skip for a missing
            # precondition — counted toward the ladder exactly like a published
            # post. No work means no credit and no penalty: the row is omitted so
            # both total_actions and success_rate stay honest.
            outcome_status = (result or {}).get("status")
            if outcome_status in NON_WORK_STATUSES:
                logger.info(
                    "[brick.action] %s returned %r — no track record written",
                    action.action_type, outcome_status,
                )
            else:
                session.add(BrickTrackRecord(
                    location_id=action.location_id,
                    action_type=action.action_type,
                    outcome="success",
                    action_metadata={"action_id": action_id, "result": result},
                ))
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

    # ── Conversational chat ───────────────────────────────────────────────────

    async def chat_stream(
        self,
        message: str,
        location_id: str,
        context_screen: str,
        context_data: Optional[Dict[str, Any]] = None,
    ):
        """
        Async generator — yields SSE-formatted strings.

        Format:
          data: {"t": "token text"}\\n\\n   — text token
          data: {"tool": "description"}\\n\\n — tool call result summary
          data: [DONE]\\n\\n                  — stream complete

        Saves user message + full Brick response to brick_messages.
        Checks response for banned phrases (non-blocking warning).
        """
        import json as _json

        if context_data is None:
            context_data = {}

        # 1 — Save user message
        await self._save_message("user", message, location_id, context_screen)

        # 2 — Load recent history (last 20 messages, oldest first)
        history = await self.list_messages(location_id, limit=20)

        # 3 — Load active memories
        memories = await self.get_active_memories(location_id)

        # 4 — Get current permit tier
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            current_tier = permit.current_tier

        # 5 — Build system prompt
        system_prompt = self._build_chat_system_prompt(memories, context_data, current_tier)

        # 6 — Build message list for Claude (normalize to alternating roles)
        msgs = self._normalize_history(history)
        msgs.append({"role": "user", "content": message})

        if not settings.anthropic_api_key:
            fallback = "Walk-through's up. What do you need?"
            await self._save_message("brick", fallback, location_id, context_screen)
            yield f"data: {_json.dumps({'t': fallback})}\n\n"
            yield "data: [DONE]\n\n"
            return

        async_client = _anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        collected_text: List[str] = []

        try:
            # Pass 1 — stream with tool capability
            async with async_client.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=1024,
                system=system_prompt,
                messages=msgs,
                tools=BRICK_TOOLS,
            ) as stream:
                async for text_token in stream.text_stream:
                    collected_text.append(text_token)
                    yield f"data: {_json.dumps({'t': text_token})}\n\n"

                final_msg = await stream.get_final_message()

            # Check for tool calls in the final message
            tool_use_blocks = [
                block for block in final_msg.content
                if hasattr(block, "type") and block.type == "tool_use"
            ]

            if tool_use_blocks:
                tool_results = []
                for tc in tool_use_blocks:
                    result = await self._execute_chat_tool(
                        tc.name, tc.input, location_id
                    )
                    summary = result.get("summary", tc.name)
                    yield f"data: {_json.dumps({'tool': summary})}\n\n"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": _json.dumps(result),
                        }
                    )

                # Pass 2 — follow-up with tool results (no tools this round)
                follow_msgs = msgs + [
                    {"role": "assistant", "content": final_msg.content},
                    {"role": "user", "content": tool_results},
                ]
                async with async_client.messages.stream(
                    model="claude-sonnet-4-5",
                    max_tokens=512,
                    system=system_prompt,
                    messages=follow_msgs,
                ) as stream2:
                    async for text_token in stream2.text_stream:
                        collected_text.append(text_token)
                        yield f"data: {_json.dumps({'t': text_token})}\n\n"

        except Exception as exc:
            logger.error("[brick.chat] Stream error: %s", exc)
            err_msg = "Lost the signal for a second. Try again."
            collected_text.append(err_msg)
            yield f"data: {_json.dumps({'t': err_msg})}\n\n"

        full_response = "".join(collected_text)

        # 7 — Save Brick response
        await self._save_message("brick", full_response, location_id, context_screen)

        # 8 — Non-blocking voice quality check
        self._check_voice_quality(full_response)

        yield "data: [DONE]\n\n"

    async def list_messages(
        self, location_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Return last N messages for location, oldest first."""
        async with async_session() as session:
            rows = await session.execute(
                sa_text(
                    "SELECT id, role, content, context_screen, created_at "
                    "FROM brick_messages "
                    "WHERE location_id = :loc "
                    "ORDER BY created_at DESC "
                    "LIMIT :lim"
                ),
                {"loc": location_id, "lim": limit},
            )
            msgs = [
                {
                    "id": str(r[0]),
                    "role": r[1],
                    "content": r[2],
                    "context_screen": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows.fetchall()
            ]
        msgs.reverse()  # Oldest first for display
        return msgs

    def _build_chat_system_prompt(
        self,
        memories: List[Dict[str, Any]],
        context_data: Dict[str, Any],
        current_tier: str,
    ) -> str:
        """Compose the full system prompt for a chat turn."""
        lines = [_BRICK_SYSTEM_PROMPT]

        lines.append(f"\nCURRENT PERMIT TIER: {current_tier}")
        lines.append(
            "ACTIONS YOU CAN EXECUTE IMMEDIATELY (within tier): "
            + ", ".join(
                k for k, v in ACTION_TIER_MAP.items()
                if _tier_allows(current_tier, v)
            )
        )
        lines.append(
            "ACTIONS THAT GO TO PUNCH LIST (above tier): "
            + ", ".join(
                k for k, v in ACTION_TIER_MAP.items()
                if not _tier_allows(current_tier, v)
            )
        )

        if memories:
            lines.append("\nSTANDING INSTRUCTIONS (always honor these):")
            for m in memories:
                lines.append(f"- {m['content']}")

        screen = context_data.get("screen", "")
        if screen:
            lines.append(f"\nUSER IS ON SCREEN: {screen}")

        if context_data.get("this_week_posts"):
            lines.append("\nTHIS WEEK'S CALENDAR:")
            for p in context_data["this_week_posts"]:
                lines.append(
                    f"- {p.get('bucket','?')} post — {p.get('status','?')} — "
                    f"{p.get('scheduled_at', '?')}"
                )

        if context_data.get("pending_actions"):
            lines.append("\nOPEN PUNCH LIST:")
            for a in context_data["pending_actions"]:
                lines.append(f"- {a.get('action_type','?')}: {a.get('rationale','')}")

        if context_data.get("track_record"):
            tr = context_data["track_record"]
            lines.append(
                f"\nTRACK RECORD: {tr.get('total_actions',0)} actions, "
                f"{tr.get('completed',0)} completed, {tr.get('approvals',0)} approvals"
            )

        if context_data.get("foundation_samples") is not None:
            lines.append(
                f"\nFOUNDATION: {context_data['foundation_samples']} samples"
            )

        lines.append(
            "\nWhen user asks you to perform a content action, use the propose_action tool. "
            "When user says 'remember', 'never', 'always', or 'from now on', use the remember tool. "
            "Keep replies 1-3 sentences. Lead with the action or answer, not with preamble."
        )

        return "\n".join(lines)

    def _normalize_history(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert brick_messages rows to Claude API format.
        Merges consecutive same-role messages to satisfy Claude's alternating requirement.
        Maps role 'brick' -> 'assistant'.
        """
        if not messages:
            return []

        normalized: List[Dict[str, Any]] = []
        for msg in messages:
            role = "assistant" if msg["role"] == "brick" else "user"
            content = msg.get("content", "")
            if normalized and normalized[-1]["role"] == role:
                # Merge into previous
                normalized[-1]["content"] += "\n\n" + content
            else:
                normalized.append({"role": role, "content": content})

        # Claude requires first message to be from user
        if normalized and normalized[0]["role"] == "assistant":
            normalized = normalized[1:]

        return normalized

    async def _execute_chat_tool(
        self, tool_name: str, tool_input: Dict[str, Any], location_id: str
    ) -> Dict[str, Any]:
        """Route a tool call to the appropriate handler."""
        if tool_name == "propose_action":
            return await self._tool_propose_action(tool_input, location_id)
        if tool_name == "remember":
            return await self._tool_remember(tool_input, location_id)
        if tool_name == "forget":
            return await self._tool_forget(tool_input, location_id)
        return {"error": f"Unknown tool: {tool_name}", "summary": f"Unknown: {tool_name}"}

    async def _tool_propose_action(
        self, tool_input: Dict[str, Any], location_id: str
    ) -> Dict[str, Any]:
        """
        Tool handler: propose_action.
        Creates BrickAction, executes if within tier, queues if above tier.
        For draft_post: generates real content via Foundation when available.
        """
        action_type = tool_input.get("action_type", "draft_post")
        payload = tool_input.get("payload", {})
        rationale = tool_input.get("rationale", "")
        required_tier = ACTION_TIER_MAP.get(action_type, "draftsman")

        # Create the action row
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            action = BrickAction(
                location_id=uuid.UUID(location_id),
                action_type=action_type,
                status="pending",
                payload=payload,
                rationale=rationale,
                actor_type="brick",
                expires_at=datetime.utcnow() + timedelta(days=7),
            )
            session.add(action)
            await session.commit()
            action_id = str(action.id)
            current_tier = permit.current_tier

        if _tier_allows(current_tier, required_tier):
            # Execute immediately — within tier
            try:
                result = await self.execute_action(action_id)
                return {
                    "status": "executed",
                    "action_id": action_id,
                    "result": result,
                    "summary": (
                        f"Drafted post on '{payload.get('topic', action_type)}' — "
                        "on the punch list for your review."
                    ),
                }
            except Exception as exc:
                logger.error("[brick.tool.propose] Execute failed: %s", exc)
                return {
                    "status": "failed",
                    "action_id": action_id,
                    "error": str(exc),
                    "summary": f"Action failed: {exc}",
                }
        else:
            # Above tier — queued to punch list
            return {
                "status": "queued",
                "action_id": action_id,
                "required_tier": required_tier,
                "current_tier": current_tier,
                "summary": (
                    f"Queued {action_type} to punch list — "
                    f"needs {required_tier} permit (you're at {current_tier})."
                ),
            }

    async def _tool_remember(
        self, tool_input: Dict[str, Any], location_id: str
    ) -> Dict[str, Any]:
        """Tool handler: remember — save a standing instruction."""
        content = (tool_input.get("content") or "").strip()
        category = (tool_input.get("category") or "rule").strip()

        if not content:
            return {"error": "No content provided", "summary": "Nothing to remember."}

        memory_id = await self.remember(location_id, content, category)
        return {
            "status": "saved",
            "memory_id": memory_id,
            "content": content,
            "category": category,
            "summary": f"Saved to standing instructions: '{content[:60]}'",
        }

    async def _tool_forget(
        self, tool_input: Dict[str, Any], location_id: str
    ) -> Dict[str, Any]:
        """Tool handler: forget — soft-delete a standing instruction."""
        memory_id = (tool_input.get("memory_id") or "").strip()
        if not memory_id:
            return {"error": "memory_id required", "summary": "No memory ID provided."}
        try:
            found = await self.forget(memory_id)
            if found:
                return {"status": "forgotten", "memory_id": memory_id, "summary": "Instruction removed."}
            return {"status": "not_found", "memory_id": memory_id, "summary": "Instruction not found."}
        except Exception as exc:
            return {"error": str(exc), "summary": f"Couldn't remove: {exc}"}

    async def _generate_draft_caption(
        self,
        topic: str,
        platform: str,
        brand_ctx: Any,
    ) -> str:
        """
        Generate a post caption in the user's voice using Foundation samples.
        Called by _dispatch_action for draft_post when Foundation is ready.
        brand_ctx is a BrandContext Pydantic model from get_brand_context().
        """
        voice_samples = getattr(brand_ctx, "voice_samples", []) or []
        samples_text = "\n\n---\n\n".join(
            s.text for s in voice_samples[:3] if getattr(s, "text", None)
        )

        vocabulary = getattr(brand_ctx, "vocabulary", None)
        vocab_yes_list = getattr(vocabulary, "use", None) or []
        vocab_no_list = getattr(vocabulary, "avoid", None) or []
        vocab_yes = ", ".join(vocab_yes_list[:6])
        vocab_no = ", ".join(vocab_no_list[:6])

        bp = getattr(brand_ctx, "brand_profile", None)
        market = getattr(bp, "market_city", "") or ""
        niche = getattr(bp, "niche_primary", "real estate") or "real estate"

        system = (
            "You write social media posts for a real estate professional. "
            "Match their authentic voice exactly from the examples below. "
            "No AI-speak, no corporate-speak, no excessive enthusiasm.\n\n"
        )
        if samples_text:
            system += f"VOICE EXAMPLES:\n\n{samples_text}\n\n"
        if vocab_yes:
            system += f"VOCABULARY TO USE: {vocab_yes}\n"
        if vocab_no:
            system += f"VOCABULARY TO AVOID: {vocab_no}\n"
        if market:
            system += f"MARKET: {market}\n"

        user_msg = (
            f"Write a {platform} post about: {topic}. "
            f"Niche: {niche}. "
            "Match the voice examples. Keep it 2-4 short paragraphs or punchy lines. "
            "No hashtags unless it's Instagram."
        )

        try:
            client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text.strip()
        except Exception as exc:
            logger.warning("[brick.draft] Caption generation failed: %s", exc)
            return f"[Draft needed — {topic}]"

    def _check_voice_quality(self, text: str) -> None:
        """
        Non-blocking check for banned phrases in a Brick response.
        Logs a warning for each hit — does not block or modify the response.
        """
        lower = text.lower()
        for phrase in _BANNED_PHRASES:
            if phrase.lower() in lower:
                logger.warning(
                    "[brick.voice.quality] Banned phrase detected: %r in response: %r",
                    phrase,
                    text[:100],
                )

    async def _save_message(
        self,
        role: str,
        content: str,
        location_id: str,
        context_screen: str,
    ) -> None:
        """Persist a chat message to brick_messages."""
        async with async_session() as session:
            msg = BrickMessage(
                location_id=uuid.UUID(location_id),
                role=role,
                content=content,
                context_screen=context_screen,
            )
            session.add(msg)
            await session.commit()

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

    async def track_record(self, location_id: str) -> Dict[str, Any]:
        """
        Aggregate brick_track_record for one location.

        The table is an event log — one row per executed action — not the
        pre-aggregated counters the SOW sketched, so the numbers are computed here
        and shared by both the eligibility check and the permit screen.
        """
        loc_uuid = uuid.UUID(location_id)
        async with async_session() as session:
            rows = await session.execute(
                select(BrickTrackRecord.outcome, BrickTrackRecord.executed_at).where(
                    BrickTrackRecord.location_id == loc_uuid
                )
            )
            records = rows.all()

        total = len(records)
        success = sum(1 for outcome, _ in records if outcome == "success")
        failure = sum(1 for outcome, _ in records if outcome == "failure")
        rejected = sum(1 for outcome, _ in records if outcome == "rejected")

        bad_times = [
            when for outcome, when in records
            if outcome in ("failure", "rejected") and when is not None
        ]
        last_bad = max(bad_times) if bad_times else None
        days_clean = None
        if last_bad is not None:
            reference = datetime.now(last_bad.tzinfo) if last_bad.tzinfo else datetime.utcnow()
            days_clean = max(0, (reference - last_bad).days)

        return {
            "total_actions": total,
            "success_count": success,
            "failure_count": failure,
            "rejected_count": rejected,
            # Rate over ALL recorded actions, so a rejection costs the same as a
            # failure. Brick asking for the wrong thing is a trust event too.
            "success_rate": round(success / total * 100, 1) if total else 0.0,
            "last_setback_at": last_bad.isoformat() if last_bad else None,
            "days_since_setback": days_clean,
        }

    async def eligibility(self, location_id: str) -> Dict[str, Any]:
        """
        What tier Brick could hold, and what is missing for the next one.

        Returns the whole ladder so the permit screen can show why a tier is out
        of reach rather than just greying out a button.
        """
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            await session.commit()
            current_tier = permit.current_tier

        record = await self.track_record(location_id)
        ladder = []
        for tier in TIER_ORDER:
            req = TIER_REQUIREMENTS[tier]
            unmet = self._unmet_requirements(req, record)
            ladder.append({
                "tier": tier,
                "rationale": req["rationale"],
                "requirements": {
                    "min_actions": req["min_actions"],
                    "min_success_rate": req["min_success_rate"],
                    "clean_days": req["clean_days"],
                },
                "eligible": not unmet,
                "unmet": unmet,
                "is_current": tier == current_tier,
                "held": _tier_rank(tier) <= _tier_rank(current_tier),
            })

        rank = _tier_rank(current_tier)
        next_tier = TIER_ORDER[rank + 1] if rank < len(TIER_ORDER) - 1 else None
        next_unmet = (
            self._unmet_requirements(TIER_REQUIREMENTS[next_tier], record)
            if next_tier else ["Already at GC — the top of the ladder."]
        )

        return {
            "current_tier": current_tier,
            "next_tier": next_tier,
            "can_promote": bool(next_tier) and not next_unmet,
            "unmet": next_unmet,
            "track_record": record,
            "ladder": ladder,
        }

    @staticmethod
    def _unmet_requirements(req: Dict[str, Any], record: Dict[str, Any]) -> List[str]:
        """Plain-language list of what this tier still needs. Empty means eligible."""
        unmet: List[str] = []
        if record["total_actions"] < req["min_actions"]:
            short = req["min_actions"] - record["total_actions"]
            unmet.append(
                f"{short} more completed action{'s' if short != 1 else ''} "
                f"({record['total_actions']} of {req['min_actions']})"
            )
        if req["min_success_rate"] and record["total_actions"]:
            if record["success_rate"] < req["min_success_rate"]:
                unmet.append(
                    f"success rate {record['success_rate']}% is below "
                    f"{req['min_success_rate']}%"
                )
        if req["clean_days"]:
            days = record["days_since_setback"]
            if days is not None and days < req["clean_days"]:
                unmet.append(
                    f"{req['clean_days'] - days} more clean day"
                    f"{'s' if req['clean_days'] - days != 1 else ''} since the last setback"
                )
        return unmet

    async def promote(
        self, location_id: str, user_id: str, override: bool = False
    ) -> Dict[str, Any]:
        """
        Advance permit tier one step, gated on the track record.

        `override=True` skips the gate for a deliberate operator decision — it is
        recorded in the audit trail rather than being silent, because an ungated
        promotion is exactly the thing this method exists to prevent.
        """
        async with async_session() as session:
            permit = await self._get_or_create_permit(session, location_id)
            current_rank = _tier_rank(permit.current_tier)
            if current_rank >= len(TIER_ORDER) - 1:
                return {"ok": False, "error": "Already at GC tier", "tier": permit.current_tier}

            new_tier = TIER_ORDER[current_rank + 1]
            record = await self.track_record(location_id)
            unmet = self._unmet_requirements(TIER_REQUIREMENTS[new_tier], record)

            if unmet and not override:
                logger.info(
                    "[brick.permit] Promotion to %s refused for %s — unmet: %s",
                    new_tier, location_id, "; ".join(unmet),
                )
                return {
                    "ok": False,
                    "error": f"Brick has not earned {new_tier} yet.",
                    "tier": permit.current_tier,
                    "target_tier": new_tier,
                    "unmet": unmet,
                    "track_record": record,
                }

            if unmet and override:
                from services.audit import write_audit_log
                await write_audit_log(
                    location_id,
                    "brick.permit.override",
                    {
                        "from_tier": permit.current_tier,
                        "to_tier": new_tier,
                        "unmet": unmet,
                        "track_record": record,
                    },
                    actor_type="user",
                    actor_id=str(user_id),
                )
                logger.warning(
                    "[brick.permit] OVERRIDE — promoted to %s for %s despite: %s",
                    new_tier, location_id, "; ".join(unmet),
                )

            permit.current_tier = new_tier
            permit.promoted_at = datetime.utcnow()
            permit.promoted_by = uuid.UUID(user_id)
            permit.updated_at = datetime.utcnow()
            await session.commit()

        logger.info("[brick.permit] Promoted to %s for location %s", new_tier, location_id)
        return {"ok": True, "tier": new_tier, "overridden": bool(unmet and override)}

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
        # Return None when score has never been computed — not 0.0 (misleading to Brick)
        foundation_score = round(foundation_row[0] * 100, 1) if (foundation_row and foundation_row[0] is not None) else None

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
        if context["foundation_score"] is not None:
            lines.append(
                f"- Foundation: {context['foundation_score']}% voice cohesion, "
                f"{context['foundation_samples']} samples"
            )
        else:
            lines.append(
                f"- Foundation: score not yet computed, "
                f"{context['foundation_samples']} samples loaded"
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
            "RULES:\n"
            "- Propose EXACTLY 3-5 PRIORITIZED actions. No more. Choose only the highest-impact items.\n"
            "- Only action_types allowed at Draftsman tier: suggest_post_idea, draft_post.\n"
            "- Do NOT list variations of the same idea. "
            "If multiple drafts would cover similar ground (educational, tactical, informational posts), "
            "consolidate them into one best action.\n"
            "- Each action must cover a distinct topic, format, or intent.\n"
            "- Rationale must pass the foreman test — no corporate-speak, reference real data."
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
            topic = payload.get("topic", "content post")
            bucket = payload.get("bucket", "brand")
            platform = payload.get("platform", "linkedin")

            # Attempt Foundation-powered caption generation
            caption = f"[Brick draft — {topic}]"
            try:
                from services.foundation import get_brand_context
                from schemas.foundation import BrandContextTaskType as _BrandTaskType
                async with async_session() as ctx_session:
                    brand_ctx = await get_brand_context(
                        ctx_session,
                        str(action.location_id),
                        _BrandTaskType.linkedin_post,
                        topic=topic,
                    )
                caption = await self._generate_draft_caption(topic, platform, brand_ctx)
                logger.info("[brick.action] Foundation caption generated for topic=%r", topic)
            except Exception as fnd_err:
                logger.warning(
                    "[brick.action] Foundation not ready, using placeholder: %s", fnd_err
                )

            post = Post(
                location_id=action.location_id,
                bucket=bucket,
                base_caption=caption,
                status="draft",
                source="brick_proposed",
            )
            session.add(post)
            await session.flush()

            logger.info(
                "[brick.action] Created draft post %s for topic=%r",
                post.id, topic,
            )
            return {"post_id": str(post.id), "topic": topic, "status": "draft", "caption": caption}

        # Wave 3 — the eight action types that were in ACTION_TIER_MAP but fell
        # through to no_op, so promoting Brick changed a label and nothing else.
        # Each delegates to a method rather than growing this if-chain.
        branch = {
            "cut_clip": self._dispatch_cut_clip,
            "queue_draft": self._dispatch_queue_draft,
            "publish_post": self._dispatch_publish_post,
            "write_show_notes": self._dispatch_write_show_notes,
            "adjust_calendar": self._dispatch_adjust_calendar,
            "send_guest_email": self._dispatch_send_guest_email,
            "pitch_sponsor": self._dispatch_pitch_sponsor,
            "adjust_vyral_mix": self._dispatch_adjust_vyral_mix,
            "replan_calendar": self._dispatch_replan_calendar,
        }.get(action_type)
        if branch is not None:
            return await branch(action, payload, session)

        # Wave 4 — the ~20 content generators behind /api/yt, /api/brand and
        # /api/social. One branch serves all of them off the registry.
        if action_type in GENERATOR_ACTIONS:
            return await self._dispatch_generator(action, payload, session)

        if action_type == "agent_commit":
            # The Crew's single commit path: approving here (Walk-through punch
            # list) or from /agents both land in jobs.commit_job, which is
            # idempotent per job. A raised commit propagates so execute_action
            # records the failure against the ladder.
            from services.agents import jobs as _agent_jobs

            job_id = str(payload.get("job_id") or "")
            commit_result = await _agent_jobs.commit_job(job_id)
            return {
                "status": "committed",
                "job_id": job_id,
                "agent_id": payload.get("agent_id", ""),
                "summary": payload.get("summary", ""),
                "commit_result": commit_result,
            }

        if action_type == "guest_asset_package":
            # The package (Drive folder + uploads + drafted email) was already built
            # at Closing by _build_guest_asset_package; the punch-list payload carries
            # the draft + Drive link. Approving it stamps the guest as delivered.
            # When Gmail send-as lands (Phase 6), the actual send happens HERE.
            gid = payload.get("guest_id", "")
            try:
                from pathlib import Path as _P
                import json as _json2
                gfile = _P(__file__).resolve().parent.parent / "data" / "guests.json"
                if gfile.exists():
                    arr = _json2.loads(gfile.read_text())
                    for g in arr:
                        if g.get("id") == gid:
                            g["assets_sent_at"] = datetime.utcnow().isoformat()
                    gfile.write_text(_json2.dumps(arr, indent=2))
            except Exception as ferr:
                logger.warning("[brick.action] asset_package guest stamp failed: %s", ferr)
            return {
                "guest_id": gid,
                "guest_name": payload.get("guest_name", ""),
                "recipient": payload.get("recipient", ""),
                "drive_url": payload.get("drive_url", ""),
                "email": payload.get("email", ""),
                "status": "delivered",
            }

        logger.warning("[brick.action] Unknown action_type %r — no-op", action_type)
        return {"action_type": action_type, "status": "no_op"}

    async def _dispatch_cut_clip(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Post an already-rendered clip to TikTok. Foreman tier.

        This never renders. Rendering belongs to the Ship It pipeline, which is
        deterministic and already tested by use; Brick's job here is to choose a
        rendered clip and publish it. Selection is by explicit `clip_id`, else the
        highest virality_score rendered clip on the given project.

        Honest reporting is the point of the return value: TikTok decides the
        actual privacy level (an unaudited app only gets SELF_ONLY), so we report
        what landed rather than what we asked for.
        """
        from pipeline.tiktok import TikTokAuthError, upload_clip

        clip_id = payload.get("clip_id")
        project_id = payload.get("project_id")
        clip: Optional[Clip] = None

        if clip_id:
            clip = await session.get(Clip, uuid.UUID(str(clip_id)))
            if clip is not None and str(clip.location_id) != str(action.location_id):
                raise PermissionError(
                    f"Clip {clip_id} belongs to another location — refusing to post"
                )
        elif project_id:
            rows = await session.execute(
                select(Clip)
                .where(
                    and_(
                        Clip.project_id == uuid.UUID(str(project_id)),
                        Clip.location_id == action.location_id,
                        Clip.status.in_(("rendered", "approved")),
                        Clip.rendered_url.isnot(None),
                    )
                )
                .order_by(Clip.virality_score.desc().nullslast())
                .limit(1)
            )
            clip = rows.scalars().first()
        else:
            raise ValueError("cut_clip payload needs clip_id or project_id")

        if clip is None:
            return {
                "action_type": "cut_clip",
                "status": "skipped",
                "reason": "no rendered clip available to post",
            }
        if not clip.rendered_url:
            return {
                "action_type": "cut_clip",
                "status": "skipped",
                "reason": f"clip {clip.id} has not been rendered yet",
                "clip_id": str(clip.id),
            }

        # Prefer the Foundation-generated social caption; fall back to the hook.
        title = (payload.get("title") or clip.clip_caption or clip.hook_text or "").strip()
        if not title:
            title = "New clip"

        duration = None
        if clip.source_end_seconds is not None and clip.source_start_seconds is not None:
            duration = float(clip.source_end_seconds) - float(clip.source_start_seconds)

        requested_privacy = payload.get("privacy_level") or "PUBLIC_TO_EVERYONE"

        try:
            result = await upload_clip(
                video_path=clip.rendered_url,
                title=title,
                schedule_time=payload.get("schedule_time"),
                privacy_level=requested_privacy,
                duration_sec=duration,
                progress_cb=lambda m: logger.info("[brick.cut_clip] %s", m),
            )
        except TikTokAuthError as auth_err:
            # Distinguish "needs a human to reconnect" from "the post failed".
            # execute_action records the raised outcome; a bare failure here would
            # look like a broken clip instead of an expired connection.
            logger.warning("[brick.cut_clip] TikTok not connected: %s", auth_err)
            return {
                "action_type": "cut_clip",
                "status": "needs_tiktok",
                "reason": str(auth_err),
                "clip_id": str(clip.id),
            }

        logger.info(
            "[brick.cut_clip] Posted clip %s to TikTok (publish_id=%s, privacy=%s)",
            clip.id, result.get("publish_id"), result.get("privacy_level"),
        )
        return {
            "action_type": "cut_clip",
            "status": "posted",
            "clip_id": str(clip.id),
            "project_id": str(clip.project_id),
            "publish_id": result.get("publish_id"),
            "privacy_level": result.get("privacy_level"),
            "requested_privacy_level": result.get("requested_privacy_level"),
            "title": title,
        }

    # ── Wave 3 dispatch branches ──────────────────────────────────────────────
    #
    # Conventions, matching _dispatch_cut_clip:
    #   * read and write through the PASSED-IN session; never commit — execute_action
    #     owns the commit, and opening a second session here would have two
    #     connections writing rows the outer one may hold.
    #   * raise for programmer/ownership errors (missing payload key, cross-location
    #     row) — execute_action records a failure and re-raises.
    #   * RETURN a status dict for "can't do it, not an error." Statuses listed in
    #     NON_WORK_STATUSES earn no track record, so a missing precondition never
    #     buys ladder progress.
    #   * a nested read-only session for Foundation is the established exception.

    async def _load_own_post(
        self, action: BrickAction, session: AsyncSession, post_id: Any
    ) -> Post:
        """Load a Post and refuse it if it belongs to another location."""
        if not post_id:
            raise ValueError("payload needs post_id")
        post = await session.get(Post, uuid.UUID(str(post_id)))
        if post is None:
            raise ValueError(f"Post {post_id} not found")
        if str(post.location_id) != str(action.location_id):
            raise PermissionError(
                f"Post {post_id} belongs to another location — refusing"
            )
        return post

    @staticmethod
    def _parse_when(raw: Any) -> datetime:
        """Parse an ISO timestamp from a payload, tolerating a trailing Z."""
        if not raw:
            raise ValueError("payload needs scheduled_at (ISO-8601)")
        if isinstance(raw, datetime):
            return raw
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

    async def _dispatch_queue_draft(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Move a draft onto the calendar for review. Bricklayer tier.

        Queueing is not publishing — the post lands as `scheduled` and still needs a
        human (or a foreman-tier publish_post) to go out. That is the whole point of
        bricklayer sitting below foreman on the ladder.
        """
        post = await self._load_own_post(action, session, payload.get("post_id"))
        if post.status not in ("draft", "scheduled"):
            return {
                "action_type": "queue_draft",
                "status": "skipped",
                "reason": f"post is {post.status!r} — only draft or scheduled can be queued",
                "post_id": str(post.id),
            }

        when = self._parse_when(payload.get("scheduled_at"))
        post.scheduled_at = when
        post.status = "scheduled"
        await session.flush()

        logger.info("[brick.queue_draft] Post %s queued for %s", post.id, when.isoformat())
        return {
            "action_type": "queue_draft",
            "status": "scheduled",
            "post_id": str(post.id),
            "scheduled_at": when.isoformat(),
        }

    async def _dispatch_adjust_calendar(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Move an already-scheduled post to a different time. Foreman tier.

        Note the location check: PATCH /api/calendar/posts/{id} does not have one,
        so this branch is stricter than the route it mirrors.
        """
        post = await self._load_own_post(action, session, payload.get("post_id"))
        if post.status not in ("draft", "scheduled"):
            return {
                "action_type": "adjust_calendar",
                "status": "skipped",
                "reason": f"cannot reschedule a post that is {post.status!r}",
                "post_id": str(post.id),
            }

        was = post.scheduled_at
        when = self._parse_when(payload.get("scheduled_at"))
        post.scheduled_at = when
        await session.flush()

        logger.info(
            "[brick.adjust_calendar] Post %s moved %s -> %s",
            post.id, was.isoformat() if was else None, when.isoformat(),
        )
        return {
            "action_type": "adjust_calendar",
            "status": "updated",
            "post_id": str(post.id),
            "was": was.isoformat() if was else None,
            "scheduled_at": when.isoformat(),
        }

    async def _dispatch_publish_post(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Publish a post's platform variants now. Foreman tier — first tier that
        reaches an audience.

        Everything goes through SocialService (contract #5); this never speaks to
        GHL directly. Publishing here is immediate rather than enqueued through the
        Arq stagger queue, which is the deliberate tradeoff for a single
        human-approved punch-list item: one post, already reviewed, going out now.
        Bulk sends keep using /api/calendar/posts/{id}/publish and its stagger.
        """
        from services.ghl_adapter import GHLAdapter
        from services.social_service import (
            SocialAuthError,
            SocialProviderError,
            SocialPublishError,
            SocialRateLimitError,
        )

        post = await self._load_own_post(action, session, payload.get("post_id"))
        if post.status in ("published", "publishing"):
            return {
                "action_type": "publish_post",
                "status": "skipped",
                "reason": f"post is already {post.status!r}",
                "post_id": str(post.id),
            }

        rows = await session.execute(
            select(PostVariant).where(PostVariant.post_id == post.id)
        )
        variants = list(rows.scalars())
        if not variants:
            return {
                "action_type": "publish_post",
                "status": "skipped",
                "reason": "no platform variants — generate them before publishing",
                "post_id": str(post.id),
            }

        adapter = GHLAdapter()
        location_id = str(action.location_id)
        try:
            accounts = await adapter.list_accounts(location_id)
        except Exception as acct_err:
            logger.warning("[brick.publish_post] account lookup failed: %s", acct_err)
            return {
                "action_type": "publish_post",
                "status": "needs_account",
                "reason": f"could not list connected accounts: {acct_err}",
                "post_id": str(post.id),
            }

        usable = {
            a.get("platform"): a.get("id")
            for a in accounts
            if a.get("id") and not a.get("expired")
        }
        if not usable:
            return {
                "action_type": "publish_post",
                "status": "needs_account",
                "reason": "no connected, unexpired social account",
                "post_id": str(post.id),
            }

        published, skipped, failed = [], [], []
        for variant in variants:
            account_id = usable.get(variant.platform)
            if not account_id:
                skipped.append({"platform": variant.platform, "reason": "no_connected_account"})
                continue
            caption = variant.caption or post.base_caption or ""
            if not caption.strip():
                skipped.append({"platform": variant.platform, "reason": "empty_caption"})
                continue
            try:
                provider_id = await adapter.publish(
                    location_id=location_id,
                    platform=variant.platform,
                    caption=caption,
                    account_id=account_id,
                    media_urls=list(variant.media_urls or []) or None,
                    first_comment=variant.first_comment,
                )
                published.append({"platform": variant.platform, "provider_post_id": provider_id})
            except (SocialAuthError,) as auth_err:
                # A dead token is a human task, not a content failure.
                logger.warning("[brick.publish_post] %s auth: %s", variant.platform, auth_err)
                failed.append({"platform": variant.platform, "reason": f"auth: {auth_err}"})
            except (SocialPublishError, SocialRateLimitError, SocialProviderError) as pub_err:
                logger.warning("[brick.publish_post] %s failed: %s", variant.platform, pub_err)
                failed.append({"platform": variant.platform, "reason": str(pub_err)})

        if published and not failed:
            post.status = "published"
        elif published and failed:
            post.status = "partially_published"
        elif failed:
            post.status = "failed"
        await session.flush()

        if not published:
            return {
                "action_type": "publish_post",
                "status": "needs_account" if not failed else "skipped",
                "reason": "nothing published",
                "post_id": str(post.id),
                "skipped": skipped,
                "failed": failed,
            }

        logger.info(
            "[brick.publish_post] Post %s -> %s (%d published, %d failed)",
            post.id, post.status, len(published), len(failed),
        )
        return {
            "action_type": "publish_post",
            "status": "published",
            "post_id": str(post.id),
            "post_status": post.status,
            "published": published,
            "skipped": skipped,
            "failed": failed,
        }

    async def _dispatch_write_show_notes(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Generate and persist show notes for a project. Foreman tier.

        The Anthropic client is synchronous, so it runs in an executor — the two
        existing copies of this generation in main.py call it inline and block the
        event loop for the duration.
        """
        project_id = payload.get("project_id")
        if not project_id:
            raise ValueError("payload needs project_id")
        project = await session.get(Project, uuid.UUID(str(project_id)))
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        if str(project.location_id) != str(action.location_id):
            raise PermissionError(f"Project {project_id} belongs to another location")

        transcript = (project.transcript or "").strip()
        if not transcript:
            return {
                "action_type": "write_show_notes",
                "status": "skipped",
                "reason": "project has no transcript yet",
                "project_id": str(project.id),
            }
        if project.show_notes and not payload.get("overwrite"):
            return {
                "action_type": "write_show_notes",
                "status": "skipped",
                "reason": "show notes already exist — pass overwrite to replace them",
                "project_id": str(project.id),
            }

        notes = await self._generate_show_notes(
            str(action.location_id), project.title or "this episode", transcript
        )
        if not notes:
            return {
                "action_type": "write_show_notes",
                "status": "skipped",
                "reason": "generation returned nothing — Foundation may not be ready",
                "project_id": str(project.id),
            }

        project.show_notes = notes
        await session.flush()
        logger.info("[brick.write_show_notes] Project %s notes written (%d chars)",
                    project.id, len(notes))
        return {
            "action_type": "write_show_notes",
            "status": "written",
            "project_id": str(project.id),
            "characters": len(notes),
        }

    async def _generate_show_notes(
        self, location_id: str, title: str, transcript: str
    ) -> str:
        """Foundation-voiced show notes. Returns "" on any failure — caller decides."""
        try:
            from schemas.foundation import BrandContextTaskType as _TaskType
            from services.foundation import get_brand_context

            async with async_session() as ctx_session:
                ctx = await get_brand_context(
                    ctx_session, location_id, _TaskType.show_notes, topic=title
                )
            samples = "\n\n".join(
                f"- {s.text[:300]}" for s in (ctx.voice_samples or [])[:4]
            )
            prompt = (
                f"Write show notes for the episode \"{title}\".\n\n"
                "Sections, in this order: Episode Summary, What You'll Learn, "
                "Episode Highlights, Resources Mentioned, Subscribe & Review. "
                "Markdown headings. Ground every claim in the transcript — invent "
                "nothing, especially no statistics, names or URLs.\n\n"
                f"The host sounds like this:\n{samples}\n\n"
                f"TRANSCRIPT (excerpt):\n{transcript[:6000]}"
            )

            def _call() -> str:
                client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
                message = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=1500,
                    temperature=0.7,
                    system=_BRICK_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                return (message.content[0].text if message.content else "").strip()

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _call)
        except Exception as err:
            logger.warning("[brick.write_show_notes] generation failed: %s", err)
            return ""

    async def _dispatch_send_guest_email(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Deliberately does NOT send. GC tier.

        POST /api/brick/actions/{id}/approve-send is documented as the only path
        that emails a guest, and that is not an accident: the send sits in the route
        AHEAD of approve_action so the human review gate cannot be bypassed, and it
        is the only place that turns an expired token into a 409 needs_gmail the
        reconnect modal can act on. A branch here calling gmail_send.send_message
        would be a second send path with neither property.

        So this reports where the send actually lives. The stale comment on the
        guest_asset_package branch ("when Gmail send-as lands, the actual send
        happens HERE") describes a plan that Phase 6 superseded.
        """
        pending = await session.execute(
            select(BrickAction)
            .where(
                and_(
                    BrickAction.location_id == action.location_id,
                    BrickAction.action_type == "guest_asset_package",
                    BrickAction.status == "pending",
                )
            )
            .order_by(BrickAction.requested_at.desc())
            .limit(1)
        )
        package = pending.scalars().first()

        if package is None:
            return {
                "action_type": "send_guest_email",
                "status": "skipped",
                "reason": (
                    "no pending guest_asset_package to send. Build the package "
                    "first — the email is created with it."
                ),
            }

        recipient = (package.payload or {}).get("recipient")
        logger.info(
            "[brick.send_guest_email] Package %s is ready for review — send is a "
            "human action at /api/brick/actions/%s/approve-send",
            package.id, package.id,
        )
        return {
            "action_type": "send_guest_email",
            "status": "needs_approve_send",
            "reason": (
                "Guest email is drafted and waiting. Sending is a reviewed human "
                "action, not an autonomous one."
            ),
            "package_action_id": str(package.id),
            "recipient": recipient,
            "review_at": f"/api/brick/actions/{package.id}/approve-send",
        }

    async def _dispatch_pitch_sponsor(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Draft a sponsor pitch. GC tier. Generates only — it sends nothing.

        There is no sanctioned outbound path for sponsor mail (the existing
        /api/sponsors/{id}/outreach route also only generates), and inventing one
        inside an autonomous dispatch is exactly the wrong place for a first
        send-to-a-stranger capability. The draft lands on the record for review.
        """
        company = (payload.get("company") or payload.get("sponsor") or "").strip()
        if not company:
            return {
                "action_type": "pitch_sponsor",
                "status": "skipped",
                "reason": "payload needs company (or sponsor) to pitch",
            }

        pitch = await self._generate_sponsor_pitch(
            str(action.location_id), company, payload.get("angle") or ""
        )
        if not pitch:
            return {
                "action_type": "pitch_sponsor",
                "status": "skipped",
                "reason": "generation returned nothing — Foundation may not be ready",
                "company": company,
            }

        logger.info("[brick.pitch_sponsor] Drafted a pitch for %s (%d chars)",
                    company, len(pitch))
        return {
            "action_type": "pitch_sponsor",
            "status": "drafted",
            "company": company,
            "pitch": pitch,
            "sent": False,
            "note": "Draft only — PodClick has no sponsor send path. Copy and send it yourself.",
        }

    async def _generate_sponsor_pitch(
        self, location_id: str, company: str, angle: str
    ) -> str:
        """Foundation-voiced sponsor pitch. Returns "" on any failure."""
        try:
            from schemas.foundation import BrandContextTaskType as _TaskType
            from services.foundation import get_brand_context

            async with async_session() as ctx_session:
                ctx = await get_brand_context(
                    ctx_session,
                    location_id,
                    _TaskType.sponsor_pitch,
                    topic=f"{company} sponsorship pitch",
                )
            samples = "\n\n".join(
                f"- {s.text[:300]}" for s in (ctx.voice_samples or [])[:4]
            )
            prompt = (
                f"Draft a short sponsorship pitch to {company}."
                + (f" Angle: {angle}." if angle else "")
                + "\n\nUnder 200 words. Lead with what the audience is worth to them, "
                "not with praise. No invented download numbers, rates or testimonials "
                "— if you do not have a figure, describe the audience instead.\n\n"
                f"The host sounds like this:\n{samples}"
            )

            def _call() -> str:
                client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
                message = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=600,
                    temperature=0.7,
                    system=_BRICK_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                return (message.content[0].text if message.content else "").strip()

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _call)
        except Exception as err:
            logger.warning("[brick.pitch_sponsor] generation failed: %s", err)
            return ""

    async def _dispatch_adjust_vyral_mix(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Rewrite the Blueprint's bucket weights. GC tier.

        Nothing in the codebase wrote vyral_mix before this — and nothing validated
        it either, so an unknown bucket key here would mint Posts that violate the
        posts.bucket CHECK constraint at auto-plan time, a failure a long way from
        its cause. Validation is not optional for an autonomous writer.
        """
        from sqlalchemy.orm.attributes import flag_modified

        from services.vyral import validate_vyral_mix

        proposed = payload.get("vyral_mix")
        ok, problems = validate_vyral_mix(proposed)
        if not ok:
            logger.info("[brick.adjust_vyral_mix] Refused: %s", "; ".join(problems))
            return {
                "action_type": "adjust_vyral_mix",
                "status": "skipped",
                "reason": "proposed mix is not valid",
                "problems": problems,
                "proposed": proposed,
            }

        rows = await session.execute(
            select(Blueprint).where(Blueprint.location_id == action.location_id)
        )
        blueprint = rows.scalars().first()
        if blueprint is None:
            return {
                "action_type": "adjust_vyral_mix",
                "status": "skipped",
                "reason": "no Blueprint for this location — build one first",
            }

        was = dict(blueprint.vyral_mix or {})
        # JSONB needs a fresh object plus flag_modified; mutating in place is the
        # documented reason YouTube chapters silently failed to persist.
        blueprint.vyral_mix = {k: float(v) for k, v in proposed.items()}
        flag_modified(blueprint, "vyral_mix")
        await session.flush()

        logger.info("[brick.adjust_vyral_mix] %s -> %s", was, blueprint.vyral_mix)
        return {
            "action_type": "adjust_vyral_mix",
            "status": "updated",
            "was": was,
            "vyral_mix": dict(blueprint.vyral_mix),
        }

    async def _dispatch_replan_calendar(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Rebuild the forward calendar from the current Vyral mix. GC tier.

        /api/calendar/auto-plan only APPENDS — running it twice gives 60 posts on 30
        overlapping dates. A *replan* has to clear first, so this deletes the
        location's future `draft` auto-plan posts and lays down a fresh bucket
        rotation. It deliberately does not touch `scheduled`, `published` or
        manually-authored posts: replanning is not a licence to unpublish or to
        discard something a human wrote.

        Captions are left empty for `draft_post` (or the auto-plan route) to fill —
        this branch does the deterministic half, which is the half worth having an
        autonomous agent do.
        """
        from services.vyral import DEFAULT_VYRAL_MIX, distribute_buckets

        # `payload.get("slot_count") or 30` would turn an explicit 0 into 30 and
        # sail past the bounds check below — absent and zero are different asks.
        raw_slots = payload.get("slot_count")
        try:
            slot_count = 30 if raw_slots is None else int(raw_slots)
        except (TypeError, ValueError):
            return {
                "action_type": "replan_calendar",
                "status": "skipped",
                "reason": f"slot_count {raw_slots!r} is not a number",
            }
        if not 1 <= slot_count <= 60:
            return {
                "action_type": "replan_calendar",
                "status": "skipped",
                "reason": f"slot_count {slot_count} outside 1-60",
            }

        start_raw = payload.get("start_date")
        start = (
            datetime.fromisoformat(str(start_raw)).date()
            if start_raw else datetime.utcnow().date()
        )

        rows = await session.execute(
            select(Blueprint).where(Blueprint.location_id == action.location_id)
        )
        blueprint = rows.scalars().first()
        mix = dict((blueprint.vyral_mix if blueprint else None) or DEFAULT_VYRAL_MIX)
        pillars = [
            p.get("name") for p in ((blueprint.pillars if blueprint else None) or [])
            if isinstance(p, dict) and p.get("name")
        ]

        # Clear only what this branch is allowed to clear.
        stale = await session.execute(
            select(Post).where(
                and_(
                    Post.location_id == action.location_id,
                    Post.status == "draft",
                    Post.source == "auto_plan",
                    Post.scheduled_at.isnot(None),
                    Post.scheduled_at >= datetime.combine(start, time.min).replace(
                        tzinfo=timezone.utc
                    ),
                )
            )
        )
        cleared = list(stale.scalars())
        for post in cleared:
            await session.delete(post)

        sequence = distribute_buckets(mix, slot_count)
        created = []
        for index, bucket in enumerate(sequence):
            slot = datetime.combine(
                start + timedelta(days=index), time(hour=15)
            ).replace(tzinfo=timezone.utc)
            post = Post(
                location_id=action.location_id,
                bucket=bucket,
                base_caption=None,
                scheduled_at=slot,
                status="draft",
                source="auto_plan",
            )
            session.add(post)
            created.append({
                "bucket": bucket,
                "scheduled_at": slot.isoformat(),
                "pillar": pillars[index % len(pillars)] if pillars else None,
            })
        await session.flush()

        logger.info(
            "[brick.replan_calendar] Cleared %d future auto-plan drafts, created %d slots",
            len(cleared), len(created),
        )
        return {
            "action_type": "replan_calendar",
            "status": "replanned",
            "cleared": len(cleared),
            "created": len(created),
            "vyral_mix": mix,
            "start_date": start.isoformat(),
            "slots": created[:5],
            "note": "Captions are empty — draft_post or auto-plan fills them.",
        }

    async def _dispatch_generator(
        self, action: BrickAction, payload: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Run one of the app's own content generators and hand back what it produced.

        One branch serves all twenty rather than twenty near-identical ones. Each
        generator's logic lives inline in a main.py route handler that takes a
        Request, so it is not importable as a plain function — this calls the real
        route in-process via httpx ASGITransport, which reuses its validation, its
        Foundation gate, its model and its exact output shape. Copying twenty
        prompt bodies in here would have guaranteed drift.

        `main` is imported lazily: main imports this module at startup, so a
        top-level import would be circular, but by the time a dispatch runs main is
        fully loaded in sys.modules.

        Nothing is persisted. A generator produces text for the punch list to show
        and a human to use; writing it somewhere is a separate, higher-tier action.
        """
        import httpx

        spec = GENERATOR_ACTIONS.get(action.action_type)
        if spec is None:
            # Unreachable via _dispatch_action's routing, but a direct caller could.
            return {"action_type": action.action_type, "status": "no_op",
                    "reason": "not a registered generator"}

        import main as _main  # lazy — see docstring

        app = getattr(_main, "studio_app", None)
        if app is None:
            return {
                "action_type": action.action_type,
                "status": "skipped",
                "reason": "studio_app not available in this process",
            }

        # The body is the payload minus Brick's own bookkeeping keys. Each route
        # validates its own inputs, so a bad body comes back as that route's 4xx
        # rather than being second-guessed here.
        body = {k: v for k, v in payload.items() if k not in ("_tier", "_label", "action_type")}

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://brick.internal", timeout=180.0
            ) as client:
                response = await client.post(spec["path"], json=body)
        except Exception as call_err:
            logger.warning(
                "[brick.generator] %s (%s) failed: %s",
                action.action_type, spec["path"], call_err,
            )
            # Raise so execute_action records a failure — an unreachable generator
            # is a real failure, not a missing precondition.
            raise

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:2000]}

        if response.status_code >= 400:
            reason = (
                (data.get("error") if isinstance(data, dict) else None)
                or f"HTTP {response.status_code}"
            )
            not_ready = isinstance(data, dict) and data.get("foundation_not_ready")

            # 4xx and 5xx mean different things to the ladder, and Wave 1 made that
            # distinction load-bearing. A 4xx is a precondition or a bad input —
            # 422 foundation_not_ready is the common one, and Brick cannot write in
            # a voice the Foundation has not learned yet, so penalising him for it
            # would be wrong. A 5xx is the generator actually breaking (a truncated
            # model response, a provider outage); that is a failure and must lower
            # the success rate rather than vanish as a free skip.
            if response.status_code >= 500:
                logger.warning(
                    "[brick.generator] %s broke in %s (HTTP %s): %s",
                    action.action_type, spec["path"], response.status_code, reason,
                )
                raise RuntimeError(
                    f"{spec['path']} failed with HTTP {response.status_code}: {reason}"
                )

            logger.info(
                "[brick.generator] %s refused by %s: %s",
                action.action_type, spec["path"], reason,
            )
            return {
                "action_type": action.action_type,
                "status": "skipped",
                "reason": reason,
                "foundation_not_ready": bool(not_ready),
                "http_status": response.status_code,
                "generator": spec["path"],
            }

        logger.info(
            "[brick.generator] %s produced %s via %s",
            action.action_type, spec.get("label", "output"), spec["path"],
        )
        return {
            "action_type": action.action_type,
            "status": "generated",
            "label": spec.get("label", action.action_type),
            "generator": spec["path"],
            "tier_required": spec["tier"],
            "cost": spec.get("cost"),
            "result": data,
            "persisted": False,
            "note": "Generated for review. Nothing was saved or published.",
        }

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
