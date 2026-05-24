"""Unit tests for Foundation Service.

Mocks:
- services.foundation._embed_text: returns a fixed 1536-dim vector
- AsyncSession: mock with side_effect for each execute() call

No live DB or OpenAI calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.foundation import BrandContext, BrandContextTaskType
from services.foundation import BrandContextError, get_brand_context, ingest_sample


FAKE_EMBEDDING: List[float] = [0.01] * 1536
FAKE_LOC_ID = "00000000-0000-0000-0000-000000000001"


def _make_blueprint_mapping() -> MagicMock:
    row = MagicMock()
    row.get = lambda key, default=None: {
        "full_name": "JP Fluellen",
        "market_city": "Springfield, MO",
        "niche_primary": "investor-agent",
        "audience_primary": "relocation buyers",
        "one_liner": "The anti-We-Buy-Ugly-Houses.",
        "differentiators": ["empathy-first", "investor edge"],
        "pillars": [],
        "voice_tone": ["direct", "confident"],
        "voice_cadence": "punchy",
        "pov": "first-person",
        "humor_level": "moderate",
        "vocabulary_yes": ["y'all", "let's go"],
        "vocabulary_no": ["leverage", "synergy"],
    }.get(key, default)
    return row


def _mock_session_for_brand_context(has_blueprint: bool = True) -> AsyncMock:
    """Build a mock AsyncSession for get_brand_context() calls."""
    session = AsyncMock()

    # 1. Blueprint query result
    bp_mapping_result = MagicMock()
    if has_blueprint:
        bp_mapping_result.mappings.return_value.first.return_value = _make_blueprint_mapping()
    else:
        bp_mapping_result.mappings.return_value.first.return_value = None

    # 2. Samples similarity query
    sample_row = {
        "text": "Just sold a house in Rountree — here's what I learned.",
        "source": "social_approved",
        "weight": 1.0,
        "platform": "linkedin",
        "similarity": 0.87,
    }
    samples_result = MagicMock()
    samples_result.mappings.return_value.all.return_value = [sample_row] if has_blueprint else []

    # 3. Score query
    score_row = MagicMock()
    score_row.score = 0.78
    score_row.computed_at = datetime(2026, 5, 24)
    score_result = MagicMock()
    score_result.first.return_value = score_row if has_blueprint else None

    # 4. Count query
    count_result = MagicMock()
    count_result.scalar.return_value = 42

    if has_blueprint:
        session.execute.side_effect = [
            bp_mapping_result,   # blueprint SELECT
            samples_result,      # similarity SELECT
            score_result,        # score SELECT
            count_result,        # count SELECT
        ]
    else:
        session.execute.return_value = bp_mapping_result

    return session


@pytest.mark.asyncio
async def test_get_brand_context_returns_correct_shape():
    """get_brand_context() must return a typed BrandContext with all required fields."""
    session = _mock_session_for_brand_context(has_blueprint=True)

    with patch("services.foundation._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = FAKE_EMBEDDING

        result = await get_brand_context(
            session=session,
            location_id=FAKE_LOC_ID,
            task_type=BrandContextTaskType.linkedin_post,
            topic="FHA loan limits",
        )

    assert isinstance(result, BrandContext)
    assert result.brand_profile.full_name == "JP Fluellen"
    assert result.brand_profile.market_city == "Springfield, MO"
    assert result.voice_profile.pov == "first-person"
    assert result.vocabulary.use == ["y'all", "let's go"]
    assert len(result.voice_samples) == 1
    assert isinstance(result.voice_samples[0].similarity, float)
    assert result.voice_samples[0].similarity == pytest.approx(0.87)
    assert result.foundation_score == pytest.approx(0.78)
    assert result.metadata.sample_count == 42
    assert "linkedin" in result.metadata.retrieval_query
    assert "FHA loan limits" in result.metadata.retrieval_query
    mock_embed.assert_called_once()


@pytest.mark.asyncio
async def test_get_brand_context_raises_when_no_blueprint():
    """get_brand_context() must raise BrandContextError when blueprint is missing."""
    session = _mock_session_for_brand_context(has_blueprint=False)

    with patch("services.foundation._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = FAKE_EMBEDDING

        with pytest.raises(BrandContextError):
            await get_brand_context(
                session=session,
                location_id=FAKE_LOC_ID,
                task_type=BrandContextTaskType.linkedin_post,
            )

    # Embedding should NOT be called when blueprint is missing (fail fast)
    mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_raises_for_invalid_source():
    """ingest_sample() must raise ValueError for unknown source values."""
    session = AsyncMock()
    session.commit = AsyncMock()

    with patch("services.foundation._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = FAKE_EMBEDDING

        with pytest.raises(ValueError, match="Invalid source"):
            await ingest_sample(
                session=session,
                location_id=FAKE_LOC_ID,
                text_content="This is a test sample.",
                source="invalid_source_type",
            )

    mock_embed.assert_not_called()
