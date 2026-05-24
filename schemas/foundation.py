"""PodClick — Pydantic schemas for the Foundation Service."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


class BrandContextTaskType(str, Enum):
    """All task types that can request brand context. From foundation-retrieval SKILL."""
    linkedin_post = "linkedin_post"
    facebook_post = "facebook_post"
    instagram_caption = "instagram_caption"
    instagram_first_comment = "instagram_first_comment"
    tiktok_caption = "tiktok_caption"
    youtube_short_caption = "youtube_short_caption"
    x_post = "x_post"
    episode_title = "episode_title"
    episode_description = "episode_description"
    show_notes = "show_notes"
    clip_caption = "clip_caption"
    thumbnail_text = "thumbnail_text"
    hashtag_set = "hashtag_set"
    guest_outreach_email = "guest_outreach_email"
    guest_asset_email = "guest_asset_email"
    sponsor_pitch = "sponsor_pitch"
    podcast_script_outline = "podcast_script_outline"
    podcast_intro_script = "podcast_intro_script"
    podcast_outro_script = "podcast_outro_script"
    scout_remix_script = "scout_remix_script"
    brick_chat_response = "brick_chat_response"


class VoiceSampleOut(BaseModel):
    text: str
    source: str
    weight: float
    platform: Optional[str] = None
    similarity: float


class BrandProfileOut(BaseModel):
    full_name: Optional[str] = None
    market_city: Optional[str] = None
    niche_primary: Optional[str] = None
    audience_primary: Optional[str] = None
    one_liner: Optional[str] = None
    differentiators: Optional[List[str]] = None
    pillars: Optional[List[dict]] = None


class VoiceProfileOut(BaseModel):
    tone: Optional[List[str]] = None
    cadence: Optional[str] = None
    pov: Optional[str] = None
    humor_level: Optional[str] = None


class VocabularyOut(BaseModel):
    use: Optional[List[str]] = None
    avoid: Optional[List[str]] = None


class RetrievalMetadata(BaseModel):
    retrieval_query: str
    sample_count: int
    retrieved_at: datetime


class BrandContext(BaseModel):
    brand_profile: BrandProfileOut
    voice_profile: VoiceProfileOut
    vocabulary: VocabularyOut
    voice_samples: List[VoiceSampleOut]
    foundation_score: float
    metadata: RetrievalMetadata


# ── Ingest ───────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    text: str
    source: str   # validated in service against VALID_SOURCES
    platform: Optional[str] = None
    topic: Optional[str] = None
    bucket: Optional[str] = None
    weight: Optional[float] = None
    edit_distance: Optional[float] = None


class IngestResponse(BaseModel):
    sample_id: str
    chunks_created: int
    embedding_dims: int


# ── Status ────────────────────────────────────────────────────────────────────

class FoundationStatusResponse(BaseModel):
    location_id: str
    sample_count: int
    latest_score: Optional[float] = None
    computed_at: Optional[datetime] = None
    has_blueprint: bool
    is_ready: bool   # True when sample_count >= 5 and has_blueprint
