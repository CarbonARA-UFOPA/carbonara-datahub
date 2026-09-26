"""Gerência do banco para a aba *Banco* da dashboard (escopo ``admin``).

Modelo que a tela mostra ("bancos" = schemas do PostgreSQL):
- ``public`` — o SISTEMA: ``measurements`` (todas as medições), ``stations``,
  ``variables``, ledger, chaves… somente leitura por aqui.
- um schema por ESTAÇÃO (``eddyfarm``, ``fluxfarm``…) — criado pela ingestão. Dentro
  dele, uma VIEW larga por arquivo monitorado (uma coluna por variável), gerada da
  tabela de medições e sempre em dia; e as TABELAS que o usuário criar pela tela
  (dados auxiliares, calibrações, anotações), que são editáveis.
- schemas "gerais" criados pela tela (ex.: ``campanhas``), também editáveis.

Escrita só em tabelas de usuário (nunca em views, nunca em ``public``); a consulta SQL
livre roda em transação READ ONLY com timeout e teto de linhas.
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
IDENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
SYSTEM_SCHEMAS = ("public",)
HIDDEN_SCHEMA_PREFIXES = ("pg_", "_timescaledb", "timescaledb_", "information_schema", "topology", "tiger")
GENERATED_COMMENT = "carbonara: view gerada da ingestão"
COLUMN_TYPES = {
    "texto": "text", "numero": "double precision", "inteiro": "bigint", "booleano": "boolean",
    "data_hora": "timestamptz", "data": "date", "json": "jsonb",
}
_ALLOWED_START = re.compile(r"^\s*(select|with|explain|show|table|values)\b", re.I)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|vacuum|copy|call|do|"
    r"refresh|reindex|cluster|lock|set\s+(?!local\s+)|reset|begin|commit|rollback|"
    r"pg_terminate_backend|pg_cancel_backend|pg_sleep|dblink|lo_)\b", re.I)


def _q(ident: str) -> str:
    """Identificador validado e entre aspas (nomes de estação/arquivo podem ter '-')."""
    if not IDENT_RE.match(ident or ""):
        raise HTTPException(400, f"nome inválido: '{ident}' (minúsculas, números, '_' ou '-', "
                                 "começando por letra, até 63)")
    return '"' + ident.replace('"', '""') + '"'


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


async def _table_kind(schema: str, table: str) -> dict:
    row = await db.pool().fetchrow(
        """
        SELECT c.relkind::text AS relkind, obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r','v','m','p')
        """, schema, table)
    if not row:
        raise HTTPException(404, f"tabela {schema}.{table} não encontrada")
    kind = {"r": "table", "p": "table", "v": "view", "m": "matview"}[row["relkind"]]
    editable = kind == "table" and schema not in SYSTEM_SCHEMAS
    return {"kind": kind, "comment": row["comment"], "editable": editable,
            "generated": (row["comment"] or "").startswith("carbonara:")}


# ---------------------------------------------------------------------------------
# Visão geral, cobertura, consulta livre
# ---------------------------------------------------------------------------------
@router.get("/overview", summary="Tamanho, hypertable, agregados, execuções")
async def overview(tz: str | None = None, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    tzi = tzinfo_of(tz)
    pool = db.pool()
    size = await pool.fetchrow(
        "SELECT pg_size_pretty(pg_database_size(current_database())) AS pretty, version() AS pg, "
        "(SELECT extversion FROM pg_extension WHERE extname='timescaledb') AS ts, "
        "(SELECT extversion FROM pg_extension WHERE extname='postgis') AS postgis, "
        "current_setting('wal_level') AS wal_level, now() AS now")
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
        "SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates")
    jobs = await pool.fetch(
        """
        SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval::text AS every,
               s.last_run_started_at, s.last_run_status, s.next_start, s.total_runs, s.total_failures
        FROM timescaledb_information.jobs j LEFT JOIN timescaledb_information.job_stats s USING (job_id)
        WHERE j.proc_name NOT LIKE 'policy_telemetry%' ORDER BY j.job_id
        """)
    runs = await pool.fetch(
        "SELECT run_id, pipeline, version, started, finished, status, stats FROM pipeline_runs "
        "ORDER BY run_id DESC LIMIT 6")
    slots = await pool.fetch("SELECT slot_name, active, wal_status FROM pg_replication_slots")
    conns = await pool.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
    return {
        "database": {"size": size["pretty"], "postgres": size["pg"].split(",")[0],
                     "timescaledb": size["ts"], "postgis": size["postgis"], "wal_level": size["wal_level"],
                     "now": iso(size["now"], tzi), "connections": conns,
                     "replication_slots": [dict(s) for s in slots]},
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
    sql = re.sub(r"--[^\n]*", "", body.sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S).strip().rstrip(";").strip()
    if ";" in sql:
        raise HTTPException(400, "uma instrução por vez (sem ';' no meio)")
    if not _ALLOWED_START.match(sql):
        raise HTTPException(400, "só consultas de leitura: comece com SELECT, WITH ou EXPLAIN")
    if _FORBIDDEN.search(sql):
        raise HTTPException(400, "palavra não permitida numa consulta de leitura")
    wrap = not re.search(r"\blimit\b", sql, re.I) and not sql.lower().startswith("explain")
    return await _run_readonly(sql + (f" LIMIT {body.max_rows + 1}" if wrap else ""), body.max_rows)


async def _run_readonly(sql: str, max_rows: int, args: tuple = ()) -> dict:
    t0 = time.monotonic()
    async with db.pool().acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            try:
                rows = await conn.fetch(sql, *args)
            except Exception as exc:  # noqa: BLE001 — erro de SQL vira 400 legível
                raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {"columns": list(rows[0].keys()) if rows else [],
            "rows": [[_jsonable(v) for v in r.values()] for r in rows],
            "row_count": len(rows), "truncated": truncated,
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


# ---------------------------------------------------------------------------------
# Navegação: bancos (schemas) → tabelas → colunas/linhas
# ---------------------------------------------------------------------------------
@router.get("/schemas", summary="Bancos (schemas): sistema, estações e gerais")
async def schemas(_who: auth.KeyInfo = Depends(auth.require("admin"))):
    rows = await db.pool().fetch(
        """
        SELECT n.nspname AS schema,
               count(*) FILTER (WHERE c.relkind IN ('r','p')) AS tables,
               count(*) FILTER (WHERE c.relkind IN ('v','m')) AS views,
               COALESCE(sum(pg_total_relation_size(c.oid)) FILTER (WHERE c.relkind IN ('r','p','m')), 0) AS bytes,
               obj_description(n.oid, 'pg_namespace') AS comment
        FROM pg_namespace n
        LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r','p','v','m')
        WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT LIKE '\\_timescaledb%'
          AND n.nspname NOT IN ('information_schema', 'timescaledb_information',
                                'timescaledb_experimental', 'topology', 'tiger', 'tiger_data')
        GROUP BY n.nspname, n.oid ORDER BY (n.nspname = 'public') DESC, n.nspname
        """)
    stations = {r["station_id"] for r in await db.pool().fetch("SELECT station_id FROM stations")}
    out = []
    for r in rows:
        kind = "sistema" if r["schema"] in SYSTEM_SCHEMAS else ("estacao" if r["schema"] in stations else "geral")
        out.append({"schema": r["schema"], "kind": kind, "tables": r["tables"], "views": r["views"],
                    "size": _pretty(r["bytes"]), "comment": r["comment"]})
    return out


def _pretty(n: int | None) -> str:
    n = int(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@router.get("/tables", summary="Tabelas e views de um banco (schema)")
async def tables(schema: str, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    _q(schema)
    rows = await db.pool().fetch(
        """
        SELECT c.relname AS name,
               CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'matview' ELSE 'table' END AS kind,
               pg_total_relation_size(c.oid) AS bytes,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
               COALESCE(s.n_live_tup, 0) AS rows_estimate,
               obj_description(c.oid, 'pg_class') AS comment,
               (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = $1 AND c.relkind IN ('r','p','v','m')
        ORDER BY c.relkind, c.relname
        """, schema)
    out = []
    for r in rows:
        d = dict(r)
        d["editable"] = d["kind"] == "table" and schema not in SYSTEM_SCHEMAS
        d["generated"] = (d["comment"] or "").startswith("carbonara:")
        if d["kind"] == "table" and (d["rows_estimate"] or 0) < 100000:
            try:
                d["rows_exact"] = await db.pool().fetchval(f"SELECT count(*) FROM {_q(schema)}.{_q(r['name'])}")
            except Exception:  # noqa: BLE001
                d["rows_exact"] = None
        else:
            d["rows_exact"] = None
        out.append(d)
    return out


@router.get("/columns", summary="Estrutura de uma tabela/view")
async def columns(schema: str, table: str, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    _q(schema); _q(table)
    meta = await _table_kind(schema, table)
    rows = await db.pool().fetch(
        """
        SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
               NOT a.attnotnull AS nullable, pg_get_expr(d.adbin, d.adrelid) AS default_value,
               EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = a.attrelid AND i.indisprimary
                         AND a.attnum = ANY(i.indkey)) AS pk,
               col_description(a.attrelid, a.attnum) AS comment
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = $1 AND c.relname = $2 AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """, schema, table)
    definition = None
    if meta["kind"] in ("view", "matview"):
        definition = await db.pool().fetchval("SELECT pg_get_viewdef(format('%I.%I', $1::text, $2::text)::regclass, true)", schema, table)
    return {**meta, "schema": schema, "table": table, "columns": [dict(r) for r in rows],
            "definition": definition}


@router.get("/rows", summary="Linhas de uma tabela/view (paginado, ordenável, filtro simples)")
async def rows(schema: str, table: str, limit: int = 50, offset: int = 0,
               sort: str | None = None, dir: str = "desc", q: str | None = None,
               _who: auth.KeyInfo = Depends(auth.require("admin"))):
    _q(schema); _q(table)
    meta = await _table_kind(schema, table)
    limit = max(1, min(limit, QUERY_MAX_ROWS))
    cols = [r["name"] for r in await db.pool().fetch(
        "SELECT a.attname AS name FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname=$1 AND c.relname=$2 "
        "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum", schema, table)]
    if not cols:
        raise HTTPException(404, "tabela sem colunas")
    if sort and sort not in cols:
        raise HTTPException(400, f"coluna de ordenação desconhecida: {sort}")
    order_col = sort or ("time" if "time" in cols else cols[0])
    direction = "ASC" if dir.lower() == "asc" else "DESC"
    if meta["generated"] and "time" in cols:
        return await _rows_generated_view(schema, table, cols, meta, limit, offset, direction)
    where, args = "", []
    if q and q.strip():
        # filtro simples: o texto aparece em qualquer coluna (cast para texto)
        args.append(f"%{q.strip()}%")
        where = " WHERE " + " OR ".join(f'{_q(c)}::text ILIKE $1' for c in cols)
    sel = ", ".join(_q(c) for c in cols)
    ctid = "ctid::text AS _ctid, " if meta["editable"] else ""
    sql = (f"SELECT {ctid}{sel} FROM {_q(schema)}.{_q(table)}{where} "
           f"ORDER BY {_q(order_col)} {direction} NULLS LAST LIMIT {limit + 1} OFFSET {max(0, offset)}")
    res = await _run_readonly(sql, limit, tuple(args))
    res.update({"schema": schema, "table": table, "limit": limit, "offset": offset,
                "sort": order_col, "dir": direction.lower(), "q": q or "", **meta})
    return res


async def _rows_generated_view(schema: str, table: str, cols: list[str], meta: dict,
                               limit: int, offset: int, direction: str) -> dict:
    """View larga de um arquivo (GROUP BY time sobre measurements): um ``ORDER BY time
    LIMIT`` direto agregaria a estação INTEIRA antes de cortar (minutos em milhões de
    linhas). Paginamos pelo TEMPO: os instantes da página vêm de uma variável-âncora por
    índice (station_id, variable_id, time) e a view é lida só nessa janela. Por isso a
    ordenação é sempre por tempo e não há filtro de texto aqui (use a consulta SQL)."""
    anchor = next((c for c in cols if c not in ("time", "flags")), None)
    empty = {"columns": cols, "rows": [], "row_count": 0, "truncated": False, "elapsed_ms": 0}
    base = {"schema": schema, "table": table, "limit": limit, "offset": offset, "sort": "time",
            "dir": direction.lower(), "q": "", **meta,
            "note": "view gerada: ordenação por tempo e sem filtro de texto (use a consulta SQL)"}
    if anchor is None:
        return {**empty, **base}
    times = await db.pool().fetch(
        f"SELECT time FROM public.measurements WHERE station_id = $1 AND variable_id = $2 "
        f"ORDER BY time {direction} LIMIT {limit + 1} OFFSET {max(0, offset)}", schema, anchor)
    if not times:
        return {**empty, **base}
    ts = [t["time"] for t in times]
    lo, hi = min(ts), max(ts)
    sel = ", ".join(_q(c) for c in cols)
    sql = (f"SELECT {sel} FROM {_q(schema)}.{_q(table)} WHERE time BETWEEN $1 AND $2 "
           f"ORDER BY time {direction} LIMIT {limit + 1}")
    res = await _run_readonly(sql, limit, (lo, hi))
    res["truncated"] = len(times) > limit or res["truncated"]
    res.update(base)
    return res


# ---------------------------------------------------------------------------------
# Escrita (só tabelas de usuário)
# ---------------------------------------------------------------------------------
class ColumnDef(BaseModel):
    name: str
    type: str = "texto"
    nullable: bool = True
    pk: bool = False


class TableCreate(BaseModel):
    schema_name: str = Field(..., alias="schema")
    name: str
    columns: list[ColumnDef] = Field(..., min_length=1)
    comment: str | None = None
    hypertable: bool = Field(False, description="converte em hypertable pela coluna 'time' (timestamptz)")

    model_config = {"populate_by_name": True}


@router.post("/schemas", summary="Criar um banco (schema) geral", status_code=201)
async def create_schema(body: dict, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    name = (body.get("name") or "").strip().lower()
    qn = _q(name)
    if name in SYSTEM_SCHEMAS or name.startswith(HIDDEN_SCHEMA_PREFIXES):
        raise HTTPException(400, "nome reservado")
    comment = (body.get("comment") or "").replace("'", "''")
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {qn}")
            if comment:
                await conn.execute(f"COMMENT ON SCHEMA {qn} IS '{comment}'")
    return {"schema": name}


@router.post("/tables", summary="Criar tabela num banco de estação ou geral", status_code=201)
async def create_table(body: TableCreate, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    schema, table = body.schema_name.strip().lower(), body.name.strip().lower()
    if schema in SYSTEM_SCHEMAS:
        raise HTTPException(400, "não se criam tabelas no schema do sistema (public)")
    qs, qt = _q(schema), _q(table)
    if not await db.pool().fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema):
        raise HTTPException(404, f"banco '{schema}' não existe (crie a estação ou o banco geral antes)")
    defs, pks = [], []
    seen = set()
    for c in body.columns:
        cname = c.name.strip().lower()
        if cname in seen:
            raise HTTPException(400, f"coluna repetida: {cname}")
        seen.add(cname)
        if c.type not in COLUMN_TYPES:
            raise HTTPException(400, f"tipo inválido '{c.type}' — use: {', '.join(COLUMN_TYPES)}")
        defs.append(f"{_q(cname)} {COLUMN_TYPES[c.type]}{'' if c.nullable and not c.pk else ' NOT NULL'}")
        if c.pk:
            pks.append(_q(cname))
    if body.hypertable and "time" not in seen:
        raise HTTPException(400, "hypertable exige uma coluna 'time' do tipo data_hora")
    ddl = f"CREATE TABLE {qs}.{qt} ({', '.join(defs)}"
    if pks and not body.hypertable:
        ddl += f", PRIMARY KEY ({', '.join(pks)})"
    ddl += ")"
    comment = (body.comment or "").replace("'", "''")
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            try:
                await conn.execute(ddl)
                if comment:
                    await conn.execute(f"COMMENT ON TABLE {qs}.{qt} IS '{comment}'")
                if body.hypertable:
                    await conn.execute(f"SELECT create_hypertable('{schema}.{table}', 'time', if_not_exists => TRUE)")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    return {"schema": schema, "table": table, "ddl": ddl}


class RowIn(BaseModel):
    schema_name: str = Field(..., alias="schema")
    table: str
    values: dict

    model_config = {"populate_by_name": True}


@router.post("/rows", summary="Inserir uma linha numa tabela de usuário", status_code=201)
async def insert_row(body: RowIn, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    schema, table = body.schema_name, body.table
    qs, qt = _q(schema), _q(table)
    meta = await _table_kind(schema, table)
    if not meta["editable"]:
        raise HTTPException(400, "só tabelas de usuário aceitam inserção (views e o sistema são somente leitura)")
    cols = {r["name"]: r["type"] for r in await db.pool().fetch(
        "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname=$1 AND c.relname=$2 AND a.attnum > 0 AND NOT a.attisdropped", schema, table)}
    names, params = [], []
    for k, v in body.values.items():
        if k not in cols:
            raise HTTPException(400, f"coluna desconhecida: {k}")
        if v is None or (isinstance(v, str) and v.strip() == ""):
            continue                                  # vazio = NULL/default
        names.append(_q(k))
        params.append(str(v) if not isinstance(v, str) else v)
    if not names:
        raise HTTPException(400, "nenhum valor informado")
    # valores entram como texto e o PostgreSQL faz o cast para o tipo da coluna
    placeholders = ", ".join(f"${i + 1}::text::{cols[n.strip(chr(34))]}" for i, n in enumerate(names))
    sql = f"INSERT INTO {qs}.{qt} ({', '.join(names)}) VALUES ({placeholders}) RETURNING ctid::text"
    try:
        ctid = await db.pool().fetchval(sql, *params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    return {"inserted": True, "ctid": ctid}


class RowDelete(BaseModel):
    schema_name: str = Field(..., alias="schema")
    table: str
    ctid: str

    model_config = {"populate_by_name": True}


@router.delete("/rows", summary="Apagar uma linha (pelo ctid mostrado na listagem)")
async def delete_row(body: RowDelete, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    schema, table = body.schema_name, body.table
    meta = await _table_kind(schema, table)
    if not meta["editable"]:
        raise HTTPException(400, "só tabelas de usuário")
    if not re.match(r"^\(\d+,\d+\)$", body.ctid or ""):
        raise HTTPException(400, "ctid inválido")
    n = await db.pool().execute(f"DELETE FROM {_q(schema)}.{_q(table)} WHERE ctid = $1::text::tid", body.ctid)
    return {"deleted": int(n.split()[-1])}


class TableDrop(BaseModel):
    schema_name: str = Field(..., alias="schema")
    table: str
    confirm: str

    model_config = {"populate_by_name": True}


@router.delete("/tables", summary="Excluir uma tabela de usuário (confirmando o nome)")
async def drop_table(body: TableDrop, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    schema, table = body.schema_name, body.table
    meta = await _table_kind(schema, table)
    if not meta["editable"]:
        raise HTTPException(400, "só tabelas de usuário podem ser excluídas por aqui")
    if body.confirm.strip() != table:
        raise HTTPException(400, "digite o nome da tabela exatamente para confirmar")
    await db.pool().execute(f"DROP TABLE {_q(schema)}.{_q(table)}")
    return {"dropped": f"{schema}.{table}"}


# ---------------------------------------------------------------------------------
# Zona de perigo — apagar dados de uma estação (sempre com texto de confirmação)
# ---------------------------------------------------------------------------------
class StationTable(BaseModel):
    station: str
    file_key: str
    confirm: str


class StationVariable(BaseModel):
    station: str
    variable_id: str
    confirm: str


async def _file_variables(station: str, file_key: str) -> list[str]:
    row = await db.pool().fetchrow(
        "SELECT variables FROM monitored_files WHERE station_id = $1 AND file_key = $2", station, file_key)
    if not row:
        raise HTTPException(404, f"registro {station}/{file_key} não encontrado no banco")
    import json as _json
    vs = row["variables"]
    if isinstance(vs, str):
        vs = _json.loads(vs)
    return [v["variable_id"] for v in vs if v.get("variable_id") and (v.get("type") or "numero") != "texto"]


def _confirm(given: str, expected: str) -> None:
    if (given or "").strip() != expected:
        raise HTTPException(400, f"confirmação incorreta: digite exatamente '{expected}'")


async def _log_run(action: str, who: str, stats: dict) -> None:
    import json as _json
    await db.pool().execute(
        "INSERT INTO pipeline_runs (pipeline, version, finished, status, stats) VALUES ('danger', '1', now(), 'ok', $1::jsonb)",
        _json.dumps({"action": action, "by": who, **stats}))


@router.post("/station/clear", summary="Zerar os dados de uma tabela (arquivo) da estação e reingerir do zero")
async def station_clear(body: StationTable, who: auth.KeyInfo = Depends(auth.require("admin"))):
    _q(body.station); _q(body.file_key)
    _confirm(body.confirm, body.file_key)
    vids = await _file_variables(body.station, body.file_key)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            res = await conn.execute("DELETE FROM measurements WHERE station_id = $1 AND variable_id = ANY($2)", body.station, vids)
            await conn.execute(
                "UPDATE ingest_files SET bytes_ingested = 0, size_bytes = 0, head_sha256 = NULL, rows_total = 0, "
                "values_total = 0, bad_timestamps = 0, last_time = NULL, status = 'ok', error = NULL "
                "WHERE station_id = $1 AND file_key = $2", body.station, body.file_key)
    n = int(res.split()[-1])
    await _log_run("station_clear", who.owner, {"station": body.station, "file_key": body.file_key, "deleted": n})
    return {"deleted": n, "variables": len(vids), "note": "ledger zerado: a ingestão relê o arquivo do início no próximo ciclo"}


@router.post("/station/drop-variable", summary="Apagar todos os dados de UMA variável da estação")
async def station_drop_variable(body: StationVariable, who: auth.KeyInfo = Depends(auth.require("admin"))):
    _q(body.station)
    _confirm(body.confirm, body.variable_id)
    res = await db.pool().execute("DELETE FROM measurements WHERE station_id = $1 AND variable_id = $2",
                                  body.station, body.variable_id)
    n = int(res.split()[-1])
    await _log_run("station_drop_variable", who.owner, {"station": body.station, "variable_id": body.variable_id, "deleted": n})
    return {"deleted": n, "note": "se a variável continuar marcada para o banco na dashboard, os dados novos voltam a entrar; "
                                  "para reingerir o histórico, zere a tabela"}


@router.post("/station/drop-table", summary="Apagar a tabela da estação: dados, view e ledger; desliga a ingestão")
async def station_drop_table(body: StationTable, who: auth.KeyInfo = Depends(auth.require("admin"))):
    qs, qv = _q(body.station), _q(body.file_key)
    _confirm(body.confirm, body.file_key)
    vids = await _file_variables(body.station, body.file_key)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            res = await conn.execute("DELETE FROM measurements WHERE station_id = $1 AND variable_id = ANY($2)", body.station, vids)
            await conn.execute(f"DROP VIEW IF EXISTS {qs}.{qv}")
            await conn.execute("DELETE FROM ingest_files WHERE station_id = $1 AND file_key = $2", body.station, body.file_key)
            await conn.execute("UPDATE monitored_files SET ingest = false, updated_at = now() WHERE station_id = $1 AND file_key = $2",
                               body.station, body.file_key)
    n = int(res.split()[-1])
    await _log_run("station_drop_table", who.owner, {"station": body.station, "file_key": body.file_key, "deleted": n})
    return {"deleted": n, "dropped_view": f"{body.station}.{body.file_key}",
            "note": "ingestão deste arquivo desligada no espelho; a dashboard também desmarca o registro"}


class TableTruncate(BaseModel):
    schema_name: str = Field(..., alias="schema")
    table: str
    confirm: str
    model_config = {"populate_by_name": True}


class ColumnDrop(BaseModel):
    schema_name: str = Field(..., alias="schema")
    table: str
    column: str
    confirm: str
    model_config = {"populate_by_name": True}


@router.post("/tables/truncate", summary="Esvaziar uma tabela de usuário")
async def truncate_table(body: TableTruncate, who: auth.KeyInfo = Depends(auth.require("admin"))):
    meta = await _table_kind(body.schema_name, body.table)
    if not meta["editable"]:
        raise HTTPException(400, "só tabelas de usuário")
    _confirm(body.confirm, body.table)
    await db.pool().execute(f"TRUNCATE {_q(body.schema_name)}.{_q(body.table)}")
    await _log_run("truncate", who.owner, {"schema": body.schema_name, "table": body.table})
    return {"truncated": f"{body.schema_name}.{body.table}"}


@router.post("/tables/drop-column", summary="Apagar uma coluna de uma tabela de usuário")
async def drop_column(body: ColumnDrop, who: auth.KeyInfo = Depends(auth.require("admin"))):
    meta = await _table_kind(body.schema_name, body.table)
    if not meta["editable"]:
        raise HTTPException(400, "só tabelas de usuário")
    _confirm(body.confirm, body.column)
    try:
        await db.pool().execute(f"ALTER TABLE {_q(body.schema_name)}.{_q(body.table)} DROP COLUMN {_q(body.column)}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    await _log_run("drop_column", who.owner, {"schema": body.schema_name, "table": body.table, "column": body.column})
    return {"dropped_column": body.column}
