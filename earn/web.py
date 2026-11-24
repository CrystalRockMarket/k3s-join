"""The member-facing Earn pages and public JSON endpoints."""
from __future__ import annotations

from flask import (
    Blueprint, Response, abort, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)
import json

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


def _programme() -> dict:
    try:
        return domain.read_snapshot(_cfg().points_snapshot)
    except (OSError, ValueError):
        return {}


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
        return render_template(
            "earn/index.html", stats=stats, programme=_programme(),
            verified=request.args.get("verified"), **data
        )
    finally:
        conn.close()


@bp.get("/earn/rules/")
def rules():
    conn = _conn()
    try:
        return render_template("earn/rules.html", stats=_public_stats(conn),
                               programme=_programme())
    finally:
        conn.close()


@bp.get("/earn/claim/")
@security.verified_required
def claim():
    conn = _conn()
    try:
        code, expires = domain.issue_claim_code(conn, g.user["id"])
        nodes = domain.nodes_for(conn, g.user["id"])
        claimable = domain.claimable_nodes(conn, g.user["id"], limit=30)
        return render_template("earn/claim.html", code=code, expires=expires,
                               nodes=nodes, claimable=claimable,
                               limit=g.user.get("node_limit") or 1)
    finally:
        conn.close()


@bp.post("/earn/nodes/<node>/unlink/")
@security.verified_required
def unlink(node: str):
    refused = security.require_csrf()
    if refused:
        return refused
    conn = _conn()
    try:
        if not domain.unlink_node(conn, g.user["id"], node[:120]):
            abort(404)
    finally:
        conn.close()
    return redirect(url_for("earn.claim"))


@bp.get("/earn/redeem/<slug>/")
@security.verified_required
def reward(slug: str):
    conn = _conn()
    try:
        item = domain.reward(conn, slug)
        if item is None or not item["active"]:
            abort(404)
        return render_template("earn/redeem.html", reward=item,
                               balance=domain.balance(conn, g.user["id"]),
                               values={})
    finally:
        conn.close()


@bp.post("/earn/redeem/<slug>/")
@security.verified_required
def reward_post(slug: str):
    refused = security.require_csrf()
    if refused:
        return refused
    conn = _conn()
    try:
        item = domain.reward(conn, slug)
        if item is None or not item["active"]:
            abort(404)
        try:
            result = domain.redeem(conn, g.user, slug, request.form)
        except domain.DomainError as exc:
            return render_template("earn/redeem.html", reward=item,
                                   balance=domain.balance(conn, g.user["id"]),
                                   error=str(exc), values=request.form), 400
        return redirect(url_for("earn.receipt", redemption_id=result["id"]))
    finally:
        conn.close()


@bp.get("/earn/receipt/<redemption_id>/")
@security.login_required
def receipt(redemption_id: str):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT r.*, w.title, w.kind, w.site, w.plan, w.term_days FROM redemptions r"
            " JOIN rewards w ON w.slug=r.reward_slug WHERE r.id=? AND r.user_id=?",
            (redemption_id, g.user["id"]),
        ).fetchone()
        if row is None:
            abort(404)
        data = dict(row)
        data["address"] = {}
        if data.get("address_enc"):
            try:
                import json as _json
                data["address"] = _json.loads(security.decrypt(data["address_enc"]))
            except (ValueError, TypeError):
                data["address"] = {}
        data["code"] = security.decrypt(data.get("code_enc") or "")
        return render_template("earn/receipt.html", item=data)
    finally:
        conn.close()


@bp.get("/earn/premium/")
@security.verified_required
def premium():
    conn = _conn()
    try:
        grants = []
        for row in conn.execute(
            "SELECT site,plan,token_enc,expires_at FROM premium_grants"
            " WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY expires_at DESC",
            (g.user["id"], db.now()),
        ).fetchall():
            grants.append({
                "site": row["site"], "plan": row["plan"],
                "expires_at": row["expires_at"],
                "token": security.decrypt(row["token_enc"]),
            })
        return render_template("earn/premium.html", grants=grants,
                               origin=_cfg().public_origin)
    finally:
        conn.close()


@bp.get("/earn/docs/")
def docs():
    return render_template("earn/docs.html", origin=_cfg().public_origin)


@bp.get("/.well-known/auth-methods.json")
def auth_methods():
    payload = {
        "version": 1,
        "methods": ["passkey", "pocketid", "password"],
        "order": ["passkey", "pocketid", "password"],
        "email_verification": True,
        "endpoints": {
            "login": "/account/login/",
            "signup": "/account/signup/",
            "passkey": "/account/passkey/login/begin",
            "verify": "/account/verify/",
            "reset": "/account/forgot/",
            "pocketid": "/account/oidc/start",
            "operator": "/admin/login",
        },
    }
    return Response(
        json.dumps(payload, separators=(",", ":")),
        mimetype="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@bp.get("/api/v1/earn/summary")
def api_summary():
    conn = _conn()
    try:
        stats = _public_stats(conn)
    finally:
        conn.close()
    return jsonify({"ok": True, "programme": "Kituwa Cluster Earn", **stats,
                    "updated_at": db.now()})


@bp.post("/api/v1/earn/claim")
def api_claim():
    if security.limited("claim:ip:" + security.client_ip(), 20, 300):
        return jsonify({"ok": False, "error": "Too many attempts."}), 429
    data = request.get_json(silent=True) if request.is_json else request.form
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected a JSON object."}), 400
    code = str(data.get("code", ""))
    node = str(data.get("node", ""))
    conn = _conn()
    try:
        result = domain.claim_with_code(conn, code, node)
    except domain.DomainError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        conn.close()
    return jsonify({"ok": True, "node": result["node"],
                    "message": "Linked. Your node now earns for this account."})


@bp.get("/api/v1/earn/premium/verify")
@bp.post("/api/v1/earn/premium/verify")
def api_premium_verify():
    if security.limited("premium:ip:" + security.client_ip(), 120, 60):
        return jsonify({"active": False, "error": "Too many requests."}), 429
    token = request.args.get("token", "")
    if request.method == "POST":
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            token = str(payload.get("token", ""))
        else:
            token = request.form.get("token", "")
    conn = _conn()
    try:
        result = domain.verify_premium(conn, token.strip())
    finally:
        conn.close()
    if not result.get("active"):
        return jsonify({"active": False}), 200
    return jsonify(result)


@bp.get("/healthz")
def healthz():
    conn = _conn()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return jsonify({"ok": True, "service": "k3s-earn"})
