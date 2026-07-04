# PodClick — Bugs & Fixes Log
> Append new entries at the bottom. Never delete entries. Format: `## [date] — Title`

---

## 2026-05-19 — Progress Bar Shows 114%

**Symptom:** Market Scout progress bar hit 114% while step 1 spun forever.

**Root Cause:** Frontend `STEPS` array had 7 entries (work steps only), but backend `_YT_STEPS` included an 8th "complete" step. When all 8 steps fired, 8/7 = 1.14 = 114%.

**Fix:**
- Backend: `_work_steps = [s for s in _YT_STEPS if s != "complete"]` — only 7 steps in `step_statuses`
- Frontend: `Math.min(100, Math.round((completedCount / STEPS.length) * 100))` — cap at 100%
- Both STEPS array (7) and step_statuses keys (7) now match exactly

**File:** `main.py` + `frontend/youtube-studio.html` `pollJob()`

---

## 2026-05-19 — All Steps Gray at 100% (Fast-Job Gray Steps Bug)

**Symptom:** Job completed instantly (quota exhausted), all steps showed as gray/pending even though progress said 100%.

**Root Cause:** `step_statuses` was not pre-populated in the job dict. Jobs completing in <1s meant the first poll hit an already-complete job with empty `step_statuses: {}`. `buildStepsUI({})` rendered all steps as pending.

**Fix:** Pre-initialize all 7 steps as "pending" at job creation:
```python
yt_spy_jobs[job_id] = {
    "step_statuses": {k: {"status": "pending", "error": None} for k in _work_steps},
    ...
}
```

**File:** `main.py` — job dict initialization in `run_competitor_spy()` endpoint

---

## 2026-05-19 — Progress Panel Frozen (renderResultTabs Error)

**Symptom:** After 100% progress, page froze on progress panel instead of showing report.

**Root Cause:** `clearInterval(pollTimer)` was called, then `renderResultTabs()` threw an exception. Since the timer was already cleared, the error left the UI stuck on the progress panel.

**Fix:** Wrap both `showReport()` and `renderResultTabs()` in separate try/catch blocks. Explicitly hide progress panel and show report panel regardless of render errors:
```javascript
try { showReport(result); } catch(e) { console.error('[PodClick] showReport:', e); }
try { renderResultTabs(result); } catch(e) { console.error('[PodClick] renderResultTabs:', e); }
document.getElementById('progress-panel').classList.add('hidden');
document.getElementById('report-panel').classList.remove('hidden');
```

**File:** `frontend/youtube-studio.html` `pollJob()`

---

## 2026-05-19 — Copy Buttons Broken (JSON.stringify in onclick Attribute)

**Symptom:** Copy buttons in Script Lab and repurpose sections did nothing or threw JS errors.

**Root Cause:** `JSON.stringify(text)` produces `"quoted string"` (with double quotes). When embedded in a double-quoted `onclick=""` attribute, the double quotes terminate the attribute early:
```html
<!-- BROKEN: outer " matches first inner " -->
<button onclick="copyText(this, "text value")">
```

**Fix (double-quoted attrs):** `.replace(/"/g, "&quot;")` on the JSON.stringify output:
```javascript
`onclick="copyText(this, ${JSON.stringify(text).replace(/"/g, '&quot;')})">`
```

**Fix (single-quoted attrs):** Use `safeAttr()` helper (also handles apostrophes in titles):
```javascript
function safeAttr(v) {
  return JSON.stringify(v == null ? '' : String(v)).replace(/'/g, "&#39;");
}
`onclick='openAdaptModal(${safeAttr(v.title)}, ${safeAttr(city)})'`
```

**Fix (arbitrary text):** Use `data-text` attribute + addEventListener (no encoding needed):
```javascript
btn.dataset.text = points[i];
btn.addEventListener('click', () => copyText(btn, btn.dataset.text));
```

**File:** `frontend/youtube-studio.html` — multiple locations in Script Lab, repurpose section, video grid

---

## 2026-05-19 — Python 3.9 Union Type Syntax Error

**Symptom:** Server crashed on startup: `SyntaxError: unsupported operand type(s)...`

**Root Cause:** Used Python 3.10+ union type annotation syntax `str | None` in Python 3.9 venv.

**Fix:** Remove type annotation from function parameter. Use plain `error=None` with no type hint:
```python
# BROKEN (3.10+):
def _mark_step(job_id: str, step_key: str, error: str | None = None):

# FIXED (3.9 compatible):
def _mark_step(job_id, step_key, error=None):
```

**File:** `main.py` — `_mark_step()` function

---

## 2026-05-19 — showToast Not Defined in studio.html

**Symptom:** `ReferenceError: showToast is not defined` in browser console.

**Root Cause:** Function was named `toast()` in studio.html, but calls used `showToast()`.

**Fix:** Changed all `showToast(...)` calls to `toast(...)`.

**File:** `frontend/studio.html`

---

## 2026-05-20 — Script Lab Sent Talking Points Instead of Full Scripts

**Symptom:** Script Lab's "Full Teleprompter Script" and "Send to Teleprompter" paths produced talking points/body outline content instead of a complete read-aloud script.

**Root Cause:** `/api/yt/script-formula` only requested `body_outline`, and `_assembleFullScript()` built the "full" script by joining those outline bullets.

**Fix:** Added `full_script` and complete `body_sections` to the Script Lab API contract. Updated `_assembleFullScript()` and SEO generation to prefer the complete `full_script`, with outline fallback for older responses.

**Files:** `main.py`, `frontend/youtube-studio.html`, `docs/API.md`, `docs/FRONTEND.md`

---

## 2026-05-20 — Click Studio Plan Tools Did Not Feed Create or Scheduler

**Symptom:** Trend Radar topics required manual retyping in Create, and Pillar Planner ideas did not populate the Content Scheduler queue used by today's studio topic.

**Root Cause:** Plan tools rendered useful ideas, but only some paths generated scripts directly. There was no shared handoff helper for Create, and `saveSchedule()` only persisted shoot days.

**Fix:** Added `useTopicInCreate()` for Trend Radar/Pillar Planner handoff, added scheduler topic queue rendering, and persisted queued topics plus market in `/api/yt/scheduler/save`.

**Files:** `frontend/youtube-studio.html`, `docs/API.md`, `docs/FRONTEND.md`

---

## 2026-05-19 — OPENAI_API_KEY NameError in generate-script

**Symptom:** `NameError: name 'OPENAI_API_KEY' is not defined` when calling generate-script endpoint.

**Root Cause:** Used bare `OPENAI_API_KEY` variable instead of `os.getenv("OPENAI_API_KEY")`.

**Fix:** `client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))`

**File:** `main.py` — `generate_script()` endpoint handler

---

## Known Limitations (not bugs — design constraints)

- **YouTube quota exhaustion:** When daily 10K unit quota is spent, all `_yt_get()` calls return `{}` silently. Steps complete instantly with no data. Quota resets midnight Pacific. No fix needed — handled gracefully. Consider quota monitoring if usage increases.
- **_yt_get silent failure:** Returns `{}` on ALL errors including auth failures, network errors, 403, 429. Good for resilience; bad for debugging. If spy runs return empty data on a fresh day, check YouTube API console for quota/auth issues.

---

## 2026-05-20 — Competitor Card Missing Channel Thumbnail, URL, and Top Video

**Symptom:** Channels tab showed letter-avatar placeholder only; no channel thumbnail, no YouTube link, no most popular video. Top Performers tab was missing channel_id and channel_thumbnail on each video card.

**Root Cause (multi-layer):**
1. `result["channels"]` builder never extracted `channel_thumbnail` or `channel_url` from YouTube API snippet (only channel_name, subscriber_count, avg_views, top_format, channel_id)
2. `top_videos_list` builder never extracted `channel_id`, `channel_thumbnail`, or `channel_url` onto video objects
3. Frontend `normChannels` dropped `channel_id` from passthrough
4. Channel card rendered hardcoded letter avatar — no image branch existed
5. "Most popular video" per channel never derived or displayed

**Data Contract Established — CompetitorCard:**
```
video_id, title, thumbnail, url,
views, likes, comments, published_at, duration,
channel_id, channel_name, channel_thumbnail, channel_url, subs,
viral_multiplier
```

**ChannelCard extends CompetitorCard + `top_video: CompetitorCard | null`** (derived at render time from allVideos grouped by channel_id)

**Fix:**
- `main.py` — `top_videos_list.append()`: added channel_id, channel_thumbnail (lookup from channel_list), channel_url
- `main.py` — `result["channels"]` builder: added channel_thumbnail, channel_url, subs (int)
- `youtube-studio.html` — `allVideos` map: added channel_id, channel_thumbnail, channel_url to contract
- `youtube-studio.html` — `normChannels` map: passthrough channel_id, channel_thumbnail, channel_url
- `youtube-studio.html` — Channel card HTML: img avatar with letter fallback, clickable channel link, top_video block with thumbnail + view count + Watch link

**Files changed:** `main.py`, `frontend/youtube-studio.html` (normalization layer + channel card HTML only)

---

## 2026-05-20 — Broken Thumbnail When Video Has No video_id (Quota-Exhausted AI Cards)

**Symptom:** Video cards in Top Performers showed a broken image icon when the YouTube API quota was exhausted. AI-generated viral_outliers had no `video_id`, `thumbnail`, or `url` fields.

**Root Cause (multi-layer):**
1. `renderVideoGrid` built `src="${thumbnail}"` where thumbnail was `""` (empty) — browsers don't reliably fire `onerror` on empty src
2. `onerror` fallback built URL as `https://i.ytimg.com/vi/${video_id}/hqdefault.jpg` — with empty `video_id` this becomes `https://i.ytimg.com/vi//hqdefault.jpg` (malformed, also 404)
3. AI prompt for `viral_outliers` schema omitted `video_id`, `thumbnail`, `url` fields — GPT-4o left them out entirely

**What happens during quota exhaustion:** `_yt_get()` returns `{}` for all calls → `gathered["outliers"]` is empty → the `if gathered.get("outliers"):` override is skipped → AI-generated `viral_outliers` (no video_id/thumbnail/url) flow through to frontend

**Fix:**
- `frontend/youtube-studio.html` — `renderVideoGrid`: replaced static img tag with conditional:
  - If `thumbnail` or `video_id` is present: render `<img>` with onerror → hide img + show 📹 placeholder div
  - If both are empty: render 📹 placeholder div directly (no img tag, no network request)
- `main.py` — AI prompt `viral_outliers` schema: added `"video_id": "", "thumbnail": "", "url": ""` fields so GPT-4o includes them (even as empty strings)

**Files:** `frontend/youtube-studio.html` `renderVideoGrid()`, `main.py` AI prompt in `_run_competitor_spy` step 7

---

## 2026-05-21 — No Toast Feedback on Schedule/Create Actions

**Symptom:** Clicking "Add to Schedule", "Add Pillar to Schedule", or "Use in Create" gave no visual confirmation. Duplicate adds were silently ignored. The file had no toast system — all feedback used blocking `alert()`.

**Fix:**
- `frontend/youtube-studio.html` — Added `ysToast(msg, kind)` function + `#ys-toast` div + CSS (mirrors studio.html's toast system)
- `addTopicToSchedule`: duplicate detected → "Already in schedule" info toast; new add → "📅 [title]... added to schedule" success toast
- `addPillarIdeasToSchedule`: refactored to track added count, toast with "N Pillar topics added" or "All topics already in schedule"
- `useTopicInCreate`: toast "✍️ Topic loaded into Script Builder" after filling Create fields

**Files:** `frontend/youtube-studio.html`

---

## 2026-05-21 — today-topic Always Returns First Queue Item (No Rotation)

**Symptom:** `/api/studio/today-topic` always returned `topics[0]` regardless of date. Every morning showed the same topic until manually removed. Queue had no self-advancing behavior.

**Root Cause:** Endpoint did a simple `topics[0]` lookup with no date awareness.

**Fix:** Date-stamp rotation — each topic gets an optional `last_served: "YYYY-MM-DD"` field written to `scheduler.json` on first access each day.
- Endpoint finds the first topic where `last_served != today`, stamps it, saves the file
- Idempotent within the same day (same topic returned all day, stamp not re-written)
- If all topics served today, falls back to `topics[0]` so Studio is never empty
- Response now includes `queue_position` and `queue_total` for UI display

**File:** `main.py` — `studio_today_topic()` endpoint

**Note:** This only occurs when YouTube API quota is exhausted (resets midnight Pacific). With real quota, `top_videos_ranked` has real `video_id` + `thumbnail` and the img loads correctly from ytimg.com.

---

## 2026-05-21 — Teleprompter Overlay Covering Whole Screen (Not Inside Camera Box)

**Symptom:** Teleprompter overlay mode covered the entire browser window including transport controls. Hovering the mouse caused it to disappear. Start/stop buttons were inaccessible. Camera feed was not visible beneath text.

**Root Cause:** Overlay used `position: fixed; inset: 0` which covered the full viewport. Mouse events were not passed through (`pointer-events` unset), blocking access to UI controls.

**Fix:**
- Replaced full-screen fixed overlay with `#cam-overlay` positioned `absolute; inset: 0` inside `#preview-shell` (which has `position: relative; overflow: hidden`)
- Added `pointer-events: none` so mouse events pass through to page buttons
- Mirrored teleprompter track as `#cam-overlay-track` inside the camera box; scroll tick syncs both elements
- Added `#cam-overlay-exit` button with `pointer-events: all` positioned inside the preview shell

**File:** `frontend/studio.html`

---

## 2026-05-21 — Device Check Modal (Riverside-Style Pre-Studio Setup)

**Symptom:** Camera button existed but camera did not turn on visually. No pre-flight device setup. Chrome blocked `getUserMedia` called automatically on page load (no user gesture). Device selection was impossible.

**Root Cause:** `checkInboundScript()` called `startCamera()` on `DOMContentLoaded` — no user gesture means Chrome silently denies `getUserMedia`.

**Fix:**
- Added full-screen `#device-check` modal that shows on page load (before studio entry)
- Left panel: 16:9 live camera preview with camera/mic toggle buttons
- Right panel: name input, camera device select, mic device select, headphones toggle, "Join Studio" / "Skip" buttons
- `dcInit()` calls `getUserMedia` in response to the page rendering (modal itself is a user-gesture context after first interaction, or user clicks Join)
- `dcEnterStudio()` transfers `_dcStream` directly to `state.camStream` without a second `getUserMedia` call
- `dcSkip()` dismisses modal and enters studio without camera
- Init now calls `dcInit()` instead of calling `startCamera()` directly

**File:** `frontend/studio.html`

---

## 2026-05-21 — Teleprompter Responsiveness Slider Barely Noticeable

**Symptom:** Moving the Responsiveness slider from min to max produced almost no change in how quickly the teleprompter tracked speech.

**Root Cause:** `getSrAlpha()` mapped the slider to an EMA alpha range of `0.15–0.50` — a spread of only 0.35. The difference between a sluggish and snappy response was imperceptible.

**Fix:** Widened alpha range to `0.10–0.85` (spread 0.75):
```javascript
function getSrAlpha() {
  const sens = parseFloat(els.vadSens.value);
  const sensNorm = (sens - 0.005) / (0.08 - 0.005);
  return 0.10 + sensNorm * 0.75; // was: 0.15 + sensNorm * 0.35
}
```

**File:** `frontend/studio.html` — `getSrAlpha()`

---

## 2026-05-21 — Teleprompter Scroll Lag on First Words (Cold-Start Alpha)

**Symptom:** After pressing record and starting to speak, there was a noticeable pause before the teleprompter began scrolling. Scroll only kicked in after a few seconds of talking.

**Root Cause:** EMA smoothing starts `speechWPS` at 0. Even with alpha=0.85, it takes several events to ramp past the 0.1 scroll threshold: `0 × 0.15 + 3.0 × 0.85 = 2.55` on first event (fine at max sensitivity), but at lower sensitivity (alpha=0.30): `0 × 0.70 + 3.0 × 0.30 = 0.90` — still below threshold. Multiple events needed.

**Fix:** Cold-start alpha — when `speechWPS < 1.0` (still ramping from silence), force `effectiveAlpha = Math.max(alpha, 0.8)` regardless of slider setting. This ensures the first few words immediately push WPS above the 0.1 scroll threshold:
```javascript
const alpha = getSrAlpha();
const effectiveAlpha = state.speechWPS < 1.0 ? Math.max(alpha, 0.8) : alpha;
state.speechWPS = state.speechWPS * (1 - effectiveAlpha) + wps * effectiveAlpha;
```
Applied to both the `isFinal` branch and the interim `wc > utteranceWords` branch in `startSpeechRecognition()`.

**File:** `frontend/studio.html` — `startSpeechRecognition()` `onresult` handler

---

## 2026-05-21 — Cover Forge Thumbnail Generation Felt Static

**Symptom:** Cover Forge only showed a plain "Generating..." button state and then rendered tiny text-only mock cards. It did not match the intended thumbnail-lab flow: AI persona/photo setup, reference options, staged security/quality checks, countdown timer, and a high-impact reveal of finished thumbnails.

**Root Cause:** The frontend treated `/api/yt/cover-forge` as a simple text concept generator. There was no state machine for the thumbnail pipeline and the backend response did not include image prompt or quality-check metadata.

**Fix:**
- Added AI Thumbnail Generator layout with video title, AI Persona/Upload selector, advanced options, reference URL, and Home Tour Mode.
- Added staged progress animation: analyzing photo, composing layout, crafting design, AI generation, and quality checks.
- Added countdown timer, progress bar, step statuses, premium-quality note, and BOOM reveal state.
- Replaced static text boxes with 16:9 thumbnail preview cards using persona, skyline/background, bold 3-5 word text, and quality-check pills.
- Extended `/api/yt/cover-forge` prompt to return `image_prompt` and `quality_checks` for each variant.

**Files:** `frontend/youtube-studio.html`, `main.py`, `docs/API.md`, `docs/FRONTEND.md`

---

## 2026-05-21 — AI Persona Library Missing From Thumbnail Workflow

**Symptom:** The transcript workflow expects agents to upload headshots, expression shots, and body/body shots so Cover Forge can create AI-persona thumbnails. Cover Forge only had a static "AI Persona" summary and a hardcoded JP placeholder; there was no upload, management, persistence, or image handoff into thumbnail generation.

**Root Cause:** The AI Persona UI was a visual stub. No backend data model existed for persona images, and Cover Forge did not send persona metadata to `/api/yt/cover-forge`.

**Fix:**
- Added persistent `data/ai_persona/` image storage plus `data/ai_persona.json` metadata.
- Added API endpoints to list, upload, delete, and serve persona photos.
- Added AI Persona Library modal with Headshots, Body Shots, and Expressions upload actions.
- Updated Cover Forge summary to show real uploaded photo counts and selected avatar.
- Updated thumbnail preview cards to use the selected uploaded persona image when available.
- Sent persona photo metadata and selected photo id into `/api/yt/cover-forge` so image prompts can reference the available AI persona set.

**Files:** `main.py`, `frontend/youtube-studio.html`, `docs/API.md`, `docs/FRONTEND.md`

---

## 2026-05-21 — Trend Radar and Pillar Planner Transcript Parity

**Symptom:** Trend Radar did not produce its own topic cards from the existing `/api/yt/content-calendar` endpoint; it only pre-filled Market Scout and ran the competitor analysis flow. Pillar Planner opened, but its market input was not wired into the shared saved-market field list because the code referenced an old `pillar-market` id.

**Root Cause:** Trend Radar was still implemented as a shortcut to Market Scout instead of a dedicated topic-discovery tool. Pillar Planner's DOM id changed to `pp-market`, but `MARKET_FIELDS` was not updated.

**Fix:**
- Reworked Trend Radar to call `/api/yt/content-calendar` directly.
- Added inline Trend Radar result cards with pillar badges, why-this-trends notes, hook angles, Schedule, Write, and Add All actions.
- Expanded the content-calendar prompt to return trend/search-intent context for each topic.
- Changed shared market persistence from `pillar-market` to `pp-market`.
- Fixed Pillar Planner prompt interpolation so `months_in_market` is actually included.

**Files:** `frontend/youtube-studio.html`, `main.py`, `docs/API.md`, `docs/FRONTEND.md`

---

## 2026-05-22 — Social Publishing (Meta + LinkedIn OAuth Setup)

**Feature:** Direct publishing from Social Studio to Facebook Pages and LinkedIn profiles.

**Required .env additions:**
```
META_APP_ID=your_facebook_app_id
META_APP_SECRET=your_facebook_app_secret
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret
```

**Meta App Setup** (https://developers.facebook.com/apps/):
- Create or use existing Facebook App
- Add "Facebook Login" product
- Valid OAuth Redirect URI: `http://localhost:8765/api/social/meta/callback`
- Required permissions: `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`
- For production: submit for App Review for `pages_manage_posts`

**LinkedIn App Setup** (https://www.linkedin.com/developers/apps):
- Create app at LinkedIn Developer Portal
- Auth tab → Authorized Redirect URLs: `http://localhost:8765/api/social/linkedin/callback`
- Required OAuth Scopes: `w_member_social`, `r_liteprofile`, `r_emailaddress`

**Instagram note:** Instagram Feed requires an image — text-only posts not supported by the API.
When user clicks "Copy Caption" on the IG card, the text is copied and they post manually with their image.

**Token storage:** `data/social_tokens.json` (never commit — add to .gitignore)

**Files:** `main.py`, `frontend/social-studio.html`, `docs/API.md`

---

## 2026-05-26 — Phase 2A: GHL locationId in request body (422 error)

**Symptom:** `POST /api/social/ghl/publish` returned GHL 422 with "property locationId should not exist".

**Root Cause:** The original `ghl_publish_post` included `locationId` in the JSON body. GHL Social Planner API expects `locationId` only in the URL path, not the request body.

**Fix:** Removed `locationId` from `_build_payload()` in `services/ghl_adapter.py`. Also added required `"type": "post"` field to payload (GHL requires it).

**File:** `services/ghl_adapter.py` — `_build_payload()` method

---

## 2026-05-26 — asyncpg AmbiguousParameterError on NULL platform filter

**Symptom:** `POST /api/social/forge` returns 500 Internal Server Error after the Foundation-powered refactor. `get_brand_context()` throws `asyncpg.exceptions.AmbiguousParameterError: could not determine data type of parameter $3`.

**Root Cause:** asyncpg cannot infer the type of a parameter used in `$3 IS NULL OR ... = $3` when the parameter might be NULL. The `IS NULL` predicate gives no type hint, and asyncpg requires an explicit cast.

**Fix:** In `services/foundation.py`, changed:
```sql
AND (:platform IS NULL OR platform IS NULL OR platform = :platform)
```
to:
```sql
AND (CAST(:platform AS text) IS NULL OR platform IS NULL OR platform = CAST(:platform AS text))
```

**Files:** `services/foundation.py` (get_brand_context SQL query, line ~145)

---

## 2026-05-27 — Phase 2B Step 0: GHL userId required + post ID extraction wrong

**Symptom 1:** `POST /api/social/ghl/publish` returned GHL 422 with "userId must be a string / userId should not be empty" after fixing the locationId issue.

**Root Cause:** GHL Social Planner POST API requires a `userId` field in the body — the GHL user ID for the location owner. This is not documented in the SOW spec but is enforced by GHL. Private integration tokens don't auto-resolve userId.

**Fix:** Added `GHL_USER_ID=HC2cVPG5PqLxs0uIUrrg` to `.env` and `config.py` (`ghl_user_id` field). Updated `_build_payload()` in `ghl_adapter.py` to include `"userId": settings.ghl_user_id` when set.

**Symptom 2:** GHL publish returned HTTP 201 success but `provider_post_id` was empty string. Server logged "GHL publish returned no post ID".

**Root Cause:** GHL response structure is `{"results": {"post": {"_id": "..."}}}` but our `_post_to_ghl()` was looking for top-level `id` or `post.id`.

**Fix:** Updated ID extraction chain in `_post_to_ghl()` to: `result.get("results", {}).get("post", {}).get("_id")` with fallback to `result.get("id")` and `result.get("post", {}).get("id")`.

**Files:** `services/ghl_adapter.py` — `_build_payload()` + `_post_to_ghl()`, `config.py`, `.env`

**Verified:** `post_attempts` row `5d12ceb4` — `status=published`, `provider_post_id=6a16636ab6f9fe3ec368beec`, `published_at=2026-05-27 03:22:18`.

---

## 2026-05-27 — Calendar modal publish: internal keys leaked into GHL payload (422)

**Symptom:** `POST /api/calendar/posts/{id}/publish` enqueues Arq jobs successfully, but worker receives GHL 422 with `"property ghl_account_id should not exist", "property stagger_offset_s should not exist"`.

**Root Cause:** `calendar_publish_post` writes `ghl_account_id` and `stagger_offset_s` into `PostVariant.platform_specific` for the worker to read. `_build_payload()` in `ghl_adapter.py` did an unconditional `payload.update(platform_specific)`, dumping ALL keys (including internal tracking fields) into the GHL POST body. GHL validates and rejects unknown properties.

**Fix:** Added `_INTERNAL_KEYS = {"ghl_account_id", "stagger_offset_s", "pillar"}` allowlist filter in `_build_payload()`. Only non-internal keys are merged into the GHL payload.

**Files:** `services/ghl_adapter.py` — `_build_payload()` method.

**Verified (Gate 7):** `post_attempts` row `d0513c57` — `status=published`, `provider_post_id=6a1702850049f6b94d57f4b7`, `published_at=2026-05-27 14:41:09`. Worker fired at 120.37s (facebook stagger=120s spec). Modal code path (`/variants/generate` → `/publish`) → Arq queue → worker → GHL confirmed end-to-end.

---

## 2026-05-27 — Arq WorkerSettings staticmethod crash (Python 3.9)

**Symptom:** `venv/bin/python -m arq workers.publish_worker.WorkerSettings` crashed on shutdown: `TypeError: 'staticmethod' object is not callable`.

**Root Cause:** Arq reads `WorkerSettings` via `settings_cls.__dict__` (raw class dictionary). In Python 3.9, `staticmethod` objects stored in `__dict__` are NOT callable directly — only callable via descriptor protocol (`Class.method`). When Arq stored `on_startup`/`on_shutdown` from `__dict__` and tried to call them, it called the raw `staticmethod` object, which fails.

**Fix:** Set `on_startup = None` and `on_shutdown = None` in `WorkerSettings`. Arq guards both with `if self.on_shutdown:` before calling, so `None` is a safe no-op.

**Files:** `workers/publish_worker.py` — `WorkerSettings` class.

---

## 2026-05-28 — Brick planning loop hit fallback (empty shell ANTHROPIC_API_KEY)

**Symptom:** `POST /api/brick/run-planning` always returned fallback plan ("Morning. Walk-through ready." + single hardcoded rationale) instead of calling Claude.

**Root Cause:** Shell environment had `ANTHROPIC_API_KEY=""` (set to empty string, e.g., from a prior export). Pydantic-settings treats an empty string env var as a valid value (overrides .env file), so `settings.anthropic_api_key` was empty even though `.env` had the real key.

**Fix:** Added `env_ignore_empty=True` to `SettingsConfigDict` in `config.py`. Pydantic-settings now treats empty-string env vars as unset and falls back to the `.env` value.

**Files:** `config.py` — `Settings.model_config`

---

## 2026-05-28 — pipeline/telegram.py used os.getenv() bypassing config.py

**Symptom:** Brick planning loop ran successfully but Telegram notification was not delivered (`send()` returned False). `is_configured()` returned False even though `.env` had both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

**Root Cause:** `pipeline/telegram.py` used `os.getenv()` directly instead of reading from `config.py` settings. Pydantic-settings reads `.env` internally but does NOT inject values into `os.environ`. Since the env vars weren't exported to the shell, `os.getenv()` returned empty strings.

**Fix:** Updated `_token()` and `_chat_id()` in `pipeline/telegram.py` to import and read from `config.settings` instead of `os.getenv()`.

**Files:** `pipeline/telegram.py` — `_token()` and `_chat_id()` helpers.

---

## 2026-05-28 — YouTube Data API 403 Referer Blocked (Scout Silent Failure)

**Symptom:** Market Scout job completes immediately with empty video grid. All API steps succeed in <1 second with no data. `_yt_get()` returns `{}` silently.

**Root Cause:** The YouTube Data API key has HTTP Referer restrictions in Google Cloud Console. `httpx.AsyncClient()` sends no `Referer` header by default, so every request returns `403 PERMISSION_DENIED — Requests from referer <empty> are blocked`. The `_yt_get()` helper silently swallows the error, returning `{}`.

**Fix:** Added `YT_API_REFERER = "http://localhost:8765/"` constant at module level. All `_httpx.AsyncClient()` instances that call YouTube Data API now pass `headers={"Referer": YT_API_REFERER}`.

**Files:** `main.py` — `YT_API_REFERER` constant, `_run_competitor_spy()` AsyncClient, video analyzer AsyncClient.

**Verified:** Live API test — Rick Astley video returned 1,777,073,707 views, 4,500,000 subs, score 394.9x, popular=True.

---

## 2026-05-30 — Step 2 Stuck on "Checking connection…" Indefinitely

**Symptom:** Onboarding step 2 (Connect GHL) showed "Checking connection…" forever. Never auto-advanced to step 3 even though GHL was already connected via `TITAN_LOCATION_ID` in `.env`.

**Root Cause:** JavaScript function declaration hoisting. Two `function onStepEnter(n)` declarations existed in the same script scope — the original at line ~579 and a "patch" block added at the bottom of the file. Both hoist to the top; the **last declaration wins**. The patch block contained:
```javascript
const _origOnStepEnter = onStepEnter;  // captures the already-hoisted second function
function onStepEnter(n) {               // this is what gets called
  _origOnStepEnter(n);                  // calls itself → infinite recursion → stack overflow
  if (n === 6) onStepEnterStep6();
}
```
Stack overflow meant `initGhlStep()` never executed, leaving the UI on the loading state.

**Fix:**
- Merged `if (n === 6) onStepEnterStep6();` directly into the original `onStepEnter()` function.
- Removed the broken `const _origOnStepEnter` override block entirely.
- Added `Promise.race()` with a 5-second timeout to `initGhlStep()` so the UI never hangs indefinitely regardless of API response time.

**File:** `frontend/onboarding.html`

---

## 2026-05-30 — Step 1 Onboarding Copy Lacks Product Explanation

**Symptom:** Onboarding step 1 jumped straight to Brick's introduction before explaining what PodClick is. A first-time user had no context for why they were being sent through this flow.

**Root Cause:** Original copy mixed product/character voice without a clear sequence. "Brick here. I'll be running your site." came before any explanation of the product.

**Fix:** Rewrote step 1 in the correct 4-part sequence:
1. Heading lands the construction metaphor: "Welcome to your job site."
2. Body explains the product in brand voice (no market-segment jargon, active phrasing).
3. Brick callout introduces the character in Brick's voice.
4. "What happens next" line sets the time expectation.

**File:** `frontend/onboarding.html`

---

## 2026-05-30 — Foundation Score Reads 0% (Never Computed)

**Symptom:** Walk-through showed "Foundation Score: 0%" despite 197 voice samples. Brick referenced "0% Foundation match" in all punch list rationales — misleading every recommendation.

**Root Cause:** `calculate_foundation_score()` was never implemented. `foundation_scores` table was empty. Every read of the table returned null → coalesced to 0.0 → displayed as 0%.

**Fix:**
- Added `calculate_foundation_score(session, location_id)` to `services/foundation.py`. Uses pgvector's `<=>` cosine distance operator — for each of 50 sampled voice samples, finds its nearest neighbor and averages the similarity scores. Inserts result into `foundation_scores`.
- Added `POST /api/foundation/compute-score` endpoint in `routers/foundation.py`.
- Changed `foundation_score` in both `brick_walkthrough` (main.py) and `_load_planning_context` (brick_agent.py) to return `None` (not 0.0) when no score has been computed. Distinguishes "uncalculated" from a real zero.
- Updated `_build_planning_prompt` to describe null score as "score not yet computed" instead of "0% match."
- Added "Compute" button in `walkthrough.html` that calls the endpoint inline.
- **Computed score for the live corpus: 65.1%** (197 samples).

**Files:** `services/foundation.py`, `routers/foundation.py`, `main.py`, `services/brick_agent.py`, `frontend/walkthrough.html`

---

## 2026-05-30 — Punch List Had 20 Items (Planning Loop Accumulation + Duplicates + Similar Actions)

**Symptom:** Walk-through punch list showed 20 items — including duplicates and "Foundation's at zero" rationales from past runs. Multiple items recommended nearly identical actions ("draft educational post," "draft tactical content").

**Root Cause (multi-layer):**
1. `run_daily_planning` created new `brick_actions` rows every run with no deduplication or slate-clearing. 5 runs × 4 actions = 20+ items.
2. Planning prompt asked for "2-4 actions" but had no hard cap and no consolidation rule.
3. Dedup at the walkthrough endpoint only caught exact-text duplicates; slightly varied rationales all passed through.

**Fix:**
- `run_daily_planning` now expires today's pending actions (`status='expired'`) before creating new ones. Makes the planning loop idempotent within a day — re-running replaces, not appends.
- Planning prompt updated to "Propose EXACTLY 3-5 PRIORITIZED actions. No more."
- Added consolidation rule: "Do not list variations of the same idea — consolidate similar drafts into one best action."
- `brick_walkthrough` deduplicates by rationale text before returning, as a last-resort safety net.
- Cleared all 24 stale accumulated actions manually; triggered fresh planning run.

**Result:** 4 distinct, correctly-reasoned actions with real 65.1% score referenced.

**Files:** `services/brick_agent.py`, `main.py`

---

## 2026-05-30 — "Built Overnight" Showed Items From 3 Days Ago

**Symptom:** "Built Overnight" section in the walk-through displayed executed actions from multiple days prior.

**Root Cause:** `brick_walkthrough` queried `brick_track_record` with only `.limit(10)` — no time filter. Returned oldest executed items regardless of when they ran.

**Fix:** Added `executed_at >= now() - interval '24 hours'` filter to the `recent_rows` query. Updated the empty-state text to "Nothing built overnight — Brick's been quiet."

**Files:** `main.py`, `frontend/walkthrough.html`

---

## 2026-05-31 — Foundation Score Never Auto-Computed After Intake (Stage 2, Step 1)

**Symptom:** New users finishing Foundation intake saw a null Foundation score in the Walk-through unless they manually clicked a hidden "Compute" button. Brick's first planning run described the Foundation as "score not yet computed" even minutes after intake completion.

**Root Cause:** `calculate_foundation_score()` was only triggered by `POST /api/foundation/compute-score` (manual call). No automatic trigger existed after sample ingestion, and no nightly cron existed to recompute for stale locations.

**Fix (two parts):**

1. **Background trigger on every ingest** — both `/api/foundation/ingest` and `/api/foundation/transcribe-and-ingest` now fire `asyncio.create_task(_bg_recompute_score(location_id))` after sample insertion. The background task opens its own session, checks that sample count >= 5, calls `calculate_foundation_score()`, and logs result. Never blocks the ingest response. Silently no-ops if samples are below threshold.

2. **Nightly cron at 03:00 America/Chicago** — added `_nightly_foundation_recompute()` to `main.py` registered on the existing APScheduler alongside the Brick planning cron. Skips recompute if a score was already computed in the last 23 hours (avoids double-work on days where intake triggered an auto-compute). Fires one hour before Brick's 04:00 planning loop so the planning context always has a fresh score.

**Verified:** POST to `/api/foundation/ingest` → score updated at `2026-05-31T14:26:02Z` (3s later, background task) → `/api/foundation/score` returns `65.1%` with 198 samples.

**Files:** `routers/foundation.py` (`_bg_recompute_score`, both ingest routes), `main.py` (`_nightly_foundation_recompute`, `_startup` cron registration)

---

## 2026-05-31 — Stage 2 Step 2: Studio → Ship It Workflow Slice (2A + 2B + 2C)

**Gap:** No path existed from "recording done in studio.html" to a Project record in the database. The recording Blob lived only in browser memory. `/api/run` (called by the existing Publish button) returned 404. Ship It was unreachable from a live recording.

**What was built (3-part bundle):**

**2A — Save & Continue (studio → project)**
- Added "What's this build?" title input to the recording tray (pre-populated from topic-title if set)
- Added "Save & Continue →" hero button in the recording tray
- `saveAndContinue()` JS function POSTs the blob + title to `POST /api/projects/from-recording`
- On success: redirects to `/project/{project_id}`
- Title fallback: `"New build — {date} {time}"` (construction vocabulary)

**2B — Auto-transcription on project load**
- `POST /api/projects/{project_id}/transcribe` endpoint: kicks off Whisper in background thread (non-blocking), returns immediately
- `_run_transcription()` background task: reads `data/recordings/{project_id}.{ext}`, calls Whisper, writes `project.transcript`, sets `transcription_status='done'` or `'failed'`
- `project.html` `boot()`: calls `_maybeAutoTranscribe()` on page load — fires transcription if recording exists but transcript is empty
- Transcription status banner shows in step 1 panel while Whisper is running
- `_pollTranscription()` polls every 4s until done or failed
- "Next Step →" button disabled until transcript is present

**2C — Database + model (schema)**
- Alembic migration `c9d4f2a1b607`: adds `recording_path` (Text, nullable) and `transcription_status` (Text, nullable) to `projects` table
- `db/models.py` Project model updated with both columns
- `_project_to_dict()` serializer updated to include both fields

**Verified:**
- `POST /api/projects/from-recording` with a real video form field → project created with `recording_path` and `transcription_status='pending'` (HTTP 201)
- `POST /api/projects/{id}/transcribe` → returns `{"ok": true, "status": "started"}` immediately; empty test file → status transitions to `'failed'` within 3s (graceful failure, not a crash)
- `/project/{id}` route serves project.html correctly

**Note:** `/api/run` (legacy Publish → Buzzsprout path) returns 404 — pre-existing broken route unrelated to this fix. Renamed Publish button to "Legacy Publish" in the tray to signal it's secondary to Save & Continue.

**Files:** `main.py` (two new endpoints + `_run_transcription` + `_project_to_dict`), `db/models.py`, `alembic/versions/c9d4f2a1b607_stage2_recording_path.py`, `frontend/studio.html` (recording tray + `saveAndContinue()`), `frontend/project.html` (transcription banner + `_maybeAutoTranscribe` + `showStep1` gate)

---

## 2026-05-31 — Stage 2 Step 2.5: Studio Recording Has No Audio for Ship It (Clip Silent Skip)

**Symptom:** Ship It on a studio-recorded project silently skipped all clip generation. No clips rendered, no SRTs written. `render_all_clips()` was never called. Foundation show notes were generated but the clip distribution pipeline produced nothing.

**Root Cause (two gaps):**

1. `_run_ship_it_async()` got `job_data` from the legacy in-memory `jobs` dict. Studio recordings (created via `POST /api/projects/from-recording`) have no associated job — `job_id` is None — so `job_data = {}`. Without `job_data["mp3_path"]`, `run_ship_it()` silently skipped clips.

2. Even if a recording file existed, it's `.webm` (browser MediaRecorder format). `render_all_clips()` expected an MP3 path (`source_mp3` param). No extraction step existed.

3. `job_data["words"]` (word-level timestamps) was also missing. Without it, `detect_clips_for_project()` skips clip detection — meaning even if audio was present, clip boundaries couldn't be computed.

**Fix:**

Added two module-level sync helpers (callable from `run_in_executor`):

- `_ship_it_extract_audio(recording_path)` — FFmpeg extracts audio track from recording to `<recording>.ship_audio.mp3`. Idempotent: skips re-extraction if output already exists and is non-empty.
- `_ship_it_whisper_words(audio_path, api_key)` — calls OpenAI Whisper API with `response_format="verbose_json"` and `timestamp_granularities=["word", "segment"]`. Returns `{text, words, segments}` in the shape `detect_clips_for_project()` expects.

In `_run_ship_it_async()`:
- Added `recording_path: str = ""` parameter (passed in from `ship_it()` endpoint)
- Added Step 2.5 block before the `run_ship_it()` call: if `job_data` is empty AND `recording_path` exists on disk, extract audio + get word timestamps → populate `job_data["mp3_path"]`, `job_data["words"]`, `job_data["segments"]`
- On extraction failure: logs warning, `job_data` stays empty, clips skipped gracefully (non-fatal)

In `ship_it()` endpoint:
- Added `recording_path = project.recording_path or ""` extraction from project
- Passes `recording_path` to `_run_ship_it_async()`

**Result:** Ship It on a studio recording now: extracts audio from webm, re-transcribes with word timestamps, detects clip candidates from actual word boundaries, renders 9:16 MP4 clips. Full pipeline active.

**Files:** `main.py` (`_ship_it_extract_audio`, `_ship_it_whisper_words`, `_run_ship_it_async` Step 2.5 block, `ship_it` endpoint)

---

## 2026-06-03 — Phase C: MP4 Upload Entry Point (Studio → Ship It from disk)

**What shipped:**

**Backend — `POST /api/projects/from-upload`** (main.py, after `create_project_from_recording`):
- Accepts `file: UploadFile` (MP4/MOV/WebM/MP3/M4A) + optional `title: str` via multipart form
- Rejects unsupported extensions with 400 before touching disk
- Saves to `data/recordings/{project_id}.{ext}`
- Creates Project record: `status='recording_done'`, `transcription_status='pending'`
- Fires `asyncio.create_task(_run_transcription(...))` immediately — Whisper starts in background
- Returns `{project_id, project: {...}}` (HTTP 201) — caller redirects to `/project/{project_id}`
- DB error handling: cleans up saved file if Project insert fails
- Pattern mirrors `create_project_from_recording` exactly (wire, don't rewrite)

**Frontend — Upload panel in `studio.html`** (`id="upload-tray"`, always visible):
- Drag-and-drop zone + "browse" file picker (`.mp4,.mov,.webm,.mp3,.m4a`)
- Shows selected filename + file size on selection
- Title field ("What's this build?") with auto-name fallback
- "Upload & Continue →" button (disabled/gray until file is selected, orange when ready)
- Upload uses `XMLHttpRequest` (not `fetch`) to fire `onprogress` events into the existing file-xfer-bar progress UI
- On 201: waits 600ms then redirects to `/project/{project_id}`
- On error: re-enables button, shows error in status line + toast

**JS functions added:** `uploadFileSelected`, `uploadDragOver`, `uploadDragLeave`, `uploadDrop`, `uploadAndContinue`, `_setUploadReady`, `_uploadFile` (module-level state)

**Docs updated:** `docs/API.md` — route table + `POST /api/projects/from-upload` shape block

**Files:** `main.py`, `frontend/studio.html`, `docs/API.md`

---

## 2026-06-03 — YouTube chapters, Vyral hashtags, Shorts upload loop

**What shipped (three additions to the Ship It → distribute path):**

**1. YouTube chapter markers (auto from Whisper segments)**
- Added `_chapters_from_segments(segments, max_chapters=8)` helper — groups Whisper verbose_json segments into time blocks, derives 3-8 chapter labels from the first 6 words of each segment
- Added `_format_chapters(chapters)` helper — formats as `0:00 Introduction\n2:15 Label…` (YouTube spec)
- Chapter markers are computed during `_run_ship_it_async` (when segments are in memory) and stored in `project.legacy_metadata["youtube_chapters"]`
- `_distribute_project()` reads stored chapters, formats them, prepends to YouTube description
- YouTube chapter requirements enforced: first at 0:00, minimum 3 chapters, no chapters within first 30s
- Chapters are NOT added to Buzzsprout (they use HTML description, no timestamps)

**2. Vyral hashtag wiring**
- `_distribute_project()` reads `data/social_hashtags.json` (the Hashtag Lab output)
- Combines `core[:10]` + `niche[:5]` sets, strips `#` prefix (YouTube tags don't use it)
- Passed as `tags=` to `upload_video()` — replaces hardcoded `["podcast", "real estate"]`
- Fallback: `["podcast", "real estate", "successagent"]` if no saved hashtag sets exist
- Shorts get same Vyral tags + `["Shorts", "YouTubeShorts"]` appended

**3. YouTube Shorts upload loop**
- After main YouTube upload succeeds, `_distribute_project()` queries the Clip table for all rendered clips for this project
- Each clip with a valid `rendered_url` on disk is uploaded as a private YouTube Short
- Title: `{hook_text} | {episode_title}` (capped at 100 chars each)
- Description: hook + full episode link (if available) + show notes preview
- Shorts only attempt if main video upload succeeded (`_yt_authorized = youtube_vid_id is not None`)
- Failures per-clip are logged and non-fatal — remaining clips continue uploading
- DEFERRED: Short `video_id` / URL is not yet stored back to the Clip DB record (see DEFERRED OPT-3 below)

**Files:** `main.py` (`_chapters_from_segments`, `_format_chapters`, `_run_ship_it_async` chapter storage, `_distribute_project` rewrite)

---

## DEFERRED OPTIMIZATIONS (known, not blocking)

Items logged here are real inefficiencies that were intentionally deferred. Do not fix during the current build sprint. Revisit when the Ship It pipeline is stable and verified.

---

### ~~DEFERRED OPT-1 — Whisper Double-Call (Cost Bleed)~~ — CLOSED 2026-06-03

**Filed:** 2026-05-31 | **Status:** ✅ CLOSED

**What it was:** Two Whisper calls per project — one text-only in `_run_transcription()`, one verbose_json in `_ship_it_whisper_words()` for word timestamps. ~$0.36 per 30-min recording vs. $0.18 optimal.

**Fix shipped (2026-06-03):**
- `_run_transcription()` rewritten: FFmpeg compress to 16kHz mono 48kbps → `{stem}.transcription.mp3`, single Whisper verbose_json call returning text + words + segments in one API call.
- Stores compressed audio path + word/segment arrays in `project.legacy_metadata` (`extracted_audio_path`, `whisper_words`, `whisper_segments`).
- `ship_it()` endpoint reads stored legacy_metadata and passes to `_run_ship_it_async()` as `stored_audio_path`, `stored_words`, `stored_segments`.
- `_run_ship_it_async` step 2.5: fast path uses stored data when available (zero second Whisper call). Fallback path (extraction + Whisper) retained for pre-fix projects where stored audio has been cleaned up.
- Also fixes the Whisper 25MB failure on real-length episodes: 40-min episode at 16kHz mono 48kbps ≈ 14MB, well under cap.
- Marathon guard: if compressed file still >24MB, chunks into 20-min segments with timestamp re-offsetting.

**UI fix (project.html):**
- `showStep1()` now handles all four transcription states in banner: running/pending (blue), failed (red, construction vocabulary), done (hidden). No dual-state possible.
- `_pollTranscription()` failed branch calls `applyProjectState()` instead of manually re-setting banner (single source of truth).
- `_maybeAutoTranscribe()` no longer auto-retries `failed` projects (user uses Retry button).

**Files:** `main.py` (`_run_transcription`, `ship_it`, `_run_ship_it_async`), `frontend/project.html`

---

### DEFERRED OPT-3 — YouTube Short URL Not Stored Back to Clip Record

**Filed:** 2026-06-03 | **Status:** Deferred

**What it is:** When `_distribute_project()` uploads each rendered clip as a YouTube Short, it logs the `video_id` but does NOT store it back to the `Clip` DB record. The Clip model has no `youtube_url` column. Short video IDs are lost after the task completes — there's no way to link back from the Clip row to its YouTube Short later.

**Impact:** JP can find the Shorts in YouTube Studio, but can't retrieve the Short URL from the PodClick UI or API. No user-facing bug today — Shorts are private and JP reviews them manually. Becomes a real gap when we add a "Share this Short" link to the project detail page.

**Ideal fix:** Add `youtube_short_url` (Text, nullable) to the `Clip` model + Alembic migration. Store `yt_result["url"]` there after each successful Short upload. Surface the link in `GET /api/projects/{id}/clips`.

**Do not fix now** — wait until the first real episode test confirms Shorts are uploading correctly. Then add the column in a Phase D/E cleanup migration.

---

### DEFERRED OPT-2 — Recording File Cleanup (.ship_audio.mp3 Accumulation)

**Filed:** 2026-05-31 | **Status:** Deferred

**What it is:** `_ship_it_extract_audio()` writes `<recording>.ship_audio.mp3` next to the source WebM recording in `data/recordings/`. Extraction is idempotent (skips if file already exists and is non-empty), which is correct. However there is no cleanup step — extracted audio files accumulate indefinitely alongside their source recordings.

**Impact:** Each extracted file is approximately the same size as the audio track from the recording (e.g., 30-minute audio ≈ 30–50MB as MP3 at -q:a 2). On a busy system with many projects, `data/recordings/` grows without bound.

**Ideal fix:** Add a cleanup routine (either post-assembly or as a periodic job) that deletes `.ship_audio.mp3` files once the assembled episode MP3 exists and the project is in `review` or later status. Alternatively, write extracted audio to a temp directory and reference it only for the duration of the Ship It run.

**Do not fix now** — data/recordings/ is local dev only. Add cleanup as part of the first production deploy checklist.

---

## 2026-06-01 — Phase A+B: Distribution columns + Buzzsprout/YouTube wiring

**What shipped:**

**Phase A — Schema migration (Alembic a7b3c8e2f015):**
- Added 5 new columns to `projects` table: `buzzsprout_url`, `buzzsprout_episode_id`, `youtube_url`, `youtube_video_id`, `legacy_metadata` (JSONB)
- Updated `db/models.py` Project model with all 5 columns
- Updated `_project_to_dict()` serializer to include all 5 new fields

**Phase B — Distribution pipeline wiring:**
- Added `_distribute_project(project_id, closing_at_ts)` async helper in `main.py`
  - Converts show_notes markdown → HTML via `markdown-it-py` before Buzzsprout upload
  - Calls `pipeline/upload.upload_episode()` as private draft (WIRE DON'T REWRITE)
  - Adds entry to `pipeline/scheduler` queue so `flip_to_public()` fires at `closing_scheduled_at`
  - Calls `pipeline/youtube.upload_video()` via executor (sync function) as `private`
  - Persists `buzzsprout_url`, `buzzsprout_episode_id`, `youtube_url`, `youtube_video_id` to project record
- Updated `schedule_closing()`:
  - Auto-increments `episode_number` from `MAX(episode_number) + 1` (defaults to 101 on first project)
  - Fires `_distribute_project()` as async background task
  - Updated response message to acknowledge background upload

**Formatting fix — Buzzsprout show notes prompt:**
- Updated Ship It step d (Foundation show notes) prompt to produce structured Buzzsprout-ready markdown sections: Episode Summary, What You'll Learn, Episode Highlights, Resources Mentioned, About the Host, Subscribe & Review
- `_distribute_project()` converts this markdown to HTML before sending to Buzzsprout API

**Files:** `db/models.py`, `alembic/versions/a7b3c8e2f015_stage2b_distribution_columns.py`, `main.py`



---

## 2026-06-03 — OPT-1 Closed: Single-transcription fix (Whisper double-call + 25MB cap)

**What shipped:**

**`_run_transcription()` rewrite (main.py):**
- FFmpeg compresses source recording to 16kHz mono ~48kbps → `{stem}.transcription.mp3`
  (40-min episode ≈ 14MB — Whisper's 25MB hard limit no longer reachable on real episodes)
- Single Whisper verbose_json call with `timestamp_granularities=["word","segment"]`
  returns `.text` + `.words` + `.segments` — no second call needed downstream
- Stores all three in `project.legacy_metadata`:
  - `extracted_audio_path` — compressed audio path for Ship It filler removal
  - `whisper_words` — word timestamps for clip detection
  - `whisper_segments` — segment timestamps for chapter markers
- Marathon guard: if compressed file still >24MB, chunks into 20-min segments with
  timestamp re-offsetting and merges results

**`ship_it()` endpoint (main.py):**
- Reads `project.legacy_metadata` before kicking off `_run_ship_it_async`
- Passes `stored_audio_path`, `stored_words`, `stored_segments` into the async task

**`_run_ship_it_async` step 2.5 (main.py):**
- Fast path: if `stored_audio_path` exists on disk and `stored_words` is non-empty,
  uses stored data directly — zero second Whisper call (OPT-1 closed)
- Fallback path: extract + Whisper for pre-fix projects or if compressed audio was cleaned up
- `_ship_it_whisper_words()` function retained as fallback only

**project.html UI fix:**
- `showStep1()` handles all four transcription states in banner: running/pending (blue "Transcribing…"),
  failed (red construction vocabulary "Transcription stalled. Paste your script above or use the Retry button."),
  done (banner hidden), else hidden. No dual-state possible.
- `_pollTranscription()` failed branch calls `applyProjectState()` — single source of truth
- `_maybeAutoTranscribe()` now returns early on `failed` status — user manually retries

**Files:** `main.py`, `frontend/project.html`, `docs/BUGS_AND_FIXES.md`


---

## 2026-06-05 — Ship It steps d+e: show notes + clip captions never generated (silent failure)

**Symptom:** After Ship It completes, `show_notes` is always null and clip `clip_caption` fields are always null. No error surfaced to the user. Steps 2 (audio) and 3 (clips) worked correctly.

**Root cause (two bugs):**

1. **Missing `session` parameter in `get_brand_context` calls.** `_run_ship_it_async` steps d and e called `get_brand_context(location_id=..., task_type=...)` — the function's first required positional argument is `session: AsyncSession`. The `TypeError` was silently caught by the outer `try/except` in each step.

2. **Pydantic v2 dict-style access on a BaseModel.** After fixing the session issue, the code tried to access the `BrandContext` object via `.get("voice_profile", {})` and `ctx["brand_profile"]` — valid in Pydantic v1 but not in v2. Replaced with attribute access: `ctx.voice_profile`, `ctx.brand_profile`, `ctx.voice_samples`, `ctx.metadata.sample_count`.

**Fix:**
- Step d: `async with _async_session() as _f_session:` wrapping the `get_brand_context` call; `session=_f_session` passed; all access changed to attribute style
- Step e: same fix per clip in the for loop
- Audit log line: `ctx.get("metadata", {}).get("sample_count", 0)` → `ctx.metadata.sample_count`

**Files:** `main.py` (`_run_ship_it_async` steps d and e)

---

## 2026-06-05 — YouTube chapters never persisted (SQLAlchemy JSONB mutation detection)

**Symptom:** After fixing the Foundation session bug, show notes saved correctly but `youtube_chapters` remained absent from `project.legacy_metadata`. `_chapters_from_segments` correctly produced 8 chapters — confirmed via direct test — but they weren't written to DB.

**Root cause:** SQLAlchemy JSONB column mutation tracking. When `existing_meta = proj.legacy_metadata` returns the dict and we mutate it in-place (`existing_meta["youtube_chapters"] = _chapters`), SQLAlchemy does not detect the change unless the column uses `MutableDict`. Reassigning `proj.legacy_metadata = existing_meta` reassigned the same dict object — SQLAlchemy saw no identity change and skipped the UPDATE.

**Fix:**
```python
from sqlalchemy.orm.attributes import flag_modified
new_meta = dict(proj.legacy_metadata or {})   # fresh copy, new object
new_meta["youtube_chapters"] = _chapters
proj.legacy_metadata = new_meta
flag_modified(proj, "legacy_metadata")          # force dirty detection
```

Applied same pattern to all future legacy_metadata writes. Also added debug print: `segments={len(_segments)} → chapters={len(_chapters)}` to make this traceable.

**Files:** `main.py` (`_run_ship_it_async` chapter persist block)

---

## 2026-06-05 — Duplicate clips accumulate on repeated Ship It runs

**Symptom:** Each Ship It run on the same project appends new Clip DB rows. After 3 runs on the same episode, 15 clip rows exist (5 clips × 3 runs) — all with identical hook text and rendered files.

**Root cause:** `_run_ship_it_async` step c renders clips and inserts rows without first clearing existing clips for the project. On re-run (fixing bugs, testing), clips accumulate.

**Fix (pending):** Before inserting new Clip rows, delete existing rows for `project_id`. Add to `_run_ship_it_async`:
```python
await session.execute(delete(Clip).where(Clip.project_id == project_uuid))
```

**Status:** Not yet fixed — deferred. Low priority for JP's own use (he runs Ship It once per episode). Will cause UI clutter if multiple runs happen. Fix before multi-user beta.

**Files:** `main.py` (`_run_ship_it_async` Clip persist block)


---

## 2026-06-11 — Full SaaS Audit: 9 fixes across nav, studio, clips, automations (+ Loom parity)

Four parallel audit agents swept all 16 pages, ~150 API routes, and the studio/clips/automation pipelines. Fixes shipped:

1. **Site-wide nav 404 (CRITICAL).** `podclick-nav.js` lived in `static/` (project root) but the server mounts `/static` from `frontend/static/` — every page silently loaded no nav. Fixed: copied to `frontend/static/`, added Brand/VSL/Permit entries + `/editor/` active matcher, added nav to `index.html`. **Files:** `static/podclick-nav.js`, `frontend/static/podclick-nav.js`, `frontend/index.html`
2. **`save-direct-video` route dead (CRITICAL).** The `@app.post("/api/studio/save-direct-video")` decorator sat on `_probe_duration()` instead of `save_direct_video()` — every Direct Video save 422'd. Moved decorator to the right function. **File:** `main.py`
3. **Studio podcast publish dead.** `_doPodcastPublishRaw` POSTed to `/api/run` (no such route). Rerouted through `POST /api/projects/from-recording` → redirects to `/project/{id}` (Ship It pipeline). Stale "Sent to Buzzsprout" copy updated. **File:** `frontend/studio.html`
4. **Punch-in data loss.** `state.elapsedSeconds` was never set (usable_end always 0) and `_uploadPunchedSegments()` had zero callers — any punch-in silently discarded everything before the punch on save/download/publish. Fixed: timer now writes `elapsedSeconds`; `saveAndContinue`, `trayDownload`, and podcast publish stitch segments via `POST /api/studio/stitch-segments` when punches exist. **File:** `frontend/studio.html`
5. **Caption styling controls never rendered.** `initEditorTools()` bound `#caption-swatches`/`#caption-positions` but the markup didn't exist. Added both to the clip editor tools sidebar (5 color swatches, bottom/middle pills). **File:** `frontend/project.html`
6. **Removed clips still uploaded as Shorts.** `_distribute_project` Shorts query had no `status != 'removed'` filter. Added. **File:** `main.py`
7. **Clip captions generated from a float.** Step e sent `clip_row.virality_score` as "CLIP TRANSCRIPT". Added `_clip_transcript_text()` — slices actual words inside the clip window from word timestamps, falls back to transcript head. **File:** `main.py`
8. **Lead Page "Export .txt" threw ReferenceError.** `downloadLeadEmailSequence()` didn't exist and `email_sequence` was never rendered. Added render block + export function (`_leadPageData` global). **File:** `frontend/youtube-studio.html`
9. **Loom parity (screen recorder, Social Studio Panel 6).** Added camera bubble PIP (canvas composite, circular bottom-left), pause/resume (`MediaRecorder.pause()`), Save to Library (POSTs to fixed `save-direct-video`, surfaces copyable `/editor/{id}` link), and AudioContext/track cleanup. **File:** `frontend/social-studio.html`

**Verified:** all 15 routes 200; OpenAPI operationId `save_direct_video_...` confirms rebind; nav renders with all 11 entries (Chrome screenshot); Studio device-check live with camera; zero console errors on /studio and /social-studio.

**Known stale doc:** the 2026-06-05 "Duplicate clips" entry says "Not yet fixed" — it IS fixed (delete-before-insert at `_run_ship_it_async`, live-verified 5 rows).

---

## 2026-06-12 — Project page "Could not load project" (nav replaced page-local topbar)

**Symptom:** /project/{id} showed "Could not load project." even though `GET /api/projects/{id}` returned 200.

**Root cause:** `podclick-nav.js` replaces ANY element with class `.topbar`. On project.html, `.topbar` is a page-local header containing `#crumbs` and the back link — not a site nav. The injected nav deleted `#crumbs`, so `renderHeader()` threw `Cannot set properties of null` and boot()'s catch swallowed it (no console.error).

**Fix:**
- `podclick-nav.js` `inject()`: only replace a `.topbar` that contains a `nav` element or `.brand` (a genuine site nav); otherwise prepend the nav above it and leave page content alone.
- `project.html` boot() catch now logs `console.error('[project] boot failed:', err)` so failures are diagnosable.

**Note:** browsers cache the nav script — hard-refresh (Cmd+Shift+R) once per page after nav updates.

**Files:** `static/podclick-nav.js`, `frontend/static/podclick-nav.js`, `frontend/project.html`

---

## 2026-06-14 — Guest Asset Package (auto-build on Closing → Drive + drafted email → Punch List)

**What shipped:** The draft-for-one-tap-approve guest asset flow. When an episode closes (or on demand), PodClick builds each linked guest's promotional package and surfaces a drafted email for approval — no Gmail send-as required yet (Phase 6 plugs into the same dispatch branch).

**Backend (`main.py`):**
- `_build_guest_asset_package(project_id)` — for each linked guest: creates a Drive folder (`pipeline/drive.create_episode_folder`), uploads assembled MP3 (`mp3_url`), source video (`recording_path`), `transcript.txt`, show notes `.md`, and the top-2 Shorts by `virality_score` (`Clip.rendered_url`, skips `status='removed'`); writes `assets_drive_url` (+ episode metadata) back to the guest in `guests.json`; drafts the email via `_compose_guest_asset_email`; creates a `guest_asset_package` BrickAction (Punch List) with the draft + Drive link + upload manifest in `payload`. Non-fatal per guest. Degrades when `drive.is_configured()` is False (uploads skipped, email still drafts with episode links).
- `_compose_guest_asset_email(guest)` — Foundation-voiced email composer (claude-sonnet-4-5), returns `(text, used_foundation, sample_count)`, falls back to a plain template on any failure (never raises). Shares the prompt with the existing `GET /api/guests/{id}/asset_email` endpoint (endpoint left intact; minor duplication noted as cleanup).
- `POST /api/projects/{id}/build-asset-package` — manual fire-and-forget trigger (same routine). Returns `guests_targeted` + message; graceful when no guests linked.
- `schedule_closing` now fires a 4th background task: `_build_guest_asset_package`.

**Brick (`services/brick_agent.py`):**
- `ACTION_TIER_MAP["guest_asset_package"] = "draftsman"` — approvable at the current tier today (vs `send_guest_email` which is gated to `gc`).
- `_dispatch_action` branch for `guest_asset_package`: stamps `guest.assets_sent_at`, returns `{status: "delivered", email, drive_url, recipient}`. This is where Gmail send-as will hook in at Phase 6.

**Verified live (server on :8765, Drive `configured:false`):**
- No-guest project → `{ok:true, guests_targeted:0, "No guests linked…"}`.
- Linked John Smith to a throwaway project → `POST build-asset-package` → background task created a `guest_asset_package` Punch List item: recipient `john@smartrealestatecoach.com`, `drive_configured:false`, skipped reason surfaced, email **drafted in JP's Foundation voice** (not the template) with real Spotify/YouTube/Apple links + existing Drive folder pulled from the guest record.
- One-tap `POST /api/brick/actions/{id}/approve` → dispatch returned `status:"delivered"` + full email; `guest.assets_sent_at` stamped. Test project `guest_ids` reverted to `[]` after.

**Still gated on JP (for live Drive uploads):** drop a Google service account JSON at `data/service_account.json` (or set `GOOGLE_SERVICE_ACCOUNT_JSON`), set `GOOGLE_DRIVE_PARENT_ID`, share the target Drive folder with the service-account email. Until then the email + episode links go out; the Drive folder/uploads are skipped gracefully.

**Known cleanup (non-blocking):** `_compose_guest_asset_email` duplicates the prompt from the `asset_email` endpoint — fold the endpoint onto the helper in a later pass. Poster asset still not generated (Cover Forge repurpose or manual upload — deferred).

**Files:** `main.py`, `services/brick_agent.py`, `docs/API.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-14 — Guest asset email: hallucinated links + no review surface (two fixes)

**Symptom 1 (caught during a live preview):** The guest asset email asked the LLM to write the episode links as a bullet list. When the episode had no real URLs yet, the model **invented** them — e.g. a fake `open.spotify.com/show/…` and `youtube.com/@…`. Sending a guest a fabricated link is a trust-killer.

**Fix 1 — deterministic links, never LLM-generated (`main.py`):**
- `_compose_guest_asset_email` now instructs the model to write the note with a literal `{{LINKS}}` token and **never** to write any URL. Code builds the link block from ONLY real values (Buzzsprout, Spotify, YouTube, Apple, Drive) and replaces the token. A regex guard (`_strip_stray_urls`) removes any URL the model emits that isn't in the allowed set. Verified: with no real URLs on the guest, the email comes back with ZERO links.
- `GET /api/guests/{id}/asset_email` refactored to delegate to `_compose_guest_asset_email` (one source of truth; the endpoint had a duplicate prompt that drifted).
- Ordering: `_build_guest_asset_package` now chains off the END of `_distribute_project` (B5) instead of racing it from `schedule_closing`, so the email carries the real Buzzsprout + YouTube links. Guest record gets `episode_url_youtube` + `episode_url_buzzsprout` from the project authoritatively.

**Symptom 2:** The Punch List (`/walkthrough`) only rendered Brick's one-line rationale + an Approve button. A `guest_asset_package` item gave no way to actually READ the email before approving — so a user could send something to a guest sight-unseen.

**Fix 2 — inline email preview in the Punch List (`frontend/walkthrough.html`):**
- `renderPunchItem` now detects `action_type === 'guest_asset_package'` and renders a preview block: a "🔒 Draft only — nothing has been sent" banner, the `To:` recipient, the Drive status line, the full email body in a scrollable `<pre>`, and a "📋 Copy email" button. Approve button relabels to "✓ Looks good — mark sent." Data already came through `/api/brick/walkthrough` `payload.email` — this was purely a render gap.
- Reassurance the architecture already guaranteed: nothing auto-sends to a guest. The email is always a `pending` draft; Gmail send-as isn't built, so delivery today = user copies it and sends from their own inbox. Approving only stamps `assets_sent_at`.

**Verified:** Built a live `guest_asset_package` item for Neal Bawa (linked guest); `/api/brick/walkthrough` returns it with the full 839-char email; served `walkthrough.html` contains the preview render code (asset-preview, copy button, banner). (Interceptor CLI not installed on this box — verified via served markup + API payload rather than a screenshot.)

**Files:** `main.py`, `frontend/walkthrough.html`, `docs/API.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-14 — Drive integration switched from service-account key → OAuth

**Why:** The Google Workspace org policy `iam.disableServiceAccountKeyCreation` blocks downloadable service-account JSON keys (Secure-by-Default). The old `pipeline/drive.py` relied on `GOOGLE_SERVICE_ACCOUNT_JSON` → dead end. OAuth (user-delegated) is the path Google recommends and needs no key file.

**What shipped (`pipeline/drive.py` rewrite + `main.py` routes):**
- `pipeline/drive.py` now uses OAuth user credentials, mirroring the working YouTube flow exactly. **Reuses the same OAuth client** (`data/youtube_client_secrets.json`) — no new credentials. Token stored at `data/drive_token.json`, auto-refreshed. Scope: `https://www.googleapis.com/auth/drive`.
- Helpers: `_secrets_path`, `is_authorized`, `is_configured` (now = "connected via OAuth"), `get_credentials`, `_save_token`, `get_auth_url`, `exchange_code`, `account_email`. `create_episode_folder` / `upload_file_to_folder` / `make_folder_public` keep identical signatures (so `_build_guest_asset_package` is untouched) — they just call the OAuth-backed `_get_service()`.
- New routes: `GET /api/drive/auth` (redirect to Google consent), `GET /api/drive/callback` (exchange + store token, success page), `POST /api/drive/disconnect`. `GET /api/drive/status` now returns `{configured, authorized, email, auth_url}`.
- The OAuth client is type **"installed" (Desktop)**, so the loopback redirect `http://localhost:8765/api/drive/callback` works without pre-registration in most cases. If Google returns `redirect_uri_mismatch`, add that URI to the client's Authorized redirect URIs.

**Verified:** `/api/drive/auth` 307-redirects to `accounts.google.com/o/oauth2/auth` with the correct client_id, `redirect_uri=…/api/drive/callback`, and `scope=…/auth/drive`. `/api/drive/status` → `authorized:false` until the user completes consent. Full upload path can only be verified live after JP grants consent (one click).

**Files:** `pipeline/drive.py`, `main.py`, `docs/API.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-14 — Episode title never auto-generated from transcript

**Symptom:** Projects kept their upload/recording filename as the title (e.g. `Neal  Bawa— 2026-06-03 17:35`), which would become the Buzzsprout + YouTube episode title at closing. Ship It generated show notes + chapters but never a title — the automation was simply missing.

**Fix (`main.py`):**
- `_generate_episode_title(transcript, guest_name, show_name)` — GPT-4o, returns one clean 50–70-char title from the transcript excerpt; leads with the guest name for interviews; empty string on failure (caller keeps existing title).
- `_looks_like_auto_title()` — detects placeholder/auto names ("New build…", "Untitled", or anything carrying a `YYYY-MM-DD` stamp) so generation only overwrites auto-titles, never a hand-set one.
- Wired into `_run_ship_it_async` show-notes persist block: every Ship It run now auto-titles the episode when the current title is a placeholder.
- `POST /api/projects/{id}/generate-title` — regenerate on demand from the stored transcript (runs the sync GPT call via `run_in_executor`), saves `project.title`.

**Verified:** Neal's project → `POST /generate-title` → `"Neal Bawa: Revolutionizing Real Estate with Data Science"` (from the real transcript), persisted to the project.

**Files:** `main.py`, `docs/API.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-14 — Studio: use iPhone as a webcam (Continuity Camera surfacing)

**What shipped:** The Device Check modal now makes the iPhone selectable as the studio camera. macOS **Continuity Camera** already exposes the iPhone as a normal `videoinput` to `getUserMedia`/`enumerateDevices` — the gaps were that it needed a re-scan when connected mid-setup, and wasn't obvious.

- `frontend/studio.html` — added a 🔄 **re-scan** button beside the camera dropdown (`dcRefreshDevices()` → re-runs `_dcPopulateDevices()`), so a just-connected iPhone appears without a page reload.
- `_dcPopulateDevices()` now flags any camera whose label matches `/iphone|continuity/i` with a 📱 prefix and shows a `#dc-cam-hint` line: detected → "pick it above"; not detected → Continuity setup tip (same Apple ID, Wi-Fi+Bluetooth, phone nearby & locked, or USB) + tap 🔄.
- Selecting it routes through existing `dcSwitchDevice()`; no backend change — the recording pipeline already records whatever `state.camStream` carries.

**Verified:** served `/studio` contains the new markup + `dcRefreshDevices` (7 matches), no boot errors. Live iPhone selection requires JP's physical phone + Continuity Camera (can't headless-test the device itself).

**Files:** `frontend/studio.html`, `docs/FRONTEND.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-16 — Episode poster auto-generated into the guest asset package (last ⏳ closed)

**What it was:** The asset package manifest carried a permanent placeholder —
`{"label": "Episode poster", "present": false, "note": "not generated yet"}`. The
poster was the only asset never produced (6/7 real). Closing the loop required a
guest headshot, which the deferred GHL intake form will supply.

**What shipped (`main.py`):**
- `_generate_episode_poster(out_path, ep_num, title, guest_name, guest_headshot, guest_tagline, host_name=None, host_headshot=None)` — SYNC PIL builder, run via `run_in_executor` (CPU work off the event loop). Renders a 1080×1080 poster: show kicker (`PODCLICK_SHOW_NAME`), `EP. {n}` badge, two gold-ringed circular avatars (host + guest), names + roles, wrapped title, "A conversation with {guest}", gold base bar. Returns `(ok, note)`.
  - **Graceful headshot fallback:** missing headshot → a gold-on-navy **initials circle** (e.g. "JD"), so a poster ALWAYS renders. The manifest note records when the placeholder was used.
  - **Title de-dup:** strips a leading `"{Guest Name}:"` prefix from the episode title (names are already on the poster) — `"Neal Bawa: Revolutionizing…"` → `"Revolutionizing…"`.
- `_resolve_headshot(name)` — finds `data/headshots/{slug}.{jpg,jpeg,png,webp}` by name slug. Convention: drop a headshot named by slug (the future guest-intake form writes here). Host resolves `jp_fluellen.png`.
- `_slugify_name()` helper + `POSTER_SHOW_NAME` / `POSTER_HOST_NAME` env overrides (`PODCLICK_SHOW_NAME`, `PODCLICK_HOST_NAME`) for white-label installs.
- `_drive_build_and_upload(...)` gained a `poster_path=None` param → uploads `EP{n} - poster.png` (image/png) into the guest's Drive folder alongside the other assets.
- `_build_guest_asset_package`: the poster is **guest-specific** (uses that guest's headshot), so it's generated per-guest inside the loop (not in the shared manifest). Each guest gets `g_manifest = base + poster entry`; the per-guest manifest flows into the Punch List payload and the reuse branch. Poster path: `data/posters/EP{ep}_{slug}.png`.

**Verified (server not required — direct function test):**
- `_resolve_headshot("Neal Bawa")` → `data/headshots/neal_bawa.jpg`; `("JP Fluellen")` → `jp_fluellen.png`.
- Neal poster (both real headshots) → `ok=True`, no note.
- Fallback (guest headshot absent) → `ok=True`, note `"guest headshot missing — initials placeholder used"`, renders JP's real photo + a clean "JD" initials circle.
- `main.py` parses; test artifacts removed.

**Headshot convention going forward:** `data/headshots/{name-slug}.{jpg,png}`. Neal + JP staged. The deferred GHL guest-intake form will write guest headshots here automatically — until then, a guest with no headshot still gets a poster (initials), and dropping a file named by slug upgrades it on the next build.

**Files:** `main.py`, `docs/BUGS_AND_FIXES.md`

**Reuse-branch backfill (follow-up, same day):** Neal's Drive folder was built
*before* the poster existed, so his rebuild hit the reuse branch (which skips
re-uploading the large MP3/MP4). Added `_drive_upload_one(folder_url, path, mime,
fname)` — extracts the folder id from the stored Drive URL and uploads a single
late-added file. The reuse branch now backfills the poster (small PNG) into the
existing folder; on failure it drops "Episode poster" from `uploaded` and records
`"Episode poster (Drive backfill failed)"` in `skipped`. **Live-verified:** Neal's
folder (`1ND1rqdpBD0TBYESdr56k`) now contains all 7 assets including
`EP101 - poster.png`; rebuild reported `uploaded` incl. Episode poster, `skipped: []`.

---

## 2026-06-16 — Phase 6: Gmail send-as + "Approve & Send" review gate (guest emails actually send)

**What it was:** The guest asset email was a draft only. Approving a `guest_asset_package`
stamped `assets_sent_at` but **never emailed the guest** — the user had to copy-paste and send
from their own inbox. JP asked to (a) actually wire the send, (b) make it fifth-grader simple,
and (c) add a review→approve→send checks-and-balances so nothing goes out unseen.

**What shipped:**

**`pipeline/gmail_send.py` (new)** — Gmail send-as via OAuth, mirrors `pipeline/drive.py`.
Reuses the SAME Google OAuth client (`data/youtube_client_secrets.json`); token at
`data/gmail_token.json`, auto-refreshed. Scopes: `gmail.send` + `openid` + `userinfo.email`.
Helpers: `is_authorized`, `is_configured`, `get_auth_url`, `exchange_code`, `account_email`,
`send_message(to, subject, body_text, from_name)` (MIME + base64url → `users().messages().send`).

**Routes (`main.py`):**
- `GET /api/gmail/status` → `{configured, authorized, email, auth_url}`
- `GET /api/gmail/auth` → Google consent redirect; `GET /api/gmail/callback` → store token, success page
- `POST /api/gmail/disconnect` → delete token
- `POST /api/brick/actions/{id}/approve-send` — the **only** path that emails a guest. Accepts
  `{email_body?, send?}`. `send=false` marks sent without emailing. `send=true` + Gmail not
  connected → `409 {needs_gmail, auth_url}` (nothing sent/marked). Splits the `Subject:` line off
  the top, sends the body via `gmail_send.send_message` (run_in_executor), persists an edited body
  back to the action payload, then calls `BrickAgent.approve_action` (stamps `assets_sent_at`).
  `_dispatch_action` was left UNCHANGED — the plain `/approve` stays a pure "mark sent manually."

**Frontend (`frontend/walkthrough.html`):**
- `loadGmailStatus()` fetched before the punch list renders → drives button state.
- guest_asset_package card now shows: a Gmail-connection line, the full email in an **editable
  `<textarea>`** (review + tweak before sending), and three buttons:
  **✅ Approve & Send Email** (confirm dialog → `approve-send {send:true}`; if not connected, the
  button reads "🔗 Connect Gmail to Send" and opens `/api/gmail/auth`), **Mark sent manually**
  (`approve-send {send:false}`, confirm), and **✗ Reject**. Copy button copies the edited text.
- `doApproveSend()` handles the 409→connect-Gmail flow and disables all buttons mid-send.

**Checks-and-balances:** review (read/edit the email) → Approve & Send (explicit confirm naming the
recipient + sender) → only then does it email. Nothing auto-sends; Brick never emails on its own.

**Verified (server on :8765):**
- `/api/gmail/status` → `authorized:false`; `/api/gmail/auth` → 307 to Google consent with correct
  client_id + `redirect_uri=…/api/gmail/callback` + `gmail.send` scope.
- Served `/walkthrough` contains all new markup/functions (loadGmailStatus, btn-approve-send,
  doApproveSend, editable textarea, Mark sent manually).
- `/api/brick/walkthrough` still returns Neal's pending item (recipient ashley@grocapitus.com,
  7 assets, email present). Deduped stale duplicate punch items → 1 pending.
- **Live send requires JP's one-time Gmail consent** (one click at /api/gmail/auth) — can't be
  completed headless. Until connected, the button routes to connect; after, Approve & Send emails.

**One-time setup for JP:** open `http://localhost:8765/api/gmail/auth`, grant send access. (If Google
returns `redirect_uri_mismatch`, add `http://localhost:8765/api/gmail/callback` to the OAuth client's
Authorized redirect URIs — same client as Drive/YouTube.)

**Files:** `pipeline/gmail_send.py`, `main.py`, `frontend/walkthrough.html`, `docs/API.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-19 — YouTube episodes now publish PUBLIC by default (+ Neal EP.101 shipped via Gmail)

**What it was:** `_distribute_project` uploaded the main episode video to YouTube as
`privacy_status="private"` (a deliberate review gate). JP wants episodes public on closing.

**Fix (`main.py`):** main upload privacy is now `os.getenv("PODCLICK_YOUTUBE_PRIVACY", "public")`
— defaults to **public**, override per-install with `PODCLICK_YOUTUBE_PRIVACY=private|unlisted|public`.
Shorts (B3b) stay `private` (promotional clips JP reviews). B3 header comment + API.md note updated.

**Also this session:** Phase 6 Gmail send-as went live end-to-end. Connected as
`james.fluellen@gmail.com` (consumer account — Workspace `jp@titanreteam.com` blocked the restricted
`gmail.send` scope at domain policy; no Success Agent domain email exists yet). Test send to JP's inbox
confirmed (msg `19ee72b24da71486`), then **Neal Bawa's EP.101 package sent for real** via
`POST /api/brick/actions/{id}/approve-send` → `to: ashley@grocapitus.com`, `sent: true`,
`assets_sent_at` stamped. Full loop verified: poster → package → Drive → Foundation email → one-button send.

**Note:** Gmail consent screen is in Testing mode → refresh token expires ~7 days; reconnect at
`/api/gmail/auth` when it lapses (same pattern as YouTube).

**Files:** `main.py`, `docs/API.md`, `docs/BUGS_AND_FIXES.md` (+ `~/.claude/skills/PodClick/SKILL.md` updated)

---

## 2026-06-19 — Episode audio shipped quiet/suppressed (no loudness normalization)

**Symptom (JP):** "The remastering of the audio being pulled from the video sounds very suppressed and quiet."

**Root cause:** `_ship_it_extract_audio()` extracted the video's audio track with a plain
copy (`-q:a 2`, no gain). Camera-mic / Continuity-Camera audio at conversational distance
runs quiet — **measured -24.4 LUFS** on a real episode vs. the **-16 LUFS** podcast standard
(~8 dB low). Nothing in the Ship It chain normalized loudness, so episodes shipped suppressed.

**Fix (`main.py`, `_ship_it_extract_audio`):** Added **two-pass EBU R128 `loudnorm`**
(measure → correct) targeting **-16 LUFS, -1.5 dBTP, LRA 11**, output at 48 kHz.
Two-pass (not single-pass) because single-pass only estimates and undershot ~2 dB in testing.
Falls back to single-pass if the measurement JSON can't be parsed. Target overridable via
`PODCLICK_LOUDNORM_I`.

**Verified:** Ran the real `_ship_it_extract_audio()` on a -24.4 LUFS source →
output measured **-16.9 LUFS** (single-pass had only reached -17.9; two-pass lands on target).
On a full-quality video source it sits right at -16.

**Scope:** Applies to all FUTURE Ship It runs. Already-published episodes (e.g. Neal EP.101)
keep their original audio unless re-shipped — offer a re-master (re-extract + re-assemble +
re-upload) if JP wants a back-catalog pass.

**Files:** `main.py`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — RE Daily Brief generator (Foundation-voiced daily RE-industry podcast script)

**What shipped:** A one-button generator for a daily 15–30 min "RE Daily Brief" — a real
estate industry briefing episode, scripted teleprompter-ready in JP's Foundation voice, wired
into the 30-Day Content Board, designed to film same-day.

**Backend (`main.py`):** `POST /api/studio/re-daily-brief` — pulls `get_brand_context`
(task_type=`podcast_script_outline`) for voice, builds a voice preamble from real samples,
generates via claude-sonnet-4-5 (max_tokens 8000, run_in_executor so the event loop never
blocks). Prompt = cold-open hook → 3-4 industry segments (rates/inventory/policy/tech, why it
matters, so-what) → one tactical takeaway → CTA; first person; **no fabricated stats/sources**
(trends only); banned corporate words excluded. Returns title/hook/script + word_count +
est_minutes (~145 wpm) + foundation usage. `add_to_board=true` creates a draft `Post`
(bucket='podcast', source='manual', today 09:00) so it lands on the board.

**Frontend (`frontend/calendar.html`):** "🎙️ RE Daily Brief" button in the board header →
modal with optional angle, length (15/20/25/30), and "Add to Content Board" toggle. Result
shows in an **editable** textarea + meta line, with **🎬 Film This Now** (hands the edited
script to the Studio teleprompter via localStorage → opens `/studio`), 📋 Copy, and ↻ Regenerate.

**Verified live (server :8765):**
- Generate (topic="falling mortgage rates…", 15 min) → `ok:true`, title "Falling Rates and
  Market Psychology", 1592 words / ~11 min, **used_foundation:true (198 samples)**, hook in JP's
  voice ("…your buyers still aren't calling you back. Let's talk about why."), opens "I'm JP Fluellen."
- add_to_board=true → `post_id` created; `GET /api/calendar` shows 1 `bucket=podcast` post dated today.
- calendar.html serves with openBriefModal/runDailyBrief/filmBriefNow/brief-script present.

**Note:** 15-min target undershot slightly (model wrote ~1600 words vs ~2175 target) — pick 20–30
min for longer. Tunable later by firming the length instruction.

**Files:** `main.py`, `frontend/calendar.html`, `docs/API.md`, `docs/FRONTEND.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — Smart cleanup: auto-remove stutters / false starts / dead air (Descript-style "remove retakes")

**What shipped (B):** Layered disfluency removal on top of the existing filler-word cut, so
Ship It now cleans fumbles automatically — not just "um/uh."

**`pipeline/audio.py` — `detect_disfluency_regions(duration, words)`** (deterministic, no LLM):
- **Stutters:** a function word repeated back-to-back (`the the`, `I-I`) → cut the earlier copy.
  Restricted to a `_STUTTER_WORDS` set (articles/pronouns/conjunctions) so rhetorical repeats
  like "no no no" / "very very" are NEVER cut.
- **False starts / restarts:** a 2–5 word phrase immediately re-spoken within ≤1.6 s
  ("I think we should— I think we should buy") → cut the FIRST instance, keep the clean take.
  Longest-window-first so the biggest restart wins; claimed words can't be double-cut.
- **Dead air:** inter-word silence > `PODCLICK_DEADAIR_SEC` (default 1.2 s) trimmed to a natural
  ~0.35 s pause.
- Gated by `PODCLICK_CLEANUP_DISFLUENCY` (default on); dead-air sub-gated by `PODCLICK_TRIM_DEADAIR`.

**Integration:** `build_keep_segments()` now appends these regions to the filler regions before
the merge/invert — so cleanup rides the **same cut + crossfade machinery as fillers** and applies
everywhere fillers already do (`process_audio`, `pipeline/assemble.py`). Zero call-site changes.

**Verified (synthetic transcript):** "the the market" → one "the" cut; "I think we should— I think
we should buy" → first take cut, "buy" take kept; **"no no no" fully preserved**; a 2.5 s gap →
trimmed, both surrounding words kept. `audio.py` parses; server restarted.

**Scope/limit:** deterministic — catches verbatim repeats, stutters, and dead air (the bulk of
real disfluency). Semantic fumbles with *different* wording ("the real— the actual problem") are
NOT caught; that needs an LLM transcript pass (easy follow-on if wanted). Conservative by design:
when unsure, it keeps the audio.

**Files:** `pipeline/audio.py`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — In-studio camera switcher (pick/switch iPhone WHILE in the studio)

**Ask (JP):** "Flawlessly add my phone as the camera and choose the phone while in the studio, not
just before." The Device Check (pre-studio) already surfaced the iPhone, but once inside the studio
there was no way to connect/select it — you had to leave and re-enter.

**What shipped (`frontend/studio.html`):**
- Transport bar now has a **camera dropdown** (`#studio-cam-select`) + **🔄 re-scan** (`#btn-cam-rescan`)
  beside Start Camera.
- `populateStudioCams()` lists video inputs (📱 for iPhone/Continuity, 📷 otherwise), marks the live
  device; called at the end of `startCamera()` and `dcEnterStudio()` so the picker is ready on entry.
- `rescanStudioCams()` re-requests permission (surfaces labels for a freshly-connected iPhone),
  re-enumerates, and toasts Continuity tips if no phone yet.
- `switchStudioCamera(deviceId)` live-swaps the feed without leaving: `getUserMedia` on the chosen
  device (1080p with native-resolution fallback for phone cams that reject exact constraints), stops
  the old tracks, repoints `els.cam.srcObject` + audio analyser + canvas composite. Since the
  recorder records the **canvas composite** (which draws `els.cam`), the recorded video follows the
  new camera automatically. **Guarded during active recording** (toast: "Stop the recording first")
  to avoid an audio-track mismatch mid-take.

**Verified:** served `/studio` contains the markup + all three functions + listeners; full inline
script passes `node --check` (no syntax break). Live iPhone selection requires JP's physical phone +
Continuity (can't headless-test the device), but the switch path is wired end-to-end.

**Files:** `frontend/studio.html`, `docs/FRONTEND.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — Studio refresh keeps the script + topic cued (persistent draft)

**Ask (JP):** "A refresh will keep the current data cued, i.e. scripts etc."

**Was:** `checkInboundScript()` did a ONE-SHOT hand-off — read `podclick_teleprompter_script`
from localStorage, populated the prompter, then **deleted** the keys. A page refresh lost the
script (and topic fields were never persisted at all).

**Fix (`frontend/studio.html`):** Persistent studio draft in localStorage (`podclick_studio_draft`).
- `_persistStudio()` saves `{script, podcastName, topic-title/pillar/market/notes}`; fired on every
  `input` to those fields, and after `generateScript()` + `loadTodayTopic()` programmatic fills.
- `_restoreStudio()` repopulates empty fields from the saved draft.
- `checkInboundScript()` rewritten: a fresh hand-off (Click Studio / RE Daily Brief) still WINS and
  becomes the persisted draft; with no hand-off it restores the draft so a refresh keeps everything
  cued ("Picked up where you left off — script still cued.").
- `clearStudioDraft()` fires once a recording is saved into a Project (saveAndContinue +
  `_doPodcastPublishRaw`) so a finished script doesn't re-cue next session.

**Verified:** served `/studio` contains `_persistStudio`/`_restoreStudio`/`clearStudioDraft` +
the input-listener wiring; full inline script passes `node --check`.

**Files:** `frontend/studio.html`, `docs/FRONTEND.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — No Ship It button on recording_done projects (couldn't trigger the pipeline)

**Symptom (JP):** On a `recording_done` project (Ship It wizard), Step 2 Audio reads
"No assembled audio yet — run Ship It to build it" — but there was no button anywhere on
the screen to actually run Ship It.

**Root cause:** The ONLY caller of `shipIt()` in `frontend/project.html` was `rerunShipIt()`,
bound to `#rerun-ship-btn`, which lives inside the `#failed-banner` (shown only on
`status==='failed'`). Step 1's "Next →" just called `navigateToStep(2)`; Step 2's "Next →"
navigated on. So a fresh recording could never enter the pipeline from the UI — the wizard
told the user to "run Ship It" with no trigger. (Backend `POST /api/projects/{id}/ship-it`
was fine; purely a missing frontend control.)

**Fix (`frontend/project.html`):**
- `showStep1()` — when the project has no assembled audio (`!mp3_url` and no
  `audio_assembly.final_url`), the primary button relabels to **"🚀 Ship It"** (falls back to
  "Next →" once audio exists). Tooltip explains it cleans fillers/stutters/dead air + normalizes
  loudness. Still gated on a transcript being present (disabled until Whisper finishes).
- `#step1-next-btn` click handler is now conditional: audio built → `navigateToStep(2)`;
  no audio → `startShipIt()`.
- New `startShipIt()` — saves any dirty transcript first, disables the button ("Shipping…"),
  POSTs ship-it, reloads, and `applyProjectState()` flips to the processing panel + auto-poll.
  On failure re-enables the button.

**Verified:** inline JS passes `node --check`; served `/project` carries `startShipIt` + the
"🚀 Ship It" label (4 matches). Live pipeline run requires JP clicking it on a real recording.

**Files:** `frontend/project.html`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — Solo videos rendered as duplicated split-screen (single-speaker not recognized)

**Symptom (JP):** Clips from a one-person episode (RE Daily Brief) rendered as a stacked
split screen — the same single speaker shown twice (top pane + bottom pane).

**Root cause:** `_run_ship_it_async` hardcoded `crop_mode="stack"` for every project.
`stack`→`split` cuts the landscape frame in half and stacks the halves (correct only for a
two-pane interview recording). `detect_layout` only measures geometry (aspect ratio →
side_by_side/stacked); it never counts speakers, so a solo recording got the interview
treatment and duplicated the speaker.

**Fix (`main.py`, `_run_ship_it_inner`):** Auto-pick the layout before rendering —
`_crop_mode = "stack" if project.guest_ids else "center"`. Solo episodes (no linked guest)
now render a single full-frame vertical crop (no split); interviews (a guest is linked) keep
the host-top / guest-bottom stack. Guest linkage is the reliable single-speaker proxy for
PodClick's model (solo podcast / RE Daily Brief = no guest; remote interview = guest linked).
No new vision deps (cv2 not installed). Per-clip override still available in the Step 3 editor
(Crop Mode pills + "↺ Re-render with this crop").

**Existing clips:** already-rendered clips keep their old crop until re-rendered — re-run Ship It
(now auto-center for solo) or use the ◻ Center pill → ↺ Re-render per clip.

**Verified:** RE Daily Brief project (c5ce978a, 0 guests) → classified solo → center. main.py
parses; server restarted so the change is live (was running without --reload).

**Files:** `main.py`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-23 — Clips now burn viral captions (word-by-word highlight) instead of ugly SRT

**Symptom (JP):** Short/clip captions were plain small white SRT subtitles (Arial 14, bottom).
The viral TikTok-style captions we'd built were never used.

**Root cause:** A full viral caption generator already existed — `pipeline/subtitles.py`
`generate_ass_subtitles` (big bold uppercase, 2-3 word groups, each word highlighted yellow as
spoken, black outline) — but the clip render path only ever generated an SRT and burned it with
`_CAPTION_STYLE`. The ASS system was built and orphaned.

**Fix (single FFmpeg pass, env-gated):**
- `pipeline/project_pipeline.py`:
  - New `generate_ass_for_clip(words, start, end, out)` — rebases the clip's words to 0 and
    delegates to `subtitles.generate_ass_subtitles` → viral .ass file.
  - `render_all_clips` generates the .ass per clip (default ON; `PODCLICK_VIRAL_CAPTIONS=0`
    falls back to plain SRT) and passes `ass_path` to both render paths.
  - `render_vertical_clip` and `render_vertical_clip_from_video` accept `ass_path`; when set,
    the FFmpeg `-vf` burns `ass='…'` instead of `subtitles=…:force_style=…`.
- `~/.claude/skills/vertical-clip-render/render_clip.py`: `render_clip` + `_build_filter` accept
  `ass_path`; when present, `cap = ass='…'` (libass renders the embedded viral styles). Uses
  `Path(...).exists()` (no new import).

**Verified (real render):** generated viral ASS for an 8s window of the RE Daily Brief webm,
rendered with `crop_mode=center` + `ass_path` → 1080×1920 h264 MP4; extracted frame shows a
single full-frame speaker (no split) with big bold uppercase **yellow** captions ("BUYERS AGAIN
THE"). Both the split-screen fix and viral captions confirmed in one frame.

**To apply to existing clips:** re-run Ship It (regenerates all clips with center crop + viral
captions) or re-render per clip in the Step 3 editor.

**Still open (told JP):** the filler/disfluency auto-cleanup runs on the MAIN episode audio but
NOT inside the rendered clips — the clip is cut straight from the source video window, so um's/
restarts within a clip remain. Applying the cleanup to clips means cutting the filler sub-regions
out of the clip video + re-timing the captions (multi-segment cut/concat) — a larger follow-up.

**Files:** `pipeline/project_pipeline.py`, `~/.claude/skills/vertical-clip-render/render_clip.py`,
`docs/BUGS_AND_FIXES.md`

---

## 2026-06-24 — Cloud-readiness: vendored render_clip (removed ~/.claude dependency)

**Why (JP):** PodClick is headed for SaaS / cloud-server deploy. The clip renderer hard-imported
`render_clip.py` from `~/.claude/skills/vertical-clip-render` via `Path.home()` — that path does
not exist on a cloud box, so clip rendering (and the new viral captions) would break on deploy.

**Fix:**
- Vendored the renderer into the repo as `pipeline/render_clip.py` (byte-for-byte logic incl. the
  `ass_path` viral-caption support).
- `_get_render_clip()` now does `from pipeline import render_clip` first; the `~/.claude` skill path
  is a fallback only (local dev parity). No `Path.home()` in the primary path.

**Verified:** `_get_render_clip().__file__` → `pipeline/render_clip.py`; `render_clip` accepts
`ass_path`; real 4s center-crop render through the vendored path produced a 1080×1920 MP4.

**Other home-dir reference (not a blocker):** `pipeline/transcribe.py` HF cache dir
`Path.home()/.cache/huggingface/hub` — only used by the LOCAL whisper path; the cloud Ship It path
uses the OpenAI Whisper API. On a cloud box `Path.home()` resolves to the server's home and the
cache dir is created on demand, so it degrades fine. Flag for the deploy checklist, not a fix.

**Files:** `pipeline/render_clip.py` (new), `pipeline/project_pipeline.py`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-24 — Per-clip Re-render didn't burn viral captions (only the Ship It pipeline did)

**Symptom (JP):** Re-rendering a clip with the Step 3 "↺ Re-render with this crop" button fixed the
crop (e.g. Center) but the clip still had the old plain SRT captions — only a full Ship It run
produced the viral word-highlight captions. Felt impossible to get a clip publish-ready one at a time.

**Root cause:** `rerender_clip` (`POST /api/projects/{id}/clips/{clip_id}/rerender`) called
`render_vertical_clip_from_video(...)` with **no `ass_path`**, so it always fell back to the plain
`_CAPTION_STYLE` SRT. The viral-ASS generation was only wired into `render_all_clips` (the Ship It
path), never the per-clip re-render endpoint.

**Fix (`main.py`, `rerender_clip`):** Generate the viral ASS the same way the pipeline does —
`generate_ass_for_clip(stored_words, start, end, <mp4>.ass)` (gated by `PODCLICK_VIRAL_CAPTIONS`,
needs `whisper_words` in legacy_metadata) — and pass `ass_path` to both
`render_vertical_clip_from_video` and the audio-only fallback. Now a per-clip re-render produces the
SAME burned viral captions + chosen crop as a full Ship It run.

**Verified:** main.py parses; server restarted. (Live re-render is JP-driven from the Step 3 editor.)

**Files:** `main.py`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-26 — Descript-style transcript editor (full edit on the Job Site + live preview)

**Ask (JP):** "RE Daily Brief get a full edit? How do I see this happening on the jobsite and
preview it? I don't see the Descript clone architecture anywhere." — chose **Build the full
Descript editor**.

**Was:** `/editor/{id}` (editor.html) was a Phase-0 stub — word-delete styling only, transcription
not wired, export disabled, reachable only from the screen recorder (not the Job Site). Projects
already store word-level timestamps (`legacy_metadata.whisper_words`) but nothing exposed them for
editing. The only "edit" was the invisible Ship It auto-cleanup.

**What shipped — a real transcript editor wired into the project flow:**

**Backend (`main.py`):**
- `GET /api/projects/{id}/source-video` — streams the project recording (FileResponse).
- `GET /api/projects/{id}/edit-data` — returns `{title, words[], duration, has_video, video_url, edited}`
  from stored `whisper_words` (prefers `edited_words` if a cut already exists).
- `POST /api/projects/{id}/apply-edit` `{cut_ranges:[[s,e]]}` — inverts cuts → keep segments
  (`_edit_invert_cuts`), frame-accurate ffmpeg trim+concat re-encode (`_apply_edit_sync`) →
  `data/recordings/{id}.edited.mp4`, remaps word timestamps left by removed time
  (`_edit_remap_words`), stores `edited_video_path` + `edited_words` + `manual_cut_regions` in
  legacy_metadata (flag_modified). Returns removed_seconds / new_duration / words_remaining.
- `GET /project/{id}/edit` — serves the editor page.
- **Ship It now prefers the edited cut:** `ship_it()` swaps `recording_path`→`edited_video_path`
  and `stored_words`→`edited_words` when present (forces audio re-extract). So the episode + clips
  build from the edited take automatically.

**Frontend (`frontend/project-editor.html`, new):** two-pane editor — sticky video (left) +
clickable word transcript (right). Click a word to cut it (strikethrough), shift-click to cut a
range, Reset, live word highlight on the play head, and **live skip-preview** (playback jumps over
cut words instantly, no render — toggle 👁). Stats show words / marked-cut / time-removed. **✂️ Apply
Edit** posts the cut ranges → re-cuts the video → returns to the project to re-run Ship It.
**Entry point:** "✂️ Edit Video" button on project.html Step 1 → `/project/{id}/edit`.

**Verified live (RE Daily Brief c5ce978a):** `/project/{id}/edit` 200; edit-data → 2391 words /
839s / video_url; source-video streams `video/mp4`. Cut/remap unit test: cutting words at 2–4s
shifts the next word from 4.0s→2.4s (exactly the 1.6s removed). Real ffmpeg cut+concat: keep
0–4s+10–13s → 6.98s valid h264 1080×1920 MP4.

**Scope/limit (v1):** Apply re-encodes (frame-accurate, fast preset crf 20) — fine for episode
lengths; could get slow on very long takes with hundreds of cuts. Cuts are word-granular; tiny
inter-word silences between separate cut words aren't removed unless those gaps are themselves
selected. No undo of an applied cut except re-editing from the (now edited) take — original webm is
preserved on disk, so a fresh Ship It from raw could be re-pointed if ever needed.

**Files:** `main.py`, `frontend/project-editor.html` (new), `frontend/project.html`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-28 — "Line it up" threw "failed" on an already-scheduled project

**Symptom (JP):** Clicking **Line it up** on EP 102 (already `scheduled`, green "CLOSED CLEAN"
banner showing) returned "failed." Nothing was actually wrong — the episode was already lined up.

**Root cause:** `schedule_closing` (`POST /api/projects/{id}/schedule-closing`) only accepts a
project in `review` status (it fires uploads + post creation + episode-number assignment, which
must not run twice). Re-clicking on a `scheduled` project → 400 "Project must be in 'review'…".
The Step 4 form stayed fully interactive after scheduling, and the generic catch surfaced the 400
as a scary "Closing failed."

**Fix (`frontend/project.html`):**
- `showStep4()` now locks the button when `status ∈ {scheduled, closing, closed}`: disabled +
  relabeled **"✓ Already lined up"** + explanatory title. Re-clicking is impossible.
- `lineItUp()`: removed the `finally` that re-enabled/relabeled the button on success (it ran
  AFTER `applyProjectState()` and clobbered the lock). Button is only re-enabled in the catch
  (failure). The catch now detects the `review` 400 and shows a friendly info toast
  ("Already lined up — this build is scheduled. Nothing more to do here.") instead of "failed."

**Note:** Re-scheduling (change date/channels after the fact) is intentionally NOT enabled yet —
it would need an idempotent re-schedule path (avoid double Buzzsprout/YouTube uploads + episode
number re-increment). Flagged for later if JP wants to edit a scheduled closing.

**Verified:** project.html JS passes `node --check`; server restarted. EP 102 stays scheduled
(closing 2026-06-28T13:00:00Z); the button now shows locked instead of erroring.

**Files:** `frontend/project.html`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-28 — Hybrid editing: auto first pass + manual 2nd pass (composing takes)

**Ask (JP):** "I like a hybrid — first-pass auto edit on long-form video, 2nd pass manual in case
something was missed."

**Was:** Two disconnected edits. (1) Ship It's auto-cleanup cut fillers/stutters/dead air from the
assembled *audio* only. (2) The manual transcript editor cut the *raw* video. They didn't compose —
opening the editor always showed the raw take, so you couldn't hand-refine the auto-cleaned result.

**What shipped — one composing take:**
- **`POST /api/projects/{id}/auto-edit`** (first pass) — runs the SAME detector the pipeline uses
  (`pipeline.audio.build_keep_segments` → filler words + `detect_disfluency_regions`) against the
  current take's word timestamps, cuts the *video* to the keep-segments via the editor's cut+concat
  engine, remaps word timestamps, and stores `edited_video_path` + `edited_words` +
  `manual_cut_regions` — exactly the format a manual edit produces.
- **Composition:** `source-video`, `edit-data`, and `apply-edit` now all operate on the CURRENT
  take (the edited cut if one exists, else raw). So: Auto-edit → editor reloads showing the cleaned
  video + remapped transcript → manual 2nd pass cuts on top → Apply. Ship It already prefers
  `edited_video_path`, so the episode + clips build from the final edited take.
- **Safe re-cut:** new `_render_take_edit()` renders to a temp file then `os.replace()` — correct
  even on the 2nd pass when src == the edited output (ffmpeg can't read+write the same path).
- New `_keeps_to_cuts()` inverts keep-segments → removed regions for the word remap.
- **Editor UI (`project-editor.html`):** purple **⚡ Auto-edit — First Pass** button (POSTs auto-edit,
  reloads the clean take); the manual button relabeled **✂️ Apply Edit (2nd pass)**.

**Verified (RE Daily Brief c5ce978a, 2391 words / 839s):** dry-run of the detector → 11 keep
segments, **10 auto-cuts, ~15.4s removed**. main.py parses; editor JS passes `node --check`; server
restarted. (Did not run the full re-encode on EP 102 — it's already scheduled; render+remap path was
verified earlier with a real cut+concat producing a valid 1080p MP4 + unit-tested remap.)

**Flow for JP:** project → ✂️ Edit Video → **⚡ Auto-edit (1st pass)** → review the cleaned take →
click any missed words to cut (**2nd pass**) → **Apply Edit** → re-run Ship It.

**Files:** `main.py`, `frontend/project-editor.html`, `docs/BUGS_AND_FIXES.md`

---

## 2026-06-28 — Re-schedule a closing (Line it up no longer errors on scheduled builds)

**Symptom (JP):** Repeated "Closing failed." Clicking **Line it up** on EP 102 (already
`scheduled`) returned 400 "Project must be in 'review'." The earlier button-lock (✓ Already lined
up) didn't help — JP wants to actually change date/channels, and the message made him look for a
nonexistent "review button."

**Fix — make scheduling idempotent + re-schedulable (`main.py` + `frontend/project.html`):**
- `schedule_closing` now accepts `review`, `scheduled`, and `closing` (was `review` only).
  `_is_reschedule = status in (scheduled, closing)`; `_already_distributed = bool(buzzsprout_url or
  youtube_url)`. Episode number is still only assigned `if not project.episode_number` (no re-bump).
- `_create_closing_posts(..., replace=_is_reschedule)` — on a re-schedule it first deletes the
  project's prior `scheduled/draft/failed` closing posts (variants cascade) so changing date/channels
  doesn't pile up duplicates. Published posts are left alone.
- `_distribute_project` is SKIPPED when `_is_reschedule and _already_distributed` — re-running would
  create duplicate Buzzsprout/YouTube uploads. The new date still moves the social posts; the
  podcast/YouTube go-public flip keeps its first scheduled time (documented tradeoff).
- Reschedule-aware response message ("Closing moved to … — won't re-upload …").
- `showStep4()`: scheduled/closing → button enabled, relabeled **↻ Update closing**; `closed` →
  disabled **✓ Closed**; `review` → **Line it up**. (Was: locked on all three.)

**Verified live:** POST schedule-closing on EP 102 (status scheduled) with an added `youtube`
channel → **200** (was 400), status stays scheduled, posts replaced. EP 102 had no prior
`buzzsprout_url` (original distribution never completed), so this run did the FIRST Buzzsprout upload
— now `buzzsprout_url` is set, so subsequent re-schedules will correctly skip re-distribution.

**Note:** the verification POST kicked EP 102's Buzzsprout upload (private draft; goes public at
closing anyway). No duplicate — it had never uploaded before.

**Files:** `main.py` (`schedule_closing`, `_create_closing_posts`), `frontend/project.html`,
`docs/BUGS_AND_FIXES.md`

---

## 2026-06-28 — Project auto-named from transcript at transcription time (not just Ship It)

**Ask (JP):** "Build this in so that a project name is generated based off of the transcript."

**Was:** `_generate_episode_title` (GPT-4o, transcript→clean title) + `_looks_like_auto_title`
existed and were wired into Ship It only. So a freshly recorded/uploaded project kept its filename
/ "New build — {date}" placeholder on the Job Site until Ship It ran (potentially much later).

**Fix (`main.py`, `_run_transcription` persist block):** Right after the transcript + word
timestamps persist (transcription done), if the current title is a placeholder
(`_looks_like_auto_title`) and there's transcript text, generate a clean name from the transcript
via `_generate_episode_title` (run_in_executor, non-blocking) and save it in the same commit.
Gated so a hand-set name or an RE-Daily-Brief title is never overwritten. Non-fatal on failure
(keeps the placeholder). Ship It's later auto-title still runs as a second pass (idempotent — the
name is no longer a placeholder, so it's left alone).

**Verified (server :8765):** `_looks_like_auto_title` → True for "New build — 2026-06-03 16:02",
"Neal  Bawa— 2026-06-03 17:35", "Untitled"; False for a hand-set name. `_generate_episode_title`
on the real RE Daily Brief transcript → "Unlocking Market Movement: The Shift Beyond Mortgage
Rates". main.py parses; server 200.

**Result:** A recording/upload now shows a real, transcript-derived name on the Job Site as soon as
transcription finishes — before Ship It.

**Files:** `main.py` (`_run_transcription`), `docs/BUGS_AND_FIXES.md`

---

## 2026-07-03 — Edited-cut projects shipped 0 clips (Ship It re-transcribed 36MB edited audio → Whisper 413)

**Symptom (JP):** Chris Gavre interview reached `review` (Ship It "finished") but the Clips step
showed **0 clips** ("No clips yet — they appear once Ship It finishes"). No assembled MP3 either.

**Root cause:** The project had been run through the transcript editor (81 manual cuts →
`edited_video_path` + `edited_words: 8489`, but `whisper_words: 0` on the raw take). When Ship It
prefers the edited cut it sets `stored_audio_path = ""` to force a fresh audio extract from the
edited video — which also disabled the step-2.5 **fast path** (needs `stored_audio_path AND
stored_words`). So it fell through to the **re-transcribe fallback**, which extracts the edited
video's audio (a full-length interview ≈ **36 MB**) and calls Whisper — **over Whisper's 25 MB
cap → 413 → zero words → zero clips** (clip detection needs word timestamps). The 8489 correct,
already-remapped `edited_words` were thrown away.

**Fix (`main.py`, `_run_ship_it_async` step 2.5):** Added **Path B** between the fast path and the
re-transcribe fallback — when `stored_words` is present but there's no stored audio (the edited-cut
case), extract audio from the (edited) recording and **REUSE the stored words**; do NOT call Whisper.
The words are already correct and aligned to that exact audio. The full re-transcribe fallback stays
as Path C for genuinely word-less projects.

**Verified live (Chris Gavre `c1b0c77e`, edited cut, 8489 words):** re-ran Ship It →
`[ship_it.2.5] Edited cut: … REUSING 8489 stored words (no re-transcribe)` → assembled MP3 built
(two-pass loudnorm) → **5 clips rendered** (clip_01–05 .mp4 + viral .ass + .srt, real hook text) →
status `review`, `mp3_url` set, `show_notes` set. (Was: 0 clips, no mp3.)

**Note:** segments aren't remapped for edited cuts (`whisper_segments: 0`), so an edited episode
won't get YouTube chapter markers — acceptable; clips + assembled audio are the priority. Chapters
on edited cuts are a later follow-on (remap segments alongside words in the editor).

**Files:** `main.py` (`_run_ship_it_async` step 2.5), `docs/BUGS_AND_FIXES.md`

---

## 2026-07-03 — Crop Mode simplified to Stack (default) + Center only; Chris Gavre clips re-rendered stack

**Ask (JP):** "This needs a top/bottom stack as a default — not any of these [Host/Guest]. So
center or top/bottom and center. No other options. Please re-render."

**Was:** The Step 3 Crop Mode picker (`frontend/project.html`) had four pills —
Stack / Host (`left`) / Guest (`right`) / Center. The Host/Guest half-crops are interview-specific
and confusing.

**Fix (`frontend/project.html`):** Removed the Host (`left`) and Guest (`right`) pills. Picker is now
two options: **⬛⬛ Stack** (top/bottom split, the active default) and **◻ Center** (single speaker).
Backend still accepts stack|left|right|center — only the UI is trimmed, so no backend change and old
values never break.

**Re-render:** Re-rendered all 5 Chris Gavre clips (`c1b0c77e`) with `crop_mode=stack` via
`POST /api/projects/{id}/clips/{clipId}/rerender` — all HTTP 200. Verified clip_01 frame: proper
top/bottom split (guest top / host bottom, two distinct people), 1080×1920, fresh mtime, viral
captions preserved.

**Files:** `frontend/project.html`, `docs/BUGS_AND_FIXES.md`

---

## 2026-07-03 — Send guest assets straight from Step 4 (Line it up → capture email → build → send)

**Ask (JP):** "How do I send the assets? When I click Line It Up it should ask me for the email and
all the things I need to send the Google Drive with all the assets."

**Root gap:** The guest asset package (Drive folder + poster + Foundation email + Gmail send) was
fully built and battle-tested (Neal EP.101 shipped for real) — but it only triggered for a **linked
guest** and could only be reviewed/sent on the **Walk-through** Punch List. The Chris Gavre project
had `guest_ids: []` and no guest record, so nothing ever built — and JP had no in-project way to
enter the recipient and send.

**What shipped — an inline "Send guest assets" flow in project Step 4 (reuses the existing machinery):**

**Backend (`main.py`):**
- `POST /api/projects/{id}/guest-assets/build` `{name, email}` — validates the email, upserts a guest
  in `guests.json` (match by email, else create id=uuid4()[:8], status='recorded'), links them to
  `project.guest_ids` (flag_modified, idempotent), then fires the existing `_build_guest_asset_package`
  (Drive upload + poster + Foundation email → Punch List item). Fire-and-forget.
- `GET /api/projects/{id}/guest-assets` — returns the newest pending `guest_asset_package` BrickAction
  for this project `{ready, action:{id, recipient, email, drive_url, drive_configured, uploaded,
  skipped, assets}}` so the panel can show the drafted email + Drive link inline.
- Ordering fixed to `BrickAction.requested_at` (model has no `created_at`).

**Frontend (`frontend/project.html`, Step 4):** "📦 Send guest the assets" section — guest name +
email inputs (name prefilled from the linked guest), **📦 Build package** → POST build → poll
guest-assets every 4s (≤3 min) → reveals a review card: recipient, Drive folder link (N files),
the drafted email in an **editable textarea**, and **✅ Send Email** / **Mark sent manually** / 📋 Copy.
Send routes through the SAME `/api/brick/actions/{id}/approve-send` (Gmail send-as, 409→connect flow).
A Gmail-connected badge shows at the top. `showStep4()` auto-surfaces an already-built package.

**Checks-and-balances preserved:** nothing sends until JP reviews the email and clicks Send; the
underlying approve-send path is unchanged.

**Verified (server :8765):** build endpoint 400s on a bad email; `GET guest-assets` → `{ready:false}`
before a build; served `/project` carries the panel markup + all functions (buildGuestAssets,
_dispatchAssets, renderAssetsReview, asset-guest-email). main.py parses; inline JS passes `node --check`.
Did NOT fire a live 265MB Drive upload against Chris Gavre's project (would link a throwaway guest +
re-upload) — the package builder + Gmail send are already proven end-to-end (Neal EP.101).

**Note:** Gmail is currently connected as `james.fluellen@gmail.com`; Drive as `jp@titanreteam.com`.

**Files:** `main.py`, `frontend/project.html`, `docs/API.md`, `docs/FRONTEND.md`, `docs/BUGS_AND_FIXES.md`

---

## 2026-07-03 — Connect-Gmail modal (graceful reconnect, no UI freeze) on the Send-assets flow

**Ask (JP):** "Can you design a pop-up Connect Gmail when this happens so the system doesn't bog
down because of it?"

**Was:** When the Gmail token had lapsed (it expires ~weekly in Testing mode), the Step 4 Send
button just `window.open('/api/gmail/auth')` in a raw tab and threw "Connect Gmail, then send again."
— the user had to notice, reconnect, come back, and click Send a second time.

**What shipped (`frontend/project.html`):** A clean **#gmail-modal** that pops the moment a send is
attempted without a live Gmail token (either the pre-check `!_gmailReady` or a backend `409
needs_gmail`). It explains the ~weekly expiry, offers **🔗 Connect Gmail** / **Not now**, opens the
Google consent in a new tab, then **polls `/api/gmail/status` every 2s** (≤5 min) — the page never
freezes. On reconnect it auto-closes, toasts "Gmail reconnected — sending now," and **resumes the
exact send that was blocked** via a stored `_gmailOnConnected` callback. New JS: `openGmailModal`,
`closeGmailModal`, `_connectGmailFromModal`. `_dispatchAssets` now routes both the pre-check and the
409 through the modal instead of a bare redirect.

**Verified (server :8765):** inline JS passes `node --check`; served `/project` carries the modal
markup + `openGmailModal`/`_connectGmailFromModal`/`gmail-modal-waiting`. (Live reconnect-resume
needs JP's physical Google consent — can't complete headless.)

**Files:** `frontend/project.html`, `docs/BUGS_AND_FIXES.md`
