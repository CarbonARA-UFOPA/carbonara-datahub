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


# Exibição de carimbo de tempo: data completa SEMPRE, e a resolução do relógio ACOMPANHA
# a cadência real dos dados. Um datalogger de 30 min não precisa de segundos (poluem o
# eixo); um arquivo com registros a cada segundo — ou frações, como os .sfo — fica
# ilegível sem eles: linhas diferentes viravam o mesmo rótulo. Por isso o formato é
# escolhido por leitura, não fixado.
TS_DISPLAY = "%d/%m/%Y %H:%M"            # cadência de 1 min ou mais
TS_DISPLAY_S = "%d/%m/%Y %H:%M:%S"       # cadência de 1 s a 1 min
TS_DISPLAY_MS = "%d/%m/%Y %H:%M:%S.%f"   # abaixo de 1 s (cortado em milissegundos)


def ts_display_format(dts: list[datetime | None]) -> str:
    """Formato de exibição adequado à cadência da série.

    Usa o MENOR intervalo entre carimbos consecutivos (não a média): se dois registros
    quaisquer estão a 1 s um do outro, os segundos importam para todos. Sem nenhum
    intervalo positivo — todos os carimbos iguais, o sintoma clássico de ter escolhido
    uma coluna de tempo que não avança a cada registro — mostra a resolução máxima,
    que é justamente o que deixa o problema visível."""
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
    """Aplica o formato; em milissegundos corta os microssegundos do strftime (%f dá 6
    casas — 3 bastam para ler, e o valor cru continua visível no preview)."""
    out = dt.strftime(fmt)
    return out[:-3] if fmt.endswith(".%f") else out


_EPOCH_MIN = datetime(1990, 1, 1)     # antes disto não há estação deste projeto
_EPOCH_MAX = datetime(2100, 1, 1)


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
        dt = datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return None
    # Faixa plausível: apontar o eixo de tempo para uma coluna que NÃO é epoch (um
    # ângulo '359', uma temperatura '26.43') converteria em silêncio para 01/01/1970 e o
    # gráfico ficaria com datas absurdas em vez de dizer que a coluna está errada.
    if not (_EPOCH_MIN <= dt <= _EPOCH_MAX):
        return None
    return dt


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


# Formatos tentados para uma coluna do tipo "timestamp" (data/hora em TEXTO). Não há
# formato por variável no cadastro — seria um campo a mais em cada linha da tabela —,
# então tentamos o do arquivo (quando não é epoch) e depois os usuais. parse_timestamp
# já cobre, para cada um, a variante com fração de segundo.
_TEXT_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                    "%Y%m%d%H%M%S", "%Y%m%d", "%H:%M:%S", "%H:%M")


def parse_datetime_text(raw: str, prefer: str | None = None) -> datetime | None:
    """Data/hora em texto, descobrindo o formato entre os usuais (o do arquivo primeiro)."""
    tentativas = []
    if prefer and not config_store.is_epoch_format(prefer):
        tentativas.append(prefer)
    tentativas.extend(f for f in _TEXT_TS_FORMATS if f != prefer)
    for f in tentativas:
        dt = parse_timestamp(raw, f)
        if dt is not None:
            return dt
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


def column_datetimes(cells: list[str], vtype: str, ts_format: str | None) -> list:
    """Carimbos de uma coluna do tipo 'epoch' ou 'timestamp' (None onde não converteu)."""
    if vtype == "epoch":
        return [parse_epoch(c) for c in cells]
    return [parse_datetime_text(c, ts_format) for c in cells]


def column_display(cells: list[str], vtype: str, ts_format: str | None,
                   decimal: str = ".", strip_re=None) -> list:
    """Como cada célula APARECE, dado o tipo da coluna — é o que o preview mostra
    embaixo do valor cru, para conferir a conversão antes de salvar. None = não há o que
    converter (tipo número/texto) ou a conversão falhou."""
    if vtype in ("epoch", "timestamp"):
        dts = column_datetimes(cells, vtype, ts_format)
        fmt = ts_display_format(dts)
        return [fmt_ts(d, fmt) if d else None for d in dts]
    if vtype == "booleano":
        return [None if (b := tailer.to_bool(c)) is None else ("sim" if b else "não")
                for c in cells]
    if vtype == "inteiro":
        return [None if (v := tailer.to_int(c, decimal, strip_re)) is None
                else str(int(v)) for c in cells]
    return [None] * len(cells)


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

    dts: list[datetime | None] = []
    raws: list[str] = []
    for row in rows:
        raw = row[ts_idx].strip().strip('"') if ts_idx is not None and ts_idx < len(row) else ""
        raws.append(raw)
        dts.append(parse_timestamp(raw, ts_format))
    # A resolução do rótulo só pode ser decidida com a série inteira em mãos.
    ts_fmt = ts_display_format(dts)
    timestamps: list[str] = [fmt_ts(d, ts_fmt) if d is not None else r
                             for d, r in zip(dts, raws)]

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
    decimal = fcfg.get("decimal", ".")
    variables = []
    for var in fcfg.get("variables", []):
        idx = _column_index(var["column"], header, row_len)
        if idx is None:
            problems.append(f"coluna '{var['column']}' não encontrada (variável '{var['label']}')")
            continue
        vtype = var.get("type") or "numero"
        cells = [row[idx] if idx < len(row) else "" for row in rows]
        entry = {
            "label": var["label"],
            "unit": var.get("unit"),
            "min": var.get("min"),
            "max": var.get("max"),
            "type": vtype,
        }
        if vtype == "texto":
            # Texto não vira linha: o gráfico fica de fora e a tela mostra o último
            # valor. Manter 'values' (tudo None) preserva o contrato da API.
            limpos = [(c or "").strip().strip('"') for c in cells]
            entry["values"] = [None] * len(cells)
            entry["last_text"] = next((c for c in reversed(limpos) if c), None)
        elif vtype in ("epoch", "timestamp"):
            # Carimbo epoch numa coluna QUALQUER: o valor plotado é o próprio epoch em
            # segundos (a linha mostra quando aquele bloco de sensor avançou) e a
            # formatação para data/hora acontece na exibição.
            dts_var = column_datetimes(cells, vtype, ts_format)
            entry["values"] = [d.timestamp() if d else None for d in dts_var]
            f = ts_display_format(dts_var)
            entry["ts_res"] = ("ms" if f == TS_DISPLAY_MS
                               else "s" if f == TS_DISPLAY_S else "min")
        elif vtype == "booleano":
            entry["values"] = [tailer.to_bool(c) for c in cells]
        elif vtype == "inteiro":
            entry["values"] = [tailer.to_int(c, decimal, strip_re) for c in cells]
        else:
            entry["values"] = [tailer.to_float(c, decimal, strip_re) for c in cells]
        variables.append(entry)

    # Última leitura em formato COMPLETO (com ano) para o tooltip do mapa — o eixo do
    # Mesmo formato do eixo (TS_DISPLAY): data completa, hora e minuto, sem segundos.
    # Pega o timestamp mais novo que casou o formato.
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
        "last_reading": fmt_ts(last_dt, ts_fmt) if last_dt else None,
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
        if pinfo.get("matched"):
            note += f" (arquivo lido: {Path(pinfo['matched']).name})"
        if pinfo.get("fallback"):
            note += (" (ainda não existe — lendo o mais recente que casa com o padrão: "
                     f"{pinfo['fallback']})")
        diagnostics.insert(0, note)

    # Carimbo convertido por linha: o preview mostra o valor CRU (é a conferência do
    # parse) e, embaixo, a data/hora que a dashboard vai usar. Sem isso um epoch fica
    # como um número grande e não dá para saber se a leitura está certa.
    ts_fmt = (entry.get("timestamp") or {}).get("format")
    ts_display: list[str | None] = []
    if ts_idx is not None:
        dts_prev = [parse_timestamp(r[ts_idx] if ts_idx < len(r) else "", ts_fmt)
                    for r in out_rows]
        disp_fmt = ts_display_format(dts_prev)
        ts_display = [fmt_ts(d, disp_fmt) if d else None for d in dts_prev]

    # Conversão de TODAS as colunas mapeadas, conforme o tipo de cada variável — sem
    # isto o preview só convertia o carimbo principal e uma coluna marcada como
    # 'epoch' continuava aparecendo como um número de dez dígitos, sem como conferir.
    ncols = max((len(r) for r in out_rows), default=0)
    col_display: dict[str, list] = {}
    for var in entry.get("variables", []):
        vtype = var.get("type") or "numero"
        if vtype in ("numero", "texto"):
            continue
        vidx = _column_index(var["column"], header, ncols)
        if vidx is None:
            continue
        cells = [r[vidx] if vidx < len(r) else "" for r in out_rows]
        col_display[str(vidx)] = column_display(cells, vtype, ts_fmt,
                                                entry.get("decimal", "."), strip_re)

    return {
        "path": str(path),
        "path_info": pinfo,
        "header": header,
        "rows": out_rows,
        "ts_index": ts_idx,
        "ts_display": ts_display,
        "col_display": col_display,
        "diagnostics": diagnostics,
    }
