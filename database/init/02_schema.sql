-- 02_schema.sql
-- Schema relacional do CARBONARA. Modelo "long/EAV" para as medições: novas variáveis
-- (temperatura, umidade, CO2, etc.) entram como linhas no catálogo `variables`, SEM ALTER TABLE.
-- Idempotente (CREATE ... IF NOT EXISTS).

-- ---------------------------------------------------------------------------
-- Estações: metadados de cada ponto de coleta (fixo ou móvel/"roving").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stations (
    station_id    TEXT PRIMARY KEY,                 -- ex.: 'flona_tapajos_primary_01'
    name          TEXT NOT NULL,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    elevation_m   DOUBLE PRECISION,
    forest_type   TEXT,                             -- 'primary' | 'secondary' | 'roving' | ...
    is_mobile     BOOLEAN NOT NULL DEFAULT FALSE,   -- true para medições móveis/roving
    installed_at  TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE stations IS 'Metadados das estações meteorológicas do CARBONARA.';
COMMENT ON COLUMN stations.forest_type IS 'Tipo de cobertura: primary, secondary, roving, etc.';

-- ---------------------------------------------------------------------------
-- Catálogo de variáveis medidas. Adicionar uma grandeza = INSERT aqui.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS variables (
    variable_id   TEXT PRIMARY KEY,                 -- ex.: 'air_temp', 'rel_humidity', 'co2'
    name          TEXT NOT NULL,                    -- nome legível
    unit          TEXT,                             -- ex.: 'degC', '%', 'ppm'
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE variables IS 'Catálogo de grandezas medidas. Novas variáveis entram sem ALTER.';

-- ---------------------------------------------------------------------------
-- Medições: tabela de fatos (vira hypertable em 03_hypertables.sql).
-- Chave natural (time, station_id, variable_id) impede duplicação na reingestão.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS measurements (
    time          TIMESTAMPTZ      NOT NULL,
    station_id    TEXT             NOT NULL REFERENCES stations(station_id),
    variable_id   TEXT             NOT NULL REFERENCES variables(variable_id),
    value         DOUBLE PRECISION,
    quality       SMALLINT         NOT NULL DEFAULT 0,   -- 0=ok, >0 = flags de QA/QC
    source_file   TEXT,                                  -- arquivo de origem (auditoria)
    ingested_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    CONSTRAINT measurements_natural_key UNIQUE (time, station_id, variable_id)
);

COMMENT ON TABLE measurements IS 'Série temporal de medições (modelo long). Hypertable particionada por time.';
COMMENT ON COLUMN measurements.quality IS '0=ok; valores >0 reservados para flags de QA/QC.';

-- ---------------------------------------------------------------------------
-- Seed de variáveis meteorológicas comuns (idempotente). Ajuste conforme os sensores reais.
-- ---------------------------------------------------------------------------
INSERT INTO variables (variable_id, name, unit, description) VALUES
    ('air_temp',     'Temperatura do ar',        'degC', 'Temperatura do ar a 2m'),
    ('rel_humidity', 'Umidade relativa',         '%',    'Umidade relativa do ar'),
    ('pressure',     'Pressão atmosférica',      'hPa',  'Pressão barométrica'),
    ('wind_speed',   'Velocidade do vento',      'm/s',  'Velocidade média do vento'),
    ('wind_dir',     'Direção do vento',         'deg',  'Direção do vento (0-360)'),
    ('rainfall',     'Precipitação',             'mm',   'Acumulado de chuva no intervalo'),
    ('solar_rad',    'Radiação solar',           'W/m2', 'Radiação solar incidente'),
    ('co2',          'Concentração de CO2',      'ppm',  'Dióxido de carbono'),
    ('soil_temp',    'Temperatura do solo',      'degC', 'Temperatura do solo'),
    ('soil_moisture','Umidade do solo',          '%',    'Conteúdo volumétrico de água no solo')
ON CONFLICT (variable_id) DO NOTHING;
