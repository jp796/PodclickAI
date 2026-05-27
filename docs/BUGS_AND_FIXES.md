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
