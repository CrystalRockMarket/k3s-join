"""Consumer accounts, passkeys, email verification, recovery, operator SSO."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import (
    Blueprint, current_app, g, jsonify, redirect, render_template, request, url_for,
)

from . import db, passkeys, security

bp = Blueprint("auth", __name__)

MAX_BODY = 16 * 1024


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _cfg():
    return current_app.config["EARN"]


def _field(name: str, limit: int = 200) -> str:
    value = request.form.get(name, "")
    return value if isinstance(value, str) else ""


def _clean_email(value: str) -> str:
    return value.strip()


def _valid_email(value: str) -> bool:
    return bool(value and len(value) <= 254 and security.EMAIL_RE.match(value))


def _valid_display(value: str) -> bool:
    return bool(security.DISPLAY_RE.match(value or ""))


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/earn/"
    if "\\" in value or len(value) > 200:
        return "/earn/"
    return value


def _user_by_email(conn, email: str):
    return conn.execute(
        "SELECT * FROM users WHERE email_lower=?", (email.strip().lower(),)
    ).fetchone()


def _send_verify(user_row) -> bool:
    token = security.issue_token(user_row["id"], "verify", 24 * 3600)
    link = f"{_cfg().public_origin}/account/verify/?token={urllib.parse.quote(token)}"
    return security.send_mail(
        user_row["email"],
        "Confirm your Kituwa Cluster account",
        "Confirm your email address for the Kituwa Cluster Earn programme.\n\n"
        f"Open this link within 24 hours (it works once):\n{link}\n\n"
        "If you did not create an account, ignore this message.\n",
    )


def _send_reset(user_row) -> bool:
    token = security.issue_token(user_row["id"], "reset", 15 * 60)
    link = f"{_cfg().public_origin}/account/reset/?token={urllib.parse.quote(token)}"
    return security.send_mail(
        user_row["email"],
        "Reset your Kituwa Cluster password",
        "A password reset was requested for this account. It expires in 15 minutes.\n\n"
        f"Open this link (it works once):\n{link}\n\n"
        "If this was not you, ignore this message. Your password has not changed.\n",
    )


def _login_response(user_row, next_path: str, kind: str = "consumer"):
    security.destroy_session()
    token = security.create_session(user_row["id"], kind)
    response = redirect(_safe_next(next_path))
    return security.set_session_cookie(response, token)


# --------------------------------------------------------------------------- #
# Signup and login
# --------------------------------------------------------------------------- #
@bp.get("/account/signup/")
def signup():
    return render_template("account/signup.html", next=_safe_next(request.args.get("next")))


@bp.post("/account/signup/")
def signup_post():
    refused = security.require_csrf()
    if refused:
        return refused
    if security.limited("signup:" + security.client_ip(), 10, 3600):
        return render_template(
            "account/signup.html", error="Too many attempts. Try again later.", next=_safe_next(_field("next"))
        ), 429

    email = _clean_email(_field("email"))
    display = _field("display_name").strip()
    password = _field("password", 200)
    next_path = _safe_next(_field("next"))

    error = ""
    if not _valid_email(email):
        error = "Enter a valid email address."
    elif not _valid_display(display):
        error = "Use 3–32 letters, numbers, spaces, dots, dashes or underscores."
    elif not _field("accept"):
        error = "Accept the terms to create an account."
    else:
        problem = security.password_problem(password)
        if problem:
            error = problem
    if error:
        return render_template("account/signup.html", error=error, email=email,
                               display_name=display, next=next_path), 400

    conn = db.connect(_cfg().db_path)
    try:
        if _user_by_email(conn, email):
            # Same response as success would give: never confirm an address.
            return render_template(
                "account/check_email.html",
                email=email,
                note="If that address can be used, a confirmation link is on its way.",
            )
        if conn.execute(
            "SELECT 1 FROM users WHERE display_lower=?", (display.lower(),)
        ).fetchone():
            return render_template("account/signup.html", error="That display name is taken.",
                                   email=email, display_name=display, next=next_path), 409
        user_id = security.new_id()
        handle = secrets.token_bytes(32)
        conn.execute(
            "INSERT INTO users(id,email,email_lower,password_hash,display_name,display_lower,"
            "webauthn_handle,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, email, email.lower(), security.hash_password(password), display,
             display.lower(), handle, db.now()),
        )
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()

    sent = _send_verify(user)
    response = _login_response(user, "/account/verify-notice/")
    return response


@bp.get("/account/login/")
def login():
    return render_template("account/login.html", next=_safe_next(request.args.get("next")))


@bp.post("/account/login/")
def login_post():
    refused = security.require_csrf()
    if refused:
        return refused
    email = _clean_email(_field("email"))
    password = _field("password", 200)
    next_path = _safe_next(_field("next"))
    ip = security.client_ip()
    acct_key = "login:acct:" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]
    if security.limited("login:ip:" + ip, 30, 900) or security.limited(acct_key, 5, 900):
        return render_template(
            "account/login.html", error="Too many attempts. Wait a few minutes.", next=next_path
        ), 429

    conn = db.connect(_cfg().db_path)
    try:
        user = _user_by_email(conn, email)
        ok = security.check_password(user["password_hash"] if user else None, password)
        if not ok or user is None or user["status"] != "active":
            return render_template(
                "account/login.html",
                error="Email or password is not correct.",
                email=email, next=next_path,
            ), 401
        if security.needs_rehash(user["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (security.hash_password(password), user["id"]),
            )
        user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    finally:
        conn.close()
    return _login_response(user, next_path)


@bp.get("/account/verify-notice/")
@security.login_required
def verify_notice():
    return render_template("account/verify_notice.html")


@bp.get("/account/verify/")
def verify():
    token = request.args.get("token", "")
    return render_template("account/verify.html", token=token)


@bp.post("/account/verify/")
def verify_post():
    refused = security.require_csrf()
    if refused:
        return refused
    token = _field("token", 200)
    user_id = security.consume_token(token, "verify")
    if not user_id:
        return render_template("account/verify.html", token="", error="That link is invalid or expired."), 400
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute(
            "UPDATE users SET email_verified_at=? WHERE id=? AND email_verified_at IS NULL",
            (db.now(), user_id),
        )
    finally:
        conn.close()
    return redirect(url_for("earn.index") + "?verified=1")


@bp.post("/account/verify/resend/")
@security.login_required
def verify_resend():
    refused = security.require_csrf()
    if refused:
        return refused
    if g.user.get("email_verified_at"):
        return redirect(url_for("earn.index"))
    if security.limited("verify:" + g.user["id"], 3, 3600):
        return redirect(url_for("auth.verify_notice") + "?slow=1")
    conn = db.connect(_cfg().db_path)
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    finally:
        conn.close()
    sent = _send_verify(user)
    return redirect(url_for("auth.verify_notice") + ("?sent=1" if sent else "?failed=1"))


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
@bp.get("/account/forgot/")
def forgot():
    return render_template("account/forgot.html")


@bp.post("/account/forgot/")
def forgot_post():
    refused = security.require_csrf()
    if refused:
        return refused
    email = _clean_email(_field("email"))
    if security.limited("forgot:ip:" + security.client_ip(), 5, 3600):
        return render_template("account/check_email.html", email=email,
                               note="If that address has an account, a reset link is on its way."), 429
    if _valid_email(email):
        conn = db.connect(_cfg().db_path)
        try:
            user = _user_by_email(conn, email)
        finally:
            conn.close()
        if user is not None:
            _send_reset(user)
    return render_template("account/check_email.html", email=email,
                           note="If that address has an account, a reset link is on its way.")


@bp.get("/account/reset/")
def reset():
    return render_template("account/reset.html", token=request.args.get("token", ""))


@bp.post("/account/reset/")
def reset_post():
    refused = security.require_csrf()
    if refused:
        return refused
    token = _field("token", 200)
    password = _field("password", 200)
    problem = security.password_problem(password)
    if problem:
        return render_template("account/reset.html", token=token, error=problem), 400
    user_id = security.consume_token(token, "reset")
    if not user_id:
        return render_template("account/reset.html", token="", error="That link is invalid or expired."), 400
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (security.hash_password(password), user_id))
    finally:
        conn.close()
    security.revoke_user_sessions(user_id)
    return redirect(url_for("auth.login") + "?reset=1")


# --------------------------------------------------------------------------- #
# Passkeys
# --------------------------------------------------------------------------- #
def _json_body() -> dict:
    if not request.is_json:
        raise ValueError("expected JSON")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("expected an object")
    return data


@bp.post("/account/passkey/login/begin")
def passkey_login_begin():
    refused = security.require_csrf()
    if refused:
        return refused
    if security.limited("pklogin:" + security.client_ip(), 40, 300):
        return jsonify({"ok": False, "error": "Too many attempts."}), 429
    try:
        flow_id, options = passkeys.begin_authentication()
    except Exception:
        current_app.logger.warning("passkey begin failed")
        return jsonify({"ok": False, "error": "Passkeys are not available right now."}), 503
    return jsonify({"ok": True, "flow": flow_id, "options": json.loads(options)})


@bp.post("/account/passkey/login/finish")
def passkey_login_finish():
    refused = security.require_csrf()
    if refused:
        return refused
    try:
        data = _json_body()
        flow_id = str(data.get("flow", ""))
        credential = data.get("credential")
        user_id = passkeys.finish_authentication(flow_id, credential)
    except passkeys.PasskeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid passkey response."}), 400
    conn = db.connect(_cfg().db_path)
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    token = security.create_session(user_id)
    response = jsonify({"ok": True, "next": "/earn/"})
    return security.set_session_cookie(response, token)


@bp.post("/account/passkey/register/begin")
@security.login_required
def passkey_register_begin():
    refused = security.require_csrf()
    if refused:
        return refused
    conn = db.connect(_cfg().db_path)
    try:
        row = conn.execute("SELECT webauthn_handle FROM users WHERE id=?", (g.user["id"],)).fetchone()
        handle = row["webauthn_handle"] if row else None
        if not handle:
            handle = secrets.token_bytes(32)
            conn.execute("UPDATE users SET webauthn_handle=? WHERE id=?", (handle, g.user["id"]))
        g.user["webauthn_handle"] = handle
    finally:
        conn.close()
    try:
        flow_id, options = passkeys.begin_registration(g.user)
    except Exception:
        current_app.logger.warning("passkey registration begin failed")
        return jsonify({"ok": False, "error": "A passkey could not be started."}), 503
    return jsonify({"ok": True, "flow": flow_id, "options": json.loads(options)})


@bp.post("/account/passkey/register/finish")
@security.login_required
def passkey_register_finish():
    refused = security.require_csrf()
    if refused:
        return refused
    try:
        data = _json_body()
        flow_id = str(data.get("flow", ""))
        credential = data.get("credential")
        label = str(data.get("label", "Passkey"))[:40] or "Passkey"
        result = passkeys.finish_registration(flow_id, credential)
    except passkeys.PasskeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid passkey response."}), 400
    if result["user_id"] != g.user["id"]:
        return jsonify({"ok": False, "error": "That passkey could not be linked."}), 403
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute(
            "INSERT INTO webauthn_credentials(id,user_id,credential_id,public_key,sign_count,"
            "transports,aaguid,device_type,backed_up,label,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (security.new_id(), g.user["id"], result["credential_id"], result["public_key"],
             result["sign_count"], result["transports"], result["aaguid"],
             result["device_type"], result["backed_up"], label, db.now()),
        )
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (g.user["id"], "passkey.add", result["credential_id"].hex()[:24], label, db.now()))
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.get("/account/")
@security.login_required
def account():
    conn = db.connect(_cfg().db_path)
    try:
        keys = conn.execute(
            "SELECT id,label,created_at,last_used_at,backed_up FROM webauthn_credentials"
            " WHERE user_id=? ORDER BY created_at", (g.user["id"],)
        ).fetchall()
        nodes = conn.execute(
            "SELECT node,linked_at,last_total FROM nodes WHERE user_id=? AND unlinked_at IS NULL"
            " ORDER BY linked_at", (g.user["id"],)
        ).fetchall()
        balance = conn.execute(
            "SELECT COALESCE(SUM(points),0) AS n FROM ledger WHERE user_id=?", (g.user["id"],)
        ).fetchone()["n"]
        grants = conn.execute(
            "SELECT site,plan,expires_at FROM premium_grants WHERE user_id=? AND revoked_at IS NULL"
            " AND expires_at > ? ORDER BY expires_at DESC", (g.user["id"], db.now())
        ).fetchall()
        redemptions = conn.execute(
            "SELECT r.id,r.status,r.points,r.created_at,r.tracking,r.code_enc,r.expires_at,"
            "w.title,w.kind FROM redemptions r JOIN rewards w ON w.slug=r.reward_slug"
            " WHERE r.user_id=? ORDER BY r.created_at DESC LIMIT 20", (g.user["id"],)
        ).fetchall()
    finally:
        conn.close()
    rows = []
    for item in redemptions:
        row = dict(item)
        row["code"] = security.decrypt(row.pop("code_enc"))
        rows.append(row)
    return render_template("account/index.html", keys=keys, nodes=nodes, balance=balance,
                           grants=grants, redemptions=rows, saved=request.args.get("saved"))


@bp.post("/account/profile/")
@security.login_required
def profile_post():
    refused = security.require_csrf()
    if refused:
        return refused
    display = _field("display_name").strip()
    leaderboard = 1 if _field("leaderboard") == "1" else 0
    error = ""
    if not _valid_display(display):
        error = "Use 3–32 letters, numbers, spaces, dots, dashes or underscores."
    conn = db.connect(_cfg().db_path)
    try:
        if not error:
            other = conn.execute(
                "SELECT id FROM users WHERE display_lower=? AND id<>?",
                (display.lower(), g.user["id"]),
            ).fetchone()
            if other:
                error = "That display name is taken."
        if not error:
            conn.execute("UPDATE users SET display_name=?, display_lower=?, leaderboard=? WHERE id=?",
                         (display, display.lower(), leaderboard, g.user["id"]))
    finally:
        conn.close()
    if error:
        return redirect(url_for("auth.account") + "?error=" + urllib.parse.quote(error))
    return redirect(url_for("auth.account") + "?saved=1")


@bp.post("/account/password/")
@security.login_required
def password_post():
    refused = security.require_csrf()
    if refused:
        return refused
    current = _field("current", 200)
    new = _field("new", 200)
    conn = db.connect(_cfg().db_path)
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
        if not security.check_password(user["password_hash"], current):
            return redirect(url_for("auth.account") + "?error=Current+password+is+not+correct")
        problem = security.password_problem(new)
        if problem:
            return redirect(url_for("auth.account") + "?error=" + urllib.parse.quote(problem))
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (security.hash_password(new), g.user["id"]))
    finally:
        conn.close()
    security.revoke_user_sessions(g.user["id"])
    response = redirect(url_for("auth.login") + "?changed=1")
    return security.clear_session_cookie(response)


@bp.post("/account/logout/")
def logout():
    refused = security.require_csrf()
    if refused:
        return refused
    security.destroy_session()
    response = redirect(url_for("earn.index"))
    return security.clear_session_cookie(response)


# --------------------------------------------------------------------------- #
# Operator SSO (Pocket ID)
# --------------------------------------------------------------------------- #
def _discovery() -> dict:
    cfg = _cfg()
    if not cfg.oidc_client_id or not cfg.oidc_client_secret:
        raise RuntimeError("OIDC is not configured")
    url = cfg.oidc_issuer + "/.well-known/openid-configuration"
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def _oidc_creds(mode: str) -> tuple[str, str, str]:
    cfg = _cfg()
    if mode == "consumer":
        client_id = cfg.oidc_consumer_client_id
        secret = cfg.oidc_consumer_client_secret
        redirect_uri = cfg.oidc_consumer_redirect_uri
    else:
        client_id = cfg.oidc_client_id
        secret = cfg.oidc_client_secret
        redirect_uri = cfg.oidc_redirect_uri
    if not client_id or not secret:
        raise RuntimeError("OIDC is not configured for " + mode)
    return client_id, secret, redirect_uri


def _oidc_error(mode: str, message: str, status: int = 400):
    if mode == "consumer":
        return render_template("account/login.html", error=message, next="/earn/"), status
    return render_template("admin/login.html", error=message), status


def _oidc_start(mode: str):
    # A redirect is cheap; a shared NAT or Cloudflare tunnel must not lock a
    # first-time login out. Sixty starts per five minutes is still bounded.
    if security.limited(f"oidc:start:{mode}:" + security.client_ip(), 60, 300):
        return "Too many attempts.", 429
    try:
        meta = _discovery()
        client_id, _secret, redirect_uri = _oidc_creds(mode)
    except Exception:
        return _oidc_error(mode, "Identity provider is unavailable.", 503)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute(
            "DELETE FROM oidc_states WHERE expires_at <= ? OR used_at IS NOT NULL",
            (db.now(),),
        )
        conn.execute(
            "INSERT INTO oidc_states(id,state_hash,verifier,nonce,mode,created_at,expires_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (security.new_id(), security.digest(state), verifier, nonce, mode,
             db.now(), security.later_iso(600)),
        )
    finally:
        conn.close()
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email groups",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return redirect(meta["authorization_endpoint"] + "?" + query)


@bp.get("/admin/oidc/start")
def oidc_start():
    return _oidc_start("operator")


@bp.get("/account/oidc/start")
def consumer_oidc_start():
    return _oidc_start("consumer")


@bp.get("/admin/oidc/callback")
@bp.get("/account/oidc/callback")
def oidc_callback():
    state = request.args.get("state", "")
    code = request.args.get("code", "")
    if not state or not code or len(state) > 200 or len(code) > 2000:
        return _oidc_error("consumer", "The sign-in response was incomplete.")
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM oidc_states WHERE state_hash=? AND used_at IS NULL AND expires_at>?",
            (security.digest(state), db.now()),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return _oidc_error("consumer", "That sign-in expired. Try again.")
        conn.execute("UPDATE oidc_states SET used_at=? WHERE id=?", (db.now(), row["id"]))
        mode = str(row["mode"] or "operator")
        conn.execute("COMMIT")
    finally:
        conn.close()
    try:
        client_id, client_secret, redirect_uri = _oidc_creds(mode)
        meta = _discovery()
        tokens = _post_form(meta["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": row["verifier"],
        })
        access = tokens.get("access_token", "")
        if not access:
            raise RuntimeError("no access token")
        who = _get_json(meta["userinfo_endpoint"], access)
    except Exception:
        current_app.logger.warning("OIDC exchange failed for %s", mode)
        return _oidc_error(mode, "The identity provider rejected the sign-in.", 401)

    subject = str(who.get("sub", ""))[:128]
    email = str(who.get("email", "")).strip().lower()[:254]
    groups = who.get("groups") or who.get("group") or []
    if isinstance(groups, str):
        groups = [groups]
    groups = [str(item) for item in groups]

    if mode == "operator":
        allowed = _cfg().oidc_allowed_group
        is_allowed = subject == _cfg().oidc_allowed_sub or allowed in groups
        if not subject or not is_allowed:
            return _oidc_error("operator", "That identity is not an operator on this site.", 403)
        conn = db.connect(_cfg().db_path)
        try:
            user = conn.execute("SELECT * FROM users WHERE email_lower=?", (email,)).fetchone() if email else None
            if user is None:
                uid = security.new_id()
                display = (str(who.get("name") or who.get("preferred_username") or "Operator")
                           .strip()[:32] or "Operator")
                base = display
                n = 1
                while conn.execute("SELECT 1 FROM users WHERE display_lower=?", (display.lower(),)).fetchone():
                    n += 1
                    display = (base[:28] + " " + str(n))[:32]
                conn.execute(
                    "INSERT INTO users(id,email,email_lower,email_verified_at,display_name,"
                    "display_lower,is_operator,created_at) VALUES(?,?,?,?,?,?,1,?)",
                    (uid, email or f"{subject}@operator.invalid", email or f"{subject}@operator.invalid",
                     db.now(), display, display.lower(), db.now()),
                )
                user_id = uid
            else:
                conn.execute("UPDATE users SET is_operator=1 WHERE id=?", (user["id"],))
                user_id = user["id"]
            conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                         (subject, "operator.login", user_id, email[:120], db.now()))
        finally:
            conn.close()
        security.destroy_session()
        token = security.create_session(user_id, "operator")
        response = redirect("/admin/")
        return security.set_session_cookie(response, token)

    # Consumer Pocket ID: the client is scoped to the members group. Do not
    # merge into an existing password account by email alone (protocol 01).
    if not subject or not email:
        return _oidc_error("consumer", "The identity provider did not return a verified email.", 401)
    if who.get("email_verified") is False:
        return _oidc_error("consumer", "That email is not verified with Kituwa ID.", 401)
    conn = db.connect(_cfg().db_path)
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE pocketid_sub=?", (subject,)
        ).fetchone()
        if user is None:
            existing = conn.execute(
                "SELECT id FROM users WHERE email_lower=?", (email,)
            ).fetchone()
            if existing is not None:
                return _oidc_error(
                    "consumer",
                    "An account with this email already exists. Sign in with your password "
                    "and use a passkey here; Kituwa ID linking is not enabled yet.",
                    409,
                )
            uid = security.new_id()
            display = (str(who.get("name") or who.get("preferred_username") or email.split("@")[0])
                       .strip()[:32] or "Member")
            base = display
            n = 1
            while conn.execute("SELECT 1 FROM users WHERE display_lower=?", (display.lower(),)).fetchone():
                n += 1
                display = (base[:28] + " " + str(n))[:32]
            conn.execute(
                "INSERT INTO users(id,email,email_lower,email_verified_at,display_name,"
                "display_lower,pocketid_sub,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (uid, email, email, db.now(), display, display.lower(), subject, db.now()),
            )
            user_id = uid
        else:
            if user["status"] != "active":
                return _oidc_error("consumer", "That account is not available.", 403)
            user_id = user["id"]
        conn.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                     (subject, "member.login", user_id, "pocketid", db.now()))
    finally:
        conn.close()
    security.destroy_session()
    token = security.create_session(user_id, "consumer")
    response = redirect("/earn/")
    return security.set_session_cookie(response, token)
