from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ._common import iso, tzinfo_of

router = APIRouter(tags=["estações"])


def _geojson(row) -> dict | None:
    if row["latitude"] is None or row["longitude"] is None:
        return None
    return {"type": "Point", "coordinates": [row["longitude"], row["latitude"]]}


@router.get("/stations", summary="Estações (aberto)")
async def list_stations(_who: auth.KeyInfo = Depends(auth.optional)):
    rows = await db.pool().fetch(
        """
        SELECT s.station_id, s.name, s.latitude, s.longitude, s.elevation_m, s.forest_type,
               s.is_mobile, s.timezone,
               (SELECT count(*) FROM monitored_files m WHERE m.station_id = s.station_id AND m.ingest) AS files_ingested,
               (SELECT max(last_time) FROM ingest_files i WHERE i.station_id = s.station_id) AS last_time
        FROM stations s ORDER BY s.station_id
        """)
    return [{
        "station_id": r["station_id"], "name": r["name"],
        "latitude": r["latitude"], "longitude": r["longitude"], "elevation_m": r["elevation_m"],
        "forest_type": r["forest_type"], "is_mobile": r["is_mobile"], "timezone": r["timezone"],
        "geometry": _geojson(r), "files_ingested": r["files_ingested"],
        "last_time": iso(r["last_time"], tzinfo_of(None)),
    } for r in rows]


@router.get("/stations/{station_id}", summary="Uma estação, com os arquivos ingeridos (aberto)")
async def get_station(station_id: str, _who: auth.KeyInfo = Depends(auth.optional)):
    s = await db.pool().fetchrow("SELECT * FROM stations WHERE station_id = $1", station_id)
    if not s:
        raise HTTPException(404, "estação não encontrada")
    files = await db.pool().fetch(
        "SELECT file_key, path, alias, dynamic_path, timezone, resample, ingest, variables, data_type, updated_at "
        "FROM monitored_files WHERE station_id = $1 ORDER BY data_type, file_key", station_id)
    return {
        "station_id": s["station_id"], "name": s["name"], "latitude": s["latitude"],
        "longitude": s["longitude"], "elevation_m": s["elevation_m"],
        "forest_type": s["forest_type"], "is_mobile": s["is_mobile"], "timezone": s["timezone"],
        "installed_at": iso(s["installed_at"], tzinfo_of(None)),
        "metadata": json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"],
        "geometry": _geojson(s),
        "files": [{
            "file_key": f["file_key"], "path": f["path"], "alias": f["alias"],
            "dynamic_path": f["dynamic_path"], "timezone": f["timezone"],
            "resample": f["resample"], "ingest": f["ingest"], "data_type": f["data_type"],
            "variables": json.loads(f["variables"]) if isinstance(f["variables"], str) else f["variables"],
            "updated_at": iso(f["updated_at"], tzinfo_of(None)),
        } for f in files],
    }


@router.get("/stations/{station_id}/variables", summary="Variáveis da estação, com o último valor")
async def station_variables(station_id: str, tz: str | None = None,
                            _who: auth.KeyInfo = Depends(auth.require("read"))):
    tzi = tzinfo_of(tz)
    rows = await db.pool().fetch(
        """
        SELECT DISTINCT ON (m.variable_id) m.variable_id, v.name, v.unit, m.time, m.value, m.quality
        FROM measurements m JOIN variables v USING (variable_id)
        WHERE m.station_id = $1
        ORDER BY m.variable_id, m.time DESC
        """, station_id)
    return [{"variable_id": r["variable_id"], "name": r["name"], "unit": r["unit"],
             "last_time": iso(r["time"], tzi), "last_value": r["value"],
             "last_quality": r["quality"]} for r in rows]
