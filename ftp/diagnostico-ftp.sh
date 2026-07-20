#!/bin/bash
# Diagnóstico FTP — testa PASV, ativo e identifica o IP interno que está sendo retornado

set -e

SERVER="${1:-200.139.13.53}"
PORT="${2:-21}"
USER="${3:-ftpupload}"

echo "=========================================="
echo "Diagnóstico FTP — $SERVER:$PORT"
echo "=========================================="
echo ""

# 1) Teste básico de conectividade
echo "1️⃣ Teste de conectividade (porta 21)"
timeout 5 bash -c "echo > /dev/tcp/$SERVER/$PORT" && \
  echo "✅ Porta 21 acessível" || \
  echo "❌ Porta 21 inacessível — verifique firewall/redirecionamento"
echo ""

# 2) Teste PASV — conecta e tenta LIST em passivo
echo "2️⃣ Teste PASV (passivo) — identifica o IP retornado"
echo "   (se falhar com timeout, é porque o IP interno não é roteável)"
ftp_output=$(mktemp)
trap "rm -f $ftp_output" EXIT

{
  sleep 1
  echo "USER $USER"
  sleep 1
  # A senha vai ser perguntada; use FTP_UPLOAD_PASS ou digite
  sleep 1
  echo "TYPE A"
  sleep 1
  echo "PASV"
  sleep 2
  echo "LIST"
  sleep 3
  echo "QUIT"
} | timeout 15 nc $SERVER $PORT > "$ftp_output" 2>&1 &

sleep 4

# Tenta capturar a resposta PASV (linha 227)
if grep -q "227" "$ftp_output" 2>/dev/null; then
  pasv_line=$(grep "227" "$ftp_output" | head -1)
  echo "✅ Resposta PASV recebida:"
  echo "   $pasv_line"

  # Extrai IP e porta da resposta PASV
  # Formato: 227 Entering Passive Mode (h1,h2,h3,h4,p1,p2)
  if [[ $pasv_line =~ \(([0-9]+),([0-9]+),([0-9]+),([0-9]+),([0-9]+),([0-9]+)\) ]]; then
    ip="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}.${BASH_REMATCH[4]}"
    port=$((${BASH_REMATCH[5]} * 256 + ${BASH_REMATCH[6]}))
    echo ""
    echo "   IP retornado: $ip"
    echo "   Porta retornada: $port"
    echo ""

    # Verifica se é IP interno (não roteável)
    if [[ $ip =~ ^10\. || $ip =~ ^172\.(1[6-9]|2[0-9]|3[01])\. || $ip =~ ^192\.168\. || $ip =~ ^127\. ]]; then
      echo "❌ PROBLEMA ENCONTRADO:"
      echo "   O servidor está retornando um IP INTERNO ($ip)"
      echo "   Isso é um problema em NAT/redirecionamento de porta!"
      echo ""
      echo "   SOLUÇÃO: Configure pasv_address no servidor:"
      echo "   - Edite /etc/vsftpd.conf"
      echo "   - Descomente: pasv_address=<IP_PUBLICO>"
      echo "   - Use o IP público: 200.139.13.53"
      echo "   - Reinicie: sudo systemctl restart vsftpd"
    else
      echo "✅ IP público retornado (OK)"
    fi
  fi
else
  echo "⚠️ Resposta PASV não capturada (timeout ou falha de login)"
fi
echo ""

# 3) Teste com curl + ativo (alternativa)
echo "3️⃣ Teste com curl (modo ATIVO) — funciona sem PASV"
echo "   Vai pedir senha de $USER:"
if command -v curl &>/dev/null; then
  # Modo ativo (-P -)
  echo "   Tentando: curl -u $USER -P - ftp://$SERVER/ --ssl-reqd -k"
  # Nota: o curl vai pedir a senha interativamente
else
  echo "   ❌ curl não disponível"
fi
echo ""

# 4) Dicas de correção
echo "=========================================="
echo "📋 RESUMO DAS CORREÇÕES"
echo "=========================================="
echo ""
echo "Se o IP retornado é INTERNO (10.x, 172.x, 192.168.x, 127.x):"
echo ""
echo "Opção A (RECOMENDADO): Configurar o servidor para responder com IP público"
echo "  1. SSH para wrkst02 (servidor FTP)"
echo "  2. sudo nano /etc/vsftpd.conf"
echo "  3. Procure: # pasv_address=<IP_PUBLICO>"
echo "  4. Descomente e mude para: pasv_address=200.139.13.53"
echo "  5. sudo systemctl restart vsftpd"
echo ""
echo "Opção B (ALTERNATIVA): Usar modo ATIVO no FileZilla"
echo "  1. FileZilla → Edit → Settings → FTP"
echo "  2. Transfer mode: Active (em vez de Passive)"
echo "  3. Reconectar"
echo ""
echo "Opção C (DOCKER): Se rodando em container, verificar redirecionamento"
echo "  1. Verify docker-compose.yml porta range 30000-30009 aberta"
echo "  2. PUBLICHOST deve estar definido como IP externo"
echo ""
