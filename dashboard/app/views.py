"""Páginas HTML (Jinja) — lista de estações, detalhe, formulários e login de admin.

Modelo de acesso: VISUALIZAR é aberto (monitor de rede interna); qualquer EDIÇÃO
(cadastros, remoções, troca de caminho) exige login de administração, definido no
bloco ``auth:`` do config.yaml (hash de senha — nunca texto puro). Sem bloco
``auth:``, a edição fica liberada (modo desenvolvimento).
"""
from __future__ import annotations

import csv
import io
import re

import secrets
from functools import wraps

from flask import (
    Blueprint,
    Response,
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

from . import api_client, config_store, dbconn, hub, series, status

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


@pages.app_template_filter("pingms")
def _fmt_ping(v):
    """RTT em ms. Abaixo de 10 ms mostra uma decimal — num enlace local a diferença
    entre 0,4 ms e 3 ms é a informação toda, e arredondar para inteiro a apagaria."""
    if not v:
        return "—"
    return f"{v:.1f} ms" if v < 10 else f"{v:.0f} ms"


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
    stations = []
    for s in cfg["stations"]:
        st = status.station_status(cfg, s, pause)
        # Frescor dos arquivos monitorados (coluna de alerta). Calculado AQUI, e não
        # dentro de station_status, porque abre os arquivos de dados: /api/status é
        # consumido por outras telas a cada ciclo e não precisa desse custo.
        # Erro de leitura nunca pode derrubar a lista de estações.
        try:
            st["freshness"] = series.file_freshness(cfg, s)
        except Exception:  # noqa: BLE001
            st["freshness"] = {"files": [], "level": "none", "total": 0,
                               "ok_count": 0, "problems": [], "unknown": [],
                               "newest": None, "newest_age": None,
                               "tooltip": "falha ao ler os arquivos monitorados"}
        stations.append(st)
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
    cfg["api"] = updated["api"]
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
    # Estado da ingestão por arquivo registrado (ledger, via API). Tolerante: sem API
    # ou com ela fora do ar a página nasce igual, só sem o resumo "no banco".
    ingest_state = {}
    if api_client.configured(cfg) and any(f.get("ingest") for f in station.get("files", [])):
        try:
            for row in api_client.get(cfg, "/files", {"station": station_id}, timeout=4) or []:
                acc = ingest_state.setdefault(row["file_key"], {
                    "files": 0, "n_values": 0, "pending": 0, "last_time": None, "errors": []})
                acc["files"] += 1
                acc["n_values"] += row.get("values_total") or 0
                acc["pending"] += row.get("pending_bytes") or 0
                if row.get("last_time") and (acc["last_time"] is None or row["last_time"] > acc["last_time"]):
                    acc["last_time"] = row["last_time"]
                if row.get("status") == "error":
                    acc["errors"].append(f"{row.get('path', '').rsplit('/', 1)[-1]}: {row.get('error')}")
        except api_client.ApiError as exc:
            ingest_state = {"_error": str(exc)}
    return render_template(
        "station_detail.html",
        station=station,
        st=status.station_status(cfg, station),
        live_names=live_names,
        ingest_state=ingest_state,
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
    return render_template("file_form.html", station=station, fcfg=None,
                           data_types=config_store.known_data_types(cfg))


@pages.post("/stations/<station_id>/files")
@admin_required
def file_create(station_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    entry, errors = config_store.validate_file_entry(request.form, station=station)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("file_form.html", station=station, fcfg=None, data_types=config_store.known_data_types(cfg)), 400
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
    return render_template("file_form.html", station=station, fcfg=fcfg,
                           data_types=config_store.known_data_types(cfg))


@pages.post("/stations/<station_id>/files/<file_id>/edit")
@admin_required
def file_update(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    entry, errors = config_store.validate_file_entry(request.form, station=station,
                                                     file_id=file_id)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("file_form.html", station=station, fcfg=fcfg, data_types=config_store.known_data_types(cfg)), 400
    # merge sobre a entry existente: chaves adicionadas à mão no YAML sobrevivem à UI;
    # as escolhas da aba Tratamento por variável também (casadas por coluna/ID).
    entry["variables"] = config_store.carry_treatment(fcfg.get("variables") or [], entry["variables"])
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
    # `back` é um RÓTULO mapeado via url_for (mesma regra dos botões do hub) — nunca
    # uma URL vinda do cliente, para não abrir redirect arbitrário.
    if request.form.get("back") == "edit":
        return redirect(url_for("pages.station_edit", station_id=station_id))
    return redirect(url_for("pages.station_detail", station_id=station_id))


# --- Chaves da API de dados ---------------------------------------------------------
# A dashboard não guarda chaves nem fala com o banco: tudo passa pela própria API,
# com a chave de serviço da dashboard (escopo admin). A chave nova aparece UMA vez.
@pages.get("/api-keys")
@admin_required
def api_keys():
    cfg = _load()
    keys, error = [], None
    if api_client.configured(cfg):
        try:
            keys = api_client.get(cfg, "/admin/keys") or []
        except api_client.ApiError as exc:
            error = str(exc)
    new_key = session.pop("new_api_key", None)
    return render_template("api_keys.html", cfg=cfg, keys=keys, error=error,
                           new_key=new_key, api_url=api_client.public_url(cfg))


@pages.post("/api-keys")
@admin_required
def api_keys_create():
    cfg = _load()
    scopes = [s for s in request.form.getlist("scopes") if s in ("read", "download", "admin")]
    try:
        days = int(request.form.get("days") or 365)
        rate = int(request.form.get("rate_limit") or 120)
    except ValueError:
        flash("Validade e limite devem ser números.", "error")
        return redirect(url_for("pages.api_keys"))
    body = {"owner": (request.form.get("owner") or "").strip() or "sem-nome",
            "scopes": scopes or ["read"], "rate_limit": rate, "days": days if days > 0 else None}
    try:
        created = api_client.post(cfg, "/admin/keys", body)
    except api_client.ApiError as exc:
        flash(f"Não foi possível criar a chave: {exc}", "error")
        return redirect(url_for("pages.api_keys"))
    session["new_api_key"] = {"key": created.get("key"), "owner": created.get("owner"),
                              "prefix": created.get("prefix")}
    flash(f"Chave criada para '{created.get('owner')}'. Copie-a AGORA: ela não será mostrada de novo.", "ok")
    return redirect(url_for("pages.api_keys"))


@pages.post("/api-keys/<int:key_id>/revoke")
@admin_required
def api_keys_revoke(key_id: int):
    cfg = _load()
    try:
        api_client.delete(cfg, f"/admin/keys/{key_id}")
        flash(f"Chave #{key_id} revogada.", "ok")
    except api_client.ApiError as exc:
        flash(f"Não foi possível revogar: {exc}", "error")
    return redirect(url_for("pages.api_keys"))


# --- Aba Tratamento --------------------------------------------------------------
# Índice compacto (estação → arquivos, com resumo) e UMA página por arquivo para as
# escolhas: ingerir? fuso? reamostrar? e, por variável, entra? ID, faixa (flag 2), salto
# (flag 3), travado (flag 4). Grava no MESMO registro que o formulário do arquivo usa —
# a ingestão relê o config a cada ciclo.
def _ledger_by_file(cfg: dict) -> dict:
    """{(station_id, file_key): resumo} do ledger da ingestão, via API (tolerante)."""
    out: dict = {}
    if not api_client.configured(cfg):
        return out
    try:
        rows = api_client.get(cfg, "/files", timeout=6) or []
    except api_client.ApiError as exc:
        return {"_error": str(exc)}
    for row in rows:
        acc = out.setdefault((row["station_id"], row["file_key"]), {
            "files": 0, "n_values": 0, "pending": 0, "last_time": None, "errors": []})
        acc["files"] += 1
        acc["n_values"] += row.get("values_total") or 0
        acc["pending"] += row.get("pending_bytes") or 0
        if row.get("last_time") and (acc["last_time"] is None or row["last_time"] > acc["last_time"]):
            acc["last_time"] = row["last_time"]
        if row.get("status") == "error":
            acc["errors"].append(row.get("error"))
    return out


def _treat_summary(f: dict) -> dict:
    vs = [v for v in f.get("variables") or [] if (v.get("type") or "numero") != "texto"]
    no_banco = [v for v in vs if v.get("ingest") is not False]
    return {"total": len(vs), "no_banco": len(no_banco),
            "faixa": sum(1 for v in no_banco if v.get("min") is not None or v.get("max") is not None),
            "salto": sum(1 for v in no_banco if v.get("qc_spike") is not None),
            "travado": sum(1 for v in no_banco if v.get("qc_stuck"))}


@pages.get("/tratamento")
@admin_required
def treatment():
    cfg = _load()
    ledger = _ledger_by_file(cfg)
    resumo = {(s["id"], f["id"]): _treat_summary(f) for s in cfg["stations"] for f in s.get("files") or []}
    for st in cfg["stations"]:
        for f in st.get("files") or []:
            f["_tipo"] = config_store.data_type_of(f)
    return render_template("tratamento.html", cfg=cfg, ledger=ledger, resumo=resumo,
                           api_ok=api_client.configured(cfg))


@pages.get("/tratamento/<station_id>/<file_id>")
@admin_required
def treatment_file(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    ledger = _ledger_by_file(cfg)
    return render_template("tratamento_arquivo.html", cfg=cfg, station=station, f=fcfg,
                           lg=ledger.get((station_id, file_id)), resumo=_treat_summary(fcfg),
                           api_ok=api_client.configured(cfg), data_types=config_store.known_data_types(cfg))


@pages.post("/tratamento/<station_id>/<file_id>")
@admin_required
def treatment_save(station_id, file_id):
    cfg = _load()
    station = _station_or_404(cfg, station_id)
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        abort(404)
    novo, errors = config_store.apply_treatment_form(request.form, station, fcfg)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("pages.treatment_file", station_id=station_id, file_id=file_id))
    station["files"] = [novo if f["id"] == file_id else f for f in station["files"]]
    _save(cfg)
    flash(f"Tratamento de {file_id} salvo" + (" — no banco." if novo.get("ingest") else " — fora do banco."), "ok")
    if request.form.get("back") == "file":
        return redirect(url_for("pages.treatment_file", station_id=station_id, file_id=file_id))
    return redirect(url_for("pages.treatment"))


# --- Aba Banco (estilo phpMyAdmin: bancos → tabelas → dados/estrutura/inserir/SQL) -----
_SQL_EXEMPLOS = [
    ("Últimos valores de uma estação",
     "SELECT variable_id, time, value, quality FROM measurements\nWHERE station_id = 'eddyfarm' ORDER BY time DESC LIMIT 50"),
    ("Tabela larga da estação (view gerada)",
     "SELECT * FROM \"eddyfarm\".\"cr1000_proc3k\" ORDER BY time DESC LIMIT 20"),
    ("Contagem por estação e variável",
     "SELECT station_id, variable_id, count(*) AS n, min(time), max(time)\nFROM measurements GROUP BY 1, 2 ORDER BY 1, 2"),
    ("Média diária (agregado contínuo)",
     "SELECT bucket, variable_id, avg_value, n_samples, n_flagged FROM measurements_daily\nWHERE station_id = 'eddyfarm' ORDER BY bucket DESC LIMIT 100"),
    ("Valores com flag de qualidade",
     "SELECT quality, variable_id, count(*) FROM measurements\nWHERE quality <> 0 GROUP BY 1, 2 ORDER BY 3 DESC"),
    ("Ledger da ingestão",
     "SELECT station_id, file_key, relpath, size_bytes, bytes_ingested, rows_total, last_time, status\nFROM ingest_files ORDER BY station_id, relpath"),
]
_COLUMN_TYPES = [("texto", "Texto"), ("numero", "Número (decimal)"), ("inteiro", "Inteiro"),
                 ("booleano", "Sim/Não"), ("data_hora", "Data e hora (com fuso)"),
                 ("data", "Data"), ("json", "JSON")]


def _banco_base(cfg: dict) -> dict:
    """Contexto comum das páginas do banco: lista de bancos (barra lateral)."""
    ctx = {"api_ok": api_client.configured(cfg), "schemas": [], "error": None,
           "exemplos": _SQL_EXEMPLOS, "column_types": _COLUMN_TYPES}
    if not ctx["api_ok"]:
        return ctx
    try:
        ctx["schemas"] = api_client.get(cfg, "/admin/db/schemas", timeout=20) or []
    except api_client.ApiError as exc:
        ctx["error"] = str(exc)
    return ctx


_ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?(\+00:00|Z)?$")


def _pretty_rows(rows: list) -> list:
    """Carimbos ISO ('2026-06-27T16:30:00+00:00') viram '2026-06-27 16:30:00' nas grades
    (o banco está em UTC; a grade diz isso no cabeçalho)."""
    out = []
    for r in rows:
        nr = []
        for v in r:
            if isinstance(v, str):
                m = _ISO_TS.match(v)
                if m:
                    v = f"{m.group(1)} {m.group(2)}" + ((m.group(3) or "")[:4] if m.group(3) and m.group(3) not in (".000", ".000000") else "")
            nr.append(v)
        out.append(nr)
    return out


def _api_or_flash(cfg, fn, *a, **kw):
    try:
        return fn(cfg, *a, **kw)
    except api_client.ApiError as exc:
        flash(str(exc), "error")
        return None


@pages.get("/banco")
@admin_required
def banco():
    cfg = _load()
    ctx = _banco_base(cfg)
    overview = coverage = None
    if ctx["api_ok"] and not ctx["error"]:
        overview = _api_or_flash(cfg, api_client.get, "/admin/db/overview", timeout=20)
        coverage = _api_or_flash(cfg, api_client.get, "/admin/db/coverage", timeout=20)
    return render_template("banco.html", cfg=cfg, overview=overview, coverage=coverage,
                           sql="", result=None, current=None, **ctx)


@pages.post("/banco/query")
@admin_required
def banco_query():
    cfg = _load()
    ctx = _banco_base(cfg)
    sql = (request.form.get("sql") or "").strip()
    result = None
    if sql and ctx["api_ok"]:
        try:
            result = api_client.post(cfg, "/admin/db/query", {"sql": sql, "max_rows": 200}, timeout=40)
            result["rows"] = _pretty_rows(result.get("rows") or [])
        except api_client.ApiError as exc:
            result = {"error": str(exc)}
    return render_template("banco.html", cfg=cfg, overview=None, coverage=None,
                           sql=sql, result=result, current=None, **ctx)


@pages.post("/banco/novo-banco")
@admin_required
def banco_new_schema():
    cfg = _load()
    name = (request.form.get("name") or "").strip().lower()
    if _api_or_flash(cfg, api_client.post, "/admin/db/schemas",
                     {"name": name, "comment": (request.form.get("comment") or "").strip()}):
        flash(f"Banco '{name}' criado.", "ok")
        return redirect(url_for("pages.banco_schema", schema=name))
    return redirect(url_for("pages.banco"))


@pages.get("/banco/<schema>")
@admin_required
def banco_schema(schema):
    cfg = _load()
    ctx = _banco_base(cfg)
    tables = _api_or_flash(cfg, api_client.get, "/admin/db/tables", {"schema": schema}, timeout=30) or []
    info = next((s for s in ctx["schemas"] if s["schema"] == schema), None)
    if info is None and ctx["api_ok"] and not ctx["error"]:
        abort(404)
    return render_template("banco_schema.html", cfg=cfg, schema=schema, info=info, tables=tables,
                           current=schema, **ctx)


@pages.post("/banco/<schema>/nova-tabela")
@admin_required
def banco_new_table(schema):
    cfg = _load()
    cols = []
    for i in range(20):
        name = (request.form.get(f"c{i}_name") or "").strip().lower()
        if not name:
            continue
        cols.append({"name": name, "type": request.form.get(f"c{i}_type") or "texto",
                     "nullable": not request.form.get(f"c{i}_notnull"), "pk": bool(request.form.get(f"c{i}_pk"))})
    body = {"schema": schema, "name": (request.form.get("name") or "").strip().lower(), "columns": cols,
            "comment": (request.form.get("comment") or "").strip(),
            "hypertable": bool(request.form.get("hypertable"))}
    if not cols:
        flash("Defina pelo menos uma coluna.", "error")
        return redirect(url_for("pages.banco_schema", schema=schema))
    if _api_or_flash(cfg, api_client.post, "/admin/db/tables", body):
        flash(f"Tabela '{body['name']}' criada.", "ok")
        return redirect(url_for("pages.banco_table", schema=schema, table=body["name"]))
    return redirect(url_for("pages.banco_schema", schema=schema))


@pages.get("/banco/<schema>/<table>")
@admin_required
def banco_table(schema, table):
    cfg = _load()
    ctx = _banco_base(cfg)
    tab = request.args.get("aba") or "dados"
    params = {"schema": schema, "table": table, "limit": 50,
              "offset": int(request.args.get("offset") or 0), "sort": request.args.get("sort"),
              "dir": request.args.get("dir") or "desc", "q": request.args.get("q") or ""}
    structure = _api_or_flash(cfg, api_client.get, "/admin/db/columns", {"schema": schema, "table": table}, timeout=30)
    data = None
    if structure and tab == "dados":
        data = _api_or_flash(cfg, api_client.get, "/admin/db/rows", params, timeout=60)
        if data:
            data["rows"] = _pretty_rows(data.get("rows") or [])
    return render_template("banco_table.html", cfg=cfg, schema=schema, table=table, tab=tab,
                           structure=structure, data=data, params=params, current=schema, **ctx)


@pages.post("/banco/<schema>/<table>/inserir")
@admin_required
def banco_insert(schema, table):
    cfg = _load()
    values = {k[4:]: v for k, v in request.form.items() if k.startswith("col_")}
    if _api_or_flash(cfg, api_client.post, "/admin/db/rows", {"schema": schema, "table": table, "values": values}):
        flash("Linha inserida.", "ok")
        return redirect(url_for("pages.banco_table", schema=schema, table=table))
    return redirect(url_for("pages.banco_table", schema=schema, table=table, aba="inserir"))


@pages.post("/banco/<schema>/<table>/apagar-linha")
@admin_required
def banco_delete_row(schema, table):
    cfg = _load()
    ctid = (request.form.get("ctid") or "").strip()
    try:
        api_client.delete_json(cfg, "/admin/db/rows", {"schema": schema, "table": table, "ctid": ctid})
        flash("Linha apagada.", "ok")
    except api_client.ApiError as exc:
        flash(str(exc), "error")
    return redirect(url_for("pages.banco_table", schema=schema, table=table,
                            offset=request.form.get("offset") or 0, q=request.form.get("q") or ""))


@pages.post("/banco/<schema>/<table>/excluir")
@admin_required
def banco_drop_table(schema, table):
    cfg = _load()
    try:
        api_client.delete_json(cfg, "/admin/db/tables",
                               {"schema": schema, "table": table, "confirm": (request.form.get("confirm") or "").strip()})
        flash(f"Tabela '{table}' excluída.", "ok")
        return redirect(url_for("pages.banco_schema", schema=schema))
    except api_client.ApiError as exc:
        flash(str(exc), "error")
        return redirect(url_for("pages.banco_table", schema=schema, table=table, aba="estrutura"))


# --- Banco: conexão, diagnóstico/manutenção, exportação; Guia para pesquisadores -------
@pages.get("/banco/conexao")
@admin_required
def banco_conexao():
    cfg = _load()
    ctx = _banco_base(cfg)
    server = _api_or_flash(cfg, api_client.get, "/admin/db/connection", timeout=20) if ctx["api_ok"] else None
    dock_ok, dock_msg = dbconn.docker_ok()
    _ok, svc = dbconn.services_status() if dock_ok else (False, "")
    return render_template("banco_conexao.html", cfg=cfg, conn=dbconn.current(), server=server,
                           docker_ok=dock_ok, docker_msg=dock_msg, services=svc, current="conexao", **ctx)


@pages.post("/banco/conexao")
@admin_required
def banco_conexao_save():
    mode = request.form.get("mode") or "local"
    host = (request.form.get("host") or "").strip()
    port = (request.form.get("port") or "5432").strip()
    name = (request.form.get("name") or "carbonara").strip()
    user = (request.form.get("user") or "carbonara").strip()
    password = (request.form.get("password") or "").strip()
    acao = request.form.get("acao") or "salvar"
    if mode == "local":
        host, port = "db", "5432"
    if not host or not port.isdigit() or not re.match(r"^[a-z_][a-z0-9_]*$", name) or not re.match(r"^[a-z_][a-z0-9_]*$", user):
        flash("Confira host, porta (número), banco e usuário (minúsculas, números e '_').", "error")
        return redirect(url_for("pages.banco_conexao"))
    if acao == "testar":
        ok, out = dbconn.test_connection(host, port, name, user, password or None)
        flash(("Conexão OK — " if ok else "Falha na conexão — ") + out[-600:], "ok" if ok else "error")
        return redirect(url_for("pages.banco_conexao"))
    updates = {"COMPOSE_PROFILES": "local-db" if mode == "local" else "", "DB_HOST": host,
               "DB_INTERNAL_PORT": port, "DB_NAME": name, "DB_USER": user}
    if password:
        updates["DB_PASSWORD"] = password
    try:
        dbconn.write_env(updates)
    except OSError as exc:
        flash(f"Não consegui gravar o .env: {exc}", "error")
        return redirect(url_for("pages.banco_conexao"))
    flash(f"Conexão gravada em {dbconn.env_path()} (modo {mode}, {host}:{port}/{name}). "
          "Clique em 'Aplicar aos serviços' para a API e a ingestão passarem a usar.", "ok")
    return redirect(url_for("pages.banco_conexao"))


@pages.post("/banco/conexao/aplicar")
@admin_required
def banco_conexao_apply():
    acao = request.form.get("acao") or "aplicar"
    if acao == "parar-db-local":
        ok, out = dbconn.stop_local_db()
        flash(("Banco local parado.\n" if ok else "Falha ao parar o banco local.\n") + out, "ok" if ok else "error")
    else:
        ok, out = dbconn.apply(dbconn.current()["mode"])
        flash(("Serviços atualizados.\n" if ok else "Falha ao aplicar.\n") + out, "ok" if ok else "error")
    return redirect(url_for("pages.banco_conexao"))


_MAINT_ACTIONS = {
    "analyze": ("Atualizar estatísticas (ANALYZE)", "rápido; melhora os planos de consulta"),
    "vacuum": ("VACUUM ANALYZE", "recupera espaço de linhas apagadas; pode levar minutos"),
    "compress": ("Comprimir chunks antigos", "aplica agora a compressão dos dados com mais de 30 dias"),
    "refresh_aggregates": ("Recalcular agregados (30 dias)", "refaz as médias horárias/diárias do período"),
    "purge_implausible_times": ("Remover carimbos implausíveis", "APAGA linhas antes de 1990 ou no futuro"),
    "purge_orphans": ("Remover séries órfãs", "APAGA séries sem registro ativo na dashboard"),
}


@pages.get("/banco/diagnostico")
@admin_required
def banco_diagnostico():
    cfg = _load()
    ctx = _banco_base(cfg)
    diag = _api_or_flash(cfg, api_client.get, "/admin/db/diagnostics", timeout=120) if ctx["api_ok"] else None
    return render_template("banco_diagnostico.html", cfg=cfg, diag=diag, actions=_MAINT_ACTIONS,
                           current="diagnostico", **ctx)


@pages.post("/banco/manutencao")
@admin_required
def banco_manutencao():
    cfg = _load()
    action = request.form.get("action") or ""
    if action not in _MAINT_ACTIONS:
        flash("Ação desconhecida.", "error")
        return redirect(url_for("pages.banco_diagnostico"))
    if action.startswith("purge") and request.form.get("confirm") != "APAGAR":
        flash("Para ações que apagam dados, digite APAGAR no campo de confirmação.", "error")
        return redirect(url_for("pages.banco_diagnostico"))
    try:
        days = int(request.form.get("days") or 30)
    except ValueError:
        days = 30
    res = _api_or_flash(cfg, api_client.post, "/admin/db/maintenance", {"action": action, "days": days}, timeout=900)
    if res:
        flash(f"{_MAINT_ACTIONS[action][0]}: {res.get('detail')} ({res.get('elapsed_ms')} ms).", "ok")
    return redirect(url_for("pages.banco_diagnostico"))


@pages.get("/banco/<schema>/<table>/exportar")
@admin_required
def banco_export(schema, table):
    """CSV da tabela/view. View gerada (arquivo de uma estação): usa /series da API no
    período pedido (formato longo: time, station, variable, value, quality). Demais:
    até 500 linhas da grade."""
    cfg = _load()
    structure = _api_or_flash(cfg, api_client.get, "/admin/db/columns", {"schema": schema, "table": table}, timeout=30)
    if not structure:
        return redirect(url_for("pages.banco_table", schema=schema, table=table))
    start = request.args.get("start") or "now-30d"
    end = request.args.get("end") or "now"
    try:
        if structure.get("generated"):
            vids = [c["name"] for c in structure["columns"] if c["name"] not in ("time", "flags")]
            data = api_client.get_bytes(cfg, "/series", {"station": _station_of(schema), "vars": ",".join(vids), "agg": "raw",
                                                          "qc": "all", "start": start, "end": end, "format": "csv",
                                                          "limit": 200000})
        else:
            rows = api_client.get(cfg, "/admin/db/rows", {"schema": schema, "table": table, "limit": 500}, timeout=60)
            if not rows["columns"]:                       # tabela vazia: cabeçalho pela estrutura
                rows["columns"] = [c["name"] for c in structure["columns"]]
            cols = [c for c in rows["columns"] if c != "_ctid"]
            skip = 1 if "_ctid" in rows["columns"] else 0
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for r in rows["rows"]:
                w.writerow(r[skip:])
            data = buf.getvalue().encode("utf-8")
    except api_client.ApiError as exc:
        flash(f"Exportação falhou: {exc}", "error")
        return redirect(url_for("pages.banco_table", schema=schema, table=table))
    resp = Response(data, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{schema}_{table}.csv"'
    return resp


@pages.get("/guia")
def guia():
    """Guia para pesquisadores: como acessar os dados (API, chaves, flags, exemplos)."""
    cfg = _load()
    stations = []
    if api_client.configured(cfg):
        try:
            stations = api_client.get(cfg, "/stations", timeout=10) or []
        except api_client.ApiError:
            stations = []
    return render_template("guia.html", cfg=cfg, stations=stations, api_url=api_client.public_url(cfg) or "http://<servidor>:8000")


# --- Banco: zona de perigo (sempre com texto de confirmação, validado também na API) -------
def _danger(cfg, path, body, ok_msg, back):
    try:
        res = api_client.post(cfg, path, body, timeout=600)
        flash(ok_msg.format(**res), "ok")
    except api_client.ApiError as exc:
        flash(str(exc), "error")
    return redirect(back)


def _station_of(schema: str) -> str:
    """Schema de estação+tipo ('eddyfarm__radiometro') → id da estação."""
    return schema.split("__", 1)[0]


@pages.post("/banco/<schema>/<table>/zerar")
@admin_required
def banco_station_clear(schema, table):
    cfg = _load()
    back = url_for("pages.banco_table", schema=schema, table=table, aba="perigo")
    return _danger(cfg, "/admin/db/station/clear",
                   {"station": _station_of(schema), "file_key": table, "confirm": request.form.get("confirm", "")},
                   "Tabela zerada: {deleted} valor(es) removido(s); a ingestão relê o arquivo do início.", back)


@pages.post("/banco/<schema>/<table>/apagar-variavel")
@admin_required
def banco_station_drop_variable(schema, table):
    cfg = _load()
    back = url_for("pages.banco_table", schema=schema, table=table, aba="perigo")
    vid = (request.form.get("variable_id") or "").strip()
    return _danger(cfg, "/admin/db/station/drop-variable",
                   {"station": _station_of(schema), "variable_id": vid, "confirm": request.form.get("confirm", "")},
                   "Variável '" + vid + "' apagada: {deleted} valor(es). {note}.", back)


@pages.post("/banco/<schema>/<table>/apagar-tabela")
@admin_required
def banco_station_drop_table(schema, table):
    cfg = _load()
    try:
        res = api_client.post(cfg, "/admin/db/station/drop-table",
                              {"station": _station_of(schema), "file_key": table, "confirm": request.form.get("confirm", "")}, timeout=600)
    except api_client.ApiError as exc:
        flash(str(exc), "error")
        return redirect(url_for("pages.banco_table", schema=schema, table=table, aba="perigo"))
    # Desliga a ingestão no REGISTRO da dashboard também — senão o próximo ciclo recria a view.
    station = config_store.get_station(cfg, _station_of(schema))
    if station:
        for f in station.get("files") or []:
            if f.get("id") == table:
                f["ingest"] = False
        _save(cfg)
    flash(f"Tabela {schema}.{table} apagada: {res.get('deleted')} valor(es) removido(s), view removida e ingestão "
          "do arquivo desligada (o arquivo em disco e o monitoramento dos gráficos continuam).", "ok")
    return redirect(url_for("pages.banco_schema", schema=schema))


@pages.post("/banco/<schema>/<table>/truncar")
@admin_required
def banco_truncate(schema, table):
    cfg = _load()
    back = url_for("pages.banco_table", schema=schema, table=table, aba="perigo")
    return _danger(cfg, "/admin/db/tables/truncate",
                   {"schema": schema, "table": table, "confirm": request.form.get("confirm", "")},
                   "Tabela {truncated} esvaziada.", back)


@pages.post("/banco/<schema>/<table>/apagar-coluna")
@admin_required
def banco_drop_column(schema, table):
    cfg = _load()
    back = url_for("pages.banco_table", schema=schema, table=table, aba="perigo")
    return _danger(cfg, "/admin/db/tables/drop-column",
                   {"schema": schema, "table": table, "column": (request.form.get("column") or "").strip(),
                    "confirm": request.form.get("confirm", "")},
                   "Coluna {dropped_column} apagada.", back)
