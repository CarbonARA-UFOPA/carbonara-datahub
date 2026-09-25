"""Publicador: executa as regras "pasta do hub → FTP" com rsync, dentro do processo da
dashboard (a dashboard é o concentrador — fica sempre no ar, como serviço).

Modelo:
- Uma THREAD de fundo acorda a cada ``POLL_SECONDS``, relê o config (edições pela UI
  valem sem reiniciar) e dispara as regras habilitadas cujo intervalo venceu.
  ``interval_min = 0`` = só manual (botão "publicar agora").
- Cada execução é UM ``rsync`` local, do usuário da dashboard, sem sudo: a raiz do
  FTP é gravável por ele (o ``ftp-hub install`` confere isso). Os arquivos chegam
  664 / pastas 2775 (grupo herdado por setgid) — legíveis por QUALQUER conta FTP.
- A origem NUNCA é modificada (só leitura: ``-rt`` sem ``--remove-source-files``).
  Ocultos (``.carbonara-status``, ``.rsync-parcial/``) ficam de fora por padrão.
- ``delete`` (espelhar) apaga no destino só o que a regra COBRE (filtros de inclusão
  também protegem o que não casa): um arquivo que um usuário FTP subiu na mesma
  pasta e não casa com o filtro não é tocado. Ainda assim, o padrão é acumular.
- Estado por regra em ``ftp-publish.state.json`` (escrita atômica) e um log de texto
  ``ftp-publish.log`` com teto (nunca cresce sem limite — mesma disciplina dos
  clientes das estações). Ambos ficam ao lado do config.yaml.
- Uma regra não roda em paralelo consigo mesma (uma execução longa não é empilhada
  pelo próximo tique). Regras diferentes rodam em paralelo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import ftp

POLL_SECONDS = 20
RUN_TIMEOUT = 6 * 3600         # uma publicação gigante não pode travar a thread para sempre
IO_TIMEOUT = 600               # --timeout do rsync (sem progresso por 10 min → falha)
LOG_MAX_LINES, LOG_KEEP_LINES = 5000, 2000

_STATS = {
    "files": re.compile(r"Number of (?:regular )?files transferred: ([\d,]+)"),
    "deleted": re.compile(r"Number of deleted files: ([\d,]+)"),
    "bytes": re.compile(r"Total transferred file size: ([\d,]+) bytes"),
    "total": re.compile(r"Number of files: ([\d,]+)"),
}


class Publisher:
    def __init__(self, store, conf_dir: Path) -> None:
        self.store = store
        self.state_path = Path(conf_dir) / "ftp-publish.state.json"
        self.log_path = Path(conf_dir) / "ftp-publish.log"
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._running: set[str] = set()
        self._thread: threading.Thread | None = None

    # --- ciclo de fundo -----------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="ftp-publisher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        time.sleep(5)  # deixa o servidor subir antes do primeiro tique
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — a thread nunca morre por uma regra
                self.log(f"ERRO no ciclo do publicador: {exc!r}")
            time.sleep(POLL_SECONDS)

    def tick(self) -> None:
        cfg = self.store.load()
        state = self.load_state()
        now = time.time()
        for rule in ftp.publications(cfg):
            if not rule["enabled"] or rule["interval_min"] <= 0:
                continue
            last = float((state.get(rule["id"]) or {}).get("last_start") or 0)
            if now - last >= rule["interval_min"] * 60 and rule["id"] not in self._running:
                self.run_async(cfg, rule, trigger="agenda")

    # --- execução -----------------------------------------------------------------------
    def is_running(self, pid: str) -> bool:
        return pid in self._running

    def run_async(self, cfg: dict, rule: dict, trigger: str = "manual") -> bool:
        """Dispara em thread própria; False se a regra já está executando."""
        with self._run_lock:
            if rule["id"] in self._running:
                return False
            self._running.add(rule["id"])
        threading.Thread(target=self._run_guarded, args=(cfg, rule, trigger),
                         name=f"ftp-pub-{rule['id']}", daemon=True).start()
        return True

    def _run_guarded(self, cfg: dict, rule: dict, trigger: str) -> None:
        try:
            self.run(cfg, rule, trigger)
        finally:
            with self._run_lock:
                self._running.discard(rule["id"])

    def build_cmd(self, cfg: dict, rule: dict) -> tuple[list[str], Path, Path]:
        src = ftp.resolve_source(cfg, rule["source"])
        dst = ftp.dest_path(cfg, rule["dest"])
        # Modos em forma SIMBÓLICA (pastas 2775, arquivos 664): a forma octal só existe
        # no rsync 3.x; a simbólica vale também no 2.6.9 (macOS de desenvolvimento).
        cmd = [shutil.which("rsync") or "rsync", "-rtp", "--no-owner", "--no-group",
               "--chmod=Du=rwx,Dg=rwxs,Do=rx,Fu=rw,Fg=rw,Fo=r",
               "--stats", f"--timeout={IO_TIMEOUT}"]
        # Ordem dos filtros importa: exclusões primeiro (vencem), depois as inclusões
        # com '*/' (para descer nas pastas) e um '--exclude=*' final que descarta o resto.
        for pat in rule["exclude"]:
            cmd.append(f"--exclude={pat}")
        if not rule["hidden"]:
            cmd.append("--exclude=.*")
        if rule["include"]:
            cmd.append("--include=*/")
            for pat in rule["include"]:
                cmd.append(f"--include={pat}")
            cmd += ["--exclude=*", "--prune-empty-dirs"]
        if rule["delete"]:
            cmd.append("--delete")
        cmd += [str(src) + "/", str(dst) + "/"]
        return cmd, src, dst

    def run(self, cfg: dict, rule: dict, trigger: str = "manual") -> dict:
        pid = rule["id"]
        started = time.time()
        self._update(pid, {"status": "executando", "last_start": started, "trigger": trigger,
                           "message": ""})
        cmd, src, dst = self.build_cmd(cfg, rule)
        result = {"status": "erro", "ok": False, "rc": None, "files": 0, "bytes": 0,
                  "deleted": 0, "message": ""}
        try:
            if not src.is_dir():
                raise FileNotFoundError(f"origem não existe: {src}")
            # Trava anti-escape: o ancestral existente mais próximo do destino,
            # RESOLVIDO (symlinks inclusos), precisa continuar sob a raiz do FTP.
            ftp_root = ftp.ftp_root(cfg).resolve()
            anc = dst
            while not anc.exists() and anc != anc.parent:
                anc = anc.parent
            if not ftp._is_within(anc.resolve(), ftp_root):
                raise PermissionError(f"destino fora da raiz do FTP: {dst}")
            self._ensure_dir(dst, ftp_root)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT,
                               stdin=subprocess.DEVNULL)
            stats = self._parse_stats(r.stdout or "")
            result.update(stats)
            result["rc"] = r.returncode
            err_tail = "\n".join((r.stderr or "").strip().splitlines()[-5:])
            if r.returncode == 0:
                result.update(status="ok", ok=True, message="")
            elif r.returncode in (23, 24):
                # 23 = alguns arquivos não copiaram (permissão); 24 = sumiram durante a
                # cópia (datalogger reescrevendo) — o resto foi. Avisar, não falhar.
                result.update(status="avisos", ok=True, message=err_tail or f"rsync rc={r.returncode}")
            else:
                result.update(status="erro", ok=False,
                              message=err_tail or f"rsync falhou (rc={r.returncode})")
        except subprocess.TimeoutExpired:
            result["message"] = f"tempo esgotado ({RUN_TIMEOUT // 3600} h) — publicação interrompida"
        except Exception as exc:  # noqa: BLE001
            result["message"] = str(exc)
        ended = time.time()
        result.update(last_start=started, last_end=ended, duration=round(ended - started, 1),
                      trigger=trigger)
        self._update(pid, result)
        self.log(f"{rule['id']} [{trigger}] {result['status']} · {result['files']} arquivo(s) · "
                 f"{ftp_fmt_bytes(result['bytes'])}"
                 + (f" · {result['deleted']} removido(s)" if result.get('deleted') else "")
                 + f" · {result['duration']} s"
                 + (f" · {result['message']}" if result['message'] else ""))
        return result

    @staticmethod
    def _ensure_dir(dst: Path, ftp_root: Path) -> None:
        """Cria o destino (e intermediárias) sob a raiz do FTP com 2775 — legível por
        todas as contas, gravável pelo grupo. Só cria o que está DENTRO da raiz."""
        parts = []
        p = dst
        while not p.exists():
            parts.append(p)
            p = p.parent
        for d in reversed(parts):
            d.mkdir(mode=0o2775)
            try:
                os.chmod(d, 0o2775)
            except OSError:
                pass

    @staticmethod
    def _parse_stats(out: str) -> dict:
        res = {"files": 0, "bytes": 0, "deleted": 0, "total": 0}
        for key, rx in _STATS.items():
            m = rx.search(out)
            if m:
                res[key] = int(m.group(1).replace(",", ""))
        return res

    # --- estado + log -------------------------------------------------------------------
    def load_state(self) -> dict:
        try:
            with self._state_lock:
                return json.loads(self.state_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _update(self, pid: str, fields: dict) -> None:
        with self._state_lock:
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                state = {}
            cur = state.get(pid) or {}
            cur.update(fields)
            state[pid] = cur
            try:
                fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), prefix=".ftp-pub-",
                                           suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(state, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, self.state_path)
            except OSError:
                pass

    def forget(self, pid: str) -> None:
        with self._state_lock:
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8")) or {}
                if pid in state:
                    del state[pid]
                    self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
            except (OSError, ValueError):
                pass

    def log(self, msg: str) -> None:
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg.replace("\n", " | ") + "\n"
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self._trim_log()
        except OSError:
            pass

    def _trim_log(self) -> None:
        try:
            if self.log_path.stat().st_size < 400_000:
                return
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
            if len(lines) > LOG_MAX_LINES:
                self.log_path.write_text("".join(lines[-LOG_KEEP_LINES:]), encoding="utf-8")
        except OSError:
            pass

    def tail_log(self, n: int = 200) -> list[str]:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-n:][::-1]

    # --- visão para a UI ----------------------------------------------------------------
    def describe(self, cfg: dict, rules: list[dict]) -> list[dict]:
        state = self.load_state()
        now = time.time()
        out = []
        for r in rules:
            st = dict(state.get(r["id"]) or {})
            running = r["id"] in self._running
            last_start = float(st.get("last_start") or 0)
            nxt = None
            if r["enabled"] and r["interval_min"] > 0:
                nxt = max(last_start + r["interval_min"] * 60, now) if last_start else now
            cmd, src, dst = self.build_cmd(cfg, r)
            out.append({
                **r,
                "state": st, "running": running,
                "status": "executando" if running else (st.get("status") or "nunca"),
                "last_start_display": ftp.fmt_epoch(last_start) if last_start else "—",
                "next_display": ("—" if nxt is None else
                                 ("agora" if nxt <= now + POLL_SECONDS else ftp.fmt_epoch(nxt))),
                "source_abs": str(src), "dest_abs": str(dst),
                "source_exists": src.is_dir(),
                "cmd_display": " ".join(cmd),
            })
        return out


def ftp_fmt_bytes(v: float | int | None) -> str:
    if not v:
        return "0 B"
    v = float(v)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if v < 1000 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1000
    return f"{v:.1f} TB"


def main() -> int:  # pragma: no cover — utilitário de terminal
    """``python -m app.ftp_publish [--config config.yaml] [id ...]`` — executa as
    publicações UMA vez (todas as habilitadas, ou só os ids dados) e sai. Útil para
    cron, para testar uma regra ou quando a dashboard estiver parada."""
    import argparse
    import sys

    from .config_store import ConfigStore

    ap = argparse.ArgumentParser(description="Executa as publicações FTP uma vez.")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config.yaml"))
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()
    store = ConfigStore(args.config)
    pub = Publisher(store, Path(args.config).parent)
    cfg = store.load()
    rules = ftp.publications(cfg)
    if args.ids:
        rules = [r for r in rules if r["id"] in set(args.ids)]
    else:
        rules = [r for r in rules if r["enabled"]]
    rc = 0
    for r in rules:
        res = pub.run(cfg, r, trigger="terminal")
        print(f"{r['id']}: {res['status']} — {res['files']} arquivo(s), "
              f"{ftp_fmt_bytes(res['bytes'])}" + (f" — {res['message']}" if res["message"] else ""))
        rc = rc or (0 if res["ok"] else 1)
    if not rules:
        print("nenhuma publicação a executar", file=sys.stderr)
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
