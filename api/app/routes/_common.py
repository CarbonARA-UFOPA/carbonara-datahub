"""Utilidades comuns às rotas: datas, fusos, formatação."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException


def parse_when(raw: str | None, default: datetime) -> datetime:
    """ISO 8601 (com ou sem fuso; sem fuso = UTC), ou atalhos relativos 'now', 'now-7d',
    'now-12h', 'now-30m'."""
    if not raw:
        return default
    s = raw.strip()
    if s.startswith("now"):
        rest = s[3:].replace(" ", "")
        base = datetime.now(timezone.utc)
        if not rest:
            return base
        try:
            sign = -1 if rest[0] == "-" else 1
            n = int(rest[1:-1])
            unit = rest[-1]
            delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
                     "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        except (KeyError, ValueError, IndexError) as exc:
            raise HTTPException(400, f"instante inválido: '{raw}' (ex.: now-7d, 2026-09-01T00:00:00Z)") from exc
        return base + sign * delta
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise HTTPException(400, f"instante inválido: '{raw}' (use ISO 8601, ex.: 2026-09-01T00:00:00Z)") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tzinfo_of(name: str | None):
    if not name or name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"fuso inválido: '{name}' (ex.: America/Santarem)") from exc


def iso(dt: datetime | None, tz) -> str | None:
    if dt is None:
        return None
    out = dt.astimezone(tz).isoformat(timespec="milliseconds")
    return out.replace("+00:00", "Z")


def split_csv(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]
