"""WebAuthn ceremonies. No hand-rolled signature checks.

Challenges live in SQLite, are bound to the browser, expire after five minutes
and are consumed atomically by the finishing request.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from flask import current_app, g, request
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json, parse_authentication_credential_json
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import db

FLOW_TTL = 300


class PasskeyError(RuntimeError):
    """A ceremony step failed or expired. The message is safe to show."""


def _cfg():
    return current_app.config["EARN"]


def binding() -> str:
    """Bind a challenge to this browser, not to a guessable account id."""
    token = getattr(g, "anon_csrf", "") or request.cookies.get("k3s_csrf", "")
    raw = f"{token}|{request.headers.get('User-Agent','')[:120]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _clean_expired() -> None:
    conn = db.connect(_cfg().db_path)
    try:
        now = int(time.time())
        conn.execute(
            "DELETE FROM challenges WHERE expires_at <= ? OR consumed_at IS NOT NULL",
            (now,),
        )
    finally:
        conn.close()


def _create_flow(kind: str, challenge: bytes, user_id: str | None, handle: bytes) -> str:
    if kind not in {"reg", "auth"}:
        raise ValueError("invalid flow kind")
    _clean_expired()
    flow_id = secrets.token_hex(24)
    now = int(time.time())
    conn = db.connect(_cfg().db_path)
    try:
        conn.execute(
            "INSERT INTO challenges(id,kind,user_id,challenge,binding,handle,created_at,expires_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (flow_id, kind, user_id, challenge, binding(), handle, now, now + FLOW_TTL),
        )
    finally:
        conn.close()
    return flow_id


def _get_flow(flow_id: str, kind: str) -> dict:
    if not flow_id or len(flow_id) > 80:
        raise PasskeyError("This sign-in attempt has expired. Please try again.")
    now = int(time.time())
    conn = db.connect(_cfg().db_path)
    try:
        row = conn.execute(
            "SELECT id,kind,user_id,challenge,binding,handle FROM challenges"
            " WHERE id=? AND kind=? AND binding=? AND consumed_at IS NULL AND expires_at > ?",
            (flow_id, kind, binding(), now),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise PasskeyError("This sign-in attempt has expired. Please try again.")
    return dict(row)


def _consume(flow_id: str, kind: str) -> bool:
    conn = db.connect(_cfg().db_path)
    try:
        now = int(time.time())
        cursor = conn.execute(
            "UPDATE challenges SET consumed_at=? WHERE id=? AND kind=? AND binding=?"
            " AND consumed_at IS NULL AND expires_at > ?",
            (now, flow_id, kind, binding(), now),
        )
        return cursor.rowcount == 1
    finally:
        conn.close()


def begin_registration(user: dict) -> tuple[str, str]:
    conn = db.connect(_cfg().db_path)
    try:
        rows = conn.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id=?",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    handle = user.get("webauthn_handle") or secrets.token_bytes(32)
    options = generate_registration_options(
        rp_id=_cfg().rp_id,
        rp_name=_cfg().rp_name,
        user_name=user["email"],
        user_id=handle if isinstance(handle, bytes) else bytes(handle),
        user_display_name=user["display_name"],
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=bytes(row["credential_id"])) for row in rows
        ],
    )
    flow_id = _create_flow(
        "reg", options.challenge, user["id"], handle if isinstance(handle, bytes) else bytes(handle)
    )
    return flow_id, options_to_json(options)


def finish_registration(flow_id: str, credential: dict[str, Any]) -> dict[str, Any]:
    flow = _get_flow(flow_id, "reg")
    cfg = _cfg()
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=flow["challenge"],
            expected_rp_id=cfg.rp_id,
            expected_origin=cfg.public_origin,
            require_user_verification=False,
        )
    except Exception as exc:  # py_webauthn raises several types
        raise PasskeyError("The passkey could not be verified. Please try again.") from exc
    if not _consume(flow_id, "reg"):
        raise PasskeyError("This sign-in attempt has expired. Please try again.")
    transports = ""
    try:
        raw = credential.get("response", {})
        transports = ",".join(str(x) for x in raw.get("transports", []))[:120]
    except (AttributeError, TypeError):
        pass
    aaguid = getattr(verified, "aaguid", "")
    if isinstance(aaguid, (bytes, bytearray)):
        aaguid = bytes(aaguid).hex()
    device_type = getattr(verified, "credential_device_type", "")
    device_type = getattr(device_type, "value", str(device_type))
    return {
        "user_id": flow["user_id"],
        "credential_id": verified.credential_id,
        "public_key": verified.credential_public_key,
        "sign_count": int(verified.sign_count),
        "transports": transports,
        "aaguid": str(aaguid)[:64],
        "device_type": device_type[:32],
        "backed_up": 1 if verified.credential_backed_up else 0,
    }


def begin_authentication() -> tuple[str, str]:
    options = generate_authentication_options(
        rp_id=_cfg().rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=None,
    )
    flow_id = _create_flow("auth", options.challenge, None, b"")
    return flow_id, options_to_json(options)


def finish_authentication(flow_id: str, credential: dict[str, Any]) -> str:
    flow = _get_flow(flow_id, "auth")
    cfg = _cfg()
    try:
        parsed = parse_authentication_credential_json(json.dumps(credential))
    except Exception as exc:
        raise PasskeyError("That passkey is not recognised.") from exc
    conn = db.connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT id,user_id,credential_id,public_key,sign_count FROM webauthn_credentials"
            " WHERE credential_id=?",
            (parsed.raw_id,),
        ).fetchone()
        if row is None:
            raise PasskeyError("That passkey is not recognised.")
        user = conn.execute(
            "SELECT id,email_verified_at,status,webauthn_handle FROM users WHERE id=?",
            (row["user_id"],),
        ).fetchone()
        if user is None or user["status"] != "active":
            raise PasskeyError("That account is not available.")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=flow["challenge"],
                expected_rp_id=cfg.rp_id,
                expected_origin=cfg.public_origin,
                credential_public_key=bytes(row["public_key"]),
                credential_current_sign_count=int(row["sign_count"]),
                require_user_verification=False,
            )
        except Exception as exc:
            raise PasskeyError("That passkey could not be verified.") from exc
        provided = parsed.response.user_handle
        stored = bytes(user["webauthn_handle"]) if user["webauthn_handle"] else b""
        if not provided or not stored or not secrets.compare_digest(bytes(provided), stored):
            raise PasskeyError("That passkey does not match this account.")
        if not _consume(flow_id, "auth"):
            raise PasskeyError("This sign-in attempt has expired. Please try again.")
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count=?, last_used_at=? WHERE id=?",
            (int(verified.new_sign_count), db.now(), row["id"]),
        )
        return row["user_id"]
    finally:
        conn.close()
