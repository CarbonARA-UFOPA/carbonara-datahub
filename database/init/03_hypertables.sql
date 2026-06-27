-- 03_hypertables.sql
-- Converte `measurements` em hypertable do TimescaleDB e configura índices,
-- compressão e agregados contínuos. Idempotente.

-- ---------------------------------------------------------------------------
-- Hypertable particionada por tempo (chunks de 7 dias).
-- if_not_exists evita erro em reinicializações; migrate_data move dados já existentes.
-- ---------------------------------------------------------------------------
SELECT create_hypertable(
    'measurements', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- Índice para consultas típicas: "série de uma variável de uma estação no tempo".
CREATE INDEX IF NOT EXISTS idx_measurements_station_var_time
    ON measurements (station_id, variable_id, time DESC);

-- ---------------------------------------------------------------------------
-- Compressão: reduz drasticamente o espaço de dados antigos.
-- Segmenta por estação/variável e ordena por tempo dentro de cada segmento.
-- ---------------------------------------------------------------------------
ALTER TABLE measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'station_id, variable_id',
    timescaledb.compress_orderby   = 'time DESC'
);

-- Comprime automaticamente chunks com mais de 30 dias.
SELECT add_compression_policy('measurements', INTERVAL '30 days', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Agregado contínuo: médias horárias por estação/variável (para dashboards/análise).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS measurements_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    station_id,
    variable_id,
    avg(value)   AS avg_value,
    min(value)   AS min_value,
    max(value)   AS max_value,
    count(*)     AS n_samples
FROM measurements
GROUP BY bucket, station_id, variable_id
WITH NO DATA;

-- Mantém o agregado atualizado: recalcula a janela de [3h, 1h] atrás a cada 1h.
SELECT add_continuous_aggregate_policy('measurements_hourly',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE);
