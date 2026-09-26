"""Carimbos de tempo: texto (strftime tolerante), Unix epoch, fuso e exibição.

Regra do projeto: o BANCO guarda sempre UTC. O fuso em que cada arquivo escreve seus
carimbos é um atributo do registro do arquivo (``timezone``, IANA) — cada estação/
equipamento pode gravar em UTC ou em hora local. ``to_utc`` é o único ponto onde um
carimbo "naive" ganha fuso.
"""
from __future__ import annotations

from datetime import datetime, timezone

try:  # py3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

# Carimbo em Unix epoch: o "formato" deixa de ser strftime e passa a significar "número
# de segundos desde 1970-01-01 UTC".
EPOCH_MARKERS = {"epoch", "unix", "unixtime", "posix", "%s", "epoch_s"}


def is_epoch_format(fmt: str | None) -> bool:
    return (fmt or "").strip().lower() in EPOCH_MARKERS


# Exibição de carimbo de tempo: data completa SEMPRE, e a resolução do relógio ACOMPANHA
# a cadência real dos dados. Um datalogger de 30 min não precisa de segundos (poluem o
# eixo); um arquivo com registros a cada segundo — ou frações, como os .sfo — fica
# ilegível sem eles: linhas diferentes viravam o mesmo rótulo.
TS_DISPLAY = "%d/%m/%Y %H:%M"            # cadência de 1 min ou mais
TS_DISPLAY_S = "%d/%m/%Y %H:%M:%S"       # cadência de 1 s a 1 min
TS_DISPLAY_MS = "%d/%m/%Y %H:%M:%S.%f"   # abaixo de 1 s (cortado em milissegundos)


def ts_display_format(dts: list[datetime | None]) -> str:
    """Formato de exibição adequado à cadência da série (MENOR intervalo entre carimbos
    consecutivos; todos iguais → resolução máxima, o que expõe uma coluna de tempo que
    não avança por registro)."""
    xs = sorted(d for d in dts if d is not None)
    passos = [(b - a).total_seconds() for a, b in zip(xs, xs[1:]) if b > a]
    if not passos:
        return TS_DISPLAY_MS
    menor = min(passos)
    if menor >= 60:
        return TS_DISPLAY
    if menor >= 1:
        return TS_DISPLAY_S
    return TS_DISPLAY_MS


def fmt_ts(dt: datetime, fmt: str) -> str:
    """Aplica o formato; em milissegundos corta os microssegundos do strftime."""
    out = dt.strftime(fmt)
    return out[:-3] if fmt.endswith(".%f") else out


_EPOCH_MIN = datetime(1990, 1, 1)     # antes disto não há estação deste projeto
_EPOCH_MAX = datetime(2100, 1, 1)
_EPOCH_MIN_UTC = _EPOCH_MIN.replace(tzinfo=timezone.utc)
_EPOCH_MAX_UTC = _EPOCH_MAX.replace(tzinfo=timezone.utc)


def epoch_seconds(raw: str) -> float | None:
    """Número de segundos de um campo epoch, com a ESCALA deduzida da ordem de grandeza
    (s, ms, µs, ns — as faixas não se sobrepõem neste século). None se não é número."""
    try:
        v = float((raw or "").strip().strip('"').replace(",", "."))
    except (TypeError, ValueError):
        return None
    a = abs(v)
    if a >= 1e17:
        v /= 1e9        # nanossegundos
    elif a >= 1e14:
        v /= 1e6        # microssegundos
    elif a >= 1e11:
        v /= 1e3        # milissegundos
    return v


def parse_epoch(raw: str, tz=None) -> datetime | None:
    """Unix epoch → datetime. Sem ``tz`` devolve um datetime naive no fuso do processo
    (comportamento da dashboard); com ``tz`` (ex.: ``timezone.utc``) devolve aware.

    Rejeita valores fora de 1990–2100: apontar o eixo de tempo para uma coluna que NÃO
    é epoch (um ângulo '359', uma temperatura '26.43') converteria em silêncio para
    01/01/1970 em vez de dizer que a coluna está errada."""
    v = epoch_seconds(raw)
    if v is None:
        return None
    try:
        dt = datetime.fromtimestamp(v, tz) if tz is not None else datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return None
    lo, hi = (_EPOCH_MIN_UTC, _EPOCH_MAX_UTC) if tz is not None else (_EPOCH_MIN, _EPOCH_MAX)
    if not (lo <= dt <= hi):
        return None
    return dt


def parse_timestamp(raw: str, fmt: str | None) -> datetime | None:
    """Parse TOLERANTE do timestamp (naive). Dataloggers Campbell/TOA5 gravam fração de
    segundo ('2026-07-23 17:00:03.607'), que o formato padrão '%Y-%m-%d %H:%M:%S' não
    casa: tentamos o formato como está e uma variante com/sem '.%f'."""
    if is_epoch_format(fmt):
        return parse_epoch(raw)
    raw = (raw or "").strip().strip('"')
    fmt = fmt or "%Y-%m-%d %H:%M:%S"
    candidates = [fmt]
    if "%f" not in fmt:
        candidates.append(fmt + ".%f")
    else:
        candidates.append(fmt.replace(".%f", "").replace("%f", "").rstrip("."))
    for f in candidates:
        try:
            return datetime.strptime(raw, f)
        except ValueError:
            continue
    return None


# Formatos tentados para uma coluna do tipo "timestamp" (data/hora em TEXTO).
_TEXT_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                    "%Y%m%d%H%M%S", "%Y%m%d", "%H:%M:%S", "%H:%M")


def parse_datetime_text(raw: str, prefer: str | None = None) -> datetime | None:
    """Data/hora em texto, descobrindo o formato entre os usuais (o do arquivo primeiro)."""
    tentativas = []
    if prefer and not is_epoch_format(prefer):
        tentativas.append(prefer)
    tentativas.extend(f for f in _TEXT_TS_FORMATS if f != prefer)
    for f in tentativas:
        dt = parse_timestamp(raw, f)
        if dt is not None:
            return dt
    return None


def valid_timezone(name: str | None) -> bool:
    """True se ``name`` é um fuso IANA conhecido (ex.: 'America/Santarem', 'UTC')."""
    if not name or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001 — chave inválida/ausente no banco de fusos
        return False
    return True


def to_utc(dt: datetime | None, tzname: str | None) -> datetime | None:
    """Carimbo do arquivo → instante UTC (aware). Um datetime naive é interpretado no
    fuso ``tzname`` (IANA); sem fuso informado assume UTC — a regra mais segura para
    dataloggers, que normalmente gravam em UTC ou em hora "padrão" sem horário de
    verão. Um datetime já aware (epoch) só é convertido."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        tz = timezone.utc
        if tzname and ZoneInfo is not None:
            try:
                tz = ZoneInfo(tzname)
            except Exception:  # noqa: BLE001
                tz = timezone.utc
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_file_timestamp_utc(raw: str, fmt: str | None, tzname: str | None) -> datetime | None:
    """Carimbo da COLUNA DE TEMPO do arquivo, já em UTC. Epoch é UTC por definição e
    ignora ``tzname``; texto é localizado em ``tzname``."""
    if is_epoch_format(fmt):
        return parse_epoch(raw, timezone.utc)
    return to_utc(parse_timestamp(raw, fmt), tzname)
