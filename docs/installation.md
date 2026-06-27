# Instalação do servidor central

Guia para subir a workstation central (FTP/FTPS + TimescaleDB + ingestão) com Docker Compose.

## Pré-requisitos

- Docker Engine 24+ e Docker Compose v2 (`docker compose version`).
- Portas livres: `21` (FTP), faixa passiva `30000-30009`, `5432` (PostgreSQL).
- ~Espaço em disco proporcional ao volume de dados das estações (landing + archive + banco).

## Passos

### 1. Clonar o repositório

```bash
git clone https://github.com/CarbonARA-UFOPA/carbonara-datahub.git
cd carbonara-datahub
```

### 2. Configurar o ambiente

```bash
cp .env.example .env
# Edite .env: defina DB_PASSWORD, FTP_USER_PASS e FTP_PUBLIC_HOST (IP/hostname da workstation).
```

### 3. Gerar o certificado TLS do FTP (FTPS)

```bash
mkdir -p ftp/tls
openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
  -keyout ftp/tls/pure-ftpd.pem -out ftp/tls/pure-ftpd.pem -subj "/CN=carbonara-ftp"
chmod 600 ftp/tls/pure-ftpd.pem
```
> Detalhes e variante com CA real em [`../ftp/README.md`](../ftp/README.md).

### 4. Configurar a ingestão

```bash
cp ingestion/config.example.yaml ingestion/config.yaml
# Ajuste o mapeamento coluna->variável conforme o formato real dos seus dataloggers.
```

### 5. Subir os serviços

```bash
docker compose up -d
docker compose ps          # todos devem ficar "healthy"/"running"
docker compose logs -f ingestion
```

No **primeiro** start, o container do banco executa `database/init/01_*.sql`,
`02_*.sql` e `03_*.sql` automaticamente (extensões, schema e hypertable).

### 6. Verificar o banco

```bash
docker exec -it carbonara-db psql -U carbonara -d carbonara -c "\dt"
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT * FROM timescaledb_information.hypertables;"
```

### 7. Criar um usuário FTP por estação

```bash
docker exec -it carbonara-ftp \
  pure-pw useradd estacao_flona01 -f /etc/pure-ftpd/passwd/pureftpd.passwd \
  -m -u ftpuser -d /home/ftpusers/carbonara/flona01
docker exec -it carbonara-ftp pure-pw mkdb
```

## Teste ponta a ponta

Valida estação → FTP → ingestão → banco, usando o CSV de exemplo.

```bash
# 1. Simula a chegada de um arquivo no landing (como se o agente tivesse enviado).
mkdir -p data/landing/flona01
cp ../examples/sample-station-data/flona01_20260627.csv data/landing/flona01/

# 2. Aguarde um ciclo de ingestão (INGEST_POLL_SECONDS) e veja os logs.
docker compose logs -f ingestion     # deve registrar "medições lidas / inseridas"

# 3. Confira os dados no banco.
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT count(*), min(time), max(time) FROM measurements;"
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT * FROM measurements ORDER BY time LIMIT 5;"

# 4. Confirme que o arquivo foi para archive/ (e não restou no landing/).
ls -R data/archive/flona01
```

Para testar o **agente real** enviando via FTP (em vez de copiar para o landing), veja
[`station-setup.md`](station-setup.md).

## Parar / atualizar

```bash
docker compose down            # para (mantém os volumes/dados)
docker compose pull && docker compose up -d --build   # atualizar imagens/serviço
```
