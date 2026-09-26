#!/usr/bin/env bash
# migrate.sh — aplica as migrações de database/migrations/*.sql num banco JÁ EXISTENTE
# (os scripts de database/init/ só rodam no primeiro start, com o cluster vazio).
#
# Controle em `schema_migrations` (nome do arquivo + data): cada migração roda uma
# vez, em ordem alfabética; os próprios .sql são idempotentes por escrito.
#
# Uso (na raiz do repositório, com o serviço db no ar):
#   ./database/migrate.sh            # aplica as pendentes
#   ./database/migrate.sh --status   # só lista o que está aplicado/pendente
#
# Lê DB_USER/DB_NAME do .env; fala com o container via `docker compose exec db psql`
# (não precisa de cliente psql no host nem da senha: dentro do container o acesso é local).
set -euo pipefail

die() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
[ -f .env ] || die ".env não encontrado em $REPO (rode sudo ./database/setup-db-host.sh)."
DB_USER="$(sed -n 's/^DB_USER=//p' .env | tail -1)"
DB_NAME="$(sed -n 's/^DB_NAME=//p' .env | tail -1)"
DB_USER="${DB_USER:-carbonara}"; DB_NAME="${DB_NAME:-carbonara}"

psql_() { docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U "$DB_USER" -d "$DB_NAME" "$@"; }

docker compose ps --status running --services 2>/dev/null | grep -qx db \
    || die "o serviço db não está rodando (docker compose up -d db)."

psql_ -c "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());" >/dev/null

aplicadas="$(psql_ -Atc "SELECT name FROM schema_migrations ORDER BY name;")"
pendentes=0
for f in "$HERE"/migrations/*.sql; do
    [ -e "$f" ] || break
    nome="$(basename "$f")"
    if printf '%s\n' "$aplicadas" | grep -qx "$nome"; then
        printf 'ok  %s (já aplicada)\n' "$nome"
        continue
    fi
    if [ "${1:-}" = "--status" ]; then
        printf '..  %s (pendente)\n' "$nome"
        pendentes=$((pendentes + 1))
        continue
    fi
    printf '>>  %s … ' "$nome"
    # Sem -1 (transação única): CREATE MATERIALIZED VIEW ... continuous não roda dentro
    # de transação. Cada .sql é idempotente, então uma falha no meio é reexecutável.
    psql_ < "$f" >/dev/null
    psql_ -c "INSERT INTO schema_migrations (name) VALUES ('$nome') ON CONFLICT DO NOTHING;" >/dev/null
    echo "aplicada"
done
[ "${1:-}" = "--status" ] && printf '%d pendente(s)\n' "$pendentes"
exit 0
