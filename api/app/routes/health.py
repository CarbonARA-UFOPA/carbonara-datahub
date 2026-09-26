from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db, settings

router = APIRouter(tags=["saúde"])


@router.get("/healthz", summary="Saúde da API e do banco")
async def healthz():
    try:
        v = await db.pool().fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"banco indisponível: {exc}") from exc
    return {"status": "ok", "api": settings.API_VERSION, "timescaledb": v}
