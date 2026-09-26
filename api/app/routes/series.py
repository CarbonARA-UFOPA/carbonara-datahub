"""Séries temporais: cruas (``measurements``) ou agregadas (agregados contínuos
horário/diário, em tempo real e só com ``quality = 0``)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import auth, db, settings
from ._common import iso, parse_when, split_csv, tzinfo_of

router = APIRouter(tags=["séries"])

AGG_VIEWS = {"1h": "measurements_hourly", "1d": "measurements_daily"}


@router.get("/series", summary="Série temporal de uma estação")
async def get_series(
    station: str = Query(..., description="station_id"),
    vars: str | None = Query(None, description="variable_id(s) separados por vírgula; vazio = todas"),
    start: str | None = Query(None, description="ISO 8601 ou now-7d / now-12h; padrão: 24 h atrás (raw), 30 d (1h), 1 ano (1d)"),
    end: str | None = Query(None, description="ISO 8601 ou now; padrão: agora"),
    agg: str = Query("raw", pattern="^(raw|1h|1d)$", description="raw = cada registro; 1h/1d = média/min/max de valores aprovados"),
    qc: str = Query("good", pattern="^(good|all)$", description="raw: good = só quality=0; all = tudo, com o flag"),
    tz: str | None = Query(None, description="fuso dos carimbos na resposta (padrão UTC)"),
    limit: int | None = Query(None, ge=1, description="máximo de pontos (raw)"),
    order: str = Query("asc", pattern="^(asc|desc)$", description="desc = os ÚLTIMOS pontos até end (raw; start opcional)"),
    format: str = Query("json", pattern="^(json|csv)$"),
    who: auth.KeyInfo = Depends(auth.require("read")),
):
    tzi = tzinfo_of(tz)
    end_dt = parse_when(end, datetime.now(timezone.utc))
    default_span = {"raw": timedelta(days=1), "1h": timedelta(days=30), "1d": timedelta(days=365)}[agg]
    if agg == "raw" and order == "desc" and not start:
        default_span = timedelta(days=36500)     # "os últimos N", sem janela
    start_dt = parse_when(start, end_dt - default_span)
    if start_dt >= end_dt:
        raise HTTPException(400, "start deve ser anterior a end")
    variables = split_csv(vars)

    if agg == "raw":
        cap = settings.MAX_RAW_POINTS if who.has("download") else settings.DEFAULT_RAW_POINTS * 5
        lim = min(limit or settings.DEFAULT_RAW_POINTS, cap)
        direction = "DESC" if order == "desc" else "ASC"
        sql = f"""
            SELECT m.time, m.variable_id, m.value, m.quality
            FROM measurements m
            WHERE m.station_id = $1 AND m.time >= $2 AND m.time < $3
              AND ($4::text[] IS NULL OR m.variable_id = ANY($4))
              AND ($5::bool OR m.quality = 0)
            ORDER BY m.time {direction}, m.variable_id
            LIMIT $6
        """
        rows = await db.pool().fetch(sql, station, start_dt, end_dt,
                                     variables or None, qc == "all", lim + 1)
        truncated = len(rows) > lim
        rows = rows[:lim]
        if order == "desc":
            rows = list(reversed(rows))            # resposta sempre cronológica
    else:
        view = AGG_VIEWS[agg]
        lim = settings.MAX_AGG_BUCKETS
        sql = f"""
            SELECT bucket AS time, variable_id, avg_value AS value, min_value, max_value,
                   n_samples, n_flagged
            FROM {view}
            WHERE station_id = $1 AND bucket >= $2 AND bucket < $3
              AND ($4::text[] IS NULL OR variable_id = ANY($4))
            ORDER BY bucket, variable_id
            LIMIT $5
        """
        rows = await db.pool().fetch(sql, station, start_dt, end_dt, variables or None, lim + 1)
        truncated = len(rows) > lim
        rows = rows[:lim]

    meta = await db.pool().fetch(
        "SELECT variable_id, name, unit FROM variables WHERE variable_id = ANY($1)",
        sorted({r["variable_id"] for r in rows}) or variables or [])
    names = {m["variable_id"]: (m["name"], m["unit"]) for m in meta}

    if format == "csv":
        return _csv(rows, agg, tzi, station)

    series: dict[str, dict] = {}
    for r in rows:
        vid = r["variable_id"]
        s = series.get(vid)
        if s is None:
            nome, unit = names.get(vid, (vid, None))
            s = series[vid] = {"variable_id": vid, "name": nome, "unit": unit,
                               "times": [], "values": []}
            if agg == "raw":
                s["quality"] = []
            else:
                s.update({"min": [], "max": [], "n": [], "n_flagged": []})
        s["times"].append(iso(r["time"], tzi))
        s["values"].append(r["value"])
        if agg == "raw":
            s["quality"].append(r["quality"])
        else:
            s["min"].append(r["min_value"])
            s["max"].append(r["max_value"])
            s["n"].append(r["n_samples"])
            s["n_flagged"].append(r["n_flagged"])
    return {
        "station": station, "agg": agg, "qc": qc if agg == "raw" else "good",
        "start": iso(start_dt, tzi), "end": iso(end_dt, tzi), "tz": tz or "UTC",
        "points": len(rows), "truncated": truncated,
        "series": list(series.values()),
    }


def _csv(rows, agg: str, tzi, station: str) -> StreamingResponse:
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        if agg == "raw":
            w.writerow(["time", "station_id", "variable_id", "value", "quality"])
        else:
            w.writerow(["bucket", "station_id", "variable_id", "avg", "min", "max", "n", "n_flagged"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0); buf.truncate()
            if agg == "raw":
                w.writerow([iso(r["time"], tzi), station, r["variable_id"], r["value"], r["quality"]])
            else:
                w.writerow([iso(r["time"], tzi), station, r["variable_id"], r["value"],
                            r["min_value"], r["max_value"], r["n_samples"], r["n_flagged"]])
            yield buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{station}_{agg}.csv"'})


@router.get("/latest", summary="Último valor de cada variável de uma estação")
async def latest(station: str, vars: str | None = None, tz: str | None = None,
                 _who: auth.KeyInfo = Depends(auth.require("read"))):
    tzi = tzinfo_of(tz)
    variables = split_csv(vars)
    rows = await db.pool().fetch(
        """
        SELECT DISTINCT ON (m.variable_id) m.variable_id, v.name, v.unit, m.time, m.value, m.quality
        FROM measurements m JOIN variables v USING (variable_id)
        WHERE m.station_id = $1 AND ($2::text[] IS NULL OR m.variable_id = ANY($2))
        ORDER BY m.variable_id, m.time DESC
        """, station, variables or None)
    return {"station": station, "tz": tz or "UTC", "values": [
        {"variable_id": r["variable_id"], "name": r["name"], "unit": r["unit"],
         "time": iso(r["time"], tzi), "value": r["value"], "quality": r["quality"]}
        for r in rows]}
