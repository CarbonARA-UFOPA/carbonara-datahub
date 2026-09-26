"""Visão do banco para a aba *Banco* da dashboard (escopo ``admin``, SOMENTE LEITURA).

Um "phpMyAdmin" mínimo: tamanho e tabelas, estado da hypertable (chunks, compressão),
agregados contínuos, período coberto por estação/variável, execuções da ingestão e uma
caixa de consulta SQL. A consulta roda numa transação ``READ ONLY`` com
``statement_timeout`` e teto de linhas — nunca altera nada, por construção.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ._common import iso, tzinfo_of

router = APIRouter(prefix="/admin/db", tags=["banco"])

QUERY_MAX_ROWS = 500
QUERY_TIMEOUT_MS = 20000
_ALLOWED_START = re.compile(r"^\s*(select|with|explain|show|table|values)\b", re.I)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|vacuum|copy|call|do|"
    r"refresh|reindex|cluster|lock|set\s+(?!local\s+)|reset|begin|commit|rollback|"
    r"pg_terminate_backend|pg_cancel_backend|pg_sleep|dblink|lo_)\b", re.I)


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, memoryview)):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


@router.get("/overview", summary="Tamanho, tabelas, hypertable, agregados, execuções")
async def overview(tz: str | None = None, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    tzi = tzinfo_of(tz)
    pool = db.pool()
    size = await pool.fetchrow(
        "SELECT pg_database_size(current_database()) AS bytes, "
        "pg_size_pretty(pg_database_size(current_database())) AS pretty, version() AS pg, "
        "(SELECT extversion FROM pg_extension WHERE extname='timescaledb') AS ts, "
        "(SELECT extversion FROM pg_extension WHERE extname='postgis') AS postgis, "
        "current_setting('wal_level') AS wal_level, now() AS now")
    tables = await pool.fetch(
        """
        SELECT c.relname AS name,
               CASE c.relkind WHEN 'r' THEN 'tabela' WHEN 'm' THEN 'view materializada'
                              WHEN 'v' THEN 'view' ELSE c.relkind::text END AS kind,
               pg_total_relation_size(c.oid) AS bytes,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
               COALESCE(s.n_live_tup, 0) AS rows_estimate,
               obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'm', 'v')
        ORDER BY pg_total_relation_size(c.oid) DESC, c.relname
        """)
    exact = {}
    for t in ("stations", "variables", "monitored_files", "ingest_files", "api_keys",
              "pipeline_runs", "schema_migrations"):
        try:
            exact[t] = await pool.fetchval(f"SELECT count(*) FROM {t}")
        except Exception:  # noqa: BLE001
            pass
    hyper = await pool.fetch(
        """
        SELECT h.hypertable_name, h.num_chunks, h.compression_enabled,
               (SELECT count(*) FROM timescaledb_information.chunks c
                 WHERE c.hypertable_name = h.hypertable_name AND c.is_compressed) AS compressed_chunks,
               (SELECT min(range_start) FROM timescaledb_information.chunks c
                 WHERE c.hypertable_name = h.hypertable_name) AS first_chunk,
               (SELECT max(range_end) FROM timescaledb_information.chunks c
                 WHERE c.hypertable_name = h.hypertable_name) AS last_chunk,
               pg_size_pretty(hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass)) AS size
        FROM timescaledb_information.hypertables h
        """)
    caggs = await pool.fetch(
        "SELECT view_name, materialized_only, compression_enabled FROM timescaledb_information.continuous_aggregates")
    jobs = await pool.fetch(
        """
        SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval::text AS every,
               s.last_run_started_at, s.last_run_status, s.next_start, s.total_runs, s.total_failures
        FROM timescaledb_information.jobs j
        LEFT JOIN timescaledb_information.job_stats s USING (job_id)
        WHERE j.proc_name NOT LIKE 'policy_telemetry%' ORDER BY j.job_id
        """)
    runs = await pool.fetch(
        "SELECT run_id, pipeline, version, started, finished, status, stats FROM pipeline_runs "
        "ORDER BY run_id DESC LIMIT 8")
    slots = await pool.fetch(
        "SELECT slot_name, active, wal_status FROM pg_replication_slots")
    conns = await pool.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
    return {
        "database": {"size": size["pretty"], "bytes": size["bytes"], "postgres": size["pg"].split(",")[0],
                     "timescaledb": size["ts"], "postgis": size["postgis"], "wal_level": size["wal_level"],
                     "now": iso(size["now"], tzi), "connections": conns,
                     "replication_slots": [dict(s) for s in slots]},
        "tables": [{**dict(t), "rows_exact": exact.get(t["name"])} for t in tables],
        "hypertables": [{**dict(h), "first_chunk": iso(h["first_chunk"], tzi),
                         "last_chunk": iso(h["last_chunk"], tzi)} for h in hyper],
        "continuous_aggregates": [dict(c) for c in caggs],
        "jobs": [{**dict(j), "last_run_started_at": iso(j["last_run_started_at"], tzi),
                  "next_start": iso(j["next_start"], tzi)} for j in jobs],
        "runs": [{**dict(r), "started": iso(r["started"], tzi), "finished": iso(r["finished"], tzi),
                  "stats": _jsonable(r["stats"])} for r in runs],
    }


@router.get("/coverage", summary="Período coberto e contagens por estação e variável")
async def coverage(station: str | None = None, tz: str | None = None,
                   _who: auth.KeyInfo = Depends(auth.require("admin"))):
    """Usa o agregado diário (tempo real): rápido mesmo com milhões de linhas."""
    tzi = tzinfo_of(tz)
    rows = await db.pool().fetch(
        """
        SELECT d.station_id, d.variable_id, v.name, v.unit,
               min(d.bucket) AS first_day, max(d.bucket) AS last_day, count(*) AS days,
               sum(d.n_samples)::bigint AS n_good, sum(d.n_flagged)::bigint AS n_flagged
        FROM measurements_daily d LEFT JOIN variables v USING (variable_id)
        WHERE ($1::text IS NULL OR d.station_id = $1)
        GROUP BY d.station_id, d.variable_id, v.name, v.unit
        ORDER BY d.station_id, d.variable_id
        """, station)
    per_station: dict[str, dict] = {}
    for r in rows:
        s = per_station.setdefault(r["station_id"], {
            "station_id": r["station_id"], "variables": 0, "n_good": 0, "n_flagged": 0,
            "first_day": None, "last_day": None})
        s["variables"] += 1
        s["n_good"] += r["n_good"] or 0
        s["n_flagged"] += r["n_flagged"] or 0
        if s["first_day"] is None or r["first_day"] < s["first_day"]:
            s["first_day"] = r["first_day"]
        if s["last_day"] is None or r["last_day"] > s["last_day"]:
            s["last_day"] = r["last_day"]
    return {
        "stations": [{**s, "first_day": iso(s["first_day"], tzi), "last_day": iso(s["last_day"], tzi)}
                     for s in per_station.values()],
        "variables": [{"station_id": r["station_id"], "variable_id": r["variable_id"],
                       "name": r["name"], "unit": r["unit"], "days": r["days"],
                       "first_day": iso(r["first_day"], tzi), "last_day": iso(r["last_day"], tzi),
                       "n_good": r["n_good"], "n_flagged": r["n_flagged"]} for r in rows],
    }


class QueryIn(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20000)
    max_rows: int = Field(200, ge=1, le=QUERY_MAX_ROWS)


@router.post("/query", summary="Consulta SQL somente leitura (SELECT/WITH/EXPLAIN)")
async def query(body: QueryIn, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    sql = re.sub(r"--[^\n]*", "", body.sql)                # comentários de linha
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S).strip().rstrip(";").strip()
    if ";" in sql:
        raise HTTPException(400, "uma instrução por vez (sem ';' no meio)")
    if not _ALLOWED_START.match(sql):
        raise HTTPException(400, "só consultas de leitura: comece com SELECT, WITH ou EXPLAIN")
    if _FORBIDDEN.search(sql):
        raise HTTPException(400, "palavra não permitida numa consulta de leitura")
    t0 = time.monotonic()
    async with db.pool().acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            try:
                rows = await conn.fetch(sql + f" LIMIT {body.max_rows + 1}"
                                        if not re.search(r"\blimit\b", sql, re.I)
                                        and not sql.lower().startswith("explain") else sql)
            except Exception as exc:  # noqa: BLE001 — erro de SQL vira 400 legível
                raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    truncated = len(rows) > body.max_rows
    rows = rows[:body.max_rows]
    columns = list(rows[0].keys()) if rows else []
    return {"columns": columns, "rows": [[_jsonable(v) for v in r.values()] for r in rows],
            "row_count": len(rows), "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}
