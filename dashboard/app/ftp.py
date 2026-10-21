"""Gerenciamento do FTP de DISTRIBUIÇÃO (vsftpd nativo no host) — contas e publicações.

A dashboard é o concentrador dos serviços do hub; este módulo cobre o FTP:

- **Servidor**: lê ``/etc/vsftpd.conf`` (644) e o estado do serviço — só leitura.
- **Contas**: o vsftpd autentica usuários LOCAIS (PAM) e aplica um perfil por usuário
  em ``user_config_dir``. LISTAR dispensa root (``/etc/passwd``, perfis 644,
  ``/etc/ftpusers``); ESCREVER (criar, senha, perfil, pasta, desativar, remover) passa
  pelo script raiz ``ftp/ftp-hub`` via ``sudo -n`` — mesmo modelo do rsync-hub: a
  lógica de contas vive num lugar só e a regra do sudoers é restrita aos subcomandos.
  A senha nunca vai na linha de comando: segue pelo stdin do processo.
- **Publicações**: regras "pasta do hub → subpasta do FTP" executadas por rsync pelo
  próprio processo da dashboard (``ftp_publish``), SEM sudo — a raiz do FTP é gravável
  pelo usuário da dashboard (política do hub). A origem pode ser qualquer pasta da
  BASE (``incoming/<id>/…`` hoje; a saída do tratamento de dados amanhã) — a regra
  não sabe o que é a origem, só copia.

Tudo aqui é chamado apenas por rotas com login de administração (ftp_views).
"""
from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import config_store, hub
from .i18n import gettext as tr

# Nome de conta: vira argumento de comando, nome de arquivo de perfil e usuário do SO.
USER_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
PUB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
SUB_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")

PROFILES = {
    "leitura": {"label": "Somente leitura",
                "desc": "lista e baixa; todo comando de escrita é negado pelo vsftpd"},
    "escrita": {"label": "Leitura e escrita",
                "desc": "baixa, envia, cria pastas, apaga e renomeia (gestão)"},
    "upload": {"label": "Só envio (caixa de entrada)",
               "desc": "envia e cria pastas; NÃO baixa, não apaga nem renomeia"},
}
SUDO_SUBCMDS = ("add", "remove", "senha", "perfil", "pasta", "desativar", "ativar",
                "link", "unlink")
LINKS_FILE = "/etc/ftp-hub/links"
# Espelha FORBIDDEN_SRC do ftp-hub: origens que nunca podem ser vinculadas/copiadas
# para o FTP (raiz, pseudo-fs e pastas com segredos do host).
FORBIDDEN_SOURCES = ("/", "/proc", "/sys", "/dev", "/run", "/etc", "/boot", "/root",
                     "/var/log", "/var/lib", "/usr", "/bin", "/sbin", "/lib", "/lib64",
                     "/snap", "/home")

DEFAULT_VSFTPD_CONF = "/etc/vsftpd.conf"
DEFAULT_USER_CONF_DIR = "/etc/vsftpd/user_conf"
FTPUSERS = "/etc/ftpusers"
VSFTPD_LOG = "/var/log/vsftpd.log"
NOLOGIN_SHELLS = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")
STABLE_BIN = "/usr/local/bin/ftp-hub"
REPO_BIN = Path(__file__).resolve().parents[2] / "ftp" / "ftp-hub"

DEFAULT_FTP_CFG = {
    "root": None,           # None = local_root do vsftpd.conf, senão <BASE>/ftp
    "vsftpd_conf": None,    # None = /etc/vsftpd.conf
    "ftp_hub_bin": None,    # None = /usr/local/bin/ftp-hub → PATH
    "public_host": None,    # host/IP a mostrar nas instruções (None = pasv_address do conf)
    "publications": [],
}


def ftp_cfg(cfg: dict) -> dict:
    raw = cfg.get("ftp") or {}
    out = dict(DEFAULT_FTP_CFG)
    out.update({k: v for k, v in raw.items() if v is not None})
    out["publications"] = list(raw.get("publications") or [])
    return out


# --- vsftpd.conf ---------------------------------------------------------------------
def vsftpd_conf_path(cfg: dict) -> Path:
    return Path(ftp_cfg(cfg).get("vsftpd_conf") or DEFAULT_VSFTPD_CONF)


def read_vsftpd_conf(path: Path) -> dict:
    """``chave=valor`` — a ÚLTIMA ocorrência vale (como no vsftpd). {} se ilegível."""
    conf: dict = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return conf
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#", 1)[0].strip()
        conf[key.strip()] = value
    return conf


def ftp_root(cfg: dict) -> Path:
    """Raiz servida pelo FTP: override do config → ``local_root`` do vsftpd.conf →
    ``<BASE>/ftp`` (convenção do hub)."""
    override = ftp_cfg(cfg).get("root")
    if override:
        p = Path(str(override)).expanduser()
        if not p.is_absolute():
            p = config_store.data_root(cfg) / p
        return p
    conf = read_vsftpd_conf(vsftpd_conf_path(cfg))
    lr = (conf.get("local_root") or "").rstrip("/")
    if lr.startswith("/"):
        return Path(lr)
    return hub.hub_base(cfg) / "ftp"


def user_conf_dir(cfg: dict) -> Path:
    conf = read_vsftpd_conf(vsftpd_conf_path(cfg))
    return Path(conf.get("user_config_dir") or DEFAULT_USER_CONF_DIR)


# --- Executável ftp-hub e sudo -------------------------------------------------------
def ftp_hub_bin(cfg: dict) -> str | None:
    override = ftp_cfg(cfg).get("ftp_hub_bin")
    if override:
        return override if Path(str(override)).is_file() else None
    if Path(STABLE_BIN).is_file():
        return STABLE_BIN
    return shutil.which("ftp-hub")


def _bin_version(path: str | None) -> str | None:
    if not path:
        return None
    try:
        m = re.search(r'FTP_HUB_VERSION="([^"]+)"',
                      Path(path).read_text(encoding="utf-8", errors="replace"))
        return m.group(1) if m else None
    except OSError:
        return None


def sudoers_hint(bin_path: str | None) -> str:
    b = bin_path or STABLE_BIN
    user = hub.dashboard_user()
    return f"{user} ALL=(root) NOPASSWD: " + ", ".join(f"{b} {c} *" for c in SUDO_SUBCMDS)


def _run(cfg: dict, args: list[str], stdin_text: str | None = None,
         timeout: int = 60) -> tuple[bool, str, str | None]:
    """Executa ``ftp-hub <args>`` como root (sudo -n). Retorna (ok, saída, senha
    gerada ou None). A linha ``SENHA=…`` é extraída e NUNCA fica na saída exibida/
    registrada — só vai para quem chamou, que a mostra uma vez."""
    bin_path = ftp_hub_bin(cfg)
    if not bin_path:
        return False, tr("ftp-hub não encontrado neste host. Instale com: "
                         "sudo ./ftp/ftp-hub install <usuario-da-dashboard>"), None
    cmd = [bin_path, *args]
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        cmd = ["sudo", "-n", *cmd]
    io_kw = ({"input": stdin_text + "\n"} if stdin_text is not None
             else {"stdin": subprocess.DEVNULL})
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **io_kw)
    except FileNotFoundError:
        return False, tr("sudo não encontrado — rode a dashboard como root ou instale o sudo."), None
    except subprocess.TimeoutExpired:
        return False, tr("tempo esgotado executando: %(cmd)s", cmd=" ".join(cmd)), None
    password = None
    kept = []
    for line in (r.stdout or "").splitlines():
        if line.startswith("SENHA="):
            password = line[len("SENHA="):]
        else:
            kept.append(line)
    out = "\n".join(kept).strip()
    if r.stderr:
        out = (out + "\n" + r.stderr.strip()).strip()
    if r.returncode != 0:
        if "password is required" in (r.stderr or "") or "senha é necessária" in (r.stderr or ""):
            out = tr("sudo pediu senha (a dashboard só executa em modo não interativo).\n"
                     "Rode no servidor: sudo %(bin)s install %(user)s\n"
                     "(ou libere no visudo: %(hint)s)",
                     bin=REPO_BIN, user=hub.dashboard_user(), hint=sudoers_hint(bin_path))
        return False, out or tr("falha (rc=%(rc)s) executando: %(cmd)s",
                                rc=r.returncode, cmd=" ".join(cmd)), None
    return True, out, password


# --- Estado do servidor ---------------------------------------------------------------
def _service_state() -> str | None:
    if not shutil.which("systemctl"):
        return None
    try:
        r = subprocess.run(["systemctl", "is-active", "vsftpd"], capture_output=True,
                           text=True, timeout=5)
        return (r.stdout or "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _listening(port: int) -> bool | None:
    """Porta em LISTEN? Lê a tabela do kernel (``ss``) — nunca abre conexão com o
    vsftpd, que registraria um CONNECT no log a cada refresh da página."""
    ss = shutil.which("ss") or shutil.which("netstat")
    if not ss:
        return None
    try:
        r = subprocess.run([ss, "-ltn"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(re.search(rf":{port}\s", r.stdout or ""))


def server_info(cfg: dict) -> dict:
    fcfg = ftp_cfg(cfg)
    conf_path = vsftpd_conf_path(cfg)
    conf = read_vsftpd_conf(conf_path)
    root = ftp_root(cfg)
    bin_path = ftp_hub_bin(cfg)
    can_write, sudo_error = hub._probe_sudo(bin_path, ["add", "__sonda__"])
    if not bin_path:
        sudo_error = tr("ftp-hub não instalado neste host.")
    port = int(conf.get("listen_port") or 21)
    disk = None
    try:
        du = shutil.disk_usage(root)
        disk = {"total": du.total, "used": du.used, "free": du.free,
                "pct": round(du.used * 100 / du.total) if du.total else 0}
    except OSError:
        pass
    return {
        "bin": bin_path, "version": _bin_version(bin_path),
        "repo_bin": str(REPO_BIN),
        "can_write": can_write, "sudo_error": sudo_error,
        "user": hub.dashboard_user(), "sudoers_hint": sudoers_hint(bin_path),
        "root": str(root), "root_exists": root.is_dir(),
        "hub_base": str(hub.hub_base(cfg)),
        "root_writable": os.access(root, os.W_OK) if root.is_dir() else False,
        "disk": disk,
        "conf_path": str(conf_path), "conf_readable": bool(conf),
        "user_conf_dir": str(user_conf_dir(cfg)),
        "service": _service_state(),
        "listening": _listening(port), "port": port,
        "tls": (conf.get("ssl_enable") or "NO").upper() == "YES",
        "tls_required": (conf.get("force_local_logins_ssl") or "NO").upper() == "YES",
        "pasv_min": conf.get("pasv_min_port"), "pasv_max": conf.get("pasv_max_port"),
        "pasv_address": conf.get("pasv_address"),
        "public_host": fcfg.get("public_host") or conf.get("pasv_address") or "<ip-do-servidor>",
        "chroot": (conf.get("chroot_local_user") or "NO").upper() == "YES",
        "anonymous": (conf.get("anonymous_enable") or "NO").upper() == "YES",
        "rsync": shutil.which("rsync"),
    }


# --- Contas (leitura direta) -----------------------------------------------------------
def valid_user(name: str) -> bool:
    return bool(USER_RE.match(name or ""))


def _disabled_users() -> set[str]:
    try:
        return {ln.strip() for ln in Path(FTPUSERS).read_text(encoding="utf-8",
                                                               errors="replace").splitlines()
                if ln.strip() and not ln.strip().startswith("#")}
    except OSError:
        return set()


def _profile_from_conf(text: str | None, global_write: bool) -> tuple[str, bool]:
    """(perfil, é_gerenciado). Sem a linha ``# ftp-hub:`` o perfil é INFERIDO das
    diretivas — contas criadas à mão antes do ftp-hub."""
    if text is None:
        return ("escrita" if global_write else "leitura"), False
    m = re.search(r"^# ftp-hub: .*perfil=(\w+)", text, re.M)
    if m and m.group(1) in PROFILES:
        return m.group(1), True
    if re.search(r"^write_enable=NO", text, re.M):
        return "leitura", False
    if re.search(r"^cmds_denied=.*RETR", text, re.M):
        return "upload", False
    return "escrita", False


def list_accounts(cfg: dict) -> list[dict]:
    """Contas FTP = usuários locais com uid ≥ 1000, shell sem login e (perfil em
    user_conf OU home sob a raiz do FTP / /srv/ftp) — o MESMO critério do
    ``ftp-hub`` (is_ftp_account), para a tela e o script concordarem."""
    root = str(ftp_root(cfg)).rstrip("/")
    ucd = user_conf_dir(cfg)
    conf = read_vsftpd_conf(vsftpd_conf_path(cfg))
    global_write = (conf.get("write_enable") or "NO").upper() == "YES"
    disabled = _disabled_users()
    hub_user = (hub.read_hub_conf().get("HUB_USER") or "carbonara").strip()
    reserved = {"root", "ftp", "nobody", hub_user, hub.dashboard_user()}
    activity = recent_activity()
    out = []
    try:
        entries = pwd.getpwall()
    except Exception:  # noqa: BLE001 — sem pwd (não-POSIX)
        entries = []
    for pw in entries:
        if pw.pw_uid < 1000 or pw.pw_name in reserved:
            continue
        if pw.pw_shell not in NOLOGIN_SHELLS:
            continue
        conf_file = ucd / pw.pw_name
        text = None
        try:
            if conf_file.is_file():
                text = conf_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
        home = pw.pw_dir.rstrip("/")
        under_root = home == root or home.startswith(root + "/")
        under_srv = home == "/srv/ftp" or home.startswith("/srv/ftp/")
        if text is None and not (under_root or under_srv):
            continue
        profile, managed = _profile_from_conf(text, global_write)
        local_root = home
        if text:
            m = re.search(r"^local_root=(.+)$", text, re.M)
            if m:
                local_root = m.group(1).strip().rstrip("/")
        if local_root == root:
            folder = "/"
        elif local_root.startswith(root + "/"):
            folder = local_root[len(root) + 1:]
        else:
            folder = local_root  # fora da raiz (conta antiga) — mostrado tal qual
        out.append({
            "user": pw.pw_name, "uid": pw.pw_uid, "home": pw.pw_dir,
            "profile": profile, "profile_label": PROFILES.get(profile, {}).get("label", profile),
            "folder": folder, "folder_abs": local_root,
            "folder_outside": not (local_root == root or local_root.startswith(root + "/")),
            "disabled": pw.pw_name in disabled, "managed": managed,
            "has_conf": text is not None,
            "activity": activity.get(pw.pw_name),
        })
    out.sort(key=lambda a: a["user"])
    return out


def get_account(cfg: dict, user: str) -> dict | None:
    if not valid_user(user):
        return None
    return next((a for a in list_accounts(cfg) if a["user"] == user), None)


# --- Atividade recente (vsftpd.log) -----------------------------------------------------
_ACT_CACHE: dict = {"at": 0.0, "data": {}}
_ACT_TTL = 30
_ACT_TAIL_BYTES = 512 * 1024
_LOG_RE = re.compile(
    r"^(?P<ts>\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4}) \[pid \d+\] \[(?P<user>[^\]]+)\] "
    r"(?P<res>OK|FAIL) (?P<op>LOGIN|UPLOAD|DOWNLOAD|DELETE|MKDIR|RMDIR|RENAME): "
    r'Client "(?P<ip>[^"]+)"')


def recent_activity() -> dict:
    """Último login / contagens por usuário a partir da CAUDA do vsftpd.log (root:adm
    640 — legível quando o usuário da dashboard está no grupo adm; senão {}). Cache
    de 30 s; lê só os últimos 512 kB, o log não é rotacionado com frequência."""
    now = time.time()
    if now - _ACT_CACHE["at"] < _ACT_TTL:
        return _ACT_CACHE["data"]
    data: dict = {}
    try:
        p = Path(VSFTPD_LOG)
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > _ACT_TAIL_BYTES:
                fh.seek(size - _ACT_TAIL_BYTES)
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        _ACT_CACHE.update(at=now, data=data)
        return data
    for line in chunk.splitlines():
        m = _LOG_RE.match(line)
        if not m:
            continue
        u = m.group("user")
        rec = data.setdefault(u, {"last_login": None, "last_login_epoch": 0, "last_ip": None,
                                  "uploads": 0, "downloads": 0, "failed_logins": 0})
        op, res = m.group("op"), m.group("res")
        ip = m.group("ip").replace("::ffff:", "")
        if op == "LOGIN":
            if res == "OK":
                try:
                    dt = datetime.strptime(m.group("ts"), "%a %b %d %H:%M:%S %Y")
                    rec["last_login_epoch"] = dt.timestamp()
                    rec["last_login"] = dt.strftime("%d/%m/%Y %H:%M")
                except ValueError:
                    rec["last_login"] = m.group("ts")
                rec["last_ip"] = ip
            else:
                rec["failed_logins"] += 1
        elif op == "UPLOAD" and res == "OK":
            rec["uploads"] += 1
        elif op == "DOWNLOAD" and res == "OK":
            rec["downloads"] += 1
    _ACT_CACHE.update(at=now, data=data)
    return data


# --- Contas (escrita via ftp-hub) -------------------------------------------------------
def normalize_subfolder(raw: str) -> tuple[str | None, str]:
    """Subpasta relativa à raiz do FTP: '' = raiz. Componentes ``[A-Za-z0-9_][A-Za-z0-9._-]*``,
    sem ``..`` — mesma regra do ftp-hub (norm_sub)."""
    s = (raw or "").strip().strip("/")
    s = re.sub(r"/+", "/", s)
    if not s:
        return "", ""
    for part in s.split("/"):
        if part in (".", "..") or not SUB_COMPONENT_RE.match(part):
            return None, tr("subpasta inválida: use letras, dígitos, '.', '_' e '-' em cada "
                            "componente (sem '..', sem componente começando por '.').")
    return s, ""


def validate_password(pw: str) -> str:
    if len(pw) < 8:
        return tr("a senha precisa ter pelo menos 8 caracteres.")
    if len(pw) > 128:
        return tr("senha longa demais (máx. 128).")
    if ":" in pw or "\n" in pw or "\r" in pw:
        return tr("a senha não pode conter ':' nem quebras de linha.")
    return ""


def add_account(cfg: dict, user: str, profile: str, sub: str,
                password: str | None) -> tuple[bool, str, str | None]:
    if not valid_user(user):
        return False, tr("nome inválido: minúsculas, dígitos, '_' e '-', 3–32 caracteres, "
                         "começando por letra."), None
    if profile not in PROFILES:
        return False, tr("perfil inválido."), None
    norm, err = normalize_subfolder(sub)
    if norm is None:
        return False, err, None
    pw = (password or "").strip()
    if pw:
        err = validate_password(pw)
        if err:
            return False, err, None
    args = ["add", user, "--perfil", profile]
    if norm:
        args += ["--pasta", norm]
    args.append("--senha-stdin" if pw else "--gerar")
    return _run(cfg, args, stdin_text=pw if pw else None)


def set_password(cfg: dict, user: str, password: str | None) -> tuple[bool, str, str | None]:
    if not valid_user(user):
        return False, tr("nome inválido."), None
    pw = (password or "").strip()
    if pw:
        err = validate_password(pw)
        if err:
            return False, err, None
        return _run(cfg, ["senha", user, "--senha-stdin"], stdin_text=pw)
    return _run(cfg, ["senha", user, "--gerar"])


def set_profile(cfg: dict, user: str, profile: str) -> tuple[bool, str]:
    if not valid_user(user) or profile not in PROFILES:
        return False, tr("usuário ou perfil inválido.")
    ok, out, _ = _run(cfg, ["perfil", user, profile])
    return ok, out


def set_folder(cfg: dict, user: str, sub: str) -> tuple[bool, str]:
    if not valid_user(user):
        return False, tr("nome inválido.")
    norm, err = normalize_subfolder(sub)
    if norm is None:
        return False, err
    ok, out, _ = _run(cfg, ["pasta", user, norm or "/"])
    return ok, out


def set_enabled(cfg: dict, user: str, enabled: bool) -> tuple[bool, str]:
    if not valid_user(user):
        return False, tr("nome inválido.")
    ok, out, _ = _run(cfg, ["ativar" if enabled else "desativar", user])
    return ok, out


def remove_account(cfg: dict, user: str) -> tuple[bool, str]:
    """Remove SÓ a conta (userdel sem -r + perfil): os arquivos ficam — é o ftp-hub que
    garante isso, e é ele que recusa nomes que não sejam contas FTP."""
    if not valid_user(user):
        return False, tr("nome inválido.")
    ok, out, _ = _run(cfg, ["remove", user])
    return ok, out


# --- Publicações (regras de cópia hub → FTP) ---------------------------------------------
def publications(cfg: dict) -> list[dict]:
    out = []
    for raw in ftp_cfg(cfg)["publications"]:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        out.append(_normalize_rule(raw))
    return out


def _normalize_rule(raw: dict) -> dict:
    def as_list(v):
        if isinstance(v, str):
            return _split_patterns(v)
        return [str(x) for x in (v or []) if str(x).strip()]
    return {
        "id": str(raw.get("id")),
        "name": str(raw.get("name") or raw.get("id")),
        "source": str(raw.get("source") or "").rstrip("/") or "",
        "dest": str(raw.get("dest") or "").strip("/"),
        "include": as_list(raw.get("include")),
        "exclude": as_list(raw.get("exclude")),
        "delete": bool(raw.get("delete", False)),
        "hidden": bool(raw.get("hidden", False)),
        "interval_min": int(raw.get("interval_min") or 0),
        "enabled": bool(raw.get("enabled", True)),
    }


def get_publication(cfg: dict, pid: str) -> dict | None:
    if not PUB_ID_RE.match(pid or ""):
        return None
    return next((p for p in publications(cfg) if p["id"] == pid), None)


def _split_patterns(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n\r]+", raw or "") if p.strip()]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "publicacao")[:40]


def resolve_source(cfg: dict, source: str) -> Path:
    """Origem: absoluta é usada tal qual; relativa é sob a BASE do hub."""
    p = Path(source).expanduser()
    return p if p.is_absolute() else hub.hub_base(cfg) / p


def dest_path(cfg: dict, dest: str) -> Path:
    return ftp_root(cfg) / dest if dest else ftp_root(cfg)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_publication(form: dict, cfg: dict, current_id: str | None = None) -> tuple[dict, list[str]]:
    errors: list[str] = []
    name = " ".join((form.get("name") or "").split())[:80]
    if not name:
        errors.append(tr("Dê um nome à publicação."))
    pid = current_id or slugify(name)
    if not current_id:
        existing = {p["id"] for p in publications(cfg)}
        base, n = pid, 2
        while pid in existing:
            pid = f"{base}-{n}"
            n += 1

    source = (form.get("source") or "").strip().rstrip("/")
    src_abs = None
    if not source:
        errors.append(tr("Informe a pasta de origem (ex.: incoming/km67)."))
    elif any(c in source for c in "\n\r\0"):
        errors.append(tr("Origem inválida."))
    else:
        src_abs = resolve_source(cfg, source)
        try:
            src_res = src_abs.resolve()
        except OSError:
            src_res = src_abs
        if not src_abs.is_dir():
            errors.append(tr("A origem não existe ou não é uma pasta: %(p)s", p=src_abs))
        elif source_forbidden(src_abs):
            errors.append(tr("Origem recusada:") + " " + source_forbidden(src_abs))
        elif _is_within(src_res, ftp_root(cfg).resolve()):
            errors.append(tr("A origem não pode estar DENTRO da raiz do FTP (copiaria o FTP sobre ele mesmo)."))

    dest_norm, err = normalize_subfolder(form.get("dest") or "")
    if dest_norm is None:
        errors.append(tr("Destino:") + " " + err)
    elif not dest_norm:
        errors.append(tr("Informe uma SUBPASTA de destino dentro do FTP (a raiz inteira não é permitida como destino)."))
    elif src_abs is not None:
        d_abs = dest_path(cfg, dest_norm)
        try:
            d_res = d_abs.resolve()
            s_res = src_abs.resolve()
        except OSError:
            d_res, s_res = d_abs, src_abs
        if d_res == s_res or _is_within(d_res, s_res) or _is_within(s_res, d_res):
            errors.append(tr("Origem e destino se sobrepõem — a cópia entraria em laço."))

    include = _split_patterns(form.get("include") or "")
    exclude = _split_patterns(form.get("exclude") or "")
    for pat in include + exclude:
        if len(pat) > 120 or re.search(r"[\s\0]", pat):
            errors.append(tr("Filtro inválido: '%(pat)s' (sem espaços; separe por vírgula).", pat=pat))
            break

    raw_int = (form.get("interval_min") or "").strip()
    interval = 60
    if raw_int:
        try:
            interval = int(raw_int)
            if not (0 <= interval <= 10080):
                raise ValueError
        except ValueError:
            errors.append(tr("Intervalo: minutos entre 0 (só manual) e 10080 (7 dias)."))

    rule = {
        "id": pid, "name": name, "source": source, "dest": dest_norm or "",
        "include": include, "exclude": exclude,
        "delete": bool(form.get("delete")), "hidden": bool(form.get("hidden")),
        "interval_min": interval, "enabled": bool(form.get("enabled")),
    }
    return rule, errors


def save_publication(store, cfg: dict, rule: dict) -> None:
    fcfg = cfg.setdefault("ftp", {}) or {}
    cfg["ftp"] = fcfg
    pubs = [p for p in (fcfg.get("publications") or []) if isinstance(p, dict)]
    for i, p in enumerate(pubs):
        if p.get("id") == rule["id"]:
            pubs[i] = rule
            break
    else:
        pubs.append(rule)
    fcfg["publications"] = pubs
    store.save(cfg)


def delete_publication(store, cfg: dict, pid: str) -> bool:
    fcfg = cfg.get("ftp") or {}
    pubs = [p for p in (fcfg.get("publications") or []) if isinstance(p, dict)]
    kept = [p for p in pubs if p.get("id") != pid]
    if len(kept) == len(pubs):
        return False
    fcfg["publications"] = kept
    cfg["ftp"] = fcfg
    store.save(cfg)
    return True


# --- Navegação para os seletores de pasta -----------------------------------------------
PICK_AREAS = {
    "incoming": "Recebidos (incoming)",
    "archive": "Arquivados (archive)",
    "quarantine": "Quarentena",
    "sistema": "Sistema de arquivos (/)",
}


def browse_dirs(cfg: dict, area: str, sub: str) -> dict | None:
    """Subpastas de um nível (sem recursão) de uma área do hub, da raiz do FTP ou do
    sistema de arquivos inteiro (``sistema`` — a origem pode ser qualquer pasta do
    servidor) — alimenta os seletores de origem/destino."""
    if area == "ftp":
        root, label = ftp_root(cfg), "FTP"
    elif area == "sistema":
        root, label = Path("/"), "/"
    elif area in PICK_AREAS:
        root, label = hub.hub_base(cfg) / area, PICK_AREAS[area]
    else:
        return None
    if area == "sistema":
        rel_norm, err = _normalize_system_rel(sub)
    else:
        rel_norm, err = normalize_subfolder(sub)
    if rel_norm is None:
        return None
    path = (root / rel_norm) if rel_norm else root
    try:
        root_res = root.resolve()
        if not _is_within(path.resolve(), root_res):
            return None
    except OSError:
        return None
    base = {"area": area, "label": label, "root": str(root), "rel": rel_norm,
            "dirs": [], "files": 0, "missing": False, "denied": False,
            "value": _pick_value(area, rel_norm)}
    if not path.is_dir():
        return {**base, "missing": True}
    dirs, files = [], 0
    try:
        with os.scandir(path) as it:
            for i, e in enumerate(it):
                if i > hub.MAX_LIST_ENTRIES:
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e.name)
                    elif e.is_file(follow_symlinks=False) and not e.name.startswith("."):
                        files += 1
                except OSError:
                    continue
    except PermissionError:
        return {**base, "denied": True}
    except OSError:
        return {**base, "missing": True}
    if area == "sistema" and not rel_norm:
        # Na raiz do sistema, esconde o que nunca pode ser origem (pseudo-fs, segredos).
        dirs = [d for d in dirs if "/" + d not in FORBIDDEN_SOURCES]
    dirs.sort()
    return {**base, "dirs": dirs, "files": files}


def _normalize_system_rel(sub: str) -> tuple[str | None, str]:
    """Caminho relativo à raiz do sistema para o seletor: aceita qualquer nome de
    pasta (o sistema tem nomes com espaço, acento…), mas nunca ``..`` nem NUL."""
    s = (sub or "").strip().strip("/")
    s = re.sub(r"/+", "/", s)
    if not s:
        return "", ""
    for part in s.split("/"):
        if part in (".", "..") or "\0" in part or "\n" in part:
            return None, tr("caminho inválido.")
    return s, ""


def _pick_value(area: str, rel: str) -> str:
    """O que vai para o campo do formulário: origem relativa à BASE (``incoming/x``),
    caminho absoluto para o sistema (``/mnt/dados/x``), destino relativo à raiz do
    FTP (``dados/x``)."""
    if area == "ftp":
        return rel
    if area == "sistema":
        return "/" + rel if rel else "/"
    return f"{area}/{rel}" if rel else area


def source_forbidden(path: Path) -> str:
    """Motivo pelo qual a origem é recusada (''= aceitável). Vale para cópia E vínculo."""
    # Testa o caminho LITERAL e o RESOLVIDO: /etc pode ser symlink (macOS → /private/etc)
    # e um symlink dentro de uma pasta permitida pode apontar para uma proibida.
    candidates = {str(path)}
    try:
        candidates.add(str(path.resolve()))
    except OSError:
        pass
    for s in candidates:
        if s == "/":
            return tr("a origem não pode ser a raiz do sistema.")
        for f in FORBIDDEN_SOURCES:
            if f != "/" and (s == f or s.startswith(f + "/")):
                return tr("a origem não pode estar em %(f)s (pastas do sistema/segredos do host "
                          "nunca vão para o FTP).", f=f)
    return ""


# --- Vínculos (bind mounts) ---------------------------------------------------------------
def _mountpoints() -> set[str]:
    pts: set[str] = set()
    try:
        for line in Path("/proc/self/mounts").read_text(encoding="utf-8",
                                                          errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                pts.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return pts


def links(cfg: dict) -> list[dict]:
    """Vínculos registrados em /etc/ftp-hub/links (644) + estado real de montagem
    (/proc/self/mounts). Montagens sob a raiz do FTP que NÃO estão no registro (ex.:
    um bind do /etc/fstab feito à mão) aparecem como ``manual``."""
    root = ftp_root(cfg)
    root_s = str(root).rstrip("/")
    mounted = _mountpoints()
    out, seen = [], set()
    try:
        text = Path(LINKS_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        sub, src = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 and parts[2] else "ro"
        dest = str(root / sub)
        seen.add(dest)
        out.append({"dest": sub, "dest_abs": dest, "source": src, "mode": mode,
                    "mounted": dest in mounted, "source_exists": Path(src).is_dir(),
                    "manual": False})
    for mp in sorted(mounted):
        if mp.startswith(root_s + "/") and mp not in seen:
            out.append({"dest": mp[len(root_s) + 1:], "dest_abs": mp, "source": "(fstab/manual)",
                        "mode": "?", "mounted": True, "source_exists": True, "manual": True})
    out.sort(key=lambda l: l["dest"])
    return out


def add_link(cfg: dict, dest: str, source: str, rw: bool = False) -> tuple[bool, str]:
    """``ftp-hub link <sub> <origem> [--rw]`` — bind mount da origem dentro do FTP."""
    norm, err = normalize_subfolder(dest)
    if norm is None:
        return False, tr("Destino:") + " " + err
    if not norm:
        return False, tr("informe uma SUBPASTA do FTP como destino do vínculo.")
    src = (source or "").strip().rstrip("/")
    if any(c in src for c in "\n\r\t\0") or not src:
        return False, tr("informe a pasta de origem.")
    if not src.startswith("/"):
        # Valor vindo do seletor das áreas do hub (``incoming/km67``): vira absoluto
        # sob a BASE — o ftp-hub só aceita caminho absoluto.
        src = str(hub.hub_base(cfg) / src)
    p = Path(src)
    if not p.is_dir():
        return False, tr("a origem não existe ou não é uma pasta: %(p)s", p=src)
    why = source_forbidden(p)
    if why:
        return False, why
    if _is_within(p.resolve(), ftp_root(cfg).resolve()):
        return False, tr("a origem já está dentro do FTP — organize com uma pasta ou publique noutra subpasta.")
    args = ["link", norm, src] + (["--rw"] if rw else [])
    ok, out, _ = _run(cfg, args, timeout=60)
    return ok, out


def remove_link(cfg: dict, dest: str) -> tuple[bool, str]:
    norm, err = normalize_subfolder(dest)
    if norm is None or not norm:
        return False, tr("vínculo inválido.")
    ok, out, _ = _run(cfg, ["unlink", norm], timeout=60)
    return ok, out


# --- Pastas do FTP (organização) — escrita direta, sem sudo -----------------------------------
def folder_listing(cfg: dict, sub: str) -> dict | None:
    """Um nível da raiz do FTP para o gerenciador de pastas: subpastas com contagem,
    se estão vazias e se são pontos de montagem (vínculos), + arquivos soltos."""
    norm, err = normalize_subfolder(sub)
    if norm is None:
        return None
    root = ftp_root(cfg)
    path = root / norm if norm else root
    try:
        if not _is_within(path.resolve(), root.resolve()) or not path.is_dir():
            return None
    except OSError:
        return None
    mounted = _mountpoints()
    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        rel = f"{norm}/{e.name}" if norm else e.name
                        full = str(path / e.name)
                        try:
                            empty = not any(os.scandir(full))
                        except OSError:
                            empty = False
                        dirs.append({"name": e.name, "rel": rel, "empty": empty,
                                     "mounted": full in mounted,
                                     "writable": os.access(full, os.W_OK)})
                    elif e.is_file(follow_symlinks=False):
                        files.append(e.name)
                except OSError:
                    continue
    except OSError:
        return None
    dirs.sort(key=lambda d: d["name"])
    crumbs, acc = [], []
    for part in norm.split("/") if norm else []:
        acc.append(part)
        crumbs.append({"name": part, "rel": "/".join(acc)})
    return {"rel": norm, "abs": str(path), "dirs": dirs, "files": sorted(files),
            "crumbs": crumbs, "writable": os.access(path, os.W_OK),
            "parent": "/".join(norm.split("/")[:-1]) if norm else None}


def create_folder(cfg: dict, parent: str, name: str) -> tuple[bool, str, str]:
    """Cria ``<raiz>/<parent>/<name>`` (2775). Retorna (ok, msg, rel_da_nova)."""
    pnorm, err = normalize_subfolder(parent)
    if pnorm is None:
        return False, tr("Pasta-mãe:") + " " + err, ""
    nnorm, err = normalize_subfolder(name)
    if nnorm is None or not nnorm or "/" in nnorm:
        return False, tr("Nome inválido: use letras, dígitos, '.', '_' e '-' (sem barras)."), ""
    rel = f"{pnorm}/{nnorm}" if pnorm else nnorm
    root = ftp_root(cfg)
    target = root / rel
    try:
        if not _is_within(target.parent.resolve(), root.resolve()):
            return False, tr("fora da raiz do FTP."), ""
        if target.exists():
            return False, tr("já existe: ftp:/%(rel)s", rel=rel), rel
        target.mkdir(mode=0o2775)
        os.chmod(target, 0o2775)
    except PermissionError:
        return False, tr("sem permissão para criar em %(p)s (a pasta-mãe não é "
                         "gravável pelo usuário da dashboard).", p=target.parent), ""
    except OSError as exc:
        return False, tr("falha ao criar a pasta: %(e)s", e=exc), ""
    return True, tr("Pasta ftp:/%(rel)s criada.", rel=rel), rel


def remove_folder(cfg: dict, sub: str) -> tuple[bool, str]:
    """Remove SÓ uma pasta VAZIA (rmdir) que não seja ponto de montagem — a dashboard
    nunca apaga dados do FTP."""
    norm, err = normalize_subfolder(sub)
    if norm is None or not norm:
        return False, tr("pasta inválida.")
    root = ftp_root(cfg)
    target = root / norm
    try:
        if not _is_within(target.resolve(), root.resolve()) or target.resolve() == root.resolve():
            return False, tr("fora da raiz do FTP.")
        if str(target) in _mountpoints():
            return False, tr("esta pasta é um vínculo — desfaça o vínculo em vez de removê-la.")
        if not target.is_dir():
            return False, tr("não é uma pasta.")
        target.rmdir()
    except OSError as exc:
        if getattr(exc, "errno", None) in (39, 66):  # ENOTEMPTY
            return False, tr("ftp:/%(p)s não está vazia — a dashboard só remove pastas vazias.", p=norm)
        return False, tr("não consegui remover: %(e)s", e=exc)
    return True, tr("Pasta vazia ftp:/%(p)s removida.", p=norm)


def fmt_epoch(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
