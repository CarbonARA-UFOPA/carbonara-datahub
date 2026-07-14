#!/usr/bin/env bash
# carbonara-station — CLIENTE de coleta CARBONARA para Linux, tudo em UM script.
#
# Roda na máquina da ESTAÇÃO e envia os arquivos de dados para o servidor central
# (o "host", preparado com o script rsync-hub deste projeto), por rsync-over-SSH.
#
# Papéis:
#   1) Assistente guiado — `setup` pergunta, nesta ordem: id da estação → IP do host
#      (porta opcional, padrão 22) → caminho local da CHAVE recebida do servidor
#      (gerada lá pelo `rsync-hub add <id>`; se não existir, oferece gerar uma nova
#      aqui) → TESTE simples de conexão → pasta local a enviar (rsync) → tempo de
#      reciclagem (minutos) + ativação do envio automático.
#   2) Envio — `send` manda os arquivos prontos (idade mínima configurável, para não
#      pegar arquivo ainda sendo escrito) e, após o sucesso, move-os para .enviados/
#      (ou apaga, conforme escolhido) — nada é reenviado, e o rsync --partial retoma
#      transferências interrompidas (link instável).
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
    MIN_AGE="${MIN_AGE:-1}"; KEEP_COPY="${KEEP_COPY:-yes}"; SEND_EVERY="${SEND_EVERY:-10}"
    SENT_DIR="$DATA_DIR/.enviados"
}

ssh_cmd() { echo "ssh -i $KEY_FILE -p $HUB_PORT -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"; }

# Teste de conexão que NÃO depende da pasta de dados: dry-run a partir de uma pasta
# vazia — valida rede, porta, chave e o confinamento no servidor, sem enviar nada.
probe_conn() {
    tmpd="$(mktemp -d)"
    if out="$(rsync -an --timeout=20 -e "$(ssh_cmd)" "$tmpd/" "$HUB_USER@$HUB_HOST:/" 2>&1)"; then
        rmdir "$tmpd" 2>/dev/null || true
        echo "ok: o servidor respondeu e aceitou a chave."
        return 0
    fi
    rmdir "$tmpd" 2>/dev/null || true
    echo "falhou:"
    echo "$out" | sed 's/^/  /'
    return 1
}

# --- Configuração guiada -----------------------------------------------------------
setup() {
    [ -r "$CONF" ] && . "$CONF" || true      # pré-preenche com a configuração atual
    echo
    echo "===== Configuração guiada da estação (Linux) ====="
    echo "Você vai precisar de: o ENDEREÇO (IP) do servidor central, o ID desta estação"
    echo "e o arquivo de CHAVE recebido do servidor (gerado lá por 'rsync-hub add <id>',"
    echo "que também cria um INSTRUCOES.txt com esses dados)."
    echo

    # 1. Identificação
    while :; do
        STATION_ID="$(ask "Id desta estação (minúsculas/dígitos/_ , ex.: flona01)" "${STATION_ID:-}")"
        echo "$STATION_ID" | grep -Eq '^[a-z0-9][a-z0-9_]{1,30}$' && break
        echo "  id inválido — use minúsculas, dígitos e '_' (2 a 31 caracteres)."
    done

    # 2. Servidor (IP + porta, padrão 22)
    while :; do
        HUB_HOST="$(ask "IP (ou hostname) do servidor central" "${HUB_HOST:-}")"
        [ -n "$HUB_HOST" ] && break
        echo "  informe um endereço."
    done
    HUB_PORT="$(ask "Porta SSH do servidor" "${HUB_PORT:-22}")"
    HUB_USER="$(ask "Conta de serviço no servidor" "${HUB_USER:-carbonara}")"

    # 3. Chave de conexão (recebida do servidor; se não existir, oferece gerar aqui)
    NEW_KEY=""
    case "${KEY_FILE:-}" in
        */carbonara_"$STATION_ID"|*/"${STATION_ID}"_key) : ;;   # default só se for deste id
        *) KEY_FILE="" ;;
    esac
    while :; do
        KEY_FILE="$(ask "Caminho local da CHAVE desta estação (arquivo ${STATION_ID}_key recebido do servidor)" "${KEY_FILE:-}")"
        [ -n "$KEY_FILE" ] || { echo "  informe um caminho."; continue; }
        case "$KEY_FILE" in /*) : ;; *) KEY_FILE="$PWD/$KEY_FILE" ;; esac
        if [ -f "$KEY_FILE" ]; then
            chmod 600 "$KEY_FILE" 2>/dev/null || \
                echo "  aviso: não consegui ajustar as permissões da chave (o ssh exige 600)."
            break
        fi
        echo "  não encontrei a chave em: $KEY_FILE"
        if ask_yn "  Gerar um NOVO par de chaves aqui (a pública precisará ser autorizada no servidor)?" n; then
            KEY_FILE="$HOME/.ssh/carbonara_$STATION_ID"
            mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
            [ -f "$KEY_FILE" ] || ssh-keygen -q -t ed25519 -N "" -C "carbonara-$STATION_ID" -f "$KEY_FILE"
            NEW_KEY=1
            break
        fi
    done

    # 4. Teste simples de conexão (o host responde? a chave é aceita?)
    echo
    if [ -n "$NEW_KEY" ]; then
        echo "(chave nova, ainda não autorizada no servidor — o teste fica para depois.)"
    elif ask_yn "Testar a conexão com o servidor agora?" s; then
        if ! probe_conn; then
            echo "  dicas: a estação foi autorizada no servidor? IP/porta corretos? há rede?"
            ask_yn "  Continuar a configuração mesmo assim?" s || { echo "configuração interrompida."; return 1; }
        fi
    fi
    echo

    # 5. Pasta local que será enviada (rsync)
    while :; do
        DATA_DIR="$(ask "Pasta LOCAL que deseja enviar por rsync" "${DATA_DIR:-$HOME/carbonara-dados}")"
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

    # 6. Tempo de reciclagem (intervalo do envio automático)
    while :; do
        SEND_EVERY="$(ask "Tempo de reciclagem do envio — a cada quantos minutos? (1-59)" "${SEND_EVERY:-10}")"
        echo "$SEND_EVERY" | grep -Eq '^[0-9]+$' && [ "$SEND_EVERY" -ge 1 ] && [ "$SEND_EVERY" -le 59 ] && break
        echo "  informe um número entre 1 e 59."
    done

    mkdir -p "$CONF_DIR"
    { echo "# gerado por carbonara-station setup em $(date -u +%FT%TZ)"
      echo "STATION_ID=$STATION_ID"
      echo "HUB_HOST=$HUB_HOST"
      echo "HUB_PORT=$HUB_PORT"
      echo "HUB_USER=$HUB_USER"
      echo "DATA_DIR=$DATA_DIR"
      echo "KEY_FILE=$KEY_FILE"
      echo "MIN_AGE=$MIN_AGE"
      echo "KEEP_COPY=$KEEP_COPY"
      echo "SEND_EVERY=$SEND_EVERY"; } > "$CONF"
    chmod 600 "$CONF"
    echo
    echo "ok: configuração gravada em $CONF."

    if [ -n "$NEW_KEY" ]; then
        echo
        echo "===== FALTA UM PASSO, NO SERVIDOR ====="
        echo "A chave foi gerada AQUI, então o administrador precisa autorizar a pública:"
        echo "    sudo rsync-hub add $STATION_ID <arquivo-com-a-chave>.pub"
        echo "(ou colando a linha no menu do rsync-hub → 'Adicionar estação')"
        echo
        echo "--- chave pública (copie a linha inteira) ---"
        cat "$KEY_FILE.pub"
        echo "---------------------------------------------"
        echo
        echo "depois de autorizada, valide com: $(basename "$0") test"
    fi

    echo
    if ask_yn "Ativar o envio automático agora (a cada $SEND_EVERY min, via cron)?" s; then
        schedule_install "$SEND_EVERY"
    else
        echo "quando quiser ativar: $(basename "$0") schedule   (ou envie manualmente com 'send')"
    fi
}

# --- Teste de conexão ---------------------------------------------------------------
test_conn() {
    load_conf
    echo "testando conexão com $HUB_USER@$HUB_HOST:$HUB_PORT (dry-run, nada é enviado)…"
    if ! probe_conn; then
        echo "verifique:"
        echo "  - a estação foi autorizada no servidor? (lá: sudo rsync-hub add $STATION_ID …)"
        echo "  - a chave é a recebida do servidor? ($KEY_FILE)"
        echo "  - IP ($HUB_HOST) e porta ($HUB_PORT) corretos? há rede até o servidor?"
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
schedule_install() {  # $1 = intervalo em minutos (instala/substitui a entrada no cron)
    iv="$1"
    command -v crontab >/dev/null 2>&1 || die "crontab não encontrado nesta máquina"
    self="$(self_path)"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true
      echo "*/$iv * * * * $self send >> $LOGF 2>&1 $CRON_TAG" ) | crontab -
    echo "ok: envio automático ativo a cada $iv min (cron do usuário $(id -un))."
    echo "para remover: $(basename "$0") unschedule"
}

schedule() {
    load_conf
    while :; do
        iv="$(ask "Enviar a cada quantos minutos? (1-59)" "$SEND_EVERY")"
        echo "$iv" | grep -Eq '^[0-9]+$' && [ "$iv" -ge 1 ] && [ "$iv" -le 59 ] && break
        echo "  informe um número entre 1 e 59."
    done
    schedule_install "$iv"
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
