# PodClick API Reference
> Last updated: 2026-05-19 | Server: http://localhost:8765 | Update on new routes.

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
| GET | `/api/drive/status` | Google Drive connection status |
| POST | `/api/drive/create_folder` | Create Drive folder |

## Click Studio (YouTube Intelligence)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/yt/competitor-spy` | Start Market Scout analysis job |
| GET | `/api/yt/competitor-spy/{job_id}` | Poll Market Scout job |
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
    "top_videos_ranked": [{ "title","channel","views","likes","comments","thumbnail","url","video_id","duration","published_at","viral_multiplier" }],
    "top_channels": [...], "hot_searches": [...], "market_standards": [...]
  }
}
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
Request:  { "mode": "idea|episode|template", "topic": "...", "title": "...", "hook_line": "...", "market": "...", "template": "Just Listed|Market Update|Client Win|Hot Take|Tip of the Week", "extra": {}, "brand_data": { ...brand_intake_response... } }
Response: { "linkedin": "...", "facebook": "...", "instagram": "...", "x": "..." }
Notes:    brand_data is optional but improves specificity. All content is Fair Housing compliant.
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
