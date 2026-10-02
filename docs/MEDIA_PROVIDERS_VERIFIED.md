# Media Providers — Verified Facts

> Replaces `AGENTS_HUB_SPEC.md` §4.6 ("What I could not verify") for provider items. Facts below were supplied by the spec owner as verified (2026-10-01). This document does not claim live calls were run from this repo. Where the spec's earlier assumptions (§4.3, §4.4, §4.5) conflict with a fact here, this document wins.

## ElevenLabs (text-to-speech)

- Endpoint: `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`
- Auth: header `xi-api-key: <key>`
- Current model: `eleven_v4`. Older `eleven_v3` and `eleven_multilingual_v2` still work.
- Response: synchronous mp3 body.

Spec corrections: §4.3 and §4.5 assumed default model `eleven_multilingual_v2`; the current model is `eleven_v4`, so `ELEVENLABS_MODEL_ID` should default to it. This note does not settle the `with-timestamps` response field names or `/v1/user/subscription` fields; those remain unconfirmed in §4.3 until checked.

## Higgsfield (video)

- Base URL: `https://api.higgsfield.ai`
- Auth: `Authorization: Key ID:SECRET` (a key pair, so `HIGGSFIELD_API_KEY` and `HIGGSFIELD_API_SECRET` are both needed).
- Submit: `POST /{model_id}` returns `request_id` and `status_url`.
- Poll: use `status_url` with backoff. A completed job gives `video.url`.
- Retention: outputs are kept about 7 days, so download immediately into the job output dir and never store the provider URL as the deliverable.
- Rate/credit limits: over-limit returns HTTP 400, not 429. The adapter must treat a 400 with a limit/credit body as `ProviderQuotaError`, not as a validation failure to ignore.
- Optional webhook: `hf_webhook`.
- Speak/Soul endpoint ids are NOT documented for the API. Talking-head video is done through `wan/v2.7/image-to-video` with `image_url` and `audio_url` inputs.

Spec corrections: §4.4 assumed the surface was unverifiable and might be empty; it is verified as above. Higgsfield is no longer assumed to expose no avatar capability, but the avatar path is image-to-video with driving audio, not a dedicated Speak/Soul endpoint.

## HeyGen (fallback avatar provider)

- Auth: header `X-Api-Key`
- Endpoint: `/v2/video/generate`
- Role: fallback avatar provider if the Higgsfield talking-head path is unavailable.

## Submagic (captions)

- Base: `api.submagic.co/v1`
- Auth: header `x-api-key`
- Role: caption generation.

## Meta Muse (text + image)

Facts supplied by the spec owner (verified 2026-10-02). Adapter: `services/media/muse.py`.

- Base URL: `https://api.meta.ai/v1`
- Auth: header `Authorization: Bearer <key>`. Key from dev.meta.ai, setting `meta_model_api_key` (env `MODEL_API_KEY` or `META_MODEL_API_KEY`).
- Text: OpenAI-compatible `POST /v1/chat/completions` (Responses and Anthropic Messages formats also exist; not used).
- Models: `muse-spark-1.3` (default; text out; image/video/document in; 1M context; tool calling; streaming), `muse-spark-1.2`, `muse-spark-1.1`.
- `-contributor` variants (e.g. `muse-spark-1.3-contributor`) are cheaper, but Meta may use prompts and outputs for training.
- Image model: `muse-image`, $0.01 per image, 150 rpm. Standard limit 3000 rpm. US-only public preview. Reasoning tokens are billed as output.
- Acceptable use: outputs must not be presented as human-written; keep any provenance watermark/metadata on `muse-image` outputs; no fake reviews or engagement; no impersonating a real person's voice or face.

How the adapter applies them:

- Missing key: `needs_setup` (`ProviderNotConfigured`), zero network calls.
- Standard model is the default. Contributor needs `META_MUSE_ALLOW_CONTRIBUTOR=1` AND a per-call `contributor=True` (or a `-contributor` model id). `pii=True` refuses an explicit contributor request and downgrades a contributor default to standard. Runners must pass `pii=True` for any client/lead data.
- Caps before any call: `META_MUSE_MAX_TOKENS` (default 2048), `META_MUSE_MAX_TOKENS_CAP` (8192), `META_MUSE_MAX_PROMPT_CHARS` (100000); timeout `META_MUSE_TIMEOUT_S` (120).
- 401/403 give a key message; 403 with region wording gives a US-only message. No exception text or key is ever put in a user message. A timeout after send is `ProviderUncertainError` and is never retried; 429 backs off 5/15/45 s; 5xx is not retried.
- Runner API: `await MuseProvider().complete(prompt, system=None, pii=False, ...)` returns text; `complete_detailed` adds model and usage; `generate_image` returns `MuseImage(data, mime, model, provenance)`; `generate(GenerationRequest("text_to_image", ...), dest)` writes the exact bytes plus `<file>.provenance.json`.
- Every result carries `ai_generated=True` and a disclosure string; callers must keep it.
- Registry: provider `muse`, capability `text_to_image`, requirement name `muse`. There is no `text` capability in `base.CAPABILITIES`, so text is reached by calling `complete()` directly.

Not verified: the image route. The adapter assumes OpenAI-compatible `POST /v1/images/generations` with `{model, prompt, n: 1}` and a response of `data[0].b64_json` or `data[0].url`. Confirm against dev.meta.ai before relying on it. Which response fields carry the provenance marker is also unconfirmed, so the adapter keeps every non-pixel field and any provenance-looking header.

## Still open (not covered by the facts above)

1. ElevenLabs `with-timestamps` response field names and `/v1/user/subscription` field names (spec §4.3).
2. Whether `POST /api/projects/{id}/auto-edit` blocks until the re-encode finishes (spec §4.6 item 3).
3. `task_type` enum values for the new `get_brand_context` calls (spec §4.6 item 4).
4. Whether the per-project Ship It/autopilot lock is acquirable from outside its module (spec §4.6 item 5).
5. Pricing units and likeness/content policy for each provider.
