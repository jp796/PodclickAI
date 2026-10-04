# PodClick Design System v2

> Owner files: `frontend/podclick-design.css` (tokens + components, the `/* ===== Design system v2 ===== */` section), `frontend/static/pc-icons.js` (icons + emoji safety net), `frontend/static/podclick-nav.js` (the shell, which loads the icons for every page). Contract tests: `tests/test_design_system.py`.

PodClick looks like a job site, not a SaaS template. Charcoal surfaces, one safety-orange accent, 1px hairlines, stencil type, and a blueprint grid used sparingly. No purple gradients, no glassmorphism, no emoji in the interface.

## 1. Rules for page agents

1. **No emoji in UI chrome.** Use an icon: `<i class="pc-i" data-i="clapperboard"></i>`. In JS-built strings use `PodClickIcons.svg('clapperboard')`. Emoji stay legal in user content and AI output only.
2. **Do not add a `<script>` for icons.** `podclick-nav.js` injects `/static/pc-icons.js` on every page that has the shell. The safety net then swaps any emoji you missed inside buttons, links, labels, headings, tabs, badges and chips.
3. **Mark AI output.** Any container that shows AI-written copy (social posts, captions, show notes, scripts) gets `data-ai-output`. Anything else that must keep literal emoji gets `data-keep-emoji`. Textareas, inputs, `pre` and `code` are protected automatically.
4. **Tokens, not hex.** Colours, spacing, radii, shadows and motion all come from `:root` variables (section 3). Use `--red-hi` / `--blue-hi` / `--purple-hi` / `--text-sec-hi` for text; the base `--red` / `--blue` / `--purple` fail contrast as text.
5. **Adopt `pc-*` components for new markup** (section 4). Legacy class names (`.btn`, `.btn-primary`, `.card`, `.empty-state` ...) keep working: the v2 layer styles them at zero specificity, so a page's own rule always wins.
6. **Bump the CSS `?v=`** on your page when you adopt v2 classes. Every page currently pins `podclick-design.css?v=20261003-1`, so browsers with that file cached will not see v2 until the page's link changes.
7. **Typography:** Big Shoulders Display for headings (`--font-display`, uppercase, `letter-spacing: .03–.06em`), Barlow for body (`--font-body`), Barlow Condensed for controls (`--font-cond`), Azeret Mono for data, labels and numbers (`--font-mono`, `.pc-num` for tabular figures).

## 2. Icons

`frontend/static/pc-icons.js` is a generated, self-contained bundle: 178 Lucide icons (ISC licence, notice in the file header), 24×24 grid, `stroke="currentColor"`, stroke width 1.75, round caps and joins, no fills except Lucide's own tiny `currentColor` dots. It makes no network requests.

```html
<i class="pc-i" data-i="mic"></i>                  <!-- 1.1em, inherits text colour -->
<i class="pc-i" data-i="rocket" data-size="20"></i> <!-- fixed 20px -->
<i class="pc-i pc-i--lg pc-i--orange" data-i="hard-hat"></i>
```

```js
PodClickIcons.svg('scissors', { size: 16, className: 'x', strokeWidth: 2, title: 'Cut' }) // markup string
PodClickIcons.mount(rootEl)         // fill <i class="pc-i" data-i> placeholders (runs automatically)
PodClickIcons.replaceEmoji(rootEl)  // run the safety net on a subtree (runs automatically)
PodClickIcons.iconFor('🎬')         // 'clapperboard' (variation selectors ignored)
PodClickIcons.names                 // every bundled icon name
```

`mount()` and `replaceEmoji()` run on `DOMContentLoaded` and again, debounced (40 ms), from a `MutationObserver` for nodes your page inserts later (toasts, rendered lists, `btn.textContent = '✓ Copied'`). Unknown names render nothing and warn once in the console.

**Icon names used by the shell:** Walk-through `house`, Studio `video`, Social `message-square-text`, Video / VSL `clapperboard`, Brand `stamp`, Calendar `calendar-days`, Job Site `hard-hat`, Scout `binoculars`, Crew `users`, Foundation `brick-wall`, Blueprint `drafting-compass`, Permit `shield-check`, Legacy builder `history`, Upload `upload`, menu `menu`/`x`. Section labels: `map`, `hammer`, `calendar-check`, `trending-up`, `layers`.

### Emoji safety net

`replaceEmoji(root)` rewrites emoji to icons **only inside UI chrome**: `button, a, summary, label, legend, th, h1–h6, nav, [role=tab], [role=button], [role=menuitem], [data-pc-icons]`, and any element whose class starts with or contains `btn`, `tab`, `badge`, `chip`, `pill`, or `pc-`.

It **never** touches text inside `textarea, input, select, option, pre, code, kbd, samp, script, style, noscript, template, svg, [contenteditable], [data-keep-emoji], [data-ai-output], .output, .result-body, .output-body, .out-text, .post-ep-output, .brick-msg*, #brick-messages, .prose, .markdown`. AI-written social posts legitimately contain emoji and must display and copy verbatim.

Surrounding text is kept (`"🎬 Record"` becomes icon + `" Record"`), U+FE0F/U+FE0E variation selectors are consumed, and a control left with no visible text gets an `aria-label` from the icon name. The original emoji is kept on the icon as `data-emoji`.

Known limits: emoji in `title`, `placeholder`, `<option>` text, CSS `content:` and `document.title` are not rewritten (they cannot hold SVG). Page JS that reads a button's `textContent` back will no longer see the emoji. Typographic bullets (`●`, `○`, `■`) and box-drawing characters are intentionally not mapped.

| Emoji | Icon |
|---|---|
| ✓ | `check` |
| ✔ | `check` |
| ✅ | `circle-check` |
| ☑ | `square-check` |
| ✗ | `x` |
| ✘ | `x` |
| ✕ | `x` |
| ✖ | `x` |
| ❌ | `circle-x` |
| ❎ | `circle-x` |
| ⚠ | `triangle-alert` |
| ❗ | `circle-alert` |
| ❓ | `circle-help` |
| ℹ | `info` |
| ⛔ | `ban` |
| 🚫 | `ban` |
| 🎬 | `clapperboard` |
| 🎙 | `mic` |
| 🎤 | `mic` |
| 🎧 | `headphones` |
| 📻 | `radio` |
| ✦ | `sparkles` |
| ✨ | `sparkles` |
| 🌟 | `sparkles` |
| ⭐ | `star` |
| 📅 | `calendar` |
| 📆 | `calendar` |
| 🗓 | `calendar-days` |
| 📋 | `clipboard-list` |
| 📝 | `notebook-pen` |
| ✏ | `pencil` |
| ✍ | `pen-line` |
| ⚡ | `zap` |
| 🔗 | `link` |
| 📎 | `paperclip` |
| ✂ | `scissors` |
| 🔒 | `lock` |
| 🔓 | `lock-open` |
| 🔑 | `key` |
| 🛡 | `shield` |
| 🔄 | `refresh-cw` |
| 🔁 | `repeat` |
| 🔀 | `shuffle` |
| ♻ | `recycle` |
| ↻ | `rotate-cw` |
| ↺ | `rotate-ccw` |
| 📱 | `smartphone` |
| 📲 | `smartphone` |
| 🖥 | `monitor` |
| 💻 | `laptop` |
| 📁 | `folder` |
| 📂 | `folder-open` |
| 🗂 | `folders` |
| 🎵 | `music` |
| 🎶 | `music` |
| 📚 | `library` |
| 📘 | `book` |
| 📖 | `book-open` |
| 📦 | `package` |
| 💼 | `briefcase` |
| 💾 | `save` |
| 📧 | `mail` |
| ✉ | `mail` |
| 📨 | `mail` |
| 📩 | `mail` |
| 📭 | `inbox` |
| 📥 | `inbox` |
| 📤 | `send` |
| ✈ | `send` |
| 🔍 | `search` |
| 🔎 | `search` |
| 🚀 | `rocket` |
| 🤖 | `bot` |
| 🧠 | `brain` |
| 🏷 | `tag` |
| 📷 | `camera` |
| 📸 | `camera` |
| 👁 | `eye` |
| 👀 | `eye` |
| 🌐 | `globe` |
| 📹 | `video` |
| 🎥 | `video` |
| 📽 | `film` |
| 🎞 | `film` |
| 📺 | `tv` |
| 🖼 | `image` |
| 🎨 | `palette` |
| 🗑 | `trash-2` |
| 📢 | `megaphone` |
| 📣 | `megaphone` |
| ▶ | `play` |
| ⏯ | `play` |
| ⏸ | `pause` |
| ⏹ | `square` |
| ⏺ | `circle-dot` |
| 🔴 | `circle-dot` |
| ⏮ | `skip-back` |
| ⏭ | `skip-forward` |
| ⬇ | `download` |
| ⬆ | `upload` |
| → | `arrow-right` |
| ➡ | `arrow-right` |
| 👉 | `arrow-right` |
| ← | `arrow-left` |
| ⬅ | `arrow-left` |
| ↑ | `arrow-up` |
| ↓ | `arrow-down` |
| ↗ | `arrow-up-right` |
| ↙ | `arrow-down-left` |
| ↘ | `arrow-down-right` |
| ↖ | `arrow-up-left` |
| 🎯 | `target` |
| 🔥 | `flame` |
| 💡 | `lightbulb` |
| 📊 | `chart-column` |
| 📈 | `trending-up` |
| 📉 | `trending-down` |
| 🏠 | `house` |
| 🏡 | `house` |
| 🏗 | `construction` |
| 🧱 | `brick-wall` |
| 👷 | `hard-hat` |
| 🔨 | `hammer` |
| 🔧 | `wrench` |
| 🛠 | `wrench` |
| ⚙ | `settings` |
| 📐 | `ruler` |
| 🔔 | `bell` |
| 👤 | `user` |
| 👥 | `users` |
| ⏳ | `hourglass` |
| ⌛ | `hourglass` |
| ⏱ | `timer` |
| ⏰ | `alarm-clock` |
| 🕐 | `clock` |
| 💰 | `circle-dollar-sign` |
| 💵 | `banknote` |
| 💸 | `banknote` |
| 🛒 | `shopping-cart` |
| 🎉 | `party-popper` |
| 🎁 | `gift` |
| 🏆 | `trophy` |
| 🥇 | `medal` |
| ❤ | `heart` |
| 💬 | `message-square` |
| 🗨 | `message-square` |
| 👍 | `thumbs-up` |
| 🔊 | `volume-2` |
| 🔇 | `volume-x` |
| 📡 | `satellite-dish` |
| 📄 | `file-text` |
| 📃 | `file-text` |
| 📑 | `files` |
| 🧾 | `receipt` |
| 💤 | `moon` |
| 🌙 | `moon` |
| ☀ | `sun` |
| 💊 | `pill` |
| 🧪 | `flask-conical` |
| 📌 | `pin` |
| 📍 | `map-pin` |
| 🗺 | `map` |
| 🧭 | `compass` |
| 🏁 | `flag` |
| 🚩 | `flag` |
| ☎ | `phone` |
| 📞 | `phone` |
| ➕ | `plus` |
| ➖ | `minus` |
| 💎 | `gem` |

## 3. Tokens

Existing palette tokens (`--bg`, `--bg-panel`, `--bg-card`, `--bg-raised`, `--bg-inset`, `--border*`, `--orange*`, `--green*`, `--yellow*`, `--text-*`, fonts, `--radius`) are unchanged. v2 adds:

| Group | Tokens |
|---|---|
| Spacing (4px grid) | `--space-1` 4 · `--space-2` 8 · `--space-3` 12 · `--space-4` 16 · `--space-5` 20 · `--space-6` 24 · `--space-7` 32 · `--space-8` 48 |
| Radius | `--radius-sm` 4px · `--radius-md` (= `--radius`, 7px) · `--radius-lg` 10px · `--radius-pill` |
| Lines + elevation | `--hairline` · `--shadow-card` · `--shadow-pop` · `--ring` (2px bg gap + 2px orange) · `--ring-soft` |
| Motion | `--ease-out` · `--ease-in-out` · `--dur-fast` 120ms · `--dur` 200ms · `--dur-slow` 320ms (all zeroed under `prefers-reduced-motion`) |
| Text contrast | `--red-hi` · `--blue-hi` · `--purple-hi` · `--text-sec-hi` · `--red-dim` |
| Primary fill | `--orange-deep` · `--grad-primary` · `--grad-primary-hover` |
| Texture | `--blueprint-line` · `--blueprint-major` |

### Contrast (WCAG 2.1, computed by script)

| Token | Hex | on `--bg-card` | on `--bg` | on `--bg-raised` | AA (4.5:1) on card |
|---|---|---|---|---|---|
| `--red-hi` | `#ec7a72` | 6.65 | 7.16 | 6.21 | pass |
| `--blue-hi` | `#7fb3e6` | 8.32 | 8.95 | 7.76 | pass |
| `--purple-hi` | `#c09ee8` | 8.14 | 8.76 | 7.60 | pass |
| `--red` | `#a03838` | 2.73 | 2.94 | 2.55 | FAIL |
| `--blue` | `#3a6ea8` | 3.48 | 3.75 | 3.25 | FAIL |
| `--purple` | `#7a4aa8` | 2.95 | 3.18 | 2.76 | FAIL |
| `--text-pri` | `#ede8e0` | 15.08 | 16.23 | 14.07 | pass |
| `--text-sec` | `#847d74` | 4.53 | 4.87 | 4.22 | pass |
| `--text-sec-hi` | `#a39b90` | 6.70 | 7.21 | 6.25 | pass |
| `--text-dim` | `#4a4540` | 1.94 | 2.09 | 1.81 | FAIL |
| `--orange` | `#d95f1e` | 4.91 | 5.28 | 4.58 | pass |
| `--orange-hi` | `#f07030` | 6.19 | 6.66 | 5.77 | pass |
| `--green-hi` | `#52c47a` | 8.35 | 8.99 | 7.79 | pass |
| `--yellow` | `#c4961c` | 6.77 | 7.28 | 6.31 | pass |
| white on `--orange` | `#ffffff` | — | — | — | 3.75 large-text only (3:1) |
| `--bg` text on `--orange-hi` | `#0b0a08` | — | — | — | 6.66 |

Findings: `--red`, `--blue`, `--purple` and `--text-dim` fail as text on every surface, so use the `-hi` variants for text and keep the base tokens for fills and borders. `--text-dim` (1.94:1) was the colour of every 9px eyebrow/stat label; v2 moves `.stat-label, .stat-sub, .card-eyebrow, .section-eyebrow, .card-header, .card-title, .panel-label, .eyebrow, .greeting-from` to `--text-sec`. `--text-sec` passes on cards (4.53) but fails on `--bg-raised` (4.22); use `--text-sec-hi` there. White on flat `--orange` is 3.75:1 (large text only), which is why primary buttons now use `--grad-primary` (white on `#b34e19` is 5.04:1; the gradient midpoint is about 4.6:1).

## 4. Components

| Need | Class | Notes |
|---|---|---|
| Button | `.pc-btn` + `--primary` / `--ghost` / `--outline` / `--danger`, sizes `--sm` / `--lg`, `--icon`, `--block`; `.is-loading` | Press scale 0.98, inner highlight on primary, `:disabled` and `[aria-disabled]` |
| Card | `.pc-card` (`--flush`, `--inset`), `.pc-card__head`, `.pc-card__title` | Hairline border, `--shadow-card`. Hover lift only when the card is interactive (`a`, `button`, `[onclick]`, `.is-interactive`) |
| Segmented tabs | `.pc-tabs > button.active` | Page-level sub-navigation still uses the existing `.pc-subtabs` |
| Chip / filter pill | `.pc-chip` (`.active` / `[aria-pressed=true]`) | |
| Badge | `.pc-badge` + `--orange` / `--green` / `--red` / `--blue` / `--yellow` | `.pc-badge` already styles the nav "Soon" tag |
| Status dot | `.pc-dot` + `--live` / `--warn` / `--error` / `--active` (pulses) | |
| Form | `.pc-field > .pc-label + .pc-input + .pc-hint/.pc-error` | Orange focus ring, `.is-invalid` / `[aria-invalid=true]` error state. Bare inputs get the same look at zero specificity |
| Table | `.pc-table` (`td.num` / `td.mono` for figures) | Mono uppercase headers, row hover |
| Tooltip | `data-tip="Text"` on any element | CSS only |
| Toast | `.pc-toast-stack > .pc-toast.is-success / .is-error / .is-info` | Slide-up entrance |
| Modal | `.pc-backdrop > .pc-modal` | Backdrop blur, pop-in, hazard-stripe top edge (also on the shared upload dialog) |
| Drawer | `.pc-drawer` | Slides from the right |
| Skeleton | `.skeleton` or `.pc-skeleton` (`--text` / `--title` / `--block`) | Shimmer, static under reduced motion |
| Empty state | `.empty-state` or `.pc-empty` > `.empty-state__icon` (holds a `pc-i`) + `h3` + `p` + actions | Dashed border on a blueprint grid |
| Progress | `.pc-progress > span` with `style="--value: 40%"` (`--striped` while working) | |
| Spinner | `.pc-spinner` | |
| Type helpers | `.pc-display`, `.pc-eyebrow` (orange tick), `.pc-num`, `.pc-kbd`, `.pc-divider`, `.pc-hazard` | |
| Texture | `.bg-blueprint`, `.bg-grain` | Heroes and empty states only |

Global upgrades every page gets without markup changes: `:focus-visible` orange ring on all interactive elements, orange `::selection`, thin hairline scrollbars, press feedback and motion on `.btn*` / `.copy-btn`, inner highlight on `.btn-primary`, gradient shell CTA, backdrop blur on `.modal-overlay`, dark zero-specificity styling for bare inputs, selects and textareas, `accent-color` on checkboxes, radios and ranges.

## 5. Rebuilding the icon set

The bundle is generated; do not hand-edit the `ICONS` object. To add an icon, add its Lucide name to `EXTRA` (or an emoji to `EMOJI_MAP`) and rebuild. The script exits non-zero if any name does not exist in `lucide-static`.

```bash
mkdir -p /tmp/pc-icons && cd /tmp/pc-icons && bun init -y && bun add lucide-static
# save the script below as build.ts, edit EMOJI_MAP / EXTRA, then:
bun build.ts ~/podcast-studio      # rewrites frontend/static/pc-icons.js
```

The script reuses the hand-written runtime already in `pc-icons.js` (everything from `(function () {` on), swapping in fresh `ICONS` and `EMOJI_MAP` objects. To change runtime behaviour, edit `pc-icons.js` directly outside those two objects, then rebuild. After rebuilding: bump `ICONS_V` in `podclick-nav.js`, run `pytest tests/test_design_system.py`, and update the emoji table above.

<details>
<summary>build.ts</summary>

```ts
// Build frontend/static/pc-icons.js from lucide-static. Fails loudly on any unknown icon name.
// Usage: bun build.ts <path-to-repo>
import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";

const repo = process.argv[2];
if (!repo) throw new Error("usage: bun build.ts <repo>");
const ICON_DIR = join(import.meta.dir, "node_modules/lucide-static/icons");
const pkg = JSON.parse(readFileSync(join(import.meta.dir, "node_modules/lucide-static/package.json"), "utf8"));

// emoji (no FE0F) -> lucide icon name
export const EMOJI_MAP: Record<string, string> = {
  "✓": "check", "✔": "check", "✅": "circle-check", "☑": "square-check",
  "✗": "x", "✘": "x", "✕": "x", "✖": "x", "❌": "circle-x", "❎": "circle-x",
  "⚠": "triangle-alert", "❗": "circle-alert", "❓": "circle-help", "ℹ": "info", "⛔": "ban", "🚫": "ban",
  "🎬": "clapperboard", "🎙": "mic", "🎤": "mic", "🎧": "headphones", "📻": "radio",
  "✦": "sparkles", "✨": "sparkles", "🌟": "sparkles", "⭐": "star",
  "📅": "calendar", "📆": "calendar", "🗓": "calendar-days",
  "📋": "clipboard-list", "📝": "notebook-pen", "✏": "pencil", "✍": "pen-line",
  "⚡": "zap", "🔗": "link", "📎": "paperclip", "✂": "scissors",
  "🔒": "lock", "🔓": "lock-open", "🔑": "key", "🛡": "shield",
  "🔄": "refresh-cw", "🔁": "repeat", "🔀": "shuffle", "♻": "recycle", "↻": "rotate-cw", "↺": "rotate-ccw",
  "📱": "smartphone", "📲": "smartphone", "🖥": "monitor", "💻": "laptop",
  "📁": "folder", "📂": "folder-open", "🗂": "folders",
  "🎵": "music", "🎶": "music", "📚": "library", "📘": "book", "📖": "book-open",
  "📦": "package", "💼": "briefcase", "💾": "save",
  "📧": "mail", "✉": "mail", "📨": "mail", "📩": "mail", "📭": "inbox", "📥": "inbox", "📤": "send", "✈": "send",
  "🔍": "search", "🔎": "search", "🚀": "rocket", "🤖": "bot", "🧠": "brain", "🏷": "tag",
  "📷": "camera", "📸": "camera", "👁": "eye", "👀": "eye", "🌐": "globe",
  "📹": "video", "🎥": "video", "📽": "film", "🎞": "film", "📺": "tv", "🖼": "image", "🎨": "palette",
  "🗑": "trash-2", "📢": "megaphone", "📣": "megaphone",
  "▶": "play", "⏯": "play", "⏸": "pause", "⏹": "square", "⏺": "circle-dot", "🔴": "circle-dot",
  "⏮": "skip-back", "⏭": "skip-forward",
  "⬇": "download", "⬆": "upload", "→": "arrow-right", "➡": "arrow-right", "👉": "arrow-right", "←": "arrow-left", "⬅": "arrow-left",
  "↑": "arrow-up", "↓": "arrow-down", "↗": "arrow-up-right", "↙": "arrow-down-left", "↘": "arrow-down-right", "↖": "arrow-up-left",
  "🎯": "target", "🔥": "flame", "💡": "lightbulb",
  "📊": "chart-column", "📈": "trending-up", "📉": "trending-down",
  "🏠": "house", "🏡": "house", "🏗": "construction", "🧱": "brick-wall", "👷": "hard-hat",
  "🔨": "hammer", "🔧": "wrench", "🛠": "wrench", "⚙": "settings", "📐": "ruler",
  "🔔": "bell", "👤": "user", "👥": "users",
  "⏳": "hourglass", "⌛": "hourglass", "⏱": "timer", "⏰": "alarm-clock", "🕐": "clock",
  "💰": "circle-dollar-sign", "💵": "banknote", "💸": "banknote", "🛒": "shopping-cart",
  "🎉": "party-popper", "🎁": "gift", "🏆": "trophy", "🥇": "medal",
  "❤": "heart", "💬": "message-square", "🗨": "message-square", "👍": "thumbs-up",
  "🔊": "volume-2", "🔇": "volume-x", "📡": "satellite-dish",
  "📄": "file-text", "📃": "file-text", "📑": "files", "🧾": "receipt",
  "💤": "moon", "🌙": "moon", "☀": "sun", "💊": "pill", "🧪": "flask-conical",
  "📌": "pin", "📍": "map-pin", "🗺": "map", "🧭": "compass", "🏁": "flag", "🚩": "flag",
  "☎": "phone", "📞": "phone", "➕": "plus", "➖": "minus", "💎": "gem",
};

// Icons used by the shell and generally useful to pages (beyond EMOJI_MAP targets).
const EXTRA = [
  "house", "video", "message-square-text", "clapperboard", "stamp", "calendar-days", "hard-hat", "binoculars",
  "users", "brick-wall", "drafting-compass", "shield-check", "history", "upload", "menu", "x",
  "map", "hammer", "calendar-check", "trending-up", "layers",
  "plus", "minus", "copy", "check-check", "chevron-right", "chevron-left", "chevron-down", "chevron-up",
  "external-link", "info", "filter", "list", "layout-grid", "ellipsis", "loader-circle", "refresh-ccw",
  "sliders-horizontal", "file-audio", "file-video", "audio-lines", "podcast", "radio-tower", "share-2",
  "wand-sparkles", "user-plus", "log-out", "grip-vertical", "circle-play", "list-checks", "undo-2", "redo-2",
  "maximize-2", "minimize-2", "cloud-upload", "image-plus", "captions", "type", "crop", "inbox",
  "circle-alert", "circle-check", "circle-x", "triangle-alert", "eye-off", "pencil", "save", "send",
];

const names = [...new Set([...Object.values(EMOJI_MAP), ...EXTRA])].sort();
const missing = names.filter((n) => !existsSync(join(ICON_DIR, n + ".svg")));
if (missing.length) {
  console.error("UNKNOWN LUCIDE ICON NAMES:", missing.join(", "));
  process.exit(1);
}

const icons: Record<string, string> = {};
for (const n of names) {
  const raw = readFileSync(join(ICON_DIR, n + ".svg"), "utf8");
  const m = raw.match(/<svg[^>]*>([\s\S]*?)<\/svg>/);
  if (!m) throw new Error("cannot parse " + n);
  const inner = m[1].replace(/\s*\n\s*/g, "").replace(/\s+\/>/g, "/>").trim();
  if (/fill="(?!none|currentColor)/.test(inner)) throw new Error(n + " has a fill");
  icons[n] = inner;
}

const LICENSE = readFileSync(join(import.meta.dir, "node_modules/lucide-static/LICENSE"), "utf8")
  .split("\n---")[0].trim().split("\n").map((l) => " * " + l).join("\n").replace(/ +$/gm, "");

// Runtime = the hand-written part of pc-icons.js. Taken from ./runtime.js when present,
// otherwise extracted from the current repo file (so this script alone can rebuild it).
const target = join(repo, "frontend/static/pc-icons.js");
const runtime = existsSync(join(import.meta.dir, "runtime.js"))
  ? readFileSync(join(import.meta.dir, "runtime.js"), "utf8")
  : (() => {
      const cur = readFileSync(target, "utf8");
      return cur.slice(cur.indexOf("(function () {"))
        .replace(/var ICONS = \{[\s\S]*?\};\n/, "var ICONS = /*__ICONS__*/{};\n")
        .replace(/var EMOJI_MAP = \{[\s\S]*?\};\n/, "var EMOJI_MAP = /*__EMOJI_MAP__*/{};\n");
    })();
if (!runtime.includes("/*__ICONS__*/{}") || !runtime.includes("/*__EMOJI_MAP__*/{}")) throw new Error("runtime placeholders missing");
const out = `/*!
 * pc-icons.js — PodClick icon system (inline SVG, zero network requests).
 * GENERATED FILE: rebuild with the script in docs/DESIGN_SYSTEM.md ("Rebuilding the icon set").
 * ${names.length} icons from Lucide v${pkg.version} (https://lucide.dev), used under the ISC licence:
 *
${LICENSE}
 */
` + runtime
  .replace("/*__ICONS__*/{}", JSON.stringify(icons, null, 0).replace(/","/g, '",\n    "'))
  .replace("/*__EMOJI_MAP__*/{}", JSON.stringify(EMOJI_MAP, null, 0).replace(/","/g, '",\n    "'));

writeFileSync(target, out);
writeFileSync(join(import.meta.dir, "emoji-map.json"), JSON.stringify(EMOJI_MAP, null, 2));
console.log(`wrote ${names.length} icons, ${Object.keys(EMOJI_MAP).length} emoji, ${out.length} bytes`);
```

</details>
