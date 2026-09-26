"""Chaves de API: autenticação por ``X-API-Key`` (ou ``Authorization: Bearer``),
escopos, validade, revogação e limite de taxa.

- A chave em claro só existe no momento da criação (``new_key``); no banco fica
  ``sha256(pepper + chave)``. Listagens mostram só o ``prefix``.
- Escopos: ``read`` (séries, últimos valores, ledger), ``download`` (exportações em
  massa — CSV/limites altos), ``admin`` (gerir chaves). ``admin`` implica os demais.
- Limite de taxa: token bucket em memória por chave (``rate_limit`` req/min) e por IP
  para as rotas anônimas — ``429`` com ``Retry-After``. Com 2 workers uvicorn o limite
  efetivo é ~2× o configurado; é um freio, não uma cota exata.
- ``last_used_at`` é atualizado no máximo uma vez por minuto por chave.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from . import db, settings

SCOPES = ("read", "download", "admin")


def hash_key(key: str) -> str:
    return hashlib.sha256((settings.API_KEY_PEPPER + key).encode("utf-8")).hexdigest()


def new_key() -> str:
    """Chave nova: prefixo fixo (identifica o serviço em logs) + 32 bytes aleatórios."""
    return "cbk_" + secrets.token_urlsafe(32)


@dataclass
class KeyInfo:
    key_id: int
    owner: str
    scopes: tuple[str, ...]
    rate_limit: int
    anonymous: bool = False
    extra: dict = field(default_factory=dict)

    def has(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


# --- cache de chaves e token buckets (por processo) ---------------------------------
_key_cache: dict[str, tuple[KeyInfo | None, float]] = {}
_buckets: dict[str, list[float]] = {}          # id -> [tokens, last_refill]
_last_touch: dict[int, float] = {}


def _take_token(bucket_id: str, per_minute: int) -> float:
    """Consome um token; devolve 0 se permitido, senão os segundos até o próximo."""
    now = time.monotonic()
    cap = max(1, per_minute)
    tokens, last = _buckets.get(bucket_id, [float(cap), now])
    tokens = min(cap, tokens + (now - last) * cap / 60.0)
    if tokens >= 1.0:
        _buckets[bucket_id] = [tokens - 1.0, now]
        return 0.0
    _buckets[bucket_id] = [tokens, now]
    return (1.0 - tokens) * 60.0 / cap


async def _lookup(key: str) -> KeyInfo | None:
    h = hash_key(key)
    hit = _key_cache.get(h)
    now = time.monotonic()
    if hit and hit[1] > now:
        return hit[0]
    row = await db.pool().fetchrow(
        "SELECT key_id, owner, scopes, rate_limit, expires_at, revoked_at FROM api_keys "
        "WHERE key_hash = $1", h)
    info = None
    if row and row["revoked_at"] is None and (
            row["expires_at"] is None or row["expires_at"].timestamp() > time.time()):
        info = KeyInfo(row["key_id"], row["owner"], tuple(row["scopes"]), int(row["rate_limit"]))
    _key_cache[h] = (info, now + settings.KEY_CACHE_SECONDS)
    return info


async def _touch(key_id: int) -> None:
    now = time.monotonic()
    if now - _last_touch.get(key_id, 0) < 60:
        return
    _last_touch[key_id] = now
    try:
        await db.pool().execute("UPDATE api_keys SET last_used_at = now() WHERE key_id = $1", key_id)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a requisição
        pass


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _extract_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if not key:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        key = request.query_params.get("api_key")   # conveniência para links/notebooks
    return key or None


async def authenticate(request: Request) -> KeyInfo:
    """Identidade da requisição: a chave (validada) ou anônimo. Chave informada mas
    inválida/expirada/revogada → 401 (nunca cai silenciosamente para anônimo)."""
    key = _extract_key(request)
    if not key:
        return KeyInfo(0, "anonymous", (), settings.ANON_RATE_LIMIT, anonymous=True)
    info = await _lookup(key)
    if info is None:
        raise HTTPException(401, "chave de API inválida, expirada ou revogada")
    return info


def _enforce_rate(request: Request, who: KeyInfo) -> None:
    bucket = f"ip:{_client_ip(request)}" if who.anonymous else f"key:{who.key_id}"
    wait = _take_token(bucket, who.rate_limit)
    if wait > 0:
        raise HTTPException(429, "limite de requisições excedido",
                            headers={"Retry-After": str(int(wait) + 1)})


def require(scope: str):
    """Dependência: exige chave com o escopo (429 se passou do limite)."""
    async def _dep(request: Request) -> KeyInfo:
        who = await authenticate(request)
        if who.anonymous:
            raise HTTPException(401, "informe a chave de API (cabeçalho X-API-Key)",
                                headers={"WWW-Authenticate": "ApiKey"})
        if not who.has(scope):
            raise HTTPException(403, f"a chave não tem o escopo '{scope}'")
        _enforce_rate(request, who)
        asyncio.get_running_loop().create_task(_touch(who.key_id))
        return who
    return _dep


async def optional(request: Request) -> KeyInfo:
    """Dependência das rotas abertas: anônimo permitido (limite por IP), chave
    reconhecida se enviada (limite da chave)."""
    who = await authenticate(request)
    _enforce_rate(request, who)
    if not who.anonymous:
        asyncio.get_running_loop().create_task(_touch(who.key_id))
    return who
