# Arquitetura

## Objetivo

Coletar os arquivos gerados pelos sensores nas estações meteorológicas, concentrá-los em
um único ponto na workstation central e armazená-los de forma **estruturada** (séries
temporais) para análise — de modo **flexível**, permitindo trocar a infraestrutura de
coleta sem reescrever todo o sistema.

## Fluxo de dados

```
ESTAÇÃO                WORKSTATION CENTRAL — wrkst02 (10.1.90.246)
                       coleta: sshd nativo (rsync)   ·   db + ingestion + processing + ftp: Docker Compose

┌─────────────┐      ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  collector  │rsync │  rsync-hub  │      │  ingestion  │      │ TimescaleDB │
│ Python/Shell│──────▶│ sshd + forced│────▶│   Python    │─────▶│  PostgreSQL │
│   + cron    │ ssh  │  rrsync -wo  │ poll │ parse→load  │ load │ +TimescaleDB│
└─────────────┘ push └─────────────┘      └─────────────┘      └─────────────┘
  só o novo;          /srv/carbonara/      ok  → archive/ ──▶ FTP (distribuição, r/o)
  retry+backoff       incoming/<estação>/  erro→ quarantine/     séries temporais
                            │
                            └── rsync-hub grava connections.log · `rsync-hub monitor` (ao vivo)
```

> O diretório de coleta (`/srv/carbonara/incoming/`) é a **fronteira física** entre o transporte
> e a ingestão: o rsync só escreve nele (confinado por estação), a ingestão só lê dele. Nenhum
> dos lados conhece o outro. O FTP deixou de ser entrada e agora só **distribui** `archive/`.

## As fronteiras de desacoplamento

O sistema é dividido em camadas **independentes e substituíveis**. Esse é o princípio
que garante a flexibilidade pedida no projeto.

| # | Camada | Responsabilidade | Como trocar |
|---|--------|------------------|-------------|
| 1 | **Transporte** | Como o arquivo viaja da estação ao servidor | Nova classe em `common/python/transports/` (repo `carbonara-stations`) + `transport.type` no config. Hoje **rsync-over-SSH** (era FTPS); trocar por SFTP/outro não toca no resto. |
| 2 | **Ingestão / parsing** | Como o arquivo vira linha no banco | Novo parser em `ingestion/parsers/` (este repo) + entrada no `config.yaml`. Só lê o diretório de coleta; ignora como o arquivo chegou. |
| 3 | **Processamento / QA-QC** | Marcar a qualidade das medições já inseridas (`measurements.quality`) | Novo check em `processing/checks/` (este repo) + entrada no `config.yaml`. Só fala com o banco; não altera valores crus, só flags. |
| 4 | **Armazenamento** | Persistir as séries temporais | TimescaleDB. Trocar o banco afeta só ingestão/processamento; coletores não mudam. |

**Consequência prática:** a ingestão e o banco dependem apenas do **diretório de coleta**
(`/srv/carbonara/incoming/`). A migração do FTP para rsync-over-SSH mudou só a camada de
transporte (no servidor e no agente) — a ingestão, o schema e o banco continuam idênticos.
Foi exatamente a flexibilidade que este desenho previa.

## Componentes

### Agente de coleta (repo `carbonara-stations`) — roda nas estações
- Scripts simples **sem Docker** (estações podem ter hardware limitado), agendados por **cron**.
- **Python** (padrão, robusto) e **Shell** (fallback sem dependências) — escolha por estação.
- Mantém **estado** dos arquivos já enviados (assinatura tamanho+mtime) → idempotente,
  envia só o novo, marca como enviado **somente após sucesso**.
- **Retry com backoff** para conexão de campo instável (Amazônia).
- Camada de transporte plugável (`transports/`); hoje **rsync-over-SSH** (era FTP/FTPS).
  O próprio rsync cuida do incremental (só o novo) e do resumo de transferência (`--partial`),
  simplificando o estado que o agente mantinha à mão.

### Servidor central (este repo, `carbonara-datahub`) — workstation `wrkst02`
Deploy **híbrido**: a coleta (**sshd + rsync**, orquestrada pelo script `rsync-hub`) roda
**nativa** no host (controle fino de permissão e menos manutenção); banco e ingestão sobem por
**Docker Compose**.

- **`rsync-hub` (nativo, um script)** — faz toda a coleta: autoriza/remove/lista estações,
  **monitora** as conexões (modo ao vivo) e é o **comando forçado** do SSH que confina cada
  estação a `/srv/carbonara/incoming/<estação>/`, **write-only** e sem acesso às demais.
- **`db` (compose)** — PostgreSQL + TimescaleDB; schema inicializado por `database/init/*.sql`.
- **`ingestion` (compose)** — serviço Python que varre `incoming/`, faz parse e carrega no
  banco; move processados para `archive/` e os com erro para `quarantine/`.
- **`ftp` (distribuição)** — **repropósito**: não recebe mais dados; serve `archive/` como
  **read-only** para consumidores. FTPS mantido para o download.

> Histórico: a coleta era FTP (pure-ftpd no compose; depois vsftpd nativo). Migramos para
> rsync-over-SSH — transporte mais eficiente (incremental nativo) e com isolamento por chave.
> O FTP permaneceu apenas como camada de **saída**.

### Modelo de isolamento da coleta (rsync-over-SSH)

Protege os dados de uma estação contra acesso por outra, e os dados centrais contra exclusão
por qualquer estação. Defesa em camadas — simples, mas real (detalhe no cabeçalho do `rsync-hub`):

| Camada | Mecanismo | Garante |
|--------|-----------|---------|
| Conta | uma conta de serviço (`carbonara`) recebe os envios, senha travada | só chave, sem login interativo |
| Chave | uma chave SSH por estação, cada uma amarrada ao seu `serve <id>` | a estação só age como ela mesma |
| SSH | `command="rsync-hub serve <id>",restrict` no `authorized_keys` | sem shell/PTY/túnel; só executa o script |
| rsync | `rrsync -wo <incoming/id>` | confina ao diretório da estação e é **write-only** (envia, não baixa) |

O "escreve mas não apaga" que motivou o vsftpd se mantém: cada chave só escreve na própria
pasta e a ingestão **move** cada arquivo para `archive/` logo após processar, tirando-o do
alcance da estação. Um `--delete` da estação afetaria, no máximo, os próprios arquivos ainda
não ingeridos — nunca os dados centrais.

## Modelo de dados (TimescaleDB)

Modelo **long/EAV**, que aceita novas variáveis sem `ALTER TABLE`:

- **`stations`** — metadados de cada estação (localização, tipo de floresta, fixa/móvel).
- **`variables`** — catálogo de grandezas (temperatura, umidade, CO₂…). Nova variável = `INSERT`.
- **`measurements`** — *hypertable* particionada por tempo. Chave natural
  `(time, station_id, variable_id)` torna a reingestão idempotente. Compressão automática
  após 30 dias + agregado contínuo horário (`measurements_hourly`).

## Decisões registradas

| Decisão | Escolha | Motivo |
|---------|---------|--------|
| Banco | PostgreSQL + TimescaleDB | Séries temporais com SQL padrão, compressão e agregados. |
| Deploy central | Docker Compose (db + ingestão) + coleta nativa | Compose dá portabilidade ao stack de dados; sshd nativo dá controle fino de permissão e menos manutenção. |
| Transporte | **rsync-over-SSH**, com camada plugável | Incremental nativo (só o novo) + `--partial` (resume em link instável) + isolamento por chave via SSH. Substituiu o FTP na coleta. |
| Coleta (gerência + monitor + comando forçado) | **um script `rsync-hub`** | Simplicidade: um só arquivo shell, menu interativo, sem daemon Python nem container extra. |
| Endpoint de coleta | **sshd nativo + `rrsync -wo`** por chave | Confinamento por diretório e write-only, herdados do SSH; conta de serviço única (sem usuário por estação). |
| Papel do FTP | **distribuição read-only** de `archive/` | Deixou de receber (isso é rsync agora); serve os dados processados para consumidores. |
| Monitoramento | `connections.log` + `rsync-hub monitor`/`status` | Saber quem conectou e qual estação ficou muda, direto no script, sem raspar log do sshd. |
| Coleta | Scripts + cron (sem Docker) | Estações nem sempre suportam Docker. |
| Linguagem do coletor | Python padrão + Shell | Cobre estações com e sem Python. |
| Repositório | Multi-repo: `carbonara-datahub` + `carbonara-stations` | Separar server e coletores: papéis, ciclos de vida e deploys distintos. |
