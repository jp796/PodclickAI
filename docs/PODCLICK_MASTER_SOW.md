# PodClick — Master Scope of Work & End-to-End Schematic

**Version:** 1.0 · Build handoff for Claude Code
**Owner:** JP Fluellen · Titan RE Team / Real Brokerage
**Target deployment:** Web (responsive), packaged for GHL Marketplace install

---

## 1. EXECUTIVE OVERVIEW

PodClick is the content operations system for personal-brand-driven professionals (beachhead: real estate agents). It collapses the **record → process → distribute → analyze → learn** content workflow into one continuous pipeline, with a personality-driven AI operator named **Brick** that grows in autonomy as the user trusts him.

The construction metaphor is the operating vocabulary. Brick is the GC. Brand Studio is the Blueprint. The voice fingerprint is the Foundation. The dashboard is the Job Site. Approvals queue is the Punch List. The autonomy ladder is Brick's Permit. Episodes are Projects that culminate in Closings.

The product is delivered as a GHL Marketplace App: **Private distribution → Agency install → White-Label** during early phase, with later transition to public listing under category **Content Management** (secondary: AI & Automation).

---

## 2. CORE PRINCIPLES (architectural non-negotiables)

Every architectural decision in this document derives from these:

1. **Brick is the brand.** One character, climbing the ladder forever. No separate "AI mode" or "autonomous mode" — Brick gains capabilities as his permit changes.
2. **Voice fingerprint is the moat.** Every AI generation must pull from the user's personal corpus. Generic LLM output is failure.
3. **Brand Studio is the source of truth.** No other module hardcodes brand voice. All generators call `getBrandContext()`.
4. **GHL is the social layer for now.** Wrap behind a SocialProvider abstraction so future providers (Postiz, direct) are swappable without touching calling code.
5. **The data tenant is the locationId.** Not the agency. Per-realtor isolation from day one, agencies get roll-up views above their locations.
6. **Trust is earned per-workflow.** The Permit ladder is the adoption strategy, not a feature.
7. **Construction language is enforced.** Every user-facing string passes through the vocabulary lens. No "settings," no "dashboard," no "AI mode." (Internal code can use whatever; user-facing copy must use the canonical terms in Section 14.)
8. **Background processing is the norm.** Anything that takes >2 seconds runs in a queue with status surfaced to Brick.

---

## 3. SYSTEM ARCHITECTURE OVERVIEW

### 3.1 The big-picture stack

```
┌─────────────────────────────────────────────────────────────┐
│  CLIENT (Web/Mobile)                                        │
│  - Walk-through Dashboard (Brick's morning report)          │
│  - Job Site (project views)                                 │
│  - Blueprint (Brand Studio)                                 │
│  - Crew screens (Scout, Draftsman, Framer, Painter, etc.)   │
│  - Studio (recording)                                       │
│  - Permit (autonomy settings)                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼  HTTPS / WebSocket
┌─────────────────────────────────────────────────────────────┐
│  API GATEWAY (REST + WebSocket)                             │
│  Authentication · Rate limiting · Request routing           │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ APP SERVICES │  │ AI SERVICES      │  │ INTEGRATION SVCS │
│              │  │                  │  │                  │
│ - Identity   │  │ - Brick (agent)  │  │ - GHL Adapter    │
│ - Blueprint  │  │ - Generation     │  │ - Gmail Adapter  │
│ - Foundation │  │ - Foundation     │  │ - YouTube Scout  │
│ - Studio     │  │   retrieval      │  │ - Anthropic API  │
│ - Projects   │  │ - Scout analysis │  │ - Storage (S3)   │
│ - Crew       │  │ - Planning loop  │  │                  │
│ - Permit     │  │                  │  │                  │
│ - Calendar   │  │                  │  │                  │
└──────────────┘  └──────────────────┘  └──────────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                 │
│  - Postgres + pgvector (relational + embeddings)            │
│  - Redis (queues, cache, real-time state)                   │
│  - S3-compatible object store (media, recordings, exports)  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  BACKGROUND WORKERS                                         │
│  - Publish queue (with stagger logic)                       │
│  - Media processing pipeline (transcode, clip, transcribe)  │
│  - Foundation ingestion (chunk, embed, store)               │
│  - Planning loop (daily + weekly crons)                     │
│  - Analytics pull (from GHL → PodClick)                     │
│  - Token refresh (GHL, Gmail)                               │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Recommended technology stack

| Layer | Recommendation | Why |
|---|---|---|
| Backend language | Node.js (TypeScript) or Python (FastAPI) | JP is already using Claude Code; TypeScript end-to-end keeps one language. If existing PodClick is Python, stay Python. |
| Frontend | Next.js + React + Tailwind | Server components for performance, file-based routing, white-label theming via CSS variables. |
| Database | Postgres 15+ with pgvector extension | Relational + vector storage in one. Avoids managing a separate vector DB until scale demands. |
| Cache + queues | Redis + BullMQ (Node) or Celery+Redis (Python) | Mature, observable, persistent across deploys. |
| Object storage | Cloudflare R2 or AWS S3 | R2 has no egress fees — important for media-heavy app. |
| Media processing | FFmpeg + Whisper (local or API) for transcription | Self-host FFmpeg for transcode/clip. Whisper API for transcripts initially; migrate to self-hosted later if cost justifies. |
| LLM | Anthropic Claude (Sonnet for most, Opus for planning loop) | Best instruction-following for character voice work. Use `claude-sonnet-4-6` or current equivalent. |
| Embeddings | OpenAI `text-embedding-3-small` (1536d) | Cheap, sufficient quality. Migrate to `-large` only if retrieval quality is poor. |
| Hosting | Vercel (frontend) + Railway/Fly.io/AWS (backend + workers) | Vercel for Next.js, Railway for everything else. Avoid Heroku. |
| Monitoring | Sentry (errors) + PostHog (product analytics) + Better Stack (uptime) | Three tools, full coverage. |
| Email | Postmark for transactional, user's Gmail for guest emails | Postmark for password resets/notifications. Gmail for personal-feeling outreach. |

---

## 4. DATA MODEL

The core schema. Names use snake_case for tables, camelCase for JSON fields in API responses.

### 4.1 Identity & tenancy

```sql
-- Top-level accounts (a GHL company or a standalone user)
CREATE TABLE accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ghl_company_id text UNIQUE,
  type text NOT NULL CHECK (type IN ('agency', 'individual')),
  white_label_config jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Each location = one realtor's workspace (the data tenant boundary)
CREATE TABLE locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ghl_location_id text UNIQUE,
  name text NOT NULL,
  status text DEFAULT 'active',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX idx_locations_account ON locations(account_id);

-- Users who can access locations
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext UNIQUE NOT NULL,
  full_name text,
  password_hash text,  -- null for OAuth-only users
  created_at timestamptz DEFAULT now()
);

-- Many-to-many: a user can access multiple locations, a location can have multiple users
CREATE TABLE user_location_access (
  user_id uuid REFERENCES users(id) ON DELETE CASCADE,
  location_id uuid REFERENCES locations(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (user_id, location_id)
);

-- OAuth tokens (per integration provider)
CREATE TABLE oauth_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (provider IN ('ghl', 'gmail', 'youtube')),
  access_token text NOT NULL,  -- encrypted at rest
  refresh_token text NOT NULL,  -- encrypted at rest
  expires_at timestamptz NOT NULL,
  scopes text[] NOT NULL,
  provider_account_id text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(location_id, provider)
);
```

### 4.2 Blueprint (Brand Studio)

```sql
CREATE TABLE blueprints (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid UNIQUE NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  
  -- Identity
  full_name text,
  market_city text,
  brokerage text,
  years_active text,
  price_range text,
  niche_primary text,
  niche_secondary text[],
  
  -- Voice (structured for prompt injection)
  voice_tone text[],
  voice_cadence text,
  vocabulary_yes text[],
  vocabulary_no text[],
  pov text DEFAULT 'first-person',
  humor_level text,
  
  -- Audience
  audience_primary text,
  audience_pain_points text[],
  audience_aspirations text[],
  
  -- Positioning
  one_liner text,
  differentiators text[],
  proof_points text[],
  
  -- Content pillars (with weights summing to 1.0)
  pillars jsonb DEFAULT '[]',
  -- Example: [{"name": "Market intelligence", "weight": 0.30, "examples": [...]}]
  
  -- Vyral mix percentages
  vyral_mix jsonb DEFAULT '{"viral": 0.4, "brand": 0.3, "personal": 0.2, "conversion": 0.1}',
  
  updated_at timestamptz DEFAULT now()
);
```

### 4.3 Foundation (voice fingerprint vector store)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE voice_samples (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  text text NOT NULL,
  embedding vector(1536) NOT NULL,
  source text NOT NULL CHECK (source IN (
    'podcast', 'social_approved', 'social_edited',
    'written_from_scratch', 'brand_studio', 'historical'
  )),
  topic text,
  platform text,
  bucket text CHECK (bucket IN ('viral', 'brand', 'personal', 'conversion', NULL)),
  weight float DEFAULT 1.0,
  edit_distance float,  -- for source='social_edited'
  episode_id uuid,
  excluded boolean DEFAULT false,
  promoted boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_voice_samples_location ON voice_samples(location_id);
CREATE INDEX idx_voice_samples_embedding ON voice_samples 
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_voice_samples_filter ON voice_samples(location_id, platform, bucket, excluded);

-- Track foundation quality over time
CREATE TABLE foundation_scores (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  score float NOT NULL,  -- 0.0 to 1.0
  sample_count int NOT NULL,
  computed_at timestamptz DEFAULT now()
);
CREATE INDEX idx_foundation_scores_location ON foundation_scores(location_id, computed_at DESC);
```

### 4.4 Studio (recording sessions)

```sql
CREATE TABLE recording_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  show_id uuid,
  guest_ids uuid[],
  mode text CHECK (mode IN ('solo_podcast', 'interview', 'live_webinar', 'evergreen_webinar')),
  status text DEFAULT 'pending' CHECK (status IN ('pending', 'recording', 'processing', 'completed', 'failed')),
  raw_video_url text,  -- S3 path
  raw_audio_url text,
  duration_seconds int,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- Per-participant tracks (for multi-track recording)
CREATE TABLE recording_tracks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES recording_sessions(id) ON DELETE CASCADE,
  participant_name text,
  participant_role text CHECK (participant_role IN ('host', 'guest', 'audience')),
  audio_url text,
  video_url text,
  upload_status text DEFAULT 'pending'
);
```

### 4.5 Projects (episodes + content initiatives)

```sql
CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  type text NOT NULL CHECK (type IN ('episode', 'campaign')),
  title text NOT NULL,
  description text,
  recording_session_id uuid REFERENCES recording_sessions(id),
  episode_number int,
  show_id uuid,
  
  -- Audio assembly (intro/main/commercial/outro)
  audio_assembly jsonb DEFAULT '{}',
  -- {"intro": "url", "main": "url", "commercials": [{"position_pct": 50, "url": "..."}], "outro": "url"}
  
  -- Status tracking
  status text DEFAULT 'draft' CHECK (status IN (
    'draft', 'recording_done', 'processing', 'review',
    'scheduled', 'closing', 'closed', 'failed'
  )),
  
  -- Closing (publish) info
  closing_scheduled_at timestamptz,
  closed_at timestamptz,
  
  -- Outputs
  final_audio_url text,
  final_video_url text,
  transcript text,
  show_notes text,
  
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX idx_projects_location ON projects(location_id, status, created_at DESC);

-- Clips generated from a project
CREATE TABLE clips (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  source_start_seconds float NOT NULL,
  source_end_seconds float NOT NULL,
  virality_score float,  -- predicted, 0-10
  hook_text text,
  rendered_urls jsonb DEFAULT '{}',
  -- {"vertical_1080x1920": "url", "square_1080x1080": "url", "horizontal_1920x1080": "url"}
  status text DEFAULT 'pending',
  created_at timestamptz DEFAULT now()
);
```

### 4.6 Crew CRMs (Guests, Sponsors)

```sql
CREATE TABLE guests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  full_name text NOT NULL,
  email text,
  company text,
  topic text,
  status text DEFAULT 'prospect' CHECK (status IN ('prospect', 'booked', 'recorded', 'aired')),
  project_id uuid REFERENCES projects(id),
  ghl_contact_id text,  -- mirror to GHL Contacts
  assets_sent_at timestamptz,
  notes text,
  social_links jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE sponsors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name text NOT NULL,
  niche text,
  status text DEFAULT 'prospect' CHECK (status IN ('prospect', 'active', 'paused', 'closed')),
  commission_terms text,
  affiliate_url text,
  utm_template text,
  episodes_count int DEFAULT 0,
  earnings_total numeric(10,2) DEFAULT 0,
  last_pitched_at timestamptz,
  notes text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE sponsor_placements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sponsor_id uuid NOT NULL REFERENCES sponsors(id) ON DELETE CASCADE,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position_pct float DEFAULT 50,
  ad_url text,
  earnings numeric(10,2) DEFAULT 0,
  created_at timestamptz DEFAULT now()
);
```

### 4.7 Calendar & posts

```sql
CREATE TABLE posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  project_id uuid REFERENCES projects(id),  -- null for standalone posts
  source_clip_id uuid REFERENCES clips(id),  -- null for non-clip posts
  bucket text CHECK (bucket IN ('viral', 'brand', 'personal', 'conversion', 'podcast')),
  scheduled_at timestamptz,
  status text DEFAULT 'draft' CHECK (status IN (
    'draft', 'scheduled', 'publishing', 'published', 'partially_published', 'failed'
  )),
  source text CHECK (source IN ('post_forge', 'auto_plan', 'manual', 'clip_distributor', 'brick_proposed')),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Per-platform variants for one post
CREATE TABLE post_variants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  platform text NOT NULL CHECK (platform IN (
    'linkedin', 'instagram', 'facebook', 'tiktok', 'youtube', 'x', 'gmb', 'threads'
  )),
  caption text,
  first_comment text,  -- for Instagram hashtag-in-comment pattern
  media_urls text[],
  platform_specific jsonb DEFAULT '{}',
  UNIQUE(post_id, platform)
);

CREATE TABLE post_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  variant_id uuid NOT NULL REFERENCES post_variants(id) ON DELETE CASCADE,
  platform text NOT NULL,
  provider text NOT NULL DEFAULT 'ghl',
  provider_post_id text,
  status text NOT NULL CHECK (status IN ('queued', 'sent_to_provider', 'published', 'failed')),
  attempt_count int DEFAULT 0,
  last_error text,
  attempted_at timestamptz DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX idx_post_attempts_post ON post_attempts(post_id);

CREATE TABLE post_analytics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  platform text NOT NULL,
  impressions int DEFAULT 0,
  reach int DEFAULT 0,
  engagement int DEFAULT 0,
  clicks int DEFAULT 0,
  shares int DEFAULT 0,
  saves int DEFAULT 0,
  fetched_at timestamptz DEFAULT now()
);
```

### 4.8 Brick & the Permit

```sql
CREATE TABLE brick_permits (
  location_id uuid PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  current_tier text NOT NULL DEFAULT 'draftsman' CHECK (current_tier IN (
    'owner_builder', 'draftsman', 'bricklayer', 'foreman', 'gc'
  )),
  -- Per-workflow overrides (e.g., GC for posting but Bricklayer for guest emails)
  workflow_overrides jsonb DEFAULT '{}',
  promoted_at timestamptz DEFAULT now(),
  promoted_by uuid REFERENCES users(id),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE brick_track_record (
  location_id uuid PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  started_at timestamptz DEFAULT now(),
  tasks_completed int DEFAULT 0,
  tasks_failed int DEFAULT 0,
  punch_list_approvals int DEFAULT 0,
  punch_list_rejections int DEFAULT 0,
  last_incident_at timestamptz,
  last_incident_reason text,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE brick_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  action_type text NOT NULL,
  -- 'draft_post', 'publish_post', 'cut_clip', 'send_email', 'pitch_sponsor', 'replan_calendar', etc.
  target_id uuid,  -- post_id, project_id, etc.
  target_type text,
  required_tier text NOT NULL,
  status text DEFAULT 'pending' CHECK (status IN (
    'pending', 'awaiting_approval', 'approved', 'rejected', 'executed', 'failed', 'undone'
  )),
  rationale text,  -- Brick's reasoning for the action
  approved_by uuid REFERENCES users(id),
  approved_at timestamptz,
  executed_at timestamptz,
  result jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX idx_brick_actions_location_status ON brick_actions(location_id, status, created_at DESC);

CREATE TABLE brick_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  user_id uuid REFERENCES users(id),
  role text NOT NULL CHECK (role IN ('user', 'brick')),
  content text NOT NULL,
  context_screen text,  -- which screen Brick was on when this was sent
  related_action_id uuid REFERENCES brick_actions(id),
  created_at timestamptz DEFAULT now()
);
```

### 4.9 Scout (Market intelligence)

```sql
CREATE TABLE scout_videos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_id uuid NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  youtube_video_id text NOT NULL,
  channel_id text,
  channel_name text,
  channel_subs int,
  title text,
  thumbnail_url text,
  views int,
  duration_seconds int,
  published_at timestamptz,
  virality_score float,  -- views / channel_subs
  niche text,
  topic_extracted text,
  transcript text,
  structure_extracted jsonb,  -- {hook, setup, payoff, cta}
  discovered_at timestamptz DEFAULT now(),
  UNIQUE(location_id, youtube_video_id)
);
CREATE INDEX idx_scout_videos_score ON scout_videos(location_id, virality_score DESC);

CREATE TABLE scout_remixes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scout_video_id uuid NOT NULL REFERENCES scout_videos(id) ON DELETE CASCADE,
  project_id uuid REFERENCES projects(id),
  remix_script text,
  status text DEFAULT 'draft',
  created_at timestamptz DEFAULT now()
);
```

### 4.10 Webhooks & events (audit trail)

```sql
CREATE TABLE webhook_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  signature_valid boolean,
  processed boolean DEFAULT false,
  processed_at timestamptz,
  error text,
  received_at timestamptz DEFAULT now()
);
CREATE INDEX idx_webhook_events_unprocessed ON webhook_events(received_at) WHERE NOT processed;

CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  location_id uuid REFERENCES locations(id),
  user_id uuid REFERENCES users(id),
  actor_type text NOT NULL CHECK (actor_type IN ('user', 'brick', 'system', 'webhook')),
  action text NOT NULL,
  target_type text,
  target_id uuid,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX idx_audit_log_location_time ON audit_log(location_id, created_at DESC);
```

---

## 5. API SURFACE

REST API at `/api/v1/*`. All endpoints require auth except OAuth callbacks and public webhooks. All endpoints accept and return JSON. All endpoints scope to the active `locationId` (passed in JWT or as `X-Location-Id` header).

### 5.1 Auth & OAuth

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | Email/password login |
| POST | `/auth/logout` | Clear session |
| POST | `/auth/refresh` | Refresh JWT |
| GET | `/auth/me` | Current user + accessible locations |
| GET | `/oauth/ghl/start` | Begin GHL OAuth (redirect to GHL) |
| GET | `/oauth/ghl/callback` | GHL OAuth callback handler |
| POST | `/oauth/ghl/disconnect` | Disconnect GHL |
| GET | `/oauth/gmail/start` | Begin Gmail OAuth |
| GET | `/oauth/gmail/callback` | Gmail OAuth callback |
| POST | `/oauth/gmail/disconnect` | Disconnect Gmail |

### 5.2 Blueprint

| Method | Path | Purpose |
|---|---|---|
| GET | `/blueprint` | Get current blueprint |
| PATCH | `/blueprint` | Update fields |
| POST | `/blueprint/intake/audit` | Run profile audit (LinkedIn/YT/IG URL) |
| POST | `/blueprint/intake/upload` | Build from uploaded content |
| POST | `/blueprint/intake/interview` | Build from voice interview |
| POST | `/blueprint/pillars/regenerate` | Regenerate content pillars |
| GET | `/blueprint/bio-pack` | Get current bio pack |
| GET | `/blueprint/conversion-pack` | Get current conversion pack |

### 5.3 Foundation

| Method | Path | Purpose |
|---|---|---|
| GET | `/foundation/score` | Current voice fingerprint score |
| GET | `/foundation/samples` | List voice samples (paginated, filterable) |
| POST | `/foundation/samples` | Manually add a sample |
| PATCH | `/foundation/samples/:id` | Promote/exclude/edit a sample |
| DELETE | `/foundation/samples/:id` | Delete sample |
| POST | `/foundation/retrain` | Trigger fresh foundation score calculation |
| POST | `/foundation/ingest` | Ingest content (URL/text/file) into foundation |

### 5.4 Studio (recording)

| Method | Path | Purpose |
|---|---|---|
| POST | `/studio/sessions` | Create recording session |
| GET | `/studio/sessions/:id` | Get session details |
| POST | `/studio/sessions/:id/start` | Mark session as recording |
| POST | `/studio/sessions/:id/end` | End session, trigger processing |
| POST | `/studio/sessions/:id/tracks` | Upload a participant track |
| GET | `/studio/sessions/:id/status` | Processing status |

### 5.5 Projects

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects` | List projects (filterable by status) |
| POST | `/projects` | Create project (auto-created from sessions usually) |
| GET | `/projects/:id` | Project detail |
| PATCH | `/projects/:id` | Update fields |
| POST | `/projects/:id/audio-assembly` | Configure intro/main/commercials/outro |
| POST | `/projects/:id/process` | Trigger media processing pipeline |
| POST | `/projects/:id/ship-it` | The hero button — process and prepare for closing |
| POST | `/projects/:id/schedule-closing` | Schedule the closing (publish) |
| GET | `/projects/:id/clips` | List generated clips |
| POST | `/projects/:id/clips/:clipId/regenerate` | Regenerate a clip |

### 5.6 Calendar & posts

| Method | Path | Purpose |
|---|---|---|
| GET | `/calendar?from=X&to=Y` | Calendar view (default 30 days) |
| POST | `/calendar/auto-plan` | Generate auto-plan for date range |
| GET | `/posts` | List posts (filterable) |
| POST | `/posts` | Create post (manual) |
| GET | `/posts/:id` | Post detail with variants |
| PATCH | `/posts/:id` | Update post |
| POST | `/posts/:id/variants` | Add/update platform variant |
| POST | `/posts/:id/publish` | Publish now |
| POST | `/posts/:id/schedule` | Schedule for future |
| POST | `/posts/:id/cancel` | Cancel scheduled |
| POST | `/posts/:id/undo` | Undo if within undo window |
| GET | `/posts/:id/analytics` | Engagement data |

### 5.7 Crew (Scout, Draftsman, Painter, etc.)

| Method | Path | Purpose |
|---|---|---|
| POST | `/crew/scout/analyze` | Run Market Scout for a market+audience |
| GET | `/crew/scout/videos` | List discovered videos |
| POST | `/crew/scout/videos/:id/remix` | Remix a video into user's voice |
| POST | `/crew/draftsman/generate` | Generate post draft (Post Forge backend) |
| POST | `/crew/framer/script` | Generate script outline (Script Lab) |
| POST | `/crew/painter/cover` | Generate cover/thumbnail |
| POST | `/crew/painter/text-overlay` | Suggest thumbnail text |

### 5.8 Guests & sponsors

| Method | Path | Purpose |
|---|---|---|
| GET | `/guests` | List guests |
| POST | `/guests` | Add guest |
| PATCH | `/guests/:id` | Update guest |
| POST | `/guests/:id/send-assets` | Trigger asset email send |
| POST | `/guests/:id/sync-to-ghl` | Push to GHL Contacts |
| GET | `/sponsors` | List sponsors |
| POST | `/sponsors` | Add sponsor |
| PATCH | `/sponsors/:id` | Update sponsor |
| POST | `/sponsors/:id/pitch` | Generate and (per permit) send pitch |
| GET | `/sponsors/:id/earnings` | Earnings breakdown |

### 5.9 Brick & Permit

| Method | Path | Purpose |
|---|---|---|
| GET | `/brick/walk-through` | Today's walk-through data |
| GET | `/brick/punch-list` | Items awaiting approval |
| POST | `/brick/actions/:id/approve` | Approve a punch list item |
| POST | `/brick/actions/:id/reject` | Reject with reason |
| POST | `/brick/chat` | Send message to Brick |
| GET | `/brick/messages?since=X` | Conversation history |
| GET | `/brick/permit` | Current permit + workflow overrides |
| POST | `/brick/permit` | Update permit tier |
| GET | `/brick/track-record` | Stats for permit screen |
| GET | `/brick/eligibility` | What tiers Brick is eligible for |

### 5.10 Webhooks (inbound)

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/ghl` | GHL events (verify signature) |
| POST | `/webhooks/gmail` | Gmail push notifications (V2) |

---

## 6. SERVICE BREAKDOWN

Each service has one responsibility. Services communicate via internal HTTP or message queue events, never share a database connection.

### 6.1 Identity Service
- User auth (JWT)
- OAuth token storage + refresh background job
- Location/account/access management
- Audit log writes

### 6.2 Blueprint Service
- CRUD on Blueprint
- Intake processing (audit URL, upload content, voice interview)
- Pillar generation
- Bio Pack & Conversion Pack rendering

### 6.3 Foundation Service
- Voice sample ingestion (chunk + embed + store)
- Retrieval API: `getBrandContext(locationId, taskType, topic)`
- Foundation score calculation (weekly cron)
- Sample curation (promote/exclude)

**Critical contract:** every AI generation in the system calls Foundation Service's `getBrandContext()`. No direct LLM calls bypass this.

### 6.4 Studio Service
- Recording session lifecycle
- Multi-track upload + sync
- Triggers media processing pipeline

### 6.5 Project Service
- Episode/project lifecycle
- Audio assembly orchestration (intro + main + commercials + outro)
- The "Ship It" workflow coordinator

### 6.6 Media Pipeline (worker)
- Transcode raw recording → final audio + video
- Transcribe (Whisper API → fallback to local Whisper)
- Detect natural break points for ad placement
- Generate clip candidates (engagement-density-based selection)
- Render clips in 3 aspect ratios (9:16, 1:1, 16:9)
- Auto-caption clips (force-aligned to transcript)
- Generate cover art via Painter

### 6.7 Calendar Service
- Post CRUD
- Auto-plan generator (uses Blueprint pillars + Vyral mix + recent performance)
- Variant generation (calls Draftsman crew)
- Schedule management

### 6.8 Social Service (the abstraction layer)
- `socialService.publish(post)` → routes to active provider adapter
- `socialService.schedule(post, when)`
- `socialService.getStatus(postId)`
- `socialService.fetchAnalytics(postId)`
- `socialService.listAccounts(locationId)`

### 6.9 GHL Adapter
- Implements SocialService interface against GHL API
- OAuth flow (start, callback, refresh)
- Per-platform post creation with stagger
- Webhook event consumer
- Contacts sync for Guest CRM

### 6.10 Gmail Adapter
- OAuth flow
- MIME message builder
- Send-as on behalf of user
- (V2) Inbox watch + classification

### 6.11 Scout Service
- YouTube API integration
- Channel/video analysis
- Virality scoring (views/subs)
- Structure extraction (hook/setup/payoff/CTA)
- Remix generation

### 6.12 Brick Agent Service
- Conversational interface (chat messages)
- Action proposal generation
- Punch list management
- Daily/weekly planning loop
- Eligibility computation for permit tiers
- Action execution (per permit) or queue for approval

### 6.13 Generation Service
- Wrapper around Anthropic API
- Always pulls Foundation context first
- Caches embeddings, rate-limits, retries
- Logs all generations for audit + future training data

### 6.14 Notification Service
- Email (Postmark) for transactional
- Web push for real-time updates
- WebSocket for live walk-through updates

---

## 7. THE PUBLISH PIPELINE (end-to-end, the most critical flow)

This is the flow you'll exercise constantly. Trace through it.

### 7.1 Triggers (where publishes start)

1. User clicks "Publish now" on a post → POST `/posts/:id/publish`
2. Scheduler cron picks up a post where `scheduled_at <= now()` and `status='scheduled'`
3. Calendar Auto-plan creates posts with future `scheduled_at` (these enter the cron path)
4. Project "Closing" event fires → creates posts for episode launch + clip distribution
5. Brick proposes a post and user approves it via Punch List

### 7.2 The flow

```
[Trigger]
    ↓
POST /posts/:id/publish (or cron picks up scheduled post)
    ↓
Calendar Service marks post status='publishing'
    ↓
Calendar Service emits 'publish.requested' event
    ↓
Publish Queue Worker picks up event
    ↓
For each platform variant in post.variants:
    ↓
  Compute stagger offset (see Section 8)
    ↓
  Enqueue platform job with delay = stagger offset
    ↓
[After delay]
    ↓
  Worker calls socialService.publishVariant(variant)
    ↓
  socialService routes to GHLAdapter.publishVariant()
    ↓
  GHLAdapter:
    1. Get valid access token (refresh if needed)
    2. Build GHL payload from variant
    3. POST /social-media-posting/{locationId}/posts to GHL
    4. Receive GHL post ID
    5. Insert into post_attempts table
    ↓
  Emit 'publish.attempted' event
    ↓
[5 minutes later]
    ↓
Verify Worker picks up post_attempt
    ↓
GET /social-media-posting/{locationId}/posts/{ghl_post_id}
    ↓
If status='published':
  Update post_attempts.status='published', published_at=now
  Update post.status (compute from all attempts: 'published' if all done, 'partially_published' if some failed)
  Emit 'publish.completed' event
  Brick may surface success in walk-through
    ↓
If status='failed':
  Update post_attempts.status='failed', last_error=reason
  Emit 'publish.failed' event
  Brick adds to punch list with retry option
    ↓
[Webhook path runs in parallel]
GHL fires webhook on actual platform publish
  → /webhooks/ghl receives event
  → Verify signature
  → Look up post_attempt by ghl_post_id
  → Update status real-time
  → Push WebSocket event to user
```

### 7.3 The undo path

```
User clicks "Undo" on a recently-published post
    ↓
POST /posts/:id/undo
    ↓
Check: published less than X minutes ago? (X = 60 for GC tier, 5 for Foreman)
    ↓
For each successfully-published platform attempt:
  Call GHLAdapter.deletePost(ghl_post_id)
    ↓
Update post.status='undone'
    ↓
Audit log entry
    ↓
Brick acknowledges in next walk-through
```

---

## 8. THE STAGGER LOGIC

(Already drafted as a separate skill file — see `social-publish-stagger/SKILL.md`. Summary here for completeness.)

Three layers of stagger:

**Layer 1: Per-platform offsets within one post**
```
linkedin   +0s
x          +60s
facebook   +120s
instagram  +180s
tiktok     +240s
youtube    +300s
gmb        +360s
```

**Layer 2: Global concurrency cap**
Queue configured with `concurrency: 8`. No more than 8 publishes in flight at once across all users.

**Layer 3: Jitter on popular schedule times**
When `scheduleAt` falls on :00, :15, :30, :45 of an hour, add deterministic jitter (`(hash(userId) % 180) - 90` seconds) so the same user always gets the same offset but global load smooths.

**Retry policy by error type:**

| HTTP status | Action | Max retries |
|---|---|---|
| 401 | Refresh token, retry once | 1 |
| 429 | Exponential backoff from 30s | 4 |
| 5xx | Exponential backoff from 30s | 4 |
| 400 | No retry, surface to Brick | 0 |
| network | Exponential backoff from 10s | 4 |

---

## 9. THE BRICK AGENT (planning loop + execution)

Brick is implemented as a structured agent with three main loops.

### 9.1 The conversational loop (synchronous, user-driven)

```
User sends message via /brick/chat
    ↓
Brick Service:
  1. Load user's last 20 messages for context
  2. Load current Blueprint, recent actions, today's walk-through data
  3. Call getBrandContext(locationId, 'brick_chat', topic=extracted_topic)
  4. Build system prompt with:
     - Brick's character (operator-coach, construction-flavored)
     - User's voice samples (so Brick references their language)
     - Current state (what's scheduled, recent performance, punch list)
     - Brick's current permit tier
  5. Send to Anthropic Claude with tool definitions
  6. If Claude calls a tool (e.g., "propose_action"), execute it
  7. Stream response back to user
    ↓
Save both messages to brick_messages
```

### 9.2 The daily planning loop (async, cron-driven, runs ~4am user-local)

```
Cron fires
    ↓
For each location with brick_permit.current_tier >= 'foreman':
    ↓
  Load context:
    - Blueprint
    - Calendar for next 7 days
    - Recent post performance (last 30 days)
    - Active projects
    - Pending punch list items
    - Recent foundation samples (last 7 days)
    - Brick's recent actions
    ↓
  Call Claude with planning prompt:
    "You are Brick, JP's content GC. Plan today's work."
    Tools available: propose_post, propose_clip, propose_email,
                     propose_pitch, propose_replan, schedule_action
    ↓
  Claude returns structured plan
    ↓
  For each proposed action:
    - If within current permit tier → execute immediately
    - If outside permit → create punch list item
    ↓
  Build today's walk-through data (last night's work + today's plan)
    ↓
  Send walk-through summary as brick_message + push notification
```

### 9.3 The weekly review loop (async, Sunday evening)

```
Sunday 5pm user-local cron fires
    ↓
For each location:
    ↓
  Load 7 days of analytics + actions
    ↓
  Call Claude with review prompt:
    "Review last week. What worked? What didn't? Adjust the Vyral mix?"
    ↓
  Claude returns:
    - Performance summary in Brick's voice
    - Proposed adjustments (e.g., shift Vyral mix +5% personal)
    - Plan for next week
    ↓
  If tier >= GC: auto-apply mix adjustments
  Else: create punch list "Approve next week's plan"
    ↓
  Email digest to user
```

### 9.4 Permit-aware action execution

Every action Brick proposes has a `required_tier`. Action gates:

| Action | Min required tier |
|---|---|
| Suggest post idea | draftsman |
| Draft post | draftsman |
| Queue draft for review | bricklayer |
| Publish a post | foreman |
| Cut and distribute clips | foreman |
| Write show notes | foreman |
| Adjust calendar schedule | foreman |
| Send guest asset email | gc |
| Send sponsor pitch | gc |
| Adjust Vyral mix automatically | gc |
| Re-plan calendar when posts underperform | gc |

Workflow overrides in `brick_permits.workflow_overrides` can demote specific actions below the current tier (e.g., overall tier=GC but guest_emails requires explicit approval).

---

## 10. THE FOUNDATION (voice fingerprint) FLOW

### 10.1 Ingestion (writes to the vector store)

Five entry points create voice samples:

1. **Podcast transcripts** — when project enters `closed` state, transcript is chunked (300 tokens, 50-token overlap), each chunk → sample with `source='podcast'`, weight=1.2

2. **Approved social posts** — when post.status flips to `published` and user didn't edit between draft and approve, save with `source='social_approved'`, weight=1.0

3. **Heavily edited drafts** — diff between AI draft and final approved version. If edit_distance > 0.30 (Levenshtein-based), save the *final* version with `source='social_edited'`, `edit_distance` recorded, weight=1.3

4. **Written from scratch** — user bypasses AI generator. Save with `source='written_from_scratch'`, weight=1.5

5. **Bulk historical upload** — Blueprint intake path, save with `source='historical'`, weight starts at 0.8 and decays with age

### 10.2 Embedding generation

```
On sample creation:
    ↓
Foundation Service receives sample
    ↓
Clean text (URLs → placeholder, normalize whitespace, preserve casing)
    ↓
If >500 tokens: chunk with 50-token overlap
    ↓
For each chunk:
  Call OpenAI embeddings API (text-embedding-3-small)
  Store {chunk_text, embedding, metadata, location_id} in voice_samples
```

### 10.3 Retrieval at generation time

```
Caller (e.g., Calendar Service) requests post generation
    ↓
Calls Generation Service with task spec
    ↓
Generation Service calls Foundation Service:
  getBrandContext({
    locationId,
    taskType: 'linkedin_post',
    topic: 'FHA loan limits'
  })
    ↓
Foundation Service:
  1. Embed the query: "linkedin post about FHA loan limits"
  2. Query voice_samples:
     SELECT text, source, weight 
     FROM voice_samples
     WHERE location_id = $1
       AND excluded = false
       AND (platform IS NULL OR platform = 'linkedin')
     ORDER BY (embedding <=> $2) / weight ASC
     LIMIT 5
  3. Load Blueprint structured fields
  4. Return:
     {
       brandProfile: {...},
       voiceSamples: [...],
       vocabularyYes: [...],
       vocabularyNo: [...]
     }
    ↓
Generation Service builds prompt with samples as few-shot examples
    ↓
Sends to Claude
    ↓
Returns generated content
    ↓
Logs to audit_log + generation_log for future training data
```

### 10.4 Score calculation (weekly)

```
Cron picks each location
    ↓
Sample 20 recent AI-generated outputs that were approved/published
Sample 20 user-written samples (source IN ('written_from_scratch', 'social_edited'))
    ↓
For each AI output:
  Find nearest neighbor in user-written set by cosine similarity
    ↓
Average all 20 nearest-neighbor similarities
    ↓
Scale to 0-100, that's the foundation score
    ↓
Insert into foundation_scores table
    ↓
Update Brick's awareness of current foundation match
```

---

## 11. GHL INTEGRATION (the social hands)

### 11.1 OAuth setup

PodClick is registered as a GHL Marketplace App with:
- **Distribution:** Private (initially) → Public after polish
- **Install type:** Agency
- **Listing type:** White-Label
- **Required scopes:** `socialplanner/post.write`, `socialplanner/post.readonly`, `socialplanner/account.readonly`, `contacts.write`, `contacts.readonly`, `oauth.write`, `oauth.readonly`

### 11.2 OAuth flow

```
User clicks "Connect GHL"
    ↓
GET /oauth/ghl/start
    ↓
Server constructs URL:
  https://marketplace.gohighlevel.com/oauth/chooselocation
    ?response_type=code
    &redirect_uri={PODCLICK_DOMAIN}/oauth/ghl/callback
    &client_id={GHL_CLIENT_ID}
    &scope=socialplanner/post.write+contacts.write+...
    ↓
Redirect user to this URL
    ↓
User authorizes on GHL side
    ↓
GHL redirects to /oauth/ghl/callback?code=X&locationId=Y
    ↓
Server exchanges code:
  POST https://services.leadconnectorhq.com/oauth/token
  Body: grant_type=authorization_code, code, client_id, client_secret,
        user_type=Location, redirect_uri
    ↓
Receive {access_token, refresh_token, expires_in, locationId, ...}
    ↓
Encrypt tokens, store in oauth_tokens table
    ↓
Fetch connected social accounts:
  GET /social-media-posting/{locationId}/accounts
    ↓
Cache account IDs per platform in account metadata
    ↓
Show success in Connections screen
```

### 11.3 Token refresh background job

```
Hourly cron:
    ↓
SELECT * FROM oauth_tokens 
WHERE provider='ghl' AND expires_at < now() + interval '2 hours'
    ↓
For each:
  POST /oauth/token with grant_type=refresh_token
    ↓
  If success: update tokens, expires_at
  If fail: mark connection broken, notify user via Brick punch list
```

### 11.4 Publish call (per platform variant)

```
POST https://services.leadconnectorhq.com/social-media-posting/{locationId}/posts
Authorization: Bearer {access_token}
Version: 2021-07-28
Content-Type: application/json

{
  "type": "post",
  "accountIds": ["{platform_account_id_from_oauth}"],
  "summary": "{variant.caption}",
  "scheduleDate": "{iso_timestamp_with_stagger_offset}",
  "status": "scheduled",
  "{platform}Details": {
    "post_type": "...",
    "first_comment": "..."   // Instagram only
  },
  "media": [
    { "url": "{cdn_url}", "type": "video|image" }
  ]
}
```

### 11.5 Status verification

```
5 minutes after schedule time:
    ↓
For each post_attempt with status='sent_to_provider':
  GET /social-media-posting/{locationId}/posts/{ghl_post_id}
    ↓
  Parse response.status:
    'published' → update post_attempts.status='published'
    'failed' → update with last_error, surface to Brick
    'scheduled' → re-queue for verification in 5 min (max 3 retries)
```

### 11.6 Webhook handling

```
POST /webhooks/ghl (inbound from GHL)
    ↓
Verify signature header against payload + app secret
    ↓
If invalid: log + 403
    ↓
Parse event type:
  - SocialPosting.PublishSuccess → update post_attempt
  - SocialPosting.PublishFailed → update + Brick punch list
  - ExternalAuth.Connected → update OAuth tokens
  - Contact.Create / Contact.Update → no-op (we push, not pull contacts)
    ↓
Insert into webhook_events
    ↓
Push WebSocket event to active user sessions
```

### 11.7 Contacts sync (Guest → GHL)

```
When guest.status changes:
    ↓
POST /contacts/upsert
{
  locationId,
  email: guest.email,
  firstName, lastName,
  source: 'PodClick',
  tags: ['podcast-guest', `ep-${episode_number}`, guest.status],
  customFields: [
    { key: 'podclick_status', field_value: guest.status },
    { key: 'episode_url', field_value: project.url },
    ...
  ]
}
    ↓
Store returned contact_id in guests.ghl_contact_id
```

---

## 12. GMAIL INTEGRATION (V1: send-as for guest assets)

### 12.1 OAuth setup

Scope requested: `https://www.googleapis.com/auth/gmail.send` only (lower trust required, faster Google verification).

### 12.2 Send-as flow

```
User clicks "Send asset email" on a guest (or Brick triggers at GC tier)
    ↓
POST /guests/:id/send-assets
    ↓
Gmail Adapter:
  1. Load OAuth tokens for gmail provider (refresh if needed)
  2. Generate email content:
     - Call Generation Service with task='guest_asset_email'
     - Foundation context pulled, voice samples injected
     - Template includes episode links, social flyer, transcript link
  3. Build MIME message:
     From: {user's connected Gmail address}
     To: {guest.email}
     Subject: "{episode_title} is live — thanks {guest.first_name}"
     Content-Type: multipart/alternative
     [HTML body + plain text fallback]
  4. Base64-encode
  5. POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send
     { "raw": "{base64_message}" }
    ↓
Update guests.assets_sent_at
    ↓
Log to audit_log
```

### 12.3 (V2) Inbox watch for CRM auto-update

Deferred until V1 ships and there's user volume to justify Google's verification process.

---

## 13. SCOUT (YouTube Market Intelligence)

### 13.1 Discovery flow

```
User triggers Scout (or daily cron for GC-tier users):
    ↓
POST /crew/scout/analyze
{
  marketCity: "Springfield, MO",
  audience: "Relocation buyers",
  competitorChannels: ["@channel1", ...] // optional
}
    ↓
Scout Service:
  1. If no competitors provided, use YouTube Data API to discover:
     Search for channels mentioning market + niche keywords
     Filter to channels with 1k-100k subs (sweet spot for "winning small")
  2. For each channel:
     List recent videos (last 90 days)
  3. For each video:
     Fetch: views, channel subs, duration, thumbnail, title
     Compute virality score = views / channel_subs
  4. Filter to score >= 1.5
  5. Upsert into scout_videos
    ↓
Return list ordered by score desc
```

### 13.2 Remix flow

```
User clicks "Remix in my voice" on a scout video
    ↓
POST /crew/scout/videos/:id/remix
    ↓
Scout Service:
  1. If transcript not yet fetched, get via YouTube captions API
  2. Call Claude with structure-extraction prompt:
     "Extract: hook, setup, payoff, CTA"
  3. Store structure in scout_videos.structure_extracted
  4. Call Generation Service:
     task = 'episode_script' or 'social_post_long'
     context = structure_extracted
     getBrandContext pulls user's voice samples
  5. Return remix script in user's voice
    ↓
User reviews; can save as project draft, generate post, etc.
```

---

## 14. THE CONSTRUCTION VOCABULARY (canonical user-facing terms)

Every user-facing string in PodClick must use these terms. Internal code can use any names.

| Internal concept | User-facing term |
|---|---|
| AI agent/coach | **Brick** |
| Brand profile / Brand Studio | **The Blueprint** |
| Voice fingerprint + vector store | **The Foundation** |
| Main dashboard | **Job Site** |
| Daily report screen | **Walk-through** |
| Approval queue | **Punch list** |
| Autonomy level / settings | **Brick's Permit** |
| Permit tiers | **Owner-Builder, Draftsman, Bricklayer, Foreman, GC** |
| Content initiative / episode | **Project** |
| Publishing an episode | **Closing** |
| Competitor video analysis | **Comps** |
| Specialist agents | **The Crew** |
| Crew members | **Scout, Draftsman, Framer, Painter, Dispatcher, Inspector** |
| Voice fingerprint score | **Foundation match** |
| GHL Marketplace category | **Content Management** |

Brick's voice rules: blue-collar professional, no corporate-speak, never apologizes excessively, says "y'all" naturally if it fits user's region, gives direct opinions, references the user's actual data, never calls itself "AI" or "the assistant" — Brick is just Brick.

Banned phrases in user-facing copy:
- "AI-powered"
- "Leverage"
- "Synergy"
- "Unlock"
- "Settings" (use "Permit" or specific page name)
- "Dashboard" (use "Walk-through" or "Job Site")
- "Workflow" (use "Project" or "Build")

---

## 15. BUILD PHASES (execution order for Claude Code)

### Phase 1 — Foundation (weeks 1-3)

Build the substrate. Nothing user-facing ships yet.

1. Set up monorepo, TypeScript, Postgres+pgvector, Redis, S3
2. Implement Identity Service (users, locations, OAuth token storage)
3. Implement GHL Marketplace App registration + OAuth flow end-to-end
4. Implement Blueprint Service with intake forms
5. Implement Foundation Service (vector store, ingestion, retrieval API)
6. Implement Generation Service wrapper around Anthropic API with mandatory `getBrandContext` injection
7. Build base UI shell (Next.js + Tailwind + white-label theming)

**Exit criteria:** A test user can sign up, connect their GHL, fill out a Blueprint, ingest a sample piece of content, and run `getBrandContext()` to retrieve voice samples + brand profile in a single API call.

### Phase 2 — Studio + Projects (weeks 3-5)

The recording-to-processing pipeline.

1. Implement Studio Service with multi-track recording
2. Implement Media Pipeline (transcode, transcribe, clip detection)
3. Implement Project Service with audio assembly
4. Build Studio UI (device check, recording, end-session)
5. Build Project detail UI (audio assembly, transcript view, clip preview)
6. Implement "Ship It" workflow that triggers full processing

**Exit criteria:** A user can record a session in PodClick, hit Ship It, and 10 minutes later see a fully-assembled episode with audio + transcript + clips + show notes ready for closing.

### Phase 3 — Social Service + GHL Publishing (weeks 5-7)

The hands.

1. Implement SocialService abstraction with adapter pattern
2. Implement GHLAdapter (publish, schedule, status, analytics)
3. Implement Publish Queue with stagger logic (per the skill file)
4. Implement Webhook handler for GHL
5. Build Calendar UI with month/week/list views
6. Build Multi-platform Publish Modal
7. Implement Auto-Plan generator using Vyral mix + Blueprint pillars

**Exit criteria:** A user can auto-plan a 30-day calendar, then watch a post publish across 5 platforms via GHL with verified status fed back to the calendar.

### Phase 4 — Brick (weeks 7-10)

The character.

1. Implement Brick Agent Service (chat, action proposal, planning loop)
2. Implement Permit system with track record + eligibility
3. Build Walk-through dashboard
4. Build Punch List UI with approve/reject
5. Build Permit promotion screen
6. Build Brick conversational panel (slides in from right on every screen)
7. Implement daily planning loop cron
8. Implement weekly review loop cron

**Exit criteria:** User wakes up to a walk-through email showing what Brick built overnight, with a punch list of items needing approval. User taps approve, items execute. User can promote/demote Brick from the permit screen.

### Phase 5 — Crew expansion (weeks 10-12)

The specialists.

1. Scout Service with YouTube integration + virality scoring
2. Remix flow with Foundation injection
3. Draftsman (Post Forge) UI refactor to call Generation Service
4. Framer (Script Lab) UI
5. Painter (Cover Forge) with thumbnail text suggestions
6. Guest CRM with GHL Contacts sync
7. Sponsor library with affiliate tracking

**Exit criteria:** All Crew screens use Brand Studio voice consistently. Scout surfaces 3+ hot comps daily. Guest status changes sync to GHL Contacts automatically.

### Phase 6 — Gmail + polish (weeks 12-14)

1. Gmail OAuth + send-as flow
2. Guest asset email generation through Foundation
3. White-label config UI for agencies
4. Analytics dashboard polish
5. Onboarding flow ("Let's lay your foundation...")
6. Public-facing marketing site

**Exit criteria:** Agency users can install PodClick across multiple sub-accounts under their own branding. New users complete onboarding and see a populated Foundation within 10 minutes.

### Phase 7 — Marketplace listing (weeks 14-16)

1. GHL Marketplace listing copy + assets
2. Demo video
3. Privacy policy + ToS
4. Submit for public listing
5. Pricing tier configuration

---

## 16. SECURITY & PRIVACY CHECKLIST

- All OAuth tokens encrypted at rest using KMS-managed key
- All PII (emails, names) stored only in primary DB, never logged
- All API endpoints require auth + locationId scope check
- All AI generations logged to audit_log with PII redacted
- GHL webhook signatures verified on every call
- Rate limiting per location (100 req / 10s) + per IP for unauth endpoints
- HTTPS only, HSTS enabled
- Foundation samples user-scoped, never shared across locations
- "Delete my brain" feature: hard delete of voice_samples + audit log entry
- GDPR-compliant data export endpoint
- Privacy policy specifically addressing: voice fingerprint usage, AI training (none on shared models), Gmail scopes, GHL data flow

---

## 17. OPEN DECISIONS TO MAKE BEFORE BUILD

Items JP needs to confirm or decide:

1. **Hosting region** — US-East primary? Multi-region from day one?
2. **Pricing tiers** — base, pro, agency, white-label-agency? Per-location or per-seat? Anchor pricing on what value JP wants to deliver.
3. **Trial length** — 14 days? 30 days? Free tier?
4. **Initial seed agencies** — who are the 3-5 friendly agencies to install Private build with first?
5. **Recording engine path** — build browser-based multi-track from scratch (hard), or wrap Daily.co / LiveKit / 100ms (faster)? Recommended: wrap for V1, own it later.
6. **Mobile** — responsive web only for V1? Native app on the V2 roadmap?
7. **Domain** — confirm app.podclick.ai as the canonical production domain.

---

## 18. WHAT THIS DOCUMENT IS NOT

To keep this scoped: this document covers PodClick *as a content operations system through Phase 7*. The following are intentionally out of scope for the initial build, on the roadmap but separate documents:

- Live webinar mode (Reos integration pattern, V2)
- Evergreen webinars (V2)
- Inbox monitoring for Guest CRM auto-update (V2, requires Google verification)
- Native mobile apps (V2)
- Direct social integrations replacing GHL (V3, only if economics demand)
- Multi-language support (V3)
- Marketplace for sharing Blueprints/templates between users (V3)

---

## 19. FINAL HANDOFF NOTES TO CLAUDE CODE

When starting the build:

1. Read this entire document before writing any code
2. Read the `social-publish-stagger/SKILL.md` skill file
3. Confirm the technology stack with JP before committing
4. Build in the phase order specified — do not jump ahead even if a later phase feature seems easier
5. Every user-facing string passes through Section 14's vocabulary lens
6. Every AI generation MUST go through `getBrandContext()` — there are no exceptions
7. Every social publish MUST go through SocialService (never direct to GHL from anywhere outside the adapter)
8. Every action Brick proposes MUST check the user's permit tier before executing
9. Write tests for the publish pipeline before writing tests for anything else — it's the highest-risk path
10. Log everything to audit_log; debugging in production requires this

Brick's voice in any system-generated content, error message, or notification should pass the test: **would a no-nonsense GC who's been running sites for 20 years say this?** If no, rewrite.

---

---

## 20. DEFERRED OPTIMIZATIONS

Items in this section are real inefficiencies identified during build. They are intentionally deferred to avoid scope creep during active sprint work. Each item documents what the problem is, what the correct fix looks like, and when to revisit.

### DO-1 — Whisper Double-Call (Ship It Cost Bleed)

**Filed:** 2026-05-31 | **Blocks:** Nothing currently

Step 2C (`_run_transcription()`) calls Whisper for plain text transcript. Step 2.5 (`_ship_it_whisper_words()`) calls Whisper again for verbose_json word timestamps. Both calls are necessary for their immediate purposes, but they charge separately — approximately $0.36 per 30-minute recording vs. $0.18 if consolidated.

**Correct fix:** Single Whisper call with `response_format="verbose_json"` and `timestamp_granularities=["word","segment"]` returns both `.text` and `.words`. Requires updating `_run_transcription()` to use verbose_json and persist word timestamps on the Project model alongside the transcript.

**Revisit when:** Transcript storage schema is finalized and the Ship It pipeline has passed verification.

---

### DO-2 — .ship_audio.mp3 File Accumulation

**Filed:** 2026-05-31 | **Blocks:** Nothing currently

`_ship_it_extract_audio()` writes `<recording>.ship_audio.mp3` adjacent to the source WebM in `data/recordings/`. Files are never cleaned up. At scale (~30–50MB per recording), `data/recordings/` grows without bound.

**Correct fix:** Cleanup routine that deletes `.ship_audio.mp3` once the assembled episode exists and the project is in `review` or later status. Alternative: write extracted audio to a temp directory scoped to the Ship It run.

**Revisit when:** First production deploy checklist. This is local dev only until then.

---

**End of document.**

**Next handoff artifacts to create:**
- `social-publish-stagger/SKILL.md` (already drafted)
- `brick-voice/SKILL.md` (Brick's character + tone rules)
- `foundation-retrieval/SKILL.md` (the getBrandContext contract)
- `vocabulary/SKILL.md` (the construction term enforcement)

Drop each of these in your Claude Code project as skill files. Together with this master SOW, they form the complete handoff.
