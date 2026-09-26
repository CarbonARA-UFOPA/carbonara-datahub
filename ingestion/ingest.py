#!/usr/bin/env python3
"""Serviço de ingestão do CARBONARA — dirigido pelos ARQUIVOS MONITORADOS da dashboard.

Só entra no banco o que a dashboard registrou com "Ingerir no banco" marcado, e só as
variáveis listadas no registro. O registro (dashboard/config.yaml) É a configuração de
ingestão: separador, cabeçalho, coluna/formato do timestamp, fuso, tipos, limites.

Regras (ver docs/estrategia-dados.md):
- ``incoming/`` é a fonte da verdade e NUNCA é tocado: nada de mover para archive/ ou
  quarantine/. O container monta a raiz de dados somente leitura.
- Leitura INCREMENTAL: um ledger (``ingest_files``) guarda, por arquivo real, o offset
  em bytes até onde já se leu. A cada ciclo lê-se só o que cresceu. Um arquivo que
  encolheu ou cujo início mudou (rotação/recriação) recomeça do zero.
- Idempotente: ``ON CONFLICT (time, station_id, variable_id) DO NOTHING``. Reingerir
  tudo (``--reprocess``) é o mecanismo de reconstrução do banco.
- Fuso: cada arquivo declara o fuso dos seus carimbos; o banco guarda UTC.
- QC: flag, nunca altera o valor. Os limites Mín/Máx do registro alimentam o
  ``range_check``; os checks globais de processing/config.yaml se somam.
- Erro em um arquivo fica registrado no ledger (status/error) e não derruba o loop.

Uso:
    python ingest.py                     # loop (INGEST_POLL_SECONDS)
    python ingest.py --once              # um ciclo
    python ingest.py --reprocess <station> [--file <id>] [--apagar]
        reinicia o offset dos arquivos (com --apagar remove antes as medições que
        eles geraram — para reingerir após mudar fuso/ID/tipo) e roda um ciclo.

Ambiente: DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME, DASHBOARD_CONFIG (config.yaml
da dashboard), INGEST_POLL_SECONDS, INGEST_SETTLE_SECONDS, INGEST_MAX_BYTES (por
arquivo e ciclo), INGEST_CYCLE_BUDGET_BYTES (por ciclo), PROCESSING_CONFIG.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import yaml
from psycopg2.extras import Json, execute_values

# Pacote compartilhado com a dashboard (raiz do repo no build; ../ em dev).
_HERE = Path(__file__).resolve().parent
for cand in (_HERE, _HERE.parent):
    if (cand / "carbonara_parse").is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

from carbonara_parse import paths, records  # noqa: E402
from processing import QualityPipeline  # noqa: E402
from processing.checks import get_check  # noqa: E402

INGEST_VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ingestion] %(message)s",
)
log = logging.getLogger("ingestion")

RESAMPLE_SECONDS = {"raw": 0, "1min": 60, "30min": 1800}


# --- Configuração ---------------------------------------------------------------
def db_dsn() -> dict:
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "user": os.environ.get("DB_USER", "carbonara"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "dbname": os.environ.get("DB_NAME", "carbonara"),
    }


class DashboardConfig:
    """Lê o config.yaml da dashboard (recarrega quando o mtime muda). Só o necessário:
    data_root, fuso global e as estações com seus arquivos."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._mtime: float | None = None
        self._cfg: dict = {"data_root": "./data", "timezone": None, "stations": []}

    def load(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            log.warning("config da dashboard não encontrado: %s", self.path)
            return self._cfg
        if mtime != self._mtime:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self._cfg = {
                "data_root": raw.get("data_root") or "./data",
                "timezone": raw.get("timezone"),
                "stations": raw.get("stations") or [],
                "_base": str(self.path.parent.resolve()),
            }
            self._mtime = mtime
            log.info("config da dashboard carregado: %d estação(ões)", len(self._cfg["stations"]))
        return self._cfg


# --- Banco de dados -------------------------------------------------------------
def ensure_station(cur, station: dict) -> None:
    """Cadastra/atualiza a estação com o que a dashboard sabe (nome, coordenadas).
    Campos que a dashboard não conhece (elevação, tipo de floresta…) ficam como estão."""
    cur.execute(
        """
        INSERT INTO stations (station_id, name, latitude, longitude)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (station_id) DO UPDATE SET
            name      = COALESCE(NULLIF(EXCLUDED.name, ''), stations.name),
            latitude  = COALESCE(EXCLUDED.latitude, stations.latitude),
            longitude = COALESCE(EXCLUDED.longitude, stations.longitude)
        """,
        (station["id"], station.get("name") or station["id"],
         station.get("latitude"), station.get("longitude")),
    )


def ensure_variables(cur, cols: list[records.ColumnSpec]) -> None:
    """Catálogo `variables`: cria as que faltam e mantém nome/unidade em dia com o registro."""
    rows = [(c.variable_id, c.label, c.unit) for c in cols if c.vtype != "texto"]
    if not rows:
        return
    execute_values(
        cur,
        """
        INSERT INTO variables (variable_id, name, unit) VALUES %s
        ON CONFLICT (variable_id) DO UPDATE SET
            name = EXCLUDED.name,
            unit = COALESCE(EXCLUDED.unit, variables.unit)
        """,
        rows,
    )


def upsert_monitored_file(cur, station_id: str, fcfg: dict, cols: list[records.ColumnSpec]) -> None:
    parser_cfg = {k: fcfg.get(k) for k in (
        "separator", "quotechar", "decimal", "skip_header_lines", "header_line",
        "timestamp", "strip_regex", "truncate_chars", "hour_offset")}
    variables = [{"variable_id": c.variable_id, "label": c.label, "unit": c.unit,
                  "min": c.vmin, "max": c.vmax, "type": c.vtype, "column": c.column}
                 for c in cols]
    cur.execute(
        """
        INSERT INTO monitored_files
            (station_id, file_key, path, alias, dynamic_path, timezone, resample, ingest,
             parser_config, variables, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (station_id, file_key) DO UPDATE SET
            path = EXCLUDED.path, alias = EXCLUDED.alias, dynamic_path = EXCLUDED.dynamic_path,
            timezone = EXCLUDED.timezone, resample = EXCLUDED.resample, ingest = EXCLUDED.ingest,
            parser_config = EXCLUDED.parser_config, variables = EXCLUDED.variables,
            updated_at = now()
        """,
        (station_id, fcfg["id"], fcfg.get("path"), fcfg.get("alias"),
         bool(fcfg.get("dynamic_path")), fcfg.get("timezone"), fcfg.get("resample") or "raw",
         bool(fcfg.get("ingest")), Json(parser_cfg), Json(variables)),
    )


def sync_monitored_flags(cur, cfg: dict) -> None:
    """Registros que deixaram de existir ou de ser ingeridos ficam com ingest=false no
    espelho (o histórico em measurements permanece)."""
    ativos = [(s["id"], f["id"]) for s in cfg["stations"]
              for f in (s.get("files") or []) if f.get("ingest") and f.get("id")]
    cur.execute("SELECT station_id, file_key FROM monitored_files WHERE ingest")
    for sid, key in cur.fetchall():
        if (sid, key) not in ativos:
            cur.execute("UPDATE monitored_files SET ingest = false, updated_at = now() "
                        "WHERE station_id = %s AND file_key = %s", (sid, key))


def insert_measurements(cur, rows: list[tuple]) -> int:
    """Insere em lote, ignorando duplicatas pela chave natural (idempotente)."""
    if not rows:
        return 0
    execute_values(
        cur,
        """
        INSERT INTO measurements
            (time, station_id, variable_id, value, quality, source_file, file_id)
        VALUES %s
        ON CONFLICT (time, station_id, variable_id) DO NOTHING
        """,
        rows, page_size=2000,
    )
    return cur.rowcount


# --- QC ------------------------------------------------------------------------
class _M:
    """Medição mínima para o pipeline de QC (mesma interface de parsers.base.Measurement)."""
    __slots__ = ("time", "station_id", "variable_id", "value", "quality")

    def __init__(self, time, station_id, variable_id, value, quality=0):
        self.time, self.station_id, self.variable_id = time, station_id, variable_id
        self.value, self.quality = value, quality


def build_qc(cols: list[records.ColumnSpec], global_qc: QualityPipeline) -> QualityPipeline:
    """Pipeline do arquivo: range_check com os limites do registro + checks globais."""
    limites = {}
    for c in cols:
        if c.vmin is None and c.vmax is None:
            continue
        limites[c.variable_id] = {"min": c.vmin if c.vmin is not None else float("-inf"),
                                  "max": c.vmax if c.vmax is not None else float("inf")}
    checks = []
    if limites:
        checks.append(get_check("range_check", {"flag": 2, "variables": limites}))
    checks.extend(global_qc.checks)
    return QualityPipeline(checks)


# --- Leitura de um arquivo ------------------------------------------------------
def _bucket(ts: datetime, seconds: int) -> datetime:
    e = int(ts.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(e, timezone.utc)


def read_new_data(path: Path, fcfg: dict, offset: int, tzname: str | None,
                  cols: list[records.ColumnSpec], max_bytes: int, closed: bool
                  ) -> tuple[list[tuple], int, dict]:
    """Lê do offset em diante e devolve (linhas prontas p/ insert sem station/file,
    novo_offset, stats). Linha = (time, variable_id, value, quality_parse).

    Com reamostragem, só os intervalos COMPLETOS são emitidos e o offset devolvido é o
    início do intervalo ainda aberto — o próximo ciclo o relê inteiro. Um arquivo
    ``closed`` (não cresce mais) descarrega tudo."""
    header = records.read_header(path, fcfg) if fcfg.get("header_line") else None
    strip_re = records.compile_strip(fcfg)
    decimal = fcfg.get("decimal", ".")
    ts_format = (fcfg.get("timestamp") or {}).get("format")
    step = RESAMPLE_SECONDS.get(fcfg.get("resample") or "raw", 0)
    stats = {"rows": 0, "bad_ts": 0, "last_time": None}
    out: list[tuple] = []
    new_offset = offset
    resolved: dict[int, list[records.ColumnSpec]] = {}

    # reamostragem: (bucket) -> {vid: [soma, n]}; início (offset) de cada bucket aberto
    acc: dict[datetime, dict[str, list[float]]] = {}
    bucket_start_offset: dict[datetime, int] = {}
    prev_offset = offset

    def cols_for(n: int) -> list[records.ColumnSpec]:
        if n not in resolved:
            lst = []
            for c in cols:
                if c.vtype == "texto":
                    continue
                idx = records.column_index(c.column, header, n)
                if idx is not None:
                    lst.append(records.ColumnSpec(c.variable_id, c.label, c.column, idx,
                                                  c.vtype, c.unit, c.vmin, c.vmax))
            resolved[n] = lst
        return resolved[n]

    def flush(bkt: datetime) -> None:
        for vid, (soma, n) in acc.pop(bkt).items():
            if n:
                out.append((bkt, vid, soma / n, 0))
        bucket_start_offset.pop(bkt, None)

    for rec in records.iter_records(path, fcfg, offset, tzname, header, max_bytes):
        stats["rows"] += 1
        if rec.time is None:
            stats["bad_ts"] += 1
            prev_offset = rec.offset
            if step == 0:
                new_offset = rec.offset
            continue
        stats["last_time"] = rec.time
        n = len(rec.cells)
        if step == 0:
            for c in cols_for(n):
                cell = rec.cells[c.index] if -n <= c.index < n else ""
                raw = (cell or "").strip().strip('"')
                if raw == "":
                    continue                       # célula vazia: não há leitura
                val = records.convert_cell(cell, c.vtype, decimal, strip_re, ts_format, tzname)
                out.append((rec.time, c.variable_id, val, 0 if val is not None else 1))
            new_offset = rec.offset
        else:
            bkt = _bucket(rec.time, step)
            if bkt not in acc:
                acc[bkt] = defaultdict(lambda: [0.0, 0])
                bucket_start_offset[bkt] = prev_offset
                # buckets anteriores a este fecharam (carimbos vêm em ordem)
                for old in [b for b in acc if b < bkt]:
                    flush(old)
            for c in cols_for(n):
                cell = rec.cells[c.index] if -n <= c.index < n else ""
                val = records.convert_cell(cell, c.vtype, decimal, strip_re, ts_format, tzname)
                if val is not None:
                    a = acc[bkt][c.variable_id]
                    a[0] += val
                    a[1] += 1
            prev_offset = rec.offset

    if step:
        if closed:
            for b in sorted(acc):
                flush(b)
            new_offset = prev_offset
        elif acc:
            # só o bucket mais recente fica aberto; tudo antes dele já foi descarregado
            aberto = max(acc)
            for b in [b for b in acc if b < aberto]:
                flush(b)
            new_offset = bucket_start_offset[aberto]
        else:
            new_offset = prev_offset
    return out, new_offset, stats


# --- Ledger --------------------------------------------------------------------
def ledger_get(cur, station_id: str, relpath: str):
    cur.execute("SELECT file_id, size_bytes, bytes_ingested, head_sha256, status "
                "FROM ingest_files WHERE station_id = %s AND relpath = %s", (station_id, relpath))
    return cur.fetchone()


def ledger_new(cur, station_id: str, file_key: str, relpath: str) -> int:
    cur.execute(
        "INSERT INTO ingest_files (station_id, file_key, relpath) VALUES (%s, %s, %s) "
        "ON CONFLICT (station_id, relpath) DO UPDATE SET file_key = EXCLUDED.file_key "
        "RETURNING file_id", (station_id, file_key, relpath))
    return cur.fetchone()[0]


def process_file(conn, station_id: str, fcfg: dict, path: Path, tzname: str | None,
                 cols: list[records.ColumnSpec], qc: QualityPipeline, settle: int,
                 max_bytes: int, closed: bool) -> int:
    """Um arquivo real: ledger → leitura incremental → QC → insert → ledger. Devolve os
    bytes lidos neste ciclo (para o orçamento do ciclo)."""
    st = path.stat()
    if time.time() - st.st_mtime < settle:
        return 0                                   # ainda sendo escrito: próximo ciclo
    relpath = str(path)
    with conn.cursor() as cur:
        row = ledger_get(cur, station_id, relpath)
        if row is None:
            file_id = ledger_new(cur, station_id, fcfg["id"], relpath)
            size_old, offset, sig_old = 0, 0, None
        else:
            file_id, size_old, offset, sig_old, _status = row
        conn.commit()

    if st.st_size == size_old and offset > 0 and st.st_size == offset:
        return 0                                   # nada novo
    if st.st_size == size_old and offset >= st.st_size and offset > 0:
        return 0

    sig = records.head_signature(path)
    reinicio = None
    if st.st_size < offset:
        reinicio = "arquivo encolheu"
    elif sig_old and sig != sig_old:
        reinicio = "início do arquivo mudou (rotação/recriação)"
    if reinicio:
        log.info("%s/%s: %s — reiniciando do zero", station_id, path.name, reinicio)
        offset = 0

    if st.st_size <= offset:
        with conn.cursor() as cur:
            cur.execute("UPDATE ingest_files SET size_bytes=%s, mtime=to_timestamp(%s), "
                        "head_sha256=%s WHERE file_id=%s", (st.st_size, st.st_mtime, sig, file_id))
        conn.commit()
        return 0

    t0 = time.time()
    try:
        lines, new_offset, stats = read_new_data(path, fcfg, offset, tzname, cols, max_bytes, closed)
        rows = []
        for ts, vid, val, qparse in lines:
            q = qc.evaluate(_M(ts, station_id, vid, val, qparse))
            rows.append((ts, station_id, vid, val, q, path.name, file_id))
        with conn.cursor() as cur:
            inserted = insert_measurements(cur, rows)
            cur.execute(
                """
                UPDATE ingest_files SET
                    size_bytes = %s, mtime = to_timestamp(%s), head_sha256 = %s,
                    bytes_ingested = %s, rows_total = rows_total + %s,
                    values_total = values_total + %s, bad_timestamps = bad_timestamps + %s,
                    last_time = COALESCE(%s, last_time), status = 'ok', error = NULL,
                    last_ingested = now()
                WHERE file_id = %s
                """,
                (st.st_size, st.st_mtime, sig, new_offset, stats["rows"], inserted,
                 stats["bad_ts"], stats["last_time"], file_id),
            )
        conn.commit()
        lidos = new_offset - offset
        if stats["rows"] or lidos:
            log.info("%s/%s: +%d bytes, %d registro(s), %d valor(es) novo(s), %d carimbo(s) "
                     "inválido(s), %.1fs", station_id, path.name, lidos, stats["rows"], inserted,
                     stats["bad_ts"], time.time() - t0)
        return max(lidos, 0)
    except Exception as exc:  # noqa: BLE001 — isola o arquivo, nunca derruba o loop
        conn.rollback()
        log.exception("falha em %s/%s", station_id, path.name)
        with conn.cursor() as cur:
            cur.execute("UPDATE ingest_files SET status='error', error=%s WHERE file_id=%s",
                        (f"{type(exc).__name__}: {exc}"[:500], file_id))
        conn.commit()
        return 0


# --- Ciclo ---------------------------------------------------------------------
def run_cycle(conn, cfg: dict, global_qc: QualityPipeline, settle: int, max_bytes: int,
              budget: int) -> dict:
    """Um ciclo completo sobre todos os registros com ingest=true."""
    gastos = 0
    resumo = {"files": 0, "skipped_budget": 0}
    with conn.cursor() as cur:
        for station in cfg["stations"]:
            if any(f.get("ingest") for f in station.get("files") or []):
                ensure_station(cur, station)
        sync_monitored_flags(cur, cfg)
    conn.commit()

    for station in cfg["stations"]:
        sid = station.get("id")
        for fcfg in station.get("files") or []:
            if not fcfg.get("ingest") or not fcfg.get("id"):
                continue
            tzname = fcfg.get("timezone") or station.get("timezone") or cfg.get("timezone")
            cols = records.resolve_columns(fcfg, None, 0)   # índices resolvidos por arquivo
            try:
                with conn.cursor() as cur:
                    ensure_variables(cur, cols)
                    upsert_monitored_file(cur, sid, fcfg, cols)
                conn.commit()
                candidatos = paths.all_matching_files(cfg, fcfg)
            except Exception:  # noqa: BLE001
                conn.rollback()
                log.exception("registro %s/%s: falha ao preparar", sid, fcfg.get("id"))
                continue
            if not candidatos:
                continue
            qc = build_qc(cols, global_qc)
            step = RESAMPLE_SECONDS.get(fcfg.get("resample") or "raw", 0)
            # O mais recente primeiro (dado novo aparece antes do backfill); os demais
            # (dias anteriores de um caminho dinâmico) são fechados: descarregam tudo.
            atual = candidatos[-1]
            agora = time.time()
            for p in reversed(candidatos):
                if gastos >= budget:
                    resumo["skipped_budget"] += 1
                    continue
                closed = p != atual or (step and agora - p.stat().st_mtime > step + settle + 60)
                gastos += process_file(conn, sid, fcfg, p, tzname, cols, qc, settle,
                                       max_bytes, bool(closed))
                resumo["files"] += 1
    return resumo


def reprocess(conn, cfg: dict, station_id: str, file_key: str | None, apagar: bool) -> None:
    """Zera o offset dos arquivos de uma estação (ou de um registro) e, com --apagar,
    remove antes as medições que eles geraram."""
    with conn.cursor() as cur:
        cond = "station_id = %s" + (" AND file_key = %s" if file_key else "")
        args = (station_id, file_key) if file_key else (station_id,)
        cur.execute(f"SELECT file_id, relpath FROM ingest_files WHERE {cond}", args)
        alvos = cur.fetchall()
        if not alvos:
            log.warning("nenhum arquivo no ledger para %s%s", station_id,
                        f"/{file_key}" if file_key else "")
            return
        ids = [a[0] for a in alvos]
        if apagar:
            cur.execute("DELETE FROM measurements WHERE station_id = %s AND file_id = ANY(%s)",
                        (station_id, ids))
            log.info("%d medição(ões) removida(s) de %s", cur.rowcount, station_id)
        cur.execute("UPDATE ingest_files SET bytes_ingested = 0, size_bytes = 0, head_sha256 = NULL, "
                    "rows_total = 0, values_total = 0, bad_timestamps = 0, last_time = NULL, "
                    "status = 'ok', error = NULL WHERE file_id = ANY(%s)", (ids,))
        cur.execute("INSERT INTO pipeline_runs (pipeline, version, finished, status, stats) "
                    "VALUES ('reprocess', %s, now(), 'ok', %s)",
                    (INGEST_VERSION, Json({"station": station_id, "file": file_key,
                                           "apagar": apagar, "files": len(ids)})))
    conn.commit()
    log.info("offset zerado em %d arquivo(s) de %s", len(ids), station_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingestão CARBONARA (dirigida pela dashboard)")
    ap.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    ap.add_argument("--reprocess", metavar="STATION", help="zera o offset dos arquivos da estação")
    ap.add_argument("--file", metavar="FILE_KEY", help="só o registro (id do arquivo na dashboard)")
    ap.add_argument("--apagar", action="store_true",
                    help="com --reprocess: apaga antes as medições geradas por esses arquivos")
    args = ap.parse_args()

    dash = DashboardConfig(os.environ.get("DASHBOARD_CONFIG", "/app/dashboard-config.yaml"))
    poll = int(os.environ.get("INGEST_POLL_SECONDS", "30"))
    settle = int(os.environ.get("INGEST_SETTLE_SECONDS", "5"))
    max_bytes = int(os.environ.get("INGEST_MAX_BYTES", str(64 * 1024 * 1024)))
    budget = int(os.environ.get("INGEST_CYCLE_BUDGET_BYTES", str(512 * 1024 * 1024)))

    qc_path = os.environ.get("PROCESSING_CONFIG", "processing/config.yaml")
    global_qc = QualityPipeline.from_file(qc_path)
    log.info("QA/QC global: %d check(s) (%s) + limites do registro por arquivo",
             len(global_qc.checks), qc_path)

    conn = psycopg2.connect(**db_dsn())
    conn.autocommit = False
    try:
        cfg = dash.load()
        if args.reprocess:
            reprocess(conn, cfg, args.reprocess, args.file, args.apagar)
            run_cycle(conn, cfg, global_qc, settle, max_bytes, budget)
            return 0
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pipeline_runs (pipeline, version, stats) VALUES "
                        "('ingestion', %s, %s) RETURNING run_id",
                        (INGEST_VERSION, Json({"poll": poll, "once": args.once})))
            run_id = cur.fetchone()[0]
        conn.commit()
        log.info("ingestão iniciada (poll=%ss, config=%s, run=%s)", poll, dash.path, run_id)
        ciclos = 0
        while True:
            cfg = dash.load()
            t0 = time.time()
            resumo = run_cycle(conn, cfg, global_qc, settle, max_bytes, budget)
            ciclos += 1
            if resumo["skipped_budget"]:
                log.info("orçamento do ciclo esgotado: %d arquivo(s) ficam para o próximo",
                         resumo["skipped_budget"])
            with conn.cursor() as cur:
                cur.execute("UPDATE pipeline_runs SET stats = stats || %s WHERE run_id = %s",
                            (Json({"cycles": ciclos, "last_cycle_s": round(time.time() - t0, 1),
                                   "last_cycle_files": resumo["files"]}), run_id))
            conn.commit()
            if args.once:
                break
            time.sleep(max(1, poll - (time.time() - t0)))
        with conn.cursor() as cur:
            cur.execute("UPDATE pipeline_runs SET finished = now(), status = 'ok' WHERE run_id = %s",
                        (run_id,))
        conn.commit()
    except KeyboardInterrupt:
        log.info("encerrando ingestão")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
