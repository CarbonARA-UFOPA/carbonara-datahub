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
#      → TESTE simples de conexão → pasta(s) local(is) a enviar (rsync) → tempo de
#      reciclagem (minutos) + ativação do envio automático.
#   2) Envio — `send` SINCRONIZA as pastas locais com o servidor: NUNCA move nem apaga
#      nada das pastas locais. A cada ciclo TODAS as pastas configuradas são varridas de
#      novo (novos arquivos são detectados automaticamente). O controle do que já foi
#      enviado é um estado local (tamanho+mtime por arquivo, em
#      ~/.config/carbonara-station/sent.state): só vai o que é novo ou mudou desde o
#      último envio. Idade mínima configurável evita arquivo ainda sendo escrito; o
#      rsync --partial retoma em link instável.
#   3) Agendamento — `schedule` instala o envio periódico no cron do usuário.
#
# Múltiplas pastas: cada pasta configurada é enviada para um sub-diretório próprio no
# servidor (o "rótulo", derivado do nome da pasta), evitando colisão de nomes entre
# pastas. Cada pasta pode ter FILTROS: tipos de arquivo a NÃO enviar (globs, ex.:
# *.tmp,*.bak) e subpastas a NÃO enviar (ex.: temp,cache).
#
# Uso:
#   ./carbonara-station.sh              # menu interativo (mostra as pastas no cabeçalho)
#   ./carbonara-station.sh setup        # configuração guiada (1ª vez ou reconfigurar)
#   ./carbonara-station.sh test         # testa a conexão com o servidor (dry-run)
#   ./carbonara-station.sh send         # envia agora os arquivos pendentes
#   ./carbonara-station.sh folders      # gerencia as pastas de sincronização
#   ./carbonara-station.sh schedule     # agenda o envio periódico (cron)
#   ./carbonara-station.sh unschedule   # remove o agendamento
#   ./carbonara-station.sh status       # configuração, pendências e últimos envios
#
# Configuração gravada em ~/.config/carbonara-station/ (config + folders, por usuário).
set -eu

CONF_DIR="${CARBONARA_STATION_HOME:-$HOME/.config/carbonara-station}"
CONF="$CONF_DIR/config"
FOLDERS="$CONF_DIR/folders"      # uma pasta por linha: PATH<TAB>LABEL<TAB>FEXC<TAB>DEXC
LOGF="$CONF_DIR/envios.log"
STATE="$CONF_DIR/sent.state"     # o que já foi enviado: rótulo/caminho<TAB>tamanho:mtime
CRON_TAG="# carbonara-station send"
HUB_DEFAULT_HOST="200.139.13.53" # servidor central do projeto (padrão sugerido no setup)

# Arrays paralelos com as pastas configuradas (preenchidos por load_folders).
F_PATH=(); F_LABEL=(); F_FEXC=(); F_DEXC=()

die()      { echo "erro: $*" >&2; exit 1; }
self_path(){ readlink -f "$0" 2>/dev/null || echo "$0"; }

# O cron congela o caminho do script no momento do agendamento — se o setup rodou de um
# pendrive/Downloads e o arquivo sair do lugar, o envio para em silêncio. Por isso o
# agendamento instala a cópia em execução num caminho ESTÁVEL e agenda a cópia instalada
# (mesmo padrão do rsync-hub no servidor). Não requer administrador: fica no perfil.
INSTALL_BIN="${CARBONARA_STATION_BIN:-$HOME/.local/bin/carbonara-station.sh}"

install_self() {  # instala a cópia em execução em $INSTALL_BIN e ecoa o caminho a agendar
    self="$(self_path)"
    [ "$self" = "$INSTALL_BIN" ] && { printf '%s\n' "$self"; return 0; }
    dir="$(dirname "$INSTALL_BIN")"
    if ! mkdir -p "$dir" 2>/dev/null || [ ! -w "$dir" ]; then
        echo "aviso: sem permissão de escrita em $dir — o agendamento vai apontar para a" >&2
        echo "cópia atual ($self). NÃO mova nem apague este arquivo, ou o envio para." >&2
        printf '%s\n' "$self"; return 0
    fi
    if ! cmp -s "$self" "$INSTALL_BIN" 2>/dev/null; then
        if cp "$self" "$INSTALL_BIN.tmp" 2>/dev/null \
           && chmod 755 "$INSTALL_BIN.tmp" 2>/dev/null \
           && mv "$INSTALL_BIN.tmp" "$INSTALL_BIN" 2>/dev/null; then
            echo "instalado em $INSTALL_BIN — o agendamento usa esta cópia (a original pode" >&2
            echo "ser movida ou apagada sem quebrar o envio)." >&2
        else
            rm -f "$INSTALL_BIN.tmp" 2>/dev/null || true
            echo "aviso: não consegui gravar $INSTALL_BIN — o agendamento vai apontar para a" >&2
            echo "cópia atual ($self). NÃO mova nem apague este arquivo, ou o envio para." >&2
            printf '%s\n' "$self"; return 0
        fi
    fi
    printf '%s\n' "$INSTALL_BIN"
}

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

# --- Pastas configuradas -------------------------------------------------------------
mk_label() { # $1=caminho → rótulo saneado e ÚNICO (não vazio) p/ o sub-dir no servidor
    base="$(basename "$1")"
    lbl="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '_')"
    lbl="${lbl##_}"; lbl="${lbl%%_}"
    [ -n "$lbl" ] || lbl="pasta"
    base_lbl="$lbl"; i=2
    while label_exists "$lbl"; do lbl="${base_lbl}-$i"; i=$((i + 1)); done
    printf '%s' "$lbl"
}
label_exists() { # $1=rótulo → rc 0 se já usado por alguma pasta carregada
    j=0
    while [ "$j" -lt "${#F_LABEL[@]}" ]; do
        [ "${F_LABEL[$j]}" = "$1" ] && return 0
        j=$((j + 1))
    done
    return 1
}

load_folders() { # lê $FOLDERS para os arrays F_*
    F_PATH=(); F_LABEL=(); F_FEXC=(); F_DEXC=()
    [ -f "$FOLDERS" ] || return 0
    while IFS="$(printf '\t')" read -r p l fe de || [ -n "${p:-}" ]; do
        [ -n "${p:-}" ] || continue
        case "$p" in \#*) continue ;; esac
        F_PATH+=("$p"); F_LABEL+=("${l:-}"); F_FEXC+=("${fe:-}"); F_DEXC+=("${de:-}")
    done < "$FOLDERS"
}

folders_save() { # grava os arrays F_* de volta em $FOLDERS
    mkdir -p "$CONF_DIR"
    : > "$FOLDERS"
    k=0
    while [ "$k" -lt "${#F_PATH[@]}" ]; do
        printf '%s\t%s\t%s\t%s\n' "${F_PATH[$k]}" "${F_LABEL[$k]}" "${F_FEXC[$k]}" "${F_DEXC[$k]}" >> "$FOLDERS"
        k=$((k + 1))
    done
    chmod 600 "$FOLDERS" 2>/dev/null || true
}

print_folders_summary() { # $1=host (opcional) $2=estação (opcional) — pastas, destino e filtros
    load_folders
    if [ "${#F_PATH[@]}" -eq 0 ]; then
        echo "  (nenhuma pasta configurada — rode 'setup' ou adicione em 'folders')"
        return
    fi
    k=0
    while [ "$k" -lt "${#F_PATH[@]}" ]; do
        # Com host conhecido, mostra o DESTINO completo (IP + pasta da estação + rótulo
        # desta pasta); sem host, só o rótulo da subpasta no servidor.
        if [ -n "${1:-}" ]; then
            dst="$1:/${2:+$2/}${F_LABEL[$k]}/"
        else
            dst="subpasta no servidor: [${F_LABEL[$k]}]"
        fi
        extra=""
        [ -n "${F_FEXC[$k]}" ] && extra="$extra  · exceto arquivos: ${F_FEXC[$k]}"
        [ -n "${F_DEXC[$k]}" ] && extra="$extra  · sem subpastas: ${F_DEXC[$k]}"
        printf '  %d) %s  →  %s%s\n' "$((k + 1))" "${F_PATH[$k]}" "$dst" "$extra"
        k=$((k + 1))
    done
}

load_conf() {
    [ -r "$CONF" ] || die "estação não configurada — rode: $(basename "$0") setup"
    . "$CONF"
    : "${STATION_ID:?configuração incompleta — rode setup de novo}"
    : "${HUB_HOST:?}"
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
    # Migração: configs antigas guardavam UMA pasta em DATA_DIR. Se ainda não há arquivo
    # de pastas, cria uma entrada a partir dela (o rótulo é derivado do nome da pasta).
    if [ ! -f "$FOLDERS" ] && [ -n "${DATA_DIR:-}" ]; then
        F_PATH=(); F_LABEL=(); F_FEXC=(); F_DEXC=()
        lbl="$(mk_label "$DATA_DIR")"
        F_PATH+=("$DATA_DIR"); F_LABEL+=("$lbl"); F_FEXC+=(""); F_DEXC+=("")
        folders_save
    fi
    load_folders
    [ "${#F_PATH[@]}" -gt 0 ] || die "nenhuma pasta configurada — rode: $(basename "$0") setup (ou 'folders')"
}

rsync_dest() {  # destino BASE (sem rótulo) conforme o modo: módulo rsyncd OU conta ssh
    if [ "$AUTH_MODE" = senha ]; then
        echo "rsync://$STATION_ID@$HUB_HOST:$HUB_PORT/$STATION_ID/"
    else
        echo "$HUB_USER@$HUB_HOST:/"
    fi
}

sig_of() { # tamanho:mtime — a "assinatura" que detecta arquivo novo/alterado
    stat -c '%s:%Y' "$1" 2>/dev/null || stat -f '%z:%m' "$1"
}

# Varre UMA pasta aplicando os filtros e emite, por arquivo elegível:
#   rótulo/caminho-relativo <TAB> assinatura <TAB> assentado(1/0)
# ($5=cutoff em epoch: assentado = mtime <= cutoff, ou seja, mais velho que MIN_AGE)
scan_folder() {
    local path="$1" fexc="$2" dexc="$3" label="$4" cutoff="$5"
    [ -d "$path" ] || return 0
    ( cd "$path" 2>/dev/null || exit 0
      find . -type f -not -path '*/.*' -not -name '.*' 2>/dev/null | sed 's|^\./||' | sort \
      | while IFS= read -r rel; do
            skip=0
            oIFS="$IFS"; set -f
            # subpastas excluídas: casa se o rótulo aparece como componente do caminho
            if [ -n "$dexc" ]; then
                IFS=','
                for d in $dexc; do
                    [ -n "$d" ] || continue
                    case "/$rel/" in */"$d"/*) skip=1; break ;; esac
                done
            fi
            # tipos de arquivo excluídos: glob contra o NOME do arquivo
            if [ "$skip" = 0 ] && [ -n "$fexc" ]; then
                base="${rel##*/}"; IFS=','
                for g in $fexc; do
                    [ -n "$g" ] || continue
                    case "$base" in $g) skip=1; break ;; esac
                done
            fi
            IFS="$oIFS"; set +f
            [ "$skip" = 0 ] || continue
            sig="$(sig_of "$rel")"
            mtime="${sig##*:}"
            if [ -n "$mtime" ] && [ "$mtime" -le "$cutoff" ]; then settled=1; else settled=0; fi
            printf '%s/%s\t%s\t%s\n' "$label" "$rel" "$sig" "$settled"
        done )
}

# Varre TODAS as pastas configuradas (re-lê o disco a cada chamada — é isto que faz
# arquivos novos serem detectados em todo ciclo). Emite as linhas de scan_folder.
scan_all() {
    local now cutoff k
    now="$(date +%s)"; cutoff="$((now - MIN_AGE * 60))"
    k=0
    while [ "$k" -lt "${#F_PATH[@]}" ]; do
        scan_folder "${F_PATH[$k]}" "${F_FEXC[$k]}" "${F_DEXC[$k]}" "${F_LABEL[$k]}" "$cutoff"
        k=$((k + 1))
    done
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

# Teste de conexão que NÃO depende das pastas de dados: dry-run a partir de uma pasta
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
        HUB_HOST="$(ask "IP (ou hostname) do servidor central" "${HUB_HOST:-$HUB_DEFAULT_HOST}")"
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

    # 5. Pasta(s) local(is) que serão enviadas (rsync)
    load_folders
    keep_folders=0
    if [ "${#F_PATH[@]}" -gt 0 ]; then
        echo "Pastas já configuradas:"
        print_folders_summary
        if ask_yn "Manter essas pastas? (responda 'n' para reconfigurar a pasta principal)" s; then
            keep_folders=1
        fi
    fi
    if [ "$keep_folders" = 0 ]; then
        F_PATH=(); F_LABEL=(); F_FEXC=(); F_DEXC=()
        DATA_DIR="${DATA_DIR:-}"
        while :; do
            DATA_DIR="$(ask "Pasta LOCAL principal a enviar por rsync" "${DATA_DIR:-$HOME/carbonara-dados}")"
            case "$DATA_DIR" in /*) : ;; *) echo "  use um caminho absoluto."; continue ;; esac
            [ -d "$DATA_DIR" ] && break
            if ask_yn "  a pasta não existe — criar $DATA_DIR?" s; then mkdir -p "$DATA_DIR"; break; fi
        done
        echo "Filtros desta pasta (opcionais — Enter deixa em branco):"
        fe="$(ask "  Tipos de arquivo a NÃO enviar (globs por vírgula, ex.: *.tmp,*.bak)" "")"
        de="$(ask "  Subpastas a NÃO enviar (por vírgula, ex.: temp,cache)" "")"
        lbl="$(mk_label "$DATA_DIR")"
        F_PATH+=("$DATA_DIR"); F_LABEL+=("$lbl"); F_FEXC+=("$fe"); F_DEXC+=("$de")
        folders_save
        echo "  (dá para adicionar mais pastas depois: menu → 'Gerenciar pastas' ou '$(basename "$0") folders'.)"
    fi
    while :; do
        MIN_AGE="$(ask "Idade mínima (minutos) para um arquivo ser considerado pronto" "${MIN_AGE:-1}")"
        echo "$MIN_AGE" | grep -Eq '^[0-9]+$' && break
        echo "  informe um número inteiro de minutos."
    done
    echo "  (as pastas locais NUNCA são alteradas: nada é movido nem apagado — o envio só"
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
      echo "MIN_AGE=$MIN_AGE"
      echo "SEND_EVERY=$SEND_EVERY"; } > "$CONF"
    chmod 600 "$CONF"
    echo
    echo "ok: configuração gravada em $CONF (pastas em $FOLDERS)."

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
# Sincronização pura: NADA é movido ou apagado das pastas locais. A cada ciclo TODAS as
# pastas são varridas de novo (novos arquivos entram automaticamente). O que já foi
# enviado é controlado pelo estado local ($STATE, chave rótulo/caminho → tamanho:mtime)
# — só vai o que é novo ou mudou; arquivos que crescem (ex.: .dat de datalogger) são
# reenviados por inteiro e o servidor não duplica medições (ingestão idempotente).
send() {
    load_conf
    mkdir -p "$CONF_DIR"
    exec 9>"$CONF_DIR/.lock"
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        echo "outro envio já está em andamento — saindo."
        return 0
    fi
    [ -f "$STATE" ] || : > "$STATE"
    # Carimbo do ciclo: o 'status' compara com o intervalo agendado para denunciar um
    # agendamento que existe mas não está executando (script movido, cron parado, …).
    date +%s > "$CONF_DIR/.last-send" 2>/dev/null || true
    # Sinal de vida ANTES de tudo: mesmo sem dados novos o servidor sabe que a
    # estação está viva (e qual é o seu ciclo). Falha aqui não interrompe o envio.
    if ! send_heartbeat; then
        echo "aviso: não consegui enviar o sinal de vida ao servidor (sem rede?)." >&2
    fi
    scan="$(mktemp)"; all="$(mktemp)"; tosend="$(mktemp)"; sentok="$(mktemp)"
    scan_all > "$scan"                     # rótulo/caminho \t sig \t assentado
    cut -f1,2 "$scan" > "$all"             # inventário atual (chave \t sig)
    # A enviar: assentados cuja assinatura difere do estado (novos ou alterados).
    awk -F'\t' '
        FILENAME==ARGV[1] { st[$1]=$2; next }
        $3=="1" && st[$1] != $2 { print $1"\t"$2 }
    ' "$STATE" "$scan" > "$tosend"
    n="$(wc -l < "$tosend" | tr -d ' ')"
    if [ "$n" = 0 ]; then
        echo "nada novo a sincronizar (arquivos com menos de $MIN_AGE min não contam)."
        rm -f "$scan" "$all" "$tosend" "$sentok"; return 0
    fi
    echo "sincronizando $n arquivo(s) novo(s)/alterado(s) com ${HUB_HOST}…"
    worst=0
    k=0
    while [ "$k" -lt "${#F_PATH[@]}" ]; do
        label="${F_LABEL[$k]}"; path="${F_PATH[$k]}"
        k=$((k + 1))
        [ -d "$path" ] || continue
        lista="$(mktemp)"
        # arquivos deste rótulo, sem o prefixo — caminhos relativos à própria pasta
        awk -F'\t' -v p="$label/" 'substr($1,1,length(p))==p { print substr($1, length(p)+1) }' \
            "$tosend" > "$lista"
        if [ ! -s "$lista" ]; then rm -f "$lista"; continue; fi
        cnt="$(wc -l < "$lista" | tr -d ' ')"
        dest="$(rsync_dest)$label/"
        echo "  [$label] $cnt arquivo(s) de ${path}…"
        rc=0
        # -rltz (sem -p/-o/-g) + --chmod: os arquivos chegam 644/755 no servidor —
        # legíveis pelos usuários locais do host, independentemente das permissões aqui.
        # --partial-dir: uma transferência cancelada/interrompida deixa o pedaço num
        # diretório OCULTO no servidor (a ingestão nunca o vê) e o próximo envio retoma
        # dali — nunca fica um arquivo truncado com nome real no incoming/.
        if [ "$AUTH_MODE" = senha ]; then
            ( cd "$path" && RSYNC_PASSWORD="$PASSWORD" rsync -rltz --chmod=D755,F644 \
                --partial-dir=.rsync-parcial --timeout=120 --files-from="$lista" \
                ./ "$dest" ) || rc=$?
        else
            ( cd "$path" && rsync -rltz --chmod=D755,F644 --partial-dir=.rsync-parcial \
                --timeout=120 --files-from="$lista" -e "$(ssh_cmd)" ./ "$dest" ) || rc=$?
        fi
        if [ "$rc" = 0 ]; then
            # anota como enviados (chave \t sig) os arquivos deste rótulo
            awk -F'\t' -v p="$label/" 'substr($1,1,length(p))==p' "$tosend" >> "$sentok"
        else
            [ "$rc" -gt "$worst" ] && worst="$rc"
            echo "  [$label] falha (rsync rc=$rc) — será tentado de novo no próximo ciclo." >&2
        fi
        rm -f "$lista"
    done
    # Atualiza o estado: remove as chaves reenviadas, junta as recém-enviadas e poda
    # para só conter arquivos que ainda existem localmente. As pastas locais ficam intocadas.
    if [ -s "$sentok" ]; then
        awk -F'\t' 'NR==FNR { u[$1]=1; next } !($1 in u)' "$sentok" "$STATE" > "$STATE.tmp" || true
        cat "$sentok" >> "$STATE.tmp"
        awk -F'\t' 'NR==FNR { ex[$1]=1; next } ($1 in ex)' "$all" "$STATE.tmp" > "$STATE"
        rm -f "$STATE.tmp"
        ok_n="$(wc -l < "$sentok" | tr -d ' ')"
        echo "$(date -u +%FT%TZ) OK $ok_n arquivo(s) sincronizados" >> "$LOGF"
        echo "ok: $ok_n arquivo(s) sincronizados (nada foi movido ou apagado localmente)."
    else
        # nada teve sucesso — ainda assim poda o estado para arquivos removidos localmente
        awk -F'\t' 'NR==FNR { ex[$1]=1; next } ($1 in ex)' "$all" "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE" || rm -f "$STATE.tmp"
    fi
    if [ "$worst" != 0 ]; then
        echo "$(date -u +%FT%TZ) FALHA rc=$worst (parte dos arquivos pendente)" >> "$LOGF"
        case "$worst" in
            23) echo "aviso: parte dos arquivos não pôde ser lida (em uso pelo datalogger?)." >&2
                echo "       o que falhou não foi registrado; será tentado de novo no próximo ciclo." >&2 ;;
            24) echo "aviso: um arquivo sumiu durante a transferência — o próximo ciclo se ajusta sozinho." >&2 ;;
            *)  echo "falha em parte do envio (rsync rc=$worst) — o estado não mudou p/ o que falhou; nova tentativa no próximo ciclo." >&2 ;;
        esac
    fi
    rm -f "$scan" "$all" "$tosend" "$sentok"
    return "$worst"
}

reset_state() {  # força reenvio completo no próximo send (o servidor não duplica)
    rm -f "$STATE"
    echo "ok: estado de envio limpo — o próximo 'send' reenvia tudo."
    echo "(o servidor não duplica medições: a ingestão é idempotente.)"
}

# --- Gerência de pastas ---------------------------------------------------------------
folders_add() {
    load_folders
    p=""
    while :; do
        p="$(ask "Caminho ABSOLUTO da pasta a incluir (Enter cancela)" "")"
        [ -n "$p" ] || { echo "cancelado."; return 0; }
        case "$p" in /*) : ;; *) echo "  use um caminho absoluto."; continue ;; esac
        if [ ! -d "$p" ]; then
            if ask_yn "  a pasta não existe — criar $p?" n; then mkdir -p "$p"; else continue; fi
        fi
        dup=0; j=0
        while [ "$j" -lt "${#F_PATH[@]}" ]; do [ "${F_PATH[$j]}" = "$p" ] && dup=1; j=$((j + 1)); done
        [ "$dup" = 1 ] && { echo "  essa pasta já está na lista."; return 0; }
        break
    done
    echo "Filtros desta pasta (opcionais — Enter deixa em branco):"
    fe="$(ask "  Tipos de arquivo a NÃO enviar (globs por vírgula, ex.: *.tmp,*.bak)" "")"
    de="$(ask "  Subpastas a NÃO enviar (por vírgula, ex.: temp,cache)" "")"
    lbl="$(mk_label "$p")"
    F_PATH+=("$p"); F_LABEL+=("$lbl"); F_FEXC+=("$fe"); F_DEXC+=("$de")
    folders_save
    echo "ok: pasta adicionada (sub-diretório no servidor: [$lbl])."
}

folders_remove() {
    load_folders
    [ "${#F_PATH[@]}" -gt 0 ] || { echo "não há pastas configuradas."; return 0; }
    print_folders_summary
    sel="$(ask "Número da pasta a remover (Enter cancela)" "")"
    [ -n "$sel" ] || return 0
    echo "$sel" | grep -Eq '^[0-9]+$' || { echo "  número inválido."; return 0; }
    idx=$((sel - 1))
    { [ "$idx" -ge 0 ] && [ "$idx" -lt "${#F_PATH[@]}" ]; } || { echo "  fora da faixa."; return 0; }
    if [ "${#F_PATH[@]}" -le 1 ]; then
        ask_yn "essa é a única pasta — tirar da lista? (a estação ficará sem enviar dados)" n || return 0
    fi
    NP=(); NL=(); NFE=(); NDE=(); j=0
    while [ "$j" -lt "${#F_PATH[@]}" ]; do
        if [ "$j" != "$idx" ]; then
            NP+=("${F_PATH[$j]}"); NL+=("${F_LABEL[$j]}"); NFE+=("${F_FEXC[$j]}"); NDE+=("${F_DEXC[$j]}")
        fi
        j=$((j + 1))
    done
    if [ "${#NP[@]}" -gt 0 ]; then
        F_PATH=("${NP[@]}"); F_LABEL=("${NL[@]}"); F_FEXC=("${NFE[@]}"); F_DEXC=("${NDE[@]}")
    else
        F_PATH=(); F_LABEL=(); F_FEXC=(); F_DEXC=()
    fi
    folders_save
    echo "ok: pasta retirada da LISTA de sincronização — os arquivos no disco continuam"
    echo "lá, nada foi apagado. (o estado de envio dela é limpo no próximo 'send'.)"
}

folders_edit() {
    load_folders
    [ "${#F_PATH[@]}" -gt 0 ] || { echo "não há pastas configuradas."; return 0; }
    print_folders_summary
    sel="$(ask "Número da pasta cujos filtros deseja editar (Enter cancela)" "")"
    [ -n "$sel" ] || return 0
    echo "$sel" | grep -Eq '^[0-9]+$' || { echo "  número inválido."; return 0; }
    idx=$((sel - 1))
    { [ "$idx" -ge 0 ] && [ "$idx" -lt "${#F_PATH[@]}" ]; } || { echo "  fora da faixa."; return 0; }
    echo "Editando filtros de: ${F_PATH[$idx]}"
    echo "(Enter mantém o valor atual; digite um espaço e Enter para LIMPAR o filtro)"
    fe="$(ask "  Tipos de arquivo a NÃO enviar (globs por vírgula)" "${F_FEXC[$idx]}")"
    de="$(ask "  Subpastas a NÃO enviar (por vírgula)" "${F_DEXC[$idx]}")"
    F_FEXC[$idx]="$(printf '%s' "$fe" | tr -d ' ')"
    F_DEXC[$idx]="$(printf '%s' "$de" | tr -d ' ')"
    folders_save
    echo "ok: filtros atualizados."
}

folders_menu() {
    while :; do
        echo
        echo "----- Pastas de sincronização -----"
        print_folders_summary
        echo " 1) Adicionar pasta"
        echo " 2) Remover pasta"
        echo " 3) Editar filtros (tipos de arquivo / subpastas) de uma pasta"
        echo " 0) Voltar"
        printf "opção> "
        read -r op || return 0
        case "$op" in
            1) ( folders_add ) || true ;;
            2) ( folders_remove ) || true ;;
            3) ( folders_edit ) || true ;;
            0|q|"") return 0 ;;
            *) echo "opção inválida" ;;
        esac
    done
}

# --- Agendamento (cron) ---------------------------------------------------------------
schedule_install() {  # $1 = intervalo em minutos (instala/substitui a entrada no cron)
    iv="$1"
    # Verificação prévia: aqui nada exige administrador — o agendamento vai para o cron
    # DO USUÁRIO e a cópia estável para o perfil ($INSTALL_BIN). Só o crontab é requisito.
    command -v crontab >/dev/null 2>&1 || die "crontab não encontrado nesta máquina"
    self="$(install_self)"
    ( crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true
      echo "*/$iv * * * * $self send >> $LOGF 2>&1 $CRON_TAG" ) | crontab -
    echo "ok: envio automático ativo a cada $iv min (cron do usuário $(id -un))."
    echo "    script agendado: $self"
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

# Remove a configuração/conexão desta estação E o agendamento — para começar do zero.
# NÃO toca nas pastas de DADOS nem no arquivo de CHAVE (que ficam fora do CONF_DIR).
purge_config() {
    echo "Isto apaga APENAS a CONFIGURAÇÃO desta estação: o endereço do servidor, a"
    echo "LISTA de quais pastas enviar (não as pastas!), os filtros, o agendamento e o"
    echo "histórico local (estado + log)."
    echo
    echo ">>> As suas PASTAS DE DADOS NÃO são apagadas — em NENHUMA hipótese. <<<"
    echo "    O cliente só LÊ seus arquivos; remover a configuração apenas faz a estação"
    echo "    'esquecer' para onde e o que enviar. Seus dados e a chave SSH ficam intactos."
    echo
    ask_yn "Remover a configuração?" n || { echo "cancelado."; return 0; }
    if command -v crontab >/dev/null 2>&1; then
        ( crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true ) | crontab - 2>/dev/null || true
        echo "  agendamento (cron) removido."
    fi
    rm -f "$CONF" "$FOLDERS" "$STATE" "$LOGF" \
          "$CONF_DIR/.carbonara-status" "$CONF_DIR/.last-send" "$CONF_DIR/.lock"
    echo "ok: configuração e agendamento removidos — rode 'setup' para configurar de novo."
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
    echo "pastas sincronizadas (somente leitura: nada é movido/apagado):"
    print_folders_summary "$HUB_HOST" "$STATION_ID"
    scan="$(mktemp)"; scan_all > "$scan"
    total="$(wc -l < "$scan" | tr -d ' ')"
    [ -f "$STATE" ] || : > "$STATE"
    pend="$(awk -F'\t' 'FILENAME==ARGV[1] { st[$1]=$2; next } $3=="1" && st[$1]!=$2 { n++ } END { print n+0 }' "$STATE" "$scan")"
    rm -f "$scan"
    echo "arquivos:  $total no total · $pend novo(s)/alterado(s) a sincronizar"
    if crontab -l 2>/dev/null | grep -qF "$CRON_TAG"; then
        line="$(crontab -l 2>/dev/null | grep -F "$CRON_TAG" | head -n 1)"
        echo "agenda:    $(echo "$line" | awk '{print $1}') (cron)"
        # Vigilância ativa: o caminho agendado ainda existe? E o último ciclo (carimbo
        # gravado pelo 'send') aconteceu dentro do esperado (2 intervalos + folga)?
        sched="$(echo "$line" | awk '{print $6}')"
        if [ -n "$sched" ] && [ ! -x "$sched" ]; then
            echo "  ATENÇÃO: o script agendado sumiu ($sched) — o envio automático está"
            echo "  QUEBRADO. Rode 'schedule' de novo para reinstalar e reagendar."
        fi
        iv="$(echo "$line" | awk '{print $1}' | tr -cd '0-9')"
        { [ -n "$iv" ] && [ "$iv" -gt 0 ]; } || iv=60
        if [ -f "$CONF_DIR/.last-send" ]; then
            last="$(cat "$CONF_DIR/.last-send" 2>/dev/null || echo 0)"
            case "$last" in ''|*[!0-9]*) last=0 ;; esac
            age=$(( $(date +%s) - last ))
            if [ "$age" -gt $(( iv * 120 + 120 )) ]; then
                echo "  ATENÇÃO: último ciclo de envio há $(( age / 60 )) min (esperado a cada"
                echo "  $iv min) — o agendamento pode não estar executando. Veja: tail $LOGF"
            else
                echo "  último ciclo: há $(( age / 60 )) min (dentro do esperado)."
            fi
        else
            echo "  (nenhum ciclo executado ainda — o primeiro ocorre em até $iv min.)"
        fi
    else
        echo "agenda:    (não agendado — use 'schedule')"
    fi
    echo "últimos envios:"
    if [ -f "$LOGF" ]; then tail -n 5 "$LOGF" | sed 's/^/  /'; else echo "  (nenhum envio registrado)"; fi
}

# --- Menu interativo -------------------------------------------------------------------
menu_header() {  # cabeçalho: identifica a estação e mostra as pastas já sincronizadas
    if [ -r "$CONF" ]; then
        ( . "$CONF" 2>/dev/null || true
          printf 'estação: %s   servidor: %s:%s\n' "${STATION_ID:-?}" "${HUB_HOST:-?}" "${HUB_PORT:-?}"
          echo "pastas sincronizadas:"
          print_folders_summary "${HUB_HOST:-}" "${STATION_ID:-}" )
    else
        echo "estação: (ainda não configurada — use a opção 1)"
        echo "pastas sincronizadas:"
        print_folders_summary
    fi
}

menu() {
    while true; do
        echo
        echo "===== carbonara-station — estação (cliente Linux) ====="
        menu_header
        echo "-------------------------------------------------------"
        echo " 1) Configurar esta estação (guiado)"
        echo " 2) Testar conexão com o servidor"
        echo " 3) Enviar agora"
        echo " 4) Gerenciar pastas de sincronização (incluir/remover/filtrar)"
        echo " 5) Agendar envio periódico"
        echo " 6) Remover agendamento"
        echo " 7) Status"
        echo " 8) Limpar estado de envio (força reenvio de tudo)"
        echo " 9) Remover configuração/conexão desta estação (e o agendamento)"
        echo " 0) Sair"
        printf "opção> "
        read -r op || exit 0
        case "$op" in
            1) ( setup ) || true ;;
            2) ( test_conn ) || true ;;
            3) ( send ) || true ;;
            4) folders_menu ;;
            5) ( schedule ) || true ;;
            6) ( unschedule ) || true ;;
            7) ( status ) || true ;;
            8) if ask_yn "limpar o estado e reenviar tudo no próximo envio?" n; then reset_state; fi ;;
            9) ( purge_config ) || true ;;
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
  $(basename "$0") folders      gerencia as pastas (incluir/remover/filtrar)
  $(basename "$0") schedule     agenda o envio periódico (cron)
  $(basename "$0") unschedule   remove o agendamento
  $(basename "$0") status       configuração, pendências e últimos envios
  $(basename "$0") reset        limpa o estado de envio (força reenvio de tudo)
  $(basename "$0") purge        remove a configuração/conexão e o agendamento (zera)
  $(basename "$0")              menu interativo

várias pastas são suportadas — cada uma vai para um sub-diretório próprio no servidor
e pode ter filtros (tipos de arquivo e subpastas a não enviar).
as pastas locais NUNCA são alteradas (nada é movido nem apagado); o controle do que já
foi enviado fica em $STATE
configuração por usuário em $CONF_DIR
EOF
}

case "${1:-}" in
    setup)      setup ;;
    test)       test_conn ;;
    send)       send ;;
    folders)    folders_menu ;;
    schedule)   schedule ;;
    unschedule) unschedule ;;
    status)     status ;;
    reset)      reset_state ;;
    purge)      purge_config ;;
    ""|menu)    menu ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
esac
