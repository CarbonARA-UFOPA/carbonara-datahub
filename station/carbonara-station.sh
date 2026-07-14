#!/usr/bin/env bash
# carbonara-station — CLIENTE de coleta CARBONARA para Linux, tudo em UM script.
#
# Roda na máquina da ESTAÇÃO e envia os arquivos de dados para o servidor central
# (o "host", preparado com o script rsync-hub deste projeto), por rsync-over-SSH.
#
# Papéis:
#   1) Assistente guiado — `setup` pergunta id/servidor/pasta, gera a chave SSH e mostra
#      a CHAVE PÚBLICA que o administrador autoriza no host (sudo rsync-hub add <id> <pub>).
#   2) Envio — `send` manda os arquivos prontos (idade mínima configurável, para não pegar
#      arquivo ainda sendo escrito) e, após o sucesso, move-os para .enviados/ (ou apaga,
#      conforme escolhido) — nada é reenviado, e o rsync --partial retoma em link instável.
#   3) Agendamento — `schedule` instala o envio periódico no cron do usuário.
#
# Uso:
#   ./carbonara-station.sh              # menu interativo
#   ./carbonara-station.sh setup        # configuração guiada (1ª vez ou reconfigurar)
#   ./carbonara-station.sh test         # testa a conexão com o servidor (dry-run)
#   ./carbonara-station.sh send         # envia agora os arquivos pendentes
#   ./carbonara-station.sh schedule     # agenda o envio periódico (cron)
#   ./carbonara-station.sh unschedule   # remove o agendamento
#   ./carbonara-station.sh status       # configuração, pendências e últimos envios
#
# Configuração gravada em ~/.config/carbonara-station/config (um arquivo por usuário).
set -eu

CONF_DIR="${CARBONARA_STATION_HOME:-$HOME/.config/carbonara-station}"
CONF="$CONF_DIR/config"
LOGF="$CONF_DIR/envios.log"
CRON_TAG="# carbonara-station send"

die()      { echo "erro: $*" >&2; exit 1; }
self_path(){ readlink -f "$0" 2>/dev/null || echo "$0"; }

ask() { # $1=pergunta $2=default → ecoa a resposta (Enter aceita o default)
    if [ -n "${2:-}" ]; then printf '%s [%s]: ' "$1" "$2" >&2; else printf '%s: ' "$1" >&2; fi
    read -r a || a=""
    if [ -n "$a" ]; then printf '%s\n' "$a"; else printf '%s\n' "${2:-}"; fi
}
ask_yn() { # $1=pergunta $2=default (s|n) → rc 0 = sim
    d="${2:-s}"
    if [ "$d" = s ]; then h="S/n"; else h="s/N"; fi
    printf '%s [%s]: ' "$1" "$h" >&2
    read -r a || a=""
    a="${a:-$d}"
    case "$a" in s|S|y|Y|sim|Sim|SIM) return 0 ;; *) return 1 ;; esac
}

load_conf() {
    [ -r "$CONF" ] || die "estação não configurada — rode: $(basename "$0") setup"
    . "$CONF"
    : "${STATION_ID:?configuração incompleta — rode setup de novo}"
    : "${HUB_HOST:?}" "${DATA_DIR:?}" "${KEY_FILE:?}"
    HUB_PORT="${HUB_PORT:-22}"; HUB_USER="${HUB_USER:-carbonara}"
    MIN_AGE="${MIN_AGE:-1}"; KEEP_COPY="${KEEP_COPY:-yes}"
    SENT_DIR="$DATA_DIR/.enviados"
}

ssh_cmd() { echo "ssh -i $KEY_FILE -p $HUB_PORT -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"; }

# --- Configuração guiada -----------------------------------------------------------
setup() {
    [ -r "$CONF" ] && . "$CONF" || true      # pré-preenche com a configuração atual
    echo
    echo "===== Configuração guiada da estação (Linux) ====="
    echo "Você vai precisar de: o ENDEREÇO do servidor central e o ID desta estação"
    echo "(combinados com o administrador do servidor)."
    echo
    while :; do
        STATION_ID="$(ask "Id desta estação (minúsculas/dígitos/_ , ex.: flona01)" "${STATION_ID:-}")"
        echo "$STATION_ID" | grep -Eq '^[a-z0-9][a-z0-9_]{1,30}$' && break
        echo "  id inválido — use minúsculas, dígitos e '_' (2 a 31 caracteres)."
    done
    while :; do
        HUB_HOST="$(ask "Endereço do servidor central (IP ou hostname)" "${HUB_HOST:-}")"
        [ -n "$HUB_HOST" ] && break
        echo "  informe um endereço."
    done
    HUB_PORT="$(ask "Porta SSH do servidor" "${HUB_PORT:-22}")"
    HUB_USER="$(ask "Conta de serviço no servidor" "${HUB_USER:-carbonara}")"
    while :; do
        DATA_DIR="$(ask "Pasta LOCAL onde ficam os dados a enviar" "${DATA_DIR:-$HOME/carbonara-dados}")"
        case "$DATA_DIR" in /*) : ;; *) echo "  use um caminho absoluto."; continue ;; esac
        [ -d "$DATA_DIR" ] && break
        if ask_yn "  a pasta não existe — criar $DATA_DIR?" s; then mkdir -p "$DATA_DIR"; break; fi
    done
    while :; do
        MIN_AGE="$(ask "Idade mínima (minutos) para um arquivo ser considerado pronto" "${MIN_AGE:-1}")"
        echo "$MIN_AGE" | grep -Eq '^[0-9]+$' && break
        echo "  informe um número inteiro de minutos."
    done
    if ask_yn "Manter cópia local após o envio (move para $DATA_DIR/.enviados)?" s
    then KEEP_COPY=yes
    else KEEP_COPY=no
    fi

    # Mantém a chave já configurada só se ela for a deste id (reconfigurar com outro
    # id gera/usa outra chave, para não confundir estação e credencial).
    case "${KEY_FILE:-}" in
        */carbonara_"$STATION_ID") : ;;
        *) KEY_FILE="$HOME/.ssh/carbonara_$STATION_ID" ;;
    esac
    if [ ! -f "$KEY_FILE" ]; then
        echo
        echo "Gerando o par de chaves SSH desta estação ($KEY_FILE)…"
        mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
        ssh-keygen -q -t ed25519 -N "" -C "carbonara-$STATION_ID" -f "$KEY_FILE"
    fi

    mkdir -p "$CONF_DIR"
    { echo "# gerado por carbonara-station setup em $(date -u +%FT%TZ)"
      echo "STATION_ID=$STATION_ID"
      echo "HUB_HOST=$HUB_HOST"
      echo "HUB_PORT=$HUB_PORT"
      echo "HUB_USER=$HUB_USER"
      echo "DATA_DIR=$DATA_DIR"
      echo "KEY_FILE=$KEY_FILE"
      echo "MIN_AGE=$MIN_AGE"
      echo "KEEP_COPY=$KEEP_COPY"; } > "$CONF"
    chmod 600 "$CONF"
    echo
    echo "ok: configuração gravada em $CONF."
    echo
    echo "===== FALTA UM PASSO, NO SERVIDOR ====="
    echo "Entregue a CHAVE PÚBLICA abaixo ao administrador do servidor, que autoriza com:"
    echo "    sudo rsync-hub add $STATION_ID <arquivo-com-a-chave>.pub"
    echo "(ou colando a linha no menu do rsync-hub → 'Adicionar estação')"
    echo
    echo "--- chave pública (copie a linha inteira) ---"
    cat "$KEY_FILE.pub"
    echo "---------------------------------------------"
    echo
    if ask_yn "A estação já foi autorizada no servidor? Testar a conexão agora?" n; then
        test_conn || true
    else
        echo "quando ela for autorizada, valide com: $(basename "$0") test"
    fi
}

# --- Teste de conexão ---------------------------------------------------------------
test_conn() {
    load_conf
    echo "testando conexão com $HUB_USER@$HUB_HOST:$HUB_PORT (dry-run, nada é enviado)…"
    if out="$(rsync -an --timeout=30 -e "$(ssh_cmd)" "$DATA_DIR/" "$HUB_USER@$HUB_HOST:/" 2>&1)"; then
        echo "ok: conexão e permissões funcionando."
    else
        echo "falhou:"
        echo "$out" | sed 's/^/  /'
        echo "verifique:"
        echo "  - a chave pública já foi autorizada no servidor? (sudo rsync-hub add $STATION_ID …)"
        echo "  - endereço ($HUB_HOST) e porta ($HUB_PORT) corretos? há rede até o servidor?"
        return 1
    fi
}

# --- Envio ---------------------------------------------------------------------------
send() {
    load_conf
    mkdir -p "$CONF_DIR"
    exec 9>"$CONF_DIR/.lock"
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        echo "outro envio já está em andamento — saindo."
        return 0
    fi
    cd "$DATA_DIR"
    list="$(mktemp)"
    # Elegíveis: arquivos "assentados" (mais velhos que MIN_AGE), fora de pastas ocultas
    # (inclui a .enviados/) — mesmo critério de dotfiles da ingestão no servidor.
    find . -type f -not -path '*/.*' -not -name '.*' -mmin "+$MIN_AGE" 2>/dev/null \
        | sed 's|^\./||' | sort > "$list"
    n="$(wc -l < "$list" | tr -d ' ')"
    if [ "$n" = 0 ]; then
        echo "nada a enviar (arquivos com menos de $MIN_AGE min de idade não contam)."
        rm -f "$list"; return 0
    fi
    echo "enviando $n arquivo(s) para $HUB_USER@${HUB_HOST}…"
    rc=0
    rsync -az --partial --timeout=120 --files-from="$list" -e "$(ssh_cmd)" \
        ./ "$HUB_USER@$HUB_HOST:/" || rc=$?
    if [ "$rc" = 0 ]; then
        while IFS= read -r f; do
            if [ "$KEEP_COPY" = yes ]; then
                mkdir -p "$SENT_DIR/$(dirname "$f")"
                mv -f "$f" "$SENT_DIR/$f"
            else
                rm -f "$f"
            fi
        done < "$list"
        echo "$(date -u +%FT%TZ) OK $n arquivo(s) enviados" >> "$LOGF"
        echo "ok: $n arquivo(s) enviados."
    else
        echo "$(date -u +%FT%TZ) FALHA rc=$rc ($n arquivo(s) pendentes)" >> "$LOGF"
        echo "falha no envio (rsync rc=$rc) — nada foi movido; será tentado de novo no próximo ciclo." >&2
    fi
    rm -f "$list"
    return "$rc"
}

# --- Agendamento (cron) ---------------------------------------------------------------
schedule() {
    load_conf
    command -v crontab >/dev/null 2>&1 || die "crontab não encontrado nesta máquina"
    while :; do
        iv="$(ask "Enviar a cada quantos minutos? (1-59)" "10")"
        echo "$iv" | grep -Eq '^[0-9]+$' && [ "$iv" -ge 1 ] && [ "$iv" -le 59 ] && break
        echo "  informe um número entre 1 e 59."
    done
    self="$(self_path)"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true
      echo "*/$iv * * * * $self send >> $LOGF 2>&1 $CRON_TAG" ) | crontab -
    echo "ok: envio agendado a cada $iv min (cron do usuário $(id -un))."
    echo "para remover: $(basename "$0") unschedule"
}

unschedule() {
    command -v crontab >/dev/null 2>&1 || die "crontab não encontrado nesta máquina"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true ) | crontab -
    echo "ok: agendamento removido (se existia)."
}

# --- Status ---------------------------------------------------------------------------
status() {
    load_conf
    if [ "$KEEP_COPY" = yes ]; then pos="move para .enviados/"; else pos="apaga"; fi
    echo "estação:   $STATION_ID"
    echo "servidor:  $HUB_USER@$HUB_HOST:$HUB_PORT"
    echo "chave:     $KEY_FILE"
    echo "dados:     $DATA_DIR (pós-envio: $pos)"
    pend="$(find "$DATA_DIR" -type f -not -path '*/.*' -not -name '.*' 2>/dev/null | wc -l | tr -d ' ')"
    echo "pendentes: $pend arquivo(s)"
    if crontab -l 2>/dev/null | grep -qF "$CRON_TAG"; then
        echo "agenda:    $(crontab -l 2>/dev/null | grep -F "$CRON_TAG" | awk '{print $1}') (cron)"
    else
        echo "agenda:    (não agendado — use 'schedule')"
    fi
    echo "últimos envios:"
    if [ -f "$LOGF" ]; then tail -n 5 "$LOGF" | sed 's/^/  /'; else echo "  (nenhum envio registrado)"; fi
}

# --- Menu interativo -------------------------------------------------------------------
menu() {
    while true; do
        echo
        echo "===== carbonara-station — estação (cliente Linux) ====="
        echo " 1) Configurar esta estação (guiado)"
        echo " 2) Testar conexão com o servidor"
        echo " 3) Enviar agora"
        echo " 4) Agendar envio periódico"
        echo " 5) Remover agendamento"
        echo " 6) Status"
        echo " 0) Sair"
        printf "opção> "
        read -r op || exit 0
        case "$op" in
            1) ( setup ) || true ;;
            2) ( test_conn ) || true ;;
            3) ( send ) || true ;;
            4) ( schedule ) || true ;;
            5) ( unschedule ) || true ;;
            6) ( status ) || true ;;
            0|q|"") exit 0 ;;
            *) echo "opção inválida" ;;
        esac
    done
}

usage() {
    cat <<EOF
carbonara-station — cliente de coleta CARBONARA (Linux)

uso:
  $(basename "$0") setup        configuração guiada (1ª vez ou reconfigurar)
  $(basename "$0") test         testa a conexão com o servidor (dry-run)
  $(basename "$0") send         envia agora os arquivos pendentes
  $(basename "$0") schedule     agenda o envio periódico (cron)
  $(basename "$0") unschedule   remove o agendamento
  $(basename "$0") status       configuração, pendências e últimos envios
  $(basename "$0")              menu interativo

configuração por usuário em $CONF
EOF
}

case "${1:-}" in
    setup)      setup ;;
    test)       test_conn ;;
    send)       send ;;
    schedule)   schedule ;;
    unschedule) unschedule ;;
    status)     status ;;
    ""|menu)    menu ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
esac
