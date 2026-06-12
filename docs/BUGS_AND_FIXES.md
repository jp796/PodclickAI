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
