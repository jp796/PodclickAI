# PodClick — Current State Inventory

> Phase 0 output. Read-only inventory executed 2026-05-23.
> Cross-referenced against: PODCLICK_MASTER_SOW.md, PODCLICK_PHASES.md, ARCHITECTURE.md, API.md

---

## 1. Tech Stack (reality vs SOW target)

| Layer | Current Reality | SOW Target | Gap |
|-------|----------------|------------|-----|
| Backend | Python 3.9, FastAPI, single `main.py` (5,500+ lines) | Node/TS or Python FastAPI | No gap on language — but monolithic, no service separation |
| Frontend | Static HTML files, vanilla JS, no build step | Next.js + React + Tailwind | Major gap — no component system, no routing, no SSR |
| Database | **Flat JSON files** on disk (`data/*.json`) | Postgres 15+ with pgvector | **Critical gap** — no relational DB, no vector store, no multi-user isolation |
| Cache/Queues | None — asyncio tasks only | Redis + BullMQ/Celery | **Critical gap** — no persistent queue, no retry, no stagger |
| Object storage | Local filesystem (`data/jobs/`, `data/clips/`, `data/library/`) | S3/R2 | Gap — not cloud-native |
| Hosting | Railway (deployed 2026-05-23) | Any cloud | ✅ Solved |
| Media | ffmpeg (local), faster-whisper stripped for Railway | ffmpeg + Whisper API | Partial — Whisper stripped from Railway deploy |

---

## 2. Services / Modules Inventory

### Backend (main.py — ~5,500 lines, no service separation)

| Module area | Routes | What it does | SOW equivalent |
|-------------|--------|-------------|----------------|
| **Audio pipeline** | `/api/process`, `/api/jobs/{id}`, `/api/upload`, `/api/schedule`, `/api/retry` | Full recording-to-MP3 pipeline: noise reduction, loudness norm, assembly, Buzzsprout upload | Projects + Ship It flow (Phase 5) |
| **Release Queue** | `/api/queue`, `/api/queue/{id}`, `/api/queue/{id}/publish` | Scheduled release queue with publish-now override | Calendar Closing (Phase 2/5) |
| **Episodes** | `/api/episodes`, `/api/episodes/{id}` | Episode history log | Projects (Phase 5) |
| **Transcription** | `/api/transcribe`, `/api/transcriptions`, `/api/transcriptions/{id}` | Whisper transcription, export | Foundation ingestion (Phase 1) |
| **Library** | `/api/library`, `/api/library/{id}` | Music/sound file library | Studio assets |
| **Profiles** | `/api/profiles`, `/api/profiles/{id}/activate` | Multi-agent profiles (name, mic, logo) | Identity / locationId (SOW Section 4.1) |
| **Sponsors** | `/api/sponsors`, `/api/sponsors/{id}/log_episode` | Sponsor library, affiliate tracking, outreach | Projects sponsor rotation (Phase 5) |
| **Guests** | `/api/guests`, `/api/guests/{id}/asset_email` | Guest CRM: Prospect→Booked→Recorded→Aired | Guest CRM (Phase 5) |
| **Brand Studio** | `/api/brand/*` (audit, voice-brain, voice-capture, intake, bio-pack, content-plan, conversion) | 3 intake paths → Brand Brief, Bio Pack, Content Plan, Conversion Pack | Blueprint / Foundation (Phase 1) |
| **Social Studio** | `/api/social/forge`, `/api/social/calendar`, `/api/social/hashtags`, `/api/social/repurpose` | Post Forge, 7-day calendar, hashtag lab, repurpose hub | Draftsman + Calendar (Phase 2/4) |
| **GHL Social** | `/api/social/ghl/accounts`, `/api/social/ghl/publish` | Direct GHL Social Planner API | SocialService GHLAdapter (Phase 2) |
| **Social OAuth** | `/api/social/meta/*`, `/api/social/linkedin/*`, `/api/social/tiktok/*`, `/api/social/youtube/*` | Platform OAuth flows + direct publish | Should route through SocialService (Phase 2) |
| **YouTube Studio** | `/api/yt/*` (competitor-spy, script-formula, cover-forge, pillar-plan, etc.) | Market Scout, Script Lab, Cover Forge, Pillar Planner, Channel Advisor, Repurpose, Lead Page, Trend Radar | Scout + Crew (Phase 4) |
| **Studio** | `/api/studio/generate-script`, `/api/studio/today-topic`, `/api/studio/show-notes`, `/api/studio/social-posts` | Script gen, topic of day, show notes, social posts from recording | Studio + Ship It (Phase 5) |
| **GHL Automation** | `/api/automation/ingest` | GHL webhook receiver | GHL integration (Phase 2) |
| **Screen recorder** | `/api/screen-record/convert` | WebM → MP4 conversion | Social Studio (exists) |
| **Clips** | `/api/clip`, `/api/clip/{id}`, `/api/clip/{id}/video/{idx}` | Clip detection + rendering + preview | Ship It (Phase 5) |
| **Drive** | `/api/drive/*` | Google Drive folder creation | Integration |
| **AI Persona** | `/api/yt/ai-persona/*` | Photo upload for thumbnail generation | Cover Forge (Phase 4) |

### Pipeline modules (`pipeline/`)

| File | What it does |
|------|-------------|
| `assemble.py` | Audio assembly: intro + main + sponsor ad + outro |
| `audio.py` | Noise reduction, loudness normalization |
| `clip.py` | Clip detection, rendering |
| `content.py` | Content generation helpers |
| `subtitles.py` | SRT/ASS subtitle generation |
| `transcribe.py` | Whisper transcription wrapper |
| `video.py` | Video processing (normalize, cut) |
| `upload.py` | Buzzsprout upload |
| `youtube.py` | YouTube Data API helper |
| `telegram.py` | Telegram bot publish |
| `tiktok.py` | TikTok OAuth + token management |
| `scheduler.py` | Release queue background loop |
| `drive.py` | Google Drive API |
| `broll.py` | B-roll generation (Pexels) |

### Frontend (static HTML, vanilla JS)

| File | What it is | SOW equivalent |
|------|-----------|----------------|
| `studio.html` | Recording studio + teleprompter + device check modal | Studio (exists) |
| `youtube-studio.html` | Market Scout, Script Lab, Cover Forge, Pillar Planner, Repurpose, Channel Advisor, Lead Page, Trend Radar, Content Scheduler | Crew screens: Scout, Framer, Painter, Inspector (Phase 4) |
| `social-studio.html` | Post Forge, Calendar (7-day), Hashtag Lab, Repurpose Hub, Connections, Screen Recorder tab | Draftsman + Calendar (Phase 2/4) |
| `brand-studio.html` | Blueprint intake: Audit/Upload/Speak → Brand Brief, Bio Pack, Content Plan, Conversion Pack | Blueprint + Foundation intake (Phase 1) |
| `index.html` | Main dashboard: episode list, clip jobs, publish flow | Job Site / Walk-through (Phase 3) |
| `recorder-widget.js` | Floating Loom-style screen recorder widget (injected on all pages) | Social Studio feature |

---

## 3. Data Models (current flat JSON)

| File | Shape | SOW table equivalent |
|------|-------|---------------------|
| `data/episodes.json` | `[{job_id, title, status, published_at, ...}]` | `projects` table |
| `data/guests.json` | `[{id, name, status, email, episode_id, ...}]` | `guests` table |
| `data/sponsors.json` | `[{id, name, read_script, episodes_count, ...}]` | `sponsors` table |
| `data/profiles.json` | `[{id, name, is_active, mic_id, logo_url, ...}]` | `locations` / identity |
| `data/library.json` | `[{id, title, type, path, ...}]` | `media_library` |
| `data/queue.json` | `[{id, job_id, release_date, status, ...}]` | `post_attempts` / calendar |
| `data/scheduler.json` | `{shoot_days, topics, market}` | `calendar_plans` |
| `data/social_calendar.json` | `[{id, day, platform, title, content}]` | `calendar_entries` |
| `data/social_hashtags.json` | `{core, niche, local, trending}` | `foundation_metadata` |
| `data/ai_persona.json` | `{photos: [{id, shot_type, path}]}` | `media_assets` |
| `data/jobs/*.json` | Per-job status dicts | `projects` / `job_runs` |
| `data/transcriptions/*.json` | Whisper output segments | `voice_samples` (Phase 1) |

**Critical gap:** No `locationId` isolation. All data is single-tenant (JP only). Every JSON file is a shared global store. Multi-tenant SaaS requires Postgres + per-location rows.

---

## 4. External Integrations

| Integration | Status | Used for | SOW phase |
|------------|--------|----------|-----------|
| OpenAI GPT-4o | ✅ Active | All AI generation (40 direct calls in main.py) | Foundation retrieval (Phase 1) — needs getBrandContext wrapper |
| GHL Social Planner | ✅ Active | Social post publishing (Titan sub-account) | SocialService (Phase 2) |
| GHL OAuth (Meta/FB/IG) | ✅ Active | Facebook + Instagram OAuth tokens | SocialService (Phase 2) |
| LinkedIn OAuth | ✅ Active | LinkedIn posting | SocialService (Phase 2) |
| TikTok OAuth | ✅ Active (pipeline/tiktok.py) | TikTok OAuth + publish | SocialService (Phase 2) |
| YouTube Data API | ✅ Active | Market Scout competitor analysis | Scout (Phase 4) |
| YouTube Upload API | ✅ Active | Direct video upload | Studio / Phase 5 |
| Buzzsprout | ✅ Active | Podcast hosting upload | Audio pipeline |
| Telegram | ✅ Active | Bot notifications + publish | Notifications |
| Pexels | ✅ Active | B-roll stock footage | Broll pipeline |
| Google Drive | ✅ Active | Project folder creation | Asset storage |
| ffmpeg | ✅ Local only | Video/audio processing | Ship It pipeline |
| Whisper | ⚠️ Stripped from Railway deploy | Transcription | Foundation ingestion (Phase 1) |
| Gmail | ❌ Not built | Guest asset emails | Phase 6 |
| Redis | ❌ Not present | Queue, cache, stagger | Phase 2 |
| Postgres/pgvector | ❌ Not present | Relational DB + voice embeddings | Phase 1 |
| S3/R2 | ❌ Not present | Cloud media storage | Phase 1 |

---

## 5. Conflict Map: Existing Code vs SOW Architecture

### 🔴 Critical conflicts

| Conflict | Current | SOW requires | Phase to fix |
|---------|---------|-------------|--------------|
| **No getBrandContext()** | 40 direct `openai.AsyncOpenAI()` calls scattered through main.py, each with their own prompt | Single `getBrandContext(locationId, taskType, topic)` contract | Phase 1 |
| **No SocialService abstraction** | GHL called directly via httpx in main.py; Meta/LinkedIn/TikTok each have their own OAuth + publish endpoints | All social publishing routes through `SocialService` with `GHLAdapter` | Phase 2 |
| **No stagger/retry queue** | GHL publish is a direct synchronous httpx POST — no retry, no rate limit protection, no verification | BullMQ/Celery queue with per-platform offsets, retry policy, post-publish verify | Phase 2 |
| **Flat JSON = no multi-tenancy** | All data in `data/*.json` — one global store | Postgres with `location_id` on every row | Phase 1 (schema migration) |
| **No Foundation** | Brand Studio produces AI-generated summaries *about* the user (voice_fingerprint dict). Not a vector store of real user voice samples | pgvector store of real chunked/embedded user voice samples | Phase 1 |
| **No Brick** | No AI agent character, no planning loop, no Punch List, no Permit ladder | Brick Agent Service with 3 loops + Walk-through dashboard | Phase 3 |

### 🟡 Partial conflicts

| Conflict | Current | SOW requires | Phase |
|---------|---------|-------------|-------|
| **Brand Studio ≠ Foundation intake** | Audit/Upload/Speak → produces Brand Brief (an AI summary). Voice capture does record audio + extract fingerprint dict. | Real 8-question voice interview → audio → Whisper → embed → pgvector | Phase 1 refactor |
| **7-day calendar ≠ 30-day** | Social calendar is 7-day Mon-Sun view | 30-day calendar with Vyral mix auto-planning | Phase 2 |
| **Construction vocabulary not applied** | UI uses "Dashboard," "Settings," "Social Studio," "Post Forge" etc. | "Job Site," "Brick's Permit," "Draftsman," etc. | Phase 2+ vocabulary pass |
| **Profiles ≠ locationId** | `profiles.json` tracks JP's device profiles (mic, logo, name). No per-agent/per-location isolation | locationId is the data tenant. Every record scoped to a location. | Phase 1 |
| **index.html is not Walk-through** | index.html shows episode list + clip jobs. No morning report, no Brick, no Punch List | Walk-through is Brick's morning report: built overnight, Punch List, Site Plan | Phase 3 |

### ✅ Already aligned with SOW

| Item | Status |
|------|--------|
| GHL token refresh | Working |
| Audio assembly (intro/main/sponsor/outro) | Working — matches Phase 5 sponsor rotation spec |
| Guest CRM state machine (Prospect→Booked→Recorded→Aired) | Working |
| Sponsor library + affiliate tracking | Working |
| Release queue with scheduled/published | Working |
| Market Scout with virality scoring | Working |
| Script Lab, Cover Forge, Pillar Planner | Working |
| Screen Recorder (Loom-style widget) | Working |
| Railway deployment + GitHub CI | Working |
| GHL Social Planner integration (Titan sub-account) | Working (just built) |

---

## 6. Phase Mapping — Where Each Existing Piece Gets Touched

| Phase | What it touches in existing code |
|-------|----------------------------------|
| **Phase 1 — Foundation** | brand-studio.html (refactor intake → real voice interview), all 40 OpenAI calls in main.py (add getBrandContext wrapper), data/*.json → Postgres migration |
| **Phase 2 — SocialService** | social-studio.html (7→30 day calendar), all social OAuth + publish endpoints (wrap in SocialService), add Redis + stagger queue, vocabulary pass on social/calendar UI |
| **Phase 3 — Brick** | index.html (rebuild as Walk-through), new Brick Agent Service, Permit system, Punch List |
| **Phase 4 — Crew refactor** | youtube-studio.html crew screens (vocabulary + Foundation-powered), all remaining OpenAI generators |
| **Phase 5 — Project flow** | Audio pipeline (assemble.py, clip.py, video.py), studio.html (Ship It button), episodes.json → Projects table, Guest CRM auto-update |
| **Phase 6 — Gmail** | guests endpoint (asset_email), new Gmail adapter |
| **Phase 7 — Onboarding** | brand-studio.html → Foundation onboarding, new white-label config |
| **Phase 8 — Marketplace** | New marketing site, GHL listing |

---

## 7. Three Questions Before Phase 1

**Q1: Single-tenant first or multi-tenant from day one?**

The existing codebase is fully single-tenant (JP only). Phase 1 requires adding Postgres. The question is whether to build the `location_id` isolation layer immediately (correct for SaaS) or defer it and build Foundation for JP-only first, then bolt on multi-tenancy in Phase 7. 

Recommendation: Build `location_id` into the schema from day one — retrofitting it later requires rewriting every query. But Phase 1 can operate with a single hardcoded locationId (`wRJ7XtAZXxepCn014afk`) while the auth layer is pending.

**Q2: Keep existing Brand Studio outputs or treat Foundation as a clean start?**

The SOW notes that existing Brand Briefs/Bio Packs are AI-generated summaries, not real voice samples. Phase 1 must pour Foundation from scratch. But `brand-studio.html` has valuable intake paths (Audit, Upload, Speak) that can feed Foundation. 

Decision needed: Does the existing Brand Studio UI become the Foundation intake UI (refactored), or does Phase 1 build a new onboarding experience alongside it?

**Q3: What's the Railway plan limit and does it support Postgres?**

Phase 1 requires adding Postgres (with pgvector) and Redis. Railway offers both as add-on services. But the current account is on Trial (30 days or $5). 

Decision needed: Upgrade Railway plan before Phase 1 starts, or use Neon (Postgres) + Upstash (Redis) as external services to avoid Railway vendor lock-in?

---

---

## 8. Phase 0 Decisions (locked 2026-05-24)

**Q1 — Tenancy: location_id in schema from day one ✅**
- Every new table gets `location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE` with index
- Hardcode Titan locationId in `config.py` (one line, one place) while auth is pending
- Build `getCurrentLocationId()` helper now — returns hardcoded value. When auth lands, swap implementation only. Zero query rewrites downstream.

**Q2 — Brand Studio: refactor existing intake, don't build alongside ✅**
- Audit path → channel auto-pull tier (YouTube captions, blog scrape, LinkedIn paste)
- Upload path → audio/video + text ingestion into voice_samples (chunking/embedding pipeline)
- Speak path → 8-question Voice Interview (needs most new work)
- Existing Brand Brief / Bio Pack / Conversion Pack outputs survive as "Brand Assets" — clearly labeled generated assets, not Foundation training data
- Output schema changes from Brand Brief dict → `voice_samples` rows in pgvector

**Q3 — Database: Neon (Postgres + pgvector) + Upstash (Redis), Railway stays for app server only ✅**
- Neon: free tier, pgvector first-class, branching for migration testing
- Upstash: per-request Redis pricing, effectively free at Phase 1 traffic levels
- Railway: web server + worker processes only — stateful infra moves to specialists
- Migration path = connection string change, not a Railway dump-and-restore

---

*Phase 0 complete. Decisions locked. Ready for Phase 1.*

---

## 9. Gate 5 Exception — create_ghl_fields.py (documented 2026-05-27)

**File:** `/Users/jamesfluellen/podcast-studio/create_ghl_fields.py`
**Purpose:** One-time admin script to bulk-create custom fields in GHL for the Lead Scoring system.
**Invocation:** `python create_ghl_fields.py` — hand-run only.

**Gate 5 exception:** This script contains a direct `https://services.leadconnectorhq.com` URL. It is NOT invoked by any route handler, cron job, webhook, or any code in the request-handling path. Confirmed via `rg` — zero imports or references to this file from any other `.py` file.

**Rule:** All automated/runtime GHL calls must go through `services/ghl_adapter.py`. This script is exempt because it predates the adapter and is strictly a one-time setup utility. Do NOT restore the pattern for any new code in the request path.

---

## 10. Phase 2A — Known Not-Yet-Exercised (2026-05-27)

**Success path for `post_attempts.status → published` has not been exercised.**

- Gate 1 testing used invalid test `account_id` values → all attempts landed as `status=failed` or `status=queued` (worker not running during test).
- No row in `post_attempts` has ever reached `status=published` in testing.
- The success path (GHLAdapter returns a real `provider_post_id` → PostAttempt flips to `published` → `verify_attempt` job confirms) is implemented and code-correct but has not been executed against a live GHL account.

**Action at Phase 2B start:** Before writing any new code, fire one real publish through `/api/social/ghl/publish` with a connected account_id (from `/api/social/ghl/accounts`) and confirm `post_attempts` shows `status=published` with a real `provider_post_id`. This is the first thing on the 2B checklist.
