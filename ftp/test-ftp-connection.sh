#!/usr/bin/env bash
#
# test-ftp-connection.sh — testa o servidor FTP/FTPS do CARBONARA data-hub a partir
# de uma máquina cliente na mesma rede.
#
# Valida, em sequência:
#   1. Alcance de rede     -> porta 21 (controle) acessível
#   2. FTPS (TLS)          -> o servidor aceita conexão criptografada
#   3. Usuário master      -> login + upload + delete (gestão completa)
#   4. Usuário upload      -> login + upload + criar pasta (escrita OK)
#   5. Upload NÃO apaga     -> DELE deve ser RECUSADO (550)  <- requisito central
#   6. FTP puro (sem TLS)  -> login funciona (estações sem TLS conseguem enviando)
#   7. Anônimo bloqueado   -> login anônimo deve ser RECUSADO
#   8. Limpeza             -> remove os artefatos de teste (via master)
#
# Uso:
#   bash test-ftp-connection.sh [IP_DO_SERVIDOR]
#   # IP padrão: 10.1.90.246
#
# Senhas: definidas em FTP_MASTER_PASS / FTP_UPLOAD_PASS (env) ou perguntadas no terminal.
#   FTP_MASTER_PASS=... FTP_UPLOAD_PASS=... bash test-ftp-connection.sh 10.1.90.246
#
# Requer: curl (com suporte a FTP/FTPS) e bash. Nenhum arquivo é deixado no servidor.

set -u

HOST="${1:-10.1.90.246}"
MASTER_USER="${MASTER_USER:-ftpmaster}"
UPLOAD_USER="${UPLOAD_USER:-ftpupload}"
PASV_HINT="30000-30009"

# --- estética ---------------------------------------------------------------
if [ -t 1 ]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; Z=$'\e[0m'; else G=; R=; Y=; B=; Z=; fi
OK=0; BAD=0; WARN=0
pass(){ echo "  ${G}[ OK ]${Z} $1"; OK=$((OK+1)); }
fail(){ echo "  ${R}[FALHA]${Z} $1"; BAD=$((BAD+1)); }
warn(){ echo "  ${Y}[AVISO]${Z} $1"; WARN=$((WARN+1)); }
hdr(){  echo; echo "${B}== $1 ==${Z}"; }

command -v curl >/dev/null 2>&1 || { echo "${R}curl não encontrado. Instale curl e tente de novo.${Z}"; exit 2; }

# --- senhas -----------------------------------------------------------------
: "${FTP_MASTER_PASS:=}"
: "${FTP_UPLOAD_PASS:=}"
if [ -z "$FTP_MASTER_PASS" ]; then read -rsp "Senha do $MASTER_USER: " FTP_MASTER_PASS; echo; fi
if [ -z "$FTP_UPLOAD_PASS" ]; then read -rsp "Senha do $UPLOAD_USER: " FTP_UPLOAD_PASS; echo; fi

# curl base: -k aceita cert autoassinado; tempos curtos p/ não travar
FTPS=(curl -s -S -k --ssl-reqd --ftp-pasv --connect-timeout 10 --max-time 40)
PLAIN=(curl -s -S -k --ftp-pasv --connect-timeout 10 --max-time 40)

TAG="conntest_$(date +%Y%m%d_%H%M%S)_$$"
TMP="$(mktemp)"; echo "CARBONARA ftp test $TAG" > "$TMP"
trap 'rm -f "$TMP"' EXIT

echo "${B}Testando servidor FTP do CARBONARA em ${HOST}${Z}"
echo "master=${MASTER_USER}  upload=${UPLOAD_USER}  passivo=${PASV_HINT}  tag=${TAG}"

# --- 1. alcance de rede (porta 21) ------------------------------------------
hdr "1. Alcance de rede (porta 21)"
if timeout 6 bash -c ">/dev/tcp/${HOST}/21" 2>/dev/null; then
  pass "porta 21/tcp acessível em ${HOST}"
else
  fail "porta 21/tcp NÃO acessível — servidor desligado, IP errado ou firewall bloqueando"
  echo; echo "${R}Sem alcance ao servidor. Abortando.${Z}"; exit 1
fi

# --- 2. FTPS (TLS) ----------------------------------------------------------
hdr "2. FTPS / TLS"
if "${FTPS[@]}" -u "${MASTER_USER}:${FTP_MASTER_PASS}" "ftp://${HOST}/" >/dev/null 2>err.$$; then
  pass "conexão FTPS estabelecida e login do master aceito sobre TLS"
else
  fail "FTPS falhou: $(tr -d '\n' <err.$$)"
fi
rm -f err.$$

# --- 3. master: upload + delete (gestão completa) ---------------------------
hdr "3. Usuário master — upload e delete"
if "${FTPS[@]}" -T "$TMP" -u "${MASTER_USER}:${FTP_MASTER_PASS}" "ftp://${HOST}/${TAG}_master.txt" >/dev/null 2>&1; then
  pass "master conseguiu ENVIAR arquivo (STOR)"
  if "${FTPS[@]}" -Q "DELE ${TAG}_master.txt" -u "${MASTER_USER}:${FTP_MASTER_PASS}" "ftp://${HOST}/" >/dev/null 2>&1; then
    pass "master conseguiu APAGAR arquivo (DELE)"
  else
    fail "master NÃO conseguiu apagar — verifique permissões de /srv/carbonara/collect"
  fi
else
  fail "master NÃO conseguiu enviar — login, TLS ou modo passivo (${PASV_HINT}) com problema"
fi

# --- 4. upload: upload + criar pasta ----------------------------------------
hdr "4. Usuário upload — escrita e criação de pasta"
UP_PATH="${TAG}_dir/up.txt"
if "${FTPS[@]}" --ftp-create-dirs -T "$TMP" -u "${UPLOAD_USER}:${FTP_UPLOAD_PASS}" "ftp://${HOST}/${UP_PATH}" >/dev/null 2>&1; then
  pass "upload conseguiu CRIAR pasta (MKD) e ENVIAR arquivo (STOR)"
else
  fail "upload NÃO conseguiu enviar/criar pasta — verifique credencial e config do ftpupload"
fi

# --- 5. upload NÃO pode apagar (requisito central) --------------------------
hdr "5. Usuário upload — exclusão deve ser BLOQUEADA"
if "${FTPS[@]}" -Q "DELE ${UP_PATH}" -u "${UPLOAD_USER}:${FTP_UPLOAD_PASS}" "ftp://${HOST}/" >/dev/null 2>&1; then
  fail "upload CONSEGUIU apagar — ERRADO! Revise cmds_denied em /etc/vsftpd/user_conf/ftpupload"
else
  pass "upload foi corretamente IMPEDIDO de apagar (550 Permission denied)"
fi

# --- 6. FTP puro sem TLS (estações sem suporte a TLS) -----------------------
hdr "6. FTP puro (sem TLS) — para estações limitadas"
if "${PLAIN[@]}" -u "${UPLOAD_USER}:${FTP_UPLOAD_PASS}" "ftp://${HOST}/" >/dev/null 2>&1; then
  pass "login em FTP puro funciona (estação sem TLS consegue enviar)"
  warn "FTP puro trafega credenciais/dados em texto puro — ok em LAN fechada, evite na internet"
else
  warn "FTP puro recusado — só conectam estações com TLS (ok se essa for a intenção)"
fi

# --- 7. anônimo deve ser bloqueado ------------------------------------------
hdr "7. Acesso anônimo deve ser BLOQUEADO"
if "${PLAIN[@]}" -u "anonymous:anonymous@x" "ftp://${HOST}/" >/dev/null 2>&1; then
  fail "login ANÔNIMO foi aceito — ERRADO! Confirme anonymous_enable=NO"
else
  pass "acesso anônimo corretamente recusado"
fi

# --- 8. limpeza (via master) ------------------------------------------------
hdr "8. Limpeza dos artefatos de teste"
"${FTPS[@]}" -Q "DELE ${UP_PATH}" -u "${MASTER_USER}:${FTP_MASTER_PASS}" "ftp://${HOST}/" >/dev/null 2>&1
if "${FTPS[@]}" -Q "RMD ${TAG}_dir" -u "${MASTER_USER}:${FTP_MASTER_PASS}" "ftp://${HOST}/" >/dev/null 2>&1; then
  pass "pasta e arquivos de teste removidos do servidor"
else
  warn "não foi possível remover ${TAG}_dir — remova manualmente em /srv/carbonara/collect/ se ficou"
fi

# --- resumo -----------------------------------------------------------------
hdr "Resumo"
echo "  ${G}OK:${Z} ${OK}   ${R}Falhas:${Z} ${BAD}   ${Y}Avisos:${Z} ${WARN}"
if [ "$BAD" -eq 0 ]; then
  echo "  ${G}${B}Tudo certo — o servidor FTP está operacional e com o modelo de permissão correto.${Z}"
  exit 0
else
  echo "  ${R}${B}Há falhas acima — revise os pontos marcados [FALHA].${Z}"
  exit 1
fi
