"""Pool asyncpg compartilhado (criado no lifespan da aplicação)."""
from __future__ import annotations

import asyncpg

from . import settings

_pool: asyncpg.Pool | None = None
_ro_pool: asyncpg.Pool | None = None
RO_ROLE = "carbonara_ro"


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


async def ensure_ro_pool() -> None:
    """Papel SEM privilégios para a consulta SQL livre da aba Banco: só leitura
    (pg_read_all_data), transações read-only por padrão e statement_timeout. A senha é
    derivada do pepper (não fica em lugar nenhum) e (re)definida a cada partida."""
    import hashlib
    import logging
    global _ro_pool
    if not settings.API_KEY_PEPPER:
        return
    pw = hashlib.sha256(("ro:" + settings.API_KEY_PEPPER).encode()).hexdigest()[:40]
    try:
        async with _pool.acquire() as conn:
            pw_sql = pw.replace("'", "''")
            # dois workers partem juntos: TODO o preparo sob um lock consultivo, senão um
            # deles falha ("tuple concurrently updated") e cai no pool de superusuário
            await conn.execute("SELECT pg_advisory_lock(hashtext('carbonara-ro-role'))")
            try:
                if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", RO_ROLE):
                    await conn.execute(f"CREATE ROLE {RO_ROLE} LOGIN PASSWORD '{pw_sql}'")
                else:
                    await conn.execute(f"ALTER ROLE {RO_ROLE} WITH LOGIN PASSWORD '{pw_sql}'")
                # SEM pg_read_all_data (ele lê até pg_authid, com os hashes de senha): leitura
                # só nos schemas do projeto, hoje e no futuro (privilégios padrão do dono).
                await conn.execute(f"REVOKE pg_read_all_data FROM {RO_ROLE}")
                await conn.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {settings.DB_USER} GRANT SELECT ON TABLES TO {RO_ROLE}")
                await conn.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {settings.DB_USER} GRANT USAGE ON SCHEMAS TO {RO_ROLE}")
                await grant_ro_on_all(conn)
                await conn.execute(f"ALTER ROLE {RO_ROLE} SET default_transaction_read_only = on")
                await conn.execute(f"ALTER ROLE {RO_ROLE} SET statement_timeout = '20s'")
                await conn.execute(f"ALTER ROLE {RO_ROLE} SET work_mem = '32MB'")
                await conn.execute(f"GRANT CONNECT ON DATABASE {settings.DB_NAME} TO {RO_ROLE}")
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtext('carbonara-ro-role'))")
        _ro_pool = await asyncpg.create_pool(
            host=settings.DB_HOST, port=settings.DB_PORT, user=RO_ROLE, password=pw,
            database=settings.DB_NAME, min_size=0, max_size=3, command_timeout=30,
            server_settings={"application_name": "carbonara-api-ro", "timezone": "UTC"})
    except Exception as exc:  # noqa: BLE001 — sem o papel, a caixa SQL cai no pool normal (regex)
        logging.getLogger("carbonara-api").warning("papel somente leitura indisponível: %s", exc)
        _ro_pool = None


async def grant_ro_on_all(conn) -> None:
    """USAGE + SELECT para o papel de leitura em todos os schemas do projeto (public,
    estações, gerais) e nas views de informação do Timescale."""
    schemas = [r["nspname"] for r in await conn.fetch(
        "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT LIKE '\\_timescaledb%' "
        "AND nspname NOT IN ('information_schema')")]
    for sch in schemas:
        q = '"' + sch.replace('"', '""') + '"'
        try:
            await conn.execute(f"GRANT USAGE ON SCHEMA {q} TO {RO_ROLE}")
            await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {q} TO {RO_ROLE}")
        except Exception:  # noqa: BLE001 — schema de extensão sem permissão: segue
            pass


def has_ro_pool() -> bool:
    return _ro_pool is not None


def ro_pool() -> asyncpg.Pool:
    if _ro_pool is None:
        raise RuntimeError("pool somente leitura não inicializado")
    return _ro_pool


async def close() -> None:
    global _pool, _ro_pool
    if _ro_pool is not None:
        await _ro_pool.close()
        _ro_pool = None
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("pool do banco não inicializado")
    return _pool
