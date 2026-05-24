"""
PodClick — SQLAlchemy ORM models.

Phase 1 tables only (identity/tenancy + Foundation).
Later phases append to this file.

Import order mirrors the FK dependency graph so Alembic
can autogenerate correct migration order.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── 4.1 Identity & tenancy ───────────────────────────────────────────────────

class Account(Base):
    """Top-level account — a GHL agency or a standalone individual user."""

    __tablename__ = "accounts"

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ghl_company_id: Column = Column(Text, unique=True, nullable=True)
    type: Column = Column(
        Text,
        CheckConstraint("type IN ('agency', 'individual')", name="ck_accounts_type"),
        nullable=False,
    )
    white_label_config: Column = Column(JSONB, server_default=text("'{}'"))
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    locations = relationship("Location", back_populates="account", cascade="all, delete-orphan")


class Location(Base):
    """
    Each location = one realtor's workspace. The data tenant boundary.
    Every other table has location_id FK pointing here.
    """

    __tablename__ = "locations"
    __table_args__ = (
        Index("idx_locations_account", "account_id"),
    )

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ghl_location_id: Column = Column(Text, unique=True, nullable=True)
    name: Column = Column(Text, nullable=False)
    status: Column = Column(Text, server_default="active")
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    account = relationship("Account", back_populates="locations")
    blueprint = relationship("Blueprint", back_populates="location", uselist=False)
    voice_samples = relationship("VoiceSample", back_populates="location", cascade="all, delete-orphan")
    foundation_scores = relationship("FoundationScore", back_populates="location", cascade="all, delete-orphan")
    oauth_tokens = relationship("OAuthToken", back_populates="location", cascade="all, delete-orphan")


class User(Base):
    """Human users who authenticate and access locations."""

    __tablename__ = "users"

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Column = Column(Text, unique=True, nullable=False)   # citext behaviour via lower() enforced at app layer
    full_name: Column = Column(Text, nullable=True)
    password_hash: Column = Column(Text, nullable=True)         # null = OAuth-only user
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    location_access = relationship("UserLocationAccess", back_populates="user", cascade="all, delete-orphan")


class UserLocationAccess(Base):
    """Many-to-many: users ↔ locations with role."""

    __tablename__ = "user_location_access"

    user_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    location_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Column = Column(
        Text,
        CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name="ck_ula_role"
        ),
        nullable=False,
    )
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", back_populates="location_access")


class OAuthToken(Base):
    """OAuth tokens per integration provider, scoped to a location."""

    __tablename__ = "oauth_tokens"
    __table_args__ = (
        UniqueConstraint("location_id", "provider", name="uq_oauth_tokens_location_provider"),
    )

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    location_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Column = Column(
        Text,
        CheckConstraint(
            "provider IN ('ghl', 'gmail', 'youtube')", name="ck_oauth_provider"
        ),
        nullable=False,
    )
    access_token: Column = Column(Text, nullable=False)   # encrypted at rest (Phase 3)
    refresh_token: Column = Column(Text, nullable=False)  # encrypted at rest
    expires_at: Column = Column(DateTime(timezone=True), nullable=False)
    scopes: Column = Column(ARRAY(Text), nullable=False)
    provider_account_id: Column = Column(Text, nullable=True)
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location = relationship("Location", back_populates="oauth_tokens")


# ── 4.2 Blueprint (Brand Studio) ─────────────────────────────────────────────

class Blueprint(Base):
    """
    Brand Studio output — the structured brand identity for a location.
    One Blueprint per location (unique constraint on location_id).
    """

    __tablename__ = "blueprints"

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    location_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Identity
    full_name: Column = Column(Text, nullable=True)
    market_city: Column = Column(Text, nullable=True)
    brokerage: Column = Column(Text, nullable=True)
    years_active: Column = Column(Text, nullable=True)
    price_range: Column = Column(Text, nullable=True)
    niche_primary: Column = Column(Text, nullable=True)
    niche_secondary: Column = Column(ARRAY(Text), nullable=True)

    # Voice
    voice_tone: Column = Column(ARRAY(Text), nullable=True)
    voice_cadence: Column = Column(Text, nullable=True)
    vocabulary_yes: Column = Column(ARRAY(Text), nullable=True)
    vocabulary_no: Column = Column(ARRAY(Text), nullable=True)
    pov: Column = Column(Text, server_default="first-person")
    humor_level: Column = Column(Text, nullable=True)

    # Audience
    audience_primary: Column = Column(Text, nullable=True)
    audience_pain_points: Column = Column(ARRAY(Text), nullable=True)
    audience_aspirations: Column = Column(ARRAY(Text), nullable=True)

    # Positioning
    one_liner: Column = Column(Text, nullable=True)
    differentiators: Column = Column(ARRAY(Text), nullable=True)
    proof_points: Column = Column(ARRAY(Text), nullable=True)

    # Content pillars (JSON: [{name, weight, examples}])
    pillars: Column = Column(JSONB, server_default=text("'[]'"))

    # Vyral mix
    vyral_mix: Column = Column(
        JSONB,
        server_default=text(
            '\'{"viral": 0.4, "brand": 0.3, "personal": 0.2, "conversion": 0.1}\''
        ),
    )

    updated_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location = relationship("Location", back_populates="blueprint")


# ── 4.3 Foundation (voice fingerprint vector store) ───────────────────────────

class VoiceSample(Base):
    """
    A single chunked text sample from the user's real content,
    embedded with OpenAI text-embedding-3-small (1536 dims).
    """

    __tablename__ = "voice_samples"
    __table_args__ = (
        Index("idx_voice_samples_location", "location_id"),
        Index(
            "idx_voice_samples_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
        Index(
            "idx_voice_samples_filter",
            "location_id", "platform", "bucket", "excluded",
        ),
    )

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    location_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Column = Column(Text, nullable=False)
    embedding: Column = Column(Vector(1536), nullable=False)
    source: Column = Column(
        Text,
        CheckConstraint(
            "source IN ('podcast', 'social_approved', 'social_edited', "
            "'written_from_scratch', 'brand_studio', 'historical')",
            name="ck_voice_samples_source",
        ),
        nullable=False,
    )
    topic: Column = Column(Text, nullable=True)
    platform: Column = Column(Text, nullable=True)
    bucket: Column = Column(
        Text,
        CheckConstraint(
            "bucket IN ('viral', 'brand', 'personal', 'conversion') OR bucket IS NULL",
            name="ck_voice_samples_bucket",
        ),
        nullable=True,
    )
    weight: Column = Column(Float, server_default="1.0")
    edit_distance: Column = Column(Float, nullable=True)   # for source='social_edited'
    episode_id: Column = Column(UUID(as_uuid=True), nullable=True)  # FK added Phase 5
    excluded: Column = Column(Boolean, server_default="false")
    promoted: Column = Column(Boolean, server_default="false")
    created_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    location = relationship("Location", back_populates="voice_samples")


class FoundationScore(Base):
    """Point-in-time snapshot of Foundation quality for a location."""

    __tablename__ = "foundation_scores"
    __table_args__ = (
        Index(
            "idx_foundation_scores_location",
            "location_id", "computed_at",
        ),
    )

    id: Column = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    location_id: Column = Column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Column = Column(Float, nullable=False)       # 0.0 – 1.0
    sample_count: Column = Column(Integer, nullable=False)
    computed_at: Column = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    location = relationship("Location", back_populates="foundation_scores")
