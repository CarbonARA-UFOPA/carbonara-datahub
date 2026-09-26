#!/usr/bin/env bash
# setup-db-host.sh — prepara ESTE servidor para rodar o banco de dados do CARBONARA
# (PostgreSQL 16 + TimescaleDB, em Docker) no DISCO DE DADOS.
# É a "fase 0" de docs/estrategia-dados.md.
#
# O que ele faz (rode UMA vez, como root, no servidor do hub; pode repetir sem dano):
#   1. confere o disco de dados (montado, sistema de arquivos com permissões Unix,
#      espaço livre) e avisa sobre o disco root, que está quase cheio;
#   2. instala o Docker Engine + plugin compose (repositório oficial; se falhar, usa os
#      pacotes do Ubuntu) com o `data-root` JÁ apontado para o disco de dados — imagens
#      e containers nunca vão para o disco root. Se o Docker já existir com dados em
#      /var/lib/docker, migra-os (copia, troca, guarda o antigo renomeado);
#   3. cria o diretório do banco no disco de dados (<disco>/carbonara-db/pgdata);
#   4. adiciona o usuário que chamou o sudo ao grupo docker;
#   5. gera o .env do compose (senhas aleatórias) se ainda não existir — NUNCA
#      sobrescreve um .env existente, só completa chaves que faltem;
#   6. limita o journald (protege o disco root) e, se o ufw estiver ativo, libera a
#      porta da API (a liberação no firewall EXTERNO é com o STI);
#   7. com --subir, sobe o serviço `db`, espera ficar saudável e confere wal_level.
#
# Uso:
#   sudo ./database/setup-db-host.sh [--disco /mnt/data_8tb] [--porta-api 8000] [--subir]
#
# Não mexe em NADA da coleta (rsync-hub), da dashboard nem do FTP.

set -euo pipefail

die()  { printf 'ERRO: %s\n' "$*" >&2; exit 1; }
ok()   { printf 'ok: %s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
sec()  { printf '\n--- %s ---\n' "$*"; }

[ "$(id -u)" = 0 ] || die "execute como root: sudo $0"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
ADMIN="${SUDO_USER:-}"
[ "$ADMIN" = root ] && ADMIN=""

DISK=/mnt/data_8tb
API_PORT=8000
SUBIR=0
while [ $# -gt 0 ]; do
    case "$1" in
        --disco)     DISK="${2:?--disco precisa de um caminho}"; shift 2 ;;
        --porta-api) API_PORT="${2:?--porta-api precisa de um número}"; shift 2 ;;
        --subir)     SUBIR=1; shift ;;
        -h|--help)   sed -n '2,24p' "$0"; exit 0 ;;
        *) die "argumento desconhecido: $1 (veja --help)" ;;
    esac
done
DISK="${DISK%/}"
DOCKER_ROOT="$DISK/docker"
PGDATA_HOST="$DISK/carbonara-db/pgdata"

echo "===== CARBONARA — preparação do servidor para o banco de dados (fase 0) ====="
echo "repositório: $REPO"
echo "disco de dados: $DISK"
[ -n "$ADMIN" ] && echo "usuário administrador: $ADMIN"

# --- 1) disco de dados e disco root --------------------------------------------------
sec "1) Discos"
mountpoint -q "$DISK" || die "$DISK não é um ponto de montagem. Monte o disco de dados (ou use --disco)."
FSTYPE="$(findmnt -no FSTYPE "$DISK")"
case "$FSTYPE" in
    vfat|exfat|ntfs|fuseblk|msdos)
        die "$DISK está em $FSTYPE, que não guarda dono/permissões Unix — o PostgreSQL se recusa a iniciar nele." ;;
esac
AVAIL_G="$(df -BG --output=avail "$DISK" | tail -1 | tr -dc '0-9')"
[ "${AVAIL_G:-0}" -ge 50 ] || die "só ${AVAIL_G:-0} GB livres em $DISK; o banco precisa de folga (mínimo 50 GB)."
ok "$DISK montado ($FSTYPE), ${AVAIL_G} GB livres"

ROOT_PCT="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
if [ "${ROOT_PCT:-0}" -ge 80 ]; then
    warn "disco root a ${ROOT_PCT}% — por isso o Docker e o banco vão para $DISK. Nada deste script grava dados no root."
else
    ok "disco root a ${ROOT_PCT}%"
fi

MEM_G="$(free -g | awk '/^Mem/{print $2}')"
[ "${MEM_G:-0}" -ge 6 ] || warn "só ${MEM_G} GB de RAM: mantenha os limites de memória do docker-compose.yml."

BASE=""
[ -r /etc/rsync-hub.conf ] && BASE="$(sed -n 's/^BASE=//p' /etc/rsync-hub.conf | tr -d '"' | tail -1)"
[ -n "$BASE" ] || BASE="$DISK/carbonara-data-hub"
if [ -d "$BASE" ]; then
    ok "raiz do hub (CARBONARA_ROOT): $BASE"
    case "$BASE" in "$DISK"/*) ;; *) warn "a raiz do hub ($BASE) NÃO está em $DISK." ;; esac
else
    warn "raiz do hub $BASE não existe ainda (o rsync-hub install cria); o .env vai apontar para ela mesmo assim."
fi

# --- 2) Docker com data-root no disco de dados ---------------------------------------
sec "2) Docker"
write_daemon_json() {
    install -d -m 755 /etc/docker
    if [ -f /etc/docker/daemon.json ] && grep -q "\"data-root\": *\"$DOCKER_ROOT\"" /etc/docker/daemon.json; then
        ok "/etc/docker/daemon.json já aponta data-root para $DOCKER_ROOT"
        return 0
    fi
    [ -f /etc/docker/daemon.json ] && cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.bak-$(date +%Y%m%d%H%M%S)"
    cat > /etc/docker/daemon.json <<EOF
{
  "data-root": "$DOCKER_ROOT",
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
EOF
    ok "/etc/docker/daemon.json gravado (data-root=$DOCKER_ROOT, logs rotacionados)"
}

install_docker_oficial() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg >/dev/null
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
        "$(dpkg --print-architecture)" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
}

install_docker_ubuntu() {
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-v2 >/dev/null
}

install -d -m 711 "$DOCKER_ROOT"

if command -v docker >/dev/null 2>&1 && systemctl is-active --quiet docker; then
    CUR_ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
    if [ "$CUR_ROOT" = "$DOCKER_ROOT" ]; then
        ok "Docker já instalado e já usa $DOCKER_ROOT"
    else
        warn "Docker já instalado com data-root em ${CUR_ROOT:-/var/lib/docker}; migrando para $DOCKER_ROOT."
        systemctl stop docker.socket docker.service 2>/dev/null || true
        if [ -d "${CUR_ROOT:-/var/lib/docker}" ] && [ -n "$(ls -A "${CUR_ROOT:-/var/lib/docker}" 2>/dev/null)" ]; then
            command -v rsync >/dev/null || apt-get install -y -qq rsync >/dev/null
            rsync -aHAX "${CUR_ROOT:-/var/lib/docker}/" "$DOCKER_ROOT/"
            mv "${CUR_ROOT:-/var/lib/docker}" "${CUR_ROOT:-/var/lib/docker}.migrado-$(date +%Y%m%d)"
            ok "conteúdo copiado; o antigo ficou em ${CUR_ROOT:-/var/lib/docker}.migrado-* (apague quando conferir)"
        fi
        write_daemon_json
        systemctl start docker
    fi
else
    write_daemon_json          # ANTES do primeiro start: o daemon já nasce no disco de dados
    if command -v docker >/dev/null 2>&1; then
        ok "Docker instalado mas parado; iniciando"
    elif install_docker_oficial; then
        ok "Docker Engine instalado (repositório oficial)"
    else
        warn "repositório oficial indisponível; instalando docker.io do Ubuntu"
        install_docker_ubuntu
        ok "Docker instalado (pacotes do Ubuntu)"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || systemctl start docker
fi

CUR_ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
[ "$CUR_ROOT" = "$DOCKER_ROOT" ] || die "o Docker está usando '$CUR_ROOT' em vez de $DOCKER_ROOT — confira /etc/docker/daemon.json e 'journalctl -u docker'."
docker compose version >/dev/null 2>&1 || die "plugin 'docker compose' não encontrado."
ok "docker $(docker version -f '{{.Server.Version}}' 2>/dev/null), $(docker compose version --short 2>/dev/null | sed 's/^/compose /')"

# --- 3) diretório do banco -----------------------------------------------------------
sec "3) Diretório do banco"
# A imagem timescale/timescaledb-ha roda o PostgreSQL como o usuário 'postgres' de uid
# 1000 DENTRO do container; o diretório precisa pertencer a esse uid. Que o uid 1000 do
# host seja o primeiro usuário da máquina é coincidência numérica, não uma concessão.
install -d -m 700 -o 1000 -g 1000 "$PGDATA_HOST"
install -d -m 755 "$DISK/carbonara-db"
ok "$PGDATA_HOST (700, uid 1000)"
if [ -n "$(ls -A "$PGDATA_HOST" 2>/dev/null)" ]; then
    ok "já contém um cluster — o init SQL NÃO roda de novo (só roda com o diretório vazio)"
fi

# --- 4) grupo docker -----------------------------------------------------------------
sec "4) Grupo docker"
if [ -n "$ADMIN" ]; then
    if id -nG "$ADMIN" | tr ' ' '\n' | grep -qx docker; then
        ok "$ADMIN já está no grupo docker"
    else
        usermod -aG docker "$ADMIN"
        ok "$ADMIN adicionado ao grupo docker (vale a partir do PRÓXIMO login, ou 'newgrp docker')"
    fi
else
    warn "não sei quem chamou o sudo; adicione seu usuário manualmente: usermod -aG docker <usuario>"
fi

# --- 5) .env do compose --------------------------------------------------------------
sec "5) .env"
ENV_FILE="$REPO/.env"
gen_secret() { local s; s="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"; printf '%s' "${s:0:32}"; }
ensure_key() {   # ensure_key CHAVE VALOR — acrescenta só se a chave não existir
    if grep -q "^$1=" "$ENV_FILE"; then return 0; fi
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    printf '   + %s\n' "$1"
}
if [ ! -f "$ENV_FILE" ]; then
    : > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "gerando $ENV_FILE:"
else
    echo "$ENV_FILE já existe — mantido; completando chaves que faltem:"
fi
ensure_key CARBONARA_ROOT "$BASE"
ensure_key PG_DATA_DIR "$PGDATA_HOST"
ensure_key DB_USER carbonara
ensure_key DB_PASSWORD "$(gen_secret)"
ensure_key DB_NAME carbonara
ensure_key DB_BIND 127.0.0.1
ensure_key DB_PORT 5432
ensure_key REPLICATION_PASSWORD "$(gen_secret)"
ensure_key API_PORT "$API_PORT"
ensure_key API_KEY_PEPPER "$(gen_secret)"
ensure_key INGEST_POLL_SECONDS 30
[ -n "$ADMIN" ] && chown "$ADMIN:$ADMIN" "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok ".env pronto (600). Senhas geradas ficam SÓ aí — guarde uma cópia em local seguro."

# --- 6) disco root e firewall --------------------------------------------------------
sec "6) Proteções"
install -d -m 755 /etc/systemd/journald.conf.d
if [ ! -f /etc/systemd/journald.conf.d/carbonara.conf ]; then
    printf '[Journal]\nSystemMaxUse=1G\n' > /etc/systemd/journald.conf.d/carbonara.conf
    systemctl restart systemd-journald 2>/dev/null || true
    ok "journald limitado a 1 GB"
else
    ok "journald já limitado"
fi
apt-get clean >/dev/null 2>&1 || true

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow "$API_PORT/tcp" comment 'CARBONARA API' >/dev/null && ok "ufw: porta $API_PORT/tcp liberada"
else
    ok "ufw inativo ou ausente — nada a fazer no firewall local"
fi

# --- 7) subir o banco (opcional) -----------------------------------------------------
if [ "$SUBIR" = 1 ]; then
    sec "7) Subindo o serviço db"
    cd "$REPO"
    docker compose pull db
    docker compose up -d db
    printf 'aguardando o banco ficar saudável'
    for _ in $(seq 1 60); do
        st="$(docker inspect -f '{{.State.Health.Status}}' carbonara-db 2>/dev/null || echo none)"
        [ "$st" = healthy ] && break
        printf '.'; sleep 2
    done
    echo
    [ "$st" = healthy ] || { docker compose logs --tail 40 db; die "o banco não ficou saudável; veja os logs acima."; }
    DB_USER_V="$(sed -n 's/^DB_USER=//p' "$ENV_FILE")"; DB_NAME_V="$(sed -n 's/^DB_NAME=//p' "$ENV_FILE")"
    docker exec carbonara-db psql -U "$DB_USER_V" -d "$DB_NAME_V" -Atc \
        "select 'wal_level='||current_setting('wal_level'); select 'extensao: '||extname||' '||extversion from pg_extension order by 1; select 'replicator: '||count(*) from pg_roles where rolname='replicator';"
    ok "banco no ar (escutando só em $(sed -n 's/^DB_BIND=//p' "$ENV_FILE"):$(sed -n 's/^DB_PORT=//p' "$ENV_FILE"))"
fi

# --- resumo --------------------------------------------------------------------------
cat <<EOF

===== Pronto =====
Docker data-root : $DOCKER_ROOT
Banco (PGDATA)   : $PGDATA_HOST
Raiz do hub      : $BASE
.env             : $ENV_FILE

Próximos passos:
  1. ${ADMIN:+saia e entre de novo (grupo docker), depois }cd $REPO
  2. docker compose up -d db && docker compose ps
  3. docker exec -it carbonara-db psql -U carbonara -d carbonara -c '\dt'
  4. Pedir ao STI a liberação de ENTRADA da porta $API_PORT/TCP neste servidor (API de dados).
     O PostgreSQL (5432) fica só em 127.0.0.1 — não precisa de liberação.
  5. A ingestão NÃO sobe por padrão (perfil 'ingestao') até a fase 1 — ver docs/estrategia-dados.md.
EOF
