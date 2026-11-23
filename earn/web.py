"""The member-facing Earn pages and public JSON endpoints."""
from __future__ import annotations

import hashlib
import json

from flask import (
    Blueprint, Response, abort, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)

from . import db, domain, security

bp = Blueprint("earn", __name__)


def _cfg():
    return current_app.config["EARN"]


def _conn():
    return db.connect(_cfg().db_path)


def _public_stats(conn) -> dict:
    members = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NOT NULL AND status='active'"
    ).fetchone()["n"]
    nodes = conn.execute(
        "SELECT COUNT(*) AS n FROM nodes WHERE unlinked_at IS NULL"
    ).fetchone()["n"]
    earned = conn.execute(
        "SELECT COALESCE(SUM(points),0) AS n FROM ledger WHERE points>0"
    ).fetchone()["n"]
    redeemed = conn.execute(
        "SELECT COALESCE(SUM(points),0) AS n FROM ledger WHERE kind='redeem'"
    ).fetchone()["n"]
    return {"members": members, "nodes": nodes, "earned": earned,
            "redeemed": abs(redeemed)}


def _member_data(conn, user) -> dict:
    nodes = domain.nodes_for(conn, user["id"])
    balance = domain.balance(conn, user["id"])
    rewards = domain.reward_list(conn)
    recent = domain.ledger_rows(conn, user["id"], 12)
    month = domain.leaderboard(conn, days=30, limit=15)
    all_time = domain.leaderboard(conn, days=0, limit=15)
    premium = domain.premium_status(conn, user["id"])
    earned = conn.execute(
        "SELECT COALESCE(SUM(points),0) AS n FROM ledger WHERE user_id=? AND points>0",
        (user["id"],),
    ).fetchone()["n"]
    return {"nodes": nodes, "balance": balance, "rewards": rewards, "recent": recent,
            "month": month, "all_time": all_time, "premium": premium, "earned": earned}


@bp.get("/earn/")
def index():
    conn = _conn()
    try:
        stats = _public_stats(conn)
        if g.get("user") is None:
            return render_template("earn/teaser.html", stats=stats,
                                   rewards=domain.reward_list(conn))
        if not g.user.get("email_verified_at"):
            return render_template("earn/unverified.html", stats=stats)
        data = _member_data(conn, g.user)
        programme = {}
        try:
            programme = domain.read_snapshot(_cfg().points_snapshot)
        except (OSError, ValueError):
            programme = {}
        return render_template(
            "earn/index.html", stats=stats, programme=programme,
            verified=request.args.get("verified"), **data
        )
    finally:
        conn.close()


@bp.get("/earn/rules/")
def rules():
    conn = _conn()
    try:
        stats = _public_stats(conn)
        programme = {}
        try:
            programme = domain.read_snapshot(_cfg().points_snapshot)
        except (OSError, ValueError):
            programme = {}
        return render_template("earn/rules.html", stats=stats, programme=programme)
    finally:
        conn.close()


@bp.get("/earn/claim/")
@security.verified_required
def claim():
    conn = _conn()
    try:
        code, expires = domain.issue_claim_code(conn, g.user["id"])
        nodes = dom
