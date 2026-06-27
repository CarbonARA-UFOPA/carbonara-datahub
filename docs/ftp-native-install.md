# Instalação do servidor FTP nativo (vsftpd) — registro ao vivo

Registro **passo a passo da instalação real** do servidor FTP/FTPS do CARBONARA data-hub,
usando **vsftpd nativo (apt)**, sem Docker. Cada passo é executado no servidor pelo operador
e o resultado é registrado aqui (✅ funcionou / ❌ falhou / ⏳ pendente).

> Abordagem **nativa**. Difere do `docker-compose.yml` (que usa pure-ftpd em container). O
> transporte é uma fronteira plugável: ingestão e banco só dependem do diretório de coleta
> (`collect/`), então essa escolha não afeta o resto do stack.

## Por que vsftpd

O requisito central é **dois papéis de usuário**: um *master* (lê/escreve/apaga) e um *upload*
(cria pastas e grava arquivos, mas **não apaga nada**). O vsftpd expressa isso nativamente,
por usuário, com `cmds_denied=DELE,RMD,RNFR,RNTO` — sem truques de filesystem e sem brechas.
Usa usuários locais comuns (menos manutenção que o banco `pure-pw` do pure-ftpd) e é o FTP
mais documentado no Ubuntu.

## Histórico de decisão

1. Cogitado pure-ftpd nativo (instalado no Passo 1 original). Descartado: **não nega comandos
   por usuário**, logo "escreve mas não deleta" não é expressável de forma limpa.
2. **Adotado vsftpd** por atender o modelo de permissão nativamente. O pure-ftpd é desabilitado
   no Passo 1.

## Servidor-alvo

| Item | Valor |
|------|-------|
| OS | Ubuntu 22.04.5 LTS x86_64 |
| Host | Dell Precision 5820 Tower (`wrkst02`) |
| Kernel | 6.8.0-124-generic |
| CPU | Intel Xeon W-2133 (12) @ 3.90 GHz |
| RAM | 32 GB |
| Servidor FTP | vsftpd (nativo, apt) |
| Autenticação | usuários locais do sistema |
| Criptografia | FTPS **opcional** (TLS oferecido; FTP puro permitido p/ estações sem suporte a TLS) |

## Convenções adotadas

- **Raiz dos dados:** `/srv/carbonara/collect/<estação>/` (cada estação tem sua pasta; nomes a definir).
- **Dois usuários de sistema** (grupo comum `ftpgroup`, gid `2015`):
  - `ftpmaster` (uid `2015`) — leitura + escrita + exclusão (gestão).
  - `ftpupload` (uid `2016`) — usado pelos scripts das estações; cria pastas e grava arquivos, **não apaga**.
- **`no-delete` do upload** é imposto pelo vsftpd (`cmds_denied`), não por permissão de filesystem.
- **`setgid`** na árvore `collect/` para o master (no grupo) sempre conseguir ler/gerir os envios.
- **Portas passivas:** `30000–30009`.
- **Certificado TLS:** `/etc/ssl/private/vsftpd.pem` (chave + cert concatenados).

---

## Progresso

| # | Passo | Status |
|---|-------|--------|
| 1 | Desabilitar pure-ftpd e instalar vsftpd | ✅ ok |
| 2 | Criar usuários de sistema e árvore `collect/` | ✅ ok |
| 3 | Configurar `/etc/vsftpd.conf` (chroot, passivo, umask) | ✅ ok |
| 4 | Configs por usuário (`master` full, `upload` sem delete) | ✅ ok |
| 5 | Gerar certificado e habilitar TLS (FTPS opcional) | ✅ ok (após corrigir `ssl_tlsv1_2`) |
| 6 | Liberar portas no firewall (ufw) | ✅ regras criadas (ufw inativo) |
| 7 | Habilitar no boot e validar conexão FTPS (master) | ✅ ok |
| 8 | Teste ponta a ponta (upload grava, não apaga; master apaga) | ✅ ok |

---

## Passo 1 — Desabilitar pure-ftpd e instalar vsftpd

**Objetivo:** parar/desabilitar o pure-ftpd instalado antes (libera a porta 21) e instalar o vsftpd.

```bash
# desabilita o pure-ftpd (não desinstala; só sai do caminho)
sudo systemctl stop pure-ftpd
sudo systemctl disable pure-ftpd

# instala o vsftpd
sudo apt update
sudo apt install -y vsftpd
```

Verificação:

```bash
apt-cache policy vsftpd | head -n 3
systemctl is-enabled pure-ftpd                 # deve dizer: disabled (ou masked)
systemctl status vsftpd --no-pager             # serviço presente (provavelmente active)
sudo ss -ltnp | grep ':21 ' || echo "porta 21 livre/observar"
```

**Resultado esperado:** vsftpd instalado; pure-ftpd `disabled`; serviço `vsftpd` presente.

**Resultado real:** ✅ **2026-06-27** — vsftpd `3.0.5-0ubuntu1.1` instalado, serviço
`active (running)` escutando em `*:21`; `pure-ftpd` = `disabled`.

---

## Passo 2 — Criar usuários de sistema e árvore `collect/`

**Objetivo:** criar o grupo comum e os dois usuários locais (`ftpmaster` e `ftpupload`),
liberar o shell `nologin` para o PAM do vsftpd e montar a raiz `/srv/carbonara/collect/`.

```bash
# grupo comum
sudo groupadd -g 2015 ftpgroup

# usuário MASTER — lê/escreve/apaga
sudo useradd -u 2015 -g 2015 -s /usr/sbin/nologin -d /srv/carbonara/collect -M ftpmaster

# usuário UPLOAD — escreve/cria pastas, não apaga (imposto pelo vsftpd no Passo 4)
sudo useradd -u 2016 -g 2015 -s /usr/sbin/nologin -d /srv/carbonara/collect -M ftpupload

# vsftpd usa pam_shells: o shell do usuário precisa constar em /etc/shells
grep -qxF /usr/sbin/nologin /etc/shells || echo /usr/sbin/nologin | sudo tee -a /etc/shells

# definir as senhas (anote-as; o upload vai para o config das estações)
sudo passwd ftpmaster
sudo passwd ftpupload

# árvore de coleta — dono master, grupo comum, setgid p/ herdar grupo
sudo mkdir -p /srv/carbonara/collect
sudo chown ftpmaster:ftpgroup /srv/carbonara /srv/carbonara/collect
sudo chmod 2770 /srv/carbonara /srv/carbonara/collect
```

- `nologin` + entrada em `/etc/shells`: as contas autenticam no FTP mas **não abrem shell** (SSH).
- `chmod 2770` (`setgid`): novos arquivos/pastas herdam o grupo `ftpgroup`, então o `ftpmaster`
  (membro do grupo) sempre consegue ler e gerir o que o `ftpupload` enviou. As permissões finais
  dos arquivos enviados serão ajustadas pelo `local_umask` no Passo 3.

Verificação:

```bash
id ftpmaster
id ftpupload
grep nologin /etc/shells
ls -ld /srv/carbonara /srv/carbonara/collect
```

**Resultado esperado:** `ftpmaster` = `uid=2015 gid=2015(ftpgroup)`, `ftpupload` = `uid=2016
gid=2015(ftpgroup)`; `/usr/sbin/nologin` presente em `/etc/shells`; `collect` como
`drwxrws--- ftpmaster ftpgroup`.

**Resultado real:** ✅ **2026-06-27** — `ftpmaster` uid 2015 e `ftpupload` uid 2016, ambos em
`ftpgroup`; `/usr/sbin/nologin` em `/etc/shells`; `/srv/carbonara` = `drwxrws--- ftpmaster
ftpgroup` (e `collect` criado dentro). Nota: `ls` de `collect` por um usuário fora do `ftpgroup`
dá "Permissão negada" — comportamento **esperado** do modo `2770` (acesso só via `ftpmaster`/`ftpupload`).

---

## Passo 3 — Configurar `/etc/vsftpd.conf` (base)

**Objetivo:** substituir a config padrão pela do CARBONARA: chroot por usuário, modo passivo,
`umask` colaborativo e o diretório de overrides por usuário (usado no Passo 4). **TLS entra no Passo 5.**

```bash
# backup do original e pasta de configs por usuário
sudo cp /etc/vsftpd.conf /etc/vsftpd.conf.orig
sudo mkdir -p /etc/vsftpd/user_conf

# escreve a config base
sudo tee /etc/vsftpd.conf >/dev/null <<'EOF'
# vsftpd — CARBONARA data-hub (base; TLS é adicionado no Passo 5)
listen=YES
listen_ipv6=NO

anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=007                 # arquivos 660 / pastas 770 -> master (no grupo) gere os envios

dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES

# cada usuário fica preso (chroot) à sua home = /srv/carbonara/collect
chroot_local_user=YES
allow_writeable_chroot=YES       # a raiz de chroot é gravável (necessário p/ os uploads)
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd

# overrides por usuário (Passo 4): master full, upload sem delete
user_config_dir=/etc/vsftpd/user_conf

# modo passivo
pasv_enable=YES
pasv_min_port=30000
pasv_max_port=30009
# pasv_address=<IP_PUBLICO>      # defina apenas se houver NAT entre estações e servidor
EOF

# reinicia e confere
sudo systemctl restart vsftpd
systemctl is-active vsftpd
```

Verificação:

```bash
systemctl status vsftpd --no-pager | head -n 5
sudo ls -ld /srv/carbonara/collect          # registra o modo real de collect
```

**Resultado esperado:** `vsftpd` reinicia `active (running)` sem erro; `collect` aparece como
`drwxrws--- ftpmaster ftpgroup`.

**Resultado real:** ✅ **2026-06-27** — `vsftpd` `active (running)` com a config nova;
`collect` = `drwxrws--- ftpmaster ftpgroup`.

---

## Passo 4 — Configs por usuário (`master` full, `upload` sem delete)

**Objetivo:** aplicar o modelo de permissão por usuário usando `user_config_dir`. É aqui que o
`ftpupload` ganha "escreve mas não apaga", de forma nativa do vsftpd.

```bash
# UPLOAD — pode enviar arquivo (STOR), criar pasta (MKD) e listar; NÃO apaga, renomeia nem baixa
sudo tee /etc/vsftpd/user_conf/ftpupload >/dev/null <<'EOF'
# estações: escrita/criação de pastas; sem exclusão, rename, download ou SITE
write_enable=YES
cmds_denied=DELE,RMD,RNFR,RNTO,SITE,RETR
EOF

# MASTER — gestão completa (herda os globais; explícito p/ documentar)
sudo tee /etc/vsftpd/user_conf/ftpmaster >/dev/null <<'EOF'
# gestão: leitura, escrita e exclusão completas
write_enable=YES
EOF

sudo systemctl restart vsftpd
```

- `cmds_denied` para o `ftpupload`: bloqueia `DELE` (apagar arquivo), `RMD` (apagar pasta),
  `RNFR`/`RNTO` (renomear), `SITE` (ex.: `SITE CHMOD`) e `RETR` (download). Sobram permitidos
  `STOR` (enviar), `APPE` (anexar), `MKD` (criar pasta) e `LIST`/`NLST` (listar).
- O `ftpmaster` não tem `cmds_denied`, portanto mantém exclusão/rename/download.

> **Nuance honesta:** o `STOR` sobre um nome já existente *sobrescreve* o arquivo (não é `DELE`,
> mas substitui conteúdo). Isso só alcança arquivos ainda **não ingeridos** — após a ingestão os
> arquivos saem de `collect/` (vão para `archive/`, fora do chroot da estação). Se quisermos vetar
> até a sobrescrita, dá para adicionar `APPE` ao `cmds_denied` e tratar reenvio por nome novo.

Verificação:

```bash
ls -l /etc/vsftpd/user_conf/
sudo cat /etc/vsftpd/user_conf/ftpupload
systemctl is-active vsftpd
```

**Resultado esperado:** os dois arquivos existem; `vsftpd` segue `active`. (O teste funcional
real — upload grava, não apaga; master apaga — é feito no Passo 8, após TLS e firewall.)

**Resultado real:** ✅ **2026-06-27** — `/etc/vsftpd/user_conf/{ftpmaster,ftpupload}` criados;
`ftpupload` com `cmds_denied=DELE,RMD,RNFR,RNTO,SITE,RETR`; `vsftpd` = `active`.

---

## Passo 5 — Gerar certificado e habilitar TLS (FTPS opcional)

**Objetivo:** criar um certificado autoassinado e **oferecer FTPS sem obrigar**. As estações
que falam TLS criptografam controle e dados; as que só fazem FTP puro continuam conectando.

> **Decisão registrada (2026-06-27):** TLS **opcional** (`force_local_*_ssl=NO`), pois a frota
> tem estações sem suporte a TLS. O certificado é **do servidor** — a estação não precisa de
> certificado próprio; clientes que falam FTPS apenas precisam aceitar cert autoassinado
> (ex.: `curl --ftp-ssl -k`, `lftp set ssl:verify-certificate no`, Python `FTP_TLS`).
> Trade-off: estações em FTP puro mandam credenciais/dados em **texto puro** — aceitável em LAN
> fechada; para exposição à internet, exigir TLS por IP no firewall ou migrar para SFTP depois.

```bash
# certificado autoassinado (chave + cert no mesmo .pem)
sudo openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
  -keyout /etc/ssl/private/vsftpd.pem -out /etc/ssl/private/vsftpd.pem \
  -subj "/CN=wrkst02"
sudo chmod 600 /etc/ssl/private/vsftpd.pem

# anexa o bloco TLS à config (oferecido, NÃO obrigatório)
sudo tee -a /etc/vsftpd.conf >/dev/null <<'EOF'

# --- TLS / FTPS (oferecido, opcional) ---
ssl_enable=YES
rsa_cert_file=/etc/ssl/private/vsftpd.pem
rsa_private_key_file=/etc/ssl/private/vsftpd.pem
force_local_logins_ssl=NO        # NÃO obriga TLS no login (estações sem TLS conectam)
force_local_data_ssl=NO          # NÃO obriga TLS no canal de dados
ssl_tlsv1=YES                    # vsftpd NÃO tem ssl_tlsv1_1/1_2; o OpenSSL do Ubuntu já barra TLS<1.2
ssl_sslv2=NO
ssl_sslv3=NO
require_ssl_reuse=NO
ssl_ciphers=HIGH
allow_anon_ssl=NO
EOF

sudo systemctl restart vsftpd
```

- Certificado autoassinado válido por 10 anos (`-days 3650`); para produção pode-se trocar por
  um emitido por CA depois (mesmo caminho de arquivo).
- `force_local_*_ssl=NO`: o TLS fica **disponível mas não obrigatório**.
- `require_ssl_reuse=NO`: evita o erro de "SSL session reuse" em clientes como `lftp`/`curl`.
- ⚠️ **Não use `ssl_tlsv1_1`/`ssl_tlsv1_2`** — não existem no vsftpd e o serviço falha ao subir
  (`500 OOPS: unrecognised variable`). Use só `ssl_tlsv1=YES`.

Verificação:

```bash
sudo ls -l /etc/ssl/private/vsftpd.pem
sudo openssl x509 -in /etc/ssl/private/vsftpd.pem -noout -subject -enddate
systemctl is-active vsftpd
```

**Resultado esperado:** arquivo `vsftpd.pem` com modo `600`; `subject=CN=wrkst02` e validade
~10 anos; `vsftpd` = `active`.

**Resultado real:** ✅ **2026-06-27** — certificado criado (`-rw------- root`, `CN=wrkst02`,
validade até 2036). O 1º restart falhou com `500 OOPS: unrecognised variable in config file:
ssl_tlsv1_2` (variável inexistente no vsftpd). **Correção:** removidas `ssl_tlsv1_1`/`ssl_tlsv1_2`
e setado `ssl_tlsv1=YES`. Após isso, `vsftpd` = `active` com `ssl_enable=YES`.

---

## Passo 6 — Liberar portas no firewall (ufw)

**Objetivo:** liberar as portas do FTP no `ufw`. Portas: `21` (controle), `20` (dados em modo
ativo) e a faixa passiva `30000–30009`.

> ⚠️ **Segurança de SSH:** se o `ufw` estiver **inativo**, **NÃO** o habilite sem antes liberar o
> SSH (`sudo ufw allow OpenSSH`), ou você se tranca para fora da máquina. As regras `allow` abaixo
> são idempotentes e seguras mesmo com o ufw inativo (só passam a valer quando/se ele for ligado).

```bash
# 1) estado atual do firewall
sudo ufw status verbose

# 2) regras do FTP (seguras em qualquer estado)
sudo ufw allow 21/tcp comment 'FTP controle'
sudo ufw allow 20/tcp comment 'FTP dados (ativo)'
sudo ufw allow 30000:30009/tcp comment 'FTP passivo'

# 3) SOMENTE se for habilitar o ufw agora (libere o SSH primeiro!):
# sudo ufw allow OpenSSH
# sudo ufw enable
```

Verificação:

```bash
sudo ufw status numbered
```

**Resultado esperado:** as regras `21`, `20` e `30000:30009/tcp` aparecem como `ALLOW`. Se o ufw
estava inativo e você optou por não habilitá-lo, ele segue `inactive` (as regras ficam registradas).

**Resultado real:** ✅ **2026-06-27** — regras `21`, `20`, `30000:30009/tcp` adicionadas (IPv4+IPv6).
`ufw` está **inativo** — decisão de mantê-lo desligado por ora (acesso em LAN; sem firewall
bloqueando no host). As regras passam a valer se o ufw for habilitado depois (lembrar de
`ufw allow OpenSSH` antes do `ufw enable`).

---

## Passo 7 — Habilitar no boot e validar conexão FTPS (usuário master)

**Objetivo:** confirmar que o vsftpd sobe no boot e fazer o **primeiro upload real via FTPS**
com o usuário `ftpmaster` (loopback), provando login + TLS + escrita no `collect/`.

> O `curl` usado aqui roda **no servidor** (não é o nosso ambiente). `-k` aceita o certificado
> autoassinado; `--ssl-reqd` força o canal por TLS; o `curl` pergunta a senha (não vai pro histórico).

```bash
# garante início automático no boot
sudo systemctl enable vsftpd
systemctl is-enabled vsftpd

# arquivo de teste
echo "carbonara teste master $(date)" | sudo tee /tmp/t_master.txt >/dev/null

# upload como MASTER via FTPS explícito + passivo (vai pedir a senha do ftpmaster)
curl -k --ssl-reqd --ftp-pasv -T /tmp/t_master.txt -u ftpmaster ftp://127.0.0.1/teste_master.txt

# confirma que o arquivo chegou em collect/ (dono ftpmaster:ftpgroup, modo 660)
sudo ls -l /srv/carbonara/collect/
```

**Resultado esperado:** `is-enabled` = `enabled`; o `curl` conclui sem erro (sem mensagem de
falha de TLS/login); `teste_master.txt` aparece em `/srv/carbonara/collect/` como
`-rw-rw---- ftpmaster ftpgroup`.

**Resultado real:** ✅ **2026-06-27** — upload via FTPS (`curl --ssl-reqd`) concluiu 100% sem
erro; `teste_master.txt` em `/srv/carbonara/collect/` como `-rw-rw---- ftpmaster ftpgroup`
(modo 660, confirmando `umask 007`). Login + TLS + escrita validados.

---

## Passo 8 — Teste ponta a ponta do modelo de permissão

**Objetivo:** provar, com o usuário `ftpupload`, que ele **envia arquivo e cria pasta** mas
**não apaga**; e que o `ftpmaster` apaga. Esta é a validação do requisito central.

```bash
# A) UPLOAD como estação: cria a pasta flona01 (MKD) e envia o arquivo (STOR) -> deve FUNCIONAR
echo "carbonara teste upload $(date)" | sudo tee /tmp/t_upload.txt >/dev/null
curl -k --ssl-reqd --ftp-pasv --ftp-create-dirs -T /tmp/t_upload.txt \
  -u ftpupload ftp://127.0.0.1/flona01/t_upload.txt

# B) DELETE como estação -> deve FALHAR (550 Permission denied)
curl -k --ssl-reqd --ftp-pasv -u ftpupload \
  -Q "DELE flona01/t_upload.txt" ftp://127.0.0.1/ ; echo "exit=$?"

# C) confere que o arquivo AINDA existe (a estação não conseguiu apagar)
sudo ls -l /srv/carbonara/collect/flona01/

# D) DELETE como master -> deve FUNCIONAR
curl -k --ssl-reqd --ftp-pasv -u ftpmaster \
  -Q "DELE flona01/t_upload.txt" ftp://127.0.0.1/ ; echo "exit=$?"

# E) confere que sumiu
sudo ls -l /srv/carbonara/collect/flona01/
```

**Resultado esperado:**
- (A) upload conclui sem erro; cria `collect/flona01/` (dono `ftpupload:ftpgroup`, `drwxrws---`).
- (B) **falha** com `550 Permission denied` e `exit` ≠ 0 — o `ftpupload` **não apaga**.
- (C) o `t_upload.txt` continua lá.
- (D) master apaga sem erro (`exit=0`).
- (E) pasta vazia.

**Resultado real:** ✅ **2026-06-27** — todos os comportamentos confirmados:
- (A) `ftpupload` enviou e criou `collect/flona01/` (`drwxrws--- ftpupload(2016) ftpgroup(2015)`).
- (B) `DELE` como `ftpupload` → `curl: (21) QUOT command failed with 550`, `exit=21` → **negado**.
- (C) `t_upload.txt` permaneceu (`-rw-rw---- ftpupload ftpgroup`).
- (D) `DELE` como `ftpmaster` → `exit=0` (apagou).
- (E) `collect/flona01/` ficou vazia.

**Requisito central validado:** estação escreve e cria pastas, **não apaga**; master gere tudo.

---

## Conclusão — instalação concluída (2026-06-27)

Servidor FTP/FTPS do CARBONARA data-hub no ar em `wrkst02` (Ubuntu 22.04.5), **vsftpd nativo**.

**O que ficou ativo:**
- `vsftpd 3.0.5` habilitado no boot, escutando na `:21`, modo passivo `30000–30009`.
- **FTPS opcional** (TLS oferecido, não obrigatório) — cert autoassinado em `/etc/ssl/private/vsftpd.pem`.
- Raiz de coleta `/srv/carbonara/collect/` (`2770`, setgid), grupo `ftpgroup`.
- **`ftpmaster`** (uid 2015) — leitura/escrita/exclusão.
- **`ftpupload`** (uid 2016) — usado pelas estações: envia e cria pastas, **não apaga**
  (`cmds_denied=DELE,RMD,RNFR,RNTO,SITE,RETR`).
- `ufw` com regras de FTP criadas, porém **inativo** (sem firewall no host por ora).

**Arquivos de config no servidor:** `/etc/vsftpd.conf`, `/etc/vsftpd/user_conf/{ftpmaster,ftpupload}`,
backup do original em `/etc/vsftpd.conf.orig`.

**Próximos passos (fora do escopo desta instalação):**
1. **Por estação:** quando os nomes forem definidos, cada estação usa a credencial `ftpupload`
   gravando em `collect/<estação>/` (a pasta é criada no 1º envio via `MKD`). Se quiser isolar
   estações entre si (uma não ver/apagar pendências da outra), aí sim criar um usuário de sistema
   por estação — decidir depois.
2. **Ingestão:** apontar o serviço de ingestão para `/srv/carbonara/collect/` (antes `landing/`) e
   confirmar que ele move os arquivos para `archive/` (fora do alcance do `ftpupload`), fechando
   também a brecha de sobrescrita por `STOR`.
3. **Rede:** confirmar se o acesso das estações é **LAN** ou **internet/NAT**. Se houver NAT,
   definir `pasv_address=<IP_público>` no `/etc/vsftpd.conf` e habilitar o `ufw`
   (`ufw allow OpenSSH` antes de `ufw enable`).
4. **Endurecer TLS (opcional):** se/quando todas as estações falarem TLS, mudar
   `force_local_*_ssl=YES` para exigir criptografia.

---

<!-- Os passos 6 a 8 serão detalhados aqui conforme cada anterior for confirmado. -->
