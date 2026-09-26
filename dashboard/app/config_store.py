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

from carbonara_parse.records import VARIABLE_ID_RE, variable_slug
from .tailer import COLUMN_TYPES

# Reamostragem antes de gravar no banco: 'raw' grava cada registro; '1min'/'30min'
# gravam a média (por variável) de cada intervalo — para arquivos de alta frequência
# (10–20 Hz), que em linhas cruas explodiriam o banco sem utilidade.
RESAMPLE_OPTIONS = ("raw", "1min", "30min")

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
    # FTP de distribuição (página "FTP"): raiz servida (None = local_root do
    # vsftpd.conf), caminho do vsftpd.conf, executável ftp-hub, host a exibir e as
    # PUBLICAÇÕES (regras hub → FTP executadas pelo publicador da dashboard).
    "ftp": {"root": None, "vsftpd_conf": None, "ftp_hub_bin": None,
            "public_host": None, "publications": []},
    # API de dados (serviço `api` do compose): url interna que a dashboard chama, chave
    # de serviço (escopos read+admin, criada com `keys_cli`) e a url pública a mostrar.
    "api": {"url": None, "key": None, "public_url": None},
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
        if isinstance(raw.get("ftp"), dict):
            cfg["ftp"].update({k: v for k, v in raw["ftp"].items() if v is not None})
            cfg["ftp"]["publications"] = list(raw["ftp"].get("publications") or [])
        if isinstance(raw.get("api"), dict):
            cfg["api"].update({k: v for k, v in raw["api"].items() if v is not None})
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


# --- Caminhos e carimbos: implementação no pacote compartilhado ------------------
# data_root / resolve_data_path / caminho com data/hora (strftime + curinga) e o
# marcador de epoch vivem em carbonara_parse (a ingestão resolve os MESMOS caminhos).
# Reexportados aqui para os demais módulos da dashboard.
from carbonara_parse.paths import (  # noqa: E402,F401
    GLOB_CHARS, data_root, has_glob, has_time_tokens, normalize_time_tokens,
    resolve_data_path, resolve_file_path, time_path_glob, truncate_pattern,
)
from carbonara_parse.paths import glob_files as _glob_files  # noqa: E402,F401
from carbonara_parse.paths import newest_glob as _newest_glob  # noqa: E402,F401
from carbonara_parse.paths import newest_matching as _newest_matching  # noqa: E402,F401
from carbonara_parse.timeparse import EPOCH_MARKERS, is_epoch_format, valid_timezone  # noqa: E402,F401


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
            "latitude": None, "longitude": None, "files": []}


def unique_file_id(station: dict, path: str) -> str:
    """Gera um id (slug) para o arquivo monitorado a partir do nome, único na estação."""
    base = re.sub(r"[^a-z0-9_-]+", "-", Path(path).stem.lower()).strip("-") or "arquivo"
    existing = {f.get("id") for f in station.get("files", [])}
    fid, n = base, 2
    while fid in existing:
        fid = f"{base}-{n}"
        n += 1
    return fid


# --- Descoberta dos arquivos da pasta da estação ----------------------------------
MAX_DISCOVER_ENTRIES = 2000  # teto de arquivos por varredura (pastas grandes); a UI avisa


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


def station_safe_resolve(cfg: dict, station: dict, subpath: str) -> Path | None:
    """Resolve ``subpath`` sob a pasta da estação com trava anti-traversal — mesma
    regra do navegador da aba Coleta (``hub.safe_resolve``): o caminho RESOLVIDO
    (symlinks inclusos) precisa continuar sob a pasta da estação, senão None."""
    base = station_incoming_dir(cfg, station).resolve()
    try:
        p = (base / (subpath or "")).resolve()
    except OSError:
        return None
    if p != base and base not in p.parents:
        return None
    return p


def browse_station_dir(cfg: dict, station: dict, subpath: str = "") -> dict:
    """Listagem de UMA pasta de ``incoming/<id>/`` — subpastas e arquivos com tamanho
    e data, mais o trilho de navegação. É o mesmo comportamento do navegador da aba
    Coleta, só que cada arquivo também informa se JÁ está monitorado e o apelido
    sugerido, para a tela de edição oferecer a marcação.

    Como a listagem é de um nível só (sem recursão), roda no carregamento da página
    sem custo — pastas gigantes de datalogger não são varridas inteiras.

    Ocultos APARECEM marcados (heartbeat, ``.rsync-parcial/``), como no navegador da
    Coleta, mas nunca são selecionáveis: a ingestão os ignora, monitorá-los não faria
    sentido. Devolve ``{base, rel, crumbs, dirs, files, truncated, max_entries,
    missing}``; ``truncated`` avisa que a pasta tem mais que ``MAX_DISCOVER_ENTRIES``
    entradas (nada de teto silencioso)."""
    base = station_incoming_dir(cfg, station)
    root = data_root(cfg)
    path = station_safe_resolve(cfg, station, subpath)
    rel_now = "" if path is None else (
        "" if path == base.resolve() else str(path.relative_to(base.resolve())))
    out = {"base": str(base), "rel": rel_now, "crumbs": [], "dirs": [], "files": [],
           "truncated": False, "max_entries": MAX_DISCOVER_ENTRIES, "missing": False}
    if path is None or not path.is_dir():
        out["missing"] = True
        return out

    # Guarda as DUAS formas do caminho monitorado (literal e resolvida): a listagem
    # anda sobre a base já resolvida, e em máquinas com symlink no caminho
    # (/var → /private/var no macOS, /srv apontado para outro volume) as duas grafias
    # existem — sem isso um arquivo já monitorado apareceria como disponível.
    monitored: set[str] = set()
    for f in station.get("files", []):
        try:
            mp = resolve_data_path(cfg, f["path"])
        except (ValueError, KeyError):
            continue
        monitored.add(str(mp))
        monitored.add(str(mp.resolve()))

    base_r = base.resolve()
    try:
        with os.scandir(path) as it:
            for i, entry in enumerate(it):
                if i >= MAX_DISCOVER_ENTRIES:
                    out["truncated"] = True
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                abspath = Path(entry.path)
                rel = str(abspath.relative_to(base_r))
                hidden = entry.name.startswith(".")
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M")
                item = {"name": entry.name, "rel": rel, "hidden": hidden, "mtime": mtime}
                if entry.is_dir(follow_symlinks=False):
                    out["dirs"].append(item)
                    continue
                try:  # caminho a GRAVAR no config: relativo à raiz de dados quando dá
                    store_path = str(abspath.relative_to(root))
                except ValueError:
                    store_path = str(abspath)
                out["files"].append({
                    **item,
                    "path": store_path,
                    "ext": abspath.suffix.lower(),
                    "size": st.st_size,
                    "monitored": str(abspath) in monitored,
                    "suggested_alias": _suggested_alias(Path(rel)),
                })
    except OSError:
        out["missing"] = True
        return out

    out["dirs"].sort(key=lambda d: d["name"].lower())
    out["files"].sort(key=lambda f: f["name"].lower())

    acc: list[str] = []
    for part in Path(rel_now).parts:
        acc.append(part)
        out["crumbs"].append({"name": part, "rel": "/".join(acc)})
    return out


def default_file_entry(path: str, alias: str | None = None) -> dict:
    """Entrada MÍNIMA para um arquivo recém-descoberto (padrões TOA5/Campbell, o
    datalogger padrão do projeto). As variáveis ficam vazias de propósito: o usuário
    abre 'Editar' depois para mapear colunas — é lá que a validação exige ≥1 variável."""
    return {
        "path": path,
        "alias": alias or None,
        "dynamic_path": False,
        "tail_lines": 100,
        "window_hours": None,
        "separator": ",",
        "quotechar": '"',
        "decimal": ".",
        "skip_header_lines": 4,
        "header_line": 2,
        "timestamp": {"column": 0, "format": "%Y-%m-%d %H:%M:%S"},
        "variables": [],
        # Banco de dados: só entra o que for marcado explicitamente (ingest) — e só as
        # variáveis listadas. timezone = fuso dos carimbos do ARQUIVO (IANA; vazio = UTC).
        "ingest": False,
        "timezone": None,
        "resample": "raw",
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

    # API de dados: url interna (a que a dashboard chama), chave (vazio = mantém a
    # atual; "-" = remove) e url pública opcional (só exibição).
    api_old = cfg.get("api") or {}
    api_url = (form.get("api_url") or "").strip().rstrip("/") or None
    if api_url and not (api_url.startswith("http://") or api_url.startswith("https://")):
        errors.append("URL da API inválida: comece com http:// ou https:// (ex.: http://127.0.0.1:8000).")
        api_url = api_old.get("url")
    api_key_raw = (form.get("api_key") or "").strip()
    api_key = api_old.get("key") if api_key_raw == "" else (None if api_key_raw == "-" else api_key_raw)
    api_public = (form.get("api_public_url") or "").strip().rstrip("/") or None

    return {"data_root": root or cfg.get("data_root"), "connections_log": connections_log,
            "timezone": timezone, "stale_fallback_seconds": stale,
            "data_root_adjusted": adjusted,
            "api": {"url": api_url, "key": api_key, "public_url": api_public}}, errors


def validate_station(form: dict, *, is_new: bool, cfg: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    sid = (form.get("id") or "").strip().lower()
    name = (form.get("name") or "").strip()
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

    # Watchdog e janela de offline NÃO vêm do formulário: os dois são do rsync-hub/
    # cliente — o heartbeat que a estação envia declara o intervalo e a janela é sempre
    # 2×intervalo + 2 min. Um 'heartbeat_file' legado no YAML continua sendo respeitado
    # como override manual; só não se edita mais pela dashboard.
    return {"id": sid, "name": name,
            "latitude": latitude, "longitude": longitude, "files": []}, errors


def _parse_separator(raw: str) -> str:
    raw = raw or ","
    if raw in ("\\t", "tab", "TAB"):
        return "\t"
    # Espaço é separador legítimo (.sfo e vários .dat), mas um espaço solo num campo de
    # texto é invisível — e some no strip. Por isso os apelidos escritos.
    if raw.strip().lower() in ("\\s", "space", "espaco", "espaço", "branco"):
        return " "
    if raw.strip() == "" and " " in raw:
        return " "
    return raw.strip() or ","


def validate_file_entry(form: dict, *, require_variables: bool = True,
                        station: dict | None = None, file_id: str | None = None
                        ) -> tuple[dict, list[str]]:
    """Valida o formulário de arquivo. ``require_variables=False`` (usado pelo preview)
    dispensa a exigência de ≥1 variável — o preview só precisa ler e dividir o arquivo
    em colunas, é justamente a etapa ANTES de mapear as variáveis.

    ``station``/``file_id`` (quando informados) permitem checar que os ``variable_id``
    de um arquivo INGERIDO não colidem com os de outro arquivo ingerido da mesma
    estação: a chave no banco é (time, station_id, variable_id), então dois arquivos
    gravando 'record' para a mesma estação se sobreporiam em silêncio."""
    errors: list[str] = []
    path = (form.get("path") or "").strip()
    if not path:
        errors.append("Informe o caminho do arquivo monitorado.")
    # Apelido opcional (nome de exibição) — desambigua arquivos de mesmo nome em
    # subpastas diferentes. Sempre gravamos a chave (None quando vazio) para que
    # limpar o campo na edição realmente remova o apelido (o merge {**f, **entry}).
    alias = (form.get("alias") or "").strip() or None
    # Caminho com data/hora: o path é um padrão strftime resolvido a cada leitura.
    dynamic_path = bool(form.get("dynamic_path"))
    if dynamic_path:
        path = normalize_time_tokens(path)
    # Truncamento do fim do nome: N últimos caracteres do NOME viram '*' na hora de
    # resolver (o path gravado fica intacto). Vazio/None = padrão fixo, sem truncar.
    truncate_chars = None
    trunc_raw = (form.get("truncate_chars") or "").strip()
    if trunc_raw:
        try:
            truncate_chars = int(trunc_raw)
            if truncate_chars < 1:
                raise ValueError
        except ValueError:
            errors.append(f"Truncar do fim do nome: valor inválido ('{trunc_raw}') — "
                          "use um número de caracteres ≥ 1, ou deixe vazio.")
            truncate_chars = None
    if truncate_chars and not dynamic_path:
        errors.append("O truncamento do fim do nome só vale com \"O nome do arquivo "
                      "muda com a data/hora\" marcado — é ali que o padrão é resolvido "
                      "a cada leitura.")
    # Deslocamento de horas: ±N somado ao relógio do servidor antes de expandir o
    # padrão. Vazio ou 0 = sem deslocamento (None gravado — o merge {**f, **entry}
    # remove um valor antigo ao limpar o campo).
    hour_offset = None
    off_raw = (form.get("hour_offset") or "").strip()
    if off_raw:
        try:
            hour_offset = int(off_raw)          # int() aceita "+2" e "-3"
            if not (-168 <= hour_offset <= 168):
                raise ValueError
        except ValueError:
            errors.append(f"Deslocar horas: valor inválido ('{off_raw}') — use um "
                          "inteiro entre -168 e +168 (ex.: +2 ou -3), ou deixe vazio.")
            hour_offset = None
        if hour_offset == 0:
            hour_offset = None
    if hour_offset and not dynamic_path:
        errors.append("O deslocamento de horas só vale com \"O nome do arquivo muda "
                      "com a data/hora\" marcado — ele desloca o relógio usado para "
                      "expandir o padrão.")
    if hour_offset and dynamic_path and path and not has_time_tokens(path):
        errors.append("Deslocar horas exige campos de tempo no caminho (%Y, %m, %d, "
                      "%H…) — num padrão só com curinga (*) não há relógio a deslocar.")
    if truncate_chars and path:
        nome = path.rpartition("/")[2]
        if truncate_chars >= len(nome):
            errors.append(f"Truncar {truncate_chars} caractere(s) consome o nome inteiro "
                          f"('{nome}', {len(nome)} caracteres) — sobraria só '*', que casa "
                          "qualquer arquivo da pasta. Diminua o valor (ou use um padrão "
                          "com * escrito, se é isso mesmo que você quer).")
    if dynamic_path and path and not (has_time_tokens(path) or has_glob(path)) \
            and not truncate_chars:
        errors.append("Caminho com data/hora marcado, mas o caminho não tem campo de "
                      "tempo (%Y, %m, %d, %H…) nem curinga (*). "
                      "Ex.: incoming/est/dados_%Y%m%d.dat ou incoming/est/%Y-%m-%d__*.sfo")
    if not dynamic_path and path and (has_time_tokens(path) or any(c in path for c in "*?")):
        errors.append("O caminho tem campos de tempo (%Y, %m, %d, %H…) ou curinga (*) — "
                      "marque \"O nome do arquivo muda com a data/hora\" para que sejam "
                      "resolvidos, ou remova-os.")

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
    # Unix epoch: grava sempre em minúsculas na forma canônica, para o parse reconhecer
    # independentemente de como foi digitado ("Epoch", "UNIX", "%s"…).
    if is_epoch_format(ts_format):
        ts_format = "epoch"

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

    # --- Banco de dados ---------------------------------------------------------
    ingest = bool(form.get("ingest"))
    tzname = (form.get("timezone") or "").strip() or None
    if tzname and not valid_timezone(tzname):
        errors.append(f"Fuso horário inválido: '{tzname}' (ex.: UTC, America/Santarem).")
        tzname = None
    resample = (form.get("resample") or "raw").strip().lower() or "raw"
    if resample not in RESAMPLE_OPTIONS:
        errors.append(f"Reamostragem inválida ('{resample}') — use uma de: "
                      + ", ".join(RESAMPLE_OPTIONS) + ".")
        resample = "raw"

    variables = []
    cols = _getlist("var_column")
    labels = _getlist("var_label")
    types = _getlist("var_type")
    units = _getlist("var_unit")
    mins = _getlist("var_min")
    maxs = _getlist("var_max")
    vids = _getlist("var_id")
    seen_ids: set[str] = set()
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
        vtype = (types[i] if i < len(types) else "").strip() or "numero"
        if vtype not in COLUMN_TYPES:
            errors.append(f"Tipo de coluna inválido ('{vtype}') — use um de: "
                          + ", ".join(COLUMN_TYPES) + ".")
            vtype = "numero"
        label = (labels[i] if i < len(labels) else "").strip() or str(column)
        unit = (units[i] if i < len(units) else "").strip() or None
        vmin = _float_or_none(mins[i] if i < len(mins) else "", f"Limite mínimo de '{label}'")
        vmax = _float_or_none(maxs[i] if i < len(maxs) else "", f"Limite máximo de '{label}'")
        if vmin is not None and vmax is not None and vmin > vmax:
            errors.append(f"'{label}': limite mínimo ({vmin}) maior que o máximo ({vmax}).")
        # ID no banco: o informado (normalizado) ou o slug do rótulo. É a chave da série
        # em measurements — precisa ser único dentro do arquivo.
        vid_raw = (vids[i] if i < len(vids) else "").strip()
        vid = variable_slug(vid_raw) if vid_raw else variable_slug(label)
        if not vid:
            vid = variable_slug(f"col_{column}")
        if vid_raw and not VARIABLE_ID_RE.match(vid):
            errors.append(f"ID no banco inválido para '{label}' ('{vid_raw}'): use letras "
                          "minúsculas, números e '_' (começando por letra, até 64).")
        if vid in seen_ids:
            errors.append(f"ID no banco repetido: '{vid}' ('{label}') — cada variável do "
                          "arquivo precisa de um ID único.")
        seen_ids.add(vid)
        variables.append({"column": column, "label": label, "unit": unit,
                          "min": vmin, "max": vmax, "type": vtype, "variable_id": vid})
    if require_variables and not variables:
        errors.append("Defina pelo menos uma variável (coluna + rótulo).")
    if ingest and station is not None:
        # Colisão com outro arquivo INGERIDO da mesma estação (mesma chave no banco).
        for other in station.get("files") or []:
            if other.get("id") == file_id or not other.get("ingest"):
                continue
            outros = {(v.get("variable_id") or variable_slug(v.get("label") or ""))
                      for v in other.get("variables") or []}
            colisao = sorted(seen_ids & outros)
            if colisao:
                errors.append(
                    f"ID(s) no banco já usados pelo arquivo '{other.get('id')}' desta "
                    f"estação: {', '.join(colisao)} — dois arquivos ingeridos não podem "
                    "gravar a mesma variável (mude o ID aqui ou desmarque a ingestão).")

    entry = {
        "path": path,
        "alias": alias,
        "dynamic_path": dynamic_path,
        # Sempre presentes (None quando vazios): limpar o campo na edição precisa
        # remover o ajuste de verdade (o merge {**f, **entry} sobrescreve).
        "truncate_chars": truncate_chars,
        "hour_offset": hour_offset,
        "tail_lines": tail_lines,
        "window_hours": window_hours,
        "separator": separator,
        "quotechar": quotechar,
        "decimal": decimal,
        "skip_header_lines": skip_header,
        "header_line": header_line,
        "timestamp": {"column": ts_column, "format": ts_format},
        "variables": variables,
        "ingest": ingest,
        "timezone": tzname,
        "resample": resample,
    }
    if strip_regex:
        entry["strip_regex"] = strip_regex
    return entry, errors
