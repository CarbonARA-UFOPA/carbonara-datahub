-- 0004_indices_e_compressao.sql — menos índices na hypertable e compressão mais cedo.
--
-- measurements tinha 4 índices (chave natural (time, station, variable), (station,
-- variable, time DESC), (variable, time DESC) e o de time da hypertable): ~8,5 GB para
-- 12 M linhas. A chave natural passa a ser (station_id, variable_id, time) — o
-- ON CONFLICT da ingestão continua válido (a inferência é pelo CONJUNTO de colunas) e
-- esse índice já serve as consultas por estação+variável; os dois redundantes saem.
-- Compressão a partir de 14 dias (era 30): os chunks recentes ficam ~10× menores.
-- Idempotente; reexecutável.
CREATE UNIQUE INDEX IF NOT EXISTS measurements_station_var_time_key
    ON measurements (station_id, variable_id, time);
ALTER TABLE measurements DROP CONSTRAINT IF EXISTS measurements_natural_key;
DROP INDEX IF EXISTS idx_measurements_station_var_time;
DROP INDEX IF EXISTS idx_measurements_var_time;
SELECT remove_compression_policy('measurements', if_exists => TRUE);
SELECT add_compression_policy('measurements', INTERVAL '14 days', if_not_exists => TRUE);
ANALYZE measurements;
