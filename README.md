# carbonara-datahub

Servidor central (workstation) do projeto **CARBONARA** que **concentra e armazena** os
dados das estações meteorológicas. Recebe os arquivos enviados pelos agentes de coleta
(repo [`carbonara-stations`](https://github.com/CarbonARA-UFOPA/carbonara-stations)) via
**rsync-over-SSH**, faz o parsing e os carrega de forma estruturada em um banco de séries
temporais.

## Componentes

Coleta **nativa no host** (um script, `rsync-hub`) + stack de dados em **Docker Compose**:

| Componente | O que é |
|------------|---------|
| `rsync-hub` (host) | **Um script** que faz tudo da coleta: autoriza/remove/lista estações, monitora as conexões (modo ao vivo) e serve de comando forçado do SSH que confina cada estação (write-only) à sua pasta `incoming/<station_id>/`. |
| `db` (compose) | PostgreSQL + **TimescaleDB** — armazena as medições (`measurements` como hypertable). |
| `ingestion` (compose) | Serviço Python que observa `incoming/`, faz parse e carrega no banco; move processados para `archive/` e erros para `quarantine/`. |
| `ftp` (compose) | **Distribuição read-only**: serve `archive/` para consumidores. Não recebe mais dados. |

```
estações → rsync/ssh → incoming/ → ingestion (parse) → TimescaleDB
             (rsync-hub)   │            └→ archive/ (ok) → FTP (distribuição) | quarantine/ (erro)
             registra conexões em log; `rsync-hub monitor` mostra ao vivo
```

### Coleta em um script (`rsync-hub`)

```bash
sudo ./rsync-hub install              # prepara o host (conta + pastas + chave) — 1x
sudo ./rsync-hub add flona01 flona01.pub   # autoriza uma estação
     ./rsync-hub list                 # lista estações
     ./rsync-hub monitor              # fica rodando mostrando as conexões
     ./rsync-hub                       # menu interativo
```

Isolamento simples e real: cada estação tem uma **chave** que só executa `rsync-hub serve
<id>` (via `command="…",restrict` no `authorized_keys` — sem shell/túnel/PTY), e o
`rrsync -wo` tranca o rsync na pasta da estação, em modo **só-escrita** (envia, não baixa
nem alcança as outras). A ingestão move o arquivo para `archive/` logo após processar,
tirando-o do alcance da estação.

## Início rápido

```bash
cp .env.example .env          # defina senhas e FTP_PUBLIC_HOST
cp ingestion/config.example.yaml ingestion/config.yaml
# gere o certificado TLS do FTP (ver ftp/README.md)
docker compose up -d
docker compose ps
```

Passo a passo completo e teste ponta a ponta em [`docs/installation.md`](docs/installation.md).

## Estrutura

```
carbonara-datahub/
├── docker-compose.yml        # db + ingestion + ftp (distribuição)
├── .env.example
├── rsync-hub                 # script único da coleta: gerência + monitor + comando forçado
├── ftp/                      # config do FTP/FTPS (agora só distribuição)
├── database/init/            # SQL idempotente rodado no 1º start (schema + hypertable)
├── ingestion/                # ingest.py + parsers plugáveis + Dockerfile
├── examples/                 # CSV de exemplo p/ teste ponta a ponta
└── docs/                     # architecture, installation, maintenance
```

## Desacoplamento

A ingestão e o banco dependem **apenas** do diretório `incoming/` — não sabem como o arquivo
chegou. A migração do FTP para rsync-over-SSH trocou só a camada de transporte; ingestão, schema
e banco não mudaram. Adicionar um formato novo de arquivo = novo parser em `ingestion/parsers/`.
Ver [`docs/architecture.md`](docs/architecture.md).

## Documentação

- [Arquitetura](docs/architecture.md)
- [Instalação](docs/installation.md)
- [Manutenção](docs/maintenance.md) — backup/restore, logs, troubleshooting.

Parte do projeto [CarbonARA-UFOPA](https://github.com/CarbonARA-UFOPA). Licença: [MIT](LICENSE).
