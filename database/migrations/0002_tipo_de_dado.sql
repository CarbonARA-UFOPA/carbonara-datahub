-- 0002_tipo_de_dado.sql — nível "tipo de dado" entre a estação e o arquivo monitorado.
-- monitored_files.data_type (ex.: datalogger, fluxo, radiacao, perfil): a ingestão o
-- copia do registro da dashboard e cria as views em schemas <estacao>__<tipo>.
ALTER TABLE monitored_files ADD COLUMN IF NOT EXISTS data_type TEXT NOT NULL DEFAULT 'dados';
COMMENT ON COLUMN monitored_files.data_type IS 'Tipo de dado (equipamento/conjunto) do arquivo; schema no banco = <station_id>__<data_type>.';
