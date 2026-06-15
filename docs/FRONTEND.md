# PodClick Frontend Reference
> Last updated: 2026-05-26 | Update on new functions or state changes.

## Files
- `frontend/youtube-studio.html` — Click Studio / Market Scout (~2,805 lines, 66 JS functions)
- `frontend/studio.html` — Recording studio + teleprompter (~1,300 lines)
- `frontend/social-studio.html` — Social Studio (Post Forge, Calendar, Hashtag Lab, Repurpose Hub) (~1,360 lines)
- `frontend/brand-studio.html` — Brand Studio (~2,000 lines)
- `frontend/blueprint.html` — Blueprint auto-generation (Step 5 — Phase 1)
- `frontend/foundation.html` — Foundation intake (Step 1–4 — Phase 1)

---

## blueprint.html — Global State

| Variable | Purpose |
|----------|---------|
| `_draftData` | Raw response from `/api/blueprint/auto-generate` (null until built) |
| `_alreadyExisted` | `true` when a populated Blueprint was overwritten — drives overwrite warning |

---

## blueprint.html — JS Functions

| Function | Description |
|----------|-------------|
| `loadFoundationStatus()` | GET `/api/foundation/status`, update Foundation tier tile; disable build button if not_ready |
| `computeTier(n)` | Returns `{label, cls}` for sample count n. Tiers: not-ready/thin/solid/deep |
| `startBuild()` | POST `/api/blueprint/auto-generate`, show spinner, call `populateDraft()` on success |
| `populateDraft(d)` | Populate tone chips, cadence, POV, humor, vocabulary, audience, pain points, pillars, raw JSON |
| `confirmBlueprint()` | UX confirmation only — Blueprint already saved by service on auto-generate |
| `discardDraft()` | Hide draft panel, clear `_draftData`, re-enable build button |
| `bpToast(msg, kind)` | Show notification toast (info/success/error) |
| `escHtml(str)` | HTML-encode `& < > "` |

---

## youtube-studio.html — Global State

| Variable | Purpose |
|----------|---------|
| `activeJobId` | Current Market Scout job UUID being polled |
| `pollTimer` | setInterval handle for job polling (2000ms) |
| `currentScript` | Last generated script object (from `/api/yt/script`) |
| `currentTopic` | Topic string for current script session |
| `currentCity` | City string for current script session |
| `_lastFormulaData` | Last Script Lab result (hook/cta/outline/end/ideas) |
| `_scriptMode` | `'bullets'` or `'full'` — current Script Lab display mode |
| `window.allVideos` | Normalized video array for Top Performers grid |
| `window.spyCity` | City used in last Market Scout run |
| `window.videoSortKey` | `'views'` \| `'viral'` \| `'likes'` |
| `window.spyResult` | Raw result object from last completed spy job |
| `STEPS` | Array of 7 `{key, label}` — matches backend `step_statuses` keys exactly |
| `SAVED_MARKET_KEY` | localStorage key: `'podclick_saved_market'` |
| `repurposeOutputs` | Object `{shorts, instagram, tiktok, blog}` — toggles |
| `PILLAR_STYLES` | Map of pillar name → CSS color class |
| `SHOOT_DAYS` | `['Mon'...'Sun']` — days scheduler |

---

## youtube-studio.html — JS Functions

### Navigation & Tabs
| Function | Description |
|----------|-------------|
| `switchTab(name)` | Switch main panel: 'plan'\|'create'\|'grow'\|'board' |
| `switchResultTab(name)` | Switch result sub-tabs: 'viral'\|'standards'\|'searches'\|'channels' |
| `openPanel(panelId)` | Show a panel by ID, hide others |

### Market Scout (Competitor Spy)
| Function | Description |
|----------|-------------|
| `runCompetitorSpy()` | POST to `/api/yt/competitor-spy`, starts `startPolling()` |
| `startPolling(jobId)` | Sets `pollTimer`, calls `pollJob()` every 2000ms |
| `pollJob(jobId)` | Fetches job status, updates progress bar + step UI; on complete → 800ms delay → show report |
| `buildStepsUI(stepStatuses)` | Renders step rows with pending/running/completed/failed states |
| `showReport(result)` | Populates report panel quadrants (market_demand, best_format, etc.) |
| `renderResultTabs(result)` | Renders all result sub-tabs: viral, standards, searches, channels |
| `renderVideoGrid()` | Renders Top Performers cards — orange score badge, Popular flag, clickable thumbnail/title/channel links, Remix in my voice button. Card shape: score (float, views/subs from API), popular (bool, score>=1.5) |
| `sortVideos(key)` | Re-sorts `window.allVideos` by 'views'\|'viral'\|'likes', re-renders grid |
| `openScoutRemix(title, channel, views, score, popular, market)` | Opens Scout remix modal — "Remix in my voice". POSTs to `/api/yt/scout-remix`. Only LLM call in Scout. |
| `runScoutRemix()` | Executes Foundation-powered remix via `/api/yt/scout-remix`. Shows foundation_not_ready error if < 5 samples. |
| `copyScoutRemix()` | Copies hook/concept/angle/cta to clipboard from Scout remix modal |
| `openThumbRemix(title, thumbnail, url, city)` | Opens thumbnail remix modal with competitor analysis |
| `timeAgo(iso)` | Converts ISO date string to "3mo ago" format |

### Script Lab
| Function | Description |
|----------|-------------|
| `runScriptBuilder()` | POST to `/api/yt/script-formula`, populates Script Lab UI |
| `setScriptMode(mode)` | Toggle 'bullets' (outline view) or 'full' (assembled script) |
| `_assembleFullScript(data)` | Uses generated full_script, falling back to hook + early_cta + body_sections/body_outline + end_screen |
| `sendToTeleprompter()` | Writes script to localStorage, opens `/studio` in new tab |
| `copyFullScript(btn)` | Copies assembled full script to clipboard |
| `copySection(elId, btn)` | Copies text content of a DOM element |

### SEO Package
| Function | Description |
|----------|-------------|
| `generateSEO()` | POST to `/api/yt/seo-package` using `currentScript` |
| `generateSEOFromFormula()` | Generate SEO from Script Lab result |
| `renderSEO(data)` | Renders title/description/tags into SEO panel |
| `renderScript(data)` | Renders script sections with copy buttons (older path) |

### Adapt Concept Modal
| Function | Description |
|----------|-------------|
| `openAdaptModal(title, city)` | Opens Remix Concept modal |
| `runAdaptConcept()` | POST to `/api/yt/adapt-concept` |
| `adaptedScriptIt()` | Hand adapted concept to Script Builder |
| `scriptIt(topic, city)` | Convenience: set topic/city + open Script Builder |
| `scriptItFromStandard(formatName, city)` | Script from a market standard format |

### Topic Finder
| Function | Description |
|----------|-------------|
| `runTopicFinder()` | POST to `/api/yt/content-calendar`, render Trend Radar topic cards with Schedule/Write actions |

### Video Advisor
| Function | Description |
|----------|-------------|
| `runVideoAdvisor()` | POST to `/api/yt/video-advisor` |
| `setAdvisorMode(mode)` | Toggle advisor mode ('video'\|'channel') |

### Pillar Planner
| Function | Description |
|----------|-------------|
| `runPillarPlanner()` | POST to `/api/yt/pillar-plan` |
| `togglePillarPlanner()` | Show/hide pillar planner UI |
| `writeThisPillarIdea(idea, market, pillar)` | Send pillar idea to Script Builder |
| `useTopicInCreate(topic)` | Send Trend Radar/Pillar Planner topic into Create Script Builder |
| `addTopicToSchedule(topic)` | Add topic object to Content Scheduler queue |
| `addPillarIdeasToSchedule(ideas, market, pillar)` | Add all ideas from one pillar into scheduler queue |
| `addTrendTopicsToSchedule(itemsJson, city)` | Add all Trend Radar result topics into scheduler queue |

### Cover Forge (Thumbnails)
| Function | Description |
|----------|-------------|
| `setCoverForgePhotoMode(mode)` | Toggle Upload / AI Persona visual state |
| `toggleCoverForgeAdvanced()` | Show/hide reference thumbnail, market, pillar, emotion, and home-tour controls |
| `loadAiPersona()` | GET `/api/yt/ai-persona`, refresh persona photo state |
| `openAiPersonaManager()` | Open AI Persona Library modal |
| `triggerAiPersonaUpload(shotType)` | Open file picker for headshots, body shots, or expressions |
| `uploadAiPersonaPhotos(files, shotType)` | POST persona images with `FormData` |
| `renderAiPersonaSummary()` | Update Cover Forge persona count/avatar summary |
| `renderAiPersonaGrid()` | Render AI Persona Library photo cards |
| `selectAiPersonaPhoto(photoId)` | Select primary persona photo for thumbnail previews |
| `deleteAiPersonaPhoto(photoId)` | DELETE selected persona photo |
| `primaryPersonaPhoto()` | Resolve selected or first persona image |
| `startCoverForgeProgress()` | Show staged thumbnail generation pipeline with countdown |
| `updateCoverForgeProgress(forcePercent)` | Animate analyzing/layout/design/generation/quality steps |
| `finishCoverForgeProgress()` | Complete progress state before revealing thumbnails |
| `stopCoverForgeProgress()` | Clear Cover Forge progress interval |
| `renderCoverForgeSteps(activeIndex)` | Render step statuses for thumbnail generation |
| `formatCoverForgeTime(totalSeconds)` | Format countdown as M:SS |
| `coverForgePalette(index, variant)` | Pick preview palette for generated thumbnail card |
| `thumbnailTextLines(text)` | Split 3-5 word thumbnail text into bold preview lines |
| `runCoverForge()` | POST to `/api/yt/cover-forge`, animate pipeline, render thumbnail previews |
| `goToCoverForge()` | Switch to Cover Forge panel |

### Repurpose Engine
| Function | Description |
|----------|-------------|
| `runRepurposeEngine()` | POST to `/api/yt/repurpose` |
| `toggleRepurposeOutput(key)` | Toggle shorts/instagram/tiktok/blog output types |

### Lead Page Generator
| Function | Description |
|----------|-------------|
| `runLeadPageGenerator()` | POST to `/api/yt/lead-page` |

### Scheduler
| Function | Description |
|----------|-------------|
| `loadSchedule()` | GET `/api/yt/scheduler` + render shoot days |
| `saveSchedule()` | POST `/api/yt/scheduler/save` with shoot days, queued topics, and market |
| `initScheduler()` | Initialize scheduler state + load |
| `renderShootDays()` | Render weekly shoot day selector |
| `toggleShootDay(day)` | Add/remove day from `selectedDays` Set |

### Utilities
| Function | Description |
|----------|-------------|
| `escHtml(str)` | HTML-encodes `& < > "` — use for all user content in HTML |
| `safeAttr(v)` | `JSON.stringify(v)` + `.replace(/'/g, "&#39;")` — for single-quoted onclick attrs |
| `copyText(btn, text)` | Clipboard write with execCommand fallback + `flashCopied` |
| `flashCopied(btn)` | Button flash "✓ Copied!" for 2s |
| `fmtNum(n)` | Format number: 1234567 → "1.2M", 1234 → "1.2K" |
| `timeAgo(iso)` | ISO date → "3mo ago" |
| `loadSavedMarket()` | Read localStorage `podclick_saved_market`, populate all city fields |
| `saveMarket(value)` | Write city to localStorage |
| `compClass(val, direction)` | Competitive analysis CSS class |
| `closeModal(modalId)` | Hide modal overlay |
| `closeModalById(id)` | Alias for closeModal |
| `openScriptBuilder()` | Switch to Script Lab panel |

---

## Encoding Conventions (CRITICAL — breaks buttons if wrong)

### Double-quoted `onclick=""` attrs — use `.replace(/"/g, "&quot;")`
```javascript
// Correct: JSON.stringify inside double-quoted attr
`onclick="copyText(this, ${JSON.stringify(text).replace(/"/g, '&quot;')})">`
// The browser decodes &quot; → " before executing onclick
```

### Single-quoted `onclick=''` attrs — use `safeAttr()`
```javascript
// Correct: safeAttr handles both " and ' escaping
`onclick='openAdaptModal(${safeAttr(v.title)}, ${safeAttr(city)})'`
// JSON.stringify wraps in " (fine in single-quoted attr)
// &#39; prevents apostrophes in titles from breaking the attribute
```

### Direct DOM — use `data-text` + addEventListener
```javascript
// For bullets/idea lists where text contains arbitrary content:
btn.dataset.text = points[i];
btn.addEventListener('click', () => copyText(btn, btn.dataset.text));
```

---

## studio.html — Key Functions

### Publish Modal
| Function | Description |
|----------|-------------|
| `openPublishModal()` | Guard-checks recordingBlob, pre-fills YouTube title from topic field, opens `#publish-modal` |
| `closePublishModal()` | Removes `.open` class, collapses YouTube sub-panel |
| `expandYouTubeTile()` | Toggles YouTube sub-panel (channel/title/privacy inputs) and `.expanded` grid class |
| `publishToPodcast()` | Closes modal then calls `_doPodcastPublish()` |
| `publishToTelegram()` | Closes modal, POSTs blob to `/api/studio/publish/telegram`, toasts result |
| `publishToYouTube()` | Stub — closes modal, toasts instruction to use Dashboard after podcast publish |

### Core Studio
| Function | Description |
|----------|-------------|
| `loadTodayTopic()` | Fetch `/api/studio/today-topic` → populate topic/pillar/market fields |
| `generateScript()` | POST `/api/studio/generate-script` → populate textarea + render prompter |
| `checkInboundScript()` | On load: read localStorage `podclick_teleprompter_script`, populate prompter, clear key. Does NOT auto-start camera (user gesture required). |
| `renderPrompter()` | Render script text into teleprompter display; syncs `#cam-overlay-track` when overlay is active |
| `toast(msg, kind)` | Show notification toast (info/success/error) |
| `_doPodcastPublish()` | (renamed from `publishRecording`) POSTs blob to `/api/run`, redirects to dashboard on success |
| `startCameraAndRecord()` | Start camera then begin recording after 600ms — called from "Record Script" CTA |
| `trayDownload()` | Trigger browser download of the current recording blob as `podclick-take-{timestamp}.webm` |
| `_doYouTubePublishRaw()` | POST recording blob to `/api/studio/publish/youtube` with title/description/privacy from modal fields. Shows file-xfer bar. Stores URL in `state.lastYouTubeUrl`. |
| `publishToYouTube()` | Closes modal, calls `_doYouTubePublishRaw()`, toasts YouTube URL on success |
| `generateShowNotes()` | POST `/api/studio/show-notes` with topic/pillar/market/script → renders markdown into `#shownotes-output` |
| `generateSocialPosts()` | POST `/api/studio/social-posts` with topic/market/episode_url → populates `_socialData` with platform posts |
| `switchPostEpTab(tab, btn)` | Toggle Show Notes / Social Posts tabs in post-episode panel |
| `switchSocialPlat(plat, btn)` | Switch active platform in social posts output (linkedin/facebook/instagram/x) |
| `copyPostEp(elId, btn)` | Copy text content of a post-episode output element to clipboard |

### Device Check Modal (pre-studio setup)
| Function | Description |
|----------|-------------|
| `dcInit()` | Called on page load — requests `getUserMedia`, populates device dropdowns, shows preview in `#dc-video` |
| `_dcPopulateDevices()` | `enumerateDevices()` → fill `#dc-cam-select` and `#dc-mic-select`. Flags iPhone/Continuity cameras with 📱 and sets the `#dc-cam-hint` setup tip. |
| `dcRefreshDevices()` | Re-runs `_dcPopulateDevices()` (🔄 button) — use after connecting an iPhone mid-setup so it appears without a page reload. |
| `dcSwitchDevice()` | Stop current `_dcStream`, restart with selected `deviceId` from dropdowns |
| `dcToggleCam()` | Enable/disable video tracks in `_dcStream`; show/hide `#dc-video` preview |
| `dcToggleMic()` | Enable/disable audio tracks in `_dcStream` |
| `dcSetHeadphones(val)` | Toggle headphones Yes/No button styles (visual only) |
| `dcEnterStudio()` | Transfer `_dcStream` → `state.camStream` without second `getUserMedia`; attach audio analyser; close modal; call `checkInboundScript()` |
| `dcSkip()` | Stop `_dcStream`; close modal; call `checkInboundScript()` — enters studio without camera |

### Teleprompter Overlay
| Function | Description |
|----------|-------------|
| `enterOverlay()` | Show `#cam-overlay` inside `#preview-shell`; mirror prompter track HTML; sync transform; recalc `promptMaxScroll` |
| `exitOverlay()` | Hide `#cam-overlay`; restore `promptMaxScroll` |
| `getSrAlpha()` | Map Responsiveness slider (0.005–0.08) → EMA alpha (0.10–0.85). Cold-start logic lives in `onresult` handler. |

---

### Screen Recorder
| Function | Description |
|----------|-------------|
| `recToggle()` | Start or stop the screen recording (called by the big record button) |
| `recStart()` | `getDisplayMedia()` + optional mic mix → `MediaRecorder.start()` → timer running |
| `recStop()` | `MediaRecorder.stop()` → `_recShowPreview()` after `onstop` fires |
| `_recShowPreview()` | Creates `URL.createObjectURL(_recBlob)` → populates `<video>` preview, shows preview state |
| `recDownloadWebM()` | Create anchor + click to download raw WebM blob |
| `recConvertMP4()` | POST WebM blob to `/api/screen-record/convert`, receive MP4 blob, trigger download |
| `recReset()` | Clear blob/state, return to idle/record UI |
| `_recFmtTime(s)` | Format seconds → `MM:SS` string |

---

---

## foundation.html — Global State

| Variable | Purpose |
|----------|---------|
| `ivState` | Card 1 state machine: `idle` \| `asking` \| `recording` \| `submitting` \| `done` |
| `ivQuestionIdx` | Current question index (0–7) in voice interview |
| `ivMediaRecorder` | Active `MediaRecorder` instance for voice interview |
| `ivChunks` | Recorded audio chunks array (cleared per question) |
| `ivTimerInterval` | `setInterval` handle for recording timer |
| `ivSeconds` | Elapsed recording seconds for timer display |
| `uploadFile` | `File` object selected/dropped for Card 2 upload (null if none) |
| `totalSamples` | Running total sample count (loaded from status, incremented on ingest) |
| `sessionPoured` | Samples added this session (resets on reload) |

---

## foundation.html — JS Functions

### Card 1 — Voice Interview
| Function | Description |
|----------|-------------|
| `startInterview()` | Show question panel, hide intro/done; call `loadQuestion(0)` |
| `loadQuestion(idx)` | Display question text + counter, reset record button state and timer |
| `startRecording()` | `getUserMedia` → `MediaRecorder.start()`, tick timer, update button |
| `stopAndSubmit()` | `MediaRecorder.stop()`, triggers `onstop` → `submitIvBlob()` |
| `submitIvBlob(blob, questionText)` | POST blob to `/api/foundation/transcribe-and-ingest`, call `addFeedItem()`, auto-advance |

### Card 2 — Upload Audio / Video
| Function | Description |
|----------|-------------|
| `setUploadFile(f)` | Store file ref, show filename + size, enable upload button |
| `uploadAudio(file, single)` | XHR POST to `/api/foundation/transcribe-and-ingest` with progress bar; calls `addFeedItem()` on success |

### Shared — Card 3 & status
| Function | Description |
|----------|-------------|
| `loadStatus()` | GET `/api/foundation/status`, set `totalSamples`, update tier badge |
| `updateBadge(count)` | Compute tier label (not_ready/thin/solid/deep) + sample count, update `#tier-badge` |
| `pourIt()` | POST `/api/foundation/ingest` with text + `source='written_from_scratch'`, call `addFeedItem()` |
| `addFeedItem(text, source)` | Increment counters, update badge, prepend feed item to `#pour-feed` |
| `fnToast(msg, kind)` | Show notification toast (`info`/`success`/`error`) |

---

## social-studio.html — Global State

| Variable | Purpose |
|----------|---------|
| `_forgeData` | Last Post Forge result `{linkedin, facebook, instagram, x}` |
| `_hashtagData` | Stored hashtag sets `{core, niche, local, trending}` from Hashtag Lab |
| `_calendarData` | Array of calendar entry objects loaded from `/api/social/calendar` |
| `_forgeMode` | Active Post Forge tab: `'idea'`, `'episode'`, or `'template'` |
| `_activeTemplate` | Selected template name in From Template tab |

---

## social-studio.html — JS Functions

### Navigation & Panels
| Function | Description |
|----------|-------------|
| `switchPanel(id)` | Show panel by id (`panel-forge`, `panel-calendar`, `panel-hashtags`, `panel-repurpose`), hide others, update nav pill |

### Post Forge
| Function | Description |
|----------|-------------|
| `switchForgeMode(mode)` | Toggle From Idea / From Episode / From Template tabs; sets `_forgeMode` |
| `switchTemplate(name)` | Set `_activeTemplate`, highlight selected template button |
| `runForge()` | Collect inputs by mode, POST to `/api/social/forge`. On 422 + `foundation_not_ready` → shows `#forge-empty-state` with link to /foundation. On success, shows thin-warning banner if `_foundation_thin`. Calls `renderForgeOutput()`. |
| `renderForgeOutput(data)` | Render LinkedIn/Facebook/Instagram/X/TikTok output blocks with char counts; auto-appends stored hashtags to Instagram if `_hashtagData` present; sets `copyBtn.dataset.text` + addEventListener for each copy button |

### Content Calendar
| Function | Description |
|----------|-------------|
| `loadCalendar()` | GET `/api/social/calendar`, store in `_calendarData`, call `renderCalendar()` |
| `renderCalendar()` | Build 7-day Mon–Sun grid; render platform badges + title snippets per day from `_calendarData` |
| `openCalendarModal(day)` | Open add-post modal pre-filled with target day |
| `addToCalendar()` | POST to `/api/social/calendar` with modal form values; reload calendar on success |
| `deleteCalendarEntry(id)` | DELETE `/api/social/calendar/{id}`; reload calendar on success |

### Hashtag Lab
| Function | Description |
|----------|-------------|
| `generateHashtags()` | POST to `/api/social/hashtags` with market + niche; store in `_hashtagData`; call `renderHashtags()` |
| `loadHashtags()` | GET `/api/social/hashtags`; store in `_hashtagData` if present; call `renderHashtags()` |
| `renderHashtags()` | Render core/niche/local/trending hashtag sets; copy-all button per set uses `btn.dataset.text` + addEventListener |

### Repurpose Hub
| Function | Description |
|----------|-------------|
| `runRepurpose()` | POST to `/api/social/repurpose` with URL/transcript + market; call `renderRepurposeOutput()` |
| `renderRepurposeOutput(data)` | Render 5 angle cards with platform badge, angle summary, post text; copy button uses `btn.dataset.text` + addEventListener |

### Utilities
| Function | Description |
|----------|-------------|
| `ssToast(msg, kind)` | Show notification toast (info/success/error) in Social Studio; kind defaults to `'info'` |
| `escHtml(str)` | HTML-encode `& < > "` — used for all dynamic content injected into DOM |
