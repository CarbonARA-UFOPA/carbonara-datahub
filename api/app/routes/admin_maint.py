"""Conexão, diagnóstico e manutenção do banco (escopo ``admin``) — aba *Banco* da dashboard.

- ``/admin/db/connection``: onde a API está conectada (host/porta/banco/usuário vindos do
  ambiente) e o que o servidor diz de si (endereço, diretório de dados, início, réplicas).
- ``/admin/db/diagnostics``: rotinas de verificação — problemas na ingestão, duplicidades,
  órfãos, séries paradas, lacunas, qualidade, carimbos implausíveis, compressão, vácuo,
  tarefas com falha, consultas presas. Só LÊ.
- ``/admin/db/maintenance``: ações explícitas e nomeadas (analyze, compressão, atualizar
  agregados, vacuum, remover carimbos implausíveis). Cada uma diz o que fez.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db, settings
from ._common import iso, tzinfo_of

router = APIRouter(prefix="/admin/db", tags=["banco: diagnóstico"])

PLAUSIBLE_MIN = datetime(1990, 1, 1, tzinfo=timezone.utc)


@router.get("/connection", summary="Onde o banco está e como a API se conecta")
async def connection(tz: str | None = None, _who: auth.KeyInfo = Depends(auth.require("admin"))):
    tzi = tzinfo_of(tz)
    r = await db.pool().fetchrow(
        """
        SELECT inet_server_addr()::text AS server_addr, inet_server_port() AS server_port,
               current_setting('data_directory') AS data_directory,
               current_setting('listen_addresses') AS listen_addresses,
               current_setting('max_connections')::int AS max_connections,
               current_setting('shared_buffers') AS shared_buffers,
               current_setting('wal_level') AS wal_level,
               pg_postmaster_start_time() AS started, pg_is_in_recovery() AS in_recovery,
               version() AS version, current_database() AS database, current_user AS "user",
               pg_size_pretty(pg_database_size(current_database())) AS size,
               (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections
        """)
    repl = await db.pool().fetch(
        "SELECT client_addr::text, state, sync_state, replay_lag::text FROM pg_stat_replication")
    slots = await db.pool().fetch(
        "SELECT slot_name, active, wal_status, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained "
        "FROM pg_replication_slots")
    return {
        "client": {"host": settings.DB_HOST, "port": settings.DB_PORT, "database": settings.DB_NAME,
                   "user": settings.DB_USER},
        "server": {**{k: r[k] for k in ("server_addr", "server_port", "data_directory", "listen_addresses",
                                        "max_connections", "shared_buffers", "wal_level", "in_recovery",
                                        "database", "user", "size", "connections")},
                   "version": r["version"].split(" on ")[0], "started": iso(r["started"], tzi)},
        "replication": [dict(x) for x in repl],
        "slots": [dict(s) for s in slots],
    }


def _check(cid: str, title: str, level: str, summary: str, rows=None, columns=None, hint: str | None = None,
           action: str | None = None) -> dict:
    return {"id": cid, "title": title, "level": level, "summary": summary,
            "columns": columns or [], "rows": rows or [], "hint": hint, "action": action}


def _fmt(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return v


@router.get("/diagnostics", summary="Verificações do banco e da ingestão")
async def diagnostics(_who: auth.KeyInfo = Depends(auth.require("admin"))):
    pool = db.pool()
    t0 = time.monotonic()
    checks: list[dict] = []

    def rows_of(recs, cols):
        return [[_fmt(r[c]) for c in cols] for r in recs]

    # --- Ingestão --------------------------------------------------------------------
    recs = await pool.fetch("SELECT station_id, file_key, relpath, error, last_ingested FROM ingest_files WHERE status = 'error' ORDER BY 1, 2")
    cols = ["station_id", "file_key", "relpath", "error", "last_ingested"]
    checks.append(_check("ingest_errors", "Arquivos com erro na ingestão", "error" if recs else "ok",
                         f"{len(recs)} arquivo(s) com erro" if recs else "nenhum erro registrado no ledger",
                         rows_of(recs, cols), cols,
                         "Corrija o registro (parse, fuso, colunas) na aba Tratamento/Editar e a ingestão tenta de novo no próximo ciclo."))

    recs = await pool.fetch(
        "SELECT station_id, file_key, relpath, size_bytes - bytes_ingested AS pendente, last_ingested "
        "FROM ingest_files WHERE size_bytes - bytes_ingested > 10*1024*1024 ORDER BY 4 DESC")
    cols = ["station_id", "file_key", "relpath", "pendente", "last_ingested"]
    checks.append(_check("ingest_backlog", "Atraso de ingestão (> 10 MB pendentes)", "warn" if recs else "ok",
                         f"{len(recs)} arquivo(s) com mais de 10 MB por ler" if recs else "ingestão em dia",
                         rows_of(recs, cols), cols, "Normal durante um backfill; persistindo, veja os logs da ingestão."))

    recs = await pool.fetch(
        "SELECT station_id, file_key, relpath, bad_timestamps, rows_total FROM ingest_files "
        "WHERE bad_timestamps > 0 AND bad_timestamps > rows_total / 100 ORDER BY 4 DESC")
    cols = ["station_id", "file_key", "relpath", "bad_timestamps", "rows_total"]
    checks.append(_check("bad_timestamps", "Carimbos de tempo não reconhecidos (> 1 % das linhas)", "warn" if recs else "ok",
                         f"{len(recs)} arquivo(s)" if recs else "carimbos reconhecidos",
                         rows_of(recs, cols), cols, "Confira o formato do timestamp do arquivo (aba Estações → Editar → pré-visualizar)."))

    recs = await pool.fetch(
        "SELECT m.station_id, m.file_key, m.path FROM monitored_files m WHERE m.ingest AND NOT EXISTS "
        "(SELECT 1 FROM ingest_files i WHERE i.station_id = m.station_id AND i.file_key = m.file_key)")
    cols = ["station_id", "file_key", "path"]
    checks.append(_check("never_found", "Registros ingeridos sem nenhum arquivo encontrado", "warn" if recs else "ok",
                         f"{len(recs)} registro(s)" if recs else "todos os registros acharam arquivo",
                         rows_of(recs, cols), cols, "O caminho/padrão não casa com nada em incoming/ — confira o registro."))

    # --- Duplicidades e órfãos --------------------------------------------------------
    recs = await pool.fetch(
        "SELECT station_id, relpath, count(*) AS registros, string_agg(file_key, ', ') AS file_keys "
        "FROM ingest_files GROUP BY 1, 2 HAVING count(*) > 1")
    cols = ["station_id", "relpath", "registros", "file_keys"]
    checks.append(_check("dup_files", "Mesmo arquivo ingerido por dois registros", "error" if recs else "ok",
                         f"{len(recs)} arquivo(s) duplicado(s)" if recs else "nenhum arquivo em dois registros",
                         rows_of(recs, cols), cols, "Dois registros lendo o mesmo arquivo gravam as mesmas séries; remova um."))

    recs = await pool.fetch(
        """
        WITH v AS (
          SELECT m.station_id, m.file_key, lower(x->>'label') AS label, x->>'variable_id' AS variable_id
          FROM monitored_files m, jsonb_array_elements(m.variables) x WHERE m.ingest AND (x->>'ingest') IS DISTINCT FROM 'false'
        )
        SELECT station_id, label, count(DISTINCT variable_id) AS ids, string_agg(DISTINCT variable_id, ', ') AS variable_ids,
               string_agg(DISTINCT file_key, ', ') AS arquivos
        FROM v GROUP BY 1, 2 HAVING count(DISTINCT variable_id) > 1 ORDER BY 1, 2
        """)
    cols = ["station_id", "label", "ids", "variable_ids", "arquivos"]
    checks.append(_check("dup_variables", "Mesmo rótulo com IDs diferentes na mesma estação (possível duplicidade)",
                         "warn" if recs else "ok",
                         f"{len(recs)} rótulo(s) repetido(s)" if recs else "sem rótulos repetidos entre arquivos ingeridos",
                         rows_of(recs, cols), cols,
                         "Se é a mesma grandeza medida duas vezes, tire uma do banco na aba Tratamento; se são sensores distintos, dê rótulos distintos."))

    recs = await pool.fetch(
        """
        WITH ativos AS (
          SELECT m.station_id, x->>'variable_id' AS variable_id FROM monitored_files m, jsonb_array_elements(m.variables) x
          WHERE m.ingest AND (x->>'ingest') IS DISTINCT FROM 'false'
        )
        SELECT d.station_id, d.variable_id, sum(d.n_samples + d.n_flagged)::bigint AS valores, max(d.bucket) AS ultimo_dia
        FROM measurements_daily d LEFT JOIN ativos a USING (station_id, variable_id)
        WHERE a.variable_id IS NULL GROUP BY 1, 2 ORDER BY 3 DESC
        """)
    cols = ["station_id", "variable_id", "valores", "ultimo_dia"]
    checks.append(_check("orphan_series", "Séries no banco sem registro ativo (órfãs)", "info" if recs else "ok",
                         f"{len(recs)} série(s) órfã(s)" if recs else "toda série tem um registro ativo",
                         rows_of(recs, cols), cols,
                         "Ficaram de quando a variável/arquivo estava no banco. Histórico útil? Mantenha. Sujeira? Use 'Remover séries órfãs'.",
                         action="purge_orphans" if recs else None))

    recs = await pool.fetch(
        "SELECT s.station_id, s.name FROM stations s WHERE NOT EXISTS (SELECT 1 FROM monitored_files m WHERE m.station_id = s.station_id)")
    cols = ["station_id", "name"]
    checks.append(_check("stations_no_files", "Estações sem nenhum arquivo registrado", "info" if recs else "ok",
                         f"{len(recs)} estação(ões)" if recs else "todas as estações têm registro",
                         rows_of(recs, cols), cols))

    # --- Séries: paradas, lacunas, qualidade -------------------------------------------
    recs = await pool.fetch(
        """
        WITH ult AS (SELECT station_id, variable_id, max(bucket) AS ultimo FROM measurements_daily GROUP BY 1, 2),
             ing AS (SELECT station_id, max(mtime) AS arq FROM ingest_files GROUP BY 1)
        SELECT u.station_id, u.variable_id, u.ultimo, i.arq AS arquivo_modificado
        FROM ult u JOIN ing i USING (station_id)
        WHERE u.ultimo < now() - interval '3 days' AND i.arq > now() - interval '1 day'
        ORDER BY u.ultimo
        """)
    cols = ["station_id", "variable_id", "ultimo", "arquivo_modificado"]
    checks.append(_check("stale_series", "Séries paradas (arquivo ainda cresce, variável não avança há 3 dias)", "warn" if recs else "ok",
                         f"{len(recs)} série(s) parada(s)" if recs else "séries acompanham a ingestão",
                         rows_of(recs, cols), cols, "Sensor desligado, coluna vazia ou variável removida do arquivo pelo datalogger."))

    recs = await pool.fetch(
        """
        WITH d AS (
          SELECT station_id, variable_id, bucket::date AS dia FROM measurements_daily
          WHERE bucket >= now() - interval '90 days' AND n_samples + n_flagged > 0
        ), agg AS (
          SELECT station_id, variable_id, count(*) AS dias_com_dado, min(dia) AS de, max(dia) AS ate,
                 (max(dia) - min(dia) + 1) AS dias_esperados
          FROM d GROUP BY 1, 2
        )
        SELECT station_id, variable_id, de, ate, dias_esperados - dias_com_dado AS dias_sem_dado, dias_esperados
        FROM agg WHERE dias_esperados - dias_com_dado > 0 ORDER BY 5 DESC LIMIT 200
        """)
    cols = ["station_id", "variable_id", "de", "ate", "dias_sem_dado", "dias_esperados"]
    checks.append(_check("gaps", "Lacunas: dias sem dado nos últimos 90 dias", "warn" if recs else "ok",
                         f"{len(recs)} série(s) com dias faltando" if recs else "sem dias faltando",
                         rows_of(recs, cols), cols, "Dias entre o primeiro e o último dado do período em que nada foi gravado."))

    recs = await pool.fetch(
        """
        SELECT station_id, variable_id, sum(n_samples)::bigint AS ok, sum(n_flagged)::bigint AS flag,
               round(100.0 * sum(n_flagged) / NULLIF(sum(n_samples) + sum(n_flagged), 0), 1) AS pct_flag
        FROM measurements_daily WHERE bucket >= now() - interval '30 days'
        GROUP BY 1, 2 HAVING sum(n_flagged) > 0.2 * (sum(n_samples) + sum(n_flagged)) ORDER BY 5 DESC
        """)
    cols = ["station_id", "variable_id", "ok", "flag", "pct_flag"]
    checks.append(_check("quality", "Variáveis com mais de 20 % de valores com flag (30 dias)", "warn" if recs else "ok",
                         f"{len(recs)} variável(is)" if recs else "qualidade dentro do esperado",
                         rows_of(recs, cols), cols, "Faixa física apertada demais, sensor com problema ou regra de salto/travado mal calibrada."))

    recs = await pool.fetch(
        "SELECT station_id, count(*) AS linhas, min(time) AS de, max(time) AS ate FROM measurements "
        "WHERE time < $1 OR time > now() + interval '1 day' GROUP BY 1", PLAUSIBLE_MIN)
    cols = ["station_id", "linhas", "de", "ate"]
    checks.append(_check("implausible_times", "Carimbos implausíveis (antes de 1990 ou no futuro)", "warn" if recs else "ok",
                         f"{sum(r['linhas'] for r in recs)} linha(s) em {len(recs)} estação(ões)" if recs else "todos os carimbos plausíveis",
                         rows_of(recs, cols), cols,
                         "Típico de datalogger sem relógio após reset (1968/1970). Não são medições reais.",
                         action="purge_implausible_times" if recs else None))

    # --- Saúde do PostgreSQL / Timescale --------------------------------------------------
    recs = await pool.fetch(
        "SELECT chunk_name, range_start, range_end FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'measurements' AND NOT is_compressed AND range_end < now() - interval '37 days' ORDER BY range_start")
    cols = ["chunk_name", "range_start", "range_end"]
    checks.append(_check("compression", "Chunks antigos ainda não comprimidos", "warn" if recs else "ok",
                         f"{len(recs)} chunk(s) com mais de 37 dias sem compressão" if recs else "compressão em dia",
                         rows_of(recs, cols), cols, "A política comprime após 30 dias; atraso indica tarefa parada.",
                         action="compress" if recs else None))

    recs = await pool.fetch(
        """
        SELECT relname, n_live_tup, n_dead_tup, round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS pct_dead,
               last_autovacuum, last_analyze, last_autoanalyze
        FROM pg_stat_user_tables WHERE schemaname = 'public' AND n_dead_tup > 1000 AND n_dead_tup > 0.2 * n_live_tup
        """)
    cols = ["relname", "n_live_tup", "n_dead_tup", "pct_dead", "last_autovacuum", "last_analyze", "last_autoanalyze"]
    checks.append(_check("bloat", "Tabelas com muitas linhas mortas (> 20 %)", "warn" if recs else "ok",
                         f"{len(recs)} tabela(s)" if recs else "sem inchaço relevante",
                         rows_of(recs, cols), cols, "Após muitos DELETE/UPDATE; o autovacuum resolve, ou rode VACUUM.",
                         action="vacuum" if recs else None))

    recs = await pool.fetch(
        """
        SELECT j.job_id, j.proc_name, j.hypertable_name, s.last_run_status, s.last_run_started_at, s.total_failures
        FROM timescaledb_information.jobs j JOIN timescaledb_information.job_stats s USING (job_id)
        WHERE j.proc_name NOT LIKE 'policy_telemetry%'
          AND ((s.last_run_status IS NOT NULL AND s.last_run_status <> 'Success') OR s.total_failures > 0)
        """)
    cols = ["job_id", "proc_name", "hypertable_name", "last_run_status", "last_run_started_at", "total_failures"]
    checks.append(_check("jobs", "Tarefas automáticas com falha", "warn" if recs else "ok",
                         f"{len(recs)} tarefa(s)" if recs else "tarefas do TimescaleDB executando",
                         rows_of(recs, cols), cols))

    recs = await pool.fetch(
        "SELECT pid, usename, application_name, state, now() - query_start AS duracao, left(query, 120) AS query "
        "FROM pg_stat_activity WHERE datname = current_database() AND state <> 'idle' AND now() - query_start > interval '60 seconds' "
        "AND pid <> pg_backend_pid()")
    cols = ["pid", "usename", "application_name", "state", "duracao", "query"]
    checks.append(_check("long_queries", "Consultas rodando há mais de 60 s", "warn" if recs else "ok",
                         f"{len(recs)} consulta(s)" if recs else "nenhuma consulta presa",
                         [[str(_fmt(r[c])) for c in cols] for r in recs], cols))

    recs = await pool.fetch(
        "SELECT slot_name, active, wal_status, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retido "
        "FROM pg_replication_slots WHERE NOT active AND pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) > 1073741824")
    cols = ["slot_name", "active", "wal_status", "retido"]
    checks.append(_check("slots", "Slot de replicação inativo retendo WAL (> 1 GB)", "warn" if recs else "ok",
                         f"{len(recs)} slot(s)" if recs else "sem WAL retido por espelho ausente",
                         rows_of(recs, cols), cols, "Um espelho que sumiu deixa o WAL crescer até encher o disco; remova o slot."))

    resumo = {"error": sum(1 for c in checks if c["level"] == "error"),
              "warn": sum(1 for c in checks if c["level"] == "warn"),
              "info": sum(1 for c in checks if c["level"] == "info"),
              "ok": sum(1 for c in checks if c["level"] == "ok")}
    return {"generated_at": iso(datetime.now(timezone.utc), timezone.utc), "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "summary": resumo, "checks": checks}


class MaintIn(BaseModel):
    action: str
    days: int = 30


@router.post("/maintenance", summary="Rotinas de manutenção (nomeadas e explícitas)")
async def maintenance(body: MaintIn, who: auth.KeyInfo = Depends(auth.require("admin"))):
    pool = db.pool()
    t0 = time.monotonic()
    out: dict = {"action": body.action}
    async with pool.acquire() as conn:
        if body.action == "analyze":
            for t in ("measurements", "stations", "variables", "ingest_files", "monitored_files"):
                await conn.execute(f"ANALYZE {t}")
            out["detail"] = "estatísticas do planejador atualizadas (ANALYZE)"
        elif body.action == "vacuum":
            await conn.execute("VACUUM (ANALYZE) measurements")
            for t in ("stations", "variables", "ingest_files", "monitored_files", "api_keys", "pipeline_runs"):
                await conn.execute(f"VACUUM (ANALYZE) {t}")
            out["detail"] = "VACUUM ANALYZE nas tabelas do sistema"
        elif body.action == "compress":
            recs = await conn.fetch(
                "SELECT format('%I.%I', chunk_schema, chunk_name) AS c FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'measurements' AND NOT is_compressed AND range_end < now() - interval '30 days'")
            n = 0
            for r in recs:
                await conn.execute(f"SELECT compress_chunk('{r['c']}'::regclass, if_not_compressed => true)")
                n += 1
            out["detail"] = f"{n} chunk(s) comprimido(s)"
        elif body.action == "refresh_aggregates":
            days = max(1, min(body.days, 3650))
            for v in ("measurements_hourly", "measurements_daily"):
                await conn.execute(f"CALL refresh_continuous_aggregate('{v}', now() - interval '{days} days', now())")
            out["detail"] = f"agregados horário e diário recalculados para os últimos {days} dias"
        elif body.action == "purge_implausible_times":
            res = await conn.execute("DELETE FROM measurements WHERE time < $1 OR time > now() + interval '1 day'", PLAUSIBLE_MIN)
            out["detail"] = f"{res.split()[-1]} linha(s) com carimbo implausível removida(s)"
        elif body.action == "purge_orphans":
            res = await conn.execute(
                """
                WITH ativos AS (
                  SELECT m.station_id, x->>'variable_id' AS variable_id FROM monitored_files m, jsonb_array_elements(m.variables) x
                  WHERE m.ingest AND (x->>'ingest') IS DISTINCT FROM 'false'
                )
                DELETE FROM measurements me WHERE NOT EXISTS (
                  SELECT 1 FROM ativos a WHERE a.station_id = me.station_id AND a.variable_id = me.variable_id)
                """)
            out["detail"] = f"{res.split()[-1]} linha(s) de séries órfãs removida(s)"
        else:
            raise HTTPException(400, "ação desconhecida")
        await conn.execute("INSERT INTO pipeline_runs (pipeline, version, finished, status, stats) VALUES "
                           "('maintenance', $1, now(), 'ok', $2::jsonb)", settings.API_VERSION,
                           '{"action": "%s", "by": "%s"}' % (body.action, who.owner))
    out["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return out
