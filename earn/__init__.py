"""Kituwa Cluster Earn — accounts, node links, points and redemptions."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, render_template

from . import db, security
from .config import Config, load as load_config


def create_app(config: Config | None = None) -> Flask:
    cfg = config or load_config()
    app = Flask(__name__, static_folder="static", static_url_path="/earn/assets")
    app.config["EARN"] = cfg
    app.config["SECRET_KEY"] = cfg.secret_key
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["JSON_SORT_KEYS"] = False

    db.init(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        db.seed(conn)
        conn.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('programme_started',?)",
            (db.now(),),
        )
    finally:
        conn.close()

    security.init_app(app)

    from . import admin, web

    app.register_blueprint(web.bp)
    app.register_blueprint(admin.bp)

    from . import auth

    app.register_blueprint(auth.bp)

    @app.template_filter("pts")
    def _pts(value):
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return "0"
        if abs(number - round(number)) < 0.05:
            return f"{int(round(number)):,}"
        return f"{number:,.1f}"

    @app.template_filter("day")
    def _day(value):
        if not value:
            return "—"
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")
            return parsed.strftime("%d %b %Y")
        except ValueError:
            return str(value)

    @app.errorhandler(404)
    def _not_found(_error):
        return render_template("error.html", code=404, message="That page does not exist."), 404

    @app.errorhandler(403)
    def _forbidden(_error):
        return render_template("error.html", code=403, message="You do not have access."), 403

    @app.errorhandler(413)
    def _too_large(_error):
        return render_template("error.html", code=413, message="That request is too large."), 413

    @app.errorhandler(500)
    def _server_error(_error):
        return render_template("error.html", code=500, message="Something went wrong on our side."), 500

    return app
