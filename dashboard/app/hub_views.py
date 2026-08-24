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
        pause=hub.pause_state(cfg),
        areas=hub.AREAS,
    )


# --- Pausa da recepção -------------------------------------------------------------
# Botões espalhados pela interface (Coleta, estação do hub, detalhe da estação) postam
# aqui; ``back`` diz de qual tela vieram para o admin voltar ao lugar certo. Nunca é
# uma URL do formulário — só um rótulo mapeado em url_for, então não há redirect aberto.
def _pause_redirect(back: str, target: str):
    sid = target if hub.valid_hub_id(target) else ""
    if back == "hub_station" and sid:
        return redirect(url_for("hub.station", station_id=sid))
    if back == "detail" and sid:
        return redirect(url_for("pages.station_detail", station_id=sid))
    if back == "index":
        return redirect(url_for("pages.index"))
    return redirect(url_for("hub.index"))


@hub_pages.post("/pause")
@admin_required
def pause():
    """PAUSA a recepção (``rsync-hub pause``): as estações continuam autorizadas, os
    dados continuam no lugar — só a chegada de novos arquivos fica suspensa."""
    cfg = _load()
    target = (request.form.get("target") or "").strip().lower()
    ok, out = hub.set_pause(cfg, target, request.form.get("reason"))
    flash(out or ("Recepção pausada." if ok else "Falha ao pausar a recepção."),
          "ok" if ok else "error")
    return _pause_redirect(request.form.get("back") or "", target)


@hub_pages.post("/resume")
@admin_required
def resume():
    """RETOMA a recepção (``rsync-hub resume``). O que ficou pendente nas estações
    sobe no ciclo seguinte — elas reenviam o que não conseguiram entregar."""
    cfg = _load()
    target = (request.form.get("target") or "").strip().lower()
    ok, out = hub.clear_pause(cfg, target)
    flash(out or ("Recepção retomada." if ok else "Falha ao retomar a recepção."),
          "ok" if ok else "error")
    return _pause_redirect(request.form.get("back") or "", target)


@hub_pages.post("/align-root")
@admin_required
def align_root():
    """Alinha o ``data_root`` da dashboard com a BASE do rsync-hub instalado.

    Existe para o caminho que a UI não controla: uma migração feita PELO TERMINAL
    (``rsync-hub move-root``) muda /etc/rsync-hub.conf, mas não tem como mexer no
    config da dashboard — que continua lendo a raiz antiga, congelada. Faz o mesmo
    que a migração pela dashboard faria no fim: troca a raiz e reaponta os caminhos
    absolutos dos cadastros."""
    from . import config_store

    cfg = _load()
    conf_base = (hub.read_hub_conf().get("BASE") or "").strip()
    if not conf_base.startswith("/"):
        flash("Não consegui ler a BASE em /etc/rsync-hub.conf — ajuste a raiz à mão "
              "no campo acima.", "error")
        return redirect(url_for("pages.settings"))
    old_bases = []
    for cand in (str(config_store.data_root(cfg)),
                 cfg.get("data_root") if str(cfg.get("data_root", "")).startswith("/") else None):
        if cand and cand not in old_bases:
            old_bases.append(cand)
    if conf_base in old_bases:
        flash(f"A raiz da dashboard já é {conf_base} — nada a alinhar.", "ok")
        return redirect(url_for("pages.settings"))
    cfg["data_root"] = conf_base
    rewritten = sum(hub.rewrite_absolute_paths(cfg, ob, conf_base) for ob in old_bases)
    current_app.store.save(cfg)
    flash(f"Raiz da dashboard alinhada com o rsync-hub: {conf_base}."
          + (f" {rewritten} caminho(s) absoluto(s) de cadastros foram reapontados." if rewritten else ""),
          "ok")
    return redirect(url_for("pages.settings"))


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
        pause=hub.pause_state(cfg),
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


@hub_pages.post("/stations/<station_id>/purge")
@admin_required
def station_purge(station_id):
    """EXCLUSÃO DEFINITIVA da estação (revoga + apaga dados do servidor). Gate
    duplo: o formulário exige digitar o id da estação e ESTA rota revalida a
    digitação — o JavaScript que habilita o botão é só conveniência, a barreira
    real é aqui. Sucesso também remove a estação do cadastro do dashboard."""
    cfg = _load()
    typed = (request.form.get("confirm_name") or "").strip()
    if typed != station_id:
        flash("Exclusão NÃO executada: o nome digitado "
              f"('{typed or '—'}') não confere com '{station_id}'. Nada foi apagado.",
              "error")
        return redirect(url_for("hub.station", station_id=station_id))
    ok, out = hub.purge_station(cfg, station_id)
    if ok:
        cfg2 = _load()
        if any(s.get("id") == station_id for s in cfg2.get("stations", [])):
            cfg2["stations"] = [s for s in cfg2["stations"] if s.get("id") != station_id]
            current_app.store.save(cfg2)
            flash(f"'{station_id}' também foi removida do cadastro de monitoramento "
                  "do dashboard.", "ok")
    flash(out or ("Estação excluída." if ok else "Falha na exclusão."),
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
    # ?estacao=<id> — quem chegou aqui pela tela "Editar estação" volta para lá em vez
    # de cair no gerenciamento da Coleta. Validado contra o formato de id (nunca uma URL
    # arbitrária vinda do usuário), e o link é montado com url_for — sem redirect aberto.
    back = (request.args.get("estacao") or "").strip()
    if not hub.valid_hub_id(back):
        back = ""
    return render_template("hub_files.html", l=listing, areas=hub.AREAS,
                           back_station=back or None)


@hub_pages.get("/download/<area>/<path:subpath>")
@admin_required
def download(area, subpath):
    cfg = _load()
    path = hub.safe_resolve(cfg, area, subpath)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)
