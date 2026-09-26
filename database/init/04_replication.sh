#!/bin/bash
# 04_replication.sh — deixa o banco PRONTO para ser espelhado (streaming replication).
# Executado pelo container no PRIMEIRO start (diretório /docker-entrypoint-initdb.d/),
# depois dos .sql. Cria o papel `replicator` e libera, no pg_hba.conf, conexões de
# replicação autenticadas por senha (scram-sha-256).
#
# Os parâmetros do servidor (wal_level=replica, max_wal_senders, slots, wal_keep_size)
# vêm do `command:` do docker-compose.yml — não daqui.
#
# A porta 5432 fica presa em 127.0.0.1 no host (DB_BIND) até o espelho existir; nessa
# hora basta liberar a porta para o IP do espelho e rodar, nele:
#   pg_basebackup -h <este-host> -U replicator -D <pgdata> -R -X stream -C -S espelho1
set -e

if [ -z "${REPLICATION_PASSWORD:-}" ]; then
    echo "04_replication: REPLICATION_PASSWORD não definido — papel 'replicator' NÃO criado."
    exit 0
fi

# Escapa aspas simples na senha (a senha gerada pelo setup é alfanumérica, mas o .env é editável).
SENHA="${REPLICATION_PASSWORD//\'/\'\'}"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') THEN
        EXECUTE format('CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD %L', '${SENHA}');
    END IF;
END
\$\$;
SQL

HBA="${PGDATA:-/home/postgres/pgdata/data}/pg_hba.conf"
if [ -w "$HBA" ] && ! grep -q 'carbonara-replica' "$HBA"; then
    {
        echo '# carbonara-replica: espelho do banco (streaming replication), senha scram'
        echo 'host    replication     replicator      0.0.0.0/0               scram-sha-256'
    } >> "$HBA"
    echo "04_replication: pg_hba.conf liberado para o papel replicator."
fi
echo "04_replication: papel 'replicator' pronto."
