#!/usr/bin/env python3
"""Dashboard de monitoramento de aquisição do CARBONARA — entrypoint.

Uso:
    python run.py [--config config.yaml] [--host 0.0.0.0] [--port 8080]

Host/porta da linha de comando têm precedência sobre o bloco `server:` do config.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard de monitoramento CARBONARA")
    default_cfg = Path(__file__).parent / "config.yaml"
    parser.add_argument("--config", default=str(default_cfg))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    app = create_app(args.config)
    server = app.store.load()["server"]
    app.run(
        host=args.host or server.get("host", "0.0.0.0"),
        port=args.port or int(server.get("port", 8080)),
        debug=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
