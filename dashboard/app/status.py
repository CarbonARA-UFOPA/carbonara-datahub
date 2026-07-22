"""Status das estações — porta fiel da lógica do `rsync-hub monitor` para Python.

Fontes (mesmas do shell, collection/rsync-hub · print_status):
- heartbeat `.carbonara-status`: o MTIME é o último sinal de vida (o arquivo chega sem
  -t, então mtime = hora de chegada no servidor); `interval_min` vem do CONTEÚDO;
- mtime mais recente dos arquivos de dados (não ocultos) em incoming/<id>/;
- último CONNECT da estação em log/connections.log.

Janela de Offline: ``interval_min*120 + 120`` segundos (2×intervalo + 2 min); sem
interval válido, o fallback global ``stale_fallback_seconds`` (default 1h).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config_store, tailer

_INTERVAL_RE = re.compile(r"^interval_min=(\d+)\s*$", re.M)
_LOG_TAIL = 500  # linhas do fim do connections.log a examinar


def _parse_log_ts(ts: str) -> float | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _last_events(log_path: Path, station_id: str) -> tuple[str | None, float | None]:
    """(último tipo de evento SSH da estação, epoch do último CONNECT)."""
    if not log_path.is_file():
        return None, None
    lines, _ = tailer.tail_lines(log_path, _LOG_TAIL)
    last_event, last_connect = None, None
    for line in lines:
        parts = line.split()
        if len(parts) < 3 or parts[2] != station_id:
            continue
        ts, event = _parse_log_ts(parts[0]), parts[1]
        if event in ("CONNECT", "DISCONNECT"):
            last_event = event
        if event == "CONNECT" and ts is not None:
            last_connect = ts
    return last_event, last_connect


def _latest_data_mtime(incoming_dir: Path) -> float | None:
    """Mtime mais recente entre os arquivos não ocultos da pasta da estação."""
    if not incoming_dir.is_dir():
        return None
    latest = None
    for f in incoming_dir.rglob("*"):
        rel = f.relative_to(incoming_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if f.is_file():
            m = f.stat().st_mtime
            latest = m if latest is None or m > latest else latest
    return latest


def station_status(cfg: dict, station: dict) -> dict:
    now = time.time()
    hb = config_store.heartbeat_path(cfg, station)

    hb_mtime, interval_min = None, None
    if hb.is_file():
        hb_mtime = hb.stat().st_mtime
        m = _INTERVAL_RE.search(hb.read_text(encoding="utf-8", errors="replace"))
        if m:
            interval_min = int(m.group(1))

    incoming_dir = config_store.resolve_data_path(cfg, f"incoming/{station['id']}")
    data_mtime = _latest_data_mtime(incoming_dir)
    last_event, last_connect = _last_events(config_store.connections_log_path(cfg), station["id"])

    candidates = [t for t in (hb_mtime, data_mtime, last_connect) if t is not None]
    act = max(candidates) if candidates else None

    limit = interval_min * 120 + 120 if interval_min else int(cfg["stale_fallback_seconds"])

    receiving = last_event == "CONNECT" or (act is not None and now - act <= 60)
    if act is None:
        status = "unknown"
    elif receiving:
        status = "receiving"
    elif now - act <= limit:
        status = "online"
    else:
        status = "offline"

    return {
        "id": station["id"],
        "name": station.get("name") or station["id"],
        "status": status,
        "receiving": receiving,
        "last_signal": (
            datetime.fromtimestamp(act).strftime("%d/%m/%Y %H:%M:%S") if act else None
        ),
        "seconds_since_signal": int(now - act) if act else None,
        "interval_min": interval_min,
        "limit_seconds": limit,
        "heartbeat_file": str(hb),
        "heartbeat_found": hb.is_file(),
        "files": len(station.get("files", [])),
    }
