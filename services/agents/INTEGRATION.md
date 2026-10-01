# The Crew — integration notes from lane A

Lane A does not edit `main.py` (lane C owns it this wave). These are the exact
changes lane C applies.

## 1. Mount the router (main.py, next to the existing routers at ~line 51–64)

Add the import beside the other Phase 1 router imports:

```python
from routers.agents import router as agents_router  # noqa: E402
```

Add the include beside the existing `include_router` calls, **with no prefix**
(every path in `routers/agents.py` is already absolute — `/agents`,
`/api/agents/...`):

```python
app.include_router(agents_router, tags=["Crew"])
```

That's it. It must be included on `app` *before* line ~13442
(`studio_app = app`), like the other routers, so it sits inside
`DeploymentBoundary`.

## 2. What the router brings with it

- A `startup` handler that runs restart recovery (`queued|running|committing`
  job files become `failed`, "Interrupted by a restart — run it again."). The job
  store also recovers lazily on first use, so ordering against other startup
  hooks does not matter.
- Job files: `data/agent_jobs/{job_id}.json`. Outputs: `data/agent_outputs/{job_id}/`.
  Uploads: `data/agent_uploads/{upload_id}{ext}`. All created on demand.
- `GET /agents` serves `frontend/agents.html` (lane B); returns a plain 404 until
  that file exists.

## 3. Runner contract (lanes C and D)

A runner module at `services/agents/runners/{agent_id}.py` exports:

```python
ROUTES = ("/api/yt/content-calendar",)          # exact paths ctx.call_route may hit;
                                                # "{param}" matches ONE path segment

async def run(ctx, inp) -> AgentResult: ...
async def commit(ctx, job, edits) -> dict: ...  # only if the spec has commit_tier
```

- Import `AgentResult`, `Output`, `CommitPlan`, `StepError` from
  `services.agents.contract`.
- `raise StepError("Brick-voice message")` for a failure the user should read.
  Any other exception fails the job with a generic message (its text is never
  shown — it could carry a key).
- `ctx.step(key, label)` is an async context manager; `ctx.declare_steps([...])`
  shows the whole plan up front; `ctx.skip_step(...)`; `ctx.warn(msg)`;
  `ctx.add_output(Output(...))` (or return them in `AgentResult.outputs`);
  `ctx.add_usage(provider, unit, amount)`; `ctx.check_cancelled()` between steps;
  `await ctx.brand_context(task_type, topic)`; `await ctx.call_route(path, body,
  method="POST")` → `(status, json)`; `ctx.media(capability)` → provider or None;
  `ctx.output_dir` (a `Path`, already created).
- File outputs: write into `ctx.output_dir`, then
  `Output(kind="video", label="Tour", file="tour.mp4")` — the URL is filled in.
- A runner that is missing, fails to import, lacks `run`, or (for a committing
  agent) lacks `commit` shows as `not_built` and its run route returns 423.
- `commit()` receives a deep copy of the job (read outputs from it) and the
  user's edits (`{output_id: new_value}`, text/cards only). Return a JSON-safe
  dict; it is stored as `commit_result` and returned on every later approve.
- Never write job ids into `PostVariant.platform_specific` (spec constraint 5).
