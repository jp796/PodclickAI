# Walk-through entries for The Crew

Ready to paste into `frontend/walkthrough.html` (that file was off limits for the
branch that wrote this). Both objects use bare keys for plain action types, so the
colon-bearing keys are quoted. `tests/test_punch_list_coverage.py` accepts both forms.

The `agent_run:*` payload is `{"input": {...agent fields...}, "summary": "..."}`,
which is what `_dispatch_action` reads. `agent_commit` carries
`{"job_id", "agent_id", "summary"}`.

## 1. Paste into `ACTION_CONSEQUENCE`

Put it after the `suggest_post_idea` line, before the closing `};`.

```js
  agent_commit: '\u26a0\ufe0f Applies a finished Crew work order to the app (posts, schedule or clips, per the agent). Runs once per work order; approving it again returns the same result.',
  'agent_run:market_scout': 'Starts Market Scout on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: YouTube quota.',
  'agent_run:trend_radar': 'Starts Trend Radar on a new work order. Nothing is committed or published; any commit is a separate approval.',
  'agent_run:pillar_planner': 'Starts Pillar Planner on a new work order. Nothing is committed or published; any commit is a separate approval.',
  'agent_run:click_studio': 'Starts Click Studio on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: OpenAI transcription, Pexels stock footage (b-roll only).',
  'agent_run:draftsman': 'Starts Draftsman on a new work order. Nothing is committed or published; any commit is a separate approval.',
  'agent_run:thumbnail': 'Starts Painter on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: image generation (only if an image provider is connected).',
  'agent_run:voiceover': 'Starts Voiceover on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: ElevenLabs characters.',
  'agent_run:avatar_video': 'Starts Avatar Video on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: ElevenLabs characters, Higgsfield credits.',
  'agent_run:home_tour': 'Starts Home Tour Video on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: Higgsfield credits (if connected), ElevenLabs characters (if connected).',
  'agent_run:market_reel': 'Starts Market Update Reel on a new work order. Nothing is committed or published; any commit is a separate approval. \u26a0\ufe0f Spends: ElevenLabs characters (if connected).',
  'agent_run:content_scheduler': 'Starts Content Scheduler on a new work order. Nothing is committed or published; any commit is a separate approval.',
  'agent_run:repurpose': 'Starts Clip Dispatcher on a new work order. Nothing is committed or published; any commit is a separate approval.',
```

## 2. Paste into `ACTION_FIELDS`

Put it after the `suggest_post_idea` line, before the closing `};`.

```js
  agent_commit: ['job_id', 'agent_id', 'summary'],
  'agent_run:market_scout': ['input', 'summary'],
  'agent_run:trend_radar': ['input', 'summary'],
  'agent_run:pillar_planner': ['input', 'summary'],
  'agent_run:click_studio': ['input', 'summary'],
  'agent_run:draftsman': ['input', 'summary'],
  'agent_run:thumbnail': ['input', 'summary'],
  'agent_run:voiceover': ['input', 'summary'],
  'agent_run:avatar_video': ['input', 'summary'],
  'agent_run:home_tour': ['input', 'summary'],
  'agent_run:market_reel': ['input', 'summary'],
  'agent_run:content_scheduler': ['input', 'summary'],
  'agent_run:repurpose': ['input', 'summary'],
```

The twelve `agent_run:*` keys come from `services/agents/registry.py`. A new agent
spec adds a tier-map key automatically and will fail the coverage test until it
gets a line in both objects.
