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
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from . import config_store, status

pages = Blueprint("pages", __name__)


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
    stations = [status.station_status(cfg, s) for s in cfg["stations"]]
    return render_template("stations.html", stations=stations, cfg=cfg)


@pages.get("/compare")
def compare():
    """Tela multi-estação: seleciona variáveis de qualquer estação/arquivo configurado.

    A seleção vive na querystring (?sel=<station>:<arquivo>:<índice da variável>),
    então uma combinação pode ser favoritada/compartilhada como URL.
    """
    cfg = _load()
    selected, sel_set = [], set()
    for token in request.args.getlist("sel"):
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
            "color": len(selected),  # índice global de cor, estável entre grupos
        })
        sel_set.add(token)
    # Agrupa por estação+arquivo: cada arquivo .dat pode ser um sensor/equipamento
    # diferente — os gráficos saem em seções separadas para não misturar as fontes.
    groups, by_key = [], {}
    for item in selected:
        key = (item["sid"], item["fid"])
        g = by_key.get(key)
        if g is None:
            g = {"sid": item["sid"], "sname": item["sname"], "fid": item["fid"], "items": []}
            by_key[key] = g
            groups.append(g)
        g["items"].append(item)
    return render_template("compare.html", cfg=cfg, selected=selected,
                           sel_set=sel_set, groups=groups)


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
    return render_template(
        "station_detail.html",
        station=station,
        st=status.station_status(cfg, station),
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
    station["name"] = updated["name"]
    station["heartbeat_file"] = updated["heartbeat_file"]
    station["latitude"] = updated["latitude"]
    station["longitude"] = updated["longitude"]
    station["offline_window_min"] = updated["offline_window_min"]
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


@pages.post("/stations/<station_id>/files/<file_id>/path")
@admin_required
def file_update_path(station_id, file_id):
    """Atalho da tela 'Editar estação': troca só o caminho do arquivo monitorado."""
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    path = (request.form.get("path") or "").strip()
    if not path:
        flash("O caminho do arquivo não pode ficar vazio.", "error")
    else:
        fcfg["path"] = path
        _save(cfg)
        try:
            resolved = config_store.resolve_data_path(cfg, path)
            if resolved.is_file():
                flash(f"Caminho de '{file_id}' atualizado.", "ok")
            else:
                flash(
                    f"Caminho de '{file_id}' salvo, mas o arquivo não existe ainda: {resolved}",
                    "error",
                )
        except ValueError as exc:
            flash(f"Caminho salvo, mas inválido: {exc}", "error")
    return redirect(url_for("pages.station_edit", station_id=station_id))


@pages.post("/stations/<station_id>/files/<file_id>/delete")
@admin_required
def file_delete(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    station["files"] = [f for f in station.get("files", []) if f["id"] != file_id]
    _save(cfg)
    flash(f"Arquivo '{file_id}' removido do monitoramento (o arquivo em disco não foi tocado).", "ok")
    return redirect(url_for("pages.station_detail", station_id=station_id))
