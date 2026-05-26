# PodClick — Phased Build Plan

**Companion to:** `PODCLICK_MASTER_SOW.md`
**Audience:** Claude Code (and JP)
**Phasing principle:** every phase ships something demo-able. No phase is "build infrastructure for next phase." Each ends with a working slice JP can use.

**Existing state (as of handoff):**
- GHL integration is wired (OAuth + posting + token refresh working)
- Studio (recording) is built
- Several existing screens: Audio Assembly, Guest CRM, Sponsor library, Release Queue, Social Studio (Post Forge, Content Calendar week-view, Hashtag Lab, Repurpose Hub, Connections, Screen Recorder), Brand Studio (Audit/Upload/Speak intake → Brand Brief, Bio Pack, Content Plan, Conversion Pack), Market Scout, Pillar Planner, Script Lab, Cover Forge, Channel Advisor, Repurpose Engine, Lead Page

**What's missing (the harmony layer):**
- A central `getBrandContext()` contract that every generator calls
- The Foundation vector store + voice fingerprint
- Brick as a persistent character across the app
- The Permit (autonomy ladder) system
- The Walk-through dashboard
- The 30-day calendar with Vyral rotation auto-plan
- The Punch List approval flow
- The SocialService abstraction wrapping GHL
- The stagger + retry layer on publishing
- The planning loop (daily + weekly Brick crons)
- Construction vocabulary applied across all user-facing strings

This phase plan addresses the gaps in the order that maximizes early value.

---

## PHASE 0 — Pre-flight (1-2 days)

**Goal:** Get the SOW into Claude Code's context, lock in decisions JP needs to make, prep the project.

**Tasks:**
1. JP drops `PODCLICK_MASTER_SOW.md`, this file, and the four SKILL.md files into the Claude Code project root or `.claude/skills/`
2. Confirm tech stack matches reality (Node/TS or Python? React/Next.js? hosting?)
3. JP answers Section 17 open decisions: hosting region, pricing tier shape, trial length, recording engine path, domain, beta agencies
4. Take inventory of existing PodClick code: what services exist, what schemas exist, what's in production, what's in dev. Document it.
5. Create a `current_state.md` in the project documenting what already works and which features are in which files

**Exit criteria:** Claude Code has full context. JP and Claude Code agree on what's already built vs what this plan adds.

---

## PHASE 1 — Foundation (intake + voice fingerprint) — 1.5 weeks

**Why first:** Foundation is the moat. Every downstream generator improves the moment Foundation exists. Without it, Brick sounds generic and PodClick sounds like every other AI tool. With it, the longer a user stays, the more switching costs accumulate against any competitor.

**Important reality:** existing PodClick installs do NOT have user voice samples preserved from prior intake (Brand Briefs / Bio Packs were AI-generated outputs *about* the user, not real user voice). This means Phase 1 must ship a complete intake flow that pours Foundation from scratch — not a backfill operation.

This phase is now shaped as a SaaS-grade onboarding experience, not just infrastructure.

**Reference SKILL files for this phase:**
- `skills/foundation-intake/SKILL.md` — the intake philosophy, interview script, ingestion pipelines, readiness thresholds
- `skills/foundation-retrieval/SKILL.md` — the getBrandContext contract

**What ships:**

1. **`voice_samples`, `foundation_scores`, `blueprints` tables with pgvector** (schema from SOW section 4.3)
2. **Foundation Service core:**
   - `getBrandContext(locationId, taskType, topic)` library function
   - `ingestSample(locationId, text, source, metadata)` 
   - `getFoundationStatus(locationId)` returning tier (not_ready/thin/solid/deep)
   - `POST /foundation/ingest`, `GET /foundation/score`, `GET /foundation/samples` endpoints
3. **Text ingestion pipeline** — chunking, embedding (OpenAI text-embedding-3-small), storage
4. **Audio ingestion pipeline:**
   - S3 upload for audio/video files
   - Whisper transcription (API to start, batched)
   - Diarization for multi-speaker audio with "which voice is yours" prompt
   - Chunking + embedding of host-only segments
5. **Voice interview UI** — the killer intake feature:
   - 8-question guided interview (script in foundation-intake SKILL)
   - Browser audio recording per question (MediaRecorder API)
   - Per-question submit → audio pipeline → samples appear in real time
   - Brick comments lightly between questions
6. **Channel auto-pull (basic):**
   - YouTube channel URL → fetch recent video captions
   - Text paste for LinkedIn posts (LinkedIn doesn't allow scraping)
   - Defer RSS/blog scrape to Phase 2 if time-pressed
7. **Blueprint auto-generation from samples** — the analysis pass that turns 5+ samples into a Blueprint draft (tone, vocabulary, audience, pillars). User confirms or adjusts.
8. **Foundation panel in Blueprint UI:**
   - Sample browser with source, date, preview
   - Curation actions: promote (weight ×2), exclude (weight 0), delete
   - Foundation match score with tier badge (thin/solid/deep)
   - "Add more material" entry points
9. **Foundation-readiness gates on generators:**
   - When Foundation is `not_ready`, generators show the empty-state error: *"I can't write in your voice until I know your voice. Pour foundation first."*
   - When `thin`, generators work with warning
   - When `solid` or `deep`, full operation
10. **Score calculation cron** (weekly background job)
11. **Refactor Post Forge** to call `getBrandContext()` end-to-end. The proof point that the contract works.

**Exit criteria:** A brand new user signs up, lands on "Pour your foundation," completes the 8-question voice interview in 10 minutes, watches samples ingest in real time, sees their Blueprint auto-populate from their actual voice, confirms it, then generates a LinkedIn post via Post Forge that demonstrably sounds like them. Foundation score displays in the Blueprint UI. Try generating *before* completing intake → blocked with helpful empty-state.

**What this UNBLOCKS:** Phase 3 (Brick character) and Phase 4 (rest of crew) both depend on this. Without Foundation, Brick sounds generic. Without the intake flow, no new user can start using PodClick.

---

## PHASE 2 — SocialService abstraction + Calendar harmony — 1 week

**Why second:** GHL is already wired but probably called directly from multiple places. Wrapping it now prevents future pain and lets Phase 3 Brick autonomously publish without bypassing safety.

**What ships:**

1. **`SocialService` interface** with adapter pattern (SOW section 6.8)
2. **`GHLAdapter`** that wraps existing GHL calls — refactor everywhere that currently calls GHL directly to go through SocialService
3. **`post_attempts` audit table** to track every publish attempt
4. **Stagger queue** with BullMQ (per the `social-publish-stagger` SKILL file):
   - Per-platform offsets (0s, 60s, 120s, ...)
   - Global concurrency cap (8)
   - Deterministic jitter for popular schedule times
5. **Retry policy** per status code (401 refresh+retry, 429 backoff, 400 no retry, etc.)
6. **5-minute post-publish verification** that confirms posts actually went live
7. **30-day Calendar view** (month grid + week + list)
8. **`POST /calendar/auto-plan`** that generates a 30-day plan respecting the Vyral mix
9. **Multi-platform publish modal** with per-platform preview (per the mockup designed earlier)
10. **Construction vocabulary pass** on calendar/posting UI: "Closing" instead of "publish," "Project" for episodes, etc.

**Exit criteria:** JP clicks "Auto-plan 30 days" in the Calendar, sees a fully-populated month respecting his Vyral mix. Clicks one post → preview modal with per-platform variants. Clicks "Ship It" → posts publish through GHL with proper stagger, verification, and audit trail.

**What this UNBLOCKS:** Phase 3 (Brick can now publish through the abstraction without bypassing safety). Phase 5 (clip distributor uses same SocialService).

---

## PHASE 3 — Brick the Foreman (autonomy ladder + walk-through) — 1.5 weeks

**Why third:** Foundation makes Brick sound like JP. SocialService gives Brick safe hands. Now we install the character and the trust model.

**This is the most consequential phase of the entire build. Get this right and PodClick becomes the only AI content tool with a real operator-class character.**

**What ships:**

1. **`brick_permits`, `brick_track_record`, `brick_actions`, `brick_messages` tables**
2. **Brick Agent Service** with three loops:
   - Conversational chat (synchronous)
   - Daily planning cron (4am user-local)
   - Weekly review cron (Sunday 5pm user-local)
3. **Permit tier system** (Owner-Builder → Draftsman → Bricklayer → Foreman → GC)
4. **Permit-aware action execution:** every Brick action checks tier before executing vs queueing to Punch List
5. **Eligibility computation** for permit promotions based on track record
6. **Walk-through Dashboard** (per the mockup designed earlier):
   - Brick's greeting message
   - "Built overnight" list
   - Punch List with approve/reject actions
   - Today's Site Plan
   - Stats: Foundation %, Posts MTD, Affiliate $
   - Active Projects with progress bars
   - Comps Scout pulled
   - Permit badge in upper right
7. **Permit Promotion screen** (per the mockup designed earlier) with track record + tier descriptions
8. **Persistent Brick chat panel** that slides in from right on every screen, context-aware to the current page
9. **`brick-voice` SKILL** applied across all Brick-generated text (operator-class tone, no corporate-speak)
10. **WebSocket connection** for real-time Brick updates

**Exit criteria:** JP opens PodClick in the morning → sees Walk-through with Brick's report, last night's work, today's plan, and 2 items in Punch List. Approves them with one tap each. Chats with Brick from the side panel, asks "what should I record this week" — Brick answers using Foundation voice + current data. JP visits Permit screen, sees his track record, can demote Brick to Bricklayer or (after eligibility) promote to GC.

**What this UNBLOCKS:** Phase 4 (each Crew specialist becomes addressable by Brick). Phase 5 (Brick orchestrates the full pipeline).

---

## PHASE 4 — Crew refactor (every generator uses Foundation + speaks construction) — 1 week

**Why fourth:** Phases 1-3 established the contracts. Now apply them to every existing generator so the whole app speaks one voice.

**What ships:**

1. **Refactor every AI generator to call `getBrandContext()`:**
   - Post Forge (already done in Phase 1) ✓
   - Script Lab
   - Cover Forge (thumbnail text)
   - Show notes generation (in Project flow)
   - Hashtag Lab
   - Repurpose Engine
   - Channel Advisor
   - Guest outreach email templates
   - Sponsor pitch templates
2. **Crew naming applied to UI:**
   - Post Forge → labeled "Draftsman"
   - Script Lab → labeled "Framer"
   - Cover Forge → labeled "Painter"
   - Repurpose Engine → labeled "Dispatcher" or rolled into Project flow
   - Market Scout → labeled "Scout"
   - Channel Advisor → labeled "Inspector"
3. **Crew screens accessible from Walk-through and from Brick chat** ("Brick, ask Scout for new comps")
4. **Scout enhancements:**
   - Pull and display YouTube thumbnails (currently missing per JP's feedback)
   - Display virality score badge prominently (already exists, polish UI)
   - "Remix in my voice" button uses Foundation
   - Auto-discover competitor channels if none provided
5. **Vocabulary pass across remaining screens** using the `vocabulary` SKILL

**Exit criteria:** Every AI-generated piece of content anywhere in PodClick passes through Foundation. Output noticeably sounds like JP across all generators. Every screen uses construction vocabulary. Scout shows real thumbnails.

**What this UNBLOCKS:** Phase 5 — Brick can now orchestrate the full pipeline because every component is consistent.

---

## PHASE 5 — Project flow integration (Studio → Ship It → Closing) — 1 week

**Why fifth:** The recording-to-distribution pipeline is the marquee feature. Now we wire it end-to-end with everything Phases 1-4 built.

**What ships:**

1. **Project as the unifying object** — recording session creates a Project automatically
2. **"Ship It" workflow** — single button at end of recording that orchestrates:
   - Media processing (transcode, transcribe via Whisper)
   - Audio assembly (intro + main + sponsor ad + outro using Sponsor library rotation)
   - Show notes generation (via Foundation)
   - Clip detection and rendering (3 aspect ratios)
   - Cover art generation
   - Post generation for each clip (Foundation-powered)
   - Schedule "Closing" event in Calendar
3. **Sponsor ad rotation logic:** when audio assembly runs, auto-select a sponsor from the library using:
   - Round-robin (default)
   - Weighted by commission
   - Topic-matched (AI suggests)
4. **Sponsor placement tracking:** increment `sponsors.episodes_count`, log to `sponsor_placements`
5. **Project detail screen** showing the full state (recording → processing → review → scheduled → closing → closed) using construction vocabulary
6. **Closing event** triggers full distribution via SocialService
7. **Guest CRM auto-update:** when Project status flips, Guest status flips too (Booked → Recorded → Aired)
8. **GHL Contacts sync:** push guest to GHL Contacts with tags
9. **Guest asset email queued** to Punch List (Foreman tier) or auto-sent (GC tier)

**Exit criteria:** JP records a podcast in Studio → hits one button → 10 minutes later he sees a fully-processed Project with assembled audio, transcript, clips, show notes, cover art, scheduled posts across 5 platforms, guest auto-updated in CRM, guest asset email drafted and sitting in Punch List. Approves email, episode closes Tuesday at 8am, clips distribute on staggered schedule. Brick reports it all in next morning's Walk-through.

**What this UNBLOCKS:** This is the hero demo. Everything after is enhancement.

---

## PHASE 6 — Gmail send-as + Guest asset polish — 3-5 days

**Why sixth:** With Phase 5 working, the only thing keeping guest asset emails from feeling personal is that they're sent from a generic PodClick address. Gmail send-as fixes that.

**What ships:**

1. **Gmail OAuth flow** (scope: `gmail.send` only)
2. **Gmail Adapter** with MIME message builder
3. **Token refresh job** for Gmail tokens (same pattern as GHL)
4. **Guest asset email generation** via Foundation (Phase 4 already did this) sent through user's Gmail
5. **Connections screen update** to include Gmail with connect/disconnect
6. **Email deliverability monitoring** — track opens/clicks if possible

**Exit criteria:** Guest receives an asset email from `jp@titanreteam.com` that lands in their primary inbox. Reply goes back to JP's actual Gmail. Process feels personal end-to-end.

**What this UNBLOCKS:** GC tier is now fully unlockable (guest emails were the gating workflow). JP can promote Brick to GC and have the full autonomous experience.

---

## PHASE 7 — Onboarding flow + white-label config — 1 week

**Why seventh:** Now the product works end-to-end for JP. Next step is making it work for *anyone else who installs it*. Onboarding is the conversion lever.

**What ships:**

1. **First-run experience using construction metaphor:**
   - "Let's lay your foundation" introduction
   - Three intake paths: Audit (URL), Upload (content), Speak (voice interview)
   - Foundation ingests immediately, shows live progress
   - "Meet Brick" introduction
   - Brick starts as Draftsman, explains the ladder
   - First Walk-through within 24 hours of signup
2. **White-label configuration UI** for agencies:
   - Brand name override
   - Logo upload
   - Color theme variables
   - Custom OAuth consent page branding
   - Per-sub-account application of brand
3. **Agency admin panel:**
   - List of connected sub-accounts
   - Per-account status overview
   - Bulk actions (e.g., "send guest assets across all clients")
4. **Pricing tier enforcement** in feature gates

**Exit criteria:** A new user clicks the GHL install link, completes OAuth, lands on Welcome, completes Blueprint in <10 minutes, sees their Foundation pouring in real time, meets Brick, gets first Walk-through next morning. An agency owner can white-label and install across 5 sub-accounts in one session.

---

## PHASE 8 — Marketplace listing + public launch — 1 week

**Why eighth:** Everything above gets PodClick working privately. This phase gets it discoverable.

**What ships:**

1. **GHL Marketplace listing copy:**
   - Tagline: "Record once. Publish everywhere. Sound like you." (or whatever JP locks in)
   - Description with Content Management primary category, AI & Automation secondary
   - Feature bullets aligned to construction vocabulary
   - Screenshots: Walk-through, Calendar with Vyral mix, Brick chat, Permit screen, Scout with comps
2. **Demo video** showing the Ship It → Closing flow end-to-end (~90 seconds)
3. **Privacy policy and Terms of Service** specifically addressing:
   - Voice fingerprint usage (per-user only, never trained on shared models)
   - AI generation logs and retention
   - Gmail scope justification
   - GHL data flow
4. **Public-facing marketing site** at podclick.ai or jpfluellen.com landing page
5. **Support documentation** for common flows
6. **Submit to GHL for public listing review**

**Exit criteria:** PodClick is publicly discoverable in the GHL Marketplace, has a polished marketing site, and JP can confidently send any GHL agency owner a link knowing the install-to-active flow works.

---

## PHASE 9 (V2 — backlog, not blocking) — months 4+

These are the next horizons but explicitly NOT part of the V1 build:

- **Inbox monitoring for Guest CRM auto-update** (Gmail readonly scope, Google verification process)
- **Live webinar mode** (extends Studio for live audience + registration)
- **Evergreen webinars** (scheduled replay with simulated chat)
- **Native mobile apps**
- **Direct social integrations** (replacing GHL if economics justify)
- **Multi-language Foundation** (training on non-English voice samples)
- **Marketplace for sharing Blueprints/Vyral mixes between users**
- **Workflow triggers from GHL** (e.g., "when contact tagged 'aired,' trigger PodClick to schedule follow-up posts")
- **Reos integration** (real estate transaction paperwork — separate product but should connect to PodClick contacts)

---

## PHASE EXECUTION RULES FOR CLAUDE CODE

These apply to every phase:

1. **Start each phase by reading this document + the SOW + the four SKILL files.** Don't trust memory across sessions.
2. **Build tests before features in the publish pipeline.** Other paths can use post-hoc tests.
3. **Every user-facing string passes vocabulary check.** Run a grep for banned terms before completing a phase.
4. **Every AI generation passes through `getBrandContext()`.** Run a grep for direct LLM client imports in non-Foundation code.
5. **Every social publish passes through `SocialService`.** Grep for direct GHL adapter imports outside the Social Service.
6. **Commit at phase exit criteria, not before.** If exit criteria aren't met, the phase isn't done.
7. **At the end of each phase, write a one-page changelog** describing what shipped, what was deferred, and what JP needs to verify.
8. **Run the demo flow yourself before declaring exit.** Each phase's exit criteria includes a specific user flow — execute it in dev before handing off.

---

## DECISION POINTS BUILT INTO THE PHASING

After Phase 3 (Brick), pause and demo to JP. The trust ladder and Walk-through are the riskiest UX bets in the whole product. If they don't feel right, fix before continuing.

After Phase 5 (Ship It flow), pause and demo to 3-5 beta users. Their reaction to "record → one button → distributed everywhere" is the gut check on whether PodClick has the magic moment.

After Phase 7 (Onboarding), do a controlled beta with 10-20 agencies before pursuing public listing. Public listing is one-way — once you're discoverable, every bug is a public bug.

---

## TIMELINE ESTIMATE (calendar weeks, assuming focused Claude Code sessions)

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Pre-flight | 1-2 days | 0.3 weeks |
| 1 — Foundation + intake flow | 1.5 weeks | 1.8 weeks |
| 2 — SocialService + Calendar | 1 week | 2.8 weeks |
| 3 — Brick + Permit + Walk-through | 1.5 weeks | 4.3 weeks |
| 4 — Crew refactor | 1 week | 5.3 weeks |
| 5 — Project flow integration | 1 week | 6.3 weeks |
| 6 — Gmail send-as | 3-5 days | 7.1 weeks |
| 7 — Onboarding + white-label | 1 week | 8.1 weeks |
| 8 — Marketplace listing | 1 week | 9.1 weeks |
| **TOTAL** | | **~9-10 weeks** |

Assume +20-30% for testing, polish, and decision delays. Realistic timeline: **12-13 weeks to public marketplace listing.**

That's an aggressive but credible timeline given how much of the underlying app is already built. The phasing front-loads the highest-leverage architectural changes (Foundation, SocialService) so that everything downstream improves automatically.

---

**End of phase plan.**

Drop this in your Claude Code project alongside `PODCLICK_MASTER_SOW.md` and the four SKILL.md files. Start each phase with: *"Read PODCLICK_PHASES.md and PODCLICK_MASTER_SOW.md, then execute Phase N. Confirm understanding before writing code."*
