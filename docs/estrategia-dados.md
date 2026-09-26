<!-- Estudo produzido em 26/09/2026 a partir do levantamento do servidor wrkst02 (ssh carbonara) e do repositório. Atualize as tabelas de inventário quando o acervo mudar. -->

# Estudo: estratégia de banco de dados e plataforma de dados do CARBONARA

## Contexto

O CARBONARA já resolveu a **coleta** (rsync-hub em produção, 10 estações enviando) e a
**visualização operacional** (dashboard Flask lendo os arquivos). Falta a camada de
**dados como produto**: recepção → tratamento → banco → API. O repositório traz um
TimescaleDB com modelo long/EAV e uma ingestão de CSV genérico, mas **nada disso está em
produção** — no servidor não há Docker nem Postgres; tudo hoje é filesystem.

O ciclo definido pelo usuário (26/09/2026) é:

```
estação → rsync → incoming/<id>/   (bruto, fica como está, é a fonte da verdade)
        → dashboard REGISTRA o arquivo monitorado + as variáveis selecionadas
        → ingestão lê SÓ esses arquivos/variáveis, aplica QC → banco (dados curados)
        → API (porta 8000, chaves de acesso) → dashboard, parceiros, público
```

Decisões já tomadas pelo usuário:
- **Só entra no banco o que está registrado na dashboard** (arquivo monitorado + variáveis
  selecionadas). O restante do acervo (interferogramas EM27, Pandora L0, zips, séries de
  20 Hz não registradas) continua como arquivo em `incoming/`, servido pelo navegador de
  arquivos da dashboard e pelo FTP. Nada de catalogar 49,5 mil arquivos no banco.
- **Banco no mesmo disco do datahub** (`/mnt/data_8tb`), rodando em **Docker**.
- **Sem backup próprio do banco**: ele só contém dados que já passaram pelo fluxo e é
  **reconstruível a partir dos arquivos** (reingestão idempotente). Em breve será
  **espelhado para outro lugar** — o desenho já nasce pronto para replicação.
- `sdb1` continua **sem RAID**.
- **Sem domínio/certificado** por ora: API em porta comum (**8000/TCP**), a liberar junto
  ao STI; **gestão de tokens/chaves** obrigatória desde o início.
- **Fuso horário varia por dado** (UTC ou local): o fuso é atributo de cada arquivo
  registrado; o banco guarda sempre UTC.
- Satélite/LiDAR esperados (fase 3): cenas ópticas, produtos derivados, LiDAR
  aéreo/drone e perfilador atmosférico. Consumidores da API: dashboard + público/parceiros.
- Tratamento na primeira entrega: parse + QC + organização (sem EddyPro/PROFFAST/BlickP).

---

## 1. Levantamento (verificado em 26/09/2026 via `ssh carbonara`)

### 1.1 Servidor wrkst02

| Item | Valor |
|---|---|
| SO / kernel | Ubuntu 22.04.5 LTS, 6.8.0 |
| CPU / RAM | 4 vCPU, 7,6 GB RAM + 2 GB swap, sem GPU |
| Disco root `/dev/sda2` | 457 GB, **79 % usado (94 GB livres)** — nada de dados nele |
| Disco de dados `/dev/sdb1` → `/mnt/data_8tb` | 7,3 TB ext4, 806 GB usados, 6,1 TB livres, disco único |
| BASE do hub | `/mnt/data_8tb/carbonara-data-hub` (`incoming/` 316 GB, ~49,5 mil arquivos) |
| Serviços ativos | rsync-hub (sshd), `carbonara-dashboard` (Flask :8080), apache2, vsftpd |
| Docker / Postgres | **não instalados**; `archive/` e `quarantine/` não existem |
| Python | 3.10 com pandas, xarray, dask, netCDF4, geopandas, scipy; GDAL 3.4; sem psycopg/FastAPI |
| Ingresso (log rsync) | 620 GB/30 d (com backfill), 30 GB/7 d |

Existe processamento **manual** fora do repo (`~/carbonara_data_manage/scripts/organiza_*`,
`/srv/carbonara-dados/*_corrigido`, fluxo `obsproc` do WRFDA) — indica consumidores
futuros (modelagem), mas não entra no escopo deste ciclo.

### 1.2 Inventário por estação (`incoming/`)

| Estação | Tam. | Arq. | Conteúdo | Registrado na dashboard hoje |
|---|---|---|---|---|
| ftir_farm | 77 GB | 23.121 | EM27/SUN interferogramas OPUS `.0001` (binário, ~3,9 MB; 454 GB/30 d) | nenhum arquivo |
| ftir_flona | 33 GB | 9.080 | idem, SN150 | nenhum |
| pandora | 70 GB | 2.117 | Pandora 126 L0 texto + `.bz2` | nenhum |
| fluxfarm | 35 GB | 2.778 | TOA5 `Flux_AmeriFluxFormat`, `Flux_CSFormat` (30 min), `Time_Series` 10–20 Hz | `_Flux_AmeriFluxFormat`, `_Flux_CSFormat`, `_Time_Series` |
| walkuptower | 50 GB | 425 | TOA5 `TableStats` 30 min, `CR3000_ts_data.dat` 5,6 GB, zips | nenhum |
| km67 | 20 GB | 256 | TOA5 `K67CPEC_Time_Series` 20 Hz, zips | `K67CPEC_Time_Series` |
| profsfo | 29 GB | 11.453 | `.sfo` (espaço, epoch, ~118 col., 1 s), tar.gz seriais, telemetria RPi | `.sfo` dinâmico (`%Y-%m-%d__*.sfo`) |
| profkm67 | 4 GB | 89 | TOA5 perfil de gases (IntAvg 30 min, SiteAvg 30 s, RawData 0,5 s) | registrado |
| eddyfarm | 159 MB | 91 | TOA5 radiômetro CR1000X | `CR1000_Proc3K`, `CR1KX_RAD_NASA_Proc3K` |
| radiosonda_pibmt | 108 MB | 99 | RS41 CSV por lançamento | nenhum (só publicação FTP) |

Implicação direta da regra "só o que está registrado": o volume que entra no banco é
**pequeno e controlado**. Mesmo o pior caso hoje registrado — `.sfo` a 1 s com ~10
variáveis selecionadas ≈ 315 milhões de linhas/ano — cabe no Timescale com compressão
(ordem de poucos GB/ano). Registros de 20 Hz (`Time_Series`) precisam de uma opção de
reamostragem no registro (ver 2.2), senão viram ~6 bilhões de linhas/ano por arquivo.

### 1.3 O que o repositório já tem e será reaproveitado

- `database/init/02_schema.sql`: `stations`, `variables`, `measurements(time, station_id,
  variable_id, value, quality, source_file, ingested_at)` com UNIQUE natural;
  `03_hypertables.sql`: chunk 7 d, compressão > 30 d, agregado contínuo `measurements_hourly`.
  Extensões: `timescaledb`, `uuid-ossp` (sem PostGIS).
- `ingestion/ingest.py`: polling, `discover_files` (dotfiles, settle), registry de parsers,
  QC em memória, `execute_values` + `ON CONFLICT DO NOTHING`. **Problema:** move o arquivo
  para `archive/`/`quarantine/` — incompatível com a coleta (os `.dat` são append-only e
  crescem; a dashboard faz tail em `incoming/`). Tem de virar leitura incremental por offset.
- `processing/`: registry de checks (`range_check`), flags 0/1/2/3+.
- **`dashboard/app/tailer.py` + `config_store.py` + `series.py` já sabem ler todos os
  formatos reais** (TOA5 com `skip_header_lines`/`header_line`, `.sfo` com `separator: " "`,
  epoch com auto-escala, `dynamic_path` com curingas, tipos por variável, `min`/`max`).
  Essa é a definição do "arquivo monitorado" — a ingestão deve **consumir a mesma
  configuração e a mesma lógica de parse**, não inventar parsers paralelos.
- `dashboard/app/api.py`: `/api/status`, `/api/netspeed`, `/api/stations/<id>/files/<fid>/series`,
  `/api/stations/<id>/browse`, `POST /api/preview` — tudo lendo arquivos.

---

## 2. Arquitetura recomendada

### 2.1 Decisões-chave

| Tema | Recomendação |
|---|---|
| Fonte da verdade | `incoming/` (bruto, imutável, só leitura para o pipeline). O banco é derivado e reconstruível. |
| O que entra no banco | Exatamente os `stations[].files[].variables[]` registrados na dashboard. O registro **é** a configuração de ingestão. |
| Armazenamento | TimescaleDB, modelo EAV `measurements` mantido (com proveniência, fuso e agregados a mais). Um só motor para tudo que é série. |
| Não-tabulares (EM27, Pandora, zips, 20 Hz não registrado) | Ficam como arquivo. Navegador de arquivos da dashboard + FTP. Fora do banco. |
| Alta frequência registrada | Opção `resample` no registro (`raw`, `1min`, `30min` — média/min/max/count) antes de inserir; padrão `raw` só para cadência ≥ 1 s. |
| Banco | Postgres 16 + TimescaleDB (+ PostGIS quando chegar a fase 3) em Docker; `data-root` do Docker e volume do Postgres em `/mnt/data_8tb`. |
| Espelhamento | `wal_level=replica`, `max_wal_senders`, usuário de replicação e slot criados desde o início; o espelho futuro entra com `pg_basebackup` + streaming replication, sem tocar o primário. |
| API | FastAPI + SQLAlchemy 2 async (asyncpg), container próprio, **porta 8000/TCP**, chaves de API com escopos, expiração e limite de taxa. |
| Fuso | Campo `timezone` (IANA) por arquivo registrado, herdando o global; o banco guarda `timestamptz` (UTC); a API devolve UTC e aceita `?tz=`. |
| Dashboard | Continua Flask; passa a ler séries via API. Ganha a tela de gestão de chaves. |

### 2.2 O registro como contrato de ingestão

Hoje o `files[]` de cada estação em `dashboard/config.yaml` tem `path`, `dynamic_path`,
`separator`, `quotechar`, `decimal`, `skip_header_lines`, `header_line`, `timestamp{column,
format}` e `variables[]{column, label, unit, min, max, type}`. Para virar contrato de
ingestão faltam poucos campos (todos editáveis no formulário existente):

- por arquivo: `ingest: true|false` (ligar/desligar a ingestão sem perder o monitoramento),
  `timezone` (IANA; vazio = herda o global; obrigatório quando o formato não traz fuso),
  `resample: raw|1min|30min`;
- por variável: `variable_id` (slug estável gerado do `label`, editável uma vez, chave no
  banco; hoje só existe `label`), e `min`/`max` passam a alimentar o `range_check`.

Regras:
- A ingestão lê **só** arquivos com `ingest: true` e **só** as colunas listadas em
  `variables[]`. Uma variável removida do registro para de ser ingerida (o histórico fica).
- Variáveis do tipo `texto` não entram (não são série). `booleano`/`inteiro`/`epoch`/
  `timestamp` entram como número, convertidos pelo mesmo `column_display`/`COLUMN_TYPES`
  do tailer.
- `dynamic_path` resolvido pelo relógio do servidor a cada ciclo; o arquivo do dia
  anterior é lido uma última vez após a virada (janela de tolerância) e fechado.
- Parse reaproveita `tailer.parse_rows` + `series.parse_epoch` + `parse_datetime_text`:
  o módulo de parse migra da dashboard para um pacote compartilhado (`carbonara_parse/`)
  importado pelos dois lados, para que preview e ingestão nunca divirjam.

### 2.3 Tratamento (entre `incoming/` e o banco)

- **Ledger incremental**: tabela `ingest_files(file_id, station_id, relpath, size, mtime,
  head_sha256, bytes_ingested, status, error, first_seen, last_ingested)`. Arquivo novo →
  parse completo; cresceu → parse a partir de `bytes_ingested` (TOA5/`.sfo` são
  append-only); encolheu ou trocou cabeçalho → reinicia do zero (rotação). Nenhum `move`.
- **Idempotência**: `ON CONFLICT (time, station_id, variable_id) DO NOTHING`. Reingestão
  total (`ingest --reprocess <station>`) é o mecanismo de reconstrução do banco — é o que
  substitui backup.
- **QC** (biblioteca `processing/` existente): `range_check` alimentado pelo `min`/`max` do
  registro; novos checks `spike`, `persistence` (valor travado), `step`, flags 3+. Sempre
  flag, nunca altera valor.
- **Fuso**: conversão para UTC no parse, usando o `timezone` do arquivo; o formato `epoch`
  já é UTC por definição e ignora o campo.
- **Quarentena**: apenas registro em `ingest_files.status='error'` + trecho ofensivo em
  `quarantine/<id>/<arquivo>.<offset>.txt`; o original nunca é tocado. A dashboard mostra
  o erro na coluna de freshness/ingestão.
- **Serviço**: container `ingestion` (compose) montando BASE **somente leitura**
  (`:ro`), loop de polling como hoje; uma passada completa por ciclo, `settle_seconds`
  mantido. Um processo, sem fila, sem orquestrador.

### 2.4 Modelo de dados no Postgres

Manter `stations`, `variables`, `measurements` (hypertable 7 d, compressão > 30 d,
`measurements_hourly`) com estes ajustes:
- `measurements.source_file TEXT` → `file_id INT REFERENCES ingest_files`; `time` é
  `timestamptz` (já é).
- Novo agregado contínuo `measurements_daily`; índice `(variable_id, time DESC)`.
- `variables` ganha `station_id` opcional? **Não** — `variable_id` é global e o par
  `(station_id, variable_id)` já distingue; o registro na dashboard cria a variável em
  `variables` se não existir (`ensure_variable`, mesmo padrão de `ensure_station`).
- `stations` ganha `timezone` padrão e, na fase 3, `geom` PostGIS.

Novas tabelas:
- `monitored_files(file_id, station_id, path, dynamic_path, parser_config JSONB, timezone,
  resample, ingest BOOL, updated_at)` — espelho do registro da dashboard (a dashboard
  grava no YAML **e** no banco; o YAML continua sendo a fonte editável até a fase 2, quando
  o banco assume).
- `ingest_files` (ledger acima) e `pipeline_runs(run_id, version, started, finished,
  status, stats JSONB)`.
- `api_keys(key_id, key_hash, owner, scopes TEXT[], rate_limit, expires_at, created_by,
  created_at, revoked_at, last_used_at)`.

### 2.5 API (porta 8000)

**Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 async (asyncpg); uvicorn com 2 workers em
container; `?format=json|csv|parquet`. Sem TLS por enquanto (sem domínio); quando o STI
liberar exposição externa, o apache2 já instalado faz o proxy com certificado.

Endpoints v1:
- `GET /stations`, `/stations/{id}` (GeoJSON), `/stations/{id}/status`
- `GET /variables`, `/stations/{id}/variables`
- `GET /series?station=&vars=&start=&end=&agg=raw|1h|1d&qc=good|all&tz=`
- `GET /latest?station=` (último valor por variável, para a dashboard)
- `GET /files?station=` (arquivos monitorados + estado de ingestão, do ledger)
- `GET /healthz`; `POST/DELETE /admin/keys` (escopo `admin`)

**Gestão de chaves e tokens** (obrigatória desde o início):
- Chave gerada uma única vez e exibida uma vez (mesmo padrão do `ftp-hub --gerar`); no
  banco só o hash (SHA-256 com pepper do `.env`).
- Escopos `read` (séries/metadados), `download` (export em massa), `admin` (gerir chaves);
  `expires_at` obrigatório (padrão 1 ano), revogação imediata, `last_used_at` para auditoria.
- Limite de taxa por chave (token bucket em memória, `429` com `Retry-After`).
- Leitura anônima só de `/stations` e `/variables`; tudo o mais exige `X-API-Key`.
- Tela na dashboard (admin) para criar/listar/revogar chaves; chamada ao `/admin/keys` com
  a chave de serviço da própria dashboard.
- Pedido ao STI: **liberar 8000/TCP de entrada** no wrkst02 (e, quando houver domínio,
  443 via apache).

**Migração da dashboard:** `series.build_series` passa a chamar `GET /series` (httpx,
localhost:8000) quando o arquivo tem `ingest: true`; arquivos não ingeridos continuam com
tail. Preview (`POST /api/preview`) continua lendo o arquivo — é justamente a checagem
antes de registrar.

### 2.6 Infra no wrkst02

- **Docker** com `data-root` em `/mnt/data_8tb/docker` (`/etc/docker/daemon.json`);
  volume do Postgres em `/mnt/data_8tb/carbonara-db/pgdata` (mesmo disco do datahub,
  fora de BASE para não ser arrastado por `move-root`). Imagem `timescale/timescaledb-ha:pg16`
  com tag fixada (traz PostGIS para a fase 3).
- **Memória (7,6 GB):** `shared_buffers=1536MB`, `effective_cache_size=3GB`,
  `work_mem=32MB`, `maintenance_work_mem=256MB`, `max_connections=40`,
  `timescaledb.max_background_workers=4`. Limites de container: db 3 GB, ingestion 1 GB,
  api 1 GB. Sobra ≈ 2 GB para page cache e para a dashboard.
- **Replicação pronta**: `wal_level=replica`, `max_wal_senders=3`, `max_replication_slots=3`,
  `wal_keep_size=2GB`, usuário `replicator` + `pg_hba` para o futuro espelho. Não há
  backup: a reconstrução é reingestão a partir de `incoming/`; o espelho cobre a
  indisponibilidade do disco.
- **Sem RAID**: perda de `sdb1` = perda de `incoming/` e do banco. O espelho do banco não
  cobre os brutos — registrar esse risco como aceito pelo usuário.
- **Disco root**: `journald SystemMaxUse=1G`, limpeza de snaps antigos; alarme em 85 %.
- Compose: `db`, `ingestion`, `api`; o serviço `ftp` do compose sai (vsftpd nativo já cumpre).

---

## 3. Roteiro em fases

| Fase | Entrega | Valor |
|---|---|---|
| **0 — Fundação** (1–2 sem.) | Docker + Postgres/Timescale no disco de 8 TB, tuning e replicação pronta; schema com `ingest_files`, `monitored_files`, `api_keys`; API mínima (`/stations`, `/variables`, `/healthz`, `/admin/keys`) na 8000; pedido de porta ao STI. | Banco no ar, chaves geráveis. |
| **1 — Ingestão dirigida pelo registro** (3–5 sem.) | Pacote `carbonara_parse/` extraído do tailer; campos `ingest`/`timezone`/`resample`/`variable_id` no formulário; ingestão incremental por offset lendo `config.yaml`; QC por `min`/`max`; `/series`, `/latest`, `/files`; dashboard lê `/series`. Backfill dos arquivos já registrados. | Dashboard e parceiros consultam o banco. |
| **2 — API pública e operação** (3–4 sem.) | Tela de chaves na dashboard, limite de taxa, export CSV/Parquet; `monitored_files` vira a fonte (YAML só cache); `--reprocess`; espelho do banco (quando o destino existir); TLS via apache quando houver domínio. | Exposição externa segura. |
| **3 — Geo** | PostGIS + `stations.geom`; produtos de satélite/LiDAR registrados como "arquivos monitorados" de família raster/nuvem (COG/COPC em `l2/`), pgSTAC + TiTiler. | Satélite e LiDAR servidos por padrão aberto. |

## 4. Arquivos críticos do repositório

- `ingestion/ingest.py` — remover `archive()`/`quarantine()`; ledger por offset; ler o
  registro da dashboard; `ensure_variable`; fuso e `resample`.
- `dashboard/app/tailer.py`, `series.py` (parse) → extrair para `carbonara_parse/`
  (usado pela dashboard e pela ingestão).
- `dashboard/app/config_store.py`, `templates/file_form.html`, `validate_file_entry` —
  campos `ingest`, `timezone`, `resample`, `variable_id`.
- `database/init/02_schema.sql`, `03_hypertables.sql` — `file_id`, `monitored_files`,
  `ingest_files`, `pipeline_runs`, `api_keys`, `measurements_daily`; `04_replication.sql`.
- `docker-compose.yml` — serviço `api`, imagem fixada, volumes em `/mnt/data_8tb`,
  BASE montada `:ro` na ingestão, remover `ftp`.
- `api/` (novo) — FastAPI, auth por chave, limite de taxa.
- `dashboard/app/series.py` (`build_series`) — chamar `/series` quando `ingest: true`.
- `docs/architecture.md` — registrar este ciclo (pt-BR).

## 5. Verificação fim a fim

- Fase 0: `docker compose ps` saudável; `df -h /` não cresce após o pull; `SHOW wal_level`
  = `replica`; `POST /admin/keys` devolve chave uma vez e `GET /stations` sem chave = 200,
  `GET /series` sem chave = 401, com chave revogada = 401.
- Fase 1: registrar `eddyfarm/CR1000_Proc3K.dat` com `ingest: true`, `timezone: UTC`;
  contagem em `measurements` = linhas × variáveis selecionadas; rodar duas vezes → zero
  duplicatas; anexar linhas ao arquivo → só o delta é lido (`bytes_ingested` avança);
  `incoming/` intacto (mtime/tamanho iguais); série da dashboard igual antes/depois da
  troca para `/series`; arquivo com `timezone: America/Santarem` aparece deslocado
  corretamente em UTC; valor fora de `min`/`max` chega com `quality=2`.
- Fase 2: `--reprocess eddyfarm` reconstrói a mesma contagem após `TRUNCATE`; `429` ao
  exceder a taxa; `pg_basebackup` do espelho conclui sem parar o primário.

## 6. Perguntas em aberto (não bloqueiam a fase 0)

1. Quais arquivos de 20 Hz hoje registrados (`Time_Series` de fluxfarm/km67) devem entrar
   reamostrados (`1min`/`30min`) ou ficar só no monitoramento (`ingest: false`)?
2. Chaves de API: validade padrão de 1 ano serve? Quem além do admin poderá emitir?
3. Quando houver o destino do espelho, será outro Linux com Docker (streaming replication
   direta) ou um serviço gerenciado (exigiria replicação lógica)?
