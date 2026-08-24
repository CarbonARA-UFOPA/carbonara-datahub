"""Status das estações — porta fiel da lógica do `rsync-hub monitor` para Python.

Fontes (mesmas do shell, collection/rsync-hub · print_status):
- heartbeat `.carbonara-status`: o MTIME é o último sinal de vida (o arquivo chega sem
  -t, então mtime = hora de chegada no servidor); `interval_min` vem do CONTEÚDO;
- mtime mais recente dos arquivos de dados (não ocultos) em incoming/<id>/;
- último CONNECT da estação em log/connections.log.

Janela de Offline: ``interval_min*120 + 120`` segundos (2×intervalo + 2 min); sem
interval válido, o fallback global ``stale_fallback_seconds`` (default 1h).

A PAUSA da recepção (``rsync-hub pause``) tem prioridade sobre tudo isso: com a
recepção suspensa a estação não consegue nem mandar o sinal de vida, então o estado
vira ``paused`` — chamá-la de "Offline" seria um diagnóstico enganoso (o
``print_status`` do shell faz o mesmo).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # py3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from . import config_store, hub, tailer

_INTERVAL_RE = re.compile(r"^interval_min=(\d+)\s*$", re.M)
_LOG_TAIL = 500  # linhas do fim do connections.log a examinar


def _fmt_signal(epoch: float, cfg: dict) -> str:
    """Formata o horário do último sinal em HORA LOCAL.

    O status (online/offline) é sempre calculado em epoch (imune a fuso); isto é só a
    EXIBIÇÃO. Por padrão usa o fuso do processo; se o processo roda em UTC (ex.: serviço
    sem TZ) enquanto o servidor está em -03, defina ``timezone`` no config (ex.:
    ``America/Santarem``) para forçar a hora local correta.
    """
    tzname = cfg.get("timezone")
    if tzname and ZoneInfo is not None:
        try:
            return datetime.fromtimestamp(epoch, ZoneInfo(tzname)).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            pass
    return datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M:%S")


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
        try:
            if f.is_file():
                m = f.stat().st_mtime
                latest = m if latest is None or m > latest else latest
        except OSError:
            continue  # a ingestão move arquivos para archive/ a qualquer momento
    return latest


def station_status(cfg: dict, station: dict, pause: dict | None = None) -> dict:
    """``pause``: estado de pausa já lido (``hub.pause_state``) — quem monta uma LISTA
    de estações passa o estado uma vez só, em vez de reler os marcadores por estação."""
    now = time.time()
    # Caminhos configurados à mão podem ser inválidos (ex.: '../' escapando a raiz);
    # um cadastro ruim nunca pode derrubar a página inteira — vira "não encontrado".
    hb_display, hb_found = station.get("heartbeat_file") or "", False
    hb_mtime, interval_min = None, None
    try:
        hb = config_store.heartbeat_path(cfg, station)
        hb_display = str(hb)
        if hb.is_file():
            hb_found = True
            hb_mtime = hb.stat().st_mtime
            m = _INTERVAL_RE.search(hb.read_text(encoding="utf-8", errors="replace"))
            if m:
                interval_min = int(m.group(1))
    except (ValueError, OSError):
        pass

    # Pasta incoming e log ancorados no heartbeat (pai do .carbonara-status = incoming
    # real da estação), não no data_root — assim a dashboard lê os MESMOS caminhos que o
    # rsync-hub mesmo quando o data_root do config aponta para outro lugar.
    try:
        incoming_dir = config_store.station_incoming_dir(cfg, station)
    except (ValueError, KeyError):
        incoming_dir = config_store.resolve_data_path(cfg, f"incoming/{station['id']}")
    data_mtime = _latest_data_mtime(incoming_dir)
    try:
        log_path = config_store.connections_log_path_for(cfg, station)
    except ValueError:
        log_path = Path("/nonexistent")
    last_event, last_connect = _last_events(log_path, station["id"])

    candidates = [t for t in (hb_mtime, data_mtime, last_connect) if t is not None]
    act = max(candidates) if candidates else None

    # Janela SEMPRE derivada do heartbeat (2×intervalo + 2 min) ou do fallback global —
    # não há override por estação: a cadência é decidida na estação (tempo de
    # reciclagem do cliente), e um ajuste local só faria a dashboard divergir do
    # `rsync-hub monitor` e do watchdog do próprio cliente, que usam a mesma conta.
    if interval_min:
        limit = interval_min * 120 + 120
    else:
        limit = int(cfg["stale_fallback_seconds"])

    receiving = last_event == "CONNECT" or (act is not None and now - act <= 60)
    if act is None:
        status = "unknown"
    elif receiving:
        status = "receiving"
    elif now - act <= limit:
        status = "online"
    else:
        status = "offline"

    # Recepção pausada no hub: sobrepõe o estado calculado (ver docstring do módulo).
    pstate = pause if pause is not None else hub.pause_state(cfg)
    paused = hub.station_paused(pstate, station["id"])
    marker = hub.pause_marker_for(pstate, station["id"]) if paused else None
    if paused:
        status = "paused"
        receiving = False

    return {
        "id": station["id"],
        "name": station.get("name") or station["id"],
        "status": status,
        "receiving": receiving,
        "paused": paused,
        # Escopo/motivo da pausa para a interface ("todas" = pausa global do hub).
        "pause_scope": (marker or {}).get("target"),
        "pause_reason": (marker or {}).get("reason"),
        "pause_since": (marker or {}).get("since_display"),
        "last_signal": (_fmt_signal(act, cfg) if act else None),
        "seconds_since_signal": int(now - act) if act else None,
        "interval_min": interval_min,
        "limit_seconds": limit,
        "heartbeat_file": hb_display,
        "heartbeat_found": hb_found,
        "files": len(station.get("files", [])),
        "latitude": station.get("latitude"),
        "longitude": station.get("longitude"),
    }
