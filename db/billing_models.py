"""Tenant-scoped billing state. Install via Alembic, never create tables on request.

The customer mapping, checkout intent and subscription snapshot are deliberately
one row per workspace. Provider event receipts commit in the same transaction as
the subscription change. No card details or full webhook payloads are retained.
"""
from contextlib import asynccontextmanager
import hashlib
import uuid

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, String, select, text
from sqlalchemy.dialects.postgresql import UUID

from db.models import Base


class BillingAccount(Base):
    __tablename__ = "billing_accounts"

    location_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)
    stripe_customer_id = Column(String(255), unique=True, nullable=True)
    livemode = Column(Boolean, nullable=True)
    stripe_subscription_id = Column(String(255), unique=True, nullable=True)
    price_id = Column(String(255), nullable=True)
    status = Column(String(64), nullable=False, default="none", server_default="none")
    current_period_end = Column(BigInteger, nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False, server_default="false")
    checkout_request_id = Column(String(64), nullable=True)
    checkout_price_id = Column(String(255), nullable=True)
    checkout_started_at = Column(BigInteger, nullable=True)
    checkout_session_id = Column(String(255), nullable=True)
    updated_at = Column(BigInteger, nullable=False, default=0, server_default="0")


class StripeEventReceipt(Base):
    __tablename__ = "stripe_event_receipts"

    event_id = Column(String(255), primary_key=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(255), nullable=False)
    processed_at = Column(BigInteger, nullable=False)


class BillingTransaction:
    def __init__(self, session, account):
        self.session = session
        self.account = account

    async def has_event(self, event_id):
        return await self.session.get(StripeEventReceipt, event_id) is not None

    async def record_event(self, event_id, event_type, processed_at):
        self.session.add(StripeEventReceipt(
            event_id=event_id, event_type=event_type,
            location_id=self.account.location_id, processed_at=processed_at,
        ))


class PostgresBillingStore:
    """Serializes workspace billing across workers with transaction-scoped locks."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    @asynccontextmanager
    async def locked(self, location_id=None, customer_id=None):
        async with self.session_factory() as session:
            async with session.begin():
                if customer_id is not None:
                    location_id = await session.scalar(select(BillingAccount.location_id).where(
                        BillingAccount.stripe_customer_id == customer_id))
                    if location_id is None:
                        yield None
                        return
                location_id = uuid.UUID(str(location_id))
                lock_id = int.from_bytes(hashlib.sha256(
                    ("podclick-billing:" + str(location_id)).encode()
                ).digest()[:8], "big", signed=True)
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
                account = await session.get(BillingAccount, location_id, populate_existing=True)
                if account is None:
                    account = BillingAccount(location_id=location_id, status="none", cancel_at_period_end=False)
                    session.add(account)
                    await session.flush()  # FK verifies this is an existing workspace.
                yield BillingTransaction(session, account)
                await session.flush()
