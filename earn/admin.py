"""Operator console. Pocket ID only; no consumer password opens this door."""
from __future__ import annotations

import csv
import io
import json
import re
from functools import wraps

from flask import (
    Blueprint, Response, abort, current_app, g, redirect, render_template, request, url_for,
)

from . import db, domain, security

bp = Blueprint("admin", __name__)
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}\Z")


def _cfg():
    return current_app.config["EARN"]


def _conn():
    return db.connect(_cfg().db_path)


def operator_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("admin.login", next=request.path))
        if not g.user.get("is_operator"):
            abort(404)
        return view(*args, **kwargs)

    return wrapped


@bp.get("/admin/login")
def login():
    if g.get("user") and g.user.get("is_operator"):
        return redirect(url_for("admin.index"))
    return render_template("admin/login.html")


@bp.get("/admin/")
@operator_required
def index():
    conn = _conn()
    try:
        counts = {
            "members": conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NOT NULL"
            ).fetchone()["n"],
            "nodes": conn.execute(
                "SELECT COUNT(*) AS n FROM nodes WHERE unlinked_at IS NULL"
            ).fetchone()["n"],
            "active": conn.execute(
                "SELECT COUNT(*) AS n FROM premium_grants WHERE revoked_at IS NULL"
                " AND expires_at>?", (db.now(),)
            ).fetchone()["n"],
            "issued": conn.execute(
                "SELECT COUNT(*) AS n FROM redemptions"
            ).fetchone()["n"],
            "balance": conn.execute(
                "SELECT COALESCE(SUM(points),0) AS n FROM ledger"
            ).fetchone()["n"],
        }
        recent = conn.execute(
            "SELECT r.id,r.status,r.points,r.created_at,r.user_id,u.display_name,w.title"
            " FROM redemptions r JOIN users u ON u.id=r.user_id"
            " JOIN rewards w ON w.slug=r.reward_slug ORDER BY r.created_at DESC LIMIT 15"
        ).fetchall()
        audit = conn.execute(
            "SELECT actor,action,target,detail,created_at FROM audit ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    return render_template("admin/index.html", counts=counts, recent=recent, audit=audit)


@bp.get("/admin/orders/")
@operator_required
def orders():
    status = request.args.get("status", "")
    conn = _conn()
    try:
        sql = (
            "SELECT r.*, u.display_name, u.email, w.title, w.kind FROM redemptions r"
            " JOIN users u ON u.id=r.user_id JOIN rewards w ON w.slug=r.reward_slug"
        )
        params = []
        if status:
            sql += " WHERE r.status=?"
            params.append(status)
        sql += " ORDER BY r.created_at DESC LIMIT 200"
        rows = []
        for row in conn.execute(sql, params):
            item = dict(row)
            item["code"] = security.decrypt(item.get("code_enc") or "")
            rows.append(item)
    finally:
        conn.close()
    return render_template("admin/orders.html", rows=rows, status=status)


@bp.post("/admin/orders/<redemption_id>/status")
@operator_required
def order_status(redemption_id: str):
    refused = security.require_csrf()
    if refused:
        return refused
    status = request.form.get("status", "")
    note = request.form.get("note", "")
    conn = _conn()
    try:
        try:
            domain.set_status(conn, g.user["id"], redemption_id, status, "", note)
        except domain.DomainError as exc:
            return redirect(url_for("admin.orders") + "?error=" + str(exc)), 400
    finally:
        conn.close()
    return redirect(url_for("admin.orders"))


@bp.get("/admin/rewards/")
@operator_required
def rewards():
    conn = _conn()
    try:
        rows = domain.reward_list(conn, include_inactive=True)
    finally:
        conn.close()
    return render_template("admin/rewards.html", rows=rows)


@bp.post("/admin/rewards/save")
@operator_required
def reward_save():
    refused = security.require_csrf()
    if refused:
        return refused
    slug = request.form.get("slug", "").strip().lower()
    if not SLUG_RE.match(slug):
        return redirect(url_for("admin.rewards") + "?error=invalid-slug")
    title = request.form.get("title", "").strip()[:120]
    # Digital premium is the only reward this programme sells.
    kind = "digital"
    try:
        cost = float(request.form.get("cost_points", "0"))
        stock_raw = request.form.get("stock", "").strip()
        stock = int(stock_raw) if stock_raw else None
        term = int(request.form.get("term_days", "0") or 0)
    except ValueError:
        return redirect(url_for("admin.rewards") + "?error=invalid-number")
    if not title or not (1 <= cost <= 10_000_000):
        return redirect(url_for("admin.rewards") + "?error=invalid-price")
    active = 1 if request.form.get("active") == "1" else 0
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO rewards(slug,title,kind,cost_points,stock,active,description,"
            "site,plan,term_days,sort_order) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(slug) DO UPDATE SET title=excluded.title,kind=excluded.kind,"
            " cost_points=excluded.cost_points,stock=excluded.stock,active=excluded.active,"
            " description=excluded.description,site=excluded.site,plan=excluded.plan,"
            " term_days=excluded.term_days,sort_order=excluded.sort_order",
            (slug, title, kind, cost, stock, active,
             request.form.get("description", "").strip()[:500],
             request.form.get("site", "").strip()[:60],
             request.form.get("plan", "").strip()[:60], term,
             int(request.form.get("sort_order", "100") or 100)),
        )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (g.user["id"], "reward.save", slug, f"{cost:g}", db.now()))
    finally:
        conn.close()
    return redirect(url_for("admin.rewards"))


@bp.get("/admin/users/")
@operator_required
def users():
    query = request.args.get("q", "").strip()[:80]
    conn = _conn()
    try:
        sql = (
            "SELECT u.id,u.email,u.display_name,u.email_verified_at,u.is_operator,"
            "u.leaderboard,u.created_at,"
            " COALESCE((SELECT SUM(points) FROM ledger WHERE user_id=u.id),0) AS balance,"
            " (SELECT COUNT(*) FROM nodes WHERE user_id=u.id AND unlinked_at IS NULL) AS nodes"
            " FROM users u"
        )
        params = []
        if query:
            sql += " WHERE u.email_lower LIKE ? OR u.display_lower LIKE ?"
            params = [f"%{query.lower()}%", f"%{query.lower()}%"]
        sql += " ORDER BY u.created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return render_template("admin/users.html", rows=rows, query=query)


@bp.post("/admin/users/<user_id>/adjust")
@operator_required
def user_adjust(user_id: str):
    refused = security.require_csrf()
    if refused:
        return refused
    try:
        points = float(request.form.get("points", "0"))
    except ValueError:
        return redirect(url_for("admin.users") + "?error=invalid-points"), 400
    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
            abort(404)
        try:
            domain.adjust(conn, g.user["id"], user_id, points,
                          request.form.get("memo", ""))
        except domain.DomainError as exc:
            return redirect(url_for("admin.users") + "?error=" + str(exc)), 400
    finally:
        conn.close()
    return redirect(url_for("admin.users"))


@bp.get("/admin/audit/")
@operator_required
def audit():
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT actor,action,target,detail,created_at FROM audit ORDER BY id DESC LIMIT 300"
        ).fetchall()
    finally:
        conn.close()
    return render_template("admin/audit.html", rows=rows)


@bp.get("/admin/export.csv")
@operator_required
def export_csv():
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT r.id,r.created_at,u.email,u.display_name,w.title,w.kind,r.points,"
            "r.status,r.expires_at FROM redemptions r JOIN users u ON u.id=r.user_id"
            " JOIN rewards w ON w.slug=r.reward_slug ORDER BY r.created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "created_at", "email", "member", "reward", "kind", "points",
                     "status", "expires_at"])
    for row in rows:
        writer.writerow([row["id"], row["created_at"], row["email"], row["display_name"],
                         row["title"], row["kind"], f"{row['points']:g}", row["status"],
                         row["expires_at"] or ""])
    response = Response(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = 'attachment; filename="k3s-earn-redemptions.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/admin/logout")
@operator_required
def logout():
    refused = security.require_csrf()
    if refused:
        return refused
    security.destroy_session()
    response = redirect(url_for("earn.index"))
    return security.clear_session_cookie(response)
