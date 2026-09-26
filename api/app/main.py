"""API de dados do CARBONARA.

Serve o que a ingestão gravou no TimescaleDB a partir dos arquivos monitorados na
dashboard: estações, variáveis, séries (cruas e agregadas), últimos valores e o
estado da ingestão. Acesso por chave de API (``X-API-Key``); ``/stations``,
``/variables`` e ``/healthz`` são abertos (com limite de taxa por IP).

Documentação interativa em ``/docs`` (OpenAPI em ``/openapi.json``).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db, settings
from .routes import admin_db, admin_maint, files, health, keys, series, stations, variables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await db.ensure_ro_pool()
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
          files.router, keys.router, admin_db.router, admin_maint.router):
    app.include_router(r)

