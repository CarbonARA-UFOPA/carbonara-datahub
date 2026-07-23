"""Leitura/escrita do config.yaml do dashboard.

O config é a única fonte de verdade do dashboard (estações, arquivos monitorados,
mapeamento de variáveis). A UI edita este arquivo — a escrita é ATÔMICA (arquivo
temporário + os.replace) e serializada por lock, para nunca corromper o YAML.
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
import threading
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9_-]+$")
INT_RE = re.compile(r"^-?\d+$")  # '--1'.lstrip('-').isdigit() é True; int('--1') lança

DEFAULT_CONFIG: dict = {
    "server": {"host": "0.0.0.0", "port": 8080, "refresh_seconds": 60},
    "data_root": "./data",           # /srv/carbonara em produção
    "connections_log": None,          # default: <data_root>/log/connections.log
    "stale_fallback_seconds": 3600,   # espelha RSYNC_HUB_STALE
    # Login de administração (edições). null = edição liberada (dev).
    # {username: ..., password_hash: <gerado com werkzeug generate_password_hash>}
    "auth": None,
    "stations": [],
}


class ConfigStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        with self._lock:
            raw = {}
            if self.path.is_file():
                raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["server"].update(raw.get("server") or {})
        for key in ("data_root", "connections_log", "stale_fallback_seconds", "auth"):
            if raw.get(key) is not None:
                cfg[key] = raw[key]
        cfg["stations"] = raw.get("stations") or []
        # Base para caminhos relativos: o diretório do próprio config.yaml —
        # independe do cwd de quem invocou o run.py.
        cfg["_base"] = str(self.path.parent.resolve())
        return cfg

    def save(self, cfg: dict) -> None:
        cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".config-", suffix=".yaml"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
                os.replace(tmp, self.path)
            except BaseException:
                os.unlink(tmp)
                raise


# --- Navegação no config ----------------------------------------------------------
def get_station(cfg: dict, station_id: str) -> dict | None:
    return next((s for s in cfg["stations"] if s.get("id") == station_id), None)


def get_file(station: dict, file_id: str) -> dict | None:
    return next((f for f in station.get("files", []) if f.get("id") == file_id), None)


def data_root(cfg: dict) -> Path:
    """Raiz de dados absoluta. data_root relativo é resolvido contra a pasta do config."""
    root = Path(cfg["data_root"]).expanduser()
    if not root.is_absolute():
        root = Path(cfg.get("_base", ".")) / root
    return root.resolve()


def resolve_data_path(cfg: dict, path: str) -> Path:
    """Caminho absoluto é usado como está; relativo é resolvido sob data_root
    (com checagem anti-traversal: `../` não pode escapar da raiz)."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    root = data_root(cfg)
    resolved = (root / p).resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"caminho relativo escapa de data_root: {path}")
    return resolved


def heartbeat_path(cfg: dict, station: dict) -> Path:
    if station.get("heartbeat_file"):
        return resolve_data_path(cfg, station["heartbeat_file"])
    return resolve_data_path(cfg, f"incoming/{station['id']}/.carbonara-status")


def connections_log_path(cfg: dict) -> Path:
    if cfg.get("connections_log"):
        return resolve_data_path(cfg, cfg["connections_log"])
    return resolve_data_path(cfg, "log/connections.log")


def unique_file_id(station: dict, path: str) -> str:
    """Gera um id (slug) para o arquivo monitorado a partir do nome, único na estação."""
    base = re.sub(r"[^a-z0-9_-]+", "-", Path(path).stem.lower()).strip("-") or "arquivo"
    existing = {f.get("id") for f in station.get("files", [])}
    fid, n = base, 2
    while fid in existing:
        fid = f"{base}-{n}"
        n += 1
    return fid


# --- Validação dos formulários ----------------------------------------------------
def validate_station(form: dict, *, is_new: bool, cfg: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    sid = (form.get("id") or "").strip().lower()
    name = (form.get("name") or "").strip()
    heartbeat = (form.get("heartbeat_file") or "").strip() or None
    if heartbeat:
        try:
            resolve_data_path(cfg, heartbeat)
        except (ValueError, KeyError):
            errors.append("Arquivo de watchdog: caminho inválido "
                          "('../' não pode escapar da raiz de dados).")
            heartbeat = None

    if not ID_RE.match(sid):
        errors.append("Identificador inválido: use apenas letras minúsculas, números, '-' e '_'.")
    if is_new and get_station(cfg, sid):
        errors.append(f"Já existe uma estação com o id '{sid}'.")
    if not name:
        name = sid

    def _coord(field: str, label: str, lo: float, hi: float) -> float | None:
        raw = (form.get(field) or "").strip().replace(",", ".")
        if raw == "":
            return None
        try:
            v = float(raw)
        except ValueError:
            errors.append(f"{label}: valor inválido ('{raw}').")
            return None
        if not (lo <= v <= hi):
            errors.append(f"{label}: fora do intervalo permitido ({lo} a {hi}).")
            return None
        return v

    errors_before = len(errors)
    latitude = _coord("latitude", "Latitude", -90.0, 90.0)
    longitude = _coord("longitude", "Longitude", -180.0, 180.0)
    if len(errors) == errors_before and (latitude is None) != (longitude is None):
        errors.append("Coordenadas: informe latitude E longitude (ou deixe ambas vazias).")
        latitude = longitude = None

    # Override opcional da janela de offline (min). Vazio = automática (heartbeat).
    offline_window_min = None
    raw = (form.get("offline_window_min") or "").strip()
    if raw:
        try:
            offline_window_min = int(raw)
            if not (1 <= offline_window_min <= 10080):
                raise ValueError
        except ValueError:
            errors.append(f"Janela de offline: valor inválido ('{raw}') — use minutos entre 1 e 10080.")
            offline_window_min = None

    return {"id": sid, "name": name, "heartbeat_file": heartbeat,
            "latitude": latitude, "longitude": longitude,
            "offline_window_min": offline_window_min, "files": []}, errors


def _parse_separator(raw: str) -> str:
    raw = raw or ","
    if raw in ("\\t", "tab", "TAB"):
        return "\t"
    if raw.strip() == "" and " " in raw:
        return " "  # separador espaço é válido (alguns .dat) — não pode virar ','
    return raw.strip() or ","


def validate_file_entry(form: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    path = (form.get("path") or "").strip()
    if not path:
        errors.append("Informe o caminho do arquivo monitorado.")

    def _int(field: str, default: int | None, lo: int, hi: int, label: str) -> int | None:
        raw = (form.get(field) or "").strip()
        if raw == "":
            return default
        try:
            v = int(raw)
            if not (lo <= v <= hi):
                raise ValueError
            return v
        except ValueError:
            errors.append(f"{label}: valor inválido ('{raw}').")
            return default

    tail_lines = _int("tail_lines", 100, 1, 10000, "Linhas do final")
    window_hours = _int("window_hours", None, 1, 8760, "Período a plotar (horas)")
    skip_header = _int("skip_header_lines", 0, 0, 100, "Linhas de cabeçalho a pular")
    header_line = _int("header_line", None, 1, 100, "Linha com nomes das colunas")

    separator = _parse_separator(form.get("separator") or ",")
    if len(separator) != 1:
        errors.append(f"Separador de colunas deve ter exatamente 1 caractere (recebi '{separator}').")
        separator = ","
    # "Ignorar aspas" (no_quotes): grava quotechar vazio = csv.QUOTE_NONE (divide os
    # campos entre aspas pelas vírgulas internas — para dataloggers que empacotam vários
    # valores numa string só, ex.: RadOut1). Senão, usa o caractere informado (padrão ").
    if form.get("no_quotes"):
        quotechar = ""
    else:
        quotechar = (form.get("quotechar") or '"').strip() or '"'
    decimal = (form.get("decimal") or ".").strip() or "."
    if decimal == separator:
        errors.append("Separador decimal não pode ser igual ao separador de colunas.")

    # Regex opcional de limpeza dos valores: os trechos casados são removidos de cada
    # célula (não do timestamp) antes de virar número — ex.: '[A-Za-z]' tira o 'm' de
    # 'm -125'. Validamos aqui para não gravar uma regex quebrada.
    strip_regex = (form.get("strip_regex") or "").strip()
    if strip_regex:
        try:
            re.compile(strip_regex)
        except re.error as exc:
            errors.append(f"Expressão para remover dos valores é inválida: {exc}")
            strip_regex = ""

    ts_col_raw = (form.get("ts_column") or "0").strip()
    ts_column: int | str = int(ts_col_raw) if INT_RE.match(ts_col_raw) else ts_col_raw
    ts_format = (form.get("ts_format") or "%Y-%m-%d %H:%M:%S").strip()

    def _getlist(field: str) -> list:
        return form.getlist(field) if hasattr(form, "getlist") else form.get(field, [])

    def _float_or_none(raw: str, what: str) -> float | None:
        raw = (raw or "").strip().replace(",", ".")
        if raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            errors.append(f"{what}: valor inválido ('{raw}').")
            return None

    variables = []
    cols = _getlist("var_column")
    labels = _getlist("var_label")
    units = _getlist("var_unit")
    mins = _getlist("var_min")
    maxs = _getlist("var_max")
    for i, col in enumerate(cols):
        col = (col or "").strip()
        if not col:
            continue
        column: int | str = int(col) if INT_RE.match(col) else col
        # Índices únicos e distintos da coluna do timestamp (uma variável por coluna).
        if str(column) == str(ts_column):
            errors.append(f"A coluna {column} é a do timestamp — remova-a das variáveis.")
            continue
        if any(str(column) == str(v["column"]) for v in variables):
            errors.append(f"Índice de coluna repetido: {column} — cada variável deve ter um índice único.")
            continue
        label = (labels[i] if i < len(labels) else "").strip() or str(column)
        unit = (units[i] if i < len(units) else "").strip() or None
        vmin = _float_or_none(mins[i] if i < len(mins) else "", f"Limite mínimo de '{label}'")
        vmax = _float_or_none(maxs[i] if i < len(maxs) else "", f"Limite máximo de '{label}'")
        if vmin is not None and vmax is not None and vmin > vmax:
            errors.append(f"'{label}': limite mínimo ({vmin}) maior que o máximo ({vmax}).")
        variables.append({"column": column, "label": label, "unit": unit,
                          "min": vmin, "max": vmax})
    if not variables:
        errors.append("Defina pelo menos uma variável (coluna + rótulo).")

    entry = {
        "path": path,
        "tail_lines": tail_lines,
        "window_hours": window_hours,
        "separator": separator,
        "quotechar": quotechar,
        "decimal": decimal,
        "skip_header_lines": skip_header,
        "header_line": header_line,
        "timestamp": {"column": ts_column, "format": ts_format},
        "variables": variables,
    }
    if strip_regex:
        entry["strip_regex"] = strip_regex
    return entry, errors
