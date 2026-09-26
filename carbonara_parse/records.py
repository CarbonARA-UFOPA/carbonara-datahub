"""Leitura INCREMENTAL de um arquivo monitorado e conversão das variáveis registradas.

É o que a ingestão usa: em vez de reler o arquivo inteiro a cada ciclo (os .dat de
datalogger crescem por append e chegam a gigabytes), lê-se a partir do byte onde a
última leitura parou. Só linhas COMPLETAS (terminadas em ``\\n``) são consumidas — uma
linha ainda sendo escrita fica para o próximo ciclo, e o offset devolvido é sempre o
início dela.

A conversão de cada célula segue o TIPO registrado na dashboard (``tailer.COLUMN_TYPES``)
e é a mesma do preview (``series.column_display``): o que a tela mostra é o que entra
no banco.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import tailer, timeparse

_CHUNK = 1024 * 1024
HEAD_SIGNATURE_BYTES = 64 * 1024


VARIABLE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def variable_slug(label: str) -> str:
    """Identificador estável de variável a partir do rótulo: minúsculas, sem acento,
    só ``[a-z0-9_]`` (``Rad1_A(1)`` → ``rad1_a_1``; ``Temperatura do painel (°C)`` →
    ``temperatura_do_painel_c``). É a chave em ``variables``/``measurements`` — por isso
    nunca muda sozinha depois de gravada: o registro guarda ``variable_id`` explícito e
    esta função só sugere o valor inicial (ou cobre registros antigos sem o campo)."""
    txt = unicodedata.normalize("NFKD", label or "")
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch)).lower()
    txt = re.sub(r"[^a-z0-9]+", "_", txt).strip("_")
    txt = re.sub(r"_+", "_", txt)[:64].strip("_")
    if not txt:
        return ""
    if not txt[0].isalpha():
        txt = ("v_" + txt)[:64]
    return txt


def variable_id_of(var: dict) -> str:
    """variable_id de uma variável registrada: o gravado, ou o slug do rótulo/coluna
    para registros anteriores ao campo."""
    vid = (var.get("variable_id") or "").strip()
    if vid:
        return vid
    return variable_slug(str(var.get("label") or var.get("column") or ""))


def compile_strip(fcfg: dict):
    """Compila a regex de limpeza dos valores (fcfg['strip_regex']); None se ausente/inválida."""
    pat = (fcfg.get("strip_regex") or "").strip()
    if not pat:
        return None
    try:
        return re.compile(pat)
    except re.error:
        return None


def column_index(spec, header: list[str] | None, row_len: int) -> int | None:
    """Resolve coluna por índice (int, negativo conta do fim) ou por nome (exige header)."""
    if isinstance(spec, int):
        return spec if -row_len <= spec < row_len else None
    if header is None:
        return None
    cleaned = [h.strip().strip('"') for h in header]
    return cleaned.index(spec) if spec in cleaned else None


def read_header(path: Path, fcfg: dict) -> list[str] | None:
    """Linha de nomes das colunas (``header_line``, 1-based), dividida como os dados."""
    hl = fcfg.get("header_line")
    if not hl:
        return None
    lines = tailer.head_lines(path, hl)
    if len(lines) < hl:
        return None
    rows = tailer.parse_rows([lines[hl - 1]], fcfg.get("separator", ","), fcfg.get("quotechar", '"'))
    return rows[0] if rows else None


def head_signature(path: Path, nbytes: int = HEAD_SIGNATURE_BYTES) -> str:
    """SHA-256 dos primeiros ``nbytes`` — identifica o arquivo além do nome: um arquivo
    rotacionado/recriado com o mesmo nome tem outro início, e a ingestão recomeça do zero
    em vez de ler um offset que agora aponta para o meio de outra coisa."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(nbytes))
    return h.hexdigest()


@dataclass
class ColumnSpec:
    """Uma variável registrada, com a coluna já resolvida para índice."""
    variable_id: str
    label: str
    column: int | str
    index: int | None
    vtype: str
    unit: str | None
    vmin: float | None
    vmax: float | None


def resolve_columns(fcfg: dict, header: list[str] | None, row_len: int) -> list[ColumnSpec]:
    """Variáveis do registro com o índice resolvido (None = coluna não encontrada)."""
    out = []
    for var in fcfg.get("variables") or []:
        vid = variable_id_of(var)
        if not vid:
            continue
        out.append(ColumnSpec(
            variable_id=vid,
            label=var.get("label") or vid,
            column=var.get("column"),
            index=column_index(var.get("column"), header, row_len),
            vtype=var.get("type") or "numero",
            unit=var.get("unit"),
            vmin=var.get("min"),
            vmax=var.get("max"),
        ))
    return out


def convert_cell(cell: str, vtype: str, decimal: str, strip_re, ts_format: str | None,
                 tzname: str | None) -> float | None:
    """Célula → valor NUMÉRICO conforme o tipo (o banco só guarda números).

    - numero/inteiro/booleano: como no gráfico (``tailer``);
    - epoch/timestamp: o instante em segundos Unix (UTC) — a mesma convenção do valor
      plotado (``series.build_series`` usa ``d.timestamp()``);
    - texto: não é série → None (a ingestão pula variáveis de texto)."""
    if vtype == "texto":
        return None
    if vtype == "epoch":
        dt = timeparse.parse_epoch(cell, timeparse.timezone.utc)
        return dt.timestamp() if dt else None
    if vtype == "timestamp":
        dt = timeparse.to_utc(timeparse.parse_datetime_text(cell, ts_format), tzname)
        return dt.timestamp() if dt else None
    if vtype == "booleano":
        return tailer.to_bool(cell)
    if vtype == "inteiro":
        return tailer.to_int(cell, decimal, strip_re)
    return tailer.to_float(cell, decimal, strip_re)


def iter_lines(path: Path, start_offset: int = 0, max_bytes: int | None = None
               ) -> Iterator[tuple[int, str]]:
    """Linhas COMPLETAS a partir de ``start_offset``: gera ``(offset_após_a_linha, texto)``.

    Para em ``max_bytes`` lidos (o chamador continua no próximo ciclo a partir do último
    offset devolvido) ou no fim do arquivo. Um resto sem ``\\n`` não é entregue — é a
    linha que o datalogger ainda está escrevendo."""
    lidos = 0
    with open(path, "rb") as fh:
        fh.seek(start_offset)
        pos = start_offset
        resto = b""
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            lidos += len(chunk)
            buf = resto + chunk
            partes = buf.split(b"\n")
            resto = partes.pop()           # última parte: sem \n (pode ser vazia)
            for parte in partes:
                pos += len(parte) + 1
                yield pos, parte.rstrip(b"\r").decode("utf-8", errors="replace")
            if max_bytes is not None and lidos >= max_bytes:
                break


@dataclass
class Record:
    offset: int                  # byte logo após a linha (novo ponto de partida)
    time: datetime | None        # carimbo em UTC (None = não reconhecido)
    cells: list[str]


def iter_records(path: Path, fcfg: dict, start_offset: int, tzname: str | None,
                 header: list[str] | None, max_bytes: int | None = None) -> Iterator[Record]:
    """Registros parseados a partir de ``start_offset``, com o carimbo já em UTC.

    Com ``start_offset == 0`` pula ``skip_header_lines`` linhas (cabeçalho); linhas em
    branco são ignoradas (mas consumidas). Um carimbo que não casa o formato vem como
    ``time=None`` — quem chama decide (a ingestão conta e pula)."""
    skip = int(fcfg.get("skip_header_lines") or 0) if start_offset == 0 else 0
    sep = fcfg.get("separator", ",")
    quote = fcfg.get("quotechar", '"')
    ts_cfg = fcfg.get("timestamp") or {}
    ts_spec = ts_cfg.get("column", 0)
    ts_format = ts_cfg.get("format") or "%Y-%m-%d %H:%M:%S"
    ts_idx_cache: dict[int, int | None] = {}

    for off, text in iter_lines(path, start_offset, max_bytes):
        if skip > 0:
            skip -= 1
            continue
        if text.strip() == "":
            continue
        rows = tailer.parse_rows([text], sep, quote)
        cells = rows[0] if rows else []
        n = len(cells)
        if n not in ts_idx_cache:
            ts_idx_cache[n] = column_index(ts_spec, header, n)
        idx = ts_idx_cache[n]
        raw = cells[idx].strip().strip('"') if idx is not None and -n <= idx < n else ""
        yield Record(off, timeparse.parse_file_timestamp_utc(raw, ts_format, tzname), cells)
