from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import auth, db

router = APIRouter(tags=["variáveis"])


@router.get("/variables", summary="Catálogo de variáveis (aberto)")
async def list_variables(_who: auth.KeyInfo = Depends(auth.optional)):
    rows = await db.pool().fetch(
        "SELECT variable_id, name, unit, description FROM variables ORDER BY variable_id")
    return [dict(r) for r in rows]
