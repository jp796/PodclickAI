"""phase3a_brick_tables

Revision ID: f7c2a9b1e305
Revises: a1f3c8d2e094
Create Date: 2026-05-27 00:00:00.000000

Phase 3A — Brick the Foreman.

Adds:
  - brick_permits    (one row per location — current autonomy tier)
  - brick_track_record (outcome history per action)
  - brick_actions    (punch list / approval queue)
  - brick_messages   (seeded greeting + walk-through copy)
  - brick_memory     (persistent standing instructions)
  - users.timezone   (drives 4am cron scheduling)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = "f7c2a9b1e305"
down_revision = "a1f3c8d2e094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users.timezone ────────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=True,
            server_default="America/Chicago",
        ),
    )

    # ── brick_permits ─────────────────────────────────────────────────────────
    op.create_table(
        "brick_permits",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "current_tier",
            sa.Text(),
            sa.CheckConstraint(
                "current_tier IN ('owner_builder','draftsman','bricklayer','foreman','gc')",
                name="ck_brick_permits_tier",
            ),
            nullable=False,
            server_default="owner_builder",
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_brick_permits_location", "brick_permits", ["location_id"])

    # ── brick_track_record ────────────────────────────────────────────────────
    op.create_table(
        "brick_track_record",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column(
            "outcome",
            sa.Text(),
            sa.CheckConstraint(
                "outcome IN ('success','failure','rejected')",
                name="ck_brick_track_outcome",
            ),
            nullable=False,
        ),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "idx_brick_track_record_location",
        "brick_track_record",
        ["location_id"],
    )
    op.create_index(
        "idx_brick_track_record_executed_at",
        "brick_track_record",
        ["executed_at"],
    )

    # ── brick_actions ─────────────────────────────────────────────────────────
    op.create_table(
        "brick_actions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('pending','approved','rejected','executed','expired')",
                name="ck_brick_actions_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'")),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # audit
        sa.Column(
            "actor_type",
            sa.Text(),
            sa.CheckConstraint(
                "actor_type IN ('brick','user')",
                name="ck_brick_actions_actor_type",
            ),
            nullable=False,
            server_default="brick",
        ),
    )
    op.create_index("idx_brick_actions_location", "brick_actions", ["location_id"])
    op.create_index("idx_brick_actions_status", "brick_actions", ["status"])
    op.create_index(
        "idx_brick_actions_requested_at", "brick_actions", ["requested_at"]
    )

    # ── brick_messages ────────────────────────────────────────────────────────
    op.create_table(
        "brick_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Text(),
            sa.CheckConstraint(
                "role IN ('brick','user')",
                name="ck_brick_messages_role",
            ),
            nullable=False,
        ),
        sa.Column("context_screen", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_brick_messages_location", "brick_messages", ["location_id"]
    )
    op.create_index(
        "idx_brick_messages_context", "brick_messages", ["context_screen"]
    )

    # ── brick_memory ──────────────────────────────────────────────────────────
    op.create_table(
        "brick_memory",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_referenced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_brick_memory_location", "brick_memory", ["location_id"])
    op.create_index(
        "idx_brick_memory_active", "brick_memory", ["location_id", "active"]
    )


def downgrade() -> None:
    op.drop_table("brick_memory")
    op.drop_table("brick_messages")
    op.drop_table("brick_actions")
    op.drop_table("brick_track_record")
    op.drop_table("brick_permits")
    op.drop_column("users", "timezone")
