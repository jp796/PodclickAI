"""phase5_projects_clips

Revision ID: e2c7f1a9b304
Revises: f7c2a9b1e305
Create Date: 2026-05-28 00:00:00.000000

Phase 5 — Project flow integration.

Adds:
  - projects   (episode state machine: draft → recording_done → ... → closed)
  - clips      (vertical 9:16 clip candidates per project)

Also wires posts.project_id FK (was deferred in Phase 2A).

Construction vocabulary: a Project is an episode in flight.
Closing = publish. Ship It = trigger the full pipeline.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers
revision = "e2c7f1a9b304"
down_revision = "f7c2a9b1e305"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── projects ──────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Bridge to existing file-based pipeline
        sa.Column("job_id", sa.Text, nullable=True, unique=True),

        sa.Column("type", sa.Text, nullable=False, server_default="episode"),
        sa.Column("title", sa.Text, nullable=False, server_default="Untitled Episode"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("episode_number", sa.Integer, nullable=True),

        # State machine
        sa.Column("status", sa.Text, nullable=False, server_default="recording_done"),

        # Ship It wizard step (1-4)
        sa.Column("wizard_step", sa.Integer, nullable=True, server_default="1"),

        # Pipeline outputs
        sa.Column("mp3_url", sa.Text, nullable=True),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("show_notes", sa.Text, nullable=True),

        # Assembly metadata + sponsor placement
        sa.Column("audio_assembly", JSONB, server_default=sa.text("'{}'")),
        sa.Column("sponsor_placement", JSONB, nullable=True),

        # Guest IDs (list of strings referencing guests.json)
        sa.Column("guest_ids", JSONB, server_default=sa.text("'[]'")),

        # Closing scheduling
        sa.Column("closing_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),

        # Guest email tracking
        sa.Column("guest_email_sent_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Constraints
        sa.CheckConstraint(
            "status IN ('draft','recording_done','processing','review','scheduled','closing','closed','failed')",
            name="ck_projects_status",
        ),
        sa.CheckConstraint(
            "type IN ('episode','campaign')",
            name="ck_projects_type",
        ),
    )
    op.create_index(
        "idx_projects_location_status",
        "projects",
        ["location_id", "status", "created_at"],
    )

    # ── clips ─────────────────────────────────────────────────────────────────
    op.create_table(
        "clips",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_start_seconds", sa.Float, nullable=False),
        sa.Column("source_end_seconds", sa.Float, nullable=False),
        sa.Column("hook_text", sa.Text, nullable=True),
        sa.Column("clip_caption", sa.Text, nullable=True),
        sa.Column("virality_score", sa.Float, nullable=True),
        sa.Column("rendered_url", sa.Text, nullable=True),
        sa.Column("srt_url", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','rendering','rendered','approved','removed')",
            name="ck_clips_status",
        ),
    )
    op.create_index("idx_clips_project", "clips", ["project_id"])

    # ── wire posts.project_id FK (was intentionally deferred in Phase 2A) ─────
    op.create_foreign_key(
        "fk_posts_project_id",
        "posts",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_project_id", "posts", type_="foreignkey")
    op.drop_index("idx_clips_project", "clips")
    op.drop_table("clips")
    op.drop_index("idx_projects_location_status", "projects")
    op.drop_table("projects")
