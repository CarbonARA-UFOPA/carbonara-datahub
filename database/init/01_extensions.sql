-- 01_extensions.sql
-- Habilita as extensões necessárias. Executado automaticamente pelo container
-- TimescaleDB no PRIMEIRO start (diretório /docker-entrypoint-initdb.d/), em ordem alfabética.
-- Idempotente: pode ser re-executado sem erro.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Útil para gerar/validar identificadores; opcional.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
