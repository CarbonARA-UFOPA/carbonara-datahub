"""CLI de chaves — para a PRIMEIRA chave (admin), antes de existir qualquer outra, e
para operar sem a dashboard. Roda dentro do container da API:

    docker compose exec api python -m app.keys_cli create --owner dashboard --scopes read,admin
    docker compose exec api python -m app.keys_cli list
    docker compose exec api python -m app.keys_cli revoke 3

A chave em claro é impressa UMA vez (linha ``KEY=...``); guarde-a na hora.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from . import auth, db


async def _create(a) -> int:
    scopes = [s for s in a.scopes.split(",") if s.strip() in auth.SCOPES]
    if not scopes:
        print(f"escopos válidos: {', '.join(auth.SCOPES)}")
        return 2
    key = auth.new_key()
    expires = datetime.now(timezone.utc) + timedelta(days=a.days) if a.days else None
    row = await db.pool().fetchrow(
        "INSERT INTO api_keys (key_hash, prefix, owner, scopes, rate_limit, expires_at, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'cli') RETURNING key_id",
        auth.hash_key(key), key[:12], a.owner, scopes, a.rate_limit, expires)
    print(f"key_id={row['key_id']} owner={a.owner} scopes={','.join(scopes)} "
          f"expires={expires.date() if expires else 'nunca'}")
    print(f"KEY={key}")
    return 0


async def _list(_a) -> int:
    rows = await db.pool().fetch("SELECT * FROM api_keys ORDER BY key_id")
    for r in rows:
        estado = "revogada" if r["revoked_at"] else (
            "expirada" if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc) else "ativa")
        print(f"{r['key_id']:>4}  {r['prefix']}…  {r['owner']:<24} {','.join(r['scopes']):<20} "
              f"{r['rate_limit']:>6}/min  {estado:<8} último uso: {r['last_used_at'] or '-'}")
    return 0


async def _revoke(a) -> int:
    n = await db.pool().execute("UPDATE api_keys SET revoked_at = now() WHERE key_id = $1 AND revoked_at IS NULL", a.key_id)
    print("revogada" if not n.endswith("0") else "não encontrada ou já revogada")
    return 0


async def _main(a) -> int:
    await db.connect()
    try:
        return await {"create": _create, "list": _list, "revoke": _revoke}[a.cmd](a)
    finally:
        await db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Chaves da API CARBONARA")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--owner", required=True)
    c.add_argument("--scopes", default="read")
    c.add_argument("--rate-limit", type=int, default=120)
    c.add_argument("--days", type=int, default=365, help="0 = sem prazo")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("key_id", type=int)
    a = ap.parse_args()
    if getattr(a, "days", None) == 0:
        a.days = None
    return asyncio.run(_main(a))


if __name__ == "__main__":
    raise SystemExit(main())
