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

### Coleta: host guiado (`rsync-hub`) + clientes (`station/`)

O lado **host** (esta workstation) é um script guiado — o `install` conduz passo a passo:
pasta raiz → conta de serviço → autorização das estações:

```bash
sudo ./rsync-hub install              # instalação GUIADA do host — 1x
sudo ./rsync-hub add flona01          # autoriza GERANDO a chave aqui: stations/flona01/
                                      #   guarda o par + INSTRUCOES.txt de ativação
sudo ./rsync-hub add flona01 f.pub    # ou autoriza uma chave já existente
sudo ./rsync-hub add flona01 --senha  # ou por USUÁRIO+SENHA (rsync daemon:873 — mais
                                      #   simples no cliente; dados sem criptografia)
     ./rsync-hub list                 # lista estações
     ./rsync-hub monitor              # fica rodando mostrando as conexões
     ./rsync-hub cliente              # instruções p/ configurar uma estação
     ./rsync-hub                      # menu interativo
```

O lado **cliente** roda nas estações (Linux **ou Windows**) — assistentes guiados em
[`station/`](station/): `carbonara-station.sh` (Linux) e `carbonara-station.ps1` +
`carbonara-station.cmd` (Windows). O `setup` pergunta, em ordem: IP do host (porta
padrão 22) → caminho da chave recebida → teste de conexão → pasta a enviar → tempo de
reciclagem (com ativação do envio automático). Ver [`station/README.md`](station/README.md).

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
├── rsync-hub                 # coleta, lado HOST: instalação guiada + gerência + monitor + comando forçado
├── station/                  # coleta, lado CLIENTE: assistentes guiados p/ estações Linux e Windows
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
