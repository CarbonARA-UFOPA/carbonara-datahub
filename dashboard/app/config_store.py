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
from datetime import datetime
from pathlib import Path

import yaml

try:  # py3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

ID_RE = re.compile(r"^[a-z0-9_-]+$")
INT_RE = re.compile(r"^-?\d+$")  # '--1'.lstrip('-').isdigit() é True; int('--1') lança

DEFAULT_CONFIG: dict = {
    "server": {"host": "0.0.0.0", "port": 8080, "refresh_seconds": 60},
    "data_root": "./data",           # /srv/carbonara em produção (raiz do hub — config master)
    "connections_log": None,          # default: <data_root>/log/connections.log
    "timezone": None,                 # fuso p/ exibir o "último sinal" (ex.: America/Santarem)
    "stale_fallback_seconds": 3600,   # espelha RSYNC_HUB_STALE
    "rsync_hub_bin": None,            # override do executável rsync-hub (gerenciamento)
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
        for key in ("data_root", "connections_log", "timezone", "stale_fallback_seconds",
                    "rsync_hub_bin", "auth"):
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


def connections_log_path_for(cfg: dict, station: dict) -> Path:
    """Como ``connections_log_path``, mas com um resgate por estação: se o log sob o
    ``data_root`` não existir, deriva a BASE real do hub a partir do heartbeat da estação
    (``incoming/<id>`` → BASE) e procura ``BASE/log/connections.log`` lá. Isso faz a
    dashboard ler o MESMO log do ``rsync-hub`` mesmo quando o ``data_root`` do config
    aponta para outro lugar (ex.: ``./data`` em dev, mas heartbeat em ``/srv/carbonara``).
    Um ``connections_log`` explícito no config continua tendo prioridade."""
    p = connections_log_path(cfg)
    if cfg.get("connections_log") or p.is_file():
        return p
    try:
        derived = station_incoming_dir(cfg, station).parent.parent / "log" / "connections.log"
        if derived.is_file():
            return derived
    except (ValueError, KeyError, OSError):
        pass
    return p


def detect_hub_stations(cfg: dict) -> list[dict]:
    """Detecta as estações configuradas no rsync-hub a partir da raiz do hub
    (``data_root``). A dashboard é complementar ao rsync-hub: em vez de cadastro manual,
    lê a lista dele. União de quatro fontes (qualquer uma basta), tolerando ausências:

    - subpastas de ``incoming/``  — a estação já enviou dados/heartbeat;
    - subpastas de ``stations/``  — par de chaves gerado no hub (``add <id>``);
    - linhas ``serve <id>`` em ``.ssh/authorized_keys`` — modo chave;
    - linhas ``<id>:...`` em ``rsyncd.secrets`` — modo senha.

    Retorna ``[{id, in_dashboard, sources}]`` ordenado por id. É read-only e sob demanda.
    Tolerante à raiz apontada para ``.../incoming`` por engano: também considera a
    pasta-pai como BASE (a BASE do hub é a que CONTÉM ``incoming/``, não a própria)."""
    existing = {s.get("id") for s in cfg.get("stations", [])}
    sources: dict[str, set[str]] = {}

    def add(sid: str, src: str) -> None:
        sid = (sid or "").strip().lower()
        if ID_RE.match(sid):
            sources.setdefault(sid, set()).add(src)

    def scan_subdirs(path: Path, src: str) -> None:
        try:
            for d in sorted(path.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    add(d.name, src)
        except OSError:
            pass

    for base in hub_base_candidates(cfg):
        scan_subdirs(base / "incoming", "incoming")
        scan_subdirs(base / "stations", "stations")
        ak = base / ".ssh" / "authorized_keys"
        try:
            if ak.is_file():
                txt = ak.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"serve\s+([A-Za-z0-9_-]+)", txt):
                    add(m.group(1), "authorized_keys")
        except OSError:
            pass
        secrets = base / "rsyncd.secrets"
        try:
            if secrets.is_file():
                for line in secrets.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        add(line.split(":", 1)[0], "rsyncd.secrets")
        except OSError:
            pass

    return [
        {"id": sid, "in_dashboard": sid in existing, "sources": sorted(sources[sid])}
        for sid in sorted(sources)
    ]


def hub_base_candidates(cfg: dict) -> list[Path]:
    """Bases plausíveis do hub para a detecção: a raiz configurada e, se ela apontar para
    uma pasta ``incoming`` (erro comum), também a pasta-pai."""
    root = data_root(cfg)
    bases = [root]
    if root.name == "incoming" and root.parent not in bases:
        bases.append(root.parent)
    return bases


def normalize_hub_root(root: str) -> tuple[str, bool]:
    """Normaliza a raiz do hub: se apontar para uma pasta ``incoming``, corrige para a
    pasta-pai (a BASE do hub é a que CONTÉM ``incoming/``). Retorna (raiz, foi_ajustada)."""
    cleaned = (root or "").strip()
    if not cleaned:
        return cleaned, False
    p = Path(cleaned.rstrip("/") or "/")
    if p.name == "incoming":
        return str(p.parent), True
    return cleaned, False


def new_station_entry(station_id: str, name: str | None = None) -> dict:
    """Estação mínima para importação: sem overrides — pastas, heartbeat e log usam os
    locais padrão sob a raiz do hub, então mudar a config master reposiciona todas."""
    return {"id": station_id, "name": name or station_id, "heartbeat_file": None,
            "latitude": None, "longitude": None, "offline_window_min": None, "files": []}


def unique_file_id(station: dict, path: str) -> str:
    """Gera um id (slug) para o arquivo monitorado a partir do nome, único na estação."""
    base = re.sub(r"[^a-z0-9_-]+", "-", Path(path).stem.lower()).strip("-") or "arquivo"
    existing = {f.get("id") for f in station.get("files", [])}
    fid, n = base, 2
    while fid in existing:
        fid = f"{base}-{n}"
        n += 1
    return fid


# --- Descoberta de arquivos .dat na pasta da estação ------------------------------
def _suggested_alias(rel: Path) -> str:
    """Apelido sugerido a partir do caminho relativo — inclui a subpasta para
    distinguir arquivos de mesmo nome em pastas diferentes."""
    parent = str(rel.parent)
    return f"{parent}/{rel.stem}" if parent not in (".", "") else rel.stem


def station_incoming_dir(cfg: dict, station: dict) -> Path:
    """Pasta ``incoming/<id>/`` da estação. Ancorada no arquivo de watchdog
    (heartbeat) cadastrado: seu diretório-pai É a pasta incoming da estação — assim
    a busca respeita um caminho ABSOLUTO configurado (ex.: /srv/carbonara/incoming/<id>)
    em vez do data_root do dashboard, que em dev aponta para ./data."""
    return heartbeat_path(cfg, station).parent


def discover_dat_files(cfg: dict, station: dict) -> list[dict]:
    """Varre a pasta incoming da estação (recursivo) por arquivos ``.dat``, ignorando
    ocultos (dotfiles/dot-dirs, como faz a ingestão). Retorna, por arquivo: o caminho a
    gravar (relativo à raiz de dados quando possível, senão absoluto), a subpasta/nome,
    tamanho, mtime e se já está monitorado. Deliberadamente SOB DEMANDA — chamado só
    quando o usuário aperta o botão, nunca automaticamente."""
    base = station_incoming_dir(cfg, station)
    root = data_root(cfg)
    monitored: set[str] = set()
    for f in station.get("files", []):
        try:
            monitored.add(str(resolve_data_path(cfg, f["path"])))
        except (ValueError, KeyError):
            pass

    found: list[dict] = []
    if not base.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(".dat"):
                continue
            abspath = Path(dirpath) / name
            rel = abspath.relative_to(base)
            try:
                store_path = str(abspath.relative_to(root))
            except ValueError:
                store_path = str(abspath)
            try:
                st = abspath.stat()
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                size, mtime = None, None
            found.append({
                "path": store_path,
                "relpath": str(rel),
                "size": size,
                "mtime": datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M") if mtime else None,
                "monitored": str(abspath) in monitored,
                "suggested_alias": _suggested_alias(rel),
            })
    return found


def default_file_entry(path: str, alias: str | None = None) -> dict:
    """Entrada MÍNIMA para um ``.dat`` recém-descoberto (padrões TOA5/Campbell, o
    datalogger padrão do projeto). As variáveis ficam vazias de propósito: o usuário
    abre 'Editar' depois para mapear colunas — é lá que a validação exige ≥1 variável."""
    return {
        "path": path,
        "alias": alias or None,
        "tail_lines": 100,
        "window_hours": None,
        "separator": ",",
        "quotechar": '"',
        "decimal": ".",
        "skip_header_lines": 4,
        "header_line": 2,
        "timestamp": {"column": 0, "format": "%Y-%m-%d %H:%M:%S"},
        "variables": [],
    }


# --- Validação dos formulários ----------------------------------------------------
def validate_settings(form: dict, cfg: dict) -> tuple[dict, list[str]]:
    """Valida a configuração master: a raiz do hub (``data_root``) e ajustes globais
    opcionais (log, fuso, fallback). É o único caminho que o usuário precisa acertar —
    o resto (incoming/, heartbeat, log) tem locais padrão derivados da raiz."""
    errors: list[str] = []
    root, adjusted = normalize_hub_root(form.get("data_root") or "")
    if not root:
        errors.append("Informe a pasta raiz do hub (ex.: /srv/carbonara).")

    connections_log = (form.get("connections_log") or "").strip() or None
    timezone = (form.get("timezone") or "").strip() or None
    if timezone:
        if ZoneInfo is None:
            errors.append("Fuso horário não suportado neste Python (zoneinfo ausente).")
            timezone = None
        else:
            try:
                ZoneInfo(timezone)
            except Exception:  # noqa: BLE001 — chave inválida/ausente no banco de fusos
                errors.append(f"Fuso horário inválido: '{timezone}' (ex.: America/Santarem).")
                timezone = None

    stale = int(cfg.get("stale_fallback_seconds") or 3600)
    raw = (form.get("stale_fallback_seconds") or "").strip()
    if raw:
        try:
            stale = int(raw)
            if not (60 <= stale <= 86400):
                raise ValueError
        except ValueError:
            errors.append("Fallback de offline: use segundos entre 60 e 86400.")

    return {"data_root": root or cfg.get("data_root"), "connections_log": connections_log,
            "timezone": timezone, "stale_fallback_seconds": stale,
            "data_root_adjusted": adjusted}, errors


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


def validate_file_entry(form: dict, *, require_variables: bool = True) -> tuple[dict, list[str]]:
    """Valida o formulário de arquivo. ``require_variables=False`` (usado pelo preview)
    dispensa a exigência de ≥1 variável — o preview só precisa ler e dividir o arquivo
    em colunas, é justamente a etapa ANTES de mapear as variáveis."""
    errors: list[str] = []
    path = (form.get("path") or "").strip()
    if not path:
        errors.append("Informe o caminho do arquivo monitorado.")
    # Apelido opcional (nome de exibição) — desambigua arquivos de mesmo nome em
    # subpastas diferentes. Sempre gravamos a chave (None quando vazio) para que
    # limpar o campo na edição realmente remova o apelido (o merge {**f, **entry}).
    alias = (form.get("alias") or "").strip() or None

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
    if require_variables and not variables:
        errors.append("Defina pelo menos uma variável (coluna + rótulo).")

    entry = {
        "path": path,
        "alias": alias,
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
