"""Estado da ingestão: o ledger (um registro por arquivo real lido)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import auth, db
from ._common import iso, tzinfo_of

router = APIRouter(tags=["ingestão"])


@router.get("/files", summary="Arquivos lidos pela ingestão (ledger)")
async def list_files(station: str | None = None, file_key: str | None = None,
                     status: str | None = None, tz: str | None = None,
                     _who: auth.KeyInfo = Depends(auth.require("read"))):
    tzi = tzinfo_of(tz)
    rows = await db.pool().fetch(
        """
        SELECT i.file_id, i.station_id, i.file_key, i.relpath, i.size_bytes, i.mtime,
               i.bytes_ingested, i.rows_total, i.values_total, i.bad_timestamps, i.last_time,
               i.status, i.error, i.first_seen, i.last_ingested,
               m.alias, m.resample, m.timezone, m.ingest
        FROM ingest_files i
        LEFT JOIN monitored_files m ON m.station_id = i.station_id AND m.file_key = i.file_key
        WHERE ($1::text IS NULL OR i.station_id = $1)
          AND ($2::text IS NULL OR i.file_key = $2)
          AND ($3::text IS NULL OR i.status = $3)
        ORDER BY i.station_id, i.file_key, i.relpath
        """, station, file_key, status)
    return [{
        "file_id": r["file_id"], "station_id": r["station_id"], "file_key": r["file_key"],
        "alias": r["alias"], "path": r["relpath"], "size_bytes": r["size_bytes"],
        "mtime": iso(r["mtime"], tzi), "bytes_ingested": r["bytes_ingested"],
        "pending_bytes": max(0, (r["size_bytes"] or 0) - (r["bytes_ingested"] or 0)),
        "rows_total": r["rows_total"], "values_total": r["values_total"],
        "bad_timestamps": r["bad_timestamps"], "last_time": iso(r["last_time"], tzi),
        "status": r["status"], "error": r["error"], "ingest": r["ingest"],
        "resample": r["resample"], "timezone": r["timezone"],
        "first_seen": iso(r["first_seen"], tzi), "last_ingested": iso(r["last_ingested"], tzi),
    } for r in rows]


@router.get("/monitored", summary="Registros que a ingestão já viu (espelho monitored_files)")
async def monitored(station: str | None = None, tz: str | None = None,
                    _who: auth.KeyInfo = Depends(auth.require("read"))):
    tzi = tzinfo_of(tz)
    rows = await db.pool().fetch(
        "SELECT station_id, file_key, ingest, data_type, resample, timezone, updated_at FROM monitored_files "
        "WHERE ($1::text IS NULL OR station_id = $1) ORDER BY 1, 2", station)
    return [{**dict(r), "updated_at": iso(r["updated_at"], tzi)} for r in rows]
