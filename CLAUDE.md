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

1. **Transport** (rsync-over-SSH) — how a file gets from station to hub. Stations *push*;
   the full-featured station agents live in the `carbonara-stations` repo, but this repo
   ships **simple guided clients** in `station/` (`carbonara-station.sh` for Linux,
   `carbonara-station.ps1` for Windows — see below). On the hub side this layer is the
   single `rsync-hub` script (see below).
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

- **Management** — `rsync-hub install` is a **guided wizard** on a TTY (root dir → service
  account → add stations; persists choices to `/etc/rsync-hub.conf`, env vars still win);
  `install --defaults` (or no TTY) skips the questions. `add <id>` **without** a pubkey
  generates the station's keypair on the hub under `$BASE/stations/<id>/` (private key +
  pubkey stay there, plus a personalized `INSTRUCOES.txt` the operator carries to the
  station) — `stations/` is deliberately outside `incoming/` (stations can write there and
  ingestion sweeps it). `add <id> <pubkey>` authorizes an existing key. Also `remove <id>`,
  `list`, and `cliente` (prints client-setup instructions). Run without args it opens an
  interactive menu (the guided "add" accepts ENTER = generate, a path, or a pasted line).
- **Password mode (second auth path)** — `add <id> --senha [senha]` authorizes a station
  via the **rsync daemon** (port 873) instead of SSH: user = station id, secrets in
  `$BASE/rsyncd.secrets` (600, source of truth), `$BASE/rsyncd.conf` fully regenerated
  from it on every add/remove (one module per station: `auth users = <id>`,
  `write only = true`, `refuse options = delete* remove-source-files`, chroot, uid/gid
  `carbonara`). The daemon is a systemd unit `carbonara-rsyncd` written by
  `ensure_rsyncd()` (fallback: bare `rsync --daemon`); rsyncd re-reads config+secrets on
  every connection, so no reload is needed after add/remove. Port 873 is a **separate
  requirement from SSH**: `add --senha` tries to open it on the host firewall
  (`open_firewall_port`: ufw/firewalld), verifies the daemon is listening
  (`rsyncd_listening`: ss/netstat) and prints a reachability checklist — but an external
  firewall on the path still needs manual liberation; if 873 cannot be opened
  end-to-end, key mode (port 22) is the only option. Tradeoff (documented everywhere
  user-facing): rsyncd auth is challenge-response (password never travels in clear) but
  **data is unencrypted** — key mode stays the recommendation for public networks.
  `remove <id>` revokes both modes.
- **Monitor** — `rsync-hub monitor` (stays running, live table + recent connections),
  `rsync-hub status` (one-shot). Reads `incoming/` mtimes + `log/connections.log`. Each
  station gets a colored dot + state: purple "Recebendo dados" (last SSH log event is a
  CONNECT with no DISCONNECT after it, or activity in the last 60s), green "Online"
  (activity within `RSYNC_HUB_STALE`, default 1h), red "Offline" (beyond it / never).
  ANSI colors only when stdout is a TTY. Deliberately NO pending-files column or
  ingestion-lag legend in this view (user request) — ingestion backlog is diagnosed via
  `docker compose logs -f ingestion`, not the monitor.
- **Forced command** — `rsync-hub serve <id>` is what each station's `authorized_keys` line
  invokes (`command="…/rsync-hub serve flona01",restrict`). It logs CONNECT/DISCONNECT/REJECT
  to `log/connections.log`, then runs `rrsync -wo /srv/carbonara/incoming/<id>`.

**Host read policy (deliberate):** local users of the hub machine may READ everything —
data under `incoming/`/`archive/` and the per-station credentials in
`$BASE/stations/<id>/` (private keys and password INSTRUCOES are 644, dirs 755) — so
admins can copy keys/instructions for operators **without sudo**. Write isolation
applies to *stations* (forced command + rrsync / write-only rsyncd modules), not to
local host users; the host is trusted. Two exceptions that MUST stay 600:
`$BASE/.ssh/authorized_keys` (sshd) and `$BASE/rsyncd.secrets` (rsyncd strict modes
refuses a world-readable secrets file — each station's password is instead readable via
its INSTRUCOES.txt). Clients upload with `-rltz --chmod=D755,F644` (not `-a`) so
arriving files are world-readable regardless of source permissions.

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

### Station clients (`station/`) — run on the stations, not the hub

Guided client scripts, one per OS: `station/carbonara-station.sh` (Linux, bash + cron) and
`station/carbonara-station.ps1` (Windows PowerShell 5.1+; scheduling via Task Scheduler).
On Windows, users launch via `station/carbonara-station.cmd` (same dir), which runs the
`.ps1` with `-ExecutionPolicy Bypass` — default Windows policy blocks `.ps1` files
outright, so keep the `.cmd` shipped alongside it.

Windows transport is **cwRsync only** (WSL support was deliberately removed — wsl.exe
exists on machines without a distro and hangs waiting for hidden input). On first setup
the client **auto-installs cwRsync**: downloads the official zip (URL in `$CwRsyncUrl`)
and extracts to `C:\cwrsync` (`Find-Rsync` looks there first, then PATH). Creating
`C:\cwrsync` needs elevation, so the wizard instructs the user to reopen PowerShell as
Administrator for that first run only. cwRsync recipe honored in `Get-RsyncPlan`:
prepend `bin\` to PATH (cygwin DLLs + bundled ssh), set `HOME` (known_hosts), and pass
all paths as `/cygdrive/...` (no cygpath.exe in the free zip — manual conversion).
`Invoke-NativeQuiet` (stdin closed + timeout/kill) is the hang-proof runner for probes —
and also for `schtasks /Create`: its hand-built argument line escapes the inner quotes of
`/TR` as `\"`, required when the script path has spaces (PowerShell's `&` operator breaks
there). Default Windows data folder offered by setup: `C:\Campbellsci\LoggerNet` when it
exists (Campbell Scientific LoggerNet output, the project's standard datalogger software).

Setup wizard order (keep both clients in sync): station id → hub IP → auth mode
(`AUTH_MODE`/`AuthMode`: `chave` = rsync-over-ssh, port 22, local path of the key
received from the hub, falling back to generating one locally and printing the pubkey;
`senha` = rsync daemon, port 873, password from the hub's INSTRUCOES.txt, user = station
id, sent via the `RSYNC_PASSWORD` env var — never on the command line — against
`rsync://<id>@host:port/<id>/`) → connection probe (dry-run from an empty temp dir) →
data folder → recycle interval (`SEND_EVERY`/`SendEveryMin`), offering to install the
schedule immediately.

**Encoding invariant (Windows files):** `carbonara-station.ps1` MUST be saved as **UTF-8
with BOM** — Windows PowerShell 5.1 reads BOM-less files as ANSI, and the pt-BR em dash
(`—`, UTF-8 `E2 80 94`) mojibakes into a CP-1252 smart quote that terminates strings and
breaks the whole parse. A full-file rewrite drops the BOM; restore it with
`printf '\xEF\xBB\xBF' | cat - file > tmp && mv tmp file` and check via
`head -c 3 file | xxd` (`efbbbf`). `carbonara-station.cmd` must stay pure ASCII.

Both clients expose the same subcommands: `setup` (guided wizard), `test` (rsync
dry-run), `send`, `schedule`/`unschedule`, `status`, `reset`, and an interactive menu
when run without args. Send semantics (keep in sync across both — this is a hard product
requirement): **the local data folder is NEVER modified** — no moves, no deletes; the
client is a one-way synchronizer. What was already sent is tracked in a local state file
(`sent.state`, one `relpath<TAB>size:mtime` line per file, next to the config); each
cycle sends only new-or-changed files (growing datalogger `.dat`s re-send whole — fine,
hub ingestion is idempotent), and on rsync rc=0 the state is updated and pruned to files
that still exist locally. `reset` wipes the state to force a full re-send. Only files
older than the configured min age are eligible; dotfiles/dot-dirs are skipped (matches
the hub's ingestion rule); on failure the state is untouched. Config lives per user
(`~/.config/carbonara-station/config` / `%LOCALAPPDATA%\carbonara-station\config.json`).

Windows scheduling: `Install-Schedule` embeds `-ConfigDir "<user's conf dir>"` in the
task action (the `.ps1` accepts `-ConfigDir` to override its config paths) and, when the
shell is elevated, creates the task with `/RU SYSTEM` — it then runs with no user logged
on, survives reboots, and shows no console flashes; SYSTEM has a different
%LOCALAPPDATA%, which is why -ConfigDir is mandatory in the action. Non-elevated
schedule = task runs only while the user is logged on. The send mutex is
`Global\CarbonaraStationSend` (cross-session, SYSTEM task vs user window) with a
local-namespace fallback.

Concurrent-append safety (why reading live datalogger files is fine): clients only READ
sources — a reader never blocks the datalogger's writes; if the writer holds an
exclusive lock, it is the *send* that fails (rc 23 → distinct warning, state untouched,
retried next cycle), never the collection. A file appended mid-transfer yields a torn
snapshot that self-heals: the state stores the *pre-transfer* signature, so the changed
file re-sends whole next cycle and idempotent ingestion absorbs it. Server side can
never see half-uploads (rsync hidden temp + atomic rename, ingestion skips dotfiles +
settle_seconds). Don't "fix" this with source-file locking — it would invert the
guarantee and risk the collection.

## Ingestion service (`ingestion/`)

Single polling loop in `ingest.py` — no watchers, no queue. `main()` loops every
`INGEST_POLL_SECONDS`, calls `discover_files()` (skips hidden entries at ANY path level —
covers dotfiles and dot-dirs like `.rsync-parcial/`, where clients park interrupted
uploads via `--partial-dir` — and files modified within `settle_seconds`, to avoid
picking up partial uploads), then `process_file()` per file.

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
