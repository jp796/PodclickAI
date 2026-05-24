"""PodClick — Foundation API routes.

Route handlers are intentionally thin: validate input, call service, return output.
All business logic lives in services/foundation.py.
"""
from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_current_location_id
from db.engine import get_db
from schemas.foundation import (
    BrandContext,
    BrandContextTaskType,
    FoundationStatusResponse,
    IngestRequest,
    IngestResponse,
    VoiceSampleOut,
)
from services.foundation import (
    BrandContextError,
    get_brand_context,
    get_foundation_status,
    ingest_sample,
)

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse, summary="Ingest a voice sample into the Foundation")
async def route_ingest(
    body: IngestRequest,
    session: AsyncSession = Depends(get_db),
) -> IngestResponse:
    location_id = get_current_location_id()
    try:
        return await ingest_sample(
            session=session,
            location_id=location_id,
            text_content=body.text,
            source=body.source,
            platform=body.platform,
            topic=body.topic,
            bucket=body.bucket,
            weight=body.weight,
            edit_distance=body.edit_distance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/status", response_model=FoundationStatusResponse, summary="Foundation readiness status")
async def route_status(
    session: AsyncSession = Depends(get_db),
) -> FoundationStatusResponse:
    location_id = get_current_location_id()
    return await get_foundation_status(session=session, location_id=location_id)


@router.get("/score", summary="Latest foundation score")
async def route_score(
    session: AsyncSession = Depends(get_db),
) -> dict:
    location_id = get_current_location_id()
    status = await get_foundation_status(session=session, location_id=location_id)
    return {
        "location_id": location_id,
        "score": status.latest_score,
        "computed_at": status.computed_at,
        "sample_count": status.sample_count,
    }


@router.get("/samples", response_model=List[VoiceSampleOut], summary="List voice samples (recent, no vectors)")
async def route_samples(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> List[VoiceSampleOut]:
    location_id = get_current_location_id()
    result = await session.execute(
        text("""
            SELECT text, source, weight, platform, 0.0 AS similarity
            FROM voice_samples
            WHERE location_id = :loc_id AND excluded = false
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"loc_id": location_id, "limit": limit, "offset": offset},
    )
    rows = result.mappings().all()
    return [
        VoiceSampleOut(
            text=row["text"],
            source=row["source"],
            weight=float(row["weight"]),
            platform=row["platform"],
            similarity=float(row["similarity"]),
        )
        for row in rows
    ]
