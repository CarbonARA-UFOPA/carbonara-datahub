"""Rotas do gerenciamento do FTP (/ftp) — TUDO exige login de administração.

Como em /hub, até a leitura é restrita: a página lista contas, pastas e atividade
de login — nada disso é para um visitante da rede interna sem login.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import ftp
from .ftp_publish import ftp_fmt_bytes
from .i18n import gettext as _
from .views import admin_required

ftp_pages = Blueprint("ftp", __name__)


def _load():
    return current_app.store.load()


def _publisher():
    return current_app.publisher


@ftp_pages.app_template_filter("fbytes")
def _fbytes(v):
    return ftp_fmt_bytes(v)


@ftp_pages.before_request
def _same_origin_only():
    """Barreira leve contra CSRF nas ações (POST): o navegador diz de onde veio
    (``Sec-Fetch-Site``/``Origin``); uma página de outro site não consegue disparar
    "remover conta" na sessão do admin. Formulários da própria dashboard passam."""
    if request.method != "POST":
        return None
    site = request.headers.get("Sec-Fetch-Site")
    if site and site not in ("same-origin", "none"):
        abort(403)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/").split("://", 1)[-1] != request.host:
        abort(403)
    return None


# --- Página principal --------------------------------------------------------------------
@ftp_pages.get("/")
@admin_required
def index():
    cfg = _load()
    info = ftp.server_info(cfg)
    rules = ftp.publications(cfg)
    reveal = session.pop("ftp_reveal", None)
    # Gerenciador de pastas: ?pasta=<sub> escolhe o nível exibido (server-side, sem estado JS).
    folder = ftp.folder_listing(cfg, request.args.get("pasta") or "")
    if folder is None:
        flash(_("Pasta não encontrada no FTP (ou fora da raiz)."), "error")
        folder = ftp.folder_listing(cfg, "")
    return render_template(
        "ftp.html",
        info=info,
        accounts=ftp.list_accounts(cfg),
        profiles=ftp.PROFILES,
        pubs=_publisher().describe(cfg, rules),
        links=ftp.links(cfg),
        folder=folder,
        pick_areas=ftp.PICK_AREAS,
        log_lines=_publisher().tail_log(60),
        reveal=reveal,
    )


def _back_to_folder(rel: str | None):
    return redirect(url_for("ftp.index", pasta=rel or None) + "#pastas")


# --- Pastas do FTP (organização) ---------------------------------------------------------
@ftp_pages.post("/pastas")
@admin_required
def folder_create():
    parent = request.form.get("parent") or ""
    ok, out, rel = ftp.create_folder(_load(), parent, request.form.get("name") or "")
    flash(out, "ok" if ok else "error")
    return _back_to_folder(parent)


@ftp_pages.post("/pastas/remover")
@admin_required
def folder_remove():
    sub = request.form.get("sub") or ""
    ok, out = ftp.remove_folder(_load(), sub)
    flash(out, "ok" if ok else "error")
    parent = "/".join(sub.strip("/").split("/")[:-1])
    return _back_to_folder(parent)


@ftp_pages.post("/pastas/apagar")
@admin_required
def folder_delete():
    """Apaga uma pasta COM conteúdo — confirmação digitada re-conferida em ftp.py."""
    sub = request.form.get("sub") or ""
    ok, out = ftp.delete_folder(_load(), sub, request.form.get("confirm") or "")
    flash(out, "ok" if ok else "error")
    parent = "/".join(sub.strip("/").split("/")[:-1])
    return _back_to_folder(parent if ok else sub)


# --- Vínculos (bind mounts) ----------------------------------------------------------------
@ftp_pages.post("/vinculos")
@admin_required
def link_create():
    ok, out = ftp.add_link(_load(), request.form.get("dest") or "",
                           request.form.get("source") or "", bool(request.form.get("rw")))
    _flash_result(ok, out, _("Vínculo criado."), _("Falha ao criar o vínculo."))
    return redirect(url_for("ftp.index") + "#vinculos")


@ftp_pages.post("/vinculos/remover")
@admin_required
def link_remove():
    ok, out = ftp.remove_link(_load(), request.form.get("dest") or "")
    _flash_result(ok, out, _("Vínculo desfeito."), _("Falha ao desfazer o vínculo."))
    return redirect(url_for("ftp.index") + "#vinculos")


# --- Contas ------------------------------------------------------------------------------
def _flash_result(ok: bool, out: str, default_ok: str, default_err: str) -> None:
    flash(out or (default_ok if ok else default_err), "ok" if ok else "error")


@ftp_pages.post("/contas")
@admin_required
def account_add():
    cfg = _load()
    user = (request.form.get("user") or "").strip().lower()
    ok, out, pw = ftp.add_account(cfg, user, request.form.get("profile") or "",
                                  request.form.get("folder") or "",
                                  request.form.get("password") or None)
    _flash_result(ok, out, _("Conta criada."), _("Falha ao criar a conta."))
    if ok and pw:
        # Mostrada UMA vez, na próxima página (a sessão é assinada; não fica em log).
        session["ftp_reveal"] = {"user": user, "password": pw}
    return redirect(url_for("ftp.account", user=user) if ok else url_for("ftp.index"))


@ftp_pages.get("/contas/<user>")
@admin_required
def account(user):
    cfg = _load()
    acc = ftp.get_account(cfg, user)
    if acc is None:
        abort(404)
    info = ftp.server_info(cfg)
    reveal = session.pop("ftp_reveal", None)
    if reveal and reveal.get("user") != user:
        reveal = None
    return render_template("ftp_account.html", acc=acc, info=info, profiles=ftp.PROFILES,
                           reveal=reveal)


@ftp_pages.post("/contas/<user>/senha")
@admin_required
def account_password(user):
    cfg = _load()
    ok, out, pw = ftp.set_password(cfg, user, request.form.get("password") or None)
    _flash_result(ok, out, _("Senha alterada."), _("Falha ao alterar a senha."))
    if ok and pw:
        session["ftp_reveal"] = {"user": user, "password": pw}
    return redirect(url_for("ftp.account", user=user))


@ftp_pages.post("/contas/<user>/perfil")
@admin_required
def account_profile(user):
    ok, out = ftp.set_profile(_load(), user, request.form.get("profile") or "")
    _flash_result(ok, out, _("Perfil alterado."), _("Falha ao alterar o perfil."))
    return redirect(url_for("ftp.account", user=user))


@ftp_pages.post("/contas/<user>/pasta")
@admin_required
def account_folder(user):
    ok, out = ftp.set_folder(_load(), user, request.form.get("folder") or "")
    _flash_result(ok, out, _("Pasta alterada."), _("Falha ao alterar a pasta."))
    return redirect(url_for("ftp.account", user=user))


@ftp_pages.post("/contas/<user>/estado")
@admin_required
def account_state(user):
    enable = (request.form.get("action") or "") == "ativar"
    ok, out = ftp.set_enabled(_load(), user, enable)
    _flash_result(ok, out, _("Estado alterado."), _("Falha ao alterar o estado."))
    return redirect(url_for("ftp.account", user=user))


@ftp_pages.post("/contas/<user>/remover")
@admin_required
def account_remove(user):
    """Remove a CONTA (não os dados). Gate duplo: o formulário pede o nome digitado e
    esta rota revalida — o JavaScript que habilita o botão é só conveniência."""
    typed = (request.form.get("confirm_name") or "").strip()
    if typed != user:
        flash(_("Remoção NÃO executada: o nome digitado ('%(typed)s') não confere "
                "com '%(user)s'.", typed=typed or "—", user=user), "error")
        return redirect(url_for("ftp.account", user=user))
    ok, out = ftp.remove_account(_load(), user)
    _flash_result(ok, out, _("Conta removida."), _("Falha ao remover a conta."))
    return redirect(url_for("ftp.index") if ok else url_for("ftp.account", user=user))


# --- Publicações --------------------------------------------------------------------------
@ftp_pages.get("/publicacoes/nova")
@admin_required
def publication_new():
    cfg = _load()
    rule = {"id": "", "name": "", "source": "", "dest": "", "include": [], "exclude": [],
            "delete": False, "hidden": False, "interval_min": 60, "enabled": True}
    return render_template("ftp_publication_form.html", rule=rule, is_new=True,
                           info=ftp.server_info(cfg), pick_areas=ftp.PICK_AREAS)


@ftp_pages.post("/publicacoes")
@admin_required
def publication_create():
    cfg = _load()
    rule, errors = ftp.validate_publication(request.form, cfg)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("ftp_publication_form.html", rule=rule, is_new=True,
                               info=ftp.server_info(cfg), pick_areas=ftp.PICK_AREAS), 400
    ftp.save_publication(current_app.store, cfg, rule)
    flash(_("Publicação '%(name)s' salva.", name=rule["name"]) + " "
          + (_("Ela roda no próximo ciclo do publicador.") if rule["enabled"] and rule["interval_min"]
             else _("Use 'publicar agora' para executá-la.")), "ok")
    return redirect(url_for("ftp.index"))


@ftp_pages.get("/publicacoes/<pid>/editar")
@admin_required
def publication_edit(pid):
    cfg = _load()
    rule = ftp.get_publication(cfg, pid)
    if rule is None:
        abort(404)
    return render_template("ftp_publication_form.html", rule=rule, is_new=False,
                           info=ftp.server_info(cfg), pick_areas=ftp.PICK_AREAS)


@ftp_pages.post("/publicacoes/<pid>")
@admin_required
def publication_update(pid):
    cfg = _load()
    if ftp.get_publication(cfg, pid) is None:
        abort(404)
    rule, errors = ftp.validate_publication(request.form, cfg, current_id=pid)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("ftp_publication_form.html", rule=rule, is_new=False,
                               info=ftp.server_info(cfg), pick_areas=ftp.PICK_AREAS), 400
    ftp.save_publication(current_app.store, cfg, rule)
    flash(_("Publicação '%(name)s' atualizada.", name=rule["name"]), "ok")
    return redirect(url_for("ftp.index"))


@ftp_pages.post("/publicacoes/<pid>/executar")
@admin_required
def publication_run(pid):
    cfg = _load()
    rule = ftp.get_publication(cfg, pid)
    if rule is None:
        abort(404)
    if _publisher().run_async(cfg, rule, trigger="manual"):
        flash(_("Publicação '%(name)s' iniciada — acompanhe o estado na tabela "
                "(a página atualiza sozinha).", name=rule["name"]), "ok")
    else:
        flash(_("'%(name)s' já está em execução.", name=rule["name"]), "error")
    return redirect(url_for("ftp.index"))


@ftp_pages.post("/publicacoes/<pid>/alternar")
@admin_required
def publication_toggle(pid):
    cfg = _load()
    rule = ftp.get_publication(cfg, pid)
    if rule is None:
        abort(404)
    rule["enabled"] = not rule["enabled"]
    ftp.save_publication(current_app.store, cfg, rule)
    flash((_("Publicação '%(name)s' habilitada.", name=rule["name"]) if rule["enabled"]
           else _("Publicação '%(name)s' desabilitada.", name=rule["name"])), "ok")
    return redirect(url_for("ftp.index"))


@ftp_pages.post("/publicacoes/<pid>/remover")
@admin_required
def publication_delete(pid):
    """Apaga só a REGRA. O que já foi publicado continua no FTP (remover uma regra
    nunca apaga dados — quem quiser limpar usa o próprio FTP com uma conta de escrita)."""
    cfg = _load()
    if ftp.delete_publication(current_app.store, cfg, pid):
        _publisher().forget(pid)
        flash(_("Publicação removida. Os arquivos já publicados permanecem no FTP."), "ok")
    else:
        flash(_("Publicação não encontrada."), "error")
    return redirect(url_for("ftp.index"))


# --- JSON para a UI ---------------------------------------------------------------------------
@ftp_pages.get("/browse.json")
@admin_required
def browse_json():
    cfg = _load()
    listing = ftp.browse_dirs(cfg, request.args.get("area") or "", request.args.get("sub") or "")
    if listing is None:
        return jsonify({"error": _("área ou pasta inválida")}), 400
    return jsonify(listing)


@ftp_pages.get("/publicacoes.json")
@admin_required
def publications_json():
    cfg = _load()
    rows = _publisher().describe(cfg, ftp.publications(cfg))
    return jsonify([{
        "id": r["id"], "status": r["status"], "running": r["running"],
        "last": r["last_start_display"], "next": r["next_display"],
        "files": (r["state"] or {}).get("files", 0),
        "bytes": ftp_fmt_bytes((r["state"] or {}).get("bytes", 0)),
        "message": (r["state"] or {}).get("message", ""),
    } for r in rows])
