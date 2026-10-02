# UI consistency — shell, tokens, and remaining off-token colors

> Wave 1 (2026-10-01): one app shell + token cleanup at the shell and `:root` level.
> Wave 2 owns the in-body hex cleanup listed below.

## Rules (single source of truth)

1. **Navigation** is rendered only by `frontend/static/podclick-nav.js` (`<nav id="pc-shell">`). No page ships its own site nav (`.nav-toggle`, sidebars, `.topbar nav`). The repo-root `static/podclick-nav.js` duplicate was deleted (nothing referenced it; `STATIC_DIR` in `main.py` is `frontend/static/`).
2. **Tokens** live in `frontend/podclick-design.css` `:root`. Every page loads it via `<link rel="stylesheet" href="/podclick-design.css?v=…">` (and `podclick-nav.js` injects it if a page forgets). Pages may define only token *aliases* (`--grad: linear-gradient(135deg, var(--orange), var(--orange-hi))`) or page-only accents; they must not redefine `--bg`, `--surface*`, `--border`, `--accent*`, `--text*`, `--radius`, `--font`.
3. **Page-local tabs** use the shared `.pc-subtabs` class (orange underline, condensed caps) and must never repeat a global destination.
4. **Pages without a hero** use `.pc-page-title` (`h1` + mono eyebrow `p`, optional `.pc-page-actions`).
5. **Cache-bust**: bump `?v=` on every page whenever `podclick-nav.js` or `podclick-design.css` changes. Current: `20261002-1`.

## Remaining off-token hex values (wave 2)

Counted with `scratchpad/hex.ts`-style scan: 6-digit hexes in each page that are **not** defined in the design-token block. "Clash" = cyan/blue/purple/navy values from the retired PodClickAI palette, which fight the orange/charcoal system and should go first.

| File | Off-token hex uses | Distinct | Most frequent | Clashing (fix first) |
|---|---|---|---|---|
| youtube-studio.html | 339 | 54 | `#5a5a7a`×71 `#7a7a9a`×66 `#c0c0d8`×38 `#2ee49c`×36 `#3a3a5a`×15 `#ffb340`×12 `#a0a0c0`×7 `#ff4d6d`×7 | `#5a5a7a` `#2ee49c` `#3a3a5a` `#a0a0c0` `#6a6a8a` `#1e1e2e` `#4a4a6a` `#2e2e42` `#6366f1` `#22c55e` |
| project.html | 134 | 79 | `#f5f7fa`×18 `#ff8080`×6 `#7fb8e8`×4 `#302a22`×4 `#ffd700`×4 `#f2aa62`×3 | `#7fb8e8` `#2ee49c` `#a0a0c0` |
| index.html | 132 | 18 | `#2ea7ff`×42 `#8b5cff`×33 `#22c55e`×14 `#ffb340`×10 `#c66cff`×5 `#a7b0c0`×5 | `#2ea7ff` `#8b5cff` `#22c55e` `#c66cff` `#a7b0c0` (+27 cyan/purple `rgba()` uses) |
| studio.html | 77 | 20 | `#2a2a3a`×17 `#2a2a3e`×10 `#13131a`×9 `#2ee49c`×7 `#141422`×7 `#1e1e2e`×5 | navy-grey device-check + upload-tray inline styles |
| projects.html | 50 | 34 | `#f5f7fa`×8 `#a79d91`×7 `#393127`×2 | `#a0a0c0` `#7fb8e8` |
| brand-studio.html | 32 | 12 | `#8899cc`×16 `#ff557e`×4 `#030b2c`×3 | `#8899cc` `#ff557e` `#030b2c` (+11 cyan `rgba(5,195,249,…)`; body still renders navy) |
| walkthrough.html | 14 | 10 | `#94a3b8` `#60a5fa` `#fb923c` `#c084fc` (permit-tier badges) | `#94a3b8` `#60a5fa` |
| onboarding.html | 11 | 9 | `#2ee49c` `#60a5fa` `#e07030` | `#2ee49c` `#60a5fa` |
| vsl-editor.html | 11 | 5 | `#f5f7fa`×7 | `#a0a0c0` |
| billing.html | 10 | 9 | light-theme palette (`#f5f6f2`, `#202420`) | intentionally light; decide whether billing joins the dark system |
| editor.html | 9 | 4 | `#f5f7fa`×5 `#ff8080`×2 | — |
| project-editor.html | 8 | 6 | `#f5f7fa`×3 `#6b2e9e` `#9b59d0` | purple AI-edit button |
| social-studio.html | 7 | 5 | `#f97316` `#ffb340` `#ff557e` `#8aafc5` `#030b2c` | `#ff557e` `#8aafc5` `#030b2c` (+11 cyan `rgba()`) |
| blueprint.html | 4 | 4 | status greens/reds | — |
| foundation.html | 4 | 4 | `#ff6b1a` `#5fb85f` | — |
| permit.html | 4 | 4 | tier accents (`--tier-*` kept as page-only vars) | `#60a5fa` |
| calendar.html | 0 | 0 | — | — |

Suggested mapping for wave 2: `#f5f7fa`→`var(--text-pri)`, `#5a5a7a`/`#7a7a9a`/`#a0a0c0`→`var(--text-sec)`, `#2ee49c`/`#22c55e`→`var(--green-hi)`, `#ff557e`/`#ff4d6d`/`#ff8080`→`var(--red)`, `#ffb340`→`var(--yellow)`, `#2ea7ff`/`#8b5cff`/cyan→`var(--orange)`/`var(--orange-hi)`, navy surfaces (`#030b2c`, `#13131a`, `#1e1e2e`, `#2a2a3a`)→`var(--bg)`/`var(--bg-card)`/`var(--border)`.

## Other inconsistencies left for wave 2

- **Fonts**: Inter was replaced with `var(--font-body)` (Barlow) and its Google Font imports removed on all pages (2026-10-02). `index.html` still sets its own Helvetica Neue body stack.
- **Dead legacy CSS**: `.nav-toggle`, `#sidebar`, `.topbar nav`, `.header-right a` rules remain in several page `<style>` blocks (no markup uses them now). Safe to delete.
- **studio.html mobile**: at 375px the "Today's topic" panel overlaps the "Upload a pre-recorded file" tray (page grid, pre-existing). The shell's Upload button does not depend on that tray.
- **onboarding.html** intentionally has no shell (full-screen first-run flow); **billing.html** is script-free, so it has no shell either (design CSS is loaded but its own light styles win).

- **Verified 2026-10-02** (uvicorn :8766, local deployment mode): /, /studio, /social-studio, /youtube-studio, /projects, /calendar, /foundation each have exactly one `<nav>` (`#pc-shell`), no page-level horizontal overflow at desktop and 375px. Console 500s are API calls failing without a database.
