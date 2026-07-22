"""App factory do dashboard de monitoramento de aquisição."""
from __future__ import annotations

import os

from flask import Flask

from .config_store import ConfigStore


def create_app(config_path: str) -> Flask:
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.secret_key = os.urandom(24)  # só para flash messages; não há sessão de login
    app.store = ConfigStore(config_path)

    from .api import api
    from .views import pages

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    @app.context_processor
    def _globals():
        from flask import session

        cfg = app.store.load()
        return {
            "refresh_seconds": cfg["server"].get("refresh_seconds", 60),
            "auth_enabled": bool(cfg.get("auth")),
            "is_admin": bool(session.get("admin")) or not cfg.get("auth"),
        }

    return app
