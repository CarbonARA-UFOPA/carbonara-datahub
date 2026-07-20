#!/bin/bash
# Execute ESTE script NO SERVIDOR FTP (wrkst02)
# Vai testar loopback (127.0.0.1) e mostrar qual IP está sendo retornado em PASV

USER="${1:-ftpmaster}"
PASS="${2:-}"

if [ -z "$PASS" ]; then
  read -sp "Digite a senha de $USER: " PASS
  echo ""
fi

SERVER="127.0.0.1"
PORT="21"

echo "============================================"
echo "TESTE FTP — NO SERVIDOR (loopback)"
echo "============================================"
echo "Servidor: $SERVER:$PORT"
echo "Usuário: $USER"
echo ""

# 1️⃣ Verificar se vsftpd está rodando
echo "1️⃣ Status do vsftpd"
systemctl is-active vsftpd && echo "✅ vsftpd ativo" || echo "❌ vsftpd não ativo"
echo ""

# 2️⃣ Verificar porta 21
echo "2️⃣ Porta 21 (controle)"
sudo ss -ltnp | grep ':21 ' && echo "✅ Escutando" || echo "❌ Não escutando"
echo ""

# 3️⃣ Verificar faixa PASV
echo "3️⃣ Portas PASV (30000-30009)"
sudo ss -ltnp | grep -E ':(30000|30001|30002|30003|30004|30005|30006|30007|30008|30009) '
echo ""

# 4️⃣ Ler a configuração atual
echo "4️⃣ Configuração vsftpd atual (PASV)"
echo ""
echo "   pasv_enable:"
sudo grep "^pasv_enable" /etc/vsftpd.conf || echo "   (não encontrado)"
echo ""
echo "   pasv_min_port:"
sudo grep "^pasv_min_port" /etc/vsftpd.conf || echo "   (não encontrado)"
echo ""
echo "   pasv_max_port:"
sudo grep "^pasv_max_port" /etc/vsftpd.conf || echo "   (não encontrado)"
echo ""
echo "   pasv_address (CRÍTICO!):"
sudo grep "^pasv_address" /etc/vsftpd.conf || sudo grep "# pasv_address" /etc/vsftpd.conf || echo "   (não configurado — ESSE É O PROBLEMA!)"
echo ""

# 5️⃣ Teste PASV via curl (loopback)
echo "5️⃣ Teste PASV — qual IP é retornado?"
tmplog=$(mktemp)
trap "rm -f $tmplog" EXIT

curl -s -k \
  --ftp-pasv \
  --ssl-reqd \
  -u "$USER:$PASS" \
  --trace-ascii "$tmplog" \
  -m 5 \
  "ftp://$SERVER/" \
  > /dev/null 2>&1

if grep -q "227 " "$tmplog"; then
  echo "✅ Resposta PASV capturada:"
  pasv_line=$(grep "227 " "$tmplog" | head -1)
  echo "   $pasv_line"

  if [[ $pasv_line =~ \(([0-9]+),([0-9]+),([0-9]+),([0-9]+),([0-9]+),([0-9]+)\) ]]; then
    ip="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}.${BASH_REMATCH[4]}"
    port=$((${BASH_REMATCH[5]} * 256 + ${BASH_REMATCH[6]}))
    echo ""
    echo "   📍 IP retornado: $ip"
    echo "   📍 Porta retornada: $port"
    echo ""

    if [ "$ip" = "127.0.0.1" ] || [ "$ip" = "localhost" ]; then
      echo "   ✅ Correto para loopback"
    else
      echo "   ⚠️  Deveria ser 127.0.0.1 aqui (loopback)"
    fi
  fi
else
  echo "❌ Resposta PASV não capturada"
  echo "   Erro curl: $(curl -s -k --ftp-pasv --ssl-reqd -u "$USER:$PASS" -m 5 "ftp://$SERVER/" 2>&1 | head -1)"
fi

echo ""

# 6️⃣ Diagnóstico final
echo "============================================"
echo "DIAGNÓSTICO FINAL"
echo "============================================"
echo ""

pasv_addr=$(sudo grep "^pasv_address" /etc/vsftpd.conf 2>/dev/null | cut -d= -f2)

if [ -z "$pasv_addr" ]; then
  echo "❌ PROBLEMA IDENTIFICADO:"
  echo "   pasv_address NÃO está configurado em /etc/vsftpd.conf"
  echo ""
  echo "🔧 SOLUÇÃO:"
  echo "   1. sudo nano /etc/vsftpd.conf"
  echo "   2. Procure: # pasv_address=<IP_PUBLICO>"
  echo "   3. Descomente e altere para: pasv_address=200.139.13.53"
  echo "   4. Salve (Ctrl+O, Enter, Ctrl+X)"
  echo "   5. sudo systemctl restart vsftpd"
  echo "   6. Reconecte no FileZilla"
else
  echo "✅ pasv_address está configurado: $pasv_addr"
  if [ "$pasv_addr" = "200.139.13.53" ]; then
    echo "   ✅ Correto!"
  else
    echo "   ⚠️  Verifique se está correto: $pasv_addr"
  fi
fi

echo ""
