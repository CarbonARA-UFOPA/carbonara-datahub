# Instalação do servidor central

Guia para subir a workstation central do CARBONARA. São **duas partes independentes**:

- **A. Coleta** — `rsync-over-SSH`, **nativa no host**, toda pelo script [`rsync-hub`](../rsync-hub).
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
sudo ./rsync-hub install          # instalação GUIADA: pasta raiz → conta → estações
```

O assistente conduz passo a passo: pergunta a **pasta raiz** dos dados (default
`/srv/carbonara`) e a **conta de serviço** (default `carbonara`, senha travada, só chave),
cria `incoming/`, `log/` e `.ssh/authorized_keys`, grava as escolhas em
`/etc/rsync-hub.conf`, oferece a cópia para `/usr/local/bin/rsync-hub` e já emenda na
autorização das primeiras estações. Para automação sem perguntas:
`sudo ./rsync-hub install --defaults`. Se o `rrsync` não for encontrado, o assistente
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
[`station/README.md`](../station/README.md)).

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
rsync-hub monitor      # tabela ao vivo (pendentes/última atividade) + últimas conexões; Ctrl-C sai
rsync-hub status       # uma leitura só
rsync-hub              # menu interativo com todas as opções acima
```

---

## Parte B — Dados (Docker Compose)

### B1. Configurar o ambiente (`.env`)

Crie o `.env` (use `.env.example` como base, se presente) com, no mínimo:

```bash
CARBONARA_ROOT=/srv/carbonara     # MESMA raiz usada pelo rsync-hub
DB_USER=carbonara
DB_PASSWORD=troque-esta-senha
DB_NAME=carbonara
FTP_USER_NAME=carbonara           # usuário de leitura da distribuição FTP
FTP_USER_PASS=troque-esta-senha
FTP_PUBLIC_HOST=10.1.90.246       # IP/hostname da workstation (para o modo passivo do FTP)
```

`CARBONARA_ROOT` é o que faz os containers montarem `incoming/`, `archive/` e `quarantine/` da
mesma raiz que a coleta usa. Sem defini-lo, o Compose cai no default de desenvolvimento (`./data`).

### B2. Configurar a ingestão

```bash
cp ingestion/config.example.yaml ingestion/config.yaml
# Ajuste o mapa coluna->variável (bloco `columns:`) conforme os dataloggers reais.
```

### B3. Gerar o certificado TLS do FTP (FTPS)

```bash
mkdir -p ftp/tls
openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
  -keyout ftp/tls/pure-ftpd.pem -out ftp/tls/pure-ftpd.pem -subj "/CN=carbonara-ftp"
chmod 600 ftp/tls/pure-ftpd.pem
```
> Detalhes e variante com CA real em [`../ftp/README.md`](../ftp/README.md).

### B4. Subir os serviços

```bash
docker compose up -d
docker compose ps          # db, ingestion e ftp devem ficar "healthy"/"running"
docker compose logs -f ingestion
```

No **primeiro** start, o container do banco executa `database/init/01_*.sql`, `02_*.sql` e
`03_*.sql` automaticamente (extensões, schema e hypertable).

### B5. Verificar o banco

```bash
docker exec -it carbonara-db psql -U carbonara -d carbonara -c "\dt"
docker exec -it carbonara-db psql -U carbonara -d carbonara \
  -c "SELECT * FROM timescaledb_information.hypertables;"
```

### B6. Distribuição pelo FTP

O serviço `ftp` **não recebe** dados — serve `archive/` como **somente leitura**. Consumidores
baixam com o usuário `FTP_USER_NAME`/`FTP_USER_PASS` (via FTPS). Nada de upload por aqui.

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
mão), use o cliente guiado em [`station/`](../station/README.md) — `setup` → autorizar no
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
