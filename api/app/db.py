"""Pool asyncpg compartilhado (criado no lifespan da aplicação)."""
from __future__ import annotations

import asyncpg

from . import settings

_pool: asyncpg.Pool | None = None


async def connect(retries: int = 60, delay: float = 5.0) -> asyncpg.Pool:
    """Cria o pool, esperando o banco (a API pode subir antes dele — ou o banco pode
    estar sendo migrado para outro host)."""
    import asyncio
    import logging
    global _pool
    for tentativa in range(1, retries + 1):
        try:
            _pool = await _create()
            return _pool
        except (OSError, asyncpg.PostgresError) as exc:
            logging.getLogger("carbonara-api").warning(
                "banco indisponível em %s:%s (%s) — tentativa %d/%d", settings.DB_HOST, settings.DB_PORT,
                str(exc).strip()[:80], tentativa, retries)
            await asyncio.sleep(delay)
    raise RuntimeError("banco não ficou disponível")


async def _create() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
        password=settings.DB_PASSWORD, database=settings.DB_NAME,
        min_size=1, max_size=8, command_timeout=60,
        server_settings={"application_name": "carbonara-api", "timezone": "UTC"},
    )


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("pool do banco não inicializado")
    return _pool
