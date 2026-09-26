"""Caminho dos arquivos monitorados: raiz de dados e caminho com data/hora (strftime).

Dataloggers costumam rotacionar o arquivo pelo relógio (dados_20260821.dat,
2026/08/21/torre.csv). Marcando "caminho com data/hora" no registro, o caminho vira um
PADRÃO strftime resolvido com o horário DO SERVIDOR a cada leitura — assim um registro
só acompanha o arquivo do dia/hora corrente, sem ninguém editar o caminho. Curingas
escritos no padrão são parte do contrato (``%Y-%m-%d__*.sfo``: o equipamento nomeia o
arquivo pelo instante em que o criou, só a data é previsível).

``cfg`` é o dicionário do config.yaml da dashboard (``data_root`` e, opcionalmente,
``_base`` = pasta do próprio config, contra a qual um data_root relativo é resolvido).
"""
from __future__ import annotations

import glob
import re
from datetime import datetime, timedelta
from pathlib import Path


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


# Quanto cada campo strftime ocupa, para montar o glob do resgate. O que não estiver
# aqui (%b, %A, %p…) vira '*' — casa igual, só menos preciso.
_TIME_TOKEN_WIDTH = {"Y": 4, "y": 2, "m": 2, "d": 2, "H": 2, "I": 2, "M": 2, "S": 2,
                     "j": 3, "U": 2, "W": 2, "u": 1, "w": 1}
_TIME_TOKEN_RE = re.compile(r"%[A-Za-z]")

# Apelidos "humanos" para os campos strftime: '%YYYY' no strftime vira '2026YYY' em
# silêncio — arquivo que nunca existe. Normalizamos na entrada.
_TIME_ALIASES = (("%YYYY", "%Y"), ("%yyyy", "%Y"), ("%YY", "%y"), ("%mm", "%m"),
                 ("%DD", "%d"), ("%dd", "%d"), ("%HH", "%H"), ("%MM", "%M"), ("%SS", "%S"))


def normalize_time_tokens(path: str) -> str:
    """Converte os apelidos (%YYYY, %mm, %DD…) para strftime (%Y, %m, %d…)."""
    out = path or ""
    for alias, canon in _TIME_ALIASES:      # os mais longos primeiro (ver tupla)
        out = out.replace(alias, canon)
    return out


def has_time_tokens(path: str) -> bool:
    """True se o caminho tem algum campo strftime (%Y, %m, %d, %H…)."""
    return bool(_TIME_TOKEN_RE.search(path or ""))


GLOB_CHARS = "*?["


def has_glob(path: str) -> bool:
    """O caminho traz curinga escrito pelo usuário?"""
    return any(c in (path or "") for c in GLOB_CHARS)


def time_path_glob(pattern: str, keep_wildcards: bool = False) -> str:
    """Converte o padrão strftime num glob: cada campo vira '?' na largura certa
    (``%Y`` → ``????``), o resto é escapado. Com ``keep_wildcards``, os curingas
    ESCRITOS no padrão são preservados."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "%" and i + 1 < len(pattern):
            tok = pattern[i + 1]
            if tok == "%":
                out.append("%")
            else:
                w = _TIME_TOKEN_WIDTH.get(tok)
                out.append("?" * w if w else "*")
            i += 2
        else:
            manter = keep_wildcards and ch in GLOB_CHARS
            out.append(ch if manter else (glob.escape(ch) if ch in GLOB_CHARS else ch))
            i += 1
    return "".join(out)


def truncate_pattern(path: str, n: int) -> str:
    """Corta os ``n`` últimos caracteres do NOME do arquivo (nunca do diretório) e
    completa com ``*``; um ``%`` partido pelo corte é removido junto."""
    dirpart, sep, nome = (path or "").rpartition("/")
    nome = nome[:-n] if 0 < n < len(nome) else ""
    if (len(nome) - len(nome.rstrip("%"))) % 2 == 1:
        nome = nome[:-1]
    if not nome.endswith("*"):
        nome += "*"
    return f"{dirpart}{sep}{nome}"


def glob_files(padrao: str) -> list[Path]:
    """Arquivos (só arquivos) que casam com um glob já pronto."""
    try:
        hits = [Path(p) for p in glob.glob(padrao)]
    except OSError:
        return []
    return [p for p in hits if p.is_file()]


def newest_glob(padrao: str) -> Path | None:
    """Arquivo mais recente (mtime) que casa com um glob já pronto."""
    files = glob_files(padrao)
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def newest_matching(pattern: str, keep_wildcards: bool = False) -> Path | None:
    """Arquivo mais recente (mtime) que casa com o padrão de tempo. None se nenhum."""
    return newest_glob(time_path_glob(pattern, keep_wildcards))


def resolve_file_path(cfg: dict, fcfg: dict, now: datetime | None = None) -> tuple[Path, dict]:
    """Caminho REAL a ler de um arquivo monitorado, e um dicionário de contexto.

    Sem ``dynamic_path`` é o de sempre (``resolve_data_path``). Com ele, o padrão é
    expandido pelo relógio do servidor (``now`` permite reproduzir uma resolução
    passada); se o arquivo do momento ainda não existir, cai no mais recente que casa
    com o padrão. O contexto (``pattern``/``expanded``/``fallback``/``matched``) é o que
    a UI mostra para o usuário saber exatamente qual arquivo foi lido."""
    raw = (fcfg.get("path") or "").strip()
    trunc = fcfg.get("truncate_chars")
    if fcfg.get("dynamic_path") and trunc:
        raw = truncate_pattern(raw, int(trunc))
    if not fcfg.get("dynamic_path") or not (has_time_tokens(raw) or has_glob(raw)):
        return resolve_data_path(cfg, raw), {}
    pattern = resolve_data_path(cfg, raw)
    agora = now or datetime.now()
    offset = fcfg.get("hour_offset")
    if fcfg.get("dynamic_path") and offset:
        agora += timedelta(hours=int(offset))
    expanded = Path(agora.strftime(str(pattern)))
    if not Path(raw).expanduser().is_absolute():
        root = data_root(cfg)
        if root not in expanded.resolve().parents and expanded.resolve() != root:
            raise ValueError(f"caminho com data/hora escapa de data_root: {expanded}")
    info = {"dynamic": True, "pattern": str(pattern), "expanded": str(expanded)}
    if trunc and fcfg.get("dynamic_path"):
        info["truncate_chars"] = int(trunc)
    if offset and fcfg.get("dynamic_path"):
        info["hour_offset"] = int(offset)
    curinga = has_glob(str(expanded))
    if curinga:
        candidatos = glob_files(str(expanded))
        if candidatos:
            hoje = max(candidatos, key=lambda p: p.stat().st_mtime)
            info["matched"] = str(hoje)
            if len(candidatos) > 1:
                info["matches"] = len(candidatos)
            return hoje, info
    elif expanded.is_file():
        return expanded, info
    newest = newest_matching(str(pattern), keep_wildcards=curinga)
    if newest is not None:
        info["fallback"] = str(newest)
        return newest, info
    return expanded, info


def all_matching_files(cfg: dict, fcfg: dict) -> list[Path]:
    """TODOS os arquivos que já casaram com um registro de caminho dinâmico (todos os
    dias/horas), do mais antigo ao mais novo por mtime — usado pela ingestão para o
    backfill do histórico e para fechar o arquivo do período anterior após a virada.
    Registro não dinâmico → só o próprio arquivo (se existir)."""
    raw = (fcfg.get("path") or "").strip()
    trunc = fcfg.get("truncate_chars")
    if fcfg.get("dynamic_path") and trunc:
        raw = truncate_pattern(raw, int(trunc))
    if not fcfg.get("dynamic_path") or not (has_time_tokens(raw) or has_glob(raw)):
        p = resolve_data_path(cfg, raw)
        return [p] if p.is_file() else []
    pattern = str(resolve_data_path(cfg, raw))
    files = glob_files(time_path_glob(pattern, keep_wildcards=has_glob(pattern)))
    return sorted(files, key=lambda p: p.stat().st_mtime)
