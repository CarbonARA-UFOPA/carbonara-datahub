# FTP de distribuição — `ftp/`

O FTP do CARBONARA **não recebe mais dados das estações** (a coleta é rsync-over-SSH,
ver `collection/rsync-hub`). Ele é a **saída**: consumidores (parceiros, análise,
outros sistemas) baixam pelo FTP o que o hub decidiu **publicar**.

Em produção o servidor é o **vsftpd nativo** do host (instalação registrada passo a
passo em [`docs/ftp-native-install.md`](../docs/ftp-native-install.md)); a variante em
container (`pure-ftpd` no `docker-compose.yml`) continua disponível para instalações
100 % Docker, mas o gerenciamento descrito aqui é o do vsftpd.

```
incoming/<estação>/…  ──(publicação: rsync, agendado)──▶  <raiz do FTP>/dados/<estação>/…
tratamento (futuro)   ──(outra publicação)─────────────▶  <raiz do FTP>/processados/…
                                                             ▲
                                          contas FTP (leitura / escrita / só envio)
```

## Duas peças

| Peça | Onde roda | O que faz |
|------|-----------|-----------|
| **`ftp/ftp-hub`** (bash, root) | no servidor, instalado em `/usr/local/bin/ftp-hub` | administra as **contas** do vsftpd: cria/remove usuários locais, senha, perfil, pasta, ativar/desativar. É o único lugar que toca `useradd`/`chpasswd`/`user_conf`. |
| **página `/ftp` da dashboard** | processo da dashboard (usuário comum) | interface das contas (chama o `ftp-hub` via `sudo -n`, regra restrita) e as **publicações** — regras "pasta do hub → subpasta do FTP" executadas com `rsync` pela própria dashboard, sem sudo. |

### Instalação (uma vez, como root, no servidor)

```bash
sudo ./ftp/ftp-hub install wrkst02      # <usuario-que-roda-a-dashboard>
```

Faz: copia o script para `/usr/local/bin/ftp-hub`; garante `/usr/sbin/nologin` em
`/etc/shells` (o PAM do vsftpd exige) e o grupo `ftpgroup`; grava
`/etc/sudoers.d/carbonara-dashboard-ftp` (validado com `visudo -cf`) liberando SÓ
`add / remove / senha / perfil / pasta / desativar / ativar` para o usuário da
dashboard; confere que esse usuário **escreve** na raiz do FTP (é assim que a
publicação funciona). Rode de novo após um `git pull` que mude o script.

### Contas — modelo

- Usuários **locais** com shell `nologin` (autenticam no FTP, nunca abrem sessão SSH),
  presos por `chroot` à sua pasta, com um **perfil** em `/etc/vsftpd/user_conf/<usuário>`
  (o vsftpd relê a cada login — nada a reiniciar):

  | Perfil | Pode | `user_conf` |
  |--------|------|-------------|
  | `leitura` | listar e baixar | `write_enable=NO` + `cmds_denied=STOR,STOU,APPE,DELE,RMD,XRMD,MKD,XMKD,RNFR,RNTO,SITE,CHMOD` |
  | `escrita` | tudo (gestão) | `write_enable=YES`, `local_umask=002` |
  | `upload` | enviar e criar pastas; **não** baixa, apaga nem renomeia | `write_enable=YES` + `cmds_denied=DELE,RMD,XRMD,RNFR,RNTO,SITE,RETR` |

- **Pasta**: subpasta da raiz do FTP (`local_root` do `vsftpd.conf`); `/` = a raiz
  toda. Uma conta de leitura presa a `dados/km67` só vê o que for publicado ali.
- Quem escreve (`escrita`/`upload`) entra no grupo dono da raiz do FTP, para gravar
  nas pastas `2775` criadas pela publicação; quem só lê fica fora (defesa em profundidade).
- **Desativar** = nome em `/etc/ftpusers` (lista de negação do PAM) + `usermod -L`; senha e
  arquivos ficam. **Remover** = `userdel` **sem** `-r`: os arquivos **nunca** são apagados.
- A senha **nunca passa pela linha de comando** (apareceria em `ps`): vai pelo stdin
  (`--senha-stdin`) ou é gerada (`--gerar`) e impressa uma vez em `SENHA=…`, que a
  dashboard mostra uma única vez.
- Barreira: `remove`/`senha`/`perfil`/… só aceitam **contas FTP** (uid ≥ 1000, shell sem
  login e perfil em `user_conf` ou home sob a raiz do FTP). `root`, a conta de serviço
  do hub, o usuário da dashboard e qualquer conta com shell são recusados — mesmo que
  a dashboard peça.

```bash
sudo ftp-hub add ufopa_lab --perfil leitura --pasta dados/km67 --gerar
sudo ftp-hub senha ufopa_lab --gerar
sudo ftp-hub perfil ufopa_lab upload
sudo ftp-hub pasta ufopa_lab caixa-entrada
sudo ftp-hub desativar ufopa_lab  |  sudo ftp-hub ativar ufopa_lab
sudo ftp-hub remove ufopa_lab
     ftp-hub list      # sem root — TSV: usuario uid perfil pasta estado home
     ftp-hub status    # resumo do servidor + contas
```

Contas criadas à mão antes do `ftp-hub` (ex.: `ftpmaster`, `ftpupload`) aparecem como
"conta antiga": o perfil é **inferido** das diretivas do `user_conf`; a primeira operação
pela dashboard passa a gravar o cabeçalho `# ftp-hub: perfil=… pasta=…`.

### Vínculos — espelhar sem copiar (preferido)

Um **vínculo** faz uma pasta **qualquer do servidor** aparecer dentro do FTP **sem ocupar
espaço** e com as mudanças visíveis **na hora**. É um *bind mount*, não um link simbólico:
cada conta fica presa por `chroot`, e o kernel resolve o alvo de um symlink *dentro* da
prisão — um link para fora da raiz do FTP aparece quebrado. O bind mount é um diretório
de verdade na visão do chroot (é a mesma técnica que o `/etc/fstab` do servidor já usa
para `carbonara-dados`).

```bash
sudo ftp-hub link dados/km67 /mnt/data_8tb/carbonara-data-hub/incoming/km67   # só leitura
sudo ftp-hub link caixa/upload /srv/entrada --rw                               # leitura+escrita
sudo ftp-hub unlink dados/km67          # desmonta e apaga o registro — nada é apagado na origem
     ftp-hub links                      # TSV: sub origem modo montado origem_existe
sudo ftp-hub link-apply                 # remonta tudo o que está registrado (usado no boot)
```

- Registro em `/etc/ftp-hub/links` (644 — a dashboard lê sem sudo); a unidade systemd
  `carbonara-ftp-links.service` (criada pelo `install`, `Before=vsftpd`) remonta os vínculos
  no boot. Não editamos o `/etc/fstab`.
- **Só leitura por padrão** (`mount -o remount,bind,ro`): nem uma conta de escrita altera a
  origem. `--rw` é explícito e a UI avisa.
- O destino precisa ser uma subpasta **vazia ou nova** — um vínculo sobre uma pasta com
  conteúdo o *esconderia*, então é recusado. A raiz do FTP inteira não pode ser vínculo.
- Origens **recusadas**: `/`, `/etc`, `/home`, `/root`, `/boot`, `/proc`, `/sys`, `/dev`,
  `/run`, `/var/log`, `/var/lib`, `/usr`, `/bin`, `/sbin`, `/lib*`, `/snap` (segredos e
  pseudo-filesystems do host nunca vão para o FTP, mesmo só-leitura) e qualquer coisa já
  dentro da raiz do FTP. A mesma lista vale para a cópia.
- Na dashboard, o card **Vínculos** lista os registrados (montado / não montado / origem
  ausente) e as montagens sob a raiz feitas fora dele (`fstab/manual`), com seletor de
  pasta que navega o **sistema de arquivos inteiro**.

### Pastas — organização

O card **Pastas do FTP** cria subpastas (2775, legíveis por todas as contas) em qualquer
nível da raiz e remove **somente pastas vazias** que não sejam vínculos — a dashboard nunca
apaga dados do FTP. Não precisa de sudo (a raiz do FTP é gravável pelo usuário da dashboard).

### Publicações (cópia) — quando copiar

Cada regra copia uma **origem** (qualquer pasta do hub — `incoming/km67`, uma subpasta,
ou um caminho absoluto; amanhã, a saída do tratamento de dados) para um **destino**
(subpasta da raiz do FTP), com filtros opcionais, em um intervalo. Invariantes:

- **A origem nunca é alterada** (`rsync -rtp`, sem `--remove-source-files`). Ocultos
  (`.carbonara-status`, `.rsync-parcial/`) ficam de fora por padrão.
- Arquivos chegam `664` e pastas `2775` (grupo herdado por setgid): legíveis por qualquer
  conta FTP, graváveis pelo grupo.
- **Acumular** (padrão) nunca remove nada do FTP. **Espelhar** (`delete`) remove no
  destino o que sumiu da origem — **só entre os arquivos que a regra cobre**: com o
  filtro `*.dat`, um `relatorio.pdf` que alguém subiu por FTP na mesma pasta não é tocado.
- Destino tem de ser uma **subpasta** (a raiz inteira é recusada); origem e destino não
  podem se sobrepor; a origem não pode estar dentro da raiz do FTP.
- Uma regra não roda em paralelo consigo mesma; regras diferentes rodam em paralelo.
- Estado em `dashboard/ftp-publish.state.json`, registro em `dashboard/ftp-publish.log`
  (com teto). `python -m app.ftp_publish [id]` (na pasta `dashboard/`, com o venv)
  executa uma vez pelo terminal — serve para cron ou para quando a dashboard estiver parada.

## TLS (FTPS)

O `vsftpd.conf` do servidor oferece FTPS explícito (`ssl_enable=YES`, certificado
autoassinado em `/etc/ssl/private/vsftpd.pem`) **sem obrigar** (`force_local_*_ssl=NO`),
porque há clientes sem TLS. A página FTP mostra esse estado. Para exigir criptografia,
mude `force_local_logins_ssl=YES` / `force_local_data_ssl=YES` e reinicie o vsftpd.

## Ferramentas de diagnóstico

`diagnostico-ftp.sh`, `test-ftp-connection.sh`, `teste-local-servidor.sh` e os registros
em `docs/` são da época da instalação (testes de conexão, modo passivo/ativo) e continuam
válidos para depurar acesso de clientes.
