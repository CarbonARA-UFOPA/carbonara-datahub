#!/usr/bin/env bash
# setup-hub-access.sh — prepara ESTE servidor para a dashboard administrar a coleta.
#
# O que ele faz (rode UMA vez, como root, no servidor do hub):
#   1. instala/atualiza o rsync-hub em /usr/local/bin (a partir do repositório);
#   2. grava em /etc/sudoers.d/carbonara-dashboard uma regra NOPASSWD restrita aos
#      subcomandos de gerência (add / remove / purge / move-root / pause / resume) para
#      o usuário que RODA a dashboard — é o que a página "Coleta" usa via `sudo -n`;
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

# Quem está RODANDO a dashboard agora — é este usuário que precisa da regra, e errar o
# nome aqui produz o pior sintoma possível: script "bem-sucedido" e tela continuando em
# somente leitura. Tenta, nesta ordem: PID file do dashboardctl, unidade systemd,
# processo do run.py. Ecoa vazio quando não achar (aí o default cai no dono da pasta).
detect_dash_user() {
    du_pid=""
    [ -r "$HERE/.dashboard.pid" ] && du_pid="$(cat "$HERE/.dashboard.pid" 2>/dev/null)"
    if [ -z "$du_pid" ] && command -v systemctl >/dev/null 2>&1; then
        du_u="$(systemctl show -p User --value carbonara-dashboard 2>/dev/null || true)"
        if [ -n "$du_u" ]; then printf '%s\n' "$du_u"; return 0; fi
        du_pid="$(systemctl show -p MainPID --value carbonara-dashboard 2>/dev/null || true)"
        [ "$du_pid" = 0 ] && du_pid=""
    fi
    if [ -z "$du_pid" ] && command -v pgrep >/dev/null 2>&1; then
        du_pid="$(pgrep -f '[r]un\.py' 2>/dev/null | head -1 || true)"
    fi
    if [ -n "$du_pid" ]; then
        ps -o user= -p "$du_pid" 2>/dev/null | tr -d ' [:space:]' || true
    fi
    return 0
}

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
grep -q "pause_reception" "$BIN" || \
    echo "AVISO: o $BIN instalado não tem 'pause'/'resume' — atualize-o pelo repositório
       para poder pausar a recepção de dados pela dashboard."

# --- 2) usuário que roda a dashboard ----------------------------------------------
DASH_USER="${1:-}"
if [ -z "$DASH_USER" ]; then
    # Quem chamou o sudo é o palpite natural — MAS a partir de um shell já root
    # (sudo su -) o SUDO_USER é root/vazio e aceitar isso instalaria a regra para o
    # usuário errado (ou nenhuma). Nesse caso, sugere o DONO da pasta da dashboard,
    # que é como o 'dashboardctl servico' escolhe o usuário do serviço.
    default="$(detect_dash_user)"
    if [ -n "$default" ]; then
        echo "detectei a dashboard rodando como: $default"
    else
        default="${SUDO_USER:-}"
        if [ -z "$default" ] || [ "$default" = root ]; then
            default="$(stat -c %U "$HERE" 2>/dev/null || stat -f %Su "$HERE" 2>/dev/null || echo '')"
        fi
        echo "(não achei a dashboard rodando — se ela estiver parada, informe o usuário à mão)"
    fi
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
    echo "AVISO: '$DASH_USER' foi informado como usuário da dashboard."
    echo "       Rodando como root ela já tem os poderes e a regra é desnecessária —"
    echo "       mas se a tela 'Coleta' continua em modo somente leitura, então o"
    echo "       processo NÃO roda como root e a regra foi instalada para o nome errado."
    echo "       Confira quem realmente roda a dashboard:"
    echo "           ps -eo user,pid,args | grep '[r]un.py'"
    echo "           systemctl show -p User --value carbonara-dashboard"
    echo "       e rode de novo:  sudo $0 <usuario-que-apareceu>"
    exit 0
fi

# --- 3) regra sudoers (restrita e validada) ---------------------------------------
# Sem a diretiva de inclusão, /etc/sudoers.d é IGNORADO e a regra não vale nada — um
# arquivo instalado com sucesso e nenhum efeito é o pior dos dois mundos.
if ! grep -Eq '^[[:space:]]*[#@]includedir[[:space:]]+/etc/sudoers\.d' /etc/sudoers 2>/dev/null; then
    echo "AVISO: /etc/sudoers não inclui /etc/sudoers.d — a regra abaixo NÃO teria efeito."
    echo "       Adicione (com visudo) a última linha:  #includedir /etc/sudoers.d"
    echo "       (o '#' faz parte da diretiva, não é comentário)"
fi
SUDOERS_FILE=/etc/sudoers.d/carbonara-dashboard
TMP="$(mktemp)"
cat > "$TMP" <<EOF
# gerado por dashboard/setup-hub-access.sh em $(date -u +%FT%TZ) — pode regerar rodando-o de novo.
# Permite à dashboard CARBONARA executar SOMENTE os subcomandos de gerência do
# rsync-hub (autorizar/revogar estações, migrar a raiz de dados e pausar/retomar a
# recepção), sem senha.
$DASH_USER ALL=(root) NOPASSWD: $BIN add *, $BIN remove *, $BIN purge *, $BIN move-root *, $BIN pause *, $BIN resume *
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
    echo "AVISO: 'sudo -l -U $DASH_USER' não mostrou a regra. Verifique, nesta ordem:"
    echo "       1) /etc/sudoers termina com:  #includedir /etc/sudoers.d"
    echo "       2) a dashboard roda mesmo como '$DASH_USER':"
    echo "          ps -eo user,pid,args | grep '[r]un.py'"
    echo "       3) na conta dela:  sudo -n -l $BIN add teste_sudoers ; echo rc=\$?"
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
echo "  - mover a raiz de dados: card 'Mover a raiz de dados' em Configurações;"
echo "  - pausar/retomar a recepção: card 'Recepção de dados' na própria página Coleta."
echo "(Não é preciso reiniciar a dashboard — a permissão é verificada a cada acesso.)"
