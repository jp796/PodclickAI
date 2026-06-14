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
    "guest_asset_package":  "draftsman",
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
