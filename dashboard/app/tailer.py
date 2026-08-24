"""Leitura eficiente do FINAL de arquivos e parse tabular.

Os .dat de datalogger crescem por append e podem ficar grandes — nunca carregamos o
arquivo inteiro: `tail_lines` faz seek do fim em blocos, só o suficiente para as N
linhas pedidas. O parse usa csv.reader (resolve aspas do formato TOA5 de graça).
"""
from __future__ import annotations

import csv
from pathlib import Path

_BLOCK = 8192

# Valores que o datalogger usa para "sem leitura" (Campbell): viram None no gráfico.
_NON_VALUES = {"", "NAN", "-NAN", "NA", "INF", "-INF", "NULL"}
_CAMPBELL_MISSING = {-7999.0, 7999.0}


def tail_lines(path: Path, n: int) -> tuple[list[str], bool]:
    """Últimas ``n`` linhas do arquivo. Retorna (linhas, contém_início_do_arquivo)."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    text = data.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    includes_start = pos == 0
    if not includes_start and lines:
        lines = lines[1:]  # a primeira pode ter sido cortada no meio pelo seek
    return lines[-n:], includes_start and len(lines) <= n


def tail_data_lines(path: Path, n: int, skip_header_lines: int) -> list[str]:
    """Últimas ``n`` linhas de DADOS (descontando o cabeçalho, se o tail o alcançou)."""
    lines, includes_start = tail_lines(path, n + skip_header_lines)
    if includes_start and skip_header_lines:
        lines = lines[skip_header_lines:]
    return lines[-n:]


def head_lines(path: Path, n: int) -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= n:
                break
            out.append(line.rstrip("\n"))
    return out


def parse_rows(lines: list[str], separator: str, quotechar: str) -> list[list[str]]:
    # quotechar vazio = SEM aspas (csv.QUOTE_NONE): divide puramente pelo separador, então
    # um campo entre aspas com vírgulas dentro (ex.: RadOut1 do Campbell,
    # "m -125,1,-1247,...") vira VÁRIAS colunas. As aspas ficam literais e são removidas
    # depois (to_float/strip). Com aspas, o comportamento normal (campo entre aspas = 1 col).
    # Separador ESPAÇO: divide por sequências de espaço/tab (str.split()), não por um
    # espaço isolado. Num formato delimitado por espaço não existe campo vazio — dois
    # espaços seguidos são alinhamento, não coluna nula — e o csv.reader criaria uma
    # coluna fantasma que desloca TODAS as seguintes (com ~100 colunas mapeadas por
    # índice, o estrago é silencioso). Com um espaço só, o resultado é idêntico.
    if separator == " ":
        return [ln.split() for ln in lines]
    if quotechar:
        reader = csv.reader(lines, delimiter=separator, quotechar=quotechar)
    else:
        reader = csv.reader(lines, delimiter=separator, quoting=csv.QUOTE_NONE)
    return [row for row in reader]


def to_float(cell: str, decimal: str = ".", strip_re=None) -> float | None:
    cell = (cell or "").strip().strip('"')
    # Limpeza opcional por regex: remove os trechos casados antes de virar número
    # (ex.: tirar o 'm' de 'm -125', ou unidades). strip_re é um Pattern já compilado.
    if strip_re is not None:
        cell = strip_re.sub("", cell).strip()
    if cell.upper() in _NON_VALUES:
        return None
    if decimal != ".":
        cell = cell.replace(decimal, ".")
    try:
        value = float(cell)
    except ValueError:
        return None
    return None if value in _CAMPBELL_MISSING else value
