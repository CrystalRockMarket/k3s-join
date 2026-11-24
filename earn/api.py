"""Public API and MCP surface for K3s Join & Earn.

One bearer key per caller, hashed at rest and scoped to read. The API is free:
there is no monthly counter and no price. The MCP endpoint
speaks JSON-RPC 2.0 over HTTP and exposes the same three read tools.

Nothing here writes cluster state. The points snapshot is produced on the
worker; this service only reads it.
"""
from __future__ import annotations

import hmac
import json
import secrets
from functools import wraps

from flask import (
    Blueprint, Response, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)

from . import db, domain, security

bp = Blueprint("api", __name__)

SCOPE = "read"
DOCS_URL = "https://k3s.kituwa.com/api/docs/"
KEYS_URL = "https://k3s.kituwa.com/account/api-keys/"
API_PAGE = "https://k3s.kituwa.com/api/"
MCP_URL = "https://k3s.kituwa.com/mcp"


def _cfg():
    return current_app.config["EARN"]


def _conn():
    return db.connect(_cfg().db_path)


def _api_error(code: str, message: str, status: int = 400, **extra):
    error = {
        "code": code,
        "message": message,
        "docs": DOCS_URL + "#errors",
    }
    error.update(extra)
    response = jsonify({"ok": False, "error": error})
    response.status_code = status
    return response


def _jsonrpc_error(rid, code: int, message: str, status: int = 200):
    response = jsonify({
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": code, "message": message},
    })
    response.status_code = status
    return response


def _new_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    key = f"k3s_{prefix}_{secret}"
    return key, prefix, security.digest(key)


def api_key_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        raw = request.headers.get("Authorization", "")
        if not raw.lower().startswith("bearer "):
            return _api_error(
                "missing_key",
                "Send Authorization: Bearer <key>. Create a key at " + KEYS_URL,
                401,
            )
        key = raw[7:].strip()
        parts = key.split("_", 2)
        if len(parts) != 3 or parts[0] != "k3s" or not parts[1] or not parts[2]:
            return _api_error("invalid_key", "That API key is not valid.", 401)
        prefix = parts[1]
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE prefix=?", (prefix,)
            ).fetchone()
            if (
                row is None
                or row["revoked_at"]
                or not hmac.compare_digest(str(row["key_hash"]), security.digest(key))
            ):
                return _api_error(
                    "invalid_key", "That API key is not valid or has been revoked.", 401
                )
            scopes = [
                item.strip()
                for item in str(row["scopes"] or "").split(",")
                if item.strip()
            ]
            if SCOPE not in scopes:
                return _api_error(
                    "forbidden", "That key does not carry the read scope.", 403
                )
            if security.limited(
                "api:" + row["id"] + ":" + security.client_ip(), 120, 60
            ):
                response = _api_error(
                    "rate_limited", "Too many requests; slow down.", 429
                )
                response.headers["Retry-After"] = "60"
                return response
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (db.now(), row["id"]),
            )
            conn.commit()
            g.api_key = dict(row)
        finally:
            conn.close()
        return view(*args, **kwargs)

    return wrapped


def _meta() -> dict:
    return {"scope": SCOPE, "docs": DOCS_URL}

def _snapshot() -> dict:
    try:
        return domain.read_snapshot(_cfg().points_snapshot)
    except (OSError, ValueError):
        return {}


def _earn_stats(conn) -> dict:
    members = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NOT NULL"
        " AND status='active'"
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
    return {
        "members": members,
        "nodes": nodes,
        "earned": earned,
        "redeemed": abs(redeemed),
        "updated_at": db.now(),
    }


def _cluster_data() -> dict:
    snapshot = _snapshot()
    return {
        "nodes": len(snapshot.get("nodes") or []),
        "live_points": snapshot.get("live_points", 0),
        "total_points": snapshot.get("total_points", 0),
        "generated_at": snapshot.get("generated_at", ""),
        "rule": snapshot.get(
            "rule", "1 point = 100 CPU-seconds of Kituwa work"
        ),
    }


def _points_data() -> dict:
    snapshot = _snapshot()
    nodes = []
    for row in (snapshot.get("nodes") or [])[:15]:
        nodes.append({
            "node": row.get("node", ""),
            "points": row.get("points", 0),
            "share": row.get("share", 0),
            "last_seen": row.get("last_seen", ""),
        })
    return {
        "generated_at": snapshot.get("generated_at", ""),
        "total_points": snapshot.get("total_points", 0),
        "live_points": snapshot.get("live_points", 0),
        "nodes": nodes,
    }


def _earn_data() -> dict:
    conn = _conn()
    try:
        return _earn_stats(conn)
    finally:
        conn.close()


def _tool_data(name: str) -> dict | None:
    if name == "cluster_summary":
        return _cluster_data()
    if name == "points_leaderboard":
        return _points_data()
    if name == "earn_stats":
        return _earn_data()
    return None


def _mcp_tools() -> list[dict]:
    return [
        {
            "name": "cluster_summary",
            "description": "Worker count, live points and snapshot freshness for the Kituwa k3s cluster.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "points_leaderboard",
            "description": "The public points snapshot: totals and the top worker nodes.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "earn_stats",
            "description": "Public Earn programme totals: verified members, linked nodes, points earned and redeemed.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]


# --------------------------------------------------------------------------- #
# Key management (member session, not bearer)
# --------------------------------------------------------------------------- #
def _keys_for(user_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id,name,prefix,scopes,created_at,last_used_at,revoked_at"
            " FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@bp.get("/account/api-keys/")
@security.verified_required
def keys_page():
    return render_template(
        "account/api_keys.html",
        keys=_keys_for(g.user["id"]),
        new_key=None,
        docs_url=DOCS_URL,
        api_page=API_PAGE,
    )


@bp.post("/account/api-keys/")
@security.verified_required
def create_key():
    refused = security.require_csrf()
    if refused:
        return refused
    if security.limited("api-key-create:" + g.user["id"], 5, 3600):
        return render_template(
            "account/api_keys.html",
            keys=_keys_for(g.user["id"]),
            new_key=None,
            error="Too many keys created in the last hour. Try again later.",
            docs_url=DOCS_URL,
            api_page=API_PAGE,
        ), 429
    name = (request.form.get("name") or "API key").strip()[:40] or "API key"
    key, prefix, key_hash = _new_key()
    conn = _conn()
    try:
        for _ in range(5):
            try:
                conn.execute(
                    "INSERT INTO api_keys(id,user_id,name,prefix,key_hash,scopes,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (security.new_id(), g.user["id"], name, prefix, key_hash, SCOPE, db.now()),
                )
                conn.commit()
                break
            except Exception:
                prefix = secrets.token_hex(4)
                key = f"k3s_{prefix}_{secrets.token_urlsafe(32)}"
                key_hash = security.digest(key)
        else:
            raise RuntimeError("could not allocate a unique API key prefix")
    finally:
        conn.close()
    return render_template(
        "account/api_keys.html",
        keys=_keys_for(g.user["id"]),
        new_key=key,
        docs_url=DOCS_URL,
        api_page=API_PAGE,
    )


@bp.post("/account/api-keys/<key_id>/revoke")
@security.verified_required
def revoke_key(key_id: str):
    refused = security.require_csrf()
    if refused:
        return refused
    conn = _conn()
    try:
        conn.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND user_id=?"
            " AND revoked_at IS NULL",
            (db.now(), key_id[:64], g.user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("api.keys_page"))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
@bp.get("/api/v1/public")
@bp.get("/api/v1/public/")
def api_root():
    return jsonify({
        "ok": True,
        "service": "K3s Join & Earn API",
        "version": "1.0.0",
        "auth": "Authorization: Bearer <key>",
        "scope": SCOPE,
        "endpoints": {
            "health": "/api/v1/public/health",
            "cluster": "/api/v1/public/cluster",
            "points": "/api/v1/public/points",
            "earn": "/api/v1/public/earn",
            "mcp": MCP_URL,
            "keys": KEYS_URL,
        },
        "docs": DOCS_URL,
    })


@bp.get("/api/v1/public/health")
def api_health():
    return jsonify({
        "ok": True,
        "service": "k3s-join-api",
        "version": "1.0.0",
        "time": db.now(),
    })


@bp.get("/api/v1/public/cluster")
@api_key_required
def api_cluster():
    return jsonify({"ok": True, "data": _cluster_data(), "meta": _meta()})


@bp.get("/api/v1/public/points")
@api_key_required
def api_points():
    return jsonify({"ok": True, "data": _points_data(), "meta": _meta()})


@bp.get("/api/v1/public/earn")
@api_key_required
def api_earn():
    return jsonify({"ok": True, "data": _earn_data(), "meta": _meta()})


# --------------------------------------------------------------------------- #
# MCP (JSON-RPC 2.0 over HTTP)
# --------------------------------------------------------------------------- #
@bp.get("/mcp")
def mcp_get():
    response = jsonify({
        "ok": False,
        "error": {
            "code": "method_not_allowed",
            "message": "POST JSON-RPC 2.0 to /mcp with Authorization: Bearer <key>.",
            "docs": MCP_URL + "/",
        },
    })
    response.status_code = 405
    return response


@bp.post("/mcp")
@api_key_required
def mcp_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _jsonrpc_error(None, -32700, "Parse error", 400)
    method = payload.get("method")
    rid = payload.get("id")
    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "k3s-join", "version": "1.0.0"},
            },
        })
    if method == "notifications/initialized":
        return ("", 202)
    if method == "tools/list":
        return jsonify({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"tools": _mcp_tools()},
        })
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        data = _tool_data(name)
        if data is None:
            return _jsonrpc_error(rid, -32602, "Unknown tool: " + name)
        return jsonify({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps(data, separators=(",", ":")),
                }],
                "isError": False,
            },
        })
    return _jsonrpc_error(rid, -32601, "Method not found")


@bp.get("/.well-known/mcp.json")
def mcp_card():
    payload = {
        "version": 1,
        "name": "K3s Join & Earn",
        "description": "Read-only cluster, points and Earn data as MCP tools.",
        "url": MCP_URL,
        "transport": "http",
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "keys": KEYS_URL,
            "docs": DOCS_URL + "#authentication",
        },
        "tools": [
            {"name": item["name"], "description": item["description"]}
            for item in _mcp_tools()
        ],
        "docs": "https://k3s.kituwa.com/mcp/",
    }
    return Response(
        json.dumps(payload, separators=(",", ":")),
        mimetype="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )
