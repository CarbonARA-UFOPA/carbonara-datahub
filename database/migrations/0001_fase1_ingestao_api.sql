-- 0001_fase1_ingestao_api.sql — fase 1 de docs/estrategia-dados.md.
-- Aplicado por database/migrate.sh (banco existente) ou por database/init/05_migrations.sh
-- (primeiro start). Idempotente: pode rodar de novo sem erro.
--
-- O que muda:
--   * PostGIS + geometria das estações (derivada de latitude/longitude);
--   * monitored_files  — espelho do registro "arquivo monitorado" da dashboard;
--   * ingest_files     — ledger da ingestão incremental (offset por arquivo real);
--   * measurements.file_id — proveniência (qual leitura de qual arquivo gerou a linha);
--   * pipeline_runs    — execuções do serviço de ingestão / reprocessamentos;
--   * api_keys         — chaves de acesso da API (só o hash é guardado);
--   * agregados contínuos horário (recriado) e diário, em tempo real e só com quality=0.

-- ---------------------------------------------------------------------------
-- Geo: PostGIS já vem na imagem timescaledb-ha. stations.geom é GERADA de lat/lon —
-- não há segunda fonte de verdade para a posição da estação.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS postgis;

ALTER TABLE stations ADD COLUMN IF NOT EXISTS timezone TEXT;
COMMENT ON COLUMN stations.timezone IS 'Fuso padrão dos carimbos desta estação (IANA); cada arquivo pode sobrepor.';

ALTER TABLE stations ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326)
    GENERATED ALWAYS AS (
        CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
             THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) END
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_stations_geom ON stations USING gist (geom);

-- ---------------------------------------------------------------------------
-- Arquivos monitorados: o registro feito na dashboard (config.yaml) é copiado aqui
-- pela ingestão a cada ciclo, para que a API e consultas SQL saibam O QUE está sendo
-- ingerido sem ler o YAML. A fonte editável continua sendo a dashboard.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monitored_files (
    station_id     TEXT        NOT NULL REFERENCES stations(station_id) ON DELETE CASCADE,
    file_key       TEXT        NOT NULL,                 -- `id` do arquivo no config da dashboard
    path           TEXT        NOT NULL,                 -- caminho/padrão registrado
    alias          TEXT,
    dynamic_path   BOOLEAN     NOT NULL DEFAULT FALSE,
    timezone       TEXT,                                 -- fuso dos carimbos do arquivo (IANA)
    resample       TEXT        NOT NULL DEFAULT 'raw',   -- raw | 1min | 30min
    ingest         BOOLEAN     NOT NULL DEFAULT FALSE,
    parser_config  JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- separador, cabeçalho, timestamp…
    variables      JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- [{variable_id, label, unit, min, max, type, column}]
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, file_key)
);
COMMENT ON TABLE monitored_files IS 'Espelho do registro de arquivos monitorados da dashboard (fonte: config.yaml).';

-- ---------------------------------------------------------------------------
-- Ledger da ingestão incremental: um registro por ARQUIVO REAL lido (um registro
-- dinâmico como %Y-%m-%d__*.sfo gera um por dia). bytes_ingested é o offset a partir
-- do qual a próxima leitura continua; head_sha256 detecta rotação/recriação.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_files (
    file_id         SERIAL      PRIMARY KEY,
    station_id      TEXT        NOT NULL REFERENCES stations(station_id) ON DELETE CASCADE,
    file_key        TEXT        NOT NULL,                -- registro (monitored_files.file_key)
    relpath         TEXT        NOT NULL,                -- caminho absoluto do arquivo lido
    size_bytes      BIGINT      NOT NULL DEFAULT 0,
    mtime           TIMESTAMPTZ,
    head_sha256     TEXT,
    bytes_ingested  BIGINT      NOT NULL DEFAULT 0,
    rows_total      BIGINT      NOT NULL DEFAULT 0,      -- registros lidos (acumulado)
    values_total    BIGINT      NOT NULL DEFAULT 0,      -- valores inseridos (acumulado)
    bad_timestamps  BIGINT      NOT NULL DEFAULT 0,      -- linhas cujo carimbo não casou
    last_time       TIMESTAMPTZ,                         -- último carimbo ingerido (UTC)
    status          TEXT        NOT NULL DEFAULT 'ok',   -- ok | error
    error           TEXT,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_ingested   TIMESTAMPTZ,
    UNIQUE (station_id, relpath)
);
CREATE INDEX IF NOT EXISTS idx_ingest_files_station_key ON ingest_files (station_id, file_key);
COMMENT ON TABLE ingest_files IS 'Ledger da ingestão incremental (offset e estado por arquivo real).';

ALTER TABLE measurements ADD COLUMN IF NOT EXISTS file_id INTEGER;
COMMENT ON COLUMN measurements.file_id IS 'ingest_files.file_id que gerou a linha (proveniência).';
CREATE INDEX IF NOT EXISTS idx_measurements_var_time ON measurements (variable_id, time DESC);

-- ---------------------------------------------------------------------------
-- Execuções do pipeline (início do serviço, reprocessamentos).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id      SERIAL      PRIMARY KEY,
    pipeline    TEXT        NOT NULL,                    -- 'ingestion' | 'reprocess'
    version     TEXT,
    started     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished    TIMESTAMPTZ,
    status      TEXT        NOT NULL DEFAULT 'running',  -- running | ok | error
    stats       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- Chaves da API: só o hash (sha256 com "pepper" do .env) fica no banco; a chave em
-- claro é mostrada UMA vez, na criação. prefix identifica a chave em listagens.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    key_id       SERIAL      PRIMARY KEY,
    key_hash     TEXT        NOT NULL UNIQUE,
    prefix       TEXT        NOT NULL,                   -- 8 primeiros caracteres da chave
    owner        TEXT        NOT NULL,                   -- quem usa (pessoa/serviço)
    scopes       TEXT[]      NOT NULL DEFAULT '{read}',  -- read | download | admin
    rate_limit   INTEGER     NOT NULL DEFAULT 120,       -- requisições por minuto
    expires_at   TIMESTAMPTZ,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);
COMMENT ON TABLE api_keys IS 'Chaves de acesso da API de dados (hash + escopos + validade).';

-- ---------------------------------------------------------------------------
-- Agregados contínuos: só valores aprovados (quality = 0) entram nas médias — a API
-- serve agregados "limpos" sem recalcular; n_flagged diz quanto ficou de fora.
-- materialized_only = false: a parte ainda não materializada é calculada na hora,
-- então a última hora/dia também aparece.
-- O horário do init (03_hypertables.sql) é recriado com essa definição.
-- ---------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS measurements_hourly;
CREATE MATERIALIZED VIEW measurements_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    station_id,
    variable_id,
    avg(value)   FILTER (WHERE quality = 0) AS avg_value,
    min(value)   FILTER (WHERE quality = 0) AS min_value,
    max(value)   FILTER (WHERE quality = 0) AS max_value,
    count(value) FILTER (WHERE quality = 0) AS n_samples,
    count(*)     FILTER (WHERE quality <> 0) AS n_flagged
FROM measurements
GROUP BY bucket, station_id, variable_id
WITH NO DATA;
ALTER MATERIALIZED VIEW measurements_hourly SET (timescaledb.materialized_only = false);
SELECT add_continuous_aggregate_policy('measurements_hourly',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS measurements_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    station_id,
    variable_id,
    avg(value)   FILTER (WHERE quality = 0) AS avg_value,
    min(value)   FILTER (WHERE quality = 0) AS min_value,
    max(value)   FILTER (WHERE quality = 0) AS max_value,
    count(value) FILTER (WHERE quality = 0) AS n_samples,
    count(*)     FILTER (WHERE quality <> 0) AS n_flagged
FROM measurements
GROUP BY bucket, station_id, variable_id
WITH NO DATA;
ALTER MATERIALIZED VIEW measurements_daily SET (timescaledb.materialized_only = false);
SELECT add_continuous_aggregate_policy('measurements_daily',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE);
