"""Gestão de chaves (escopo ``admin``). A chave em claro é devolvida UMA vez, na criação."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ._common import iso, tzinfo_of

router = APIRouter(prefix="/admin/keys", tags=["chaves"])


class KeyCreate(BaseModel):
    owner: str = Field(..., min_length=1, max_length=120, description="quem vai usar a chave")
    scopes: list[str] = Field(default=["read"])
    rate_limit: int = Field(120, ge=1, le=100000, description="requisições por minuto")
    days: int | None = Field(365, ge=1, le=3650, description="validade em dias (null = sem prazo)")


def _row(r, tzi) -> dict:
    return {"key_id": r["key_id"], "prefix": r["prefix"], "owner": r["owner"],
            "scopes": list(r["scopes"]), "rate_limit": r["rate_limit"],
            "expires_at": iso(r["expires_at"], tzi), "created_by": r["created_by"],
            "created_at": iso(r["created_at"], tzi), "revoked_at": iso(r["revoked_at"], tzi),
            "last_used_at": iso(r["last_used_at"], tzi),
            "active": r["revoked_at"] is None and (r["expires_at"] is None
                                                     or r["expires_at"] > datetime.now(timezone.utc))}


@router.get("", summary="Listar chaves")
async def list_keys(tz: str | None = None, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    tzi = tzinfo_of(tz)
    rows = await db.pool().fetch("SELECT * FROM api_keys ORDER BY key_id")
    return [_row(r, tzi) for r in rows]


@router.post("", summary="Criar chave (a chave em claro só aparece nesta resposta)", status_code=201)
async def create_key(body: KeyCreate, who: auth.KeyInfo = Depends(auth.require("admin"))):
    scopes = [s for s in body.scopes if s in auth.SCOPES]
    if not scopes:
        raise HTTPException(400, f"escopos válidos: {', '.join(auth.SCOPES)}")
    key = auth.new_key()
    expires = datetime.now(timezone.utc) + timedelta(days=body.days) if body.days else None
    row = await db.pool().fetchrow(
        """
        INSERT INTO api_keys (key_hash, prefix, owner, scopes, rate_limit, expires_at, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *
        """, auth.hash_key(key), key[:12], body.owner, scopes, body.rate_limit, expires, who.owner)
    out = _row(row, timezone.utc)
    out["key"] = key
    return out


@router.delete("/{key_id}", summary="Revogar chave")
async def revoke_key(key_id: int, who: auth.KeyInfo = Depends(auth.require("admin"))):
    if key_id == who.key_id:
        raise HTTPException(400, "uma chave não pode revogar a si mesma")
    n = await db.pool().execute(
        "UPDATE api_keys SET revoked_at = now() WHERE key_id = $1 AND revoked_at IS NULL", key_id)
    if n.endswith("0"):
        raise HTTPException(404, "chave não encontrada ou já revogada")
    auth._key_cache.clear()
    return {"revoked": key_id}
