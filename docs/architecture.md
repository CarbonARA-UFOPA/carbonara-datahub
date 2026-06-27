# Arquitetura

## Objetivo

Coletar os arquivos gerados pelos sensores nas estações meteorológicas, concentrá-los em
um único ponto na workstation central e armazená-los de forma **estruturada** (séries
temporais) para análise — de modo **flexível**, permitindo trocar a infraestrutura de
coleta sem reescrever todo o sistema.

## Fluxo de dados

```
[ ESTAÇÃO METEOROLÓGICA ]                 [ WORKSTATION CENTRAL (Docker Compose) ]
  sensores → pastas locais                  ┌───────────────┐
        │                                    │  FTP/FTPS     │  data/landing/<station_id>/
        │  collector (Python/Shell)          │  (pure-ftpd)  │        │
        │  agendado por cron, a cada N min    └───────────────┘        ▼
        └──────── upload FTPS ──────────────▶                    ┌──────────────┐
        (envia só o novo; estado evita        ┌──────────────┐   │  ingestion   │ poll a cada N s
         reenvio; retry+backoff)              │ TimescaleDB  │◀──│  (Python)    │ parse → load
                                              │ (PostgreSQL) │   └──────────────┘
                                              └──────────────┘   processados → data/archive/
                                                                 com erro     → data/quarantine/
```

## As três fronteiras de desacoplamento

O sistema é dividido em três camadas **independentes e substituíveis**. Esse é o princípio
que garante a flexibilidade pedida no projeto.

| # | Camada | Responsabilidade | Como trocar |
|---|--------|------------------|-------------|
| 1 | **Transporte** | Como o arquivo viaja da estação ao servidor | Nova classe em `common/python/transports/` (repo `carbonara-stations`) + `transport.type` no config. FTPS → SFTP/rsync sem tocar no resto. |
| 2 | **Ingestão / parsing** | Como o arquivo vira linha no banco | Novo parser em `ingestion/parsers/` (este repo) + entrada no `config.yaml`. Só lê `landing/`; ignora como o arquivo chegou. |
| 3 | **Armazenamento** | Persistir as séries temporais | TimescaleDB. Trocar o banco afeta só a ingestão; coletores não mudam. |

**Consequência prática:** a ingestão e o banco dependem apenas do **diretório de pouso**
(`data/landing/`). Trocar o servidor FTP por SFTP ou rsync muda só a camada de transporte
(no servidor e no agente) — o resto continua idêntico.

## Componentes

### Agente de coleta (repo `carbonara-stations`) — roda nas estações
- Scripts simples **sem Docker** (estações podem ter hardware limitado), agendados por **cron**.
- **Python** (padrão, robusto) e **Shell** (fallback sem dependências) — escolha por estação.
- Mantém **estado** dos arquivos já enviados (assinatura tamanho+mtime) → idempotente,
  envia só o novo, marca como enviado **somente após sucesso**.
- **Retry com backoff** para conexão de campo instável (Amazônia).
- Camada de transporte plugável (`transports/`); hoje FTPS via `ftplib` (lib padrão).

### Servidor central (este repo, `carbonara-datahub`) — workstation, via Docker Compose
- **`ftp`**: pure-ftpd com TLS (FTPS); um usuário por estação → `landing/<station_id>/`.
- **`db`**: PostgreSQL + TimescaleDB; schema inicializado por `database/init/*.sql`.
- **`ingestion`**: serviço Python que varre `landing/`, faz parse e carrega no banco;
  move arquivos processados para `archive/` e os com erro para `quarantine/`.

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
| Deploy central | Docker Compose | Portabilidade; trocar infra com baixo acoplamento. |
| Coleta | Scripts + cron (sem Docker) | Estações nem sempre suportam Docker. |
| Linguagem do coletor | Python padrão + Shell | Cobre estações com e sem Python. |
| Transporte | FTPS, com camada plugável | Requisito de FTP simples + flexibilidade futura. |
| Repositório | Multi-repo: `carbonara-datahub` + `carbonara-stations` | Separar server e coletores: papéis, ciclos de vida e deploys distintos. |
