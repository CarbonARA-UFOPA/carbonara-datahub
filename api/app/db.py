"""Pool asyncpg compartilhado (criado no lifespan da aplicação)."""
from __future__ import annotations

import asyncpg

from . import settings

_pool: asyncpg.Pool | None = None


async def connect() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
        password=settings.DB_PASSWORD, database=settings.DB_NAME,
        min_size=1, max_size=8, command_timeout=60,
        server_settings={"application_name": "carbonara-api", "timezone": "UTC"},
    )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("pool do banco não inicializado")
    return _pool
