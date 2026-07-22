"""Monta as séries dos gráficos (e o preview do formulário) a partir do tail do arquivo."""
from __future__ import annotations

from datetime import datetime

from . import config_store, tailer

MAX_POINTS = 500  # decimação: acima disso o gráfico fica pesado sem ganho visual


def _column_index(spec, header: list[str] | None, row_len: int) -> int | None:
    """Resolve coluna por índice (int) ou por nome (str, exige header_line)."""
    if isinstance(spec, int):
        return spec if -row_len <= spec < row_len else None
    if header is None:
        return None
    cleaned = [h.strip().strip('"') for h in header]
    return cleaned.index(spec) if spec in cleaned else None


def _read_header(path, fcfg) -> list[str] | None:
    hl = fcfg.get("header_line")
    if not hl:
        return None
    lines = tailer.head_lines(path, hl)
    if len(lines) < hl:
        return None
    rows = tailer.parse_rows([lines[hl - 1]], fcfg["separator"], fcfg["quotechar"])
    return rows[0] if rows else None


def _decimate(indices_len: int) -> list[int]:
    if indices_len <= MAX_POINTS:
        return list(range(indices_len))
    step = indices_len / MAX_POINTS
    return [int(i * step) for i in range(MAX_POINTS)]


def build_series(cfg: dict, station: dict, fcfg: dict) -> dict:
    path = config_store.resolve_data_path(cfg, fcfg["path"])
    if not path.is_file():
        return {"error": f"Arquivo não encontrado: {path}"}

    lines = tailer.tail_data_lines(path, fcfg["tail_lines"], fcfg.get("skip_header_lines", 0))
    rows = tailer.parse_rows(lines, fcfg["separator"], fcfg["quotechar"])
    if not rows:
        return {"error": "Nenhuma linha de dados encontrada no final do arquivo."}

    header = _read_header(path, fcfg)
    row_len = max(len(r) for r in rows)

    ts_cfg = fcfg.get("timestamp") or {}
    ts_idx = _column_index(ts_cfg.get("column", 0), header, row_len)
    ts_format = ts_cfg.get("format") or "%Y-%m-%d %H:%M:%S"

    timestamps: list[str] = []
    for row in rows:
        raw = row[ts_idx].strip().strip('"') if ts_idx is not None and ts_idx < len(row) else ""
        try:
            timestamps.append(datetime.strptime(raw, ts_format).strftime("%d/%m %H:%M"))
        except ValueError:
            timestamps.append(raw)

    variables = []
    problems: list[str] = []
    for var in fcfg.get("variables", []):
        idx = _column_index(var["column"], header, row_len)
        if idx is None:
            problems.append(f"coluna '{var['column']}' não encontrada (variável '{var['label']}')")
            continue
        values = [
            tailer.to_float(row[idx], fcfg.get("decimal", ".")) if idx < len(row) else None
            for row in rows
        ]
        variables.append({
            "label": var["label"],
            "unit": var.get("unit"),
            "min": var.get("min"),
            "max": var.get("max"),
            "values": values,
        })

    keep = _decimate(len(rows))
    return {
        "file": fcfg["id"],
        "path": str(path),
        "rows_read": len(rows),
        "decimated": len(keep) < len(rows),
        "timestamps": [timestamps[i] for i in keep],
        "variables": [
            {**v, "values": [v["values"][i] for i in keep]} for v in variables
        ],
        "problems": problems,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


def build_preview(cfg: dict, entry: dict, max_rows: int = 15) -> dict:
    """Preview do formulário: últimas linhas parseadas + diagnóstico, antes de salvar."""
    try:
        path = config_store.resolve_data_path(cfg, entry["path"])
    except ValueError as exc:
        return {"error": str(exc)}
    if not path.is_file():
        return {"error": f"Arquivo não encontrado: {path}"}

    lines = tailer.tail_data_lines(path, max_rows, entry.get("skip_header_lines", 0))
    rows = tailer.parse_rows(lines, entry["separator"], entry["quotechar"])
    header = _read_header(path, entry)

    diagnostics: list[str] = []
    if rows:
        ncols = [len(r) for r in rows]
        if min(ncols) == 1 and max(ncols) == 1:
            diagnostics.append(
                "Cada linha virou UMA coluna só — o separador provavelmente está errado."
            )
        if len(set(ncols)) > 1:
            diagnostics.append(
                f"Linhas com número variável de colunas ({min(ncols)}–{max(ncols)}) — "
                "confira separador e linhas de cabeçalho."
            )
        ts_cfg = entry.get("timestamp") or {}
        ts_idx = _column_index(ts_cfg.get("column", 0), header, max(ncols))
        if ts_idx is not None:
            raw = rows[-1][ts_idx].strip().strip('"') if ts_idx < len(rows[-1]) else ""
            try:
                datetime.strptime(raw, ts_cfg.get("format") or "%Y-%m-%d %H:%M:%S")
            except ValueError:
                diagnostics.append(
                    f"Timestamp não casa com o formato: valor '{raw}' × formato "
                    f"'{ts_cfg.get('format')}'."
                )
    else:
        diagnostics.append("Nenhuma linha de dados lida — arquivo vazio ou só cabeçalho.")

    return {
        "path": str(path),
        "header": header,
        "rows": rows[-max_rows:],
        "diagnostics": diagnostics,
    }
