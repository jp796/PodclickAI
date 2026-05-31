"""stage2_recording_path

Stage 2 Step 2 — Studio to Ship It slice.

Adds:
  - projects.recording_path        (local disk path to raw WebM, nullable)
  - projects.transcription_status  (pending|running|done|failed, nullable)
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d4f2a1b607"
down_revision = "e2c7f1a9b304"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw recording file path — data/recordings/{project_id}.webm on local disk.
    # Populated by POST /api/projects/from-recording when studio hands off.
    op.add_column("projects", sa.Column("recording_path", sa.Text(), nullable=True))
    # Transcription state — separate from the main project status so Ship It
    # can track transcription progress without ambiguity.
    # Values: pending | running | done | failed
    op.add_column(
        "projects",
        sa.Column("transcription_status", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "transcription_status")
    op.drop_column("projects", "recording_path")
