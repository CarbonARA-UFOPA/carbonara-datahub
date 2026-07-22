#!/usr/bin/env python3
"""Serviço de processamento (QA/QC) do CARBONARA.

Roda passadas periódicas de controle de qualidade sobre as medições recém-inseridas
pela ingestão, marcando a coluna ``measurements.quality`` com flags (ver convenção em
``checks/base.py``). É a quarta camada da arquitetura: lê e escreve APENAS no banco —
não conhece arquivos, diretórios nem transporte.

Fluxo por passada:
    marca-d'água (última ingested_at avaliada, persistida no banco)
        -> roda cada check do config sobre a janela ingested_at > marca-d'água
        -> se TODOS os checks passarem, avança a marca-d'água

Os checks são idempotentes (só tocam linhas com quality = 0), então reavaliar a mesma
janela — após uma falha, por exemplo — não corrompe nada.

Config: ./config.yaml (ver config.example.yaml). Conexão do banco via variáveis de ambiente.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import yaml

import checks  # registra os checks via __init__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [processing] %(message)s",
)
log = logging.getLogger("processing")

WATERMARK_KEY = "qc_watermark"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


# --- Estado (marca-d'água) -------------------------------------------------------
def ensure_state_table(conn) -> None:
    """Cria a tabela de estado do processamento (idempotente, sem depender do init/)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_state (
                key   TEXT PRIMARY KEY,
                value TIMESTAMPTZ NOT NULL
            )
            """
        )
    conn.commit()


def read_watermark(conn) -> datetime:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM processing_state WHERE key = %s", (WATERMARK_KEY,))
        row = cur.fetchone()
    return row[0] if row else EPOCH


def write_watermark(conn, value: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO processing_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (WATERMARK_KEY, value),
        )
    conn.commit()


# --- Passada de QC ----------------------------------------------------------------
def pending_target(conn, since: datetime) -> datetime | None:
    """Limite superior da janela desta passada (linhas que chegarem depois ficam p/ a próxima)."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(ingested_at) FROM measurements WHERE ingested_at > %s", (since,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def run_pass(conn, cfg: dict) -> None:
    since = read_watermark(conn)
    target = pending_target(conn, since)
    if target is None:
        return  # nada novo desde a última passada

    all_ok = True
    for spec in cfg.get("checks", []):
        name = spec["check"]
        try:
            check = checks.get_check(name, spec.get("options", {}))
            flagged = check.run(conn, since)
            conn.commit()
            log.info("check %s: %d medições sinalizadas", name, flagged)
        except Exception:  # noqa: BLE001 — falha de um check não derruba o serviço
            conn.rollback()
            all_ok = False
            log.exception("falha no check %s; janela será reavaliada na próxima passada", name)

    # Só avança a marca-d'água com todos os checks OK — reavaliar é barato e idempotente.
    if all_ok:
        write_watermark(conn, target)


def main() -> int:
    cfg = load_config(os.environ.get("PROCESSING_CONFIG", "config.yaml"))
    poll = int(os.environ.get("PROCESSING_POLL_SECONDS", cfg.get("poll_seconds", 60)))

    log.info("processamento (QA/QC) iniciado (poll=%ss, %d checks)", poll, len(cfg.get("checks", [])))
    conn = psycopg2.connect(**db_dsn())
    ensure_state_table(conn)
    try:
        while True:
            run_pass(conn, cfg)
            time.sleep(poll)
    except KeyboardInterrupt:
        log.info("encerrando processamento")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
