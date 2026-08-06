"""Integração de GERENCIAMENTO com o rsync-hub — a parte de escrita da dashboard.

A dashboard sempre foi complementar ao rsync-hub (lê a lista de estações, o log e
os dados). Este módulo fecha o ciclo: permite ADMINISTRAR a coleta pela interface —
autorizar/revogar estações, ver e baixar credenciais, navegar e baixar arquivos.

Modelo de privilégios (espelha a política do hub, docs em collection/rsync-hub):
- LEITURA dispensa sudo — o hub mantém credenciais, authorized_keys, rsyncd.secrets
  e dados legíveis pelos usuários locais do host (política deliberada).
- ESCRITA (add/remove) exige root: em vez de reimplementar a lógica de chaves aqui,
  executamos o PRÓPRIO ``rsync-hub`` via ``sudo -n`` (não interativo). Assim a regra
  continua num lugar só; a dashboard é apenas um invólucro. Sem regra no sudoers o
  recurso degrada com instrução clara (SUDOERS_HINT), nunca com um prompt pendurado.

Tudo aqui é chamado apenas por rotas com login de administração (hub_views).
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import config_store

# Mesma validação do rsync-hub (valid_id) — o id vira argumento de comando e caminho.
HUB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,30}$")

# Áreas navegáveis do hub: slug da URL -> (subpasta sob a BASE, rótulo de exibição).
AREAS = {
    "incoming": ("incoming", "Recebidos (incoming)"),
    "archive": ("archive", "Arquivados (archive)"),
    "quarantine": ("quarantine", "Quarentena"),
    "stations": ("stations", "Credenciais (stations)"),
}

MAX_LIST_ENTRIES = 2000  # teto de entradas por listagem (pastas gigantes de archive)


def sudoers_hint(bin_path: str | None) -> str:
    """Regra sudoers que o gerenciamento precisa (a mesma que o script
    dashboard/setup-hub-access.sh instala em /etc/sudoers.d/)."""
    b = bin_path or "/usr/local/bin/rsync-hub"
    user = os.environ.get("USER") or "usuario-da-dashboard"
    return f"{user} ALL=(root) NOPASSWD: {b} add *, {b} remove *, {b} purge *, {b} move-root *"


def hub_base(cfg: dict) -> Path:
    """BASE do hub para leitura: o primeiro candidato que contenha ``incoming/``
    (tolera raiz apontada para .../incoming), senão a própria raiz configurada."""
    candidates = config_store.hub_base_candidates(cfg)
    for base in candidates:
        if (base / "incoming").is_dir():
            return base
    return candidates[0]


def rsync_hub_bin(cfg: dict) -> str | None:
    """Caminho do executável rsync-hub. Ordem: override no config → cópia estável de
    /usr/local/bin (a que o install/add mantém) → PATH. None = não instalado."""
    override = (cfg.get("rsync_hub_bin") or "").strip() if cfg.get("rsync_hub_bin") else ""
    if override:
        return override if Path(override).is_file() else None
    stable = "/usr/local/bin/rsync-hub"
    if Path(stable).is_file():
        return stable
    return shutil.which("rsync-hub")


def read_hub_conf() -> dict:
    """Lê /etc/rsync-hub.conf (formato shell ``CHAVE="valor"``) — é a config que o
    rsync-hub instalado realmente usa, persistida pelo ``install``. Só exibição."""
    conf: dict = {}
    try:
        text = Path("/etc/rsync-hub.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return conf
    for m in re.finditer(r'^\s*(BASE|HUB_USER|RRSYNC)\s*=\s*"?([^"\n]*)"?\s*$', text, re.M):
        conf[m.group(1)] = m.group(2).strip()
    return conf


def hub_info(cfg: dict) -> dict:
    """Painel de contexto do gerenciamento: binário, versão, BASE efetiva e se as
    operações de escrita estão disponíveis (root ou sudo sem senha)."""
    bin_path = rsync_hub_bin(cfg)
    version = None
    if bin_path:
        try:
            m = re.search(r'RSYNC_HUB_VERSION="([^"]+)"',
                          Path(bin_path).read_text(encoding="utf-8", errors="replace"))
            version = m.group(1) if m else None
        except OSError:
            pass
    conf = read_hub_conf()
    base = hub_base(cfg)
    conf_base = conf.get("BASE")
    return {
        "bin": bin_path,
        "version": version,
        "base": str(base),
        "conf_base": conf_base,
        # BASE divergente = a dashboard lê de um lugar e o rsync-hub escreve em outro
        # (ex.: dev com ./data). O template avisa; as escritas usam a BASE do hub.
        "base_mismatch": bool(conf_base) and str(base) != conf_base,
        "can_write": _can_write(bin_path),
        "sudoers_hint": sudoers_hint(bin_path),
    }


def _can_write(bin_path: str | None) -> bool:
    if not bin_path:
        return False
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    # ``sudo -n -l <cmd> <args>`` responde sem prompt: rc 0 = há regra NOPASSWD que
    # cobre ESSA invocação. Sondamos com 'add' + argumento fictício porque a regra
    # recomendada é restrita aos subcomandos ("rsync-hub add *", nunca o script todo).
    if not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", bin_path, "add", "__sonda__"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_hub(cfg: dict, args: list[str], timeout: int = 90) -> tuple[bool, str]:
    """Executa ``rsync-hub <args>`` como root (direto se já formos root, senão
    ``sudo -n``). Retorna (ok, saída combinada) — a saída do script é a mensagem
    para o admin, exatamente como no terminal."""
    bin_path = rsync_hub_bin(cfg)
    if not bin_path:
        return False, ("rsync-hub não encontrado neste host (procurei o override "
                       "'rsync_hub_bin' do config, /usr/local/bin/rsync-hub e o PATH). "
                       "Prepare o host com: sudo ./collection/rsync-hub install")
    cmd = [bin_path, *args]
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        cmd = ["sudo", "-n", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return False, "sudo não encontrado — rode a dashboard como root ou instale o sudo."
    except subprocess.TimeoutExpired:
        return False, f"tempo esgotado executando: {' '.join(cmd)}"
    out = ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
    if r.returncode != 0:
        if "password is required" in (r.stderr or "") or "senha" in (r.stderr or "").lower():
            out = ("sudo pediu senha (a dashboard só executa modo não interativo).\n"
                   "Rode no servidor: sudo dashboard/setup-hub-access.sh <usuario-da-dashboard>\n"
                   f"(ou libere no visudo a linha: {sudoers_hint(bin_path)})")
        return False, out or f"falha (rc={r.returncode}) executando: {' '.join(cmd)}"
    return True, out


# --- Estações: listar / autorizar / revogar ---------------------------------------
def list_hub_stations(cfg: dict) -> list[dict]:
    """Lista de gerenciamento: além das fontes (detect_hub_stations), resolve o(s)
    modo(s) de autenticação e se há credenciais guardadas em ``stations/<id>/``."""
    detected = config_store.detect_hub_stations(cfg)
    base = hub_base(cfg)
    out = []
    for d in detected:
        sid = d["id"]
        creds_dir = base / "stations" / sid
        out.append({
            **d,
            "key_mode": "authorized_keys" in d["sources"],
            "pw_mode": "rsyncd.secrets" in d["sources"],
            "has_creds": creds_dir.is_dir(),
            "incoming": (base / "incoming" / sid).is_dir(),
        })
    return out


def valid_hub_id(sid: str) -> bool:
    return bool(HUB_ID_RE.match(sid or ""))


def add_station_key(cfg: dict, sid: str, pubkey_line: str | None) -> tuple[bool, str]:
    """``rsync-hub add <id>`` — sem chave o hub GERA o par em stations/<id>/ (com
    INSTRUCOES.txt); com uma linha colada, grava-a num arquivo temporário e autoriza."""
    if not valid_hub_id(sid):
        return False, "id inválido (minúsculas, dígitos e '_', começando por letra/dígito)."
    if not pubkey_line:
        return _run_hub(cfg, ["add", sid])
    line = pubkey_line.strip()
    if not re.match(r"^(ssh-|ecdsa-|sk-)\S+\s+\S+", line):
        return False, "isso não parece uma chave pública SSH ('ssh-ed25519 AAAA…')."
    # Temporário legível por root (quem executa o add); apagado em seguida.
    fd, tmp = tempfile.mkstemp(prefix="carbonara-pub-", suffix=".pub", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        os.chmod(tmp, 0o644)
        return _run_hub(cfg, ["add", sid, tmp])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def add_station_password(cfg: dict, sid: str, password: str | None) -> tuple[bool, str]:
    """``rsync-hub add <id> --senha [senha]`` — senha vazia = o hub gera uma; a senha
    final fica no INSTRUCOES.txt da estação (visível na página de credenciais)."""
    if not valid_hub_id(sid):
        return False, "id inválido (minúsculas, dígitos e '_', começando por letra/dígito)."
    pw = (password or "").strip()
    if pw and (":" in pw or " " in pw):
        return False, "a senha não pode conter ':' nem espaço."
    args = ["add", sid, "--senha"] + ([pw] if pw else [])
    return _run_hub(cfg, args)


def move_root(cfg: dict, new_root: str) -> tuple[bool, str]:
    """``rsync-hub move-root <novo> --confirmo`` — migra a raiz INTEIRA do hub.

    Toda a parte delicada (cópia preservando permissões, home da conta de serviço,
    /etc/rsync-hub.conf, rsyncd) é do próprio rsync-hub; a raiz antiga fica intacta
    como cópia de segurança. Timeout generoso: a cópia é proporcional aos dados —
    para volumes muito grandes, o terminal é o lugar certo (a UI avisa)."""
    new = (new_root or "").strip()
    if not new.startswith("/") or "\n" in new or "\r" in new:
        return False, "informe um caminho ABSOLUTO (começando com /) para a nova raiz."
    if new.rstrip("/") in ("", "/"):
        return False, "não dá para usar '/' como raiz."
    # Espelha a validação do rsync-hub (que também recusa — cinto e suspensório).
    if any(c in new for c in " \t'\":\\"):
        return False, "o novo caminho não pode conter espaços, aspas, ':' ou '\\'."
    return _run_hub(cfg, ["move-root", new, "--confirmo"], timeout=7200)


def rewrite_absolute_paths(cfg: dict, old_base: str, new_base: str) -> int:
    """Pós-migração: reescreve IN PLACE, nos cadastros do dashboard, os caminhos
    ABSOLUTOS que apontavam para DENTRO da raiz antiga — ``connections_log``,
    ``heartbeat_file`` e ``files[].path`` das estações. Sem isso, um cadastro
    absoluto (ex.: ``/srv/carbonara/incoming/x/a.dat``) continuaria lendo a árvore
    antiga, congelada. Caminhos relativos não precisam de nada: já seguem o
    ``data_root`` novo. Retorna quantos caminhos foram reescritos."""
    old = old_base.rstrip("/") + "/"
    new = new_base.rstrip("/")

    def fix(p):
        if isinstance(p, str) and p.startswith(old):
            return new + "/" + p[len(old):]
        return None

    count = 0
    fixed = fix(cfg.get("connections_log"))
    if fixed:
        cfg["connections_log"] = fixed
        count += 1
    for s in cfg.get("stations", []):
        fixed = fix(s.get("heartbeat_file"))
        if fixed:
            s["heartbeat_file"] = fixed
            count += 1
        for f in s.get("files", []):
            fixed = fix(f.get("path"))
            if fixed:
                f["path"] = fixed
                count += 1
    return count


def remove_station(cfg: dict, sid: str) -> tuple[bool, str]:
    """``rsync-hub remove <id>`` — revoga chave E senha; dados e credenciais em disco
    são preservados (comportamento do próprio hub)."""
    if not valid_hub_id(sid):
        return False, "id inválido."
    return _run_hub(cfg, ["remove", sid])


def purge_station(cfg: dict, sid: str) -> tuple[bool, str]:
    """``rsync-hub purge <id> --confirmo`` — EXCLUSÃO DEFINITIVA: revoga o acesso e
    apaga do servidor incoming/, archive/, quarantine/ e stations/ da estação.

    A confirmação digitada (nome da estação) é validada na ROTA, antes de chegar
    aqui — este código só roda depois dela. Irreversível; timeout maior porque
    apagar um archive/ grande pode demorar."""
    if not valid_hub_id(sid):
        return False, "id inválido."
    return _run_hub(cfg, ["purge", sid, "--confirmo"], timeout=600)


# --- Credenciais da estação (stations/<id>/) --------------------------------------
def station_credentials(cfg: dict, sid: str) -> dict:
    """Arquivos de ``stations/<id>/`` (chave privada, .pub, INSTRUCOES.txt) com o
    conteúdo dos textos exibíveis inline. Leitura direta — a política do hub os
    mantém legíveis (644) justamente para dispensar sudo na cópia."""
    base = hub_base(cfg)
    d = base / "stations" / sid
    info: dict = {"dir": str(d), "exists": d.is_dir(), "files": [],
                  "pubkey": None, "instrucoes": None}
    if not d.is_dir():
        return info
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        st = p.stat()
        info["files"].append({"name": p.name, "size": st.st_size,
                              "mtime": _fmt_ts(st.st_mtime)})
        try:
            if p.name.endswith(".pub"):
                info["pubkey"] = p.read_text(encoding="utf-8", errors="replace").strip()
            elif p.name == "INSTRUCOES.txt":
                info["instrucoes"] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return info


def authorized_key_line(cfg: dict, sid: str) -> str | None:
    """Linha da estação no authorized_keys (modo chave) — mostra o comando forçado."""
    base = hub_base(cfg)
    try:
        for line in (base / ".ssh" / "authorized_keys").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if f"serve {sid}\"" in line:
                return line
    except OSError:
        pass
    return None


# --- Kit de ativação (.zip) --------------------------------------------------------
# Clientes guiados que rodam NA estação — empacotados junto com as credenciais para
# o operador levar tudo de uma vez. O dashboard roda de dentro do repositório, então
# a pasta collection/station/ está dois níveis acima de app/.
CLIENT_DIR = Path(__file__).resolve().parents[2] / "collection" / "station"
CLIENT_FILES = ("carbonara-station.sh", "carbonara-station.ps1", "carbonara-station.cmd")


def station_kit_zip(cfg: dict, sid: str) -> tuple[bytes | None, str]:
    """Monta em memória o kit de ativação da estação: credenciais geradas no hub
    (``stations/<id>/`` — chave privada, ``.pub``, ``INSTRUCOES.txt``) + os clientes
    de ``collection/station/`` + um LEIA-ME. Retorna (zip_bytes, "") ou (None, erro).

    Cuidados: os bytes são copiados TAL QUAL (o ``.ps1`` precisa manter o BOM UTF-8 —
    invariante do cliente Windows); o modo Unix vai no external_attr para a chave
    privada sair 600 e o ``.sh`` executável ao descompactar no Linux."""
    creds_dir = hub_base(cfg) / "stations" / sid
    cred_files: list[Path] = []
    if creds_dir.is_dir():
        cred_files = sorted(p for p in creds_dir.iterdir() if p.is_file())
    client_files = [CLIENT_DIR / n for n in CLIENT_FILES if (CLIENT_DIR / n).is_file()]
    if not cred_files and not client_files:
        return None, ("nada para empacotar: a estação não tem credenciais em "
                      f"{creds_dir} e os clientes de collection/station/ não foram "
                      "encontrados junto ao dashboard.")

    def mode_for(name: str) -> int:
        if name.endswith("_key"):
            return 0o600     # chave privada: protegida ao descompactar
        if name.endswith(".sh"):
            return 0o755     # cliente Linux: executável ao descompactar
        return 0o644

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        def add(arcname: str, data: bytes, mode: int) -> None:
            zi = zipfile.ZipInfo(arcname)
            zi.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(zi, data, zipfile.ZIP_DEFLATED)

        listed: list[str] = []
        for p in cred_files:
            try:
                add(p.name, p.read_bytes(), mode_for(p.name))
                listed.append(p.name)
            except OSError:
                continue
        for p in client_files:
            add(f"cliente/{p.name}", p.read_bytes(), mode_for(p.name))
            listed.append(f"cliente/{p.name}")
        add("LEIA-ME.txt", _kit_readme(sid, listed).encode("utf-8"), 0o644)
    return buf.getvalue(), ""


def _kit_readme(sid: str, listed: list[str]) -> str:
    descr = {
        "INSTRUCOES.txt": "passo a passo de ativação (COMECE POR ELE)",
        f"{sid}_key": "chave privada da estação — mantenha-a protegida",
        f"{sid}_key.pub": "chave pública (cópia; a original já está autorizada no hub)",
        "cliente/carbonara-station.sh": "cliente Linux (bash + cron)",
        "cliente/carbonara-station.ps1": "cliente Windows (PowerShell)",
        "cliente/carbonara-station.cmd": "Windows: abra por ESTE (destrava o .ps1)",
    }
    linhas = "\n".join(f"  {n:<34} {descr.get(n, '')}".rstrip() for n in listed)
    return (
        f"CARBONARA — kit de ativação da estação '{sid}'\n"
        f"{'=' * (42 + len(sid))}\n\n"
        "Conteúdo deste pacote:\n"
        f"{linhas}\n\n"
        "Leve este pacote para a máquina da estação e siga o INSTRUCOES.txt.\n"
        "Onde as instruções mandam copiar o cliente \"da pasta collection/station\n"
        "do repositório\", não precisa: ele já está aqui, na pasta cliente/.\n"
    )


# --- Navegador de arquivos (read-only) --------------------------------------------
def area_root(cfg: dict, area: str) -> Path | None:
    sub = AREAS.get(area)
    return (hub_base(cfg) / sub[0]) if sub else None


def safe_resolve(cfg: dict, area: str, subpath: str) -> Path | None:
    """Resolve ``subpath`` sob a raiz da área com trava anti-traversal: o caminho
    RESOLVIDO (symlinks inclusos) precisa continuar sob a raiz, senão None."""
    root = area_root(cfg, area)
    if root is None:
        return None
    p = (root / (subpath or "")).resolve()
    root_r = root.resolve()
    if p != root_r and root_r not in p.parents:
        return None
    return p


def list_dir(cfg: dict, area: str, subpath: str) -> dict | None:
    """Listagem de uma pasta da área: subpastas e arquivos com tamanho/mtime, e o
    trilho de navegação (breadcrumbs). Dotfiles APARECEM (heartbeat, .rsync-parcial)
    — isto é uma tela de administração; a ingestão é quem os ignora."""
    path = safe_resolve(cfg, area, subpath)
    if path is None or not path.is_dir():
        return None
    root = area_root(cfg, area).resolve()
    dirs, files, truncated = [], [], False
    try:
        with os.scandir(path) as it:
            for i, entry in enumerate(it):
                if i >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = str(Path(entry.path).relative_to(root))
                item = {"name": entry.name, "rel": rel, "hidden": entry.name.startswith("."),
                        "mtime": _fmt_ts(st.st_mtime)}
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(item)
                else:
                    files.append({**item, "size": st.st_size})
    except OSError:
        return None
    dirs.sort(key=lambda x: x["name"])
    files.sort(key=lambda x: x["name"])

    crumbs, acc = [], []
    rel_now = "" if path == root else str(path.relative_to(root))
    for part in Path(rel_now).parts:
        acc.append(part)
        crumbs.append({"name": part, "rel": "/".join(acc)})
    return {"area": area, "label": AREAS[area][1], "root": str(root), "rel": rel_now,
            "crumbs": crumbs, "dirs": dirs, "files": files, "truncated": truncated,
            "max_entries": MAX_LIST_ENTRIES}


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")


def fmt_size(n: int | None) -> str:
    """Tamanho humano (pt-BR usa os mesmos sufixos)."""
    if n is None:
        return "—"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"
