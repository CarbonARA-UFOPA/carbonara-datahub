#!/usr/bin/env bash
# carbonara-station — CLIENTE de coleta CARBONARA para Linux, tudo em UM script.
#
# Roda na máquina da ESTAÇÃO e envia os arquivos de dados para o servidor central
# (o "host", preparado com o script rsync-hub deste projeto), por rsync-over-SSH.
#
# Papéis:
#   1) Assistente guiado — `setup` pergunta, nesta ordem: id da estação → IP do host →
#      como autenticar: CHAVE SSH (porta 22; arquivo recebido do servidor, gerado lá
#      pelo `rsync-hub add <id>`) OU USUÁRIO+SENHA (porta 873, rsync direto; criado lá
#      pelo `rsync-hub add <id> --senha` — mais simples, porém dados sem criptografia)
#      → TESTE simples de conexão → pasta local a enviar (rsync) → tempo de
#      reciclagem (minutos) + ativação do envio automático.
#   2) Envio — `send` SINCRONIZA a pasta local com o servidor: NUNCA move nem apaga
#      nada da pasta local. O controle do que já foi enviado é um estado local
#      (tamanho+mtime por arquivo, em ~/.config/carbonara-station/sent.state): só vai
#      o que é novo ou mudou desde o último envio. Idade mínima configurável evita
#      arquivo ainda sendo escrito; o rsync --partial retoma em link instável.
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
STATE="$CONF_DIR/sent.state"     # o que já foi enviado: caminho<TAB>tamanho:mtime
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
    : "${HUB_HOST:?}" "${DATA_DIR:?}"
    AUTH_MODE="${AUTH_MODE:-chave}"
    if [ "$AUTH_MODE" = senha ]; then
        : "${PASSWORD:?configuração incompleta (senha) — rode setup de novo}"
        HUB_PORT="${HUB_PORT:-873}"
    else
        : "${KEY_FILE:?configuração incompleta (chave) — rode setup de novo}"
        HUB_PORT="${HUB_PORT:-22}"
    fi
    HUB_USER="${HUB_USER:-carbonara}"
    MIN_AGE="${MIN_AGE:-1}"; SEND_EVERY="${SEND_EVERY:-10}"
}

rsync_dest() {  # destino conforme o modo: módulo do rsync daemon OU conta ssh
    if [ "$AUTH_MODE" = senha ]; then
        echo "rsync://$STATION_ID@$HUB_HOST:$HUB_PORT/$STATION_ID/"
    else
        echo "$HUB_USER@$HUB_HOST:/"
    fi
}

sig_of() { # tamanho:mtime — a "assinatura" que detecta arquivo novo/alterado
    stat -c '%s:%Y' "$1" 2>/dev/null || stat -f '%z:%m' "$1"
}

scan_all() { # gera $1: inventário completo da pasta — caminho<TAB>assinatura
    ( cd "$DATA_DIR" && find . -type f -not -path '*/.*' -not -name '.*' 2>/dev/null \
        | sed 's|^\./||' | sort \
        | while IFS= read -r f; do printf '%s\t%s\n' "$f" "$(sig_of "$f")"; done ) > "$1"
}

ssh_cmd() { echo "ssh -i $KEY_FILE -p $HUB_PORT -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"; }

# Sinal de vida: a cada ciclo de envio (mesmo sem dados novos) sobe um arquivinho
# OCULTO (.carbonara-status) para a pasta da estação no servidor, declarando o tempo
# de reciclagem — é com ele que o monitor marca Online/Offline por estação. Sem -t:
# o mtime no servidor é o horário de CHEGADA (imune a fuso/relógio da estação).
send_heartbeat() {
    hb="$CONF_DIR/.carbonara-status"
    { echo "station=$STATION_ID"
      echo "interval_min=$SEND_EVERY"
      echo "ts=$(date -u +%FT%TZ)"; } > "$hb"
    if [ "$AUTH_MODE" = senha ]; then
        RSYNC_PASSWORD="$PASSWORD" rsync -z --chmod=F644 --timeout=30 "$hb" \
            "rsync://$STATION_ID@$HUB_HOST:$HUB_PORT/$STATION_ID/.carbonara-status" >/dev/null 2>&1
    else
        rsync -z --chmod=F644 --timeout=30 -e "$(ssh_cmd)" "$hb" \
            "$HUB_USER@$HUB_HOST:/.carbonara-status" >/dev/null 2>&1
    fi
}

# Teste de conexão que NÃO depende da pasta de dados: dry-run a partir de uma pasta
# vazia — valida rede, porta, credencial e o confinamento no servidor, sem enviar nada.
probe_conn() {
    tmpd="$(mktemp -d)"
    if [ "$AUTH_MODE" = senha ]; then
        out="$(RSYNC_PASSWORD="$PASSWORD" rsync -an --timeout=20 "$tmpd/" "$(rsync_dest)" 2>&1)" && ok=0 || ok=1
    else
        out="$(rsync -an --timeout=20 -e "$(ssh_cmd)" "$tmpd/" "$(rsync_dest)" 2>&1)" && ok=0 || ok=1
    fi
    rmdir "$tmpd" 2>/dev/null || true
    if [ "$ok" = 0 ]; then
        echo "ok: o servidor respondeu e aceitou a credencial."
        return 0
    fi
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
    # 3. Autenticação: chave SSH ou usuário+senha
    echo "Como esta estação se autentica no servidor?"
    echo "  1) chave SSH (recomendado — tráfego criptografado; porta 22)"
    echo "  2) usuário e senha (mais simples — rsync direto na porta 873; dados sem criptografia)"
    d=1; [ "${AUTH_MODE:-}" = senha ] && d=2
    modo="$(ask "modo" "$d")"
    if [ "$modo" = 2 ]; then AUTH_MODE=senha; else AUTH_MODE=chave; fi

    NEW_KEY=""
    if [ "$AUTH_MODE" = senha ]; then
        HUB_PORT="$(ask "Porta do rsync no servidor" "${HUB_PORT:-873}")"
        echo "O usuário é o próprio id da estação ($STATION_ID); a senha está no"
        echo "INSTRUCOES.txt entregue pelo administrador (gerada por 'rsync-hub add $STATION_ID --senha')."
        while :; do
            PASSWORD="$(ask "Senha desta estação" "${PASSWORD:-}")"
            [ -n "$PASSWORD" ] && break
            echo "  informe a senha."
        done
    else
        HUB_PORT="$(ask "Porta SSH do servidor" "${HUB_PORT:-22}")"
        # A conta de serviço é ÚNICA no servidor (recebe todas as estações) — quase sempre
        # 'carbonara'. Usuários tendem a digitar o id da estação aqui; confirme se mudar.
        HUB_USER="$(ask "Conta de serviço no servidor — NÃO é o id da estação; aperte Enter" "${HUB_USER:-carbonara}")"
        if [ "$HUB_USER" != carbonara ]; then
            if ! ask_yn "  atenção: normalmente a conta é 'carbonara' — usar '$HUB_USER' mesmo?" n; then
                HUB_USER=carbonara
                echo "  ok, usando 'carbonara'."
            fi
        fi
        # Chave recebida do servidor; se não existir, oferece gerar aqui.
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
    fi

    # 4. Teste simples de conexão (o host responde? a credencial é aceita?)
    echo
    if [ -n "$NEW_KEY" ]; then
        echo "(chave nova, ainda não autorizada no servidor — o teste fica para depois.)"
    elif ask_yn "Testar a conexão com o servidor agora?" s; then
        if ! probe_conn; then
            if [ "$AUTH_MODE" = senha ]; then
                echo "  dicas: senha correta? a estação foi criada no servidor com"
                echo "         'rsync-hub add $STATION_ID --senha'? porta $HUB_PORT liberada no firewall?"
            else
                echo "  dicas: 'Permission denied' → a conta de serviço está certa? (padrão: carbonara,"
                echo "         não é o id da estação) e a chave é a gerada no servidor para este id?"
                echo "         Outras causas: estação não autorizada, IP/porta errados, sem rede."
            fi
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
    echo "  (a pasta local NUNCA é alterada: nada é movido nem apagado — o envio só"
    echo "   sincroniza com o servidor o que for novo ou mudar.)"

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
      echo "AUTH_MODE=$AUTH_MODE"
      if [ "$AUTH_MODE" = senha ]; then
          echo "PASSWORD=$PASSWORD"
      else
          echo "HUB_USER=${HUB_USER:-carbonara}"
          echo "KEY_FILE=$KEY_FILE"
      fi
      echo "DATA_DIR=$DATA_DIR"
      echo "MIN_AGE=$MIN_AGE"
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
        if [ "$AUTH_MODE" = senha ]; then
            echo "  - a senha confere com a do servidor? (lá: sudo rsync-hub add $STATION_ID --senha)"
            echo "  - porta $HUB_PORT liberada no firewall? o serviço carbonara-rsyncd está no ar?"
        else
            echo "  - 'Permission denied' → a conta de serviço está certa? (a sua: '$HUB_USER';"
            echo "    o padrão é 'carbonara' — não é o id da estação)"
            echo "  - saiu 'rsync-hub: Permission denied'? o problema é NO SERVIDOR (o script"
            echo "    saiu do lugar/não é executável pela conta de serviço) — lá, rode de novo:"
            echo "    sudo rsync-hub add $STATION_ID   (regrava a chave apontando p/ /usr/local/bin)"
            echo "  - a estação foi autorizada no servidor? (lá: sudo rsync-hub add $STATION_ID …)"
            echo "  - a chave é a recebida do servidor? ($KEY_FILE)"
        fi
        echo "  - IP ($HUB_HOST) e porta ($HUB_PORT) corretos? há rede até o servidor?"
        return 1
    fi
}

# --- Envio ---------------------------------------------------------------------------
# Sincronização pura: NADA é movido ou apagado da pasta local. O que já foi enviado é
# controlado pelo estado local ($STATE, assinatura tamanho:mtime por arquivo) — só vai
# o que é novo ou mudou; arquivos que crescem (ex.: .dat de datalogger) são reenviados
# por inteiro e o servidor não duplica medições (ingestão idempotente).
send() {
    load_conf
    mkdir -p "$CONF_DIR"
    exec 9>"$CONF_DIR/.lock"
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        echo "outro envio já está em andamento — saindo."
        return 0
    fi
    [ -f "$STATE" ] || : > "$STATE"
    # Sinal de vida ANTES de tudo: mesmo sem dados novos o servidor sabe que a
    # estação está viva (e qual é o seu ciclo). Falha aqui não interrompe o envio.
    if ! send_heartbeat; then
        echo "aviso: não consegui enviar o sinal de vida ao servidor (sem rede?)." >&2
    fi
    all="$(mktemp)"; elig="$(mktemp)"; tosend="$(mktemp)"; lista="$(mktemp)"
    scan_all "$all"
    # Elegíveis: já "assentados" (mais velhos que MIN_AGE), fora de pastas/arquivos
    # ocultos — mesmo critério de dotfiles da ingestão no servidor.
    ( cd "$DATA_DIR" && find . -type f -not -path '*/.*' -not -name '.*' -mmin "+$MIN_AGE" 2>/dev/null \
        | sed 's|^\./||' ) > "$elig"
    # A enviar: elegíveis que são novos ou cuja assinatura mudou desde o último envio.
    awk -F'\t' '
        FILENAME==ARGV[1] { st[$1]=$2; next }
        FILENAME==ARGV[2] { el[$1]=1;  next }
        ($1 in el) && st[$1] != $2 { print }
    ' "$STATE" "$elig" "$all" > "$tosend"
    n="$(wc -l < "$tosend" | tr -d ' ')"
    if [ "$n" = 0 ]; then
        echo "nada novo a sincronizar (arquivos com menos de $MIN_AGE min não contam)."
        rm -f "$all" "$elig" "$tosend" "$lista"; return 0
    fi
    cut -f1 "$tosend" > "$lista"
    echo "sincronizando $n arquivo(s) novo(s)/alterado(s) com ${HUB_HOST}…"
    rc=0
    # -rltz (sem -p/-o/-g) + --chmod: os arquivos chegam 644/755 no servidor —
    # legíveis pelos usuários locais do host, independentemente das permissões aqui.
    # --partial-dir: uma transferência cancelada/interrompida deixa o pedaço num
    # diretório OCULTO no servidor (a ingestão nunca o vê) e o próximo envio retoma
    # dali — nunca fica um arquivo truncado com nome real no incoming/.
    if [ "$AUTH_MODE" = senha ]; then
        ( cd "$DATA_DIR" && RSYNC_PASSWORD="$PASSWORD" rsync -rltz --chmod=D755,F644 \
            --partial-dir=.rsync-parcial --timeout=120 --files-from="$lista" \
            ./ "$(rsync_dest)" ) || rc=$?
    else
        ( cd "$DATA_DIR" && rsync -rltz --chmod=D755,F644 --partial-dir=.rsync-parcial \
            --timeout=120 --files-from="$lista" -e "$(ssh_cmd)" ./ "$(rsync_dest)" ) || rc=$?
    fi
    if [ "$rc" = 0 ]; then
        # Registra no estado — a pasta local permanece intocada.
        awk -F'\t' 'NR==FNR { u[$1]=1; next } !($1 in u)' "$tosend" "$STATE" > "$STATE.tmp" || true
        cat "$tosend" >> "$STATE.tmp"
        # Poda do estado: só entradas de arquivos que ainda existem localmente.
        awk -F'\t' 'NR==FNR { ex[$1]=1; next } ($1 in ex)' "$all" "$STATE.tmp" > "$STATE"
        rm -f "$STATE.tmp"
        echo "$(date -u +%FT%TZ) OK $n arquivo(s) sincronizados" >> "$LOGF"
        echo "ok: $n arquivo(s) sincronizados (nada foi movido ou apagado localmente)."
    else
        echo "$(date -u +%FT%TZ) FALHA rc=$rc ($n arquivo(s) pendentes)" >> "$LOGF"
        case "$rc" in
            23) echo "aviso: parte dos arquivos não pôde ser lida (em uso pelo datalogger?)." >&2
                echo "       nada foi registrado; tudo será tentado de novo no próximo ciclo." >&2 ;;
            24) echo "aviso: um arquivo sumiu durante a transferência — o próximo ciclo se ajusta sozinho." >&2 ;;
            *)  echo "falha no envio (rsync rc=$rc) — o estado não mudou; será tentado de novo no próximo ciclo." >&2 ;;
        esac
    fi
    rm -f "$all" "$elig" "$tosend" "$lista"
    return "$rc"
}

reset_state() {  # força reenvio completo no próximo send (o servidor não duplica)
    rm -f "$STATE"
    echo "ok: estado de envio limpo — o próximo 'send' reenvia tudo."
    echo "(o servidor não duplica medições: a ingestão é idempotente.)"
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
    echo "estação:   $STATION_ID"
    if [ "$AUTH_MODE" = senha ]; then
        echo "servidor:  $HUB_HOST:$HUB_PORT (usuário+senha, rsync direto)"
    else
        echo "servidor:  $HUB_USER@$HUB_HOST:$HUB_PORT (chave SSH)"
        echo "chave:     $KEY_FILE"
    fi
    echo "dados:     $DATA_DIR (somente leitura: nada é movido/apagado)"
    all="$(mktemp)"; scan_all "$all"
    total="$(wc -l < "$all" | tr -d ' ')"
    [ -f "$STATE" ] || : > "$STATE"
    pend="$(awk -F'\t' 'FILENAME==ARGV[1] { st[$1]=$2; next } st[$1]!=$2 { n++ } END { print n+0 }' "$STATE" "$all")"
    rm -f "$all"
    echo "arquivos:  $total no total · $pend novo(s)/alterado(s) a sincronizar"
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
        echo " 7) Limpar estado de envio (força reenvio de tudo)"
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
            7) if ask_yn "limpar o estado e reenviar tudo no próximo envio?" n; then reset_state; fi ;;
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
  $(basename "$0") send         sincroniza agora (só o que é novo/alterado)
  $(basename "$0") schedule     agenda o envio periódico (cron)
  $(basename "$0") unschedule   remove o agendamento
  $(basename "$0") status       configuração, pendências e últimos envios
  $(basename "$0") reset        limpa o estado de envio (força reenvio de tudo)
  $(basename "$0")              menu interativo

a pasta local NUNCA é alterada (nada é movido nem apagado); o controle do que já
foi enviado fica em $STATE
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
    reset)      reset_state ;;
    ""|menu)    menu ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
esac
