"""Run the Earn service (behind nginx on the host)."""
from __future__ import annotations

from waitress import serve

from . import create_app
from .config import load


def main() -> None:
    cfg = load()
    app = create_app(cfg)
    serve(app, host="127.0.0.1", port=8141, threads=8, connection_limit=200)


if __name__ == "__main__":
    main()
