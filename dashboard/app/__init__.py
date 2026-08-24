"""App factory do dashboard de monitoramento de aquisição."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from .config_store import ConfigStore


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
    app.store = ConfigStore(config_path)

    from .api import api
    from .hub_views import hub_pages
    from .views import pages

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(hub_pages, url_prefix="/hub")

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
        }

    return app
