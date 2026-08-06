"""Rotas de gerenciamento da coleta (/hub) — TUDO exige login de administração.

Diferente do resto da dashboard (visualização aberta), aqui até a leitura é
restrita: as páginas expõem credenciais das estações (chave privada, senha no
INSTRUCOES.txt) e download de qualquer arquivo do hub — nada disso deve vazar
para um visitante da rede interna sem login.
"""
from __future__ import annotations

import io

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from . import hub, status
from .views import admin_required

hub_pages = Blueprint("hub", __name__)


def _load():
    return current_app.store.load()


@hub_pages.app_template_filter("hsize")
def _hsize(n):
    return hub.fmt_size(n)


@hub_pages.get("/")
@admin_required
def index():
    cfg = _load()
    return render_template(
        "hub.html",
        info=hub.hub_info(cfg),
        stations=hub.list_hub_stations(cfg),
        areas=hub.AREAS,
    )


@hub_pages.post("/stations")
@admin_required
def station_add():
    cfg = _load()
    sid = (request.form.get("id") or "").strip().lower()
    mode = request.form.get("mode") or "chave"
    if mode == "senha":
        ok, out = hub.add_station_password(cfg, sid, request.form.get("password"))
    else:
        ok, out = hub.add_station_key(cfg, sid, (request.form.get("pubkey") or "").strip() or None)
    flash(out or ("Estação autorizada." if ok else "Falha ao autorizar."),
          "ok" if ok else "error")
    if ok:
        flash("Kit de ativação pronto: use o botão \"Baixar kit da estação (.zip)\" "
              "abaixo — chave, INSTRUCOES.txt e o cliente num pacote só.", "ok")
        return redirect(url_for("hub.station", station_id=sid))
    return redirect(url_for("hub.index"))


@hub_pages.get("/stations/<station_id>")
@admin_required
def station(station_id):
    cfg = _load()
    if not hub.valid_hub_id(station_id):
        abort(404)
    stations = {s["id"]: s for s in hub.list_hub_stations(cfg)}
    entry = stations.get(station_id)
    if entry is None:
        abort(404)
    # Status ao vivo com a mesma lógica das outras telas (heartbeat/mtimes/log).
    from . import config_store
    st = None
    dash_station = config_store.get_station(cfg, station_id)
    if dash_station:
        st = status.station_status(cfg, dash_station)
    return render_template(
        "hub_station.html",
        entry=entry,
        st=st,
        creds=hub.station_credentials(cfg, station_id),
        ak_line=hub.authorized_key_line(cfg, station_id),
        info=hub.hub_info(cfg),
        areas=hub.AREAS,
    )


@hub_pages.get("/stations/<station_id>/kit")
@admin_required
def station_kit(station_id):
    """Kit de ativação (.zip): credenciais do hub + clientes + LEIA-ME, num download
    só — é o pacote que o operador leva para a máquina da estação."""
    cfg = _load()
    if not hub.valid_hub_id(station_id):
        abort(404)
    data, err = hub.station_kit_zip(cfg, station_id)
    if data is None:
        flash(err, "error")
        return redirect(url_for("hub.station", station_id=station_id))
    return send_file(io.BytesIO(data), as_attachment=True, mimetype="application/zip",
                     download_name=f"carbonara-estacao-{station_id}.zip")


@hub_pages.post("/stations/<station_id>/remove")
@admin_required
def station_remove(station_id):
    cfg = _load()
    ok, out = hub.remove_station(cfg, station_id)
    flash(out or ("Acesso revogado." if ok else "Falha ao revogar."),
          "ok" if ok else "error")
    return redirect(url_for("hub.index") if ok
                    else url_for("hub.station", station_id=station_id))


@hub_pages.post("/move-root")
@admin_required
def move_root():
    """Migra a raiz de dados do hub (formulário em Configurações). Em caso de
    sucesso, o data_root do PRÓPRIO dashboard é atualizado junto — as pendências
    que continuam manuais (.env do compose) vêm na saída do comando, exibida ao
    admin no flash."""
    from . import config_store

    cfg = _load()
    new_root = (request.form.get("new_root") or "").strip()
    if not request.form.get("ack"):
        flash("Migração não executada: confirme que entendeu as pendências manuais "
              "(caixa de confirmação).", "error")
        return redirect(url_for("pages.settings"))
    # '/x/incoming' por engano → corrige para /x (a BASE é quem CONTÉM incoming/),
    # como o settings já faz — evita criar uma raiz .../incoming/incoming.
    new_root, adjusted = config_store.normalize_hub_root(new_root)
    # As BASEs antigas precisam ser capturadas ANTES do comando (depois o conf já
    # mudou). Vários candidatos porque os cadastros podem ter sido gravados com
    # qualquer grafia da raiz: a do /etc/rsync-hub.conf, o data_root literal do
    # config, ou a forma resolvida (symlinks — ex.: /var vs /private/var).
    old_bases = []
    for cand in (hub.read_hub_conf().get("BASE"),
                 cfg.get("data_root") if str(cfg.get("data_root", "")).startswith("/") else None,
                 str(hub.hub_base(cfg))):
        if cand and cand not in old_bases:
            old_bases.append(cand)
    ok, out = hub.move_root(cfg, new_root)
    if ok:
        cfg2 = _load()
        cfg2["data_root"] = new_root.rstrip("/")
        rewritten = sum(hub.rewrite_absolute_paths(cfg2, ob, new_root) for ob in old_bases)
        current_app.store.save(cfg2)
        if adjusted:
            flash(f"O caminho informado terminava em incoming/ — usei '{new_root}' "
                  "(a raiz do hub é a que CONTÉM incoming/).", "ok")
        if rewritten:
            flash(f"{rewritten} caminho(s) absoluto(s) de cadastros do dashboard "
                  "(watchdog/arquivos monitorados/log) foram reapontados para a nova raiz.", "ok")
    flash(out or ("Raiz migrada." if ok else "Falha na migração."),
          "ok" if ok else "error")
    return redirect(url_for("pages.settings"))


# --- Navegador de arquivos ---------------------------------------------------------
@hub_pages.get("/files/<area>/", defaults={"subpath": ""})
@hub_pages.get("/files/<area>/<path:subpath>")
@admin_required
def files(area, subpath):
    cfg = _load()
    listing = hub.list_dir(cfg, area, subpath)
    if listing is None:
        # Área válida mas pasta inexistente ainda (ex.: quarantine/ vazia): mostra
        # a raiz com aviso em vez de 404 seco.
        if hub.area_root(cfg, area) is None:
            abort(404)
        flash("Pasta não encontrada (ou fora da área permitida).", "error")
        listing = hub.list_dir(cfg, area, "") or {
            "area": area, "label": hub.AREAS[area][1], "root": str(hub.area_root(cfg, area)),
            "rel": "", "crumbs": [], "dirs": [], "files": [], "truncated": False,
            "max_entries": hub.MAX_LIST_ENTRIES, "missing": True,
        }
    return render_template("hub_files.html", l=listing, areas=hub.AREAS)


@hub_pages.get("/download/<area>/<path:subpath>")
@admin_required
def download(area, subpath):
    cfg = _load()
    path = hub.safe_resolve(cfg, area, subpath)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)
