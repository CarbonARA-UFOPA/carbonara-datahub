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
from datetime import datetime, timezone
from pathlib import Path

from . import config_store
from .i18n import gettext as tr

# Mesma validação do rsync-hub (valid_id) — o id vira argumento de comando e caminho.
HUB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,30}$")

# Áreas navegáveis do hub: slug da URL -> (subpasta sob a BASE, rótulo de exibição).
AREAS = {
    "incoming": ("incoming", "Recebidos (incoming)"),
    "archive": ("archive", "Arquivados (archive)"),
    "quarantine": ("quarantine", "Quarentena"),
    "stations": ("stations", "Credenciais (stations)"),
    # A raiz do FTP de distribuição NÃO é necessariamente <BASE>/ftp: area_root a
    # resolve por ftp.ftp_root (local_root do vsftpd.conf). O rótulo é o que aparece
    # nas abas do navegador de arquivos.
    "ftp": ("ftp", "FTP (publicado)"),
}

MAX_LIST_ENTRIES = 2000  # teto de entradas por listagem (pastas gigantes de archive)


def dashboard_user() -> str:
    """Usuário REAL do processo — é ELE que precisa estar na regra do sudoers.

    Deliberadamente não confia no ``$USER`` do ambiente: quando a dashboard sobe por
    systemd (``dashboardctl servico``) ou por ``sudo -u``, o ambiente pode carregar o
    usuário da sessão que disparou, e não o dono do processo. Uma regra instalada para
    o nome errado é indistinguível de "sudo não configurado" — e foi assim que a tela
    já sugeriu o nome errado ao administrador."""
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:  # noqa: BLE001 — sem pwd (não-POSIX) cai no ambiente
        return os.environ.get("USER") or "usuario-da-dashboard"


def sudoers_hint(bin_path: str | None) -> str:
    """Regra sudoers que o gerenciamento precisa (a mesma que o script
    dashboard/setup-hub-access.sh instala em /etc/sudoers.d/)."""
    b = bin_path or "/usr/local/bin/rsync-hub"
    user = dashboard_user()
    return (f"{user} ALL=(root) NOPASSWD: {b} add *, {b} remove *, {b} purge *, "
            f"{b} move-root *, {b} pause *, {b} resume *")


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
    can_write, sudo_error = _probe_sudo(bin_path, ["add", "__sonda__"])
    return {
        "bin": bin_path,
        "version": version,
        # Usuário REAL do processo — o que precisa constar da regra do sudoers.
        "user": dashboard_user(),
        # Mensagem crua do sudo quando ele recusa: é o que diferencia "regra ausente"
        # de "regra instalada para outro usuário" ou "sudoers.d não é lido".
        "sudo_error": sudo_error,
        "base": str(base),
        "conf_base": conf_base,
        # BASE divergente = a dashboard lê de um lugar e o rsync-hub escreve em outro
        # (ex.: dev com ./data). O template avisa; as escritas usam a BASE do hub.
        "base_mismatch": bool(conf_base) and str(base) != conf_base,
        "can_write": can_write,
        # Sonda separada: instalações que rodaram o setup-hub-access.sh ANTES da pausa
        # existir têm sudoers sem 'pause'/'resume' — o resto continua funcionando e a
        # tela pede só a atualização da regra, em vez de falhar no clique.
        "can_pause": _can_run(bin_path, ["pause", "__sonda__"]),
        "sudoers_hint": sudoers_hint(bin_path),
    }


def _probe_sudo(bin_path: str | None, args: list[str]) -> tuple[bool, str]:
    """Sonda ``sudo -n -l <bin> <args>`` e devolve (pode?, MOTIVO da recusa).

    O motivo é o que o próprio sudo diz — "a password is required" (regra existe mas
    não é NOPASSWD, ou nem é a regra que casou), "Sorry, user X may not run…" (nenhuma
    regra para ESTE usuário/comando) etc. Sem isso a tela só sabia dizer "sem
    permissão", que não distingue regra ausente de regra no usuário errado."""
    if not bin_path:
        return False, tr("rsync-hub não encontrado neste host.")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True, ""
    if not shutil.which("sudo"):
        return False, tr("o comando 'sudo' não existe (ou não está no PATH deste processo).")
    try:
        r = subprocess.run(["sudo", "-n", "-l", bin_path, *args],
                           capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False, tr("o 'sudo' não respondeu em 10s (pediu senha e ficou esperando?).")
    except OSError as exc:
        return False, tr("falha ao executar o sudo: %(erro)s", erro=exc)
    if r.returncode == 0:
        return True, ""
    msg = ((r.stderr or "") + " " + (r.stdout or "")).strip()
    return False, msg or tr("sudo recusou (rc=%(rc)s) sem mensagem.", rc=r.returncode)


def _can_run(bin_path: str | None, args: list[str]) -> bool:
    """A dashboard consegue executar ``rsync-hub <args>`` como root sem senha?
    ``sudo -n -l <cmd> <args>`` responde sem prompt: rc 0 = há regra NOPASSWD que cobre
    ESSA invocação. Sondamos com argumento fictício porque a regra recomendada é
    restrita aos subcomandos ("rsync-hub add *", nunca o script inteiro)."""
    return _probe_sudo(bin_path, args)[0]


def _run_hub(cfg: dict, args: list[str], timeout: int = 90) -> tuple[bool, str]:
    """Executa ``rsync-hub <args>`` como root (direto se já formos root, senão
    ``sudo -n``). Retorna (ok, saída combinada) — a saída do script é a mensagem
    para o admin, exatamente como no terminal."""
    bin_path = rsync_hub_bin(cfg)
    if not bin_path:
        return False, tr("rsync-hub não encontrado neste host (procurei o override "
                         "'rsync_hub_bin' do config, /usr/local/bin/rsync-hub e o PATH). "
                         "Prepare o host com: sudo ./collection/rsync-hub install")
    cmd = [bin_path, *args]
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        cmd = ["sudo", "-n", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return False, tr("sudo não encontrado — rode a dashboard como root ou instale o sudo.")
    except subprocess.TimeoutExpired:
        return False, tr("tempo esgotado executando: %(cmd)s", cmd=" ".join(cmd))
    out = ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
    if r.returncode != 0:
        if "password is required" in (r.stderr or "") or "senha" in (r.stderr or "").lower():
            out = tr("sudo pediu senha (a dashboard só executa modo não interativo).\n"
                     "Rode no servidor: sudo dashboard/setup-hub-access.sh <usuario-da-dashboard>\n"
                     "(ou libere no visudo a linha: %(hint)s)", hint=sudoers_hint(bin_path))
        return False, out or tr("falha (rc=%(rc)s) executando: %(cmd)s",
                                rc=r.returncode, cmd=" ".join(cmd))
    return True, out


# --- Pausa da recepção -------------------------------------------------------------
# Espelha `rsync-hub pause/resume` (collection/rsync-hub): marcadores em <BASE>/pause/
# — ``_todas`` para a pausa global e ``<id>`` por estação. A LEITURA é direta (644/755,
# política de leitura aberta do hub), então o estado aparece na interface mesmo sem a
# regra de sudo; só PAUSAR/RETOMAR passa pelo rsync-hub como root.
PAUSE_ALL = "_todas"      # nome do marcador global (igual ao do shell)
PAUSE_FIELDS = {"desde": "since", "por": "by", "motivo": "reason"}


def pause_dir(cfg: dict) -> Path:
    return hub_base(cfg) / "pause"


def _fmt_pause_ts(ts: str, cfg: dict) -> str:
    """Carimbo UTC do marcador (``2026-08-24T12:00:00Z``) em hora local de exibição —
    mesma convenção do status (config ``timezone`` manda, senão o fuso do processo)."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ts or ""
    tzname = cfg.get("timezone")
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return dt.astimezone(ZoneInfo(tzname)).strftime("%d/%m/%Y %H:%M")
        except Exception:  # noqa: BLE001 — fuso inválido no config nunca quebra a tela
            pass
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


def _read_marker(path: Path, cfg: dict, target: str) -> dict:
    info = {"target": target, "since": "", "by": "", "reason": "", "since_display": ""}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    for line in text.splitlines():
        key, _, value = line.partition("=")
        field = PAUSE_FIELDS.get(key.strip())
        if field:
            info[field] = value.strip()
    info["since_display"] = _fmt_pause_ts(info["since"], cfg)
    return info


def pause_state(cfg: dict) -> dict:
    """Estado da pausa: ``{all, stations, paused_ids, any}``. Nunca levanta — uma raiz
    mal configurada só significa "nada pausado", jamais uma página quebrada."""
    state = {"all": None, "stations": {}, "paused_ids": [], "any": False}
    try:
        d = pause_dir(cfg)
        entries = sorted(d.iterdir()) if d.is_dir() else []
    except OSError:
        entries = []
    for f in entries:
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if f.name == PAUSE_ALL:
            state["all"] = _read_marker(f, cfg, "todas")
        elif valid_hub_id(f.name):
            state["stations"][f.name] = _read_marker(f, cfg, f.name)
    state["paused_ids"] = sorted(state["stations"])
    state["any"] = bool(state["all"] or state["stations"])
    return state


def station_paused(state: dict, sid: str) -> bool:
    """Uma estação está pausada pela pausa global OU pela sua própria."""
    return bool(state.get("all")) or sid in (state.get("stations") or {})


def pause_marker_for(state: dict, sid: str) -> dict | None:
    """Marcador que EXPLICA a pausa da estação — a global tem prioridade (é ela que o
    `serve` cita para a estação)."""
    if state.get("all"):
        return state["all"]
    return (state.get("stations") or {}).get(sid)


def _pause_target(target: str) -> tuple[str | None, str]:
    """Normaliza o alvo do formulário: ``--todas`` (global) ou o id validado.
    Sempre devolve um argumento EXPLÍCITO — a regra do sudoers é ``pause *``, que não
    casa com o comando sem argumento nenhum."""
    t = (target or "").strip().lower()
    if t in ("", "todas", "all", "--todas", "--all"):
        return "--todas", tr("todas as estações")
    if not valid_hub_id(t):
        return None, ""
    return t, tr("a estação '%(id)s'", id=t)


def set_pause(cfg: dict, target: str, reason: str | None = None) -> tuple[bool, str]:
    """``rsync-hub pause <id|--todas> [motivo]`` — suspende a recepção sem revogar
    credenciais nem apagar dados; as estações reenviam quando a recepção voltar."""
    arg, _label = _pause_target(target)
    if arg is None:
        return False, tr("id de estação inválido para pausar.")
    # Motivo vira uma linha do marcador (motivo=...): quebras de linha o corromperiam.
    why = " ".join((reason or "").split())[:200]
    return _run_hub(cfg, ["pause", arg] + ([why] if why else []))


def clear_pause(cfg: dict, target: str) -> tuple[bool, str]:
    """``rsync-hub resume <id|--todas>`` — retoma a recepção."""
    arg, _label = _pause_target(target)
    if arg is None:
        return False, tr("id de estação inválido para retomar.")
    return _run_hub(cfg, ["resume", arg])


# --- Estações: listar / autorizar / revogar ---------------------------------------
def list_hub_stations(cfg: dict) -> list[dict]:
    """Lista de gerenciamento: além das fontes (detect_hub_stations), resolve o(s)
    modo(s) de autenticação e se há credenciais guardadas em ``stations/<id>/``."""
    detected = config_store.detect_hub_stations(cfg)
    base = hub_base(cfg)
    pstate = pause_state(cfg)
    out = []
    for d in detected:
        sid = d["id"]
        creds_dir = base / "stations" / sid
        out.append({
            **d,
            "paused": station_paused(pstate, sid),
            "paused_own": sid in pstate["stations"],
            "pause": pause_marker_for(pstate, sid),
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
        return False, tr("id inválido (minúsculas, dígitos e '_', começando por letra/dígito).")
    if not pubkey_line:
        return _run_hub(cfg, ["add", sid])
    line = pubkey_line.strip()
    if not re.match(r"^(ssh-|ecdsa-|sk-)\S+\s+\S+", line):
        return False, tr("isso não parece uma chave pública SSH ('ssh-ed25519 AAAA…').")
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
        return False, tr("id inválido (minúsculas, dígitos e '_', começando por letra/dígito).")
    pw = (password or "").strip()
    if pw and (":" in pw or " " in pw):
        return False, tr("a senha não pode conter ':' nem espaço.")
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
        return False, tr("informe um caminho ABSOLUTO (começando com /) para a nova raiz.")
    if new.rstrip("/") in ("", "/"):
        return False, tr("não dá para usar '/' como raiz.")
    # Espelha a validação do rsync-hub (que também recusa — cinto e suspensório).
    if any(c in new for c in " \t'\":\\"):
        return False, tr("o novo caminho não pode conter espaços, aspas, ':' ou '\\'.")
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
        return False, tr("id inválido.")
    return _run_hub(cfg, ["remove", sid])


def purge_station(cfg: dict, sid: str) -> tuple[bool, str]:
    """``rsync-hub purge <id> --confirmo`` — EXCLUSÃO DEFINITIVA: revoga o acesso e
    apaga do servidor incoming/, archive/, quarantine/ e stations/ da estação.

    A confirmação digitada (nome da estação) é validada na ROTA, antes de chegar
    aqui — este código só roda depois dela. Irreversível; timeout maior porque
    apagar um archive/ grande pode demorar."""
    if not valid_hub_id(sid):
        return False, tr("id inválido.")
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
        return None, tr("nada para empacotar: a estação não tem credenciais em "
                        "%(dir)s e os clientes de collection/station/ não foram "
                        "encontrados junto ao dashboard.", dir=creds_dir)

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
    if area == "ftp":
        from . import ftp  # import tardio: ftp importa hub
        return ftp.ftp_root(cfg)
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
