"""Monta as séries dos gráficos (e o preview do formulário) a partir do tail do arquivo."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from . import api_client, config_store, tailer

MAX_POINTS = 500  # decimação: acima disso o gráfico fica pesado sem ganho visual


# Parse de carimbos, resolução de colunas e cabeçalho: implementação no pacote
# compartilhado carbonara_parse (a ingestão usa EXATAMENTE as mesmas funções — o que o
# preview mostra é o que entra no banco). Nomes locais mantidos para os chamadores.
from carbonara_parse.records import column_index as _column_index  # noqa: E402
from carbonara_parse.records import compile_strip as _compile_strip  # noqa: E402
from carbonara_parse.records import read_header as _read_header  # noqa: E402
from carbonara_parse.timeparse import (  # noqa: E402,F401
    TS_DISPLAY, TS_DISPLAY_MS, TS_DISPLAY_S, fmt_ts, parse_datetime_text, parse_epoch,
    parse_timestamp, ts_display_format,
)


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
    """Série de um arquivo monitorado. Arquivo INGERIDO no banco (ingest=true) com a
    API configurada → lê do banco (o que a API serve é o que o resto do mundo vê);
    qualquer falha aí volta ao tail do arquivo, avisando em ``problems``."""
    if fcfg.get("ingest") and api_client.configured(cfg):
        try:
            return series_from_api(cfg, station, fcfg)
        except _SemDadosNoBanco as exc:
            out = build_series_from_file(cfg, station, fcfg)
            out.setdefault("problems", []).insert(0, f"{exc} — lendo o arquivo diretamente")
            return out
        except Exception as exc:  # noqa: BLE001 — a API nunca derruba o gráfico
            out = build_series_from_file(cfg, station, fcfg)
            out.setdefault("problems", []).insert(
                0, f"banco indisponível ({exc}) — lendo o arquivo diretamente")
            return out
    return build_series_from_file(cfg, station, fcfg)


class _SemDadosNoBanco(RuntimeError):
    """A API respondeu, mas ainda não há valores deste arquivo (ingestão não rodou)."""


def _display_tz(cfg: dict, fcfg: dict):
    """Fuso em que os carimbos do banco (UTC) são mostrados: o do arquivo — assim os
    rótulos batem com o que está escrito no arquivo — ou o global da dashboard."""
    from datetime import timezone as _tz
    name = fcfg.get("timezone") or cfg.get("timezone")
    if not name:
        return _tz.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return _tz.utc


def series_from_api(cfg: dict, station: dict, fcfg: dict) -> dict:
    """Mesmo contrato de ``build_series_from_file``, com os dados vindos da API
    (``/series?order=desc``: os últimos ``tail_lines`` registros)."""
    from carbonara_parse.records import variable_id_of
    from datetime import datetime as _dt

    variables_cfg = fcfg.get("variables") or []
    numericas = [(v, variable_id_of(v)) for v in variables_cfg
                 if (v.get("type") or "numero") != "texto"]
    vids = [vid for _v, vid in numericas]
    tail_lines = int(fcfg.get("tail_lines") or 100)
    # limit conta LINHAS (instante × variável); pede-se um instante a mais e corta-se
    # aos últimos tail_lines instantes aqui — o 'truncated' da API não se aplica.
    params = {"station": station["id"], "vars": ",".join(vids), "agg": "raw", "qc": "all",
              "order": "desc", "limit": (max(1, tail_lines) + 1) * max(1, len(vids))}
    window_hours = fcfg.get("window_hours")
    if window_hours:
        params["start"] = f"now-{int(window_hours)}h"
    data = api_client.get(cfg, "/series", params)

    tz = _display_tz(cfg, fcfg)
    por_var = {s["variable_id"]: s for s in data.get("series", [])}
    # Eixo comum: união dos instantes de todas as variáveis (ordenado).
    instantes: set[str] = set()
    for s in por_var.values():
        instantes.update(s["times"])
    ordenados = sorted(instantes)[-max(1, tail_lines):]
    if not ordenados:
        raise _SemDadosNoBanco("o banco ainda não tem valores deste arquivo (a ingestão "
                               "roda a cada ciclo; confira o chip 'no banco' na estação)")
    dts = [_dt.fromisoformat(t.replace("Z", "+00:00")).astimezone(tz).replace(tzinfo=None)
           for t in ordenados]
    ts_fmt = ts_display_format(dts)
    idx = {t: i for i, t in enumerate(ordenados)}

    problems: list[str] = []
    sem_valores: list[str] = []
    variables = []
    for v, vid in numericas:
        vtype = v.get("type") or "numero"
        entry = {"label": v["label"], "unit": v.get("unit"), "min": v.get("min"),
                 "max": v.get("max"), "type": vtype, "values": [None] * len(ordenados)}
        s = por_var.get(vid)
        if s:
            for t, val, q in zip(s["times"], s["values"], s.get("quality", [])):
                if t in idx:
                    entry["values"][idx[t]] = val
            if vtype in ("epoch", "timestamp"):
                dts_var = [_dt.fromtimestamp(x, tz).replace(tzinfo=None)
                           for x in s["values"] if x is not None]
                f = ts_display_format(dts_var)
                entry["ts_res"] = "ms" if f == TS_DISPLAY_MS else "s" if f == TS_DISPLAY_S else "min"
        else:
            sem_valores.append(f"{v['label']} ({vid})")
        variables.append(entry)
    if sem_valores:
        mostra = ", ".join(sem_valores[:4]) + (f" e mais {len(sem_valores) - 4}" if len(sem_valores) > 4 else "")
        problems.append(f"{len(sem_valores)} variável(is) ainda sem valores no banco: {mostra}")
    # Colunas de texto não vão para o banco: último valor lido do próprio arquivo.
    textos = [v for v in variables_cfg if (v.get("type") or "numero") == "texto"]
    if textos:
        try:
            tail = build_series_from_file(cfg, station, {**fcfg, "tail_lines": 5})
            ultimos = {e["label"]: e.get("last_text") for e in tail.get("variables", [])}
        except Exception:  # noqa: BLE001
            ultimos = {}
        for v in textos:
            variables.append({"label": v["label"], "unit": v.get("unit"), "min": None,
                              "max": None, "type": "texto", "values": [None] * len(ordenados),
                              "last_text": ultimos.get(v["label"])})
    # Ordem original do registro (a página indexa os canvas por posição).
    ordem = {v["label"]: i for i, v in enumerate(variables_cfg)}
    variables.sort(key=lambda e: ordem.get(e["label"], 999))

    keep = _decimate(len(ordenados))
    return {
        "file": fcfg["id"],
        "path": str(fcfg.get("path")),
        "path_info": {"source": "banco"},
        "rows_read": len(ordenados),
        "decimated": len(keep) < len(ordenados),
        "last_reading": fmt_ts(dts[-1], ts_fmt) if dts else None,
        "timestamps": [fmt_ts(dts[i], ts_fmt) for i in keep],
        "epochs": [round(_dt.fromisoformat(ordenados[i].replace("Z", "+00:00")).timestamp(), 3)
                   for i in keep],
        "variables": [{**v, "values": [v["values"][i] for i in keep]} for v in variables],
        "problems": problems,
        "source": "banco",
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


def build_series_from_file(cfg: dict, station: dict, fcfg: dict) -> dict:
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
    if pinfo.get("matches", 0) > 1:
        problems.append(
            f"{pinfo['matches']} arquivos casam com o padrão do período — lendo só o "
            f"mais recente ({path.name}); se não é o esperado, o truncamento/curinga "
            "está frouxo demais")
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
        # cujo timestamp não casou o formato. Vai com MILÉSIMOS (era int): arquivos
        # sub-segundo (.sfo) truncavam todas as linhas do mesmo segundo para o mesmo
        # instante e o eixo comum perdia a resolução real do dado.
        "epochs": [round(dts[i].timestamp(), 3) if dts[i] is not None else None
                   for i in keep],
        "variables": [
            {**v, "values": [v["values"][i] for i in keep]} for v in variables
        ],
        "problems": problems,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


# --- Frescor dos arquivos monitorados (coluna de alerta da aba Estações) ----------
# A aba Estações é a tela de saúde da rede: aqui interessa APENAS o último carimbo de
# cada arquivo, não a série. Por isso lê-se um punhado de linhas do fim e para —
# montar a série inteira de cada cadastro a cada refresh sairia caro (um arquivo de
# datalogger tem dezenas de variáveis) e nada disso apareceria na tela.
#
# Uma estação pode estar ONLINE (rsync entregando em dia) e mesmo assim com um sensor
# parado há dias: o arquivo continua chegando, mas com carimbo velho. É justamente
# esse buraco entre "a estação transmite" e "o sensor mede" que esta coluna cobre.
FRESH_TAIL_LINES = 5
FRESH_WARN_SECONDS = 86400          # > 1 dia  → amarelo
FRESH_ALERT_SECONDS = 7 * 86400     # > 7 dias → vermelho
# Ordem de gravidade: um arquivo que ainda não chegou (dynamic_path do período) é
# NEUTRO, não falha — mesma regra do gráfico, que mostra 'aguardando' em tom neutro.
FRESH_RANK = {"ok": 0, "unknown": 1, "warn": 2, "alert": 3}
_FRESH_TTL = 30.0
_fresh_cache: dict = {}


def _human_age(seconds: float) -> str:
    """Idade curta para caber na célula da tabela ('há 3 d', 'há 5 h', 'há 12 min')."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _file_last_dt(cfg: dict, fcfg: dict):
    """(datetime|None, Path|None, nota) do último registro reconhecido do arquivo."""
    try:
        path, pinfo = config_store.resolve_file_path(cfg, fcfg)
    except (ValueError, KeyError, OSError) as exc:
        return None, None, str(exc)
    if not path.is_file():
        if pinfo.get("dynamic"):
            return None, None, "aguardando o arquivo do período"
        return None, None, "arquivo não encontrado"
    try:
        lines = tailer.tail_data_lines(path, FRESH_TAIL_LINES,
                                       fcfg.get("skip_header_lines", 0))
        rows = tailer.parse_rows(lines, fcfg.get("separator", ","),
                                 fcfg.get("quotechar", '"'))
    except OSError as exc:
        return None, path, f"falha ao ler: {exc}"
    if not rows:
        return None, path, "sem linhas de dados"
    ts_cfg = fcfg.get("timestamp") or {}
    ts_idx = _column_index(ts_cfg.get("column", 0), _read_header(path, fcfg),
                           max(len(r) for r in rows))
    ts_format = ts_cfg.get("format") or "%Y-%m-%d %H:%M:%S"
    # De trás para frente: a última linha pode estar sendo escrita neste instante.
    for row in reversed(rows):
        raw = row[ts_idx].strip().strip('"') if ts_idx is not None and ts_idx < len(row) else ""
        dt = parse_timestamp(raw, ts_format)
        if dt is not None:
            return dt, path, None
    return None, path, "carimbo não reconhecido"


def _file_label(fcfg: dict, path: Path | None) -> str:
    """Nome curto do arquivo na coluna. Apelido AUTOMÁTICO (o que contém o id) não
    serve: em cadastro com caminho rotativo ele congela o nome do dia em que foi
    criado. Nesse caso vale o nome REAL do arquivo lido agora."""
    alias = (fcfg.get("alias") or "").strip()
    fid = fcfg.get("id", "?")
    if alias and fid not in alias:
        return alias
    return path.name if path is not None else fid


def file_freshness(cfg: dict, station: dict) -> dict:
    """Último carimbo de cada arquivo monitorado da estação, com nível de alerta.

    ``level`` da estação = o PIOR entre os arquivos (ver FRESH_RANK). O resultado
    fica em cache por estação: a aba Estações se recarrega sozinha e reabrir todos
    os arquivos de todas as estações a cada ciclo é trabalho jogado fora.
    """
    files = station.get("files") or []
    # Assinatura do cadastro no cache: adicionar/remover/reapontar um arquivo tem de
    # aparecer na hora, sem esperar o TTL vencer.
    sig = tuple((f.get("id"), f.get("path"), f.get("alias")) for f in files)
    agora = time.time()
    hit = _fresh_cache.get(station.get("id"))
    if hit and hit["sig"] == sig and agora - hit["t"] < _FRESH_TTL:
        return hit["data"]

    out = []
    for fcfg in files:
        dt, path, nota = _file_last_dt(cfg, fcfg)
        idade = None if dt is None else max(0.0, agora - dt.timestamp())
        if idade is None:
            nivel = "unknown"
        elif idade >= FRESH_ALERT_SECONDS:
            nivel = "alert"
        elif idade >= FRESH_WARN_SECONDS:
            nivel = "warn"
        else:
            nivel = "ok"
        out.append({
            "id": fcfg.get("id"),
            "label": _file_label(fcfg, path),
            "level": nivel,
            "note": nota,
            "age_seconds": None if idade is None else int(idade),
            "age_display": None if idade is None else _human_age(idade),
            "display": fmt_ts(dt, TS_DISPLAY) if dt is not None else None,
        })

    pior = max((f["level"] for f in out), key=lambda n: FRESH_RANK[n], default="ok")
    lidos = [f for f in out if f["display"]]
    recente = min(lidos, key=lambda f: f["age_seconds"]) if lidos else None
    data = {
        "files": out,
        "level": pior if out else "none",
        "total": len(out),
        "ok_count": sum(1 for f in out if f["level"] == "ok"),
        "problems": [f for f in out if f["level"] in ("warn", "alert")],
        "unknown": [f for f in out if f["level"] == "unknown"],
        # Carimbo mais recente entre todos os arquivos: o "último dado" da estação.
        "newest": recente["display"] if recente else None,
        "newest_age": recente["age_display"] if recente else None,
        # Uma linha por arquivo, para o title= da célula (o detalhe completo sem
        # ocupar a tabela).
        "tooltip": "\n".join(
            f"{f['label']}: " + (f"{f['display']} (há {f['age_display']})"
                                 if f["display"] else (f["note"] or "sem leitura"))
            for f in out) or "nenhum arquivo monitorado",
    }
    _fresh_cache[station.get("id")] = {"t": agora, "sig": sig, "data": data}
    return data


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
        if pinfo.get("truncate_chars"):
            note += f" (truncado: {pinfo['truncate_chars']} caractere(s) do fim do nome)"
        if pinfo.get("hour_offset"):
            note += f" (relógio deslocado {pinfo['hour_offset']:+d}h)"
        if pinfo.get("matched"):
            note += f" (arquivo lido: {Path(pinfo['matched']).name})"
        if pinfo.get("fallback"):
            note += (" (ainda não existe — lendo o mais recente que casa com o padrão: "
                     f"{pinfo['fallback']})")
        if pinfo.get("matches", 0) > 1:
            note += (f" — ATENÇÃO: {pinfo['matches']} arquivos casam com o padrão; "
                     "só o mais recente é lido. Truncamento/curinga frouxo demais?")
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
