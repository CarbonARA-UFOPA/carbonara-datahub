# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`carbonara-datahub` is the **central server** side of the CARBONARA project (weather-station
data for forest monitoring in the Amazon). Stations push files to the hub via **rsync-over-SSH**;
the hub parses them and loads them into a time-series database. Docs and comments are written in
**Brazilian Portuguese** — match that language when editing docs, comments, and log messages.
Exception: the top-level `README.md` is in **English** (project-facing) — keep it that way.

> Collection was **migrated from FTP to rsync-over-SSH**. FTP now only *distributes* processed
> data (read-only); it no longer receives. If you find older docs/comments describing FTP upload
> or `/srv/carbonara/collect/`, they are stale — the current landing dir is
> `/srv/carbonara/incoming/`.

## Running the stack

```bash
cp .env.example .env                              # DB_PASSWORD, FTP_USER_PASS, FTP_PUBLIC_HOST
cp ingestion/config.example.yaml ingestion/config.yaml
cp processing/config.example.yaml processing/config.yaml
docker compose up -d
docker compose ps                                 # all should be healthy/running
docker compose logs -f ingestion
```

Compose services: `db` (TimescaleDB-HA image, pinned tag, PostGIS included, bind-mounted
`PG_DATA_DIR` on the data disk, tuned for the 7.6 GB host, `wal_level=replica` for a future
mirror) and `ingestion` (with the QA/QC library from `processing/` built in) — the latter
sits behind the `ingestao` **profile** and does NOT start with a plain `up -d`: the current
`ingest.py` MOVES files out of `incoming/`, which breaks the collection (append-only `.dat`s,
dashboard tailing) — it is rewritten in phase 1 of `docs/estrategia-dados.md` (incremental,
driven by the dashboard's monitored files/variables, never moving anything). The old
`ftp` compose service is gone (native vsftpd). Collection is
**not** a Compose service — it's the `rsync-hub` script running natively on the host (see the
collection layer below); prep the host with `sudo ./collection/rsync-hub install`.
**Host prep for the database** is `sudo ./database/setup-db-host.sh` (phase 0): installs
Docker with `data-root` on the data disk (`/mnt/data_8tb` — the root disk is ~80 % full,
nothing may land there), creates `PG_DATA_DIR`, generates `.env` (never overwrites), limits
journald; `--subir` also brings `db` up and checks health/wal_level. Production facts
(26/09/2026): 4 vCPU, 7.6 GB RAM, BASE `/mnt/data_8tb/carbonara-data-hub`, no RAID, no
backup by design (the DB only holds data that passed through the flow and is rebuilt by
idempotent re-ingestion), API planned on **8000/TCP** with API keys, per-file timezone with
the DB always in UTC.

Notes:
- `.env.example` is referenced but **not committed** — compose requires `FTP_USER_PASS`,
  `DB_USER`, `DB_PASSWORD` (fails fast with `:?` if unset).
- `CARBONARA_ROOT` sets the data-volume root: defaults to `./data` for local dev, set it to
  `/srv/carbonara` in production (where the native sshd writes `incoming/`). Subdirs are
  `incoming/`, `archive/`, `quarantine/`, `log/`.
- Rebuild after code changes: `docker compose up -d --build ingestion` (also required after
  editing `processing/` — the QC library is baked into the ingestion image, whose build
  context is the **repo root**; keep `.dockerignore` excluding `data/`).
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

## Architecture: four decoupled layers

The core design principle is that transport, ingestion, and storage are independent and
swappable, connected only through a **shared landing directory** (`/srv/carbonara/incoming/`).
Full rationale in `docs/architecture.md`.

1. **Transport** (rsync-over-SSH) — how a file gets from station to hub. Stations *push*;
   the full-featured station agents live in the `carbonara-stations` repo, but this repo
   ships **simple guided clients** in `collection/station/` (`carbonara-station.sh` for Linux,
   `carbonara-station.ps1` for Windows — see below). On the hub side this layer is the
   single `rsync-hub` script (see below).
2. **Ingestion/parsing** (`ingestion/`) — turns files into DB rows. It is **deliberately blind
   to transport**: it only watches the landing directory and does not know how a file arrived.
   Untouched by the FTP→rsync migration.
3. **Processing/QA-QC** (`processing/`) — a **library** the ingestion service runs
   between parse and insert: pluggable checks compute each measurement's `quality` flag
   in memory, so rows land in the DB already qualified. Never modifies or drops values.
4. **Storage** (TimescaleDB) — persists the time series.

Data flow: `station → rsync/ssh → /srv/carbonara/incoming/<station_id>/ → ingestion (parse) →
TimescaleDB`, with processed files moved to `archive/<station_id>/<date>/` and failures to
`quarantine/<station_id>/`. FTP now serves `archive/` read-only for downstream consumers.

### Collection layer: one script — `rsync-hub` (native on host)

All collection is **one bash script**, `collection/rsync-hub`, running natively on the host
(not in Compose). It has three roles, dispatched by its first arg:

- **Stable path invariant** — the `command=` line in authorized_keys embeds an absolute
  path to this script that the *service account* must be able to execute forever. A path
  inside `/home/<admin>/` breaks with `sh: ... rsync-hub: Permission denied` at the
  station (homes are 750). `add_station` therefore self-installs the running copy to
  `/usr/local/bin/rsync-hub` and always points keys there — keep that behavior.
- **Version drift** — `RSYNC_HUB_VERSION` (top of script): bump it on every behavior
  change; it shows in `rsync-hub help` and the monitor title, which is how a stale
  `/usr/local/bin` copy is spotted. The dispatcher also warns (except in `serve`) when
  the running copy differs from the installed one (`cmp`). Admins run the installed
  copy day-to-day, so an outdated `/usr/local/bin` silently reintroduces old behavior
  (e.g., 700/600 perms in `stations/`) — that's the failure mode this guards against.
- **Management** — `rsync-hub install` is a **guided wizard** on a TTY (root dir → service
  account → add stations; persists choices to `/etc/rsync-hub.conf`, env vars still win);
  `install --defaults` (or no TTY) skips the questions. `add <id>` **without** a pubkey
  generates the station's keypair on the hub under `$BASE/stations/<id>/` (private key +
  pubkey stay there, plus a personalized `INSTRUCOES.txt` the operator carries to the
  station) — `stations/` is deliberately outside `incoming/` (stations can write there and
  ingestion sweeps it). `add <id> <pubkey>` authorizes an existing key. Also `remove <id>`
  (revoke only, data preserved), `purge <id> [--confirmo]` (revoke AND `rm -rf` the
  station's incoming/archive/quarantine/stations dirs — nothing else; a TTY asks to
  TYPE the station id, non-TTY requires `--confirmo` — the dashboard's calling form),
  `list`, and `cliente` (prints client-setup instructions). Run without args it opens an
  interactive menu (the guided "add" accepts ENTER = generate, a path, or a pasted line).
  `move-root <new> [--confirmo]` migrates the ENTIRE base to another dir — never a
  subset: INCOMING derives from BASE, the service account's **home IS the base** (sshd
  finds authorized_keys through it → `usermod -d`), and rsyncd.conf/systemd unit embed
  absolute paths. Flow: preflight (absolute, not nested either way, empty target,
  service account can traverse) → `rsync -aH` copy → switch home+conf → delta pass →
  regenerate/restart rsyncd → `RAIZ-MIGRADA.txt` marker in the old root, which is
  **never deleted** (manual, after verifying). The preflight REFUSES a destination on
  a filesystem that cannot hold Unix ownership/modes (`vfat/exfat/ntfs/fuseblk`…): the
  hub's whole model rests on them (sshd StrictModes over `authorized_keys`, the 644/755
  read policy, rrsync), so an external backup drive as the root would take every
  station down with `Permission denied (publickey)`. It also WARNS for `/media`,
  `/run/media` and `/mnt` (a removable root is the service account's HOME — unmounted,
  sshd finds no keys), names the exact directory in the path missing `o+x` instead of
  "some directory", and points a freshly formatted mount (`lost+found` only) at a
  subdirectory. It prints the manual follow-ups
  (compose `.env` `CARBONARA_ROOT` + `up -d`, dashboard data_root); `--confirmo` is
  required without a TTY (that's how the dashboard invokes it).
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
- **Pause (suspended reception)** — `pause <id|--todas> [motivo]` / `resume
  <id|--todas>` suspend the ARRIVAL of data without revoking credentials or deleting
  anything (maintenance: disk, root migration, DB down). State = marker files in
  `$BASE/pause/` (`_todas` = global, `<id>` = per station), 644 inside a 755 dir so the
  dashboard READS the state with no sudo; only writing goes through root. Enforcement
  covers BOTH auth paths: key mode → `serve` refuses *before* the CONNECT log line
  (logs `PAUSED`, exit 129, prints the reason to the station's rsync output); password
  mode → `rebuild_rsyncd_conf` omits the paused station's module (rsyncd re-reads the
  config per connection, so it applies immediately and the secret stays untouched).
  Clients see a plain failure, don't advance their local state and re-send everything
  next cycle — a pause **delays** data, never loses it. Global and per-station pauses
  are INDEPENDENT: `resume --todas` deliberately does not clear individual ones (it
  reports what is still paused). In `print_status`/monitor the yellow "Pausada"
  OVERRIDES online/offline — a paused station can't even send its heartbeat, so
  "Offline" would be a misleading diagnosis; `dashboard/app/status.py` mirrors this.
- **Never truncate a control file** — `authorized_keys`, `rsyncd.secrets` and
  `rsyncd.conf` are replaced through `write_replace` (write a temp, CHECK the write
  succeeded, only then `mv`). The old `grep -v … > f.tmp || true; mv f.tmp f` pattern
  **wiped `authorized_keys` when the disk was full** (a rename needs no space, so the
  truncated temp replaced the good file and every station got
  `Permission denied (publickey)`). A failed write must keep the previous file and say
  so — never fall through silently.
- **Diagnostic/repair** — `rsync-hub diagnostico [--reparar]` answers "why did the
  stations stop sending?": disk usage of BASE, service-account home vs BASE (a
  half-finished `move-root` leaves them apart and sshd then reads the wrong
  `authorized_keys`), authorized_keys line count/perms, presence of
  `/usr/local/bin/rsync-hub` (the forced-command path), active pauses, and per-station
  key vs stored `stations/<id>/*.pub`. `--reparar` re-authorizes stations whose public
  key is still stored on the hub — the station keeps its own key, nothing is reissued.
- **Per-station data volume in the dashboard** (column *Consumo*): the headline value is
  the LAST transmission (`status._last_events` → `last_bytes`: the most recent DISCONNECT
  with `bytes>0`, no 64 kB floor — that floor exists for the speed, where a tiny session
  would measure the ping; here the size IS the datum, and skipping `bytes=0` keeps the
  heartbeat from zeroing the column every cycle), with the 30-day total underneath — the
  same two-line shape as the *Velocidade* column (last session on top, aggregate below).
  The *Ping* column beside it follows the same shape (`last_rtt_ms` / `avg_rtt_ms`, from
  the `rtt=` field — see the reverse-ping bullet in the collection layer; no 64 kB floor).
  The 30-day figure comes from `status.volumes`: ONE scan serves every station and the
  result is cached for 60 s — summing the period per station on every refresh would be
  pure waste. The scan itself is
  bounded by the PERIOD, not by the log size: `tailer.tail_lines_since` walks the file
  backwards and stops at the first line older than the cutoff (the ISO-UTC stamp orders
  as text), which matters because `connections.log` is never rotated.
- **Monitor/status layout** (deliberate, after the table became unreadable): seven
  columns — `ESTAÇÃO ESTADO ÚLTIMO SINAL CICLO VELOCIDADE PING 30 DIAS` — and
  deliberately **no legend under the table** (it was tried and removed at the user's
  request; the meaning of the columns lives in `docs/installation.md`, the averages in
  `rsync-hub volume` — don't re-add it). Speed and ping show the **last** measured session
  only (that is what answers "how is the link right now"); the AVERAGES moved to
  `rsync-hub volume`, which gained `VELOC. MÉD` / `PING MÉD` columns over the report's
  own period — don't put `últ · méd` pairs back in the table. `ÚLTIMO SINAL` is
  `HH:MM:SS (há quanto tempo)`, with `dd/mm HH:MM` when it is not today. The network
  block is 2 lines, not 5.
- **The monitor reads each source ONCE per cycle, never once per station** — this is a
  hard performance invariant, not a style choice. The previous shape (two `grep`s over
  the whole never-rotated `connections.log` + a recursive `find` + several `date`/`awk`
  forks, PER STATION) cost ~2.8 s per refresh with 9 stations and an 8 MB log, and the
  table visibly drew itself row by row. Now: `load_log` (one `tail -n
  $RSYNC_HUB_LOG_TAIL` + one awk → last event, last CONNECT, last speed, last ping AND
  the 1 h/24 h connection counts that `print_network` used to compute with two more
  full-log scans), `scan_heartbeats` (one `find -printf` + one awk over the
  `.carbonara-status` files), `load_volumes` (60 s cache), and `scan_data` — the deep
  walk of `incoming/`, which is the expensive one and therefore runs **only when some
  station has no heartbeat** (with a heartbeat, it is a newer-or-equal signal, so the
  walk would change nothing on screen) and is cached for `RSYNC_HUB_SCAN_TTL` (30 s).
  Each pass emits `eval`-able assignments (`LG_<id>_*`, `HB_<id>_*`, `DT_<id>`,
  `VL_<id>`) looked up per station by indirect expansion — zero processes in the loop.
  Ids are sanitized (`sane_id`) on both sides so a stray directory name cannot become
  code in the `eval`. Log timestamps are converted to epoch **inside awk**
  (`dias()`/`epoch()`, the days-from-civil algorithm) because `mktime` is a gawk
  extension and a `date -d` fork per station was part of the cost; verified against
  Python on 208 stamps from 1990–2115. `print_status` must keep running BEFORE
  `print_network` (it is `load_log` that counts the connections).
- **The table is built in a buffer and printed once** (`col_pad` → `COL_OUT`, appended
  to `tb`): printing row by row is what made the refresh look like it "came up from the
  bottom" on a slow cycle.
- **Column alignment must go through `col`/`colr`** (never a bare `printf %-16s` with
  accented text): the shell's printf pads by BYTES under locale C and by CHARACTERS
  under UTF-8, so `ESTAÇÃO` (7 chars, 9 bytes) lined up on one machine and skewed the
  whole table on another. `col_width` measures bytes minus UTF-8 continuation bytes
  (`10xxxxxx`), which is the column count in both cases, using only builtins (no fork
  per cell). Values printed in the table stay ASCII for the same reason.
- **`fmt_log_pretty`** renders the monitor's recent connections in Portuguese
  (`recebeu 12.4 MB · 6.8 s · ping 38 ms`) instead of raw log fields; `fmt_log_local`
  keeps the raw line for `diagnostico`, where the raw form is the point.
- **Monitor columns** `30 DIAS` come from `load_volumes`, refreshed at most
  once a minute (`VOL30_TTL`) while the speed/ping columns refresh every cycle — the volume
  scan covers 30 days of log, the speed only its tail. With `tac` the scan reads
  backwards and EXITS at the cutoff, so its cost follows the period, not the (never
  rotated) log size; without `tac` it falls back to `tail -n 200000`.
- **Per-station data volume**: `rsync-hub volume [dias]` sums the same `bytes=` field
  per station over today / 7 days / N days (default 30), plus a TOTAL row. Cutoffs are
  computed in LOCAL time and converted to UTC (`date -d … -u`) because the log is UTC
  but "today" for the reader is the local day; comparison against the ISO stamp is
  textual, which already orders correctly. Sessions logged before this feature have no
  `bytes=` and are skipped rather than counted as zero.
- **Per-station speed**: `serve` measures the delivered payload (`dir_bytes` on
  `incoming/<id>` before/after) and the session duration in ms, and appends
  `bytes=<n> ms=<n>` to the DISCONNECT line — extra fields at the END, so every existing
  parser (shell `awk '{print $2}'`, `status._last_events`) keeps working. Readers
  (`load_speeds`/`station_speed` in the monitor table, `status.py` for the dashboard
  column) ignore sessions under 64 kB, where latency dominates and the number would
  describe the ping, not the link. It is the USEFUL rate (payload ÷ session), not a
  bandwidth test — and it only exists for sessions logged by this version onward.
- **Per-station ping — measured in REVERSE** (`tcp_rtt_ms` in `serve` → field `rtt=`,
  after `ms=`): the hub never sends a packet to the station (it is behind NAT, with no
  fixed address). The station is the one who connects, and the kernel already keeps the
  RTT of that TCP connection — `SSH_CLIENT` ("<ip> <porta> <porta>") pins the exact
  socket for `ss -tin "dst <ip>:<porta>"`, preferring `minrtt` (the observed floor,
  immune to queueing) over the smoothed `rtt`. Sampled BEFORE `t0` so the `ss` fork does
  not inflate the session duration (which would corrupt the speed). The formatter runs
  under `LC_ALL=C`: in a pt_BR locale awk would write `rtt=36,9` and the comma breaks
  every reader (the monitor's awk reads 36, Python's `float()` raises). No `ss`, no
  inet_diag, no field → the value is simply absent and readers show `-`; never a
  fabricated zero. Unlike the speed, the ping has NO 64 kB floor — latency does not
  depend on how much was sent, and dropping small sessions would discard exactly the
  heartbeat-only cycles, which are the majority. It measures the RTT to the station's
  PUBLIC endpoint (with NAT, the last LAN hop is missing — irrelevant; on 4G/satellite
  the measured leg is the one that dominates). Key mode only: password-mode sessions
  never reach `serve` (rsyncd has its own log).
- **Network block** (`print_network`, shown by `status` and by every `monitor` cycle):
  default-route interface (`/proc/net/route`) + live ↓/↑ rate from `/proc/net/dev`,
  totals since boot, whether 22 (and 873, only when password mode is in use) are
  listening, WHICH stations have a session open right now (`ps` → `rsync-hub serve
  <id>`) and CONNECT counts for the last hour/24 h (ISO-UTC stamps compare as text).
  The rate needs two samples: `monitor` uses its own refresh interval as the window
  (free), one-shot `status` calls `net_prime_sample` (a 1 s sleep). `print_network`
  updates `PREV_*` in the CALLER's shell — never call it through a pipe, or the window
  never closes. No `/proc` → prints the IP and says the rate is unavailable.
- **The monitor is the DEFAULT screen** (user request): `rsync-hub` with no args opens
  it, and **Ctrl+O** (`$'\017'`) opens the menu, from which `0` returns to the monitor
  (`MENU_FROM_MONITOR` switches the label and makes `0`/option 10 `return` instead of
  `exit`, so laços never stack). `q` exits, any other key forces an immediate refresh.
  Two things are load-bearing here: (a) **^O is the terminal's `discard` character**
  (`stty -a` → `discard = ^O`), so the tty driver would eat it AND blank the output —
  `mon_term_setup` undefines it and restores the saved `stty -g` on INT and on EXIT
  (`m` stays as an alias for terminals that still swallow it); (b) with **no interactive
  TTY** (pipe, cron) the no-arg form must NOT loop — it falls back to a one-shot
  `status`. `rsync-hub menu` still opens the menu directly.
- **Monitor** — `rsync-hub monitor` (stays running, live table + recent connections),
  `rsync-hub status` (one-shot). Reads `incoming/` mtimes + `log/connections.log`. Each
  station gets a colored dot + state: purple "Recebendo dados" (last SSH log event is a
  CONNECT with no DISCONNECT after it, or activity in the last 60s), green "Online",
  red "Offline", yellow "Pausada" (overrides the rest — see the pause bullet above).
  ANSI colors only when stdout is a TTY. Deliberately NO pending-files
  column or ingestion-lag legend in this view (user request) — ingestion backlog is
  diagnosed via `docker compose logs -f ingestion`, not the monitor.

  **Heartbeat protocol (per-station offline detection):** every client send cycle —
  even with nothing to sync — uploads the hidden dotfile `.carbonara-status` into
  `incoming/<id>/` declaring `interval_min` (the station's recycle interval). It is sent
  WITHOUT `-t`, so its server-side mtime = arrival time (immune to station clock/tz);
  ingestion never sees it (hidden). The monitor computes a per-station window of
  `2 × interval_min + 2 min` — beyond that, Offline. Stations without a heartbeat
  (older clients) fall back to the global `RSYNC_HUB_STALE` (default 1h). Keep the
  heartbeat FIRST in the clients' send flow (liveness even when there is no data) and
  non-fatal on failure.
- **Forced command** — `rsync-hub serve <id>` is what each station's `authorized_keys` line
  invokes (`command="…/rsync-hub serve flona01",restrict`). It logs CONNECT/DISCONNECT/REJECT
  to `log/connections.log`, then runs `rrsync -wo /srv/carbonara/incoming/<id>`.

**Host read policy (deliberate):** local users of the hub machine may READ everything
without sudo — data under `incoming/`/`archive/`, per-station credentials in
`$BASE/stations/<id>/` (private keys and INSTRUCOES are 644, dirs 755), the station
list (`$BASE/.ssh` is 755 and `authorized_keys` 644 — sshd StrictModes only forbids
group/other WRITE), and `rsyncd.secrets` (644, which requires `strict modes = false` in
the generated rsyncd.conf — without it the daemon refuses a world-readable secrets
file). Nothing is writable by local users (owner root/carbonara everywhere); write
isolation applies to *stations* (forced command + rrsync / write-only rsyncd modules).
The host is trusted — do not "harden" these perms back, it breaks sudo-less `list` and
key copying. Clients upload with `-rltz --chmod=D755,F644` (not `-a`) so arriving files
are world-readable regardless of source permissions.

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

### Station clients (`collection/station/`) — run on the stations, not the hub

Guided client scripts, one per OS: `collection/station/carbonara-station.sh` (Linux, bash + cron) and
`collection/station/carbonara-station.ps1` (Windows PowerShell 5.1+; scheduling via Task Scheduler).
On Windows, users launch via `collection/station/carbonara-station.cmd` (same dir), which runs the
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
dry-run), `send`, `folders` (manage sync folders), `schedule`/`unschedule`, `status`,
`reset`, `purge` (wipe this station's config + folders + state + schedule, for a clean
restart — never touches the data folders or the SSH key, which live outside the config
dir), and an interactive menu when run without args (whose header shows the station and
the configured sync folders). Send semantics (keep in sync across both — this is a
hard product requirement): **the local data folders are NEVER modified** — no moves, no
deletes; the client is a one-way synchronizer.

**Multiple folders + filters** (keep in sync across both): a station syncs a **list** of
folders, not one. Each folder has a unique **label** (sanitized from its basename) and is
sent to its own sub-dir on the server — `incoming/<id>/<label>/…` — so same-named files in
different folders never collide. Per folder, two optional **filters** narrow what is sent:
file-type globs (matched against the filename, e.g. `*.tmp,*.bak`) and sub-folder names to
skip (e.g. `temp,cache`); filters apply only to *selection* (the scan), the folders stay
untouched. On Linux the folder list lives in a separate `folders` file (TSV:
`PATH<TAB>LABEL<TAB>FILE_EXCLUDES<TAB>DIR_EXCLUDES`); on Windows it is the `Folders[]`
array in `config.json` (needs `ConvertTo-Json -Depth`). **Every send re-scans all folders
from disk** — this is what makes files created after the first sync get picked up (there is
no cached listing). Legacy single-folder configs (`DATA_DIR` / `DataDir`) are migrated to a
one-entry list on first run.

What was already sent is tracked in a local state file (`sent.state`, one
`<label>/relpath<TAB>size:mtime` line per file, next to the config); each cycle sends only
new-or-changed files (growing datalogger `.dat`s re-send whole — fine, hub ingestion is
idempotent). Sends are **per folder** (one rsync + one `--files-from` each, to that
folder's label sub-dir); state is updated only for the folders whose rsync returned rc=0
(partial success across folders is fine) and pruned to files that still exist locally.
`reset` wipes the state to force a full re-send. Only files older than the configured min
age are eligible; dotfiles/dot-dirs are skipped (matches the hub's ingestion rule); on
failure the affected folder's state is untouched. Config lives per user
(`~/.config/carbonara-station/` / `%LOCALAPPDATA%\carbonara-station\`).

**Stable-copy scheduling + watchdog (keep in sync across both clients):** cron/Task
Scheduler freeze the script path at schedule time, so `schedule` first **self-installs
the running copy to a stable path** and schedules THAT copy — Linux
`~/.local/bin/carbonara-station.sh` (`install_self`, override via
`CARBONARA_STATION_BIN`), Windows
`%LOCALAPPDATA%\carbonara-station\bin\carbonara-station.ps1` (`Install-Self`;
`Copy-Item` is byte-for-byte, preserving the `.ps1` BOM — never rewrite the copy with
text APIs). This install needs NO admin (it lives in the user profile); on write
failure the client warns loudly and schedules the running copy instead (telling the
user not to move it). The elevated Windows re-run and the task `/TR` both point at the
installed copy. Watchdog: every `send` stamps `<confdir>/.last-send` (content = epoch
on Linux, mtime used on Windows — locale-independent, no schtasks output parsing);
`status` warns when the schedule exists but the last cycle is older than
`2×interval + 2 min`, and (Linux) when the scheduled path is gone. `purge` removes the
marker along with the rest of the config.

Windows scheduling (`Install-Schedule`): the routine is a Task Scheduler task that lives
independently of the interactive window — the goal is that it keeps sending with the
window CLOSED, with nobody logged on, and AFTER REBOOT. That requires running as SYSTEM
(`/RU SYSTEM /RL HIGHEST`), which needs admin. So when not elevated, Install-Schedule
OFFERS to self-elevate (`Start-Process -Verb RunAs -Wait`) and re-runs `schedule
-ConfigDir <dir> -Every <N>` in the elevated window (the `-Every` param skips the
re-prompt); it then confirms via `schtasks /Query`. If the user declines/UAC fails, it
falls back to a logged-on-only task and says so loudly. The action runs `send` with
`-WindowStyle Hidden` (no flashes) and embeds `-ConfigDir "<user's conf dir>"` — SYSTEM
has a different %LOCALAPPDATA%, so -ConfigDir is mandatory (the `.ps1` accepts it to
override its config paths). After creation, a best-effort `Set-ScheduledTask` (Win8+)
hardens settings: `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
-StartWhenAvailable -MultipleInstances IgnoreNew` (so it doesn't stop on battery and
catches up missed runs after downtime/reboot). Key-mode caveat: setup's `icacls` grants
the SSH key `SYSTEM:R` in addition to the user, otherwise the SYSTEM task couldn't read
the key and the SSH send would fail. The send mutex is `Global\CarbonaraStationSend`
(cross-session, SYSTEM task vs user window) with a local-namespace fallback.

**Resilience invariants for unattended stations** (keep in sync across both clients —
the operator may not visit the station for months):
- **Nothing the client writes may grow without bound.** `envios.log` is capped each
  send (`trim_log` / `Trim-EnvioLog`, ~2000 lines → keep 800). On Linux the trim
  truncates the SAME inode: cron holds an append-mode fd on that file, so recreating it
  would send every later cycle to an orphan inode. A client that fills the station's
  disk is the failure it is supposed to survive.
- **`sent.state` is written atomically** (temp + rename, both clients). Written in
  place, a full local disk truncates it and the next cycle re-sends the entire
  collection — harmless for the DB (idempotent) but hours of link on a slow station.
- **Failures are classified, not just numbered** (`explain_failure` /
  `Show-FailureHint`): server paused, credential refused, server disk full, network
  down. The message must say whether the STATION needs anything — the honest answer is
  almost always "no, and do NOT redo the wizard or generate a new key", because a
  re-run wizard at a remote station is what turns a server-side outage into a
  permanent one.
- Never add retry loops inside a cycle: the schedule IS the retry, and `flock -n` /
  the mutex keep cycles from stacking up.

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

Single polling loop in `ingest.py` — no watchers, no queue, **no parsers registry any
more** (the old `ingestion/parsers/` + `ingestion/config.yaml` were removed). The
ingestion is **driven by the dashboard's registrations**: it reads `dashboard/config.yaml`
(mounted read-only as `/app/dashboard-config.yaml`, reloaded when its mtime changes) and
ingests ONLY the files with `ingest: true`, and ONLY their `variables[]` with `ingest` not
false. A `texto` variable (radiosonde SERIAL, sensor model…) is stored too: `value` NULL,
the cell in `measurements.value_text` (migration 0006), quality 0, no QC; the file view
exposes it as a TEXT column and the API returns the text in the value field (raw only —
the aggregates are numeric). With `resample`, the LAST text of the bucket is kept. That registration IS the ingestion config (separator, header, timestamp
column/format, `timezone`, `resample`, per-variable `variable_id`/`min`/`max`/`type`).

Key invariants when editing:
- **The dashboard config is mounted as a DIRECTORY** (`./dashboard:/app/dashboard:ro`,
  `DASHBOARD_CONFIG=/app/dashboard/config.yaml`), never as a single file: `ConfigStore.save`
  writes atomically (tempfile + `os.replace`), which changes the inode, and a file bind
  mount stays pinned to the old inode — the ingestion would never see new registrations
  (this happened: a file showed "aguardando" forever). `DashboardConfig.load` reloads on
  mtime change; the Tratamento tab explains a pending file via `/monitored` (seen by the
  ingestion but no file matched) vs not seen at all.
- **`incoming/` is never touched.** The container mounts `CARBONARA_ROOT` at the SAME
  absolute path, read-only. Nothing is moved to `archive/`/`quarantine/` (those dirs no
  longer exist in the flow). Errors are recorded in the ledger (`ingest_files.status/error`).
- **Incremental by byte offset** (`carbonara_parse.records.iter_records`): the ledger
  `ingest_files` keeps, per REAL file (a dynamic path yields one row per day), `bytes_ingested`
  = where the next read starts, plus `head_sha256` of the first 64 kB. Only complete lines
  (ending in `\n`) are consumed. A file that shrank or whose head changed restarts from 0
  ("rotação"). Files modified within `INGEST_SETTLE_SECONDS` are skipped this cycle.
- **Parse is the shared package `carbonara_parse/`** (repo root; also imported by the
  dashboard via a `sys.path` insert in `dashboard/app/__init__.py`, and copied into the
  ingestion image). Never re-implement a parser: what the dashboard preview shows is what
  enters the DB. `variable_id_of(var)` = stored `variable_id` or `variable_slug(label)`.
- **Timezone**: each file declares the tz of its timestamps (`timezone`, IANA; empty =
  UTC; station/global tz as fallback); the DB always stores UTC (`timeparse.to_utc`).
  Epoch timestamps are UTC by definition and ignore the field.
- **`resample`** (`raw` | `1min` | `30min`): averages per bucket before insert (for 10–20 Hz
  files). Only COMPLETE buckets are emitted; the returned offset is the start of the still
  open bucket, re-read next cycle. Older files of a dynamic set (and files idle for
  > bucket+settle+60 s) are "closed" and flush everything.
- **QC**: `build_qc` = `range_check` with the registration's min/max (flag 2) + global
  checks from `processing/config.yaml`. Flag only, never modify.
- **Idempotent**: `ON CONFLICT (time, station_id, variable_id) DO NOTHING`; `file_id`
  (ledger row) is the provenance column. `python ingest.py --reprocess <station> [--file
  <key>] [--apagar]` resets offsets (and with `--apagar` deletes the rows those files
  produced) — that is the DB rebuild mechanism (there is no DB backup by design).
- The registration is mirrored into `monitored_files` every cycle (so the API/SQL know what
  is ingested without reading YAML); `ensure_station`/`ensure_variables` upsert from it.
- Budgets: `INGEST_MAX_BYTES` per file per cycle (64 MB) and `INGEST_CYCLE_BUDGET_BYTES`
  per cycle (512 MB); the newest file of a dynamic set is read first (fresh data before
  backfill). Reads happen in **slices of `INGEST_CHUNK_BYTES` (4 MB), one commit each**
  (ledger offset advances per slice): a 58 MB TOA5 with 62 variables would otherwise be
  millions of tuples in memory at once inside a 1 GB container. Per-file error isolation: exception → rollback + ledger `status='error'`,
  never crash the loop.
- Schema changes go in `database/migrations/NNNN_*.sql` (idempotent SQL; continuous
  aggregates cannot run inside a transaction) applied by `./database/migrate.sh` on an
  existing DB and by `database/init/05_migrations.sh` on first start — both record
  `schema_migrations`. `0001` added PostGIS (`stations.geom` generated from lat/lon),
  `monitored_files`, `ingest_files`, `pipeline_runs`, `api_keys`, `measurements.file_id`,
  and recreated `measurements_hourly` + `measurements_daily` as real-time aggregates over
  `quality = 0` only (with `n_flagged`).

## Processing library (`processing/`)

QA/QC applied **in memory, between parse and insert** — not a standalone service: the
ingestion container imports `processing.QualityPipeline`, builds it once at startup from
`processing/config.yaml` (mounted read-only; env `PROCESSING_CONFIG` overrides the path)
and calls `evaluate(measurement)` per parsed measurement; the returned int is what gets
inserted into `measurements.quality`. Key invariants when editing:

- **Parse flags win**: `evaluate()` returns the parser's flag untouched when
  `quality != 0`; the **first** violated check decides otherwise (order checks in the
  config from most to least severe).
- **Never modify or drop a measurement** — every row still enters the DB; rejection is
  only ever `quality != 0`, keeping raw data auditable.
- **Missing/empty config = QC disabled**, with a warning at ingestion startup — never a
  crash. Checks must be cheap (they run per measurement per file).
- **No retroactive re-evaluation**: inserts are `ON CONFLICT DO NOTHING`, so rule changes
  apply to new rows only; history is re-evaluated by re-dropping archived files into
  `incoming/` (idempotent inserts make this safe for values, but note conflicting rows
  keep their original flag).
- **Flag convention** (documented in `checks/base.py` and `processing/README.md`):
  `0` ok, `1` parse-invalid (parser), `2` out of physical range (`range_check`),
  `3+` reserved. New rules claim the next free flag via `default_flag` and document it.
- **Adding a check = plugin registry** (same pattern as parsers): subclass `Check` in
  `processing/checks/` implementing `violates(measurement) -> bool`, decorate with
  `@register("name")`, import it in `checks/__init__.py`, reference it in
  `processing/config.yaml` under `checks:`, then rebuild the ingestion image.
  `pipeline.py` and `ingest.py` do not change.

## Acquisition dashboard (`dashboard/`)

Data-acquisition monitoring dashboard ("Dashboard de Monitoramento de Aquisição de
Dados", credited to Helvecio Neto in the UI footer — keep the credit). Flask +
Chart.js, **runs natively on the host** — not in Compose: registers stations, tails
configured `.dat` files for per-variable charts (with physical min/max limit lines) and
computes live station status. Viewing is open; edits require the admin login
(`auth:` block in config.yaml, hashed password). Key points when editing:

- **Status logic is a Python port of `rsync-hub monitor`** (`app/status.py` mirrors
  `print_status` in `collection/rsync-hub`): last signal = max(heartbeat mtime, newest
  non-hidden file mtime in `incoming/<id>/`, last CONNECT in `connections.log`); window
  = `interval_min*120 + 120`s (interval from heartbeat CONTENT, liveness from its
  MTIME), fallback `stale_fallback_seconds` (1h). A hub pause overrides the computed
  state with `paused` (see the pause bullet above). If the shell logic changes, change
  `status.py` with it. There is **no per-station override** of the window and the
  watchdog path is not editable in the UI (user request): both belong to the
  station/client (the heartbeat declares `interval_min`), and the same
  `interval*120 + 120` lives in three places — `rsync-hub print_status`, `status.py`
  and the client's own `status` watchdog. A dashboard-only knob would silently
  disagree with all three. Legacy `heartbeat_file`/`offline_window_min` keys in an
  existing config are preserved but ignored (`heartbeat_file` still honored as a
  manual YAML override); don't re-add form fields for them.
- **Pause of reception in the UI** (`hub.pause_state/set_pause/clear_pause` +
  `POST /hub/pause|/hub/resume`): reading the markers is a plain filesystem read, so
  the state shows even without the sudoers rule; pausing/resuming shells out to
  `rsync-hub pause|resume` like every other write. The state is injected into EVERY
  page by the context processor (`pause_state`) and rendered as a banner in
  `base.html` — visible to non-admins too, since "no new data" is otherwise read as a
  station failure. Buttons live in three places (Coleta card, hub station page,
  station detail card) and all post `target` + `back` (a LABEL mapped through
  `url_for`, never a caller-supplied URL). `target` is always explicit (`--todas` or
  an id) because the sudoers rule is `pause *`, which does not match an argument-less
  command. `hub_info["can_pause"]` probes that rule separately so installs whose
  `setup-hub-access.sh` predates the feature get a "re-run it" hint instead of a
  failing click.
- **Freshness column on the stations tab** (*Últ. dado*, next to *Arqv. mon.*):
  `series.file_freshness` reads only the LAST timestamp of each monitored file
  (`FRESH_TAIL_LINES` lines from the end, 30 s per-station cache keyed by a
  signature of the file entries so an edit shows up immediately). It answers a
  question the status dot cannot: a station can be *Online* — rsync delivering on
  schedule — while a sensor has been dead for days, because the file still arrives,
  just with an old stamp. Thresholds `FRESH_WARN_SECONDS` (1 d, amber) /
  `FRESH_ALERT_SECONDS` (7 d, red); a file that has not arrived yet or whose stamp
  did not parse is `unknown` and rendered NEUTRAL grey, never as a failure (same
  rule as the pending chart). Only the late files are named in the cell (3 max,
  then "+N"); the full per-file list lives in the cell's `title`. It is computed in
  `pages.index`, NOT in `status.station_status` — `/api/status` is polled by other
  screens and must not pay for opening data files.
- **Server network rate** (`app/netspeed.py` + `/api/netspeed`, shown on the Estações
  page): reads the kernel counters in `/proc/net/dev` between two calls — it never
  generates traffic and never runs a speed test. "The interface with internet" is the
  one holding the default route in `/proc/net/route`; the others are listed as
  secondary. No `/proc` (non-Linux) → `available: false` and the widget hides. This is
  the HOST's throughput, not per-station bandwidth: real per-station accounting would
  need conntrack/nftables per IP or rsyncd log parsing — deliberately out of scope.
- **Database integration** (`app/api_client.py`, config block `api: {url, key,
  public_url}` edited in *Configurações*): a file registered with `ingest: true` gets its
  charts from the API (`series.series_from_api`, same JSON contract as the file path;
  labels shown in the file's `timezone`), falling back to the file tail on any API error
  (noted in `problems`). Per-file form fields `ingest`, `timezone`, `resample` and
  per-variable `variable_id` ("ID no banco": slug of the label if empty; unique within the
  file AND across the station's ingested files — validated server-side in
  `validate_file_entry(form, station=, file_id=)`, since the DB key is
  (time, station, variable)). `station_detail` shows a "no banco" chip per ingested file
  from `/files`. `/api-keys` (admin) lists/creates/revokes keys via the API. The dashboard
  is the ONLY place that decides what enters the DB — keep it that way.
- **Tratamento tab** (`/tratamento` index + `/tratamento/<station>/<file>` per file;
  `config_store.apply_treatment_form`): ONE place to decide what enters the DB and with
  which rules — per file (`ingest`, `timezone`, `resample`) and per variable (`ingest`
  true/false = monitored but kept out of the DB, `variable_id`, `min`/`max` → flag 2,
  `qc_spike` = max delta between consecutive readings → flag 3, `qc_stuck` = N identical
  readings → flag 4). The index is a compact table (station → files, counts); the per-file
  page has a search filter, "todas/nenhuma/inverter (só as visíveis)" and a sticky header
  (files have 60–120 variables — never list all variables of all files on one page again).
  The file form (`file_update`) must keep these per-variable keys when it rebuilds
  `variables[]` — `carry_treatment` matches by column/ID; don't drop it. Checks live in
  `processing/checks/{spike,persistence}_check.py` (stateful per station+variable; the
  ingestion reads each file in order).
- **Data type level** (`data_type` on every monitored file; `config_store.data_type_of`,
  suggestions `DATA_TYPE_SUGGESTIONS`, default `datalogger` for TOA5-shaped registrations
  else `dados`; mirrored in `monitored_files.data_type`, migration 0002): the hierarchy is
  **station → data type → file** (a tower has a met datalogger, a flux system, a radiometer,
  a profile… each with files). Editable in the file form and in Tratamento; the Tratamento
  index groups files by type; station detail shows a purple type chip.
- **`table_name`** (optional, per file; migration 0003; `config_store.table_name_of`): the
  name of the file's view in the station schema, default = registration id. Needed because
  a dynamic pattern created from a concrete file inherits a date-bearing id; the API accepts
  either file_key or table_name in the danger endpoints (`_registration`). Renaming moves the
  view; the old one is found by the `[<file_key>]` tag in the view comment. Identifiers may
  start with a digit (quoted everywhere) — `IDENT_RE` allows it.
- **QC precedence**: a variable with min/max in the registration is REMOVED from the global
  `processing/config.yaml` range check (`build_qc`), otherwise a generic id such as
  `pressure` (surface 850–1100 hPa) would flag a radiosonde profile (5–1000 hPa).
- **Banco tab** (`/banco`, admin, phpMyAdmin-like, through the API's `/admin/db/*`):
  "bancos" = PostgreSQL SCHEMAS. `public` = the system (measurements, stations, ledger…)
  — read-only. **One schema per station AND data type** (`<station>__<data_type>`, e.g.
  `eddyfarm__datalogger`), created by the ingestion (`ensure_station_view`), holding one
  **wide VIEW per monitored file** (one column per variable + `flags`, built from
  `measurements`, recreated only when the variable list or the type changes; a view of the
  same file in another schema of the station — old type, or the pre-0002 `<station>.<file>`
  shape — is dropped, and an emptied schema with it) and any user-created tables. The
  sidebar renders a tree station → type; `/admin/db/schemas` splits the name on `__` and
  reports `station`/`data_type`; dashboard routes derive the station with `_station_of()`. "Gerais" schemas hold cross-station user tables.
  Sidebar = schemas; pages: schema (tables + "nova tabela" with typed columns, optional
  hypertable), table (tabs Dados with sort/filter/pagination by `ctid`, Estrutura,
  Inserir; drop with typed confirmation). Writes are allowed ONLY on user tables
  (`editable` = regular table outside `public`); views and system tables never. The free
  SQL box stays SELECT/WITH/EXPLAIN, READ ONLY, 20 s, ≤500 rows. This deliberately keeps
  ONE physical measurements hypertable (API, aggregates, QC, replication all rely on it)
  — the per-station "databases" are views over it, not separate PostgreSQL databases.
  asyncpg gotchas met here: `relkind` comes back as bytes (cast `::text`), `format('%I.%I',
  $1::text, $2::text)`, `ctid = $1::text::tid`.
  Generated views are paginated BY TIME through an anchor variable (`_rows_generated_view`):
  a plain `ORDER BY time LIMIT` would aggregate the whole station first (minutes on 6 M rows).
  Layout rule learned the hard way: these pages use `main.wide` and every table sits in a
  `.table-wrap` (overflow-x) — tables wider than the card pushed buttons outside the border.
- **Banco → Conexão** (`/banco/conexao`, `app/dbconn.py`): shows where the DB is (from the
  repo `.env` + the API's `/admin/db/connection`) and lets the admin point the services at
  another PostgreSQL: modes `local` (`DB_HOST=db`, `COMPOSE_PROFILES=local-db`) and
  `externo` (`DB_HOST=<ip>`, `DB_INTERNAL_PORT`, `COMPOSE_PROFILES=` empty → the `db`
  service does not start). It edits ONLY the connection keys of `.env` (atomic, mode kept),
  "Testar conexão" runs asyncpg inside the api image with the form values (nothing
  written), "Aplicar aos serviços" runs `docker compose up -d api ingestion [db]` as the
  dashboard user (docker group; no sudo) and "Parar banco local" is a separate explicit
  action. `db` therefore lives under the `local-db` profile and api/ingestion have NO
  `depends_on` (both wait/retry for the DB themselves: `db.connect` retries, ingestion
  `wait_for_schema`). Keep `COMPOSE_PROFILES=local-db` in `.env` (setup writes it) or
  `docker compose up -d` will silently skip the database.
- **Banco → Diagnóstico e manutenção** (`/banco/diagnostico`, API `admin_maint.py`):
  read-only checks (ledger errors, backlog, bad timestamps, registrations that never found
  a file, same file under two registrations, same label with different IDs in a station,
  orphan series, stale series, day gaps in 90 d from `measurements_daily`, > 20 % flagged,
  implausible timestamps < 1990 / future, uncompressed old chunks, dead tuples, failed
  Timescale jobs, long queries, inactive slots retaining WAL) each with level/hint and an
  optional named action; `/admin/db/maintenance` actions: `analyze`, `vacuum`, `compress`,
  `refresh_aggregates`, `purge_implausible_times`, `purge_orphans` (purges require typing
  APAGAR in the UI; every run is logged in `pipeline_runs`). Never add implicit writes.
- **Review-driven invariants (26/09/2026 audit)** — keep them:
  - The free SQL box runs on a dedicated **read-only role** `carbonara_ro` (`db.ensure_ro_pool`:
    `pg_read_all_data`, `default_transaction_read_only`, 20 s timeout; password derived from
    `API_KEY_PEPPER`, (re)set at every start under an advisory lock because two uvicorn
    workers start together). The keyword regex is defense in depth, not the barrier; every
    query is wrapped in `SELECT * FROM (...) _q LIMIT n+1` (an inner LIMIT does not protect
    the worker from materializing millions of rows).
  - **One ingestion process at a time**: `run_cycle` takes `pg_try_advisory_lock('carbonara-ingest')`
    (skips the cycle if busy); `--reprocess` takes the blocking lock around reset + cycle.
    Without it the service overwrote the reset offsets with the ones it had read before.
  - Files are processed in **chronological order** and the QC pipeline per registration is
    **kept across cycles** (`qc_for`): spike/persistence compare with the previous reading,
    so rebuilding them each cycle (one new record per cycle) would never fire them.
  - A file is "closed" (flush the open resample bucket, deliver a last line without `\n`)
    only when a newer file exists or it has not changed for `INGEST_CLOSE_AFTER_SECONDS`
    (2 h) — rsync delivers in batches and preserves the station mtime. A slice stopped by
    the byte cap never flushes (`state['eof']`), or a half bucket would be frozen by
    `DO NOTHING`. Settle uses `st_ctime` (server write time), not the station mtime.
  - `head_sha256` is compared over the SAME length (`ingest_files.head_len`, migration
    0005): a file under 64 kB changed signature at every append and restarted from zero.
  - `insert_measurements` counts per page (`cur.rowcount` only covers the last page).
  - Dashboard: **CSRF guard is app-wide** (`app.before_request` in `create_app`,
    Sec-Fetch-Site/Origin) + `SESSION_COOKIE_SAMESITE=Lax`; `dbconn` passes the DB
    password through the environment (never argv) and quotes `.env` values.
  - API rate limit uses `request.client.host` only (uvicorn honours `X-Forwarded-For` just
    for `TRUSTED_PROXY_IPS`), is applied BEFORE the key lookup, negatives are not cached,
    caches are pruned; key cache TTL 15 s.
  - `/series` validates station (404) and variables (`unknown_variables`), reports
    `limit_applied`/`first_time`/`last_time`/`hint`; naive `start`/`end` are read in `tz`.
    `/stations/{id}` hides file paths/metadata from anonymous callers.
- **Zona de perigo** (tab `aba=perigo` on a table page; API `/admin/db/station/{clear,
  drop-variable,drop-table}` and `/admin/db/tables/{truncate,drop-column}`): for a generated
  station table — zerar (delete the file's variables for that station AND reset its ledger
  rows so the ingestion re-reads from byte 0 = "reapply rules to history"), apagar variável
  (delete one variable's rows of the station), apagar tabela (delete data + DROP VIEW +
  delete ledger + `monitored_files.ingest=false`, and the dashboard also sets `ingest:
  false` on the registration so the next cycle does not recreate the view); for user tables
  — TRUNCATE, DROP COLUMN, DROP TABLE. Every action needs the exact confirmation text (table
  name / variable id) typed in the UI AND re-checked by the API, and is logged in
  `pipeline_runs` (pipeline `danger`). `incoming/` is never touched.
- **CSV export** on every table page (`/banco/<schema>/<table>/exportar`): generated
  views go through `/series?format=csv` (long format, period params), other tables/views
  dump up to 500 rows. **`/guia`** is the public researcher guide (endpoints, flags,
  Python/R/curl examples, good practices) — keep it in sync with the API.
- **Config-driven** (`dashboard/config.yaml`, gitignored; example versioned): the UI
  itself edits it via `ConfigStore` (atomic write: tempfile + `os.replace`, under a
  lock). Relative `data_root` resolves against the **config file's directory**, not cwd.
- **Never loads whole data files** — `app/tailer.py` seeks from the end; keep it that
  way (datalogger `.dat`s grow unbounded). Campbell `NAN`/`±7999` become `None` (gaps).
- **Space-separated files** (`.sfo` and many `.dat`): `separator: " "` makes
  `tailer.parse_rows` use `str.split()` instead of `csv.reader` — runs of spaces are
  alignment, never empty columns, and a phantom column would silently shift every
  index-mapped variable after it. The form writes/reads it as `\s` (a lone space in a
  text input is invisible and gets stripped); `\t` behaves the same way.
- **Each variable has a primitive `type`** (`tailer.COLUMN_TYPES`, default `numero`,
  absent = `numero` for old configs): `numero` · `inteiro` · `booleano` (0/1, also
  sim/não·true/false·on/off, and numeric fallback so `0000` is false) · `epoch` ·
  `timestamp` (date/time as TEXT — no per-variable format field: `parse_datetime_text`
  tries the file's `ts_format` then the usual patterns) · `texto`.
  The plotted value is ALWAYS numeric — the type changes how it is READ: `epoch`/
  `timestamp` plot epoch seconds and are displayed as a clock (the API sends `ts_res`,
  the JS formats), `booleano` becomes sim/não with a 0–1 axis, `inteiro` drops decimals.
  `texto` is the exception: it is NOT a time series, so `station_detail.html` renders a
  last-value box instead of a canvas and the compare picker skips it (a text variable
  would otherwise be an unexplained empty line).
  **The types must also apply in the parse PREVIEW** (`build_preview.col_display`,
  keyed by column index): the preview is where you verify a mapping before saving, so
  every mapped column shows its converted value under the raw cell — a column typed
  `epoch` that still reads as a 10-digit number is the bug this closes. Keep
  `column_display` as the single conversion path shared by the preview.
- **Variable column index is user-editable** (a 118-column `.sfo` makes "add rows until
  you reach column 104" absurd). New rows still auto-fill the next free index. The
  guards are SERVER-side in `validate_file_entry` — no duplicates, never the timestamp
  column — and negative indexes are legitimate (`-1` = last column, resolved by
  `series._column_index`); a non-numeric value is a column NAME, which needs
  `header_line`. Don't put `readonly` back on that input.
- **Timestamp display: full date always, clock resolution follows the DATA**
  (`series.ts_display_format` picks `TS_DISPLAY` / `_S` / `_MS` from the SMALLEST gap
  between consecutive stamps; `fmt_ts` trims `%f` to milliseconds). A 30-min datalogger
  gets `24/08/2026 00:29` (seconds would be noise); a per-second or sub-second file
  (`.sfo`) gets `…:47` / `…:47.833`, otherwise different records collapse into the same
  label. All stamps equal → max resolution, which is exactly what exposes "the chosen
  time column does not advance per record". Drives the chart axis, `last_reading` and
  the preview; the compare page builds its own labels in JS (full date + a clock whose
  resolution follows `cmpAxisStep` — the common axis is bucketed at the FINEST
  median gap among the selected series, capped at 60 s and floored at 1 ms; the
  60 s cap is what still aligns two dataloggers writing the same minute at
  different seconds, and the fine end is what keeps a 1 s `.sfo` from collapsing
  into three points. `series.build_series` therefore emits `epochs` with
  milliseconds — never re-truncate it to `int`). A file's timestamp is NEVER shown raw: the preview
  prints the raw cell (that is the point of a parse check) with the converted value
  underneath, or "não convertido". `parse_epoch` also REJECTS stamps outside
  1990–2100: pointing the time axis at a non-epoch column (an angle `359`, a temp
  `26.43`) would otherwise silently render 1970 dates instead of saying the column is
  wrong. Station liveness (`status._fmt_signal`) deliberately keeps seconds — it answers
  "is it sending right now?", not "when was this measured".
- **Timestamp column**: `timestamp.format` is a strftime pattern OR the literal
  `epoch` (any of `config_store.EPOCH_MARKERS`, normalized to `epoch` on save), meaning
  Unix seconds — fraction preserved (`1787542188.433029` → microseconds) and ms/µs/ns
  auto-scaled by magnitude (`series.parse_epoch`; the ranges never overlap this
  century). It applies to whatever column is the time axis — the marker is in the
  FORMAT, not the position. `EPOCH_MARKERS` lives in `config_store` because `series`
  already imports it (the reverse would be a cycle).
- **Rotating filenames** (`dynamic_path` + a strftime `path`, optionally with `*`):
  resolved on EVERY read from the server clock (`config_store.resolve_file_path`), so
  one registration follows the current day/hour file. **Wildcards are part of the
  contract**: equipment often names the file after the instant it was CREATED
  (`2026-08-25__07-13-05.sfo`), so only the date is predictable — `%Y-%m-%d__*.sfo`
  matches today's file whatever the time in the name (newest wins), and a glob with no
  time token at all (`*.sfo`) means "the newest file in this folder".
  `time_path_glob(..., keep_wildcards=True)` stops escaping them for that case.
  **The card title must follow the file, not the registration**: a file added through
  the browser gets an `id`/`alias` derived from the CONCRETE filename, so a dynamic
  entry would keep showing yesterday's name forever. `station_detail` resolves the
  current name server-side (`live_names`, never fatal) so the first paint is right, the
  JS refreshes it each cycle, the id moves to the chip's tooltip, and an
  auto-generated alias (one that CONTAINS the id) is hidden — a hand-written alias
  still shows. Missing current file → falls back to the newest match of
  the pattern (`time_path_glob` turns `%Y` into `????`), reported in `path_info` and as
  a `problems` note. Nothing matches at all → `{"pending": True, ...}`, which the UI
  renders in a NEUTRAL tone and hides the charts (`.series-pending`) — a file that has
  not arrived yet is not a misconfiguration and must never look like a failure.
  Human-written aliases (`%YYYY`, `%mm`, `%DD`…) are normalized to strftime on save
  (`normalize_time_tokens`): strftime would expand `%YYYY` to `2026YYY` **without
  erroring**, giving a silently empty chart.
- Chart.js is **vendored** in `dashboard/static/` (no CDN — the host may be offline).
- Read-only over the data: removing stations/files from the dashboard never touches disk.
- **Hub management page** (`/hub`, blueprint `app/hub_views.py` + logic `app/hub.py`):
  admin-only (even viewing — it exposes credentials/downloads). Reads (station list,
  `stations/<id>/` credentials, file browser over `incoming/`/`archive/`/`quarantine/`/
  `stations/` with downloads) go straight to the filesystem — the hub's open read
  policy makes that sudo-less. The per-station "kit de ativação" ZIP
  (`/hub/stations/<id>/kit`) bundles `stations/<id>/` credentials + the
  `collection/station/` clients + a generated LEIA-ME — files are copied byte-for-byte
  (the `.ps1` BOM invariant), with external_attr modes so the private key extracts as
  600 and the `.sh` as executable. Writes (authorize/revoke stations) shell out to the
  **installed `rsync-hub`** via `sudo -n` (never reimplement its key/rsyncd logic);
  `dashboard/setup-hub-access.sh` (run once as root on the server) installs the
  sudoers rule restricted to `add`/`remove`/`purge`/`move-root` — validated with
  `visudo -cf` before install; without it the page degrades to read-only with the
  hint on screen. Station deletion (`POST /hub/stations/<id>/purge`) re-validates
  the typed station name server-side (the JS unlock is convenience only) and, on
  success, also drops the station from the dashboard's own config.
  **Changing the root always rewrites the stations' ABSOLUTE paths**
  (`hub.rewrite_absolute_paths`, wired into all three entry points: the move-root
  route, `pages.settings_save`, and `POST /hub/align-root`). A registered absolute
  `heartbeat_file`/`files[].path`/`connections_log` would otherwise keep reading the
  old, frozen tree — which looks exactly like "the stations went offline".
  Page order on `/hub` is deliberate (user request): stations list → authorize a new
  station → global reception pause, with a discreet base/version line last. There is
  **no general "browse the areas" card** — it duplicated the per-station *arquivos* /
  *gerenciar* buttons; `hub.files` stays reachable from those, so don't re-add it.
  Anything that acts on the ROOT itself (edit `data_root`, migrate it, align it with
  the hub) lives in *Configurações*, never in *Coleta* — Coleta is for stations, their
  credentials and the file browser. The `/hub` page only shows a one-line pointer when
  the roots diverge (user request; don't move the controls back there).
  `/hub/align-root` covers the path the UI does not control: a `move-root` run from
  the TERMINAL updates `/etc/rsync-hub.conf` but cannot touch the dashboard's config,
  so the page detects the divergence (`base_mismatch`) and offers one click to adopt
  the hub's BASE. Do not "simplify" any of these three call sites away.
  Moving the data root lives as a card in *Configurações* → POST `/hub/move-root`
  (`rsync-hub move-root <new> --confirmo`, long timeout); on success the dashboard
  updates its own `data_root`, while compose `.env` remains a manual follow-up (shown
  to the admin). File browsing stays strictly read-only (no
  delete/move — ingestion owns the `incoming/ → archive|quarantine` lifecycle), and
  `safe_resolve` blocks path traversal (resolved path must stay under the area root).
  `hub.files` accepts an optional `?estacao=<id>` that only changes the "back" button
  (validated with `valid_hub_id`, rebuilt via `url_for` — never a caller-supplied URL,
  so no open redirect); it is threaded through the breadcrumb/area/subfolder links so
  the origin survives navigation.
- **Station file browser inside *Estações → Editar*** (card *Arquivos desta estação no
  servidor*): the SAME browsing UX as the `/hub` one — one folder at a time,
  breadcrumbs, size/date, download — **plus** a checkbox per file to add it to
  monitoring. It replaced two redundant cards (a link-out card + a recursive
  `.dat`-only discovery list); keep it as ONE card. Backend is
  `config_store.browse_station_dir` (one level, no recursion — so it can load on page
  open without walking huge datalogger trees) behind `api.station_browse`
  (`/api/stations/<id>/browse?sub=`), with `station_safe_resolve` as the
  anti-traversal gate (mirrors `hub.safe_resolve`) also used by
  `pages.station_file_download`. Hidden entries are SHOWN and flagged (parity with the
  hub browser) but never selectable — ingestion ignores them. The JS
  (`initStationBrowser`) keeps the selection in a Map **outside** the table so it
  survives folder changes, and materializes the server's existing
  `pick`/`path_<n>/alias_<n>` contract as hidden inputs only at submit — don't change
  that contract in `files_discover_add`. `monitored` matching stores BOTH the literal
  and the resolved path (symlinked roots like macOS `/var` → `/private/var`).
  Everything here is admin-only twice over: the card renders under
  `{% if is_admin %}` and every route is `admin_required` — keep both guards.
- **Station creation lives only in *Coleta*** (authorize in rsync-hub, or *Importar do
  rsync-hub*). The *Estações* tab deliberately has NO "Nova estação" button — a
  station created there would have no counterpart on the hub. `pages.station_new`
  still exists for direct URL use; don't re-add the button.
- Lifecycle: `dashboard/dashboardctl` (`iniciar|parar|reiniciar|status|log|servico`,
  English aliases accepted) — background run via nohup+setsid+PID file (survives SSH
  disconnect), auto-creates `.venv/` on first start, `servico` installs a systemd
  unit `carbonara-dashboard` running as the dashboard dir's owner (never root) and
  hands control to systemctl (start/stop refuse while the unit is active).
- This is the admin slice of a future full dashboard that will read from TimescaleDB.

## FTP de distribuição (`ftp/ftp-hub` + página `/ftp` da dashboard)

In production the FTP is **native vsftpd on the host** (not the compose `pure-ftpd`;
install log in `docs/ftp-native-install.md`): local system users authenticated by PAM,
`chroot_local_user`, per-user profile files in `user_config_dir`
(`/etc/vsftpd/user_conf/<user>`), `local_root` = the served root (`<BASE>/ftp` on the
server). The FTP only DISTRIBUTES; nothing arrives through it. Two pieces, same
privilege model as the collection layer:

- **`ftp/ftp-hub`** (bash, root, self-installs to `/usr/local/bin/ftp-hub` via
  `install [dash-user]`, which also writes `/etc/sudoers.d/carbonara-dashboard-ftp`
  validated with `visudo -cf`, restricted to `add remove senha perfil pasta desativar
  ativar`). It is the ONLY place that runs `useradd`/`chpasswd`/`usermod`/`userdel`
  and writes `user_conf`. Bump `FTP_HUB_VERSION` on behavior changes. Invariants:
  - **Passwords never on the command line** (`ps` leak): `--senha-stdin` (first stdin
    line) or `--gerar` (prints `SENHA=…` once; the dashboard strips that line from any
    displayed/logged output and shows it a single time via the session).
  - **`is_ftp_account` gate**: `remove/senha/perfil/pasta/desativar/ativar` only act
    on uid ≥ 1000 + nologin shell + (profile file exists OR home under the FTP root or
    `/srv/ftp`); `root`, `HUB_USER`, `SUDO_USER`, `ftp`, `nobody` are always refused —
    the dashboard can never `userdel` a real user. `add` refuses existing users.
  - **Never deletes data**: `remove` is `userdel` WITHOUT `-r`; folders are created
    (`2775`, group = owner group of the FTP root) but never removed.
  - Profiles: `leitura` (`write_enable=NO` + full `cmds_denied`), `escrita`
    (`write_enable=YES`, `local_umask=002`), `upload` (write, `cmds_denied=DELE,RMD,
    XRMD,RNFR,RNTO,SITE,RETR`). Writers join the FTP root's owner group (`carbonara`),
    readers are removed from it. Header `# ftp-hub: perfil=… pasta=…` makes the profile
    machine-readable; accounts predating the script are INFERRED from directives
    ("conta antiga" in the UI). Disable = name in `/etc/ftpusers` (PAM deny list,
    readable without root) + `usermod -L`. Control files go through `write_replace`.
  - Subfolder syntax is validated identically in bash (`norm_sub`) and Python
    (`ftp.normalize_subfolder`): components `[A-Za-z0-9_][A-Za-z0-9._-]*`, no `..`.
- **Dashboard** (`app/ftp.py` logic, `app/ftp_views.py` blueprint `/ftp`,
  `app/ftp_publish.py` publisher; admin-only even for viewing). READS are sudo-less
  (`/etc/vsftpd.conf`, `pwd`, profile files 644, `/etc/ftpusers`, tail of
  `/var/log/vsftpd.log` when the dashboard user is in `adm`); WRITES shell out to the
  installed `ftp-hub` via `sudo -n` — never reimplement account logic in Python.
  `list_accounts` must keep the SAME criteria as `is_ftp_account`. Port-listening
  check parses `ss -ltn`; never connect to the FTP (it would log a CONNECT per refresh).
  POSTs on the blueprint reject cross-site requests (`Sec-Fetch-Site`/`Origin`).
- **Links (bind mounts) — the preferred "mirror without copying"** (`ftp-hub link <sub>
  <abs-source> [--rw]` / `unlink` / `links` / `link-apply`; dashboard card *Vínculos*).
  Symlinks do NOT work here: accounts are chrooted and the kernel resolves a symlink
  target inside the jail, so a link to anything outside the FTP root shows as broken.
  A bind mount is a real directory in the chroot's view: zero space, instant changes,
  and `remount,bind,ro` (default) keeps write-profile accounts from touching the
  source. Registry `/etc/ftp-hub/links` (`sub<TAB>source<TAB>ro|rw`, 644, read by the
  dashboard without sudo); systemd unit `carbonara-ftp-links.service` (written by
  `install`, `Before=vsftpd`) re-applies it at boot — we never edit `/etc/fstab`
  (the pre-existing `carbonara-dados` fstab bind shows up as `fstab/manual`).
  Guards in BOTH bash (`valid_src`/`FORBIDDEN_SRC`) and Python
  (`ftp.source_forbidden`/`FORBIDDEN_SOURCES`, also applied to copy rules): source
  absolute, exists, not `/`, not under `/etc /home /root /boot /proc /sys /dev /run
  /var/log /var/lib /usr /bin /sbin /lib* /snap`, not inside the FTP root; dest is a
  non-root subfolder that is EMPTY or new (mounting over content would hide it).
  `unlink` never deletes anything (rmdir only if empty). The picker area `sistema`
  browses the whole filesystem (admin-only, one level, forbidden roots hidden).
- **Folders** (card *Pastas do FTP*, `ftp.create_folder`/`remove_folder`): direct
  Python writes, no sudo (FTP root is writable by the dashboard user); `mkdir` 2775,
  `rmdir` ONLY for empty non-mountpoint dirs — the dashboard never deletes FTP data.
- **Publications** (`ftp.publications` in config.yaml, edited by the UI): rules
  `source` (relative to hub BASE or absolute — any folder, today `incoming/<id>/…`,
  tomorrow the processing output) → `dest` (subfolder of the FTP root; the root itself
  is refused), `include`/`exclude` globs, `delete` (mirror), `hidden`, `interval_min`
  (0 = manual), `enabled`. The `Publisher` thread (started in `create_app`, disabled by
  `CARBONARA_NO_PUBLISHER=1`) re-reads the config every 20 s and runs due rules; each
  run is one `rsync -rtp --no-owner --no-group --chmod=Du=rwx,Dg=rwxs,Do=rx,Fu=rw,Fg=rw,Fo=r`
  as the dashboard user, NO sudo (the FTP root is group/world-writable by the hub's
  policy; `ftp-hub install` checks it). Symbolic `--chmod` on purpose: octal is rsync
  3.x-only. Filter order: excludes, `.*` (unless hidden), `--include=*/` + includes +
  `--exclude=*` + `--prune-empty-dirs`; `--delete` without `--delete-excluded`, so a
  mirror only removes files the rule covers (verified on the server's rsync 3.2.7).
  Validation refuses a source inside the FTP root, overlapping source/dest and `/`.
  Per-rule lock (no self-overlap), rc 23/24 = "avisos" (partial), state JSON written
  atomically, log capped. `describe()` feeds the table; `/ftp/publicacoes.json` is
  polled by the page. The FTP root is also browsable as area `ftp` in `hub.AREAS`
  (`area_root` special-cases it through `ftp.ftp_root`). macOS ships openrsync, so
  the publisher cannot be validated locally — test rsync behavior on the Linux server.

## Data API (`api/`)

FastAPI + asyncpg, container `api` on `API_PORT` (8000; ask the STI to open it — 5432
stays on 127.0.0.1). Endpoints: `/stations`, `/stations/{id}`, `/variables`, `/healthz`
(open, per-IP rate limit) and, with a key, `/series` (`agg=raw|1h|1d`, `qc=good|all`,
`order=desc` = the LAST N points — what the dashboard uses, `tz=`, `format=json|csv`),
`/latest`, `/stations/{id}/variables`, `/files` (ledger), `/admin/keys`. Docs at `/docs`.
- **Keys** (`api/app/auth.py`): `X-API-Key` (or Bearer / `?api_key=`); DB stores only
  `sha256(API_KEY_PEPPER + key)` + `prefix`; scopes `read` / `download` / `admin` (admin
  implies all); `expires_at`, `revoked_at`, `last_used_at`; token-bucket rate limit per key
  (`rate_limit`/min — with 2 uvicorn workers the effective limit is ~2×). The clear-text key
  is returned ONCE (creation). First key: `docker compose exec api python -m app.keys_cli
  create --owner dashboard --scopes read,admin`; then the dashboard manages keys at
  `/api-keys` through the API (it never touches the DB).
- Aggregates come from the continuous aggregates (real-time, quality=0 only); raw reads
  are capped (`API_DEFAULT_RAW_POINTS`/`API_MAX_RAW_POINTS`, higher with `download`).

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

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |
