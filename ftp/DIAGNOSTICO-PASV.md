# FTP — Problema de PASV com Redirecionamento de Porta

## O Problema (seu caso)

**Sintoma no FileZilla:**
```
Conectando 200.139.13.53:21...        ✅ Conecta OK
Conexão TLS estabelecida               ✅ Login funciona
Obtendo lista de pastas...
O servidor enviou uma resposta passiva com um endereço não roteável
Usando o endereço do servidor como alternativa
Conexão expirou após 20 segundos        ❌ Timeout na transferência de dados
```

## Causa Raiz

Você está acessando o servidor FTP por um **IP público com redirecionamento de porta** (`200.139.13.53:21`), mas o servidor está **respondendo com um IP interno** (provavelmente `10.x.x.x`, `192.168.x.x`, etc.) nas respostas do comando **PASV** (modo passivo).

**O fluxo defeituoso:**
1. Cliente conecta a `200.139.13.53:21` ✅ (funciona)
2. Login bem-sucedido ✅ (TLS OK)
3. Cliente pede `LIST` em modo passivo
4. Servidor responde: "Conecte em `192.168.x.x:30001`" (IP interno)
5. Cliente tenta conectar em `192.168.x.x:30001` ❌ (não roteável, timeout)

**Por que acontece:**
- O servidor FTP está rodando **dentro da LAN/container** e não sabe que foi acesso por um IP público
- Ele responde com seu próprio IP local (onde está escutando)
- O redirecionamento de porta só funciona na porta **controle (21)**; o cliente não consegue alcançar as portas **passivas (30000-30009)** do servidor interno

## Soluções

### ✅ Solução 1: Configurar IP Público no vsftpd (RECOMENDADO)

**Se o servidor é vsftpd nativo (em wrkst02):**

```bash
# SSH para o servidor
ssh user@wrkst02

# Abra o arquivo de config
sudo nano /etc/vsftpd.conf

# Procure esta linha (deve estar comentada):
# pasv_address=<IP_PUBLICO>

# Descomente e altere para:
pasv_address=200.139.13.53

# Salve (Ctrl+O, Enter, Ctrl+X)

# Reinicie o serviço
sudo systemctl restart vsftpd

# Valide
systemctl status vsftpd --no-pager
```

**Se o servidor é pure-ftpd (em Docker):**

Edite o `docker-compose.yml`:

```yaml
ftp:
  image: stilliard/pure-ftpd:hardened
  environment:
    PUBLICHOST: "200.139.13.53"    # ← Mude para o IP público
    FTP_PASSIVE_PORTS: "30000:30009"
```

Reinicie:
```bash
docker compose down
docker compose up -d ftp
```

---

### 🔄 Solução 2: Usar Modo ATIVO (Alternativa Rápida)

Se não puder/quiser alterar o servidor agora:

**No FileZilla:**
1. Conecte normalmente
2. **Edit → Settings → FTP**
3. Em "Transfer Mode", escolha **Active** (em vez de Passive)
4. **OK** e reconecte

**Funcionamento do modo ativo:**
- Cliente abre porta local e **diz ao servidor** qual porta escutar
- Servidor conecta **de volta** ao cliente (em vez do cliente conectar no servidor)
- Contorna o problema do IP interno
- Funciona bem se o cliente (seu PC) não estiver atrás de um firewall restritivo

---

### 🧪 Como Testar

**Teste 1: Diagnosticar o IP retornado**

```bash
# Use o script de diagnóstico criado
bash ftp/diagnostico-ftp.sh 200.139.13.53 21 ftpupload

# Vai mostrar qual IP o servidor está retornando em PASV
```

**Teste 2: Depois de corrigir**

```bash
# Conecte com curl em modo passivo (deve funcionar)
curl -k --ssl-reqd --ftp-pasv -u ftpupload ftp://200.139.13.53/

# Ou direto no FileZilla — reconecte e tente listar pastas
```

---

## Checklist de Resolução

- [ ] **Passo 1:** Determine onde o servidor FTP está rodando
  - Docker (docker-compose.yml) → Solução 1B
  - vsftpd nativo (wrkst02) → Solução 1A
  
- [ ] **Passo 2:** Escolha a solução
  - Preferência: Solução 1 (fix no servidor)
  - Alternativa: Solução 2 (modo ativo no cliente)

- [ ] **Passo 3:** Aplique a correção

- [ ] **Passo 4:** Teste com o script `diagnostico-ftp.sh`

- [ ] **Passo 5:** Reconecte no FileZilla e tente listar pastas

---

## Referências

- Configuração vsftpd: `/etc/vsftpd.conf` (linha `pasv_address`)
- Documentação do projeto: `docs/ftp-native-install.md` (Passo 3, comentário sobre NAT)
- Script de teste: `ftp/test-ftp-connection.sh` (referência de conexão FTPS)
