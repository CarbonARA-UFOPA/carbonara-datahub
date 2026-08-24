"""Monta as séries dos gráficos (e o preview do formulário) a partir do tail do arquivo."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config_store, tailer

MAX_POINTS = 500  # decimação: acima disso o gráfico fica pesado sem ganho visual


def _compile_strip(fcfg: dict):
    """Compila a regex de limpeza dos valores (fcfg['strip_regex']); None se ausente/ inválida."""
    pat = (fcfg.get("strip_regex") or "").strip()
    if not pat:
        return None
    try:
        return re.compile(pat)
    except re.error:
        return None


def _column_index(spec, header: list[str] | None, row_len: int) -> int | None:
    """Resolve coluna por índice (int) ou por nome (str, exige header_line)."""
    if isinstance(spec, int):
        return spec if -row_len <= spec < row_len else None
    if header is None:
        return None
    cleaned = [h.strip().strip('"') for h in header]
    return cleaned.index(spec) if spec in cleaned else None


def parse_epoch(raw: str) -> datetime | None:
    """Unix epoch → datetime (fuso do processo, como os carimbos de texto dos arquivos).

    Aceita fração de segundo — ``1787542188.433029`` são 6 casas, precisão de
    microssegundo, preservada no datetime. A ESCALA é deduzida da ordem de grandeza,
    porque a mesma coluna aparece em segundos, ms, µs ou ns conforme o equipamento:
    hoje o epoch em segundos tem ~1,8e9, em ms ~1,8e12, em µs ~1,8e15 e em ns ~1,8e18 —
    as faixas não se sobrepõem em nenhum momento plausível deste século."""
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
    try:
        return datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return None


def parse_timestamp(raw: str, fmt: str | None) -> datetime | None:
    """Parse TOLERANTE do timestamp. Dataloggers Campbell/TOA5 gravam fração de segundo
    ('2026-07-23 17:00:03.607'), que o formato padrão '%Y-%m-%d %H:%M:%S' não casa. Aqui
    tentamos o formato como está e, se falhar, uma variante: com '.%f' anexado quando o
    formato não a declara, ou sem ela quando o valor não tem fração. Assim o padrão da
    dashboard funciona com ou sem os milissegundos, sem o usuário ter que ajustar nada."""
    # Unix epoch vale para QUALQUER coluna escolhida como eixo de tempo — o marcador
    # está no FORMATO, não na posição da coluna.
    if config_store.is_epoch_format(fmt):
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


def _read_header(path, fcfg) -> list[str] | None:
    hl = fcfg.get("header_line")
    if not hl:
        return None
    lines = tailer.head_lines(path, hl)
    if len(lines) < hl:
        return None
    rows = tailer.parse_rows([lines[hl - 1]], fcfg.get("separator", ","), fcfg.get("quotechar", '"'))
    return rows[0] if rows else None


def _decimate(indices_len: int) -> list[int]:
    if indices_len <= MAX_POINTS:
        return list(range(indices_len))
    step = indices_len / MAX_POINTS
    return [int(i * step) for i in range(MAX_POINTS)]


def build_series(cfg: dict, station: dict, fcfg: dict) -> dict:
    # Caminho com data/hora (dynamic_path) é expandido aqui, a cada leitura, pelo
    # relógio do servidor — por isso o gráfico segue o arquivo que rotaciona sozinho.
    try:
        path, pinfo = config_store.resolve_file_path(cfg, fcfg)
    except ValueError as exc:
        return {"error": str(exc)}
    if not path.is_file():
        if pinfo.get("dynamic"):
            # 'pending': o arquivo do padrão ainda não chegou (estação sem sincronizar
            # hoje, primeira execução). Não é falha de configuração — a UI mostra em
            # tom neutro e esconde os gráficos, em vez de um erro vermelho.
            return {"pending": True,
                    "error": "Aguardando o arquivo do período: "
                             f"{Path(pinfo['expanded']).name} ainda não chegou "
                             "(nenhum arquivo casa com o padrão)."}
        return {"error": f"Arquivo não encontrado: {path}"}

    # .get com defaults: entradas escritas à mão no YAML podem omitir chaves
    tail_lines = fcfg.get("tail_lines", 100)
    lines = tailer.tail_data_lines(path, tail_lines, fcfg.get("skip_header_lines", 0))
    rows = tailer.parse_rows(lines, fcfg.get("separator", ","), fcfg.get("quotechar", '"'))
    if not rows:
        return {"error": "Nenhuma linha de dados encontrada no final do arquivo."}

    header = _read_header(path, fcfg)
    row_len = max(len(r) for r in rows)

    ts_cfg = fcfg.get("timestamp") or {}
    ts_idx = _column_index(ts_cfg.get("column", 0), header, row_len)
    ts_format = ts_cfg.get("format") or "%Y-%m-%d %H:%M:%S"

    timestamps: list[str] = []
    dts: list[datetime | None] = []
    for row in rows:
        raw = row[ts_idx].strip().strip('"') if ts_idx is not None and ts_idx < len(row) else ""
        dt = parse_timestamp(raw, ts_format)
        if dt is not None:
            dts.append(dt)
            timestamps.append(dt.strftime("%d/%m %H:%M"))
        else:
            dts.append(None)
            timestamps.append(raw)

    problems: list[str] = []

    # Período em TEMPO (opcional): recorta as linhas lidas para as últimas N horas,
    # contadas a partir do timestamp mais recente. A leitura continua limitada por
    # tail_lines (nunca lemos o arquivo inteiro) — se o tail não cobrir o período,
    # avisamos em vez de ler mais.
    window_hours = fcfg.get("window_hours")
    if window_hours:
        newest = next((d for d in reversed(dts) if d is not None), None)
        if newest is not None:
            cutoff = newest - timedelta(hours=window_hours)
            start = next(
                (i for i, d in enumerate(dts) if d is not None and d >= cutoff), 0
            )
            if start == 0 and len(rows) >= tail_lines:
                problems.append(
                    f"as {tail_lines} linhas lidas podem não cobrir o período de "
                    f"{window_hours} h — aumente 'Linhas do final a plotar'"
                )
            rows = rows[start:]
            timestamps = timestamps[start:]
            dts = dts[start:]  # mantém dts alinhado com rows (epochs abaixo indexam por linha)

    strip_re = _compile_strip(fcfg)
    variables = []
    for var in fcfg.get("variables", []):
        idx = _column_index(var["column"], header, row_len)
        if idx is None:
            problems.append(f"coluna '{var['column']}' não encontrada (variável '{var['label']}')")
            continue
        values = [
            tailer.to_float(row[idx], fcfg.get("decimal", "."), strip_re) if idx < len(row) else None
            for row in rows
        ]
        variables.append({
            "label": var["label"],
            "unit": var.get("unit"),
            "min": var.get("min"),
            "max": var.get("max"),
            "values": values,
        })

    # Última leitura em formato COMPLETO (com ano) para o tooltip do mapa — o eixo do
    # gráfico usa '%d/%m %H:%M' (sem ano, para não poluir), mas o "última leitura" precisa
    # do ano. Pega o timestamp mais novo que casou o formato.
    last_dt = next((d for d in reversed(dts) if d is not None), None)
    keep = _decimate(len(rows))
    if pinfo.get("fallback"):
        problems.append(
            f"o arquivo do momento ({Path(pinfo['expanded']).name}) ainda não existe — "
            f"lendo o mais recente que casa com o padrão ({path.name})")
    return {
        "file": fcfg["id"],
        "path": str(path),
        # Contexto do caminho com data/hora: a UI mostra QUAL arquivo foi realmente lido.
        "path_info": pinfo,
        "rows_read": len(rows),
        "decimated": len(keep) < len(rows),
        "last_reading": last_dt.strftime("%d/%m/%Y %H:%M") if last_dt else None,
        "timestamps": [timestamps[i] for i in keep],
        # Instante real de cada ponto (epoch, s) — permite ao cliente juntar séries de
        # ARQUIVOS DIFERENTES num eixo de tempo comum (tela Comparar). None = linha
        # cujo timestamp não casou o formato.
        "epochs": [int(dts[i].timestamp()) if dts[i] is not None else None for i in keep],
        "variables": [
            {**v, "values": [v["values"][i] for i in keep]} for v in variables
        ],
        "problems": problems,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


def build_preview(cfg: dict, entry: dict, max_rows: int = 15) -> dict:
    """Preview do formulário: últimas linhas parseadas + diagnóstico, antes de salvar."""
    try:
        path, pinfo = config_store.resolve_file_path(cfg, entry)
    except ValueError as exc:
        return {"error": str(exc)}
    if not path.is_file():
        if pinfo.get("dynamic"):
            return {"error": "Arquivo do momento ainda não existe e nenhum arquivo "
                             f"casa com o padrão: {pinfo['expanded']}"}
        return {"error": f"Arquivo não encontrado: {path}"}

    lines = tailer.tail_data_lines(path, max_rows, entry.get("skip_header_lines", 0))
    rows = tailer.parse_rows(lines, entry["separator"], entry["quotechar"])
    header = _read_header(path, entry)
    strip_re = _compile_strip(entry)

    diagnostics: list[str] = []
    ts_idx = None
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
            if parse_timestamp(raw, ts_cfg.get("format")) is None:
                diagnostics.append(
                    f"Timestamp não casa com o formato: valor '{raw}' × formato "
                    f"'{ts_cfg.get('format')}'."
                )
    else:
        diagnostics.append("Nenhuma linha de dados lida — arquivo vazio ou só cabeçalho.")

    # Mostra no preview o MESMO que os gráficos vão usar: aplica a limpeza por regex em
    # cada célula (menos a do timestamp, que tem parse próprio). Assim você vê o 'm' etc.
    # já removido antes de mapear as variáveis.
    out_rows = rows[-max_rows:]
    if strip_re is not None:
        out_rows = [
            [c if i == ts_idx else strip_re.sub("", (c or "")).strip() for i, c in enumerate(r)]
            for r in out_rows
        ]

    if pinfo.get("dynamic"):
        # No preview o usuário PRECISA ver o padrão virando arquivo — é como ele
        # confere se os campos %Y/%m/%d/%H estão certos antes de salvar.
        note = f"Caminho com data/hora: {pinfo['pattern']} → {pinfo['expanded']}"
        if pinfo.get("fallback"):
            note += (" (ainda não existe — lendo o mais recente que casa com o padrão: "
                     f"{pinfo['fallback']})")
        diagnostics.insert(0, note)

    return {
        "path": str(path),
        "path_info": pinfo,
        "header": header,
        "rows": out_rows,
        "diagnostics": diagnostics,
    }
