# Instalação do servidor central

Guia para subir a workstation central do CARBONARA. São **duas partes independentes**:

- **A. Coleta** — `rsync-over-SSH`, **nativa no host**, toda pelo script [`rsync-hub`](../collection/rsync-hub).
- **B. Dados** — TimescaleDB + ingestão + FTP de distribuição, em **Docker Compose**.

A fronteira entre elas é o diretório `incoming/`: o `rsync-hub` só escreve nele, a ingestão só
lê dele. Dá para instalar/testar cada parte separadamente.

## Pré-requisitos

- **Host (coleta):** `openssh-server` e `rsync` (o `rrsync` acompanha o pacote `rsync`).
- **Stack de dados:** Docker Engine 24+ e Docker Compose v2 (`docker compose version`).
- **Portas livres:**
  - `22` — SSH, usada pela coleta (rsync das estações por **chave**).
  - `873` — rsync daemon, usada pela coleta das estações por **usuário+senha** (opcional;
    só se alguma estação usar esse modo).
  - `21` + faixa passiva `30000-30009` — FTP de **distribuição** (saída dos dados processados).
  - `5432` — PostgreSQL.
- Espaço em disco proporcional ao volume das estações (`incoming` + `archive` + banco).

Defina uma **raiz de dados** e use a mesma nas duas partes. Em produção:
`CARBONARA_ROOT=/srv/carbonara` (é o default do `rsync-hub`).

### 0. Clonar o repositório

```bash
git clone https://github.com/CarbonARA-UFOPA/carbonara-datahub.git
cd carbonara-datahub
```

---

## Parte A — Coleta com `rsync-hub` (nativo no host)

### A1. Instalar dependências e preparar o host (guiado)

```bash
sudo apt-get update && sudo apt-get install -y openssh-server rsync
sudo ./collection/rsync-hub install   # instalação GUIADA: pasta raiz → conta → estações
```

O assistente conduz passo a passo: pergunta a **pasta raiz** dos dados (default
`/srv/carbonara`) e a **conta de serviço** (default `carbonara`, senha travada, só chave),
cria `incoming/`, `log/` e `.ssh/authorized_keys`, grava as escolhas em
`/etc/rsync-hub.conf`, oferece a cópia para `/usr/local/bin/rsync-hub` e já emenda na
autorização das primeiras estações. Para automação sem perguntas:
`sudo ./collection/rsync-hub install --defaults`. Se o `rrsync` não for encontrado, o assistente
avisa (ele acompanha o pacote `rsync`).

### A2. Autorizar cada estação

O jeito mais simples é **gerar a chave da estação no próprio servidor**:

```bash
sudo rsync-hub add flona01     # sem arquivo: gera o par + registra + cria incoming/flona01
rsync-hub list                 # confere as estações autorizadas
```

Isso cria `/srv/carbonara/stations/flona01/` com a **chave privada** (`flona01_key` —
leve-a para a estação), a pública (permanece lá, já autorizada) e um **`INSTRUCOES.txt`**
com o passo a passo de ativação para o operador (ver
[`collection/station/README.md`](../collection/station/README.md)).

> **Leitura sem sudo:** os arquivos de `stations/` (e os dados de `incoming/`/`archive/`)
> são legíveis por qualquer usuário local da workstation — dá para copiar a chave e as
> instruções para o operador sem `sudo`. A blindagem de **escrita** vale para as
> estações remotas; o host é considerado confiável. Instalações antigas: normalize com
> `sudo chmod -R u+rwX,go+rX /srv/carbonara/stations`.

Alternativas:

- a estação gera a própria chave no `setup` do cliente e te apresenta a **chave
  pública** — autorize com `sudo rsync-hub add flona01 flona01.pub` (ou colando a linha
  no menu interativo → "Adicionar estação");
- **usuário+senha** (sem chave nenhuma — o mais simples no cliente):
  `sudo rsync-hub add flona01 --senha` cria o usuário (= id da estação) e gera a senha,
  servida por um **rsync daemon** na porta `873` (serviço `carbonara-rsyncd`, gerenciado
  pelo próprio `rsync-hub`; módulo write-only, `--delete` recusado). **Atenção:** a
  senha não trafega em claro, mas os **dados sim** — prefira chave fora de redes
  fechadas/VPN.

Instruções prontas para passar ao operador: `rsync-hub cliente`.

Cada chave fica amarrada ao seu `serve <id>`: a estação só escreve na **própria** pasta, em
modo write-only (não baixa nem alcança as outras). Para revogar: `sudo rsync-hub remove flona01`
(o acesso some; os dados permanecem).

### A3. Acompanhar as conexões

```bash
rsync-hub              # TELA PADRÃO: monitor ao vivo (Ctrl+O abre o menu, q sai)
rsync-hub monitor      # o mesmo monitor, chamado explicitamente
rsync-hub status       # uma leitura só
rsync-hub menu         # vai direto ao menu interativo
```

Rodar `rsync-hub` sem argumentos abre o **monitor**, que é a tela em que se fica: tabela
ao vivo por estação e últimas conexões. Dentro dele, **Ctrl+O** abre o menu com todas as
opções (instalar, autorizar estação, pausar…) e a opção `0` volta ao monitor; **q** sai e
qualquer outra tecla força uma atualização imediata. Sem terminal interativo (num pipe ou
no cron), `rsync-hub` sem argumentos faz uma leitura só, em vez de ficar em laço.

> Por que Ctrl+O e não outra tecla: é o atalho pedido, mas ^O é por padrão o caractere
> `discard` do terminal (`stty -a` mostra `discard = ^O`) — o driver o intercepta antes de
> qualquer programa. O monitor desliga esse caractere enquanto roda e devolve o terminal
> exatamente como estava ao sair (inclusive por Ctrl-C). Se algum terminal ainda assim
> engolir o ^O, **m** abre o mesmo menu.

A tabela tem uma linha por estação e sete colunas:

| coluna | o que é |
| --- | --- |
| **ESTAÇÃO** | id da estação (a pasta em `incoming/`) |
| **ESTADO** | *Recebendo dados* · *Online* · *Offline* · *Pausada* (a bolinha repete em cor) |
| **ÚLTIMO SINAL** | hora local do último sinal de vida e **há quanto tempo** (fora de hoje, dia/mês + hora) |
| **CICLO** | de quanto em quanto tempo a estação declara que envia |
| **VELOCIDADE** | taxa útil da **última** sessão medida (payload ÷ duração) |
| **PING** | latência do enlace na **última** sessão medida |
| **30 DIAS** | total recebido da estação nos últimos 30 dias |

Velocidade e ping são sempre da ÚLTIMA sessão — é o que responde "como está o enlace
agora"; as **médias** (e outros recortes de período) ficam em `rsync-hub volume`. Só
entram sessões ocorridas a partir da versão que grava a medição no log. A velocidade
ignora sessões abaixo de 64 kB — nelas o tempo é quase todo handshake, e o número
falaria do ping, não do enlace. As colunas de velocidade e ping acompanham cada ciclo do
monitor; o consumo de 30 dias é recalculado no máximo uma vez por minuto (a varredura é
de 30 dias de log).

O **PING é medido ao contrário**: o hub não manda pacote nenhum para a estação (ela
costuma estar atrás de NAT, sem endereço fixo). Quem conecta é a estação, e o kernel do
servidor já mede o RTT dessa conexão TCP — o `serve` só lê esse valor (`ss -tin`) e o
grava como `rtt=` na linha DISCONNECT. Custo zero de tráfego, funciona atrás de NAT e não
depende de ICMP. Ao contrário da velocidade, o ping NÃO tem piso de tamanho: ele não
depende do quanto foi enviado, então os ciclos que só mandam o heartbeat também contam.
Havendo NAT, o que se mede é o RTT até o endereço público da estação — em 4G/satélite
esse é justamente o trecho que domina; numa estação atrás de um roteador local falta só
o último salto, irrelevante. Sem `ss` no servidor a coluna fica `-`, nunca um número
inventado.

Antes da tabela vêm **duas linhas de rede do servidor**: a interface com a rota padrão
(a que tem internet) com a taxa ↓/↑ ao vivo e o total desde o boot; depois as portas
22/873, quantas conexões houve na última hora e em 24 h, e quais estações estão
**recebendo agora**.
No `monitor` a taxa sai de graça (a janela é o próprio intervalo de atualização); no
`status`, que é uma leitura só, ele mede por 1 segundo.

Status de cada estação (bolinha colorida): ● **roxo** = *Recebendo dados* (sessão aberta
agora ou arquivos chegando); ● **verde** = *Online* (atividade na última hora — ajuste
com `RSYNC_HUB_STALE`); ● **vermelho** = *Offline* (sem atividade além da janela);
● **amarelo** = *Pausada* (recepção suspensa de propósito — veja abaixo).

### A4. Quanto cada estação consome

```bash
rsync-hub volume          # hoje, 7 dias e 30 dias, por estação, + TOTAL
rsync-hub volume 90       # outra janela
```

Soma o payload entregue por sessão (o mesmo `bytes=` que alimenta a coluna VELOCIDADE)
e mostra, nas duas últimas colunas, a **velocidade média** e o **ping médio** do período
escolhido — é aqui que ficam as médias que o monitor não mostra. Serve para dimensionar
plano de dados/link: quem consome mais, quem consome menos, quem tem o pior enlace. Só
conta sessões ocorridas a partir da versão que registra a medição.

### A5. Quando as estações param de enviar

```bash
rsync-hub diagnostico              # só leitura: disco, chaves, home da conta, pausa, log
sudo rsync-hub diagnostico --reparar   # reautoriza com o .pub que o hub já guarda
```

Mensagens típicas na estação e o que significam:

| Mensagem no cliente | Onde falhou | Causa comum |
|---|---|---|
| `Permission denied (publickey,password)` | autenticação do **sshd** — nem chegou ao rsync-hub | a chave saiu do `authorized_keys` (ex.: gravação com o **disco cheio**), ou o home da conta de serviço não é mais a raiz (`move-root` interrompido) |
| `rsync-hub: somente rsync (envio) é permitido` | comando forçado | alguém tentou usar a chave para outra coisa |
| `rsync-hub: recepção de dados PAUSADA` | comando forçado | pausa intencional (veja A6) |
| `No space left on device` / rc 11 | gravação em `incoming/` | disco do servidor cheio |

**Disco cheio é a causa raiz mais frequente**: libere espaço primeiro (`df -h`,
arquivos antigos em `archive/`), depois rode o `diagnostico`. Se ele apontar estações
`RECUPERÁVEL`, o `--reparar` recoloca as chaves usando os `.pub` guardados em
`<raiz>/stations/<id>/` — a estação **não** precisa de chave nova nem de reconfiguração.

### A6. Pausar e retomar a recepção

Para uma manutenção (disco, migração da raiz, banco parado) você pode suspender a
**chegada** de dados sem revogar nada e sem apagar nada:

```bash
sudo rsync-hub pause --todas "manutenção do servidor"   # pausa TODAS as estações
sudo rsync-hub pause flona01 "troca do disco"           # pausa só uma
sudo rsync-hub resume --todas                           # retoma todas
sudo rsync-hub resume flona01                           # retoma uma
```

As estações continuam autorizadas: elas tentam enviar, recebem uma recusa explicando a
pausa e **reenviam tudo no primeiro ciclo depois de retomada** — o estado local do
cliente só avança em envio bem-sucedido, então nada se perde (só atrasa). Vale para os
dois modos de autenticação: no modo chave o `serve` recusa a sessão (registra `PAUSED`
no `connections.log`); no modo usuário+senha o módulo da estação sai do `rsyncd.conf`.

A pausa global e as pausas por estação são independentes — `resume --todas` não
desfaz uma pausa individual. O estado fica em marcadores `<raiz>/pause/` (`_todas` ou
`<id>`) e aparece também na dashboard, que tem botões para pausar/retomar.

---

## Parte B — Banco de dados (Docker Compose)

O desenho completo (o que entra no banco, tratamento, API, fases) está em
[`estrategia-dados.md`](estrategia-dados.md). Esta parte cobre a **fase 0**: o banco no ar,
no disco de dados, pronto para ser espelhado.

### B1. Preparar o host (uma vez, como root)

```bash
sudo ./database/setup-db-host.sh            # disco padrão /mnt/data_8tb; --disco para outro
sudo ./database/setup-db-host.sh --subir    # idem, e já sobe o banco ao final
```

O script instala o Docker com o `data-root` **no disco de dados** (o disco root da
workstation está quase cheio — nada do banco pode ir para lá), cria
`<disco>/carbonara-db/pgdata`, põe seu usuário no grupo `docker`, limita o journald e
gera o `.env` com senhas aleatórias (nunca sobrescreve um `.env` existente). Saia e entre
de novo para o grupo `docker` valer. Ele não toca na coleta, na dashboard nem no FTP.

O `.env` gerado tem `CARBONARA_ROOT` (a MESMA raiz do rsync-hub, lida de
`/etc/rsync-hub.conf`), `PG_DATA_DIR`, `DB_*`, `REPLICATION_PASSWORD`, `API_PORT` e
`API_KEY_PEPPER` — modelo em `.env.example`.

### B2. Subir e verificar o banco

```bash
docker compose up -d          # sobe só o db (a ingestão fica no perfil `ingestao`, ver abaixo)
docker compose ps             # carbonara-db deve ficar "healthy"
docker exec -it carbonara-db psql -U carbonara -d carbonara -c "\dt"
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT * FROM timescaledb_information.hypertables;" \
  -c "SHOW wal_level;"        # replica — pronto para o espelho
```

No **primeiro** start (diretório `PG_DATA_DIR` vazio), o container executa
`database/init/01_*.sql`, `02_*.sql`, `03_*.sql` (extensões, schema, hypertable) e
`04_replication.sh` (papel `replicator` + `pg_hba`). Para reaplicar o schema em dev:
`docker compose down` e esvaziar `PG_DATA_DIR` (destrói os dados).

O PostgreSQL escuta só em `127.0.0.1:5432` (`DB_BIND`). Quando o espelho existir, libere
a porta para o IP dele e rode lá `pg_basebackup -h <hub> -U replicator -R -X stream`.

### B3. Ingestão e processamento (QA/QC)

A ingestão **não sobe por padrão**: a versão atual move arquivos de `incoming/`, o que
é incompatível com a coleta (os `.dat` crescem por append e a dashboard faz tail neles).
Ela é reescrita na fase 1 para ler só os arquivos monitorados, de forma incremental.
Para testes em dev (raiz `./data`, nunca em produção):

```bash
cp ingestion/config.example.yaml ingestion/config.yaml     # mapa coluna->variável
cp processing/config.example.yaml processing/config.yaml   # faixas físicas (QC)
docker compose --profile ingestao up -d --build ingestion
docker compose logs -f ingestion
```

### B4. Porta da API

A API de dados (fase 0/1) escuta em `API_PORT` (padrão **8000/TCP**). Peça ao STI a
liberação de **entrada** dessa porta no servidor; o 5432 não precisa (fica local). Sem
domínio/certificado a API sai em HTTP; quando houver, o apache2 já instalado faz o proxy
com TLS. O acesso é por chave de API (`X-API-Key`), gerida pela dashboard.

### B5. Distribuição pelo FTP

**Servidor com vsftpd nativo** (o caso da workstation de produção — instalação em
[`ftp-native-install.md`](ftp-native-install.md)): as contas e as **publicações** (o que do
hub aparece no FTP) são administradas pela página **FTP** da dashboard. Prepare o host uma vez:

```bash
sudo ./ftp/ftp-hub install <usuario-que-roda-a-dashboard>   # instala o script + regra sudo
```

Depois, na dashboard (admin) → **FTP**: crie contas (`leitura` / `escrita` / `upload`, presas a
uma subpasta) e publicações ("`incoming/<estação>` → `dados/<estação>`, a cada 60 min", com
filtros). A origem nunca é alterada; detalhes em [`../ftp/README.md`](../ftp/README.md).

---

## Teste ponta a ponta

Valida `incoming/ → ingestão → banco → archive/`, sem depender de uma estação real. Simula a
chegada de um arquivo (como se o `rsync-hub` o tivesse recebido) e acompanha o resto:

```bash
# 1. Simula a chegada no incoming/ (use a MESMA raiz do .env / rsync-hub).
sudo install -d -o carbonara -g carbonara /srv/carbonara/incoming/flona01
sudo cp examples/sample-station-data/flona01_20260627.csv /srv/carbonara/incoming/flona01/

# 2. Aguarde um ciclo de ingestão (INGEST_POLL_SECONDS) e veja os logs.
docker compose logs -f ingestion     # deve registrar "medições lidas / inseridas"

# 3. Confira os dados no banco.
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT count(*), min(time), max(time) FROM measurements;"
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT * FROM measurements ORDER BY time LIMIT 5;"

# 4. Confirme que o arquivo foi para archive/ (e não restou no incoming/).
sudo ls -R /srv/carbonara/archive/flona01
```

> Em desenvolvimento, sem `CARBONARA_ROOT`, troque `/srv/carbonara` por `./data` (e dispense o
> `sudo`): `mkdir -p data/incoming/flona01 && cp examples/... data/incoming/flona01/`.

Para testar uma estação **real** enviando por rsync-over-SSH (em vez de copiar o arquivo à
mão), use o cliente guiado em [`collection/station/`](../collection/station/README.md) — `setup` → autorizar no
host → `test` → `send` — ou o agente completo do repo
[`carbonara-stations`](https://github.com/CarbonARA-UFOPA/carbonara-stations).

---

## Parar / atualizar

```bash
docker compose down            # para o stack de dados (mantém os volumes/dados)
docker compose pull && docker compose up -d --build   # atualizar imagens/serviço

# A coleta não precisa ser "parada": o rsync-hub só roda sob demanda (gerência/monitor) e
# como comando forçado a cada conexão. Para suspender uma estação: sudo rsync-hub remove <id>.
```
