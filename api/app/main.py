"""API de dados do CARBONARA.

Serve o que a ingestão gravou no TimescaleDB a partir dos arquivos monitorados na
dashboard: estações, variáveis, séries (cruas e agregadas), últimos valores e o
estado da ingestão. Acesso por chave de API (``X-API-Key``); ``/stations``,
``/variables`` e ``/healthz`` são abertos (com limite de taxa por IP).

Documentação interativa em ``/docs`` (OpenAPI em ``/openapi.json``).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, settings
from .routes import admin_db, files, health, keys, series, stations, variables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    try:
        yield
    finally:
        await db.close()


app = FastAPI(
    title="CARBONARA — API de dados",
    version=settings.API_VERSION,
    description=__doc__,
    lifespan=lifespan,
    docs_url="/docs", redoc_url=None,
)

# API pública de leitura: qualquer origem pode consultar (a autorização é pela chave).
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Authorization", "Content-Type"],
)

for r in (health.router, stations.router, variables.router, series.router,
          files.router, keys.router, admin_db.router):
    app.include_router(r)


@app.exception_handler(ValueError)
async def _value_error(_request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)
