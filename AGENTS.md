# PodClick — Project Context
> Reference docs below replace reading full source files. Update docs as code changes.

@docs/ARCHITECTURE.md
@docs/API.md
@docs/FRONTEND.md
@docs/BUGS_AND_FIXES.md

## Project Rules

- **Server start:** `cd ~/podcast-studio && venv/bin/uvicorn main:app --reload --port 8765`
- **Python version:** 3.9 — NO `str | None` syntax (use `Optional[str]` or just `= None`)
- **Frontend:** Static HTML, no build step. Edit files and hard-refresh browser.
- **Secrets:** All in `.env` — never hardcode. `os.getenv("KEY_NAME")` always.
- **Background jobs:** `asyncio.create_task()` — stored in `yt_spy_jobs` dict by UUID.
- **YouTube quota:** 10K units/day. Resets midnight Pacific. `_yt_get()` returns `{}` on failure.
- **Copy button encoding:** See `docs/FRONTEND.md` "Encoding Conventions" before adding any onclick copy button.
- **New API route:** Add to `docs/API.md` immediately.
- **New JS function:** Add to `docs/FRONTEND.md` immediately.
- **Bug fixed:** Add dated entry to `docs/BUGS_AND_FIXES.md` immediately.
