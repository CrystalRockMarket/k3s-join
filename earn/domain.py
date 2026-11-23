"""The Earn domain: node links, points, rewards, redemptions, premium grants."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from werkzeug.security import safe_join  # noqa: F401  (kept for import parity)

from . import db, security

PHYSICAL_FIELDS = ("name", "line1", "line2", "city", "region", "postal", "country", "phone")


class DomainError(ValueError):
    """A user-facing refusal. The message is safe to show verbatim."""


def _cfg():
    return current_app.config["EARN"]


# --------------------------------------------------------------------------- #
# Snapshot ingestion
# --------------------------------------------------------------------------- #
def read_snapshot(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise DomainError("points snapshot has no nodes")
    return payload


def ingest(conn: sqlite3.Connection, payload: dict) -> dict:
    """Record per-node totals and credit linked accounts for new work.

    The worker owns the ledger. This host never estimates a point: it only moves
    the difference between two published totals, and an unchanged snapshot is a
    no-op.
    """
    generated = str(payload.get("generated_at") or db.now())
    credited = 0.0
    touched = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for node in payload.get("nodes") or []:
            name = str(node.get("node") or "").strip()[:120]
            if not name:
                continue
            try:
                points = float(node.get("points") or 0)
            except (TypeError, ValueError):
                continue
            touched += 1
            conn.execute(
                "INSERT INTO node_totals(node,points,generated_at,updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(node) DO UPDATE SET points=excluded.points,"
                " generated_at=excluded.generated_at, updated_at=excluded.updated_at",
                (name, points, generated, db.now()),
            )
            linked = conn.execute(
                "SELECT * FROM nodes WHERE node=? AND unlinked_at IS NULL", (name,)
            ).fetchone()
            if linked is None:
                continue
            last_total = float(linked["last_total"] or 0)
            if linked["last_generated_at"] == generated:
                continue
            delta = points - last_total
            if delta < 0:
                # A reset ledger is a reset, not a debt.
                delta = 0
            conn.execute(
                "UPDATE nodes SET last_total=?, last_generated_at=? WHERE node=?",
                (points, generated, name),
            )
            if delta > 0:
                ref = f"node:{name}:{generated}"
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (linked["user_id"], name, "earn", delta, ref,
                     f"{delta:,.1f} points from {name}", db.now()),
                )
                if cursor.rowcount == 1:
                    credited += delta
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return {"generated_at": generated, "nodes": touched, "credited": credited}


# --------------------------------------------------------------------------- #
# Balance and ledger
# --------------------------------------------------------------------------- #
def balance(conn: sqlite3.Connection, user_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(points),0) AS n FROM ledger WHERE user_id=?", (user_id,)
    ).fetchone()
    return float(row["n"] or 0)


def ledger_rows(conn: sqlite3.Connection, user_id: str, limit: int = 20):
    return conn.execute(
        "SELECT points,kind,node,memo,created_at FROM ledger WHERE user_id=?"
        " ORDER BY id DESC LIMIT ?", (user_id, limit)
    ).fetchall()


def link_node(conn: sqlite3.Connection, user: dict, node: str) -> dict:
    node = node.strip()[:120]
    if not node:
        raise DomainError("Choose a node.")
    total = conn.execute("SELECT * FROM node_totals WHERE node=?", (node,)).fetchone()
    if total is None:
        raise DomainError("That node is not in the published cluster list yet.")
    active = conn.execute(
        "SELECT node FROM nodes WHERE user_id=? AND unlinked_at IS NULL", (user["id"],)
    ).fetchall()
    if any(row["node"] == node for row in active):
        raise DomainError("That node is already linked to this account.")
    limit = int(user.get("node_limit") or 1)
    if len(active) >= limit:
        raise DomainError(f"This account can link {limit} node(s). Unlink one first.")
    other = conn.execute(
        "SELECT user_id FROM nodes WHERE node=? AND unlinked_at IS NULL", (node,)
    ).fetchone()
    if other is not None:
        raise DomainError("That node is already linked to another account.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO nodes(node,user_id,role,last_total,last_generated_at,linked_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(node) DO UPDATE SET user_id=excluded.user_id, role=excluded.role,"
            " last_total=excluded.last_total, last_generated_at=excluded.last_generated_at,"
            " linked_at=excluded.linked_at, unlinked_at=NULL",
            (node, user["id"], "volunteer", float(total["points"] or 0),
             total["generated_at"] or "", db.now()),
        )
        # One welcome credit per account, not per node.
        if _cfg().welcome_points > 0:
            conn.execute(
                "INSERT OR IGNORE INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (user["id"], "", "bonus", float(_cfg().welcome_points),
                 f"welcome:{user['id']}", "Welcome credit for linking your first node", db.now()),
            )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (user["id"], "node.link", node, "", db.now()))
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return {"node": node, "baseline": float(total["points"] or 0)}


def unlink_node(conn: sqlite3.Connection, user_id: str, node: str) -> bool:
    cursor = conn.execute(
        "UPDATE nodes SET unlinked_at=? WHERE node=? AND user_id=? AND unlinked_at IS NULL",
        (db.now(), node, user_id),
    )
    if cursor.rowcount == 1:
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (user_id, "node.unlink", node, "", db.now()))
        return True
    return False


def nodes_for(conn: sqlite3.Connection, user_id: str):
    return conn.execute(
        "SELECT n.node,n.linked_at,n.last_total,n.last_generated_at,"
        " COALESCE(t.points,0) AS live_total, t.updated_at"
        " FROM nodes n LEFT JOIN node_totals t ON t.node=n.node"
        " WHERE n.user_id=? AND n.unlinked_at IS NULL ORDER BY n.linked_at", (user_id,)
    ).fetchall()


def claimable_nodes(conn: sqlite3.Connection, user_id: str, limit: int = 50):
    return conn.execute(
        "SELECT t.node,t.points,t.updated_at FROM node_totals t"
        " LEFT JOIN nodes n ON n.node=t.node AND n.unlinked_at IS NULL"
        " WHERE n.node IS NULL ORDER BY t.points DESC LIMIT ?", (limit,)
    ).fetchall()


# --------------------------------------------------------------------------- #
# Leaderboard
# --------------------------------------------------------------------------- #
def leaderboard(conn: sqlite3.Connection, days: int = 0, limit: int = 20):
    cutoff = ""
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT u.display_name, u.id,"
        " COALESCE(SUM(CASE WHEN l.points>0 THEN l.points ELSE 0 END),0) AS earned,"
        " COUNT(DISTINCT l.node) AS nodes"
        " FROM users u JOIN ledger l ON l.user_id=u.id"
        " WHERE u.leaderboard=1 AND u.email_verified_at IS NOT NULL AND u.status='active'"
        " AND (?='' OR l.created_at>=?)"
        " GROUP BY u.id HAVING earned>0 ORDER BY earned DESC, u.created_at ASC LIMIT ?",
        (cutoff, cutoff, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Rewards and redemptions
# --------------------------------------------------------------------------- #
def reward_list(conn: sqlite3.Connection, include_inactive: bool = False):
    sql = "SELECT * FROM rewards"
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY kind='digital' DESC, sort_order, cost_points"
    return conn.execute(sql).fetchall()


def reward(conn: sqlite3.Connection, slug: str):
    return conn.execute("SELECT * FROM rewards WHERE slug=?", (slug,)).fetchone()


def validate_address(form: dict) -> dict:
    clean = {}
    for key in PHYSICAL_FIELDS:
        value = str(form.get(key, "") or "").strip()
        limit = 120 if key in {"name", "line1", "line2", "city", "region"} else 40
        if key == "phone":
            limit = 40
        if len(value) > limit:
            raise DomainError(f"{key.replace('_', ' ').title()} is too long.")
        clean[key] = value
    for required in ("name", "line1", "city", "postal", "country"):
        if not clean[required]:
            raise DomainError("Name, address line 1, city, postal code and country are required.")
    clean["country"] = clean["country"].upper()
    if not security.COUNTRY_RE.match(clean["country"]):
        raise DomainError("Country must be a two-letter code (US, GB, DE…).")
    return clean


def redeem(conn: sqlite3.Connection, user: dict, slug: str, form: dict) -> dict:
    if not user.get("email_verified_at"):
        raise DomainError("Confirm your email before redeeming points.")
    item = reward(conn, slug)
    if item is None or not item["active"]:
        raise DomainError("That reward is not available.")
    cost = float(item["cost_points"])
    if cost <= 0:
        raise DomainError("That reward is not priced.")
    address = validate_address(form) if item["kind"] == "physical" else {}
    redemption_id = security.new_id()
    now = db.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        available = balance(conn, user["id"])
        if available < cost:
            raise DomainError(
                f"You need {cost - available:,.0f} more points for this reward."
            )
        if item["stock"] is not None:
            updated = conn.execute(
                "UPDATE rewards SET stock=stock-1 WHERE slug=? AND stock>0", (slug,)
            )
            if updated.rowcount != 1:
                raise DomainError("That reward is out of stock.")
        if item["kind"] == "physical":
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM redemptions WHERE user_id=? AND"
                " status IN ('pending','approved')", (user["id"],)
            ).fetchone()["n"]
            if pending >= _cfg().max_pending_orders:
                raise DomainError("You have too many open orders. Wait for one to ship.")
        status = "issued" if item["kind"] == "digital" else "pending"
        code = ""
        code_hash = ""
        code_enc = ""
        expires = None
        if item["kind"] == "digital":
            code = "kprem_" + secrets.token_urlsafe(32)
            code_hash = security.digest(code)
            code_enc = security.encrypt(code)
            site = item["site"] or "premium"
            current = conn.execute(
                "SELECT MAX(expires_at) AS until FROM premium_grants WHERE user_id=?"
                " AND site=? AND revoked_at IS NULL AND expires_at>?", (user["id"], site, now)
            ).fetchone()["until"]
            start = datetime.now(timezone.utc)
            if current:
                try:
                    parsed = datetime.strptime(current, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    start = max(start, parsed)
                except ValueError:
                    pass
            until = (start + timedelta(days=int(item["term_days"] or 30))).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            expires = until
            conn.execute(
                "INSERT INTO premium_grants(id,user_id,redemption_id,site,plan,token_hash,"
                "token_enc,issued_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (security.new_id(), user["id"], redemption_id, site, item["plan"] or "premium",
                 code_hash, code_enc, now, until),
            )
        conn.execute(
            "INSERT INTO redemptions(id,user_id,reward_slug,points,status,address_enc,"
            "code_hash,code_enc,expires_at,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (redemption_id, user["id"], slug, cost, status,
             security.encrypt(json.dumps(address)) if address else "",
             code_hash, code_enc, expires, now, now),
        )
        conn.execute(
            "INSERT INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (user["id"], "", "redeem", -cost, f"redeem:{redemption_id}",
             f"Redeemed: {item['title']}", now),
        )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (user["id"], "redeem", redemption_id, f"{slug} {cost:g}", now))
        conn.execute("COMMIT")
    except DomainError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return {"id": redemption_id, "status": status, "code": code, "expires_at": expires}


# --------------------------------------------------------------------------- #
# Premium
# --------------------------------------------------------------------------- #
def premium_status(conn: sqlite3.Connection, user_id: str):
    return conn.execute(
        "SELECT site,plan,MAX(expires_at) AS until FROM premium_grants"
        " WHERE user_id=? AND revoked_at IS NULL AND expires_at>? GROUP BY site,plan",
        (user_id, db.now()),
    ).fetchall()


def verify_premium(conn: sqlite3.Connection, token: str) -> dict:
    if not token or len(token) > 200:
        return {"active": False}
    row = conn.execute(
        "SELECT site,plan,expires_at,revoked_at FROM premium_grants WHERE token_hash=?",
        (security.digest(token),),
    ).fetchone()
    if row is None:
        return {"active": False}
    active = row["revoked_at"] is None and row["expires_at"] > db.now()
    return {
        "active": active,
        "site": row["site"],
        "plan": row["plan"],
        "expires_at": row["expires_at"],
    }


# --------------------------------------------------------------------------- #
# Operator controls
# --------------------------------------------------------------------------- #
def adjust(conn: sqlite3.Connection, actor: str, user_id: str, points: float, memo: str) -> None:
    if not memo.strip():
        raise DomainError("An adjustment needs a reason.")
    if points == 0 or abs(points) > 10_000_000:
        raise DomainError("Adjustment is outside the allowed range.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (user_id, "", "adjust", points, f"adjust:{security.new_id()}",
             memo.strip()[:200], db.now()),
        )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (actor, "points.adjust", user_id, f"{points:g} {memo[:120]}", db.now()))
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


def set_status(conn: sqlite3.Connection, actor: str, redemption_id: str, status: str,
               tracking: str = "", note: str = "") -> None:
    allowed = {"approved", "shipped", "delivered", "cancelled", "issued"}
    if status not in allowed:
        raise DomainError("Unknown status.")
    row = conn.execute("SELECT * FROM redemptions WHERE id=?", (redemption_id,)).fetchone()
    if row is None:
        raise DomainError("No such redemption.")
    if row["status"] == "cancelled":
        raise DomainError("That redemption is already cancelled.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE redemptions SET status=?, tracking=?, note=?, updated_at=? WHERE id=?",
            (status, tracking.strip()[:120], note.strip()[:500], db.now(), redemption_id),
        )
        if status == "cancelled":
            conn.execute(
                "INSERT OR IGNORE INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["user_id"], "", "refund", float(row["points"]),
                 f"refund:{redemption_id}", "Redemption cancelled", db.now()),
            )
            if row["code_hash"]:
                conn.execute(
                    "UPDATE premium_grants SET revoked_at=? WHERE redemption_id=?",
                    (db.now(), redemption_id),
                )
            if row["reward_slug"]:
                conn.execute(
                    "UPDATE rewards SET stock=COALESCE(stock,0)+1 WHERE slug=? AND stock IS NOT NULL",
                    (row["reward_slug"],),
                )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (actor, "redemption." + status, redemption_id, tracking[:120], db.now()))
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------- #
# Claim codes: proof that the member controls the machine
# --------------------------------------------------------------------------- #
def issue_claim_code(conn: sqlite3.Connection, user_id: str) -> tuple[str, str]:
    """Return (code, expires_at). The plaintext is shown once and never stored."""
    code = "k3sclaim_" + secrets.token_urlsafe(24)
    expires = security.later_iso(30 * 60)
    conn.execute(
        "INSERT INTO claim_codes(id,user_id,code_hash,created_at,expires_at)"
        " VALUES(?,?,?,?,?)",
        (security.new_id(), user_id, security.digest(code), db.now(), expires),
    )
    return code, expires


def claim_with_code(conn: sqlite3.Connection, code: str, node: str) -> dict:
    if not code.startswith("k3sclaim_") or len(code) > 120:
        raise DomainError("That claim code is not valid.")
    node = (node or "").strip()[:120]
    if not node:
        raise DomainError("The machine did not report a node name.")
    now = db.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM claim_codes WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
            (security.digest(code), now),
        ).fetchone()
        if row is None:
            raise DomainError("That claim code is invalid or has expired.")
        total = conn.execute("SELECT * FROM node_totals WHERE node=?", (node,)).fetchone()
        if total is None:
            raise DomainError(
                "That node has not reported to the cluster yet. Wait for one points "
                "publication (five minutes) and try again."
            )
        active = conn.execute(
            "SELECT node FROM nodes WHERE user_id=? AND unlinked_at IS NULL", (row["user_id"],)
        ).fetchall()
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if user is None or user["status"] != "active":
            raise DomainError("That account is not available.")
        if any(item["node"] == node for item in active):
            raise DomainError("That node is already linked to this account.")
        if len(active) >= int(user["node_limit"] or 1):
            raise DomainError("This account has reached its node limit.")
        other = conn.execute(
            "SELECT user_id FROM nodes WHERE node=? AND unlinked_at IS NULL", (node,)
        ).fetchone()
        if other is not None:
            raise DomainError("That node is already linked to another account.")
        conn.execute(
            "UPDATE claim_codes SET used_at=?, node=? WHERE id=? AND used_at IS NULL",
            (now, node, row["id"]),
        )
        conn.execute(
            "INSERT INTO nodes(node,user_id,role,last_total,last_generated_at,linked_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(node) DO UPDATE SET user_id=excluded.user_id,"
            " last_total=excluded.last_total, last_generated_at=excluded.last_generated_at,"
            " linked_at=excluded.linked_at, unlinked_at=NULL",
            (node, row["user_id"], "volunteer", float(total["points"] or 0),
             total["generated_at"] or "", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ledger(user_id,node,kind,points,ref,memo,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (row["user_id"], "", "bonus", float(_cfg().welcome_points),
             f"welcome:{row['user_id']}", "Welcome credit for linking your first node", now),
        )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (row["user_id"], "node.claim", node, "", now))
        conn.execute("COMMIT")
        return {"node": node, "user_id": row["user_id"]}
    except DomainError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
