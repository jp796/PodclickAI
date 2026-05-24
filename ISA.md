---
task: "Phase 1 Step 2 — Foundation Service core"
project: PodClick
effort: E3
phase: verify
progress: 36/40
mode: algorithm
started: "2026-05-24"
updated: "2026-05-24"
---

## Problem

PodClick has a Neon Postgres schema with the `voice_samples`, `blueprints`, and `foundation_scores` tables but zero application code to read from or write to them. Any content generator that fires right now would call OpenAI directly with no user voice context — producing the generic boilerplate failure mode that Foundation was built to prevent. Phase 1 is not complete until `get_brand_context()` exists and can be called by every downstream generator.

## Vision

A developer (or curl) POSTs a text sample to `/api/foundation/ingest`, sees a `sample_id` returned in under 2 seconds, then immediately GETs `/api/foundation/samples` and finds that row back — vector populated, source set, weight correct. When `get_brand_context()` is called for the same location, it returns a typed `BrandContext` that includes that sample as a few-shot example alongside the blueprint fields, ready to drop directly into a generation prompt. The Foundation contract is live and proven.

## Out of Scope

This step does not build the generation layer that *consumes* `get_brand_context()` — that's the Content Crew (Phase 5). It does not implement the score calculation cron (weekly background job) — the score endpoint returns the latest stored score, not a live recalculation. It does not build Blueprint intake (that's also Phase 1 but a separate step). It does not implement the chunking pipeline for long transcripts (podcast ingestion, Phase 5) — ingest accepts single text units only. It does not include authentication or multi-tenant JWT routing — TITAN_LOCATION_ID is used for all queries.

## Principles

- Service functions are the authority; routes are thin wrappers — zero business logic in route handlers.
- The embedding call is the only async I/O that doesn't touch the DB; isolate it so it's mockable in tests.
- Every ingested sample must immediately be queryable — no delayed writes, no background jobs for the ingest path.
- The `BrandContextError` exception is the canonical signal for missing-blueprint state; callers must handle it.
- Python 3.9 everywhere: `Optional[T]` not `T | None`, `List[T]` not `list[T]` in type hints.

## Constraints

- Python 3.9 — no `str | None` union syntax; use `Optional[str]` from `typing`.
- OpenAI import pattern: `import openai as _openai` — never bare `import openai` at module level.
- All DB queries use the async SQLAlchemy session from `db.engine.get_db()`.
- Vector similarity queries use raw `text()` SQL with `::vector` cast — pgvector ORM operators are not available in this SQLAlchemy version without the `pgvector.sqlalchemy` operator overloads; use string embedding format `[x1,x2,...,x1536]`.
- `TITAN_LOCATION_ID` from `config.get_current_location_id()` scopes all Phase 1 queries.
- No new pip packages beyond what's already in `requirements.txt` — numpy not allowed; manual float-list serialization for vectors.
- Route prefix: `/api/foundation` — consistent with existing routes in `main.py`.
- Test file uses `pytest` + `pytest-asyncio` + `unittest.mock` — no additional test frameworks.

## Goal

Implement `get_brand_context()`, `ingest_sample()`, and `get_foundation_status()` as async service functions in `services/foundation.py`, backed by Pydantic schemas in `schemas/foundation.py`, exposed via a FastAPI router at `/api/foundation`, and covered by a unit test that mocks the embedding call and DB session and asserts the returned `BrandContext` has the correct shape. The implementation is done when `POST /api/foundation/ingest` stores a real OpenAI embedding in `voice_samples` and `GET /api/foundation/samples` returns that row.

## Criteria

- [x] ISC-1: `schemas/foundation.py` exists at `podcast-studio/schemas/foundation.py`
- [x] ISC-2: `BrandContextTaskType` enum in schemas has all 21 task types from the skill contract
- [x] ISC-3: `BrandContext` Pydantic model has `brand_profile`, `voice_profile`, `vocabulary`, `voice_samples`, `foundation_score`, `metadata` fields
- [x] ISC-4: `VoiceSampleOut` has `text`, `source`, `weight`, `platform`, `similarity` fields
- [x] ISC-5: `BrandProfileOut` has `full_name`, `market_city`, `niche_primary`, `audience_primary`, `one_liner`, `differentiators`, `pillars`
- [x] ISC-6: `VoiceProfileOut` has `tone`, `cadence`, `pov`, `humor_level`
- [x] ISC-7: `VocabularyOut` has `use` and `avoid` fields
- [x] ISC-8: `IngestRequest` has `text`, `source`, `platform`, `topic`, `bucket`, `weight`, `edit_distance`
- [x] ISC-9: `IngestResponse` has `sample_id`, `chunks_created`, `embedding_dims`
- [x] ISC-10: `FoundationStatusResponse` has `location_id`, `sample_count`, `latest_score`, `computed_at`, `has_blueprint`, `is_ready`
- [x] ISC-11: `services/foundation.py` exists at `podcast-studio/services/foundation.py`
- [x] ISC-12: `get_brand_context()` accepts `session`, `location_id`, `task_type`, `topic`, `platform`, `audience`, `additional_context` params
- [x] ISC-13: `get_brand_context()` raises `BrandContextError` when no blueprint found for location
- [x] ISC-14: `get_brand_context()` calls `_embed_text()` exactly once per invocation (verified by mock assert)
- [x] ISC-15: `get_brand_context()` executes vector similarity SQL with `ORDER BY (embedding <=> ...) / weight ASC LIMIT 5`
- [x] ISC-16: `get_brand_context()` returns `BrandContext` instance (isinstance check passes)
- [x] ISC-17: `get_brand_context()` sets `metadata.retrieval_query` containing the task_type string
- [x] ISC-18: `ingest_sample()` accepts `session`, `location_id`, `text`, `source`, `platform`, `topic`, `bucket`, `weight`, `edit_distance`
- [x] ISC-19: `ingest_sample()` calls `_embed_text()` and stores result in `voice_samples.embedding`
- [x] ISC-20: `ingest_sample()` returns `IngestResponse` with correct `embedding_dims=1536`
- [x] ISC-21: `ingest_sample()` validates `source` is one of the 6 allowed values; raises `ValueError` on invalid
- [x] ISC-22: `get_foundation_status()` returns `FoundationStatusResponse` with correct `is_ready` logic
- [x] ISC-23: `_embed_text()` uses `import openai as _openai` pattern (grep confirms no bare `import openai`)
- [x] ISC-24: `routers/foundation.py` exists with a FastAPI `APIRouter`
- [x] ISC-25: `POST /api/foundation/ingest` route exists and calls `ingest_sample()`
- [x] ISC-26: `GET /api/foundation/score` route exists and returns latest foundation score
- [x] ISC-27: `GET /api/foundation/samples` route exists and returns paginated voice samples list
- [x] ISC-28: `GET /api/foundation/status` route exists and returns `FoundationStatusResponse`
- [x] ISC-29: Foundation router is wired into `main.py` via `app.include_router`
- [x] ISC-30: `tests/test_foundation.py` exists with at least one `@pytest.mark.asyncio` test
- [x] ISC-31: Unit test mocks `_embed_text` and DB session; assert `get_brand_context()` returns `BrandContext`
- [x] ISC-32: Unit test asserts `voice_samples` list is non-empty and `similarity` field is a float
- [x] ISC-33: Unit test asserts `metadata.retrieval_query` contains the topic string
- [x] ISC-34: `venv/bin/pytest tests/test_foundation.py -v` exits 0 (all tests pass)
- [DEFERRED-VERIFY] ISC-35: `POST /api/foundation/ingest` live call with real OpenAI key returns HTTP 200 with `sample_id` — Neon credentials need rotation; follow-up task required
- [DEFERRED-VERIFY] ISC-36: After live ingest, `GET /api/foundation/samples` returns the ingested row — depends on ISC-35
- [x] ISC-37: Anti: No bare `import openai` at module level in `services/foundation.py` (grep returns empty)
- [x] ISC-38: Anti: No business logic in route handlers — route bodies are ≤8 lines each (grep/read check)
- [x] ISC-39: Anti: `get_brand_context()` does NOT produce output when blueprint is missing — raises exception instead (unit test for missing-blueprint path)
- [x] ISC-40: Anti: `str | None` union syntax does not appear in any new Python file (grep returns empty)

## Test Strategy

| ISC | type | check | threshold | tool |
|-----|------|-------|-----------|------|
| ISC-1 | file-exists | `fd foundation.py schemas/` | present | Bash |
| ISC-2 | content | `grep -c "= " schemas/foundation.py` on enum | ≥21 lines | Grep |
| ISC-3 | content | `grep "brand_profile\|voice_samples\|foundation_score" schemas/foundation.py` | all 6 fields present | Grep |
| ISC-4..10 | content | `grep` for each field name in schemas file | each field present | Grep |
| ISC-11 | file-exists | `fd foundation.py services/` | present | Bash |
| ISC-12 | content | `grep "def get_brand_context" services/foundation.py` | signature present | Grep |
| ISC-13..17 | unit-test | pytest mock test for missing blueprint path | raises BrandContextError | Bash |
| ISC-18..22 | content+unit | grep + pytest ingest mock test | pass | Grep/Bash |
| ISC-23 | grep | `grep "import openai as _openai" services/foundation.py` | 1 match | Grep |
| ISC-24..29 | content | grep for router registration in main.py | present | Grep |
| ISC-30..34 | test | `venv/bin/pytest tests/test_foundation.py -v` | exit 0 | Bash |
| ISC-35..36 | live | curl + psql count | HTTP 200, row present | Bash |
| ISC-37..40 | grep | pattern absence checks | 0 matches | Grep |

## Features

| name | description | satisfies | depends_on | parallelizable |
|------|-------------|-----------|------------|----------------|
| schemas | All Pydantic models and enums in `schemas/foundation.py` | ISC-1..10 | — | false |
| embed_helper | `_embed_text(text) -> List[float]` private async helper using `_openai` pattern | ISC-23 | schemas | false |
| get_brand_context | Full retrieval service function — blueprint load, query build, embed, vector SQL, assemble BrandContext | ISC-12..17 | embed_helper | false |
| ingest_sample | Ingest service function — validate, embed, insert VoiceSample row | ISC-18..22 | embed_helper | false |
| get_foundation_status | Status service function — counts, latest score, blueprint presence, is_ready flag | ISC-22 | schemas | false |
| routes | FastAPI router with 4 endpoints, wired into main.py | ISC-24..29 | get_brand_context, ingest_sample, get_foundation_status | false |
| unit_tests | pytest-asyncio tests with mocked embedding and DB | ISC-30..34, ISC-39 | all above | false |
| live_verification | curl ingest + psql count + samples GET | ISC-35..36 | routes | false |
