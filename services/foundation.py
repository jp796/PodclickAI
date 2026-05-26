"""PodClick — Foundation Service.

The single source of truth for all brand context retrieval and voice sample ingestion.
Every content generator in PodClick calls get_brand_context() before touching an LLM.

Import aliases:
  import openai as _openai       — never bare at module level
  import anthropic as _anthropic — never bare at module level
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import anthropic as _anthropic
import openai as _openai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from schemas.foundation import (
    BrandContext,
    BrandContextTaskType,
    BrandProfileOut,
    FoundationStatusResponse,
    IngestResponse,
    RetrievalMetadata,
    VocabularyOut,
    VoiceProfileOut,
    VoiceSampleOut,
)

VALID_SOURCES = (
    "podcast",
    "social_approved",
    "social_edited",
    "written_from_scratch",
    "brand_studio",
    "historical",
)

# Platform implied by task type — used when platform param not explicitly passed
_TASK_PLATFORM_MAP = {
    BrandContextTaskType.linkedin_post: "linkedin",
    BrandContextTaskType.facebook_post: "facebook",
    BrandContextTaskType.instagram_caption: "instagram",
    BrandContextTaskType.instagram_first_comment: "instagram",
    BrandContextTaskType.tiktok_caption: "tiktok",
    BrandContextTaskType.youtube_short_caption: "youtube",
    BrandContextTaskType.x_post: "x",
}


class BrandContextError(Exception):
    """Raised when Foundation cannot produce brand context (missing blueprint, etc.)."""


# ── Private helpers ───────────────────────────────────────────────────────────

async def _embed_text(text_to_embed: str) -> List[float]:
    """Embed a string using OpenAI text-embedding-3-small (1536 dims).

    Uses the _openai alias — never import openai bare at module level.
    """
    _openai.api_key = settings.openai_api_key
    client = _openai.AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=text_to_embed,
    )
    return response.data[0].embedding


def _serialize_embedding(embedding: List[float]) -> str:
    """Serialize a float list to pgvector text format: '[x1,x2,...]'."""
    return "[" + ",".join(str(round(v, 8)) for v in embedding) + "]"


def _build_retrieval_query(
    task_type: BrandContextTaskType,
    topic: Optional[str],
    platform: Optional[str],
    audience: Optional[str],
) -> str:
    """Build the natural-language query text for similarity search."""
    parts: List[str] = []
    if platform:
        parts.append(f"{platform} {task_type.value.replace('_', ' ')}")
    else:
        parts.append(task_type.value.replace("_", " "))
    if topic:
        parts.append(f"about {topic}")
    if audience:
        parts.append(f"for {audience}")
    return " ".join(parts)


# ── Public service functions ──────────────────────────────────────────────────

async def get_brand_context(
    session: AsyncSession,
    location_id: str,
    task_type: BrandContextTaskType,
    topic: Optional[str] = None,
    platform: Optional[str] = None,
    audience: Optional[str] = None,
    additional_context: Optional[str] = None,
) -> BrandContext:
    """Retrieve brand context for a content generation task.

    This is the central contract: EVERY user-attributed LLM generation must call this first.
    Raises BrandContextError if no blueprint exists for the location.
    """
    # 1. Load blueprint
    bp_result = await session.execute(
        text("SELECT * FROM blueprints WHERE location_id = :loc_id"),
        {"loc_id": location_id},
    )
    blueprint = bp_result.mappings().first()
    if blueprint is None:
        raise BrandContextError(
            f"No blueprint found for location {location_id}. "
            "Complete Blueprint intake before generating content."
        )

    # 2. Build retrieval query
    platform_filter = platform or _TASK_PLATFORM_MAP.get(task_type)
    retrieval_query = _build_retrieval_query(task_type, topic, platform_filter, audience)

    # 3. Embed
    query_embedding = await _embed_text(retrieval_query)
    emb_str = _serialize_embedding(query_embedding)

    # 4. Vector similarity search
    samples_result = await session.execute(
        text("""
            SELECT text, source, weight, platform,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similarity
            FROM voice_samples
            WHERE location_id = :loc_id
              AND excluded = false
              AND (CAST(:platform AS text) IS NULL OR platform IS NULL OR platform = CAST(:platform AS text))
            ORDER BY (embedding <=> CAST(:emb AS vector)) / GREATEST(weight, 0.001) ASC
            LIMIT 5
        """),
        {
            "emb": emb_str,
            "loc_id": location_id,
            "platform": platform_filter,
        },
    )
    raw_samples = samples_result.mappings().all()
    voice_samples = [
        VoiceSampleOut(
            text=row["text"],
            source=row["source"],
            weight=float(row["weight"]),
            platform=row["platform"],
            similarity=float(row["similarity"]),
        )
        for row in raw_samples
    ]

    # 5. Latest foundation score
    score_result = await session.execute(
        text("""
            SELECT score, computed_at FROM foundation_scores
            WHERE location_id = :loc_id
            ORDER BY computed_at DESC LIMIT 1
        """),
        {"loc_id": location_id},
    )
    score_row = score_result.first()
    foundation_score = float(score_row.score) if score_row else 0.0

    # 6. Total sample count (for metadata)
    count_result = await session.execute(
        text("SELECT count(*) FROM voice_samples WHERE location_id = :loc_id AND excluded = false"),
        {"loc_id": location_id},
    )
    sample_count = count_result.scalar() or 0

    return BrandContext(
        brand_profile=BrandProfileOut(
            full_name=blueprint.get("full_name"),
            market_city=blueprint.get("market_city"),
            niche_primary=blueprint.get("niche_primary"),
            audience_primary=blueprint.get("audience_primary"),
            one_liner=blueprint.get("one_liner"),
            differentiators=blueprint.get("differentiators"),
            pillars=blueprint.get("pillars") or [],
        ),
        voice_profile=VoiceProfileOut(
            tone=blueprint.get("voice_tone"),
            cadence=blueprint.get("voice_cadence"),
            pov=blueprint.get("pov"),
            humor_level=blueprint.get("humor_level"),
        ),
        vocabulary=VocabularyOut(
            use=blueprint.get("vocabulary_yes"),
            avoid=blueprint.get("vocabulary_no"),
        ),
        voice_samples=voice_samples,
        foundation_score=foundation_score,
        metadata=RetrievalMetadata(
            retrieval_query=retrieval_query,
            sample_count=int(sample_count),
            retrieved_at=datetime.utcnow(),
        ),
    )


async def ingest_sample(
    session: AsyncSession,
    location_id: str,
    text_content: str,
    source: str,
    platform: Optional[str] = None,
    topic: Optional[str] = None,
    bucket: Optional[str] = None,
    weight: Optional[float] = None,
    edit_distance: Optional[float] = None,
) -> IngestResponse:
    """Embed and store a voice sample in the Foundation vector store.

    Returns the new sample_id and embedding dimensions.
    Raises ValueError if source is not one of VALID_SOURCES.
    """
    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid source '{source}'. Must be one of: {', '.join(VALID_SOURCES)}"
        )

    # Embed
    embedding = await _embed_text(text_content)
    emb_str = _serialize_embedding(embedding)

    effective_weight = weight if weight is not None else 1.0
    sample_id = str(uuid.uuid4())

    await session.execute(
        text("""
            INSERT INTO voice_samples
                (id, location_id, text, embedding, source, platform, topic, bucket, weight, edit_distance)
            VALUES
                (:id, :loc_id, :text, CAST(:emb AS vector), :source, :platform, :topic, :bucket, :weight, :edit_distance)
        """),
        {
            "id": sample_id,
            "loc_id": location_id,
            "text": text_content,
            "emb": emb_str,
            "source": source,
            "platform": platform,
            "topic": topic,
            "bucket": bucket,
            "weight": effective_weight,
            "edit_distance": edit_distance,
        },
    )
    await session.commit()

    return IngestResponse(
        sample_id=sample_id,
        chunks_created=1,
        embedding_dims=len(embedding),
    )


async def get_foundation_status(
    session: AsyncSession,
    location_id: str,
) -> FoundationStatusResponse:
    """Return the current Foundation status for a location."""
    # Blueprint presence
    bp_result = await session.execute(
        text("SELECT id FROM blueprints WHERE location_id = :loc_id"),
        {"loc_id": location_id},
    )
    has_blueprint = bp_result.first() is not None

    # Sample count
    count_result = await session.execute(
        text("SELECT count(*) FROM voice_samples WHERE location_id = :loc_id AND excluded = false"),
        {"loc_id": location_id},
    )
    sample_count = int(count_result.scalar() or 0)

    # Latest score
    score_result = await session.execute(
        text("""
            SELECT score, computed_at FROM foundation_scores
            WHERE location_id = :loc_id
            ORDER BY computed_at DESC LIMIT 1
        """),
        {"loc_id": location_id},
    )
    score_row = score_result.first()

    return FoundationStatusResponse(
        location_id=location_id,
        sample_count=sample_count,
        latest_score=float(score_row.score) if score_row else None,
        computed_at=score_row.computed_at if score_row else None,
        has_blueprint=has_blueprint,
        is_ready=sample_count >= 5 and has_blueprint,
    )


async def assert_foundation_ready(
    session: AsyncSession,
    location_id: str,
) -> None:
    """Gate helper — raises BrandContextError if Foundation has fewer than 5 samples.

    Call this as the first line of any generator that produces user-attributed content.
    Tier 'not_ready' (0-4 samples) → blocked.
    Tier 'thin' (5-14 samples) → allowed but callers should surface a warning.
    """
    count_result = await session.execute(
        text(
            "SELECT count(*) FROM voice_samples "
            "WHERE location_id = :loc_id AND excluded = false"
        ),
        {"loc_id": location_id},
    )
    sample_count = int(count_result.scalar() or 0)
    if sample_count < 5:
        raise BrandContextError(
            "foundation_not_ready: Foundation has fewer than 5 samples. "
            "Pour foundation before generating content."
        )


def _extract_json(text_content: str) -> str:
    """Strip markdown code fences and return raw JSON string."""
    # Remove ```json ... ``` or ``` ... ``` wrappers
    fenced = re.sub(r"^```(?:json)?\s*", "", text_content.strip(), flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced.strip())
    return fenced.strip()


async def auto_generate_blueprint(
    session: AsyncSession,
    location_id: str,
    api_key: str,
) -> Dict[str, Any]:
    """Analyse Foundation voice samples and auto-populate the Blueprint.

    Precondition: >= 5 non-excluded samples (raises BrandContextError otherwise).
    Uses a single claude-sonnet-4-6 call at temperature 0.3, max_tokens 2000.
    DOES NOT ingest the output into voice_samples — it is a Blueprint, not a sample.

    Returns the parsed draft dict. The caller is responsible for the confirm/save flow;
    this function writes to the blueprints table immediately on success.

    Raises BrandContextError on insufficient samples.
    Raises ValueError if the LLM returns unparseable JSON.
    """
    # 1. Gate: must have at least 5 samples
    count_result = await session.execute(
        text(
            "SELECT count(*) FROM voice_samples "
            "WHERE location_id = :loc_id AND excluded = false"
        ),
        {"loc_id": location_id},
    )
    sample_count = int(count_result.scalar() or 0)
    if sample_count < 5:
        raise BrandContextError(
            "foundation_not_ready: Need at least 5 voice samples to build a Blueprint. "
            f"Current count: {sample_count}."
        )

    # 2. Check if blueprint already exists (warn-before-overwrite flag)
    bp_check = await session.execute(
        text(
            "SELECT id, voice_tone, audience_primary FROM blueprints "
            "WHERE location_id = :loc_id"
        ),
        {"loc_id": location_id},
    )
    existing = bp_check.mappings().first()
    already_exists = existing is not None and (
        existing.get("voice_tone") or existing.get("audience_primary")
    )

    # 3. Load up to 30 most recent non-excluded samples
    samples_result = await session.execute(
        text(
            "SELECT text FROM voice_samples "
            "WHERE location_id = :loc_id AND excluded = false "
            "ORDER BY created_at DESC LIMIT 30"
        ),
        {"loc_id": location_id},
    )
    sample_texts = [row["text"] for row in samples_result.mappings().all()]
    all_text = "\n\n---\n\n".join(sample_texts)

    # 4. Single claude-sonnet-4-6 call — verbatim prompt from foundation-intake SKILL
    client = _anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        temperature=0.3,
        messages=[
            {
                "role": "user",
                "content": (
                    "Analyze the following voice samples from one person and extract "
                    "their brand profile. Return strict JSON.\n\n"
                    f"Samples:\n{all_text}\n\n"
                    "Return JSON with these fields:\n"
                    '- tone: array of 3-5 adjectives describing tone '
                    '(e.g., ["direct", "warm", "no-fluff"])\n'
                    "- cadence: one-sentence description of their speaking/writing rhythm\n"
                    '- pov: "first-person" or "second-person" or "third-person"\n'
                    '- humor_level: "none" or "dry, sparingly" or "frequent" or "playful"\n'
                    "- vocabulary_yes: array of 5-10 phrases or words they use naturally "
                    "(verbatim from samples)\n"
                    "- vocabulary_no: empty array (Brick will learn this over time from edits)\n"
                    "- audience_primary: their main audience as described in samples\n"
                    "- audience_pain_points: array of pain points they reference\n"
                    "- pillars: array of 3-5 content pillars detected with "
                    "{name, weight (0-1), examples} — weights sum to 1.0\n\n"
                    "Do not invent details not in the samples. "
                    "If something can't be inferred, leave it null or empty."
                ),
            }
        ],
    )

    # 5. Parse JSON — strip code fences if present
    raw = message.content[0].text if message.content else ""
    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"Blueprint auto-generation returned invalid JSON: {exc}\nRaw: {raw[:400]}"
        )

    # 6. UPSERT to blueprints — never insert into voice_samples
    bp_id = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO blueprints
                (id, location_id, voice_tone, voice_cadence, pov, humor_level,
                 vocabulary_yes, vocabulary_no, audience_primary, audience_pain_points,
                 pillars, updated_at)
            VALUES
                (:id, :loc_id, :tone, :cadence, :pov, :humor_level,
                 :vocab_yes, :vocab_no, :audience, :pain_points,
                 CAST(:pillars AS jsonb), now())
            ON CONFLICT (location_id) DO UPDATE SET
                voice_tone          = EXCLUDED.voice_tone,
                voice_cadence       = EXCLUDED.voice_cadence,
                pov                 = EXCLUDED.pov,
                humor_level         = EXCLUDED.humor_level,
                vocabulary_yes      = EXCLUDED.vocabulary_yes,
                vocabulary_no       = EXCLUDED.vocabulary_no,
                audience_primary    = EXCLUDED.audience_primary,
                audience_pain_points = EXCLUDED.audience_pain_points,
                pillars             = EXCLUDED.pillars,
                updated_at          = now()
        """),
        {
            "id": bp_id,
            "loc_id": location_id,
            "tone": parsed.get("tone") or [],
            "cadence": parsed.get("cadence"),
            "pov": parsed.get("pov", "first-person"),
            "humor_level": parsed.get("humor_level"),
            "vocab_yes": parsed.get("vocabulary_yes") or [],
            "vocab_no": parsed.get("vocabulary_no") or [],
            "audience": parsed.get("audience_primary"),
            "pain_points": parsed.get("audience_pain_points") or [],
            "pillars": json.dumps(parsed.get("pillars") or []),
        },
    )
    await session.commit()

    parsed["_already_existed"] = already_exists
    parsed["_sample_count"] = sample_count
    return parsed
