"""
PodClick — async SQLAlchemy engine + session factory.

Usage:
    from db.engine import async_session

    async with async_session() as session:
        result = await session.execute(select(Location))
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

_connect_args: dict = {}
if settings.requires_ssl:
    # asyncpg doesn't accept sslmode= in the URL — pass ssl via connect_args
    import ssl as _ssl
    _connect_args = {"ssl": _ssl.create_default_context()}

engine = create_async_engine(
    settings.async_database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args=_connect_args,
    echo=False,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields a session per request."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
