# FTP (vsftpd) - Configuração Final CARBONARA

**Data:** 2026-07-08  
**Status:** ✅ **FUNCIONANDO**

---

## ✅ Configuração Atual

### Servidor
- **Host:** 200.139.13.53:21
- **Tipo:** vsftpd (FTP inseguro - sem TLS)
- **IP Interno:** 10.1.90.246
- **Diretório:** /srv/carbonara/collect

### Usuários
| Usuário | Senha | Permissões |
|---------|-------|-----------|
| **ftpmaster** | model25 | ✅ Full access (leitura, escrita, exclusão) |
| **ftpupload** | carbonara26 | ⚠️ Limited (escrita, criar pastas, SEM exclusão) |

### Configuração vsftpd.conf (arquivo `.orig`)

```
listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=007

chroot_local_user=YES
allow_writeable_chroot=YES
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
user_config_dir=/etc/vsftpd/user_conf

# PASV
pasv_enable=YES
pasv_min_port=30000
pasv_max_port=30009
pasv_address=200.139.13.53
pasv_promiscuous=YES

# PORT (Ativo)
port_promiscuous=YES

# TLS (DESABILITADO por enquanto)
ssl_enable=NO
force_local_logins_ssl=NO
force_local_data_ssl=NO
```

### Estrutura de Diretórios
```
/srv/carbonara/
└── collect/
    └── (vazio inicialmente)
    
Permissões: drwxrws--- (2770)
Dono: ftpmaster:ftpgroup
```

---

## 🧪 Teste (FileZilla)

**Configuração recomendada:**
- Host: `200.139.13.53`
- Port: `21`
- Username: `ftpmaster`
- Password: `model25`
- Protocol: **FTP - File Transfer Protocol**
- Encryption: **None (insecure)** ⚠️

---

## ⚠️ Melhorias Futuras

### Quando conseguir mapear as portas 30000-30009:

1. **Habilitar TLS novamente**
   - Edite `/etc/vsftpd.conf.orig`:
   ```
   ssl_enable=YES
   force_local_logins_ssl=YES
   force_local_data_ssl=YES
   ```

2. **Port Forwarding no Router**
   ```
   Porta Externa: 30000-30009
   IP Interno: 10.1.90.246
   Porta Interna: 30000-30009
   Protocolo: TCP
   ```

3. **Remova promiscuous (opcional)**
   - Após mapear portas, pode remover:
   ```
   port_promiscuous=YES
   pasv_promiscuous=YES
   ```

---

## 📋 Checklist de Uso

- [x] FTP funciona (sem TLS)
- [x] Login ftpmaster funciona
- [x] Pasta collect acessível
- [x] Modo PASIVO funciona
- [x] Modo ATIVO funciona
- [ ] TLS habilitado (quando conseguir mapear portas)
- [ ] Portas 30000-30009 mapeadas no router

---

## 🔒 Segurança

**Atual:** ⚠️ FTP inseguro (sem encriptação)
- Senha transmitida em texto plano
- Dados transmitidos sem encriptação

**Futuro (com TLS):** ✅ FTPS encriptado
- Senha encriptada
- Dados encriptados
- Certificado autoassinado em `/etc/ssl/private/vsftpd.pem`

---

## 📞 Contato

Se precisar de mudanças futuras:
- Edite `/etc/vsftpd.conf.orig`
- Reinicie: `sudo systemctl restart vsftpd`
- Arquivo regenerado automaticamente

---

**✅ FTP CARBONARA DATA-HUB CONFIGURADO E FUNCIONANDO**
