"""PodClick — Blueprint API routes.

POST /api/blueprint/auto-generate  — analyse Foundation samples → UPSERT Blueprint draft.

Route handlers are intentionally thin: validate input, call service, return output.
All business logic lives in services/foundation.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_current_location_id, settings
from db.engine import get_db
from services.foundation import BrandContextError, auto_generate_blueprint

router = APIRouter()


@router.post(
    "/auto-generate",
    summary="Auto-generate Blueprint from Foundation voice samples",
)
async def route_auto_generate(
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Analyse Foundation voice samples and auto-populate the Blueprint.

    Preconditions:
    - >= 5 non-excluded voice samples (else 422 foundation_not_ready)
    - ANTHROPIC_API_KEY set in environment (else 500)

    Returns:
    - Parsed Blueprint draft (tone, cadence, pov, humor_level, vocabulary, audience, pillars)
    - _already_existed: bool — true when a populated Blueprint already exists (warn before overwrite)
    - _sample_count: int — number of samples used

    Does NOT ingest any data back into voice_samples.
    """
    location_id = get_current_location_id()

    api_key = settings.anthropic_api_key
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Add it to .env and restart.",
        )

    try:
        result = await auto_generate_blueprint(
            session=session,
            location_id=location_id,
            api_key=api_key,
        )
        return result
    except BrandContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
