#!/bin/bash
# 05_migrations.sh — no PRIMEIRO start (cluster vazio), aplica também as migrações de
# database/migrations/ (montado em /migrations pelo docker-compose.yml), registrando-as
# em schema_migrations — assim um banco novo e um banco migrado ficam idênticos, e
# database/migrate.sh não tenta reaplicá-las.
set -e
DIR=/migrations
[ -d "$DIR" ] || { echo "05_migrations: $DIR não montado — nada a aplicar."; exit 0; }
psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());"
for f in "$DIR"/*.sql; do
    [ -e "$f" ] || break
    nome="$(basename "$f")"
    echo "05_migrations: aplicando $nome"
    psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$f"
    psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "INSERT INTO schema_migrations (name) VALUES ('$nome') ON CONFLICT DO NOTHING;"
done
