"""Páginas HTML (Jinja) — lista de estações, detalhe, formulários e login de admin.

Modelo de acesso: VISUALIZAR é aberto (monitor de rede interna); qualquer EDIÇÃO
(cadastros, remoções, troca de caminho) exige login de administração, definido no
bloco ``auth:`` do config.yaml (hash de senha — nunca texto puro). Sem bloco
``auth:``, a edição fica liberada (modo desenvolvimento).
"""
from __future__ import annotations

import secrets
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from . import config_store, hub, status

pages = Blueprint("pages", __name__)


@pages.app_template_filter("hbytes")
def _fmt_hbytes(v):
    """Volume de dados em unidade legível (mesma escala do `rsync-hub volume`)."""
    if not v:
        return "—"
    for unidade in ("B", "kB", "MB", "GB", "TB"):
        if v < 1000 or unidade == "TB":
            return f"{v:.0f} {unidade}" if unidade == "B" else f"{v:.1f} {unidade}"
        v /= 1000
    return f"{v:.1f} TB"


@pages.app_template_filter("bps")
def _fmt_bps(v):
    """Bytes por segundo em unidade legível (mesma escala do monitor no terminal)."""
    if not v:
        return "—"
    for unidade in ("B/s", "kB/s", "MB/s", "GB/s"):
        if v < 1000 or unidade == "GB/s":
            return f"{v:.0f} {unidade}" if unidade == "B/s" else f"{v:.1f} {unidade}"
        v /= 1000
    return f"{v:.1f} GB/s"


# --- Autenticação de administração -------------------------------------------------
def auth_enabled(cfg) -> bool:
    return bool(cfg.get("auth"))


def _check_login(cfg, username: str, password: str) -> bool:
    auth = cfg.get("auth") or {}
    if not secrets.compare_digest(username, str(auth.get("username") or "")):
        return False
    if auth.get("password_hash"):
        return check_password_hash(auth["password_hash"], password)
    # fallback: senha em texto no config (desaconselhado; prefira password_hash)
    return bool(auth.get("password")) and secrets.compare_digest(str(auth["password"]), password)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        cfg = _load()
        if auth_enabled(cfg) and not session.get("admin"):
            if request.path.startswith("/api"):
                from flask import jsonify
                return jsonify({"error": "Login de administração necessário."}), 401
            flash("Faça login para editar.", "error")
            return redirect(url_for("pages.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@pages.get("/login")
def login():
    return render_template("login.html")


@pages.post("/login")
def login_post():
    cfg = _load()
    if _check_login(cfg, request.form.get("username", ""), request.form.get("password", "")):
        session["admin"] = True
        flash("Login de administração efetuado.", "ok")
        nxt = request.form.get("next") or ""
        safe = nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt
        return redirect(nxt if safe else url_for("pages.index"))
    flash("Usuário ou senha incorretos.", "error")
    return render_template("login.html"), 401


@pages.post("/logout")
def logout():
    session.pop("admin", None)
    flash("Sessão de administração encerrada.", "ok")
    return redirect(url_for("pages.index"))


def _load():
    return current_app.store.load()


def _save(cfg):
    current_app.store.save(cfg)


def _station_or_404(cfg, station_id):
    station = config_store.get_station(cfg, station_id)
    if not station:
        abort(404)
    return station


@pages.get("/")
def index():
    cfg = _load()
    pause = hub.pause_state(cfg)
    stations = [status.station_status(cfg, s, pause) for s in cfg["stations"]]
    # Quantas estações do rsync-hub ainda não estão na dashboard (banner de importação).
    # Só varre para quem pode editar, e nunca deixa uma raiz mal configurada quebrar a página.
    new_count = 0
    if session.get("admin") or not auth_enabled(cfg):
        try:
            new_count = sum(1 for d in config_store.detect_hub_stations(cfg) if not d["in_dashboard"])
        except Exception:  # noqa: BLE001
            new_count = 0
    return render_template("stations.html", stations=stations, cfg=cfg, new_count=new_count)


# --- Configuração master + detecção de estações do rsync-hub ----------------------
@pages.get("/settings")
@admin_required
def settings():
    cfg = _load()
    detected = []
    try:
        detected = config_store.detect_hub_stations(cfg)
    except Exception:  # noqa: BLE001
        pass
    return render_template("settings.html", cfg=cfg, detected=detected,
                           hub_info=hub.hub_info(cfg))


@pages.post("/settings")
@admin_required
def settings_save():
    cfg = _load()
    updated, errors = config_store.validate_settings(request.form, cfg)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("settings.html", cfg={**cfg, **updated}, detected=[],
                               hub_info=None), 400
    # Raízes ANTIGAS (antes da troca): cadastros gravados com caminho ABSOLUTO apontam
    # para dentro delas e continuariam lendo a árvore velha, congelada — o sintoma é
    # "as estações ficaram offline depois que mudei a raiz". O mesmo tratamento que a
    # migração pela dashboard já fazia; aqui vale para a troca manual da raiz (típica
    # depois de um `rsync-hub move-root` feito pelo terminal).
    old_bases = []
    for cand in (str(config_store.data_root(cfg)),
                 cfg.get("data_root") if str(cfg.get("data_root", "")).startswith("/") else None):
        if cand and cand not in old_bases:
            old_bases.append(cand)

    cfg["data_root"] = updated["data_root"]
    cfg["connections_log"] = updated["connections_log"]
    cfg["timezone"] = updated["timezone"]
    cfg["stale_fallback_seconds"] = updated["stale_fallback_seconds"]
    new_abs = str(config_store.data_root(cfg))
    rewritten = 0
    if new_abs not in old_bases:
        rewritten = sum(hub.rewrite_absolute_paths(cfg, ob, new_abs) for ob in old_bases)
    _save(cfg)
    if rewritten:
        flash(f"{rewritten} caminho(s) absoluto(s) de cadastros (watchdog, arquivos "
              "monitorados, log de conexões) foram reapontados para a nova raiz.", "ok")
    if updated.get("data_root_adjusted"):
        flash(f"A raiz apontava para uma pasta incoming/; ajustei para '{updated['data_root']}' "
              "(a raiz do hub é a que CONTÉM incoming/).", "ok")
    flash("Configuração master salva. Pastas, heartbeat e log passam a derivar da nova raiz.", "ok")
    return redirect(url_for("pages.settings"))


@pages.post("/stations/import-detected")
@admin_required
def stations_import_detected():
    cfg = _load()
    try:
        detected = config_store.detect_hub_stations(cfg)
    except Exception as exc:  # noqa: BLE001
        flash(f"Falha ao detectar estações do rsync-hub: {exc}", "error")
        return redirect(url_for("pages.index"))
    added = 0
    for d in detected:
        if d["in_dashboard"]:
            continue
        cfg["stations"].append(config_store.new_station_entry(d["id"]))
        added += 1
    if added:
        _save(cfg)
        flash(f"{added} estação(ões) importada(s) do rsync-hub. Pastas e heartbeat usam os "
              "locais padrão sob a raiz do hub — abra cada uma para adicionar arquivos "
              "de monitoramento.", "ok")
    else:
        flash("Nenhuma estação nova detectada no rsync-hub.", "ok")
    return redirect(url_for("pages.index"))


@pages.get("/compare")
def compare():
    """Tela multi-estação: seleciona variáveis de qualquer estação/arquivo configurado.

    A seleção vive na querystring (?sel=<station>:<arquivo>:<índice da variável>),
    então uma combinação pode ser favoritada/compartilhada como URL.
    """
    cfg = _load()
    selected, sel_set = [], set()
    for token in request.args.getlist("sel"):
        if token in sel_set:   # mesmo token repetido na URL não vira série duplicada
            continue
        parts = token.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            continue
        sid, fid, idx = parts[0], parts[1], int(parts[2])
        station = config_store.get_station(cfg, sid)
        fcfg = config_store.get_file(station, fid) if station else None
        if not fcfg or idx >= len(fcfg.get("variables", [])):
            continue
        var = fcfg["variables"][idx]
        selected.append({
            "sid": sid,
            "sname": station.get("name") or sid,
            "fid": fid,
            "idx": idx,
            "label": var["label"],
            "unit": var.get("unit"),
            "color": len(selected),  # índice global de cor, estável entre séries
            "series_url": url_for("api.file_series", station_id=sid, file_id=fid),
        })
        sel_set.add(token)
    # Todas as séries vão num ÚNICO gráfico com eixo de tempo comum (mesclado no
    # cliente via os epochs da API); a legenda identifica estação · arquivo · variável.
    return render_template("compare.html", cfg=cfg, selected=selected, sel_set=sel_set)


@pages.get("/map")
def map_view():
    """Mapa das estações (somente visualização): status ao vivo + gráficos ao clicar.

    As camadas base (OSM padrão, satélite, relevo) vêm de servidores públicos de
    tiles — o mapa precisa de internet; a biblioteca Leaflet é vendorada em static/.
    """
    cfg = _load()
    stations = []
    for s in cfg["stations"]:
        st = status.station_status(cfg, s)
        stations.append({
            "id": s["id"],
            "name": s.get("name") or s["id"],
            "latitude": s.get("latitude"),
            "longitude": s.get("longitude"),
            "status": st["status"],
            "last_signal": st["last_signal"],
            "files": [
                {
                    "id": f["id"],
                    "alias": f.get("alias"),
                    "series_url": url_for("api.file_series", station_id=s["id"], file_id=f["id"]),
                    "variables": [
                        {"label": v["label"], "unit": v.get("unit")}
                        for v in f.get("variables", [])
                    ],
                }
                for f in s.get("files", [])
            ],
        })
    return render_template("map.html", stations=stations)


# --- Estações ---------------------------------------------------------------------
@pages.get("/stations/new")
@admin_required
def station_new():
    return render_template("station_form.html", station=None)


@pages.post("/stations")
@admin_required
def station_create():
    cfg = _load()
    station, errors = config_store.validate_station(request.form, is_new=True, cfg=cfg)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("station_form.html", station=request.form), 400
    cfg["stations"].append(station)
    _save(cfg)
    flash(f"Estação '{station['id']}' cadastrada.", "ok")
    return redirect(url_for("pages.station_detail", station_id=station["id"]))


@pages.get("/stations/<station_id>")
def station_detail(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    # Nome REAL do arquivo de cada cadastro com caminho dinâmico: o id e o apelido são
    # congelados no cadastro (viraram o nome do arquivo daquele dia) e no dia seguinte
    # passam a mentir. Resolvido aqui para a página já nascer com o nome certo — o JS
    # reatualiza a cada ciclo. Nunca pode derrubar a página: erro vira "sem nome vivo".
    live_names = {}
    for f in station.get("files", []):
        if not f.get("dynamic_path"):
            continue
        try:
            caminho, _info = config_store.resolve_file_path(cfg, f)
            live_names[f["id"]] = caminho.name
        except (ValueError, OSError, KeyError):
            pass
    return render_template(
        "station_detail.html",
        station=station,
        st=status.station_status(cfg, station),
        live_names=live_names,
    )


@pages.get("/stations/<station_id>/edit")
@admin_required
def station_edit(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    return render_template("station_form.html", station=station)


@pages.post("/stations/<station_id>/edit")
@admin_required
def station_update(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    updated, errors = config_store.validate_station(request.form, is_new=False, cfg=cfg)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("station_form.html", station=station), 400
    # heartbeat_file/offline_window_min não vêm mais do formulário; um valor legado no
    # YAML é preservado como está (não é editável nem apagável pela tela).
    station["name"] = updated["name"]
    station["latitude"] = updated["latitude"]
    station["longitude"] = updated["longitude"]
    _save(cfg)
    flash("Estação atualizada.", "ok")
    return redirect(url_for("pages.station_detail", station_id=station_id))


@pages.post("/stations/<station_id>/delete")
@admin_required
def station_delete(station_id):
    cfg = _load()
    _station_or_404(cfg, station_id)
    cfg["stations"] = [s for s in cfg["stations"] if s["id"] != station_id]
    _save(cfg)
    flash(f"Estação '{station_id}' removida (os dados em disco não foram tocados).", "ok")
    return redirect(url_for("pages.index"))


# --- Arquivos monitorados ---------------------------------------------------------
@pages.get("/stations/<station_id>/files/new")
@admin_required
def file_new(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    return render_template("file_form.html", station=station, fcfg=None)


@pages.post("/stations/<station_id>/files")
@admin_required
def file_create(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    entry, errors = config_store.validate_file_entry(request.form)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("file_form.html", station=station, fcfg=None), 400
    entry["id"] = config_store.unique_file_id(station, entry["path"])
    station.setdefault("files", []).append(entry)
    _save(cfg)
    flash(f"Arquivo '{entry['id']}' adicionado ao monitoramento.", "ok")
    return redirect(url_for("pages.station_detail", station_id=station_id))


@pages.get("/stations/<station_id>/browse/download")
@admin_required
def station_file_download(station_id):
    """Baixa um arquivo da pasta da estação — o 'baixar' do navegador da aba Coleta,
    aqui escopado a ``incoming/<id>/`` por ``station_safe_resolve`` (o caminho
    resolvido tem de continuar sob a pasta da estação, senão 404)."""
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    path = config_store.station_safe_resolve(cfg, station, request.args.get("sub") or "")
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@pages.post("/stations/<station_id>/files/discover-add")
@admin_required
def files_discover_add(station_id):
    """Adiciona em lote os arquivos marcados na listagem da estação (de qualquer
    extensão) como arquivos monitorados. Cada um entra com config MÍNIMA (padrões
    TOA5 + apelido, variáveis vazias) — o mapeamento de colunas/variáveis é feito
    depois em 'Editar'."""
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    existing = set()
    for f in station.get("files", []):
        try:
            existing.add(str(config_store.resolve_data_path(cfg, f["path"])))
        except (ValueError, KeyError):
            pass

    added = 0
    for token in request.form.getlist("pick"):
        path = (request.form.get(f"path_{token}") or "").strip()
        if not path:
            continue
        try:
            resolved = str(config_store.resolve_data_path(cfg, path))
        except ValueError:
            continue
        if resolved in existing:  # não duplica um caminho já monitorado
            continue
        alias = (request.form.get(f"alias_{token}") or "").strip() or None
        entry = config_store.default_file_entry(path, alias)
        entry["id"] = config_store.unique_file_id(station, path)
        station.setdefault("files", []).append(entry)
        existing.add(resolved)
        added += 1

    if added:
        _save(cfg)
        flash(f"{added} arquivo(s) adicionado(s) ao monitoramento. Abra 'Editar' em cada "
              "um para mapear as colunas e variáveis antes de aparecerem gráficos.", "ok")
    else:
        flash("Nenhum arquivo novo foi selecionado.", "error")
    return redirect(url_for("pages.station_edit", station_id=station_id))


@pages.get("/stations/<station_id>/files/<file_id>/edit")
@admin_required
def file_edit(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    return render_template("file_form.html", station=station, fcfg=fcfg)


@pages.post("/stations/<station_id>/files/<file_id>/edit")
@admin_required
def file_update(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    entry, errors = config_store.validate_file_entry(request.form)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("file_form.html", station=station, fcfg=fcfg), 400
    # merge sobre a entry existente: chaves adicionadas à mão no YAML sobrevivem à UI
    station["files"] = [
        {**f, **entry, "id": file_id} if f["id"] == file_id else f
        for f in station["files"]
    ]
    _save(cfg)
    flash("Configuração do arquivo atualizada.", "ok")
    return redirect(url_for("pages.station_detail", station_id=station_id))


@pages.post("/stations/<station_id>/files/<file_id>/delete")
@admin_required
def file_delete(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    station["files"] = [f for f in station.get("files", []) if f["id"] != file_id]
    _save(cfg)
    flash(f"Arquivo '{file_id}' removido do monitoramento (o arquivo em disco não foi tocado).", "ok")
    return redirect(url_for("pages.station_detail", station_id=station_id))
