"""Earn service configuration. Environment only; no secrets in the repo.

The service runs on kituwa-cloud-1 behind nginx. Its database is a small,
single-host SQLite file: the host holds the request-time state (accounts,
sessions, redemptions), while the points ledger itself stays on the worker and
is published to the host as a read-only snapshot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    data_dir: Path
    db_path: Path
    secret_key: str
    fernet_key: str
    public_origin: str
    rp_id: str
    rp_name: str
    cookie_secure: bool
    cookie_name: str
    session_idle_minutes: int
    session_days: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_reply_to: str
    smtp_tls: bool
    smtp_ssl: bool
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str
    oidc_allowed_group: str
    oidc_allowed_sub: str
    points_snapshot: Path
    welcome_points: int
    max_pending_orders: int
    trust_proxy: bool
    testing: bool


def load() -> Config:
    base = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get("EARN_DATA", base / "data" / "earn")).resolve()
    secure = _bool("EARN_COOKIE_SECURE", True)
    testing = _bool("EARN_TESTING", False)
    return Config(
        data_dir=data,
        db_path=Path(os.environ.get("EARN_DB", data / "earn.db")).resolve(),
        secret_key=os.environ.get("EARN_SECRET_KEY", "dev-only-not-a-secret"),
        fernet_key=os.environ.get("EARN_FERNET_KEY", ""),
        public_origin=os.environ.get("EARN_PUBLIC_ORIGIN", "http://127.0.0.1:8141").rstrip("/"),
        rp_id=os.environ.get("EARN_RP_ID", "localhost"),
        rp_name=os.environ.get("EARN_RP_NAME", "Kituwa Cluster"),
        cookie_secure=secure,
        cookie_name="__Host-k3s_session" if secure else "k3s_dev_session",
        session_idle_minutes=_int("EARN_SESSION_IDLE_MINUTES", 30),
        session_days=_int("EARN_SESSION_DAYS", 7),
        smtp_host=os.environ.get("EARN_SMTP_HOST", "").strip(),
        smtp_port=_int("EARN_SMTP_PORT", 465),
        smtp_user=os.environ.get("EARN_SMTP_USER", "").strip(),
        smtp_password=os.environ.get("EARN_SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("EARN_SMTP_FROM", "Kituwa Cluster <admin@kituwa.com>").strip(),
        smtp_reply_to=os.environ.get("EARN_SMTP_REPLY_TO", "support@kituwa.com").strip(),
        smtp_tls=_bool("EARN_SMTP_TLS", True),
        smtp_ssl=_bool("EARN_SMTP_SSL", True),
        oidc_issuer=os.environ.get("EARN_OIDC_ISSUER", "https://auth.kituwa.com").rstrip("/"),
        oidc_client_id=os.environ.get("EARN_OIDC_CLIENT_ID", "").strip(),
        oidc_client_secret=os.environ.get("EARN_OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get(
            "EARN_OIDC_REDIRECT_URI", "https://k3s.kituwa.com/admin/oidc/callback"
        ),
        oidc_allowed_group=os.environ.get("EARN_OIDC_ALLOWED_GROUP", "operators").strip(),
        oidc_allowed_sub=os.environ.get("EARN_OIDC_ALLOWED_SUB", "").strip(),
        points_snapshot=Path(
            os.environ.get("EARN_POINTS_SNAPSHOT", "/var/www/k3s.kituwa.com/points/points.json")
        ),
        welcome_points=_int("EARN_WELCOME_POINTS", 500),
        max_pending_orders=_int("EARN_MAX_PENDING_ORDERS", 3),
        trust_proxy=_bool("EARN_TRUST_PROXY", True),
        testing=testing,
    )
