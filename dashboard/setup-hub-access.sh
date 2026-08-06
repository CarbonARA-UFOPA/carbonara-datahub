#!/usr/bin/env bash
# setup-hub-access.sh — prepara ESTE servidor para a dashboard administrar a coleta.
#
# O que ele faz (rode UMA vez, como root, no servidor do hub):
#   1. instala/atualiza o rsync-hub em /usr/local/bin (a partir do repositório);
#   2. grava em /etc/sudoers.d/carbonara-dashboard uma regra NOPASSWD restrita aos
#      subcomandos de gerência (add / remove / move-root) para o usuário que RODA a
#      dashboard — é o que a página "Coleta" usa via `sudo -n`;
#   3. valida a regra com visudo -cf ANTES de instalar (uma regra quebrada em
#      sudoers.d pode travar o sudo do servidor inteiro — nunca gravamos sem validar);
#   4. verifica que o usuário consegue LER a raiz do hub (listagem/downloads).
#
# Uso:
#   sudo ./dashboard/setup-hub-access.sh [usuario-da-dashboard]
#   (sem argumento, pergunta — sugerindo quem chamou o sudo)
#
# A LEITURA não precisa de nada disto: a política do hub já deixa dados e
# credenciais legíveis pelos usuários locais. Este script só habilita a ESCRITA.
set -eu

die() { echo "erro: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "execute como root: sudo $0 [usuario-da-dashboard]"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_HUB="$HERE/../collection/rsync-hub"
BIN=/usr/local/bin/rsync-hub

echo "===== Acesso da dashboard ao rsync-hub — configuração do servidor ====="

# --- 1) rsync-hub instalado e atualizado ------------------------------------------
if [ -f "$REPO_HUB" ]; then
    if [ ! -f "$BIN" ] || ! cmp -s "$REPO_HUB" "$BIN"; then
        install -m 755 "$REPO_HUB" "$BIN"
        echo "ok: rsync-hub instalado/atualizado em $BIN (cópia do repositório)."
    else
        echo "ok: $BIN já está igual ao do repositório."
    fi
else
    [ -f "$BIN" ] || die "rsync-hub não encontrado (nem em $REPO_HUB, nem em $BIN) — rode o script de dentro do repositório, ou instale o hub antes"
    echo "aviso: repositório sem collection/rsync-hub — mantendo o $BIN existente."
fi
grep -q "move-root" "$BIN" || \
    die "o $BIN instalado é antigo (sem 'move-root') — atualize-o a partir do repositório"

# --- 2) usuário que roda a dashboard ----------------------------------------------
DASH_USER="${1:-}"
if [ -z "$DASH_USER" ]; then
    default="${SUDO_USER:-}"
    if [ -n "$default" ]; then
        printf "Usuário que RODA a dashboard (dono do processo run.py) [%s]: " "$default"
    else
        printf "Usuário que RODA a dashboard (dono do processo run.py): "
    fi
    read -r DASH_USER || DASH_USER=""
    DASH_USER="${DASH_USER:-$default}"
fi
[ -n "$DASH_USER" ] || die "informe o usuário: sudo $0 <usuario-da-dashboard>"
id "$DASH_USER" >/dev/null 2>&1 || die "usuário não existe: $DASH_USER"
if [ "$DASH_USER" = root ]; then
    echo "ok: a dashboard roda como root — já tem os poderes; regra sudoers desnecessária."
    exit 0
fi

# --- 3) regra sudoers (restrita e validada) ---------------------------------------
SUDOERS_FILE=/etc/sudoers.d/carbonara-dashboard
TMP="$(mktemp)"
cat > "$TMP" <<EOF
# gerado por dashboard/setup-hub-access.sh em $(date -u +%FT%TZ) — pode regerar rodando-o de novo.
# Permite à dashboard CARBONARA executar SOMENTE os subcomandos de gerência do
# rsync-hub (autorizar/revogar estações e migrar a raiz de dados), sem senha.
$DASH_USER ALL=(root) NOPASSWD: $BIN add *, $BIN remove *, $BIN move-root *
EOF
if ! visudo -cf "$TMP" >/dev/null 2>&1; then
    rm -f "$TMP"
    die "a regra sudoers gerada não passou no visudo -cf — nada foi instalado"
fi
install -m 440 -o root -g root "$TMP" "$SUDOERS_FILE"
rm -f "$TMP"
echo "ok: regra instalada em $SUDOERS_FILE:"
grep -v '^#' "$SUDOERS_FILE" | sed 's/^/    /'

# --- 4) verificações ---------------------------------------------------------------
if sudo -l -U "$DASH_USER" 2>/dev/null | grep -q "$BIN"; then
    echo "ok: o sudo confirma os comandos liberados para '$DASH_USER'."
else
    echo "AVISO: 'sudo -l -U $DASH_USER' não mostrou a regra — algum Defaults global"
    echo "       pode estar interferindo; teste na conta: sudo -n $BIN add teste_sudoers"
fi

BASE=/srv/carbonara
if [ -r /etc/rsync-hub.conf ]; then
    # shellcheck disable=SC1091
    . /etc/rsync-hub.conf
fi
if su -s /bin/sh -c "test -r '$BASE' && test -x '$BASE'" "$DASH_USER" 2>/dev/null; then
    echo "ok: '$DASH_USER' consegue LER a raiz do hub ($BASE) — listagem, credenciais e downloads funcionam."
else
    echo "AVISO: '$DASH_USER' não consegue ler $BASE. A política padrão do hub deixa"
    echo "       tudo legível — normalize as permissões com: sudo rsync-hub install --defaults"
fi

echo
echo "Pronto. Na dashboard, entre como admin e abra a página 'Coleta':"
echo "  - autorizar/revogar estações e baixar credenciais/arquivos: já funciona;"
echo "  - mover a raiz de dados: card 'Mover a raiz de dados' em Configurações."
echo "(Não é preciso reiniciar a dashboard — a permissão é verificada a cada acesso.)"
