"""App factory do dashboard de monitoramento de aquisição."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# O parse dos arquivos (tailer, carimbos, caminhos com data/hora) vive no pacote
# compartilhado `carbonara_parse/`, na raiz do repositório — o mesmo código que a
# ingestão usa. A dashboard roda de dashboard/ (run.py, .venv), então a raiz entra no
# sys.path aqui, ANTES de qualquer import do app.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if (_REPO_ROOT / "carbonara_parse").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from flask import Flask  # noqa: E402

from .config_store import ConfigStore  # noqa: E402


def _secret_key(config_path: str) -> bytes:
    """Chave da sessão de admin, persistida ao lado do config — sobrevive a restarts
    (chave aleatória por processo derrubaria o login a cada reinício/worker)."""
    p = Path(config_path).parent / ".secret_key"
    try:
        if p.is_file():
            return p.read_bytes()
        key = os.urandom(32)
        p.write_bytes(key)
        p.chmod(0o600)
        return key
    except OSError:
        return os.urandom(32)


def create_app(config_path: str) -> Flask:
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.secret_key = _secret_key(config_path)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.store = ConfigStore(config_path)

    @app.before_request
    def _same_origin_only():
        """Barreira contra CSRF em TODAS as ações (POST/DELETE) de todos os blueprints:
        o navegador diz de onde veio (Sec-Fetch-Site/Origin); uma página de outro site
        não consegue disparar "zerar tabela", "revogar chave" ou "aplicar serviços" na
        sessão do admin. Formulários da própria dashboard passam."""
        from flask import abort, request
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return None
        site = request.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            abort(403)
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/").split("://", 1)[-1] != request.host:
            abort(403)
        return None

    from .api import api
    from .ftp_publish import Publisher
    from .ftp_views import ftp_pages
    from .hub_views import hub_pages
    from .views import pages

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(hub_pages, url_prefix="/hub")
    app.register_blueprint(ftp_pages, url_prefix="/ftp")

    # Publicador hub → FTP: thread de fundo que executa as regras de `ftp.publications`
    # no intervalo de cada uma. Estado e log ficam ao lado do config.yaml.
    # CARBONARA_NO_PUBLISHER=1 desliga (testes, ou uma segunda instância só de leitura —
    # duas dashboards publicando a mesma regra rodariam rsync em duplicidade).
    app.publisher = Publisher(app.store, Path(config_path).parent)
    if os.environ.get("CARBONARA_NO_PUBLISHER") != "1":
        app.publisher.start()

    @app.url_defaults
    def _static_cache_bust(endpoint, values):
        """Anexa ?v=<mtime> a todo estático: depois de um git pull + reinício, o
        navegador SEMPRE busca o dashboard.js/style.css novos — sem depender de
        Ctrl+Shift+R (cache do navegador/proxy servindo JS velho é a causa clássica
        de 'a correção não chegou')."""
        if endpoint == "static" and "filename" in values:
            p = Path(app.static_folder) / values["filename"]
            try:
                values["v"] = int(p.stat().st_mtime)
            except OSError:
                pass

    # Bilinguismo (pt-BR ⇄ en) — ver app/i18n.py.
    from . import i18n

    app.jinja_env.globals["_"] = i18n.template_gettext

    @app.get("/lang/<code>")
    def set_lang(code):
        """Botão de idioma do topo: grava o cookie e volta para a mesma página
        (`next` é sempre um caminho LOCAL — nunca redireciona para fora)."""
        from flask import redirect, request

        resp = redirect(i18n.safe_next(request.args.get("next")))
        if code in i18n.LANGS:
            resp.set_cookie(i18n.COOKIE, code, max_age=365 * 86400, samesite="Lax")
        return resp

    @app.get("/i18n.js")
    def i18n_js():
        """Catálogo para o JavaScript: `t(texto, vars)` traduz como o `_` do Python.
        Em português o catálogo vai vazio (t devolve o próprio texto)."""
        import json

        from flask import Response

        lang = i18n.get_lang()
        cat = i18n.catalog_en() if lang == "en" else {}
        body = (
            f"window.LANG={json.dumps(lang)};window.I18N={json.dumps(cat, ensure_ascii=False)};\n"
            "window.t=function(s,v){var k=String(s).replace(/\\s+/g,' ').trim();"
            "var o=Object.prototype.hasOwnProperty.call(window.I18N,k)?window.I18N[k]:s;"
            "if(v)o=o.replace(/%\\((\\w+)\\)s/g,function(m,n){return n in v?String(v[n]):m;});"
            "return o;};\n"
        )
        resp = Response(body, mimetype="application/javascript")
        # Varia com o cookie: nunca cachear (é pequeno e a troca de idioma é imediata).
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _globals():
        from flask import session

        from . import hub

        cfg = app.store.load()
        # Pausa da recepção: aparece no topo de TODAS as páginas (inclusive para quem
        # não é admin) — é a explicação de por que os dados pararam de chegar.
        try:
            pause = hub.pause_state(cfg)
        except Exception:  # noqa: BLE001 — banner informativo nunca derruba a página
            pause = {"all": None, "stations": {}, "paused_ids": [], "any": False}
        return {
            "refresh_seconds": cfg["server"].get("refresh_seconds", 60),
            "auth_enabled": bool(cfg.get("auth")),
            "is_admin": bool(session.get("admin")) or not cfg.get("auth"),
            "pause_state": pause,
            "lang": i18n.get_lang(),
            "lang_labels": i18n.LANG_LABELS,
        }

    return app
