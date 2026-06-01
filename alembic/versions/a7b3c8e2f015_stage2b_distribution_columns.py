"""stage2b_distribution_columns

Phase B Consolidation — Distribution columns on projects table.

Adds:
  - projects.buzzsprout_url          (TEXT, nullable)  — audio_url from Buzzsprout
  - projects.buzzsprout_episode_id   (TEXT, nullable)  — Buzzsprout episode id
  - projects.youtube_url             (TEXT, nullable)  — YouTube watch URL
  - projects.youtube_video_id        (TEXT, nullable)  — YouTube video id
  - projects.legacy_metadata         (JSONB, nullable) — catch-all for migrated fields
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a7b3c8e2f015"
down_revision = "c9d4f2a1b607"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("buzzsprout_url", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("buzzsprout_episode_id", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("youtube_url", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("youtube_video_id", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("legacy_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "legacy_metadata")
    op.drop_column("projects", "youtube_video_id")
    op.drop_column("projects", "youtube_url")
    op.drop_column("projects", "buzzsprout_episode_id")
    op.drop_column("projects", "buzzsprout_url")
