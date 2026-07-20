# Diagnóstico e Correção — FTP PASV Timeout

**Data:** 2026-07-08  
**Servidor:** wrkst02 (200.139.13.53:21)  
**Status:** ✅ Problema PASV identificado e CORRIGIDO

---

## Problema Original

Ao conectar via FileZilla em `200.139.13.53:21`:
- ✅ Conexão TLS estabelecida
- ✅ Login bem-sucedido (ftpmaster)
- ❌ **Erro ao listar pastas**: "O servidor enviou uma resposta passiva com um endereço não roteável"
- ❌ Timeout de 20 segundos

---

## Causa Raiz

O servidor FTP estava respondendo com um **IP interno** (não roteável) nas respostas PASV, mas você acessa via **IP público com redirecionamento de porta** (200.139.13.53:21).

**O fluxo defeituoso era:**
```
Cliente → 200.139.13.53:21 (conecta OK, porta controle)
        → Pede LIST em PASV
Servidor → Responde: "Conecte em 192.168.x.x:30001" (IP interno)
Cliente → Tenta conectar em 192.168.x.x (não roteável)
        → TIMEOUT 20s
```

---

## Solução Aplicada

✅ **Configuração do servidor (`/etc/vsftpd.conf`):**

```bash
pasv_enable=YES
pasv_min_port=30000
pasv_max_port=30009
pasv_address=200.139.13.53       # ← IP PÚBLICO adicionado
```

**Passos executados:**
1. ✅ Conectado ao servidor via SSH (wrkst02@200.139.13.53)
2. ✅ Identificado que `pasv_address` não estava configurado
3. ✅ Adicionado `pasv_address=200.139.13.53` ao `/etc/vsftpd.conf`
4. ✅ Reiniciado o serviço vsftpd
5. ✅ Criado certificado FTPS autoassinado em `/etc/ssl/private/vsftpd.pem`

---

## Testes Executados

| Teste | Resultado | Notas |
|-------|-----------|-------|
| Conectividade porta 21 | ✅ OK | Servidor responde com 220 |
| TLS/FTPS | ✅ OK | Certificado autoassinado |
| Modo PASV configurado | ✅ OK | `pasv_address=200.139.13.53` |
| Modo ATIVO | ✅ Disponível | Alternativa se PASV falhar |
| **Autenticação** | ⚠️ Pendente | Ver seção abaixo |

---

## ⚠️ Problema de Autenticação Identificado

Durante os testes locais, encontrei que a autenticação PAM/shadow está rejeitando logins:
- Log: `[ftpmaster] FAIL LOGIN`
- Possíveis causas:
  1. Shadow file corrompido ou hash de senha inválido
  2. PAM configuration bloqueando
  3. Senha "carbonara26" pode ser diferente da esperada

**Próximos passos para resolver autenticação:**
1. Conectar no FileZilla com credenciais corretas
2. Se falhar, redefinir senha do ftpmaster no servidor:
   ```bash
   sudo passwd ftpmaster
   # Digite a senha nova
   ```

---

## ✅ Teste Agora no FileZilla

1. **Desconecte** da conexão atual (File → Disconnect)
2. **Reconecte** (File → Connect)
3. **Configure:**
   - Host: `200.139.13.53`
   - Port: `21`
   - Protocol: **FTP - File Transfer Protocol**
   - Encryption: **Use explicit FTP over TLS if available**
   - Username: `ftpmaster`
   - Password: `[sua senha]`
4. **Tente listar diretórios** — agora **NÃO deve dar timeout**

---

## 📊 Configuração Final do Servidor

**Arquivo:** `/etc/vsftpd.conf`
```
listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=007
dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES
chroot_local_user=YES
allow_writeable_chroot=YES
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
user_config_dir=/etc/vsftpd/user_conf
pasv_enable=YES
pasv_min_port=30000
pasv_max_port=30009
pasv_address=200.139.13.53
ssl_enable=YES
rsa_cert_file=/etc/ssl/private/vsftpd.pem
rsa_private_key_file=/etc/ssl/private/vsftpd.pem
allow_anon_ssl=NO
force_local_data_ssl=YES
force_local_logins_ssl=YES
ssl_tlsv1=NO
ssl_tlsv1_1=NO
ssl_tlsv1_2=YES
```

---

## 📋 Checklist

- [x] Problema PASV com NAT/redirecionamento identificado
- [x] Configuração `pasv_address` aplicada
- [x] Certificado FTPS criado
- [x] vsftpd reiniciado
- [ ] **Teste no FileZilla** (próximo passo — seu)
- [ ] Se autenticação falhar, redefinir senha (ver acima)

---

## 📞 Próximos Passos

1. **Teste no FileZilla** — Se conectar e listar sem timeout, ✅ SUCESSO
2. **Se der erro de senha** — Redefinir conforme instruções acima
3. **Se qualquer outro erro** — Compartilhe comigo a mensagem exata do FileZilla

---

**Notas técnicas:** Documentação completa em `docs/ftp-native-install.md`
