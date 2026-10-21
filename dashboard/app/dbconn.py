"""Onde está o banco: leitura/escrita do ``.env`` do compose e aplicação pelos serviços.

A dashboard não fala com o PostgreSQL; quem fala são os containers ``api`` e ``ingestion``,
configurados pelo ``.env`` na raiz do repositório. Esta página edita SÓ as chaves de
conexão desse arquivo (preservando o resto) e, a pedido, roda ``docker compose up -d``
para os serviços relerem — o usuário da dashboard está no grupo ``docker`` (o
``setup-db-host.sh`` cuida disso), então não precisa de sudo.

Dois modos:
- **local**: ``DB_HOST=db`` + ``COMPOSE_PROFILES=local-db`` (o banco é o serviço ``db``
  deste compose, no disco de dados);
- **externo**: ``DB_HOST=<ip/nome>``, ``DB_INTERNAL_PORT=<porta>`` e ``COMPOSE_PROFILES=``
  vazio (o ``db`` local não sobe; o banco vive em outro servidor — o caso da migração).
A senha nunca é exibida; em branco, mantém a atual.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .i18n import gettext as _

CONN_KEYS = ("COMPOSE_PROFILES", "DB_HOST", "DB_INTERNAL_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
             "DB_BIND", "DB_PORT")
_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def env_path() -> Path:
    return repo_root() / ".env"


def read_env() -> dict:
    """Chaves do .env (última ocorrência vence, como no compose)."""
    out: dict = {}
    p = env_path()
    if not p.is_file():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(ln)
        if m and not ln.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def write_env(updates: dict) -> None:
    """Substitui/acrescenta só as chaves informadas; o resto do arquivo fica intacto.
    Escrita atômica, preservando o modo (600)."""
    p = env_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    def fmt(v) -> str:
        v = "" if v is None else str(v)
        if re.fullmatch(r"[A-Za-z0-9_./:@,+=-]*", v):
            return v
        # compose lê .env com aspas duplas e escapes: senha com $, #, espaço ou aspas
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'
    pending = dict(updates)
    out = []
    for ln in lines:
        m = _LINE.match(ln)
        if m and not ln.lstrip().startswith("#") and m.group(1) in pending:
            out.append(f"{m.group(1)}={fmt(pending.pop(m.group(1)))}")
        else:
            out.append(ln)
    for k, v in pending.items():
        out.append(f"{k}={fmt(v)}")
    mode = p.stat().st_mode & 0o777 if p.is_file() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        os.unlink(tmp)
        raise


def current() -> dict:
    env = read_env()
    local = (env.get("DB_HOST") or "db") == "db" and "local-db" in (env.get("COMPOSE_PROFILES") or "local-db")
    return {
        "mode": "local" if local else "externo",
        "host": env.get("DB_HOST") or "db",
        "port": env.get("DB_INTERNAL_PORT") or "5432",
        "name": env.get("DB_NAME") or "carbonara",
        "user": env.get("DB_USER") or "carbonara",
        "has_password": bool(env.get("DB_PASSWORD")),
        "bind": env.get("DB_BIND") or "127.0.0.1",
        "host_port": env.get("DB_PORT") or "5432",
        "pg_data_dir": env.get("PG_DATA_DIR"),
        "env_path": str(env_path()),
        "env_writable": os.access(env_path(), os.W_OK) if env_path().is_file() else os.access(env_path().parent, os.W_OK),
    }


def docker_ok() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, _("docker não encontrado no PATH da dashboard")
    try:
        r = subprocess.run(["docker", "compose", "version"], cwd=str(repo_root()), capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, _("docker compose indisponível: %(erro)s", erro=exc)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:300] or _("docker compose falhou")
    return True, r.stdout.strip()


def _compose(args: list[str], timeout: int = 300, extra_env: dict | None = None) -> tuple[bool, str]:
    try:
        r = subprocess.run(["docker", "compose", *args], cwd=str(repo_root()), capture_output=True,
                           text=True, timeout=timeout, env={**os.environ, **(extra_env or {})})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    txt = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    txt = "\n".join(ln for ln in txt.splitlines() if not ln.startswith("time=") and ln.strip())
    return r.returncode == 0, txt[-3000:]


def services_status() -> tuple[bool, str]:
    return _compose(["ps", "--format", "table {{.Name}}\t{{.Status}}"], timeout=60)


def apply(mode: str) -> tuple[bool, str]:
    """Faz os serviços relerem o .env: sobe api + ingestion (recriados quando o ambiente
    mudou) e, no modo local, o db. Sem --remove-orphans: um db local que ficou para
    trás quando se muda para externo NÃO é destruído (parar é outra ação explícita)."""
    svcs = ["api", "ingestion"] + (["db"] if mode == "local" else [])
    return _compose(["up", "-d", *svcs], timeout=600)


def stop_local_db() -> tuple[bool, str]:
    return _compose(["--profile", "local-db", "stop", "db"], timeout=120)


def test_connection(host: str, port: str, name: str, user: str, password: str | None) -> tuple[bool, str]:
    """Testa a conexão COM os parâmetros informados, de dentro da imagem da API (que já
    tem o driver) — antes de gravar nada. Senha vazia = a do .env."""
    env = read_env()
    pw = password or env.get("DB_PASSWORD") or ""
    code = ("import asyncio, asyncpg, os\n"
            "async def m():\n"
            "    c = await asyncpg.connect(host=os.environ['H'], port=int(os.environ['P']), database=os.environ['D'],"
            " user=os.environ['U'], password=os.environ['W'], timeout=10)\n"
            "    v = await c.fetchval(\"select version()\"); t = await c.fetchval(\"select extversion from pg_extension where extname='timescaledb'\")\n"
            "    n = await c.fetchval(\"select count(*) from information_schema.tables where table_name='measurements'\")\n"
            "    print('OK:', v.split(',')[0], '| timescaledb', t, '| measurements:', 'sim' if n else 'NÃO (schema ausente)')\n"
            "    await c.close()\n"
            "asyncio.run(m())")
    # a senha vai pelo AMBIENTE (-e W sem valor = herda), nunca na linha de comando (ps)
    args = ["run", "--rm", "--no-deps", "-T", "-e", f"H={host}", "-e", f"P={port}", "-e", f"D={name}",
            "-e", f"U={user}", "-e", "W", "api", "python", "-c", code]
    ok, out = _compose(args, timeout=90, extra_env={"W": pw})
    if ok and "OK:" in out:
        return True, next((ln for ln in out.splitlines() if ln.startswith("OK:")), out)
    # falha: só a última linha útil (a exceção), não o traceback inteiro
    linhas = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith(("Traceback", "File ", "  "))]
    return False, (linhas[-1] if linhas else out)[-300:]
