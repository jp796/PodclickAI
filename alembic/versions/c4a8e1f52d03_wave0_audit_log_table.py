"""wave0: create the audit_log table

Twelve raw-SQL sites in main.py have been inserting into `audit_log` while no
migration or model ever created it, and eleven of those sites swallow the failure
with `except Exception: pass`. If the table was never hand-created in the database,
every one of those writes has been failing silently — which means the audit trail
the Brick permit ladder treats as its accountability substrate does not exist.

This migration creates it for real, with the actor columns the permit design
already assumes (`actor_type` / `actor_id`, where Brick's own actions write
actor_type='brick') and indexes for the two ways it will be read: by location over
time, and by action.

`IF NOT EXISTS` is deliberate. The table may have been created by hand at some
point, and this migration must be safe to apply either way.

Revision ID: c4a8e1f52d03
Revises: b6e4d9f7a210
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4a8e1f52d03"
down_revision: Union[str, None] = "b6e4d9f7a210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            location_id UUID,
            action      TEXT NOT NULL,
            actor_type  TEXT NOT NULL DEFAULT 'user',
            actor_id    TEXT,
            payload     JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Pre-existing hand-made tables may lack the actor columns the permit design
    # needs; add them rather than failing or silently leaving them absent.
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS actor_type TEXT NOT NULL DEFAULT 'user'")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS actor_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_location_created "
        "ON audit_log (location_id, created_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log (action)")


def downgrade() -> None:
    # Only the indexes and the columns this migration may have added come back off.
    # The table itself is left in place: it is append-only history, and a
    # downgrade that destroys an audit trail is worse than a downgrade that
    # leaves a table behind.
    op.execute("DROP INDEX IF EXISTS idx_audit_log_action")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_location_created")
