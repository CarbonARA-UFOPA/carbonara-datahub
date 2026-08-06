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
