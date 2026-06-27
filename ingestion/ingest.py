#!/usr/bin/env python3
"""Serviço de ingestão do CARBONARA.

Observa o diretório de pouso (``landing/``), faz parse dos arquivos novos e carrega as
medições no TimescaleDB. É deliberadamente cego ao TRANSPORTE: não sabe (nem importa)
se o arquivo chegou via FTP, SFTP ou rsync — só vê o diretório.

Fluxo por arquivo:
    landing/<station_id>/arquivo.csv
        -> escolhe o parser (config) -> parse -> INSERT em measurements (idempotente)
        -> move para archive/<station_id>/<YYYY-MM-DD>/   (sucesso)
        -> move para quarantine/<station_id>/             (erro)

Config: ./config.yaml (ver config.example.yaml). Conexão do banco via variáveis de ambiente.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path

import psycopg2
import yaml
from psycopg2.extras import execute_values

import parsers  # registra os parsers via __init__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ingestion] %(message)s",
)
log = logging.getLogger("ingestion")


# --- Configuração ---------------------------------------------------------------
def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def db_dsn() -> dict:
    return dict(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=os.environ.get("DB_NAME", "carbonara"),
    )


# --- Banco de dados -------------------------------------------------------------
def ensure_station(conn, station_id: str) -> None:
    """Auto-registra uma estação desconhecida com metadados mínimos (preenchidos depois)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stations (station_id, name)
            VALUES (%s, %s)
            ON CONFLICT (station_id) DO NOTHING
            """,
            (station_id, station_id),
        )
    conn.commit()


def insert_measurements(conn, rows: list[tuple]) -> int:
    """Insere em lote, ignorando duplicatas pela chave natural (idempotente)."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO measurements
                (time, station_id, variable_id, value, quality, source_file)
            VALUES %s
            ON CONFLICT (time, station_id, variable_id) DO NOTHING
            """,
            rows,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


# --- Movimentação de arquivos ---------------------------------------------------
def _move(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_dir / src.name))


def archive(src: Path, archive_root: Path, station_id: str) -> None:
    day = time.strftime("%Y-%m-%d")
    _move(src, archive_root / station_id / day)


def quarantine(src: Path, quarantine_root: Path, station_id: str) -> None:
    _move(src, quarantine_root / station_id)


# --- Seleção de parser ----------------------------------------------------------
def parser_for(station_id: str, cfg: dict):
    """Resolve o parser pela config: específico da estação ou o default."""
    stations_cfg = cfg.get("stations", {})
    spec = stations_cfg.get(station_id) or cfg.get("default_parser")
    if not spec:
        raise KeyError(f"sem parser configurado para '{station_id}' e sem default_parser")
    return parsers.get_parser(spec["parser"], spec.get("options", {}))


# --- Loop -----------------------------------------------------------------------
def discover_files(landing: Path, settle_seconds: int) -> list[Path]:
    """Arquivos prontos para processar: subpasta = station_id, e estáveis (não em upload)."""
    ready = []
    now = time.time()
    for station_dir in sorted(p for p in landing.iterdir() if p.is_dir()):
        for f in sorted(station_dir.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            # Evita pegar arquivo no meio de um upload: exige inatividade mínima.
            if now - f.stat().st_mtime < settle_seconds:
                continue
            ready.append(f)
    return ready


def process_file(conn, f: Path, landing: Path, cfg: dict, paths: dict) -> None:
    station_id = f.relative_to(landing).parts[0]
    try:
        parser = parser_for(station_id, cfg)
        ensure_station(conn, station_id)
        rows = [
            (m.time, m.station_id, m.variable_id, m.value, m.quality, f.name)
            for m in parser.parse(str(f), station_id)
        ]
        inserted = insert_measurements(conn, rows)
        archive(f, paths["archive"], station_id)
        log.info("%s: %d medições lidas, %d novas inseridas", f.name, len(rows), inserted)
    except Exception:  # noqa: BLE001 — qualquer falha isola o arquivo, não derruba o serviço
        conn.rollback()
        log.exception("falha ao processar %s; movendo para quarantine", f)
        quarantine(f, paths["quarantine"], station_id)


def main() -> int:
    cfg = load_config(os.environ.get("INGEST_CONFIG", "config.yaml"))
    paths = {
        "landing": Path(cfg.get("paths", {}).get("landing", "/data/landing")),
        "archive": Path(cfg.get("paths", {}).get("archive", "/data/archive")),
        "quarantine": Path(cfg.get("paths", {}).get("quarantine", "/data/quarantine")),
    }
    poll = int(os.environ.get("INGEST_POLL_SECONDS", cfg.get("poll_seconds", 30)))
    settle = int(cfg.get("settle_seconds", 5))

    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)

    log.info("ingestão iniciada (poll=%ss, landing=%s)", poll, paths["landing"])
    conn = psycopg2.connect(**db_dsn())
    try:
        while True:
            for f in discover_files(paths["landing"], settle):
                process_file(conn, f, paths["landing"], cfg, paths)
            time.sleep(poll)
    except KeyboardInterrupt:
        log.info("encerrando ingestão")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
