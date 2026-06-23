# PodClick API Reference
> Last updated: 2026-05-28 | Server: http://localhost:8765 | Update on new routes.

## Core Podcast Pipeline

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/process` | Ingest audio file → start transcription + processing job |
| POST | `/api/retry/{old_job_id}` | Retry failed job |
| GET | `/api/status` | Server health + job queue stats |
| GET | `/api/jobs/{job_id}` | Poll job status + result |
| POST | `/api/upload` | Upload audio file |
| POST | `/api/automation/ingest` | Webhook ingest for automated pipelines |

## Content Schedule & Queue

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/schedule` | Save/update content schedule |
| GET | `/api/queue` | List scheduled content queue |
| DELETE | `/api/queue/{entry_id}` | Remove queue entry |
| PATCH | `/api/queue/{entry_id}` | Update queue entry |
| POST | `/api/queue/{entry_id}/publish` | Publish queue entry |
| POST | `/api/mark_published` | Mark episode as published |

## Episodes & Library

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/episodes` | List all episodes |
| PATCH | `/api/episodes/{job_id}` | Update episode metadata |
| DELETE | `/api/episodes/{job_id}` | Remove episode |
| GET | `/api/library` | List library items |
| POST | `/api/library` | Add library item |
| DELETE | `/api/library/{item_id}` | Remove library item |
| GET | `/api/library/{item_id}/file` | Download library file |

## Timestamps & Transcriptions

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/timestamps/{job_id}` | Generate/save chapter timestamps |
| GET | `/api/transcriptions` | List all transcriptions |
| GET | `/api/transcriptions/{tx_id}` | Get transcription |
| DELETE | `/api/transcriptions/{tx_id}` | Delete transcription |
| PATCH | `/api/transcriptions/{tx_id}` | Update transcription |
| GET | `/api/transcriptions/{tx_id}/export` | Export transcription |
| POST | `/api/transcribe` | Start transcription job |

## Profiles, Sponsors, Guests

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/profiles` | List show profiles |
| POST | `/api/profiles` | Create profile |
| PUT | `/api/profiles/{profile_id}` | Update profile |
| DELETE | `/api/profiles/{profile_id}` | Delete profile |
| POST | `/api/profiles/{profile_id}/activate` | Set active profile |
| GET | `/api/sponsors` | List sponsors |
| POST | `/api/sponsors` | Add sponsor |
| PUT | `/api/sponsors/{sponsor_id}` | Update sponsor |
| DELETE | `/api/sponsors/{sponsor_id}` | Delete sponsor |
| POST | `/api/sponsors/{sponsor_id}/log_episode` | Log sponsor episode use |
| GET | `/api/sponsors/{sponsor_id}/outreach` | Sponsor outreach data |
| POST | `/api/guests/extract` | Extract guest info from URL |
| GET | `/api/guests` | List guests |
| POST | `/api/guests` | Add guest |
| PUT | `/api/guests/{guest_id}` | Update guest |
| DELETE | `/api/guests/{guest_id}` | Delete guest |
| GET | `/api/guests/{guest_id}/asset_email` | Get guest asset email |

## Clips

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/clip` | Create clip from episode |
| GET | `/api/clip/{job_id}` | Poll clip job status |
| POST | `/api/clip/{job_id}/post` | Post clip to social |
| GET | `/api/clip/{job_id}/video/{clip_index}` | Stream clip MP4 for in-browser preview |
| GET | `/api/clips` | List all clips |

## Platform Auth

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tiktok/auth` | Start TikTok OAuth |
| GET | `/api/tiktok/callback` | TikTok OAuth callback |
| GET | `/api/tiktok/status` | TikTok connection status |
| POST | `/api/tiktok/disconnect` | Clear TikTok tokens from .env |
| POST | `/api/screen-record/convert` | Convert WebM screen recording to MP4 via ffmpeg |
| GET | `/api/youtube/status` | YouTube connection status |
| GET | `/api/youtube/auth` | Start YouTube OAuth |
| GET | `/api/youtube/callback` | YouTube OAuth callback |
| POST | `/api/youtube/disconnect` | Disconnect YouTube account |
| POST | `/api/youtube/upload` | Upload video to YouTube |
| GET | `/api/drive/status` | Google Drive connection status (`{configured, authorized, email, auth_url}`) |
| GET | `/api/drive/auth` | Start Drive OAuth — redirects to Google consent (reuses YouTube OAuth client, scope `drive`) |
| GET | `/api/drive/callback` | Drive OAuth callback — exchanges code, stores `data/drive_token.json` |
| POST | `/api/drive/disconnect` | Remove the stored Drive OAuth token |
| POST | `/api/drive/create_folder` | Create Drive folder |
| GET | `/api/gmail/status` | Gmail send-as status (`{configured, authorized, email, auth_url}`) |
| GET | `/api/gmail/auth` | Start Gmail OAuth — Google consent (reuses YouTube OAuth client, scope `gmail.send` + `userinfo.email`) |
| GET | `/api/gmail/callback` | Gmail OAuth callback — exchanges code, stores `data/gmail_token.json` |
| POST | `/api/gmail/disconnect` | Remove the stored Gmail OAuth token |

## Click Studio (YouTube Intelligence)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/yt/competitor-spy` | Start Market Scout analysis job |
| GET | `/api/yt/competitor-spy/{job_id}` | Poll Market Scout job |
| POST | `/api/yt/scout-remix` | Rewrite Scout video concept in user's voice (Foundation-powered — ONLY LLM call in Scout) |
| POST | `/api/yt/script` | Generate YouTube video script |
| POST | `/api/yt/script-formula` | **Script Lab** — hook/CTA/outline/end/ideas |
| POST | `/api/yt/seo-package` | Generate title/description/tags |
| POST | `/api/yt/content-calendar` | Generate Trend Radar topic calendar |
| POST | `/api/yt/adapt-concept` | Remix concept for local market |
| POST | `/api/yt/video-advisor` | AI video strategy advice |
| POST | `/api/yt/scheduler/save` | Save content schedule |
| GET | `/api/yt/scheduler` | Load content schedule |
| POST | `/api/yt/pillar-plan` | Generate pillar content plan |
| GET | `/api/yt/ai-persona` | List AI Persona thumbnail photos |
| POST | `/api/yt/ai-persona/photos` | Upload AI Persona headshots/body shots |
| DELETE | `/api/yt/ai-persona/photos/{photo_id}` | Delete AI Persona photo |
| GET | `/api/yt/ai-persona/photos/{photo_id}` | Serve AI Persona image |
| POST | `/api/yt/cover-forge` | Generate thumbnail variants, image prompts, and quality checks |
| POST | `/api/yt/repurpose` | Repurpose video for Shorts/IG/TikTok/Blog |
| POST | `/api/yt/lead-page` | Generate lead page copy |

## Recording Studio

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/studio/today-topic` | Get today's shoot topic from schedule |
| POST | `/api/studio/generate-script` | GPT-4o script from topic/pillar/market |
| POST | `/api/studio/publish/telegram` | Send studio recording blob directly to Telegram |
| POST | `/api/studio/publish/youtube` | Upload studio recording blob directly to YouTube |
| POST | `/api/studio/social-posts` | GPT-4o social posts for LinkedIn/Facebook/Instagram/X |
| POST | `/api/studio/show-notes` | GPT-4o Buzzsprout-ready show notes |

---

## Key Endpoint Shapes

### POST /api/yt/competitor-spy
```json
Request:  { "city": "Springfield MO", "audience": "home sellers" }
Response: { "job_id": "uuid", "status": "running" }
Poll:     GET /api/yt/competitor-spy/{job_id}
Poll response includes: status, step, step_statuses{}, result{}
```

### GET /api/yt/competitor-spy/{job_id} — result shape
```json
{
  "status": "complete",
  "step_statuses": { "scanning_market": {"status":"completed","error":null}, ... },
  "result": {
    "market_demand": "...", "best_format": "...", "opportunity_gap": "...",
    "content_ideas": [...], "viral_outliers": [...],
    "top_videos_ranked": [{
      "title","channel","views","likes","comments","thumbnail","url","video_id","duration","published_at",
      "channel_id","channel_url","channel_thumbnail","subs",
      "viral_multiplier",
      "score",    // float: views / subs — YouTube Data API deterministic math, zero LLM
      "popular"   // bool: score >= VIRALITY_POPULAR_THRESHOLD (1.5)
    }],
    "top_channels": [...], "hot_searches": [...], "market_standards": [...]
  }
}
```

### POST /api/yt/scout-remix
```json
Request:  { "title": "...", "channel": "...", "views": 123456, "score": 4.2, "popular": true, "market": "Springfield MO" }
Response: { "hook": "...", "concept": "...", "angle": "...", "cta": "..." }
Errors:   422 { "error": "...", "foundation_not_ready": true } — Foundation gate failed (< 5 samples)
Notes:
  The ONLY LLM call in Scout. All virality numbers in the request come from YouTube Data API
  deterministic math — no LLM ever generates a view count, sub count, or score.
  Routes through get_brand_context(task_type='scout_remix_script') → Foundation voice samples.
  Uses claude-sonnet-4-5. Returns structured JSON with hook/concept/angle/cta.
```

### POST /api/yt/script-formula (Script Lab)
```json
Request:  { "topic": "...", "city": "...", "audience": "...", "pillar": "..." }
Response: { "hook": "...", "early_cta": "...", "body_outline": [...], "body_sections": [...], "end_screen": "...", "full_script": "...", "next_video_ideas": [...] }
```

### POST /api/studio/generate-script
```json
Request:  { "topic": "...", "pillar": "...", "market": "...", "notes": "..." }
Response: { "script": "PART 1 — HOOK\n...", "title": "...", "hook_line": "..." }
```

### GET /api/studio/today-topic
```json
Response: { "topic": "..." | null, "pillar": "..." | null, "message": "...", "market": "..." }
```

### POST /api/studio/publish/youtube
```
Request:  multipart/form-data — video (blob, required), title (str, required), description (str, optional), privacy (str, optional, default "private")
Response: { "ok": true, "video_id": "...", "url": "https://www.youtube.com/watch?v=..." }
Error:    400 if YouTube not connected or missing title | 502 on upload failure
```

### POST /api/studio/social-posts
```json
Request:  { "title": "...", "hook_line": "...", "topic": "...", "pillar": "...", "market": "...", "episode_url": "..." }
Response: { "linkedin": "...", "facebook": "...", "instagram": "...", "x": "..." }
Notes:    At least one of title or topic required. Uses GPT-4o with JSON mode.
          LinkedIn: 150-200 words professional. Facebook: 100-150 words community.
          Instagram: 3 punchy lines + 5 hashtags. X: under 280 chars.
```

### POST /api/studio/show-notes
```json
Request:  { "title": "...", "hook_line": "...", "topic": "...", "pillar": "...", "market": "...", "script": "..." }
Response: { "show_notes": "## Episode Summary\n...\n## Key Takeaways\n- ...\n## Call to Action\n..." }
Notes:    All fields optional. Provide script to extract real talking points. Returns clean markdown.
```

### Scheduler Payload Shape
```json
{
  "schedule": [{ "day": "Mon", "time": "09:00", "channel": "Success Agent" }],
  "topics": [{ "title": "...", "pillar": "Relocation", "market": "...", "notes": "..." }],
  "market": "Springfield, MO"
}
```

---

## Social Studio

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/social-studio` | Serve social-studio.html frontend page |
| GET | `/api/social/hashtags` | Load saved hashtag sets |
| POST | `/api/social/hashtags` | Generate + save 4 hashtag sets (core/niche/local/trending) |
| POST | `/api/social/forge` | Generate 4-platform social posts (idea/episode/template mode) |
| GET | `/api/social/calendar` | Load calendar entries |
| POST | `/api/social/calendar` | Add calendar entry |
| DELETE | `/api/social/calendar/{entry_id}` | Remove calendar entry |
| POST | `/api/social/repurpose` | Extract 5 post angles from URL or transcript |
| GET | `/api/social/connections` | All platform connection statuses (FB, IG, LI, TikTok) |
| GET | `/api/social/meta/status` | Meta (Facebook + Instagram) connection status |
| GET | `/api/social/meta/auth` | Start Meta OAuth flow |
| GET | `/api/social/meta/callback` | Meta OAuth callback |
| POST | `/api/social/meta/select-page` | Set active Facebook page for publishing |
| POST | `/api/social/meta/disconnect` | Disconnect Meta account |
| POST | `/api/social/publish/facebook` | Publish text post to selected Facebook Page |
| POST | `/api/social/publish/instagram` | Instagram caption copy (image required for direct post) |
| GET | `/api/social/linkedin/status` | LinkedIn connection status |
| GET | `/api/social/linkedin/auth` | Start LinkedIn OAuth flow |
| GET | `/api/social/linkedin/callback` | LinkedIn OAuth callback |
| POST | `/api/social/linkedin/disconnect` | Disconnect LinkedIn account |
| POST | `/api/social/publish/linkedin` | Publish text post to LinkedIn profile |

### POST /api/social/forge
```json
Request:  { "mode": "idea|episode|template", "topic": "...", "title": "...", "hook_line": "...", "market": "...", "template": "Just Listed|Market Update|Client Win|Hot Take|Tip of the Week", "extra": {} }
Response: { "linkedin": "...", "facebook": "...", "instagram": "...", "x": "...", "tiktok": "...", "_foundation_thin": false, "_sample_count": 12 }
Errors:   422 { "error": "foundation_not_ready: ...", "foundation_not_ready": true } — Foundation gate failed (< 5 samples)
Notes:
  Foundation-powered — calls assert_foundation_ready() then get_brand_context() before any LLM work.
  Uses claude-sonnet-4-5 with Foundation voice samples as few-shot examples in system prompt.
  brand_data field is REMOVED — voice context now comes exclusively from Foundation.
  All content is Fair Housing compliant.
  _foundation_thin = true when sample_count is 5–14 (thin tier, non-blocking).
```

### POST /api/social/hashtags
```json
Request:  { "market": "Springfield, MO", "niche_input": "First-time buyers" }
Response: { "core": [...10], "niche": [...10], "local": [...10], "trending": [...5], "market": "...", "niche_input": "..." }
Notes:    Persists to data/social_hashtags.json. GET /api/social/hashtags returns saved sets.
```

### POST /api/social/calendar
```json
Request:  { "day": "Mon", "platform": "linkedin|facebook|instagram|x|all", "title": "...", "content": "...", "date": "YYYY-MM-DD" }
Response: { "ok": true, "id": "uuid" }
```

### POST /api/social/repurpose
```json
Request:  { "url": "https://youtube.com/watch?v=...", "transcript": "...", "market": "...", "brand_data": {} }
Response: { "angles": [ { "angle": "...", "platform": "linkedin|facebook|instagram|x", "post": "..." } ] }
Notes:    angles array has 5 items. At least one of url or transcript required.
```

---

## Social Publishing — GHL (Phase 2A)

All GHL API calls are exclusively in `services/ghl_adapter.py`. No other file may call `services.leadconnectorhq.com`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/social/ghl/accounts` | List connected GHL social accounts via GHLAdapter |
| POST | `/api/social/ghl/publish` | Publish/schedule single-platform post via GHLAdapter — writes PostAttempt audit record |
| POST | `/api/social/ghl/publish/multi` | Multi-platform publish with stagger via Arq queue |

### POST /api/social/ghl/publish
```json
Request:  { "platform": "linkedin", "content": "...", "account_id": "ghl_acct_id", "scheduled_at": "2026-05-26T09:00:00Z", "media_urls": [] }
Response: { "ok": true, "post_id": "ghl_post_id", "status": "published|scheduled", "attempt_id": "uuid" }
Errors:   400 — non-retryable GHL error (400/422) | 401 — token expired | 500 — provider/network error
Notes:    Creates Post + PostVariant + PostAttempt records in DB.
          Lifecycle events in logs: [publish.requested] [publish.attempted] [publish.completed] [publish.failed]
```

### POST /api/social/ghl/publish/multi
```json
Request:  {
  "variants": [
    { "platform": "linkedin", "content": "...", "account_id": "..." },
    { "platform": "facebook", "content": "...", "account_id": "..." }
  ],
  "scheduled_at": "2026-05-26T09:00:00Z"  // optional
}
Response: { "enqueued": [ { "platform", "attempt_id", "stagger_offset_s" } ], "post_id": "uuid" }
Notes:
  Stagger offsets (SOW section 8 Layer 1):
    linkedin +0s | x +60s | facebook +120s | instagram +180s | tiktok +240s | youtube +300s | gmb +360s
  Layer 3: deterministic jitter when scheduled_at is on :00/:15/:30/:45 of the hour.
  Layer 2: global concurrency cap of 8 enforced by Arq WorkerSettings.max_jobs.
  Jobs enqueued to Arq worker — start worker with: venv/bin/python -m arq workers.publish_worker.WorkerSettings
  Retry policy: 401→refresh+retry 1x | 429→exponential 30s 4 retries | 5xx→exponential 30s 4 retries
               400→no retry (surface as [publish.failed]) | network→10s 4 retries
```

### Workers

Start the publish worker (separate process):

```bash
venv/bin/python -m arq workers.publish_worker.WorkerSettings
```

Worker files:
- `workers/publish_worker.py` — WorkerSettings, max_jobs=8, Redis TLS (Upstash)
- `workers/publish_jobs.py` — `publish_variant` job + `verify_attempt` job (runs 5min after publish)

---

## Foundation

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/foundation` | Serve foundation.html frontend page |
| POST | `/api/foundation/ingest` | Ingest a voice sample (text + source) → embed + store in voice_samples |
| GET | `/api/foundation/status` | Foundation readiness: sample_count, has_blueprint, is_ready, latest_score |
| GET | `/api/foundation/score` | Latest foundation score + computed_at |
| GET | `/api/foundation/samples` | Paginated list of voice samples (no vectors, similarity=0.0) |

## Blueprint

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/blueprint` | Serve blueprint.html frontend page |
| POST | `/api/blueprint/auto-generate` | Analyse Foundation samples → UPSERT Blueprint draft |

### POST /api/blueprint/auto-generate
```json
Request:  {} (no body — uses TITAN_LOCATION_ID + ANTHROPIC_API_KEY from env)
Response: {
  "tone": ["direct","warm","no-fluff"],
  "cadence": "Short punchy sentences. Pauses for emphasis.",
  "pov": "first-person",
  "humor_level": "dry, sparingly",
  "vocabulary_yes": ["let's get into it","no fluff","here's the deal"],
  "vocabulary_no": [],
  "audience_primary": "First-time home sellers in Springfield MO",
  "audience_pain_points": ["don't know where to start","scared of lowball offers"],
  "pillars": [{"name":"Education","weight":0.4,"examples":["..."]}, ...],
  "_already_existed": false,
  "_sample_count": 12
}
Errors:
  422 — "foundation_not_ready: Need at least 5 voice samples …" (gate failure)
  500 — "ANTHROPIC_API_KEY not configured" or LLM returned invalid JSON
Notes:
  Precondition: >= 5 non-excluded voice samples in Foundation.
  Uses claude-sonnet-4-5 at temperature=0.3, max_tokens=2000.
  UPSERTs directly to blueprints table on success — caller reviews draft in UI.
  Does NOT ingest anything into voice_samples.
  _already_existed = true means a populated Blueprint was overwritten — frontend shows confirm dialog.
```

### POST /api/foundation/ingest
```json
Request:  { "text": "...", "source": "social_approved|social_edited|written_from_scratch|historical|brand_studio|podcast", "platform": "linkedin|...", "topic": "...", "bucket": "viral|brand|personal|conversion", "weight": 1.0, "edit_distance": null }
Response: { "sample_id": "uuid", "chunks_created": 1, "embedding_dims": 1536 }
Notes:    source is required and must be one of the 6 allowed values. Returns 422 on invalid source.
```

### GET /api/foundation/status
```json
Response: { "location_id": "...", "sample_count": 12, "latest_score": 0.74, "computed_at": "2026-05-24T...", "has_blueprint": true, "is_ready": true }
Notes:    is_ready = sample_count >= 5 AND has_blueprint. Tier thresholds: not_ready (0-4), thin (5-14), solid (15-49), deep (50+).
```

### POST /api/foundation/transcribe-and-ingest
```
Request:  multipart/form-data — audio (UploadFile, required), single_speaker (str, default "true")
Response: { "sample_id": "uuid", "chunks_created": 1, "embedding_dims": 1536, "transcript_preview": "First 300 chars...", "multi_speaker_warning": false }
Notes:    Transcribes via OpenAI Whisper (whisper-1). Ingests transcript as source="podcast", weight=1.2.
          25 MB hard limit per file. Supported formats: mp3, mp4, m4a, wav, webm, ogg, flac, mov, mpeg.
          single_speaker="false" (or "0"/"no") sets multi_speaker_warning=true in response (ingest still proceeds).
          Returns 422 if no speech detected.
```

---

## Brand Studio

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/brand-studio` | Serve brand-studio.html frontend page |
| POST | `/api/brand/profile-audit` | Score existing profile URL/bio — Brand Score + 5 axes + bio rewrite |
| POST | `/api/brand/voice-brain` | Extract voice fingerprint from content URLs or transcript text |
| POST | `/api/brand/voice-capture` | Transcribe mic recording (Whisper) + extract voice fingerprint |
| POST | `/api/brand/intake` | Generate complete brand brief (accepts optional voice_fingerprint) |
| POST | `/api/brand/bio-pack` | Generate platform-optimized bios + headshot prompt |
| POST | `/api/brand/content-plan` | Generate 12-month content plan |
| POST | `/api/brand/conversion` | Generate lead magnet, VSL script, and email sequence |

### POST /api/brand/profile-audit
```json
Request:  { "url": "...", "platform": "linkedin|youtube|instagram|manual", "bio_text": "..." }
Response: { "brand_score": 72, "axes": { "clarity": 16, "niche_authority": 14, "consistency": 15, "cta_strength": 12, "visual_identity": 15 }, "strengths": ["...","...","..."], "gaps": ["...","...","..."], "bio_rewrite": "...", "scraped_ok": true }
Notes:    All fields optional. If URL scraping fails, returns error:"manual_required" — instruct user to paste bio_text.
          Axes each scored 1-20. brand_score is overall 1-100.
```

### POST /api/brand/voice-brain
```json
Request:  { "urls": ["https://youtube.com/watch?v=..."], "transcripts": ["paste transcript here"] }
Response: { "voice_fingerprint": { "vocabulary": [...], "energy_level": "...", "signature_phrases": [...], "topics": [...], "communication_style": "...", "system_prompt_fragment": "Write in the voice of someone who: ..." } }
Notes:    At least one of urls or transcripts required. YouTube URLs use existing _yt_get() helper.
          voice_fingerprint.system_prompt_fragment is injected into /api/brand/intake system prompt when passed.
```

### POST /api/brand/voice-capture
```
Request:  multipart/form-data — audio (blob, required, webm/opus from MediaRecorder)
Response: { "transcript": "...", "voice_fingerprint": { ...same shape as voice-brain... } }
Notes:    Transcribes via OpenAI Whisper (whisper-1). Returns both transcript and fingerprint.
          Frontend uses 3-question interview flow before calling this endpoint.
```

### POST /api/brand/intake
```json
Request (all optional):
{
  "name": "...", "market": "...", "niche": "...", "price_range": "...", "years": "...",
  "brokerage": "...", "tone": "...", "personality": "...", "style": "...", "mood": "...",
  "differentiator": "...", "platform": "...", "frequency": "..."
}
Response:
{
  "value_prop": "I help [who] [outcome] without [obstacle]",
  "ica_description": "...",
  "pain_points": ["...", "...", "...", "...", "..."],
  "brand_voice_guide": "...",
  "color_direction": "...",
  "typography_direction": "...",
  "content_pillars": ["...", "...", "...", "..."],
  "thumbnail_formula": "...",
  "bio_one_liner": "...",
  "brand_brief_markdown": "# Brand Brief\n..."
}
```

### POST /api/brand/bio-pack
```json
Request:  { "brand_data": { ...brand_intake_response... } }
Response:
{
  "instagram": "...",
  "linkedin": "...",
  "youtube": "...",
  "facebook": "...",
  "google_business": "...",
  "headshot_prompt": "...",
  "consistency_checklist": ["...", "...", "...", "...", "...", "...", "...", "..."]
}
```

### POST /api/brand/content-plan
```json
Request:  { "brand_data": { ...brand_intake_response... } }
Response:
{
  "months": [
    {
      "month": "January",
      "theme": "...",
      "topics": [
        { "title": "...", "pillar": "...", "hook": "...", "format": "Short-form|YouTube|Both" }
      ]
    }
  ]
}
Notes: Returns 12 month objects, each with 4 topics.
```

### POST /api/brand/conversion
```json
Request:  { "brand_data": { ...brand_intake_response... } }
Response:
{
  "lead_magnet_title": "...",
  "lead_magnet_outline": ["...", "...", "..."],
  "vsl_script": "## HOOK\n...\n## PROBLEM\n...\n## AGITATE\n...\n## SOLUTION\n...\n## PROOF\n...\n## CTA\n...",
  "emails": [
    { "subject": "...", "body": "..." }
  ]
}
Notes: emails array has 5 items — Welcome, Value, Story, Objection, CTA sequence.
```

---

## Content Board — 30-Day Calendar (Phase 2B)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/calendar` | Serve the 30-Day Content Board page (frontend/calendar.html) |
| GET | `/api/calendar` | List posts in a date range (default today → +30 days) |
| POST | `/api/calendar/auto-plan` | Auto-generate posts from Blueprint pillars + Vyral mix |
| GET | `/api/calendar/posts/{post_id}` | Full post with all variants |
| PATCH | `/api/calendar/posts/{post_id}` | Update Post.scheduled_at (draft or scheduled only) |
| POST | `/api/calendar/posts/{post_id}/variants/generate` | Generate platform-specific variants via Foundation |
| POST | `/api/calendar/posts/{post_id}/publish` | Publish all variants via Arq queue (stagger applied) |
| DELETE | `/api/calendar/posts/{post_id}` | Delete a draft post (cascades variants + attempts) |

### GET /api/calendar
```json
Query:    ?from_date=2026-05-27&to_date=2026-06-26 (both optional)
Response: {
  "posts": [
    {
      "id": "uuid", "bucket": "viral", "scheduled_at": "2026-05-28T15:00:00+00:00",
      "status": "draft", "source": "auto_plan",
      "caption_preview": "First 80 chars...",
      "platforms_with_variants": ["base", "facebook"],
      "created_at": "..."
    }
  ],
  "from": "2026-05-27", "to": "2026-06-26"
}
```

### POST /api/calendar/auto-plan
```json
Request:  { "slot_count": 30, "start_date": "2026-05-27" } (both optional)
Response: {
  "ok": true,
  "posts_created": 30,
  "mix_actual": { "viral": 12, "brand": 9, "personal": 6, "conversion": 3, "podcast": 0 },
  "posts": [ { "id", "bucket", "pillar", "scheduled_at", "caption_preview" } ]
}
Notes:
  Loads Blueprint pillars + vyral_mix (or defaults if missing).
  Anti-clumping bucket sequence — no 3 consecutive same bucket.
  Generates base captions concurrently via Foundation (semaphore=8, gpt-4o, temp=0.8).
  Creates Post.status='draft', source='auto_plan', and PostVariant(platform='base').
  Logs each generation as [auto_plan.generated].
```

### GET /api/calendar/posts/{post_id}
```json
Response: {
  "id": "...", "bucket": "viral", "scheduled_at": "...",
  "status": "draft", "source": "auto_plan",
  "variants": [
    { "id": "...", "platform": "base", "caption": "...", "first_comment": null,
      "media_urls": [], "platform_specific": {"pillar": "Market intelligence"} },
    { "id": "...", "platform": "facebook", "caption": "...", "first_comment": null,
      "media_urls": [], "platform_specific": {} }
  ]
}
```

### PATCH /api/calendar/posts/{post_id}
```json
Request:  { "scheduled_at": "2026-06-01T15:00:00Z" }
Response: { "id": "...", "bucket": "...", "scheduled_at": "...", "status": "..." }
Errors:   400 if status not in ('draft', 'scheduled') | 404 if post not found
```

### POST /api/calendar/posts/{post_id}/variants/generate
```json
Request:  { "platforms": ["linkedin", "facebook", "instagram"] } (optional, default same)
Response: {
  "ok": true,
  "generated": [ { "id", "platform", "caption", "first_comment" } ],
  "skipped":   ["base"]  // platforms already present
}
Notes:
  Loads base PostVariant caption as source content.
  Per-platform style:
    linkedin   = professional, 3-4 sentences, no hashtags
    instagram  = conversational, line breaks, 5-8 sentences + 10-hashtag first_comment
    facebook   = friendly community, 2-3 sentences, no hashtags
    x          = under 280 chars, no hashtags
  Concurrent generation via asyncio (semaphore=3). All calls route through get_brand_context().
  Logs each as [variant.generated].
```

### POST /api/calendar/posts/{post_id}/publish
```json
Request:  (no body)
Response: {
  "ok": true,
  "enqueued": [ { "platform": "linkedin", "attempt_id": "uuid", "stagger_s": 0 } ],
  "skipped":  [ { "platform": "tiktok",   "reason": "no_connected_account" } ]
}
Notes:
  Loads all non-base PostVariants for the post.
  Matches each to a non-expired GHL account from ghl_adapter.list_accounts().
  Stagger offsets: linkedin=0, x=60, facebook=120, instagram=180, tiktok=240, youtube=300, gmb=360.
  Creates PostAttempt(status='queued') for each, sets Post.status='publishing'.
  Enqueues 'publish_variant' jobs in Arq with _defer_by=stagger_s.
  Worker must be running: venv/bin/python -m arq workers.publish_worker.WorkerSettings
```

### DELETE /api/calendar/posts/{post_id}
```json
Response: { "ok": true, "deleted": "post_id" }
Errors:   400 if status != 'draft' | 404 if not found
Notes:    Cascading delete via FK — removes PostVariants and PostAttempts.
```

---

## Brick the Foreman (Phase 3A)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/walkthrough` | Serve walk-through dashboard (frontend/walkthrough.html) |
| GET | `/permit` | Serve Brick's permit screen (frontend/permit.html) |
| GET | `/api/brick/walkthrough` | Today's walk-through data (greeting, punch list, recent actions, stats) |
| GET | `/api/brick/actions` | List pending punch list actions for location |
| POST | `/api/brick/actions/{id}/approve` | Approve + execute a punch list item (no email — "mark sent manually") |
| POST | `/api/brick/actions/{id}/approve-send` | **Approve & Send** a guest_asset_package — emails the (optionally edited) draft via Gmail send-as, then marks sent |
| POST | `/api/brick/actions/{id}/reject` | Reject punch list item with optional reason |
| GET | `/api/brick/permit` | Current tier + track record stats |
| POST | `/api/brick/permit/promote` | Advance permit tier one step |
| POST | `/api/brick/permit/demote` | Reduce permit tier one step |
| POST | `/api/brick/memory` | Add a standing instruction to brick_memory |
| GET | `/api/brick/memory` | List active brick_memory rows |
| DELETE | `/api/brick/memory/{id}` | Soft-delete a brick_memory row (active=False) |
| POST | `/api/brick/run-planning` | Manually trigger daily planning loop |

### GET /api/brick/walkthrough
```json
Response: {
  "greeting": "Thursday morning. 36 posts closed, nothing queued ahead.",
  "permit_tier": "draftsman",
  "posts_mtd": 36,
  "upcoming_posts": 0,
  "foundation_score": 0.0,
  "foundation_samples": 197,
  "pending_actions": [
    {
      "id": "uuid",
      "action_type": "draft_post",
      "rationale": "36 posts this month means you're closing strong — let's bank one for next week.",
      "payload": { "topic": "...", "bucket": "brand" },
      "requested_at": "ISO timestamp"
    }
  ],
  "recent_actions": [
    { "action_type": "draft_post", "outcome": "success|failure|rejected", "executed_at": "ISO timestamp" }
  ]
}
```

### POST /api/brick/actions/{id}/approve
```json
Response: { "ok": true, "result": { "post_id": "uuid", "topic": "...", "status": "draft" } }
Errors:   403 — tier gate: "Brick's current permit (draftsman) cannot execute send_guest_email (requires gc)"
          400 — action not found or not pending
Notes:    Executes the action after approval. Phase 3A supports draft_post and suggest_post_idea.
          Tier gate checked in execute_action — PermissionError → 403.
```

### POST /api/brick/actions/{id}/reject
```json
Request:  { "reason": "Topic not relevant this week" }   (optional)
Response: { "ok": true, "action_id": "uuid", "status": "rejected" }
Notes:    Sets status=rejected, records reason in review_note.
```

### GET /api/brick/permit
```json
Response: {
  "current_tier": "draftsman",
  "promoted_at": "ISO timestamp",
  "track_record": {
    "total_actions": 2,
    "completed": 1,
    "failed": 0,
    "approvals": 1,
    "rejections": 1
  }
}
```

### POST /api/brick/permit/promote
```json
Request:  {} (no body)
Response: { "ok": true, "previous_tier": "draftsman", "new_tier": "bricklayer" }
Errors:   400 if already at GC tier
```

### POST /api/brick/permit/demote
```json
Request:  {} (no body)
Response: { "ok": true, "previous_tier": "draftsman", "new_tier": "owner_builder" }
Errors:   400 if already at owner_builder tier
```

### POST /api/brick/memory
```json
Request:  { "content": "Never pitch DealCheck on Mondays", "category": "scheduling" }
Response: { "ok": true, "id": "uuid", "content": "...", "category": "..." }
Notes:    Content injected as STANDING INSTRUCTIONS into every planning prompt.
          category is optional free text (defaults to "general").
```

### GET /api/brick/memory
```json
Response: { "memories": [ { "id": "uuid", "content": "...", "category": "...", "active": true, "created_at": "...", "last_referenced_at": "..." } ] }
Notes:    Returns only active (soft-not-deleted) rows.
```

### DELETE /api/brick/memory/{id}
```json
Response: { "ok": true }
Notes:    Soft delete — sets active=False. Row preserved for audit trail.
```

### POST /api/brick/run-planning
```json
Response: {
  "ok": true,
  "greeting": "Thursday morning. 36 posts closed, nothing queued ahead.",
  "actions_created": ["draft_post", "draft_post", "suggest_post_idea"],
  "walk_through_items": [
    { "action_type": "draft_post", "rationale": "...", "payload": {"topic":"...","bucket":"..."} }
  ]
}
Notes:    Triggers the same logic as the 4am cron. Useful for manual testing.
          Sends Telegram notification on completion (if configured).
          Active brick_memory rows are injected as STANDING INSTRUCTIONS into the planning prompt.
          last_referenced_at updated for each memory row read.
```

---

## Phase 5 — Projects (Job Site + Ship It)

### Pages

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/projects` | Serve projects.html — Job Site (project list) |
| GET | `/project/{project_id}` | Serve project.html — Ship It wizard (4-step review) |

### Project API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/projects` | List projects (optional `?status=review&limit=50`) |
| GET | `/api/projects/{id}` | Single project detail |
| PATCH | `/api/projects/{id}` | Update editable fields |
| POST | `/api/projects/{id}/transition` | State-machine transition |
| GET | `/api/projects/{id}/clips` | List clips for project (rendered_url returns /video endpoint, not filesystem path) |
| GET | `/api/projects/{id}/clips/{clipId}/video` | Stream rendered MP4 for in-browser preview |
| PATCH | `/api/projects/{id}/clips/{clipId}` | Update clip caption/status/hook_text |
| POST | `/api/projects/{id}/ship-it` | Hero button — trigger full pipeline |
| POST | `/api/projects/from-upload` | Phase C — upload pre-recorded file → project + transcription |
| POST | `/api/projects/{id}/schedule-closing` | Step 4 — schedule publish + create posts |
| POST | `/api/projects/{id}/generate-title` | Generate a clean episode title from the transcript (GPT-4o); saves `project.title` |
| POST | `/api/projects/{id}/build-asset-package` | Build guest asset package(s) → Drive folder + uploads + drafted email → Punch List |

### GET /api/projects
```json
Query:    ?status=review&limit=20 (both optional)
Response: [ { "id", "title", "status", "wizard_step", "episode_number", "mp3_url",
              "transcript", "show_notes", "sponsor_placement", "guest_ids",
              "closing_scheduled_at", "closed_at", "created_at", "updated_at" } ]
```

### POST /api/projects/from-upload
```
Request:  multipart/form-data — file (UploadFile, required), title (str, optional)
Response: { "project_id": "uuid", "project": {...} }
Accepted: .mp4, .mov, .webm, .mp3, .m4a
Errors:   400 — unsupported extension | 500 — location not configured or DB error
Notes:    Saves file to data/recordings/{project_id}.{ext}. Creates Project with
          status='recording_done', transcription_status='pending'.
          Kicks off Whisper transcription in background immediately.
          Caller redirects to /project/{project_id} on 201.
          Uses XMLHttpRequest in the Studio upload panel to show upload progress bar.
```

### POST /api/projects/{id}/ship-it
```json
Request:  (no body)
Response: { "ok": true, "project_id": "uuid", "status": "processing" }
Errors:   400 — Project not in review or recording_done | 404 — Not found
Notes:    Transitions project to 'processing', fires background task:
          a. select_sponsor() round-robin from sponsors.json
          b. detect_clips_for_project() → clip candidates from transcript
          c. render_all_clips() → 9:16 MP4s + SRTs in data/project_clips/{id}/
          d. Foundation show_notes + clip_captions via get_brand_context()
          e. Persists all to DB, transitions to 'review'
```

### POST /api/projects/{id}/schedule-closing
```json
Request:  { "closing_at": "2026-06-01T08:00:00Z", "platforms": ["linkedin","facebook"] }
Response: { ...project dict..., "message": "Closing lined up for June 1 at 8:00 AM. ..." }
Errors:   400 — Project must be in 'review' | 404 — Not found
Notes:    Transitions project to 'scheduled'.
          Auto-assigns episode_number = MAX(episode_number)+1 if not already set (starts at 101).
          Fires four background tasks:
            1. _update_guest_statuses — advances linked guests → 'recorded'
            2. _create_closing_posts  — creates Post+PostVariant rows per platform with stagger
            3. _distribute_project   — uploads to Buzzsprout (private draft) + YouTube (private),
               persists buzzsprout_url/buzzsprout_episode_id/youtube_url/youtube_video_id,
               adds entry to pipeline/scheduler queue so flip_to_public() fires at closing_at.
            4. _build_guest_asset_package — per linked guest: creates a Drive folder + uploads
               (assembled MP3, source video, transcript.txt, show notes, top-2 Shorts by virality),
               writes assets_drive_url back to the guest, drafts the asset email in JP's voice
               (Foundation), and drops a 'guest_asset_package' item on the Punch List for one-tap
               approve. Degrades gracefully when Drive isn't configured (email still drafts with
               episode links; uploads skipped). Approving stamps guest.assets_sent_at; Gmail
               send-as (Phase 6) plugs into the same dispatch branch later.
          Show notes converted from Markdown → HTML before Buzzsprout upload.
          YouTube uploads PUBLIC by default (env override PODCLICK_YOUTUBE_PRIVACY=private|unlisted|public).
Response fields added (Phase B): buzzsprout_url, buzzsprout_episode_id, youtube_url,
          youtube_video_id, legacy_metadata (all nullable until distribution completes).
```

### POST /api/projects/{id}/transition
```json
Request:  { "new_status": "review" }
Response: { ...updated project dict... }
State machine: draft→recording_done | recording_done→processing|review |
               processing→review|failed | review→scheduled|closing |
               scheduled→closing | closing→closed|failed | failed→review
```

### POST /api/brick/actions/{action_id}/approve-send
```json
Request:  { "email_body": "<edited full email incl. 'Subject:' line>", "send": true }   (both optional)
Response: { "ok": true, "sent": true, "to": "guest@x.com", "gmail_email": "jp@titanreteam.com", "result": {...} }
Errors:
  409 { "ok": false, "needs_gmail": true, "auth_url": "/api/gmail/auth" } — Gmail not connected (nothing sent/marked)
  400 — wrong action_type / already actioned / no recipient
  502 — Gmail send failed
Notes:
  The ONLY path that emails a guest. send=false → marks sent WITHOUT emailing (user sent it themselves).
  send=true (default) + Gmail not connected → 409 needs_gmail. Splits the "Subject:" line off the
  top of the drafted/edited email; sends the rest as the body via pipeline/gmail_send.send_message
  (run_in_executor). On success calls BrickAgent.approve_action (stamps guest.assets_sent_at, marks executed).
  An edited email_body is persisted back to the action payload before sending so the record matches.
```

### POST /api/studio/re-daily-brief
```json
Request:  { "topic": "", "length_min": 20, "add_to_board": true, "date": "YYYY-MM-DD" }  (all optional)
Response: { "ok": true, "title", "hook", "script", "word_count", "est_minutes",
            "used_foundation", "sample_count", "post_id" }
Notes:
  Generates a teleprompter-ready 15–30 min RE Daily Brief podcast script in the host's
  Foundation voice (get_brand_context, task_type=podcast_script_outline, claude-sonnet-4-5,
  run_in_executor). Cold-open hook → 3-4 industry segments → tactical takeaway → CTA.
  Does NOT invent stats/sources (trends only). add_to_board=true creates a draft Post
  (bucket='podcast', source='manual') dated today 09:00 on the 30-day board.
  Frontend: 🎙️ RE Daily Brief button on /calendar → modal → "🎬 Film This Now" hands the
  (editable) script to the Studio teleprompter via localStorage(podclick_teleprompter_script).
```
