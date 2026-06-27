# carbonara-datahub

Servidor central (workstation) do projeto **CARBONARA** que **concentra e armazena** os
dados das estações meteorológicas. Recebe os arquivos enviados pelos agentes de coleta
(repo [`carbonara-stations`](https://github.com/CarbonARA-UFOPA/carbonara-stations)),
faz o parsing e os carrega de forma estruturada em um banco de séries temporais.

## Serviços (Docker Compose)

| Serviço | O que é |
|---------|---------|
| `ftp` | Servidor FTP/FTPS (pure-ftpd) — ponto único onde as estações depositam os arquivos, em `data/landing/<station_id>/`. |
| `db` | PostgreSQL + **TimescaleDB** — armazena as medições (`measurements` como hypertable). |
| `ingestion` | Serviço Python que observa `data/landing/`, faz parse e carrega no banco; move processados para `data/archive/` e erros para `data/quarantine/`. |

```
estações → FTP/FTPS → data/landing/ → ingestion (parse) → TimescaleDB
                                          └→ archive/ (ok) | quarantine/ (erro)
```

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
├── docker-compose.yml        # ftp + db + ingestion
├── .env.example
├── ftp/                      # config do FTP/FTPS (TLS, usuário por estação)
├── database/init/            # SQL idempotente rodado no 1º start (schema + hypertable)
├── ingestion/                # ingest.py + parsers plugáveis + Dockerfile
├── examples/                 # CSV de exemplo p/ teste ponta a ponta
└── docs/                     # architecture, installation, maintenance
```

## Desacoplamento

A ingestão e o banco dependem **apenas** do diretório `data/landing/` — não sabem como o
arquivo chegou. Trocar o FTP por SFTP/rsync afeta só o serviço de transporte; o resto não muda.
Adicionar um formato novo de arquivo = novo parser em `ingestion/parsers/`. Ver
[`docs/architecture.md`](docs/architecture.md).

## Documentação

- [Arquitetura](docs/architecture.md)
- [Instalação](docs/installation.md)
- [Manutenção](docs/maintenance.md) — backup/restore, logs, troubleshooting.

Parte do projeto [CarbonARA-UFOPA](https://github.com/CarbonARA-UFOPA). Licença: [MIT](LICENSE).
