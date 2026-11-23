"""Security boundary: sessions, CSRF, rate limits, passwords, encryption.

Every request decision lives here so route handlers stay small and the
invariants are testable in one place.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, jsonify, make_response, redirect, request, url_for

from . import db

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
_DUMMY_HASH = HASHER.hash("earn-timing-equaliser-not-a-real-password")
EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
DISPLAY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{1,30}[A-Za-z0-9]\Z")
COUNTRY_RE = re.compile(r"[A-Z]{2}\Z")
PASSWORD_MIN = 15
PASSWORD_MAX = 128

# A small local blocklist. The check is deliberately cheap and never sends a
# password to a third party; the list covers the obvious service-name choices.
BLOCKED_PASSWORDS = {
    "passwordpassword", "passwordpassword1", "correcthorsebatterystaple",
    "kituwakituwakituwa", "k3sk3sk3sk3sk3s", "kituwacluster", "clusterkituwa",
    "letmeinletmein", "qwertyqwertyqwerty", "123456789012345",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=(), publickey-credentials-get=(self), publickey-credentials-create=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
}
CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'none'; frame-src 'none'"
)


# --------------------------------------------------------------------------- #
# Time, ids, encryption
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def later_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def new_id() -> str:
    return secrets.token_urlsafe(24)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    key = current_app.config["EARN"].fernet_key
    if not key:
        # Development only: a stable key derived from the dev signing key so a
        # restart can still read its own data. Production sets EARN_FERNET_KEY.
        key = base64.urlsafe_b64encode(
            hashlib.sha256(current_app.config["EARN"].secret_key.encode()).digest()
        ).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def password_problem(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    low = password.lower()
    if low in BLOCKED_PASSWORDS or "kituwa" in low or low.startswith("k3s"):
        return "That password is too easy to guess. Choose something unique."
    return ""


def hash_password(password: str) -> str:
    return HASHER.hash(password)


def check_password(stored: str | None, password: str) -> bool:
    if not stored:
        # Unknown accounts take a comparable verification path.
        try:
            HASHER.verify(_DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False
    try:
        return HASHER.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str | None) -> bool:
    return bool(stored) and HASHER.check_needs_rehash(stored)


# --------------------------------------------------------------------------- #
# Request context: IP, user agent
# --------------------------------------------------------------------------- #
def client_ip() -> str:
    cfg = current_app.config["EARN"]
    remote = request.remote_addr or ""
    if cfg.trust_proxy and remote in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:45]
    return remote[:45]


def user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:255]


# --------------------------------------------------------------------------- #
# Rate limits
# --------------------------------------------------------------------------- #
def hit(bucket: str, limit: int, window_seconds: int) -> bool:
    """Return True when the request is within the limit, False when throttled."""
    conn = db.connect(current_app.config["EARN"].db_path)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = now_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT count, window_start FROM rate_limits WHERE bucket=?", (bucket,)
        ).fetchone()
        if row is None or row["window_start"] < cutoff:
            conn.execute(
                "INSERT INTO rate_limits(bucket,count,window_start) VALUES(?,1,?)"
                " ON CONFLICT(bucket) DO UPDATE SET count=1, window_start=excluded.window_start",
                (bucket, now),
            )
            conn.execute("COMMIT")
            return True
        count = row["count"] + 1
        conn.execute("UPDATE rate_limits SET count=? WHERE bucket=?", (count, bucket))
        conn.execute("COMMIT")
        return count <= limit
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        # A limiter that cannot answer fails closed.
        return False
    finally:
        conn.close()


def limited(bucket: str, limit: int, window: int) -> bool:
    return not hit(bucket, limit, window)


# --------------------------------------------------------------------------- #
# Sessions and CSRF
# --------------------------------------------------------------------------- #
def _session_ttl(cfg) -> int:
    return cfg.session_idle_minutes * 60


def create_session(user_id: str, kind: str = "consumer") -> str:
    cfg = current_app.config["EARN"]
    token = secrets.token_urlsafe(32)
    sid = digest(token)
    conn = db.connect(cfg.db_path)
    try:
        conn.execute(
            "INSERT INTO sessions(id,user_id,csrf,kind,created_at,last_seen,expires_at,ip,ua)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                sid, user_id, secrets.token_urlsafe(24), kind, now_iso(), now_iso(),
                later_iso(cfg.session_days * 86400), client_ip(), user_agent(),
            ),
        )
        conn.execute(
            "UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user_id)
        )
    finally:
        conn.close()
    return token


def set_session_cookie(response, token: str):
    cfg = current_app.config["EARN"]
    response.set_cookie(
        cfg.cookie_name,
        token,
        max_age=cfg.session_days * 86400,
        secure=cfg.cookie_secure,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def clear_session_cookie(response):
    cfg = current_app.config["EARN"]
    response.delete_cookie(cfg.cookie_name, path="/", secure=cfg.cookie_secure, httponly=True)
    return response


def load_session():
    cfg = current_app.config["EARN"]
    token = request.cookies.get(cfg.cookie_name, "")
    if not token:
        return None
    sid = digest(token)
    conn = db.connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT s.csrf, s.kind, s.created_at AS session_created, s.expires_at,"
            " s.last_seen, u.id, u.email, u.email_verified_at, u.display_name, u.status,"
            " u.is_operator, u.node_limit, u.leaderboard, u.webauthn_handle"
            " FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?",
            (sid,),
        ).fetchone()
        if row is None:
            return None
        now = now_iso()
        if row["expires_at"] <= now or row["status"] != "active":
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            return None
        idle_cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=cfg.session_idle_minutes)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        if row["last_seen"] < idle_cutoff:
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            return None
        # Touch at most once a minute to keep writes bounded.
        if row["last_seen"] < (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"):
            conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, sid))
        return dict(row)
    finally:
        conn.close()


def destroy_session():
    cfg = current_app.config["EARN"]
    token = request.cookies.get(cfg.cookie_name, "")
    if token:
        conn = db.connect(cfg.db_path)
        try:
            conn.execute("DELETE FROM sessions WHERE id=?", (digest(token),))
        finally:
            conn.close()


def revoke_user_sessions(user_id: str) -> None:
    conn = db.connect(current_app.config["EARN"].db_path)
    try:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    finally:
        conn.close()


def csrf_token() -> str:
    if g.get("user") is not None:
        token = g.user.get("_csrf") or ""
        if token:
            return token
    token = getattr(g, "anon_csrf", "")
    if not token:
        token = secrets.token_urlsafe(24)
        g.anon_csrf = token
    return token


def csrf_ok() -> bool:
    expected = ""
    if g.get("user") is not None:
        expected = g.user.get("_csrf") or ""
    if not expected:
        expected = getattr(g, "anon_csrf", "") or request.cookies.get("k3s_csrf", "")
    provided = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("_csrf")
        or ""
    )
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def require_csrf():
    if not csrf_ok():
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "csrf"}), 403
        return "This form expired. Reload the page and try again.", 403
    return None


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=target))
        return view(*args, **kwargs)

    return wrapped


def verified_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.get("email_verified_at"):
            return redirect(url_for("auth.verify_notice"))
        return view(*args, **kwargs)

    return wrapped


def operator_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        if not g.user.get("is_operator"):
            return "Not found", 404
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------- #
# Tokens, mail
# --------------------------------------------------------------------------- #
def issue_token(user_id: str, kind: str, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = db.connect(current_app.config["EARN"].db_path)
    try:
        conn.execute(
            "INSERT INTO tokens(id,user_id,kind,token_hash,created_at,expires_at)"
            " VALUES(?,?,?,?,?,?)",
            (new_id(), user_id, kind, digest(token), now_iso(), later_iso(ttl_seconds)),
        )
    finally:
        conn.close()
    return token


def consume_token(token: str, kind: str) -> str | None:
    """Atomically consume a token and return its user id, or None."""
    if not token or len(token) > 200:
        return None
    conn = db.connect(current_app.config["EARN"].db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, user_id, expires_at FROM tokens WHERE token_hash=? AND kind=?"
            " AND used_at IS NULL",
            (digest(token), kind),
        ).fetchone()
        if row is None or row["expires_at"] < now_iso():
            conn.execute("COMMIT")
            return None
        updated = conn.execute(
            "UPDATE tokens SET used_at=? WHERE id=? AND used_at IS NULL",
            (now_iso(), row["id"]),
        )
        conn.execute("COMMIT")
        return row["user_id"] if updated.rowcount == 1 else None
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        return None
    finally:
        conn.close()


def send_mail(to: str, subject: str, text: str) -> bool:
    cfg = current_app.config["EARN"]
    if not cfg.smtp_host or not cfg.smtp_from:
        current_app.logger.warning("mail skipped: SMTP is not configured")
        return False
    message = EmailMessage()
    message["From"] = cfg.smtp_from
    message["To"] = to
    message["Subject"] = subject
    if cfg.smtp_reply_to:
        message["Reply-To"] = cfg.smtp_reply_to
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(text)
    try:
        if cfg.smtp_ssl:
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=12)
        try:
            if cfg.smtp_tls and not cfg.smtp_ssl:
                server.starttls()
            if cfg.smtp_user:
                server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(message)
        finally:
            server.quit()
        return True
    except (OSError, smtplib.SMTPException) as exc:
        current_app.logger.warning("mail failed: %s", type(exc).__name__)
        return False


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #
def init_app(app) -> None:
    @app.before_request
    def _load():
        g.user = load_session()
        if g.user is not None:
            g.user["_csrf"] = ""
            conn = db.connect(current_app.config["EARN"].db_path)
            try:
                row = conn.execute(
                    "SELECT csrf FROM sessions WHERE id=?", (digest(request.cookies.get(current_app.config["EARN"].cookie_name, "")),)
                ).fetchone()
                g.user["_csrf"] = row["csrf"] if row else ""
            finally:
                conn.close()
        # Anonymous double-submit cookie, created on first render.
        g.anon_csrf = request.cookies.get("k3s_csrf", "")

    @app.after_request
    def _headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Content-Security-Policy", CSP)
        cfg = current_app.config["EARN"]
        if cfg.cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        token = getattr(g, "anon_csrf", "")
        if token and request.cookies.get("k3s_csrf") != token:
            response.set_cookie(
                "k3s_csrf", token, max_age=3600, secure=cfg.cookie_secure,
                httponly=True, samesite="Lax", path="/",
            )
        if request.path.startswith(("/account", "/earn", "/admin")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.context_processor
    def _globals():
        return {
            "csrf": csrf_token(),
            "current_user": g.get("user"),
            "public_origin": current_app.config["EARN"].public_origin,
        }
