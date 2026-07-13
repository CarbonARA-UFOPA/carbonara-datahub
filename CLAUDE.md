# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`carbonara-datahub` is the **central server** side of the CARBONARA project (weather-station
data for forest monitoring in the Amazon). Stations push files to the hub via **rsync-over-SSH**;
the hub parses them and loads them into a time-series database. Docs and comments are written in
**Brazilian Portuguese** — match that language when editing docs, comments, and log messages.

> Collection was **migrated from FTP to rsync-over-SSH**. FTP now only *distributes* processed
> data (read-only); it no longer receives. If you find older docs/comments describing FTP upload
> or `/srv/carbonara/collect/`, they are stale — the current landing dir is
> `/srv/carbonara/incoming/`.

## Running the stack

```bash
cp .env.example .env                              # DB_PASSWORD, FTP_USER_PASS, FTP_PUBLIC_HOST
cp ingestion/config.example.yaml ingestion/config.yaml
docker compose up -d
docker compose ps                                 # all should be healthy/running
docker compose logs -f ingestion
```

Compose services: `db`, `ingestion`, and `ftp` (now read-only distribution). Collection is
**not** a Compose service — it's the `rsync-hub` script running natively on the host (see the
collection layer below); prep the host with `sudo ./rsync-hub install`.

Notes:
- `.env.example` is referenced but **not committed** — compose requires `FTP_USER_PASS`,
  `DB_USER`, `DB_PASSWORD` (fails fast with `:?` if unset).
- `CARBONARA_ROOT` sets the data-volume root: defaults to `./data` for local dev, set it to
  `/srv/carbonara` in production (where the native sshd writes `incoming/`). Subdirs are
  `incoming/`, `archive/`, `quarantine/`, `log/`.
- Rebuild a service after code changes: `docker compose up -d --build ingestion` (or `monitor`).
- DB init SQL (`database/init/0{1,2,3}_*.sql`) runs **only on first start** with an empty
  `db_data` volume. To re-run schema changes during dev, `docker compose down -v` wipes the
  volume (destroys all data) then `up -d`.
- Inspect the DB: `docker exec -it carbonara-db psql -U carbonara -d carbonara`
- End-to-end test (no real station): drop a sample CSV into the landing dir and watch it flow:
  ```bash
  mkdir -p data/incoming/flona01
  cp examples/sample-station-data/flona01_20260627.csv data/incoming/flona01/
  docker compose logs -f ingestion    # "medições lidas / inseridas"
  ```

There is **no test suite, linter, or CI** configured. The ingestion service has no unit tests;
validation is the end-to-end path above.

## Architecture: three decoupled layers

The core design principle is that transport, ingestion, and storage are independent and
swappable, connected only through a **shared landing directory** (`/srv/carbonara/incoming/`).
Full rationale in `docs/architecture.md`.

1. **Transport** (rsync-over-SSH) — how a file gets from station to hub. Stations *push*; the
   station agents live in the `carbonara-stations` repo, not here. On the hub side this layer
   is the single `rsync-hub` script (see below).
2. **Ingestion/parsing** (`ingestion/`) — turns files into DB rows. It is **deliberately blind
   to transport**: it only watches the landing directory and does not know how a file arrived.
   Untouched by the FTP→rsync migration.
3. **Storage** (TimescaleDB) — persists the time series.

Data flow: `station → rsync/ssh → /srv/carbonara/incoming/<station_id>/ → ingestion (parse) →
TimescaleDB`, with processed files moved to `archive/<station_id>/<date>/` and failures to
`quarantine/<station_id>/`. FTP now serves `archive/` read-only for downstream consumers.

### Collection layer: one script — `rsync-hub` (native on host)

All collection is **one bash script** at repo root, `rsync-hub`, running natively on the host
(not in Compose). It has three roles, dispatched by its first arg:

- **Management** — `rsync-hub install` (one-time host prep: service account + dirs + keyfile),
  `add <id> <pubkey>`, `remove <id>`, `list`. Run without args it opens an interactive menu.
- **Monitor** — `rsync-hub monitor` (stays running, live table + recent connections),
  `rsync-hub status` (one-shot). Reads `incoming/` mtimes + `log/connections.log`.
- **Forced command** — `rsync-hub serve <id>` is what each station's `authorized_keys` line
  invokes (`command="…/rsync-hub serve flona01",restrict`). It logs CONNECT/DISCONNECT/REJECT
  to `log/connections.log`, then runs `rrsync -wo /srv/carbonara/incoming/<id>`.

**Model & invariants** (don't weaken): a **single service account** (`carbonara`, locked
password, keys in `$BASE/.ssh/authorized_keys`) receives all pushes — there is *no* per-station
OS user. Station isolation is enforced by (a) each key being pinned to its own `serve <id>` via
the forced command, (b) `restrict` (no shell/PTY/forwarding), and (c) `rrsync -wo` confining the
transfer to that station's dir, write-only. `authorized_keys` lives outside `incoming/` so a
station can't overwrite it. The DISCONNECT log line relies on **not** using `exec` for rrsync —
keep rrsync as a child so the line runs after it exits.

> This deliberately replaced an earlier 3-component design (`collector/` + `hub/hubctl` +
> Python `monitor` container). If you find references to those paths, `hubctl`, `st_<id>`
> per-station users, or `connections.jsonl`/`status.json`, they are stale — it's all `rsync-hub`
> and `connections.log` now.

## Ingestion service (`ingestion/`)

Single polling loop in `ingest.py` — no watchers, no queue. `main()` loops every
`INGEST_POLL_SECONDS`, calls `discover_files()` (skips dotfiles and files modified within
`settle_seconds`, to avoid picking up partial uploads), then `process_file()` per file.

Key invariants when editing:
- The **first path component** under `landing/` is the `station_id` (`data/landing/flona01/…`
  → `flona01`). Unknown stations are auto-registered with minimal metadata via `ensure_station`.
- **Idempotent by design**: inserts use `ON CONFLICT (time, station_id, variable_id) DO NOTHING`,
  so reprocessing the same file inserts no duplicates. Preserve this natural key.
- **Per-file error isolation**: any exception in `process_file` rolls back the transaction,
  logs, and moves the file to `quarantine/` — it must never crash the loop.
- One long-lived DB connection is shared across the loop; `conn.rollback()` on failure is what
  keeps it usable.

### Adding a new file format = new parser

Parsers are a plugin registry (`ingestion/parsers/base.py`). To add one:
1. Subclass `Parser`, implement `parse(path, station_id) -> Iterable[Measurement]`, decorate
   with `@register("your_name")`.
2. Import it in `ingestion/parsers/__init__.py` (the import triggers `@register` as a side
   effect — a parser not imported there is invisible to the registry).
3. Reference it in `ingestion/config.yaml` under `default_parser` or per-station `stations:`.

`ingest.py` itself does not change. A parser must **raise on invalid input** — that is what
routes the file to quarantine. The included `csv_generic` parser is config-driven: `columns:`
maps CSV column → `variable_id`, non-numeric values become `NULL` with `quality=1`.

## Data model (TimescaleDB)

Long/EAV model so new measured quantities need **no `ALTER TABLE`** — just an `INSERT` into
`variables`. Three tables (`database/init/02_schema.sql`):

- `stations` — station metadata (location, forest type, fixed vs. mobile/roving).
- `variables` — catalog of quantities (`air_temp`, `co2`, …); seeded with common weather vars.
- `measurements` — the fact table, a **hypertable** partitioned by `time` (7-day chunks,
  `03_hypertables.sql`). Natural key `(time, station_id, variable_id)`. Auto-compression after
  30 days; hourly continuous aggregate `measurements_hourly` for dashboards/analysis.

Ingestion writes `measurements` columns `(time, station_id, variable_id, value, quality,
source_file)`; `quality=0` means OK, `>0` reserved for QA/QC flags.
