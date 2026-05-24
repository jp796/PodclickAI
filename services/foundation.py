"""PodClick — Foundation Service.

The single source of truth for all brand context retrieval and voice sample ingestion.
Every content generator in PodClick calls get_brand_context() before touching an LLM.

Import openai as _openai — never at top level without alias.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

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
              AND (:platform IS NULL OR platform IS NULL OR platform = :platform)
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
