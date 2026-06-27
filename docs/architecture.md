# Arquitetura

## Objetivo

Coletar os arquivos gerados pelos sensores nas estações meteorológicas, concentrá-los em
um único ponto na workstation central e armazená-los de forma **estruturada** (séries
temporais) para análise — de modo **flexível**, permitindo trocar a infraestrutura de
coleta sem reescrever todo o sistema.

## Fluxo de dados

```
ESTAÇÃO                WORKSTATION CENTRAL — wrkst02 (10.1.90.246)
                       FTP: nativo (vsftpd)   ·   db + ingestion: Docker Compose

┌─────────────┐      ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  collector  │      │     FTP     │      │  ingestion  │      │ TimescaleDB │
│ Python/Shell│─FTPS▶│   vsftpd    │─────▶│   Python    │─────▶│  PostgreSQL │
│   + cron    │ upld │  collect/   │ poll │ parse→load  │ load │ +TimescaleDB│
└─────────────┘      └─────────────┘      └─────────────┘      └─────────────┘
  só o novo;          /srv/carbonara/      ok  → archive/        séries
  retry+backoff       collect/<estação>/   erro→ quarantine/     temporais
```

> O diretório de coleta (`/srv/carbonara/collect/`) é a **fronteira física** entre o transporte
> e a ingestão: o FTP só escreve nele, a ingestão só lê dele. Nenhum dos lados conhece o outro.

## As três fronteiras de desacoplamento

O sistema é dividido em três camadas **independentes e substituíveis**. Esse é o princípio
que garante a flexibilidade pedida no projeto.

| # | Camada | Responsabilidade | Como trocar |
|---|--------|------------------|-------------|
| 1 | **Transporte** | Como o arquivo viaja da estação ao servidor | Nova classe em `common/python/transports/` (repo `carbonara-stations`) + `transport.type` no config. FTPS → SFTP/rsync sem tocar no resto. |
| 2 | **Ingestão / parsing** | Como o arquivo vira linha no banco | Novo parser em `ingestion/parsers/` (este repo) + entrada no `config.yaml`. Só lê o diretório de coleta; ignora como o arquivo chegou. |
| 3 | **Armazenamento** | Persistir as séries temporais | TimescaleDB. Trocar o banco afeta só a ingestão; coletores não mudam. |

**Consequência prática:** a ingestão e o banco dependem apenas do **diretório de coleta**
(`/srv/carbonara/collect/`). Trocar o servidor FTP por SFTP ou rsync muda só a camada de
transporte (no servidor e no agente) — o resto continua idêntico.

## Componentes

### Agente de coleta (repo `carbonara-stations`) — roda nas estações
- Scripts simples **sem Docker** (estações podem ter hardware limitado), agendados por **cron**.
- **Python** (padrão, robusto) e **Shell** (fallback sem dependências) — escolha por estação.
- Mantém **estado** dos arquivos já enviados (assinatura tamanho+mtime) → idempotente,
  envia só o novo, marca como enviado **somente após sucesso**.
- **Retry com backoff** para conexão de campo instável (Amazônia).
- Camada de transporte plugável (`transports/`); hoje FTP/FTPS via `ftplib` (lib padrão).

### Servidor central (este repo, `carbonara-datahub`) — workstation `wrkst02`
Deploy **híbrido**: o FTP roda **nativo** no host (controle fino de permissão e menos
manutenção); o banco e a ingestão sobem por **Docker Compose**.

- **`ftp` (nativo, vsftpd)** — ponto único onde as estações concentram arquivos, em
  `/srv/carbonara/collect/<estação>/`. **FTPS opcional** (TLS oferecido, não obrigatório,
  para acomodar estações sem suporte a TLS). Modelo de dois papéis (ver abaixo).
  Instalação registrada passo a passo em [`ftp-native-install.md`](ftp-native-install.md).
- **`db` (compose)** — PostgreSQL + TimescaleDB; schema inicializado por `database/init/*.sql`.
- **`ingestion` (compose)** — serviço Python que varre o diretório de coleta, faz parse e
  carrega no banco; move arquivos processados para `archive/` e os com erro para `quarantine/`.

> O `docker-compose.yml` ainda traz um serviço `ftp` (pure-ftpd) como alternativa containerizada;
> a instalação **vigente** é a nativa em vsftpd. Os dois caminhos escrevem no mesmo diretório de
> coleta, então a ingestão é indiferente a qual está em uso.

### Modelo de permissão do FTP (dois papéis)

Protege os dados coletados contra exclusão acidental ou maliciosa por uma estação:

| Papel | Usuário | Pode | Não pode |
|-------|---------|------|----------|
| **Upload** (estações) | `ftpupload` | enviar arquivos (`STOR`), criar pastas (`MKD`) | apagar, renomear, baixar (`DELE`/`RMD`/`RNFR`/`RETR`) |
| **Master** (gestão) | `ftpmaster` | ler, escrever e **apagar** tudo | — |

O "escreve mas não apaga" é imposto **nativamente** pelo vsftpd (`cmds_denied` por usuário) —
um motivo decisivo para o vsftpd no lugar do pure-ftpd, que não tem ACL por comando. A ingestão,
ao mover os arquivos de `collect/` para `archive/`, os tira do alcance da estação, fechando também
a possibilidade de uma sobrescrita por `STOR` em arquivo ainda não ingerido.

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
| Deploy central | Docker Compose (db + ingestão) + FTP nativo | Compose dá portabilidade ao stack de dados; FTP nativo dá controle fino de permissão e menos manutenção. |
| Servidor FTP | **vsftpd nativo** (apt) | "Escreve mas não apaga" por usuário (`cmds_denied`) de forma nativa; pure-ftpd não tem ACL por comando. |
| Criptografia FTP | FTPS **opcional** | Estações sem suporte a TLS precisam conectar; TLS oferecido a quem puder. Em LAN fechada o trade-off é aceitável. |
| Modelo de acesso | `ftpmaster` (r/w/delete) + `ftpupload` (só escrita) | Proteger os dados coletados contra exclusão por uma estação. |
| Coleta | Scripts + cron (sem Docker) | Estações nem sempre suportam Docker. |
| Linguagem do coletor | Python padrão + Shell | Cobre estações com e sem Python. |
| Transporte | FTP/FTPS, com camada plugável | Requisito de FTP simples + flexibilidade futura (SFTP/rsync). |
| Repositório | Multi-repo: `carbonara-datahub` + `carbonara-stations` | Separar server e coletores: papéis, ciclos de vida e deploys distintos. |
