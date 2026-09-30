"""Add workspace billing snapshots and atomic Stripe event receipts.

Revision ID: b6e4d9f7a210
Revises: a7b3c8e2f015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b6e4d9f7a210"
down_revision = "c4a8e1f52d03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_accounts",
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stripe_customer_id", sa.String(255), unique=True, nullable=True),
        sa.Column("livemode", sa.Boolean(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), unique=True, nullable=True),
        sa.Column("price_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(64), nullable=False, server_default="none"),
        sa.Column("current_period_end", sa.BigInteger(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("checkout_request_id", sa.String(64), nullable=True),
        sa.Column("checkout_price_id", sa.String(255), nullable=True),
        sa.Column("checkout_started_at", sa.BigInteger(), nullable=True),
        sa.Column("checkout_session_id", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "stripe_event_receipts",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("processed_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_stripe_event_receipts_location_id", "stripe_event_receipts", ["location_id"])


def downgrade():
    op.drop_index("ix_stripe_event_receipts_location_id", table_name="stripe_event_receipts")
    op.drop_table("stripe_event_receipts")
    op.drop_table("billing_accounts")
