"""SQLite schema and small query helpers for the Earn service."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  email_lower TEXT NOT NULL UNIQUE,
  email_verified_at TEXT,
  password_hash TEXT,
  display_name TEXT NOT NULL,
  display_lower TEXT NOT NULL UNIQUE,
  webauthn_handle BLOB,
  status TEXT NOT NULL DEFAULT 'active',
  leaderboard INTEGER NOT NULL DEFAULT 1,
  node_limit INTEGER NOT NULL DEFAULT 1,
  is_operator INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'consumer',
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  ua TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id, kind);

CREATE TABLE IF NOT EXISTS challenges (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  user_id TEXT,
  challenge BLOB NOT NULL,
  binding TEXT NOT NULL,
  handle BLOB,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id BLOB NOT NULL UNIQUE,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL DEFAULT 0,
  transports TEXT NOT NULL DEFAULT '',
  aaguid TEXT NOT NULL DEFAULT '',
  device_type TEXT NOT NULL DEFAULT '',
  backed_up INTEGER NOT NULL DEFAULT 0,
  label TEXT NOT NULL DEFAULT 'Passkey',
  created_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_id);

CREATE TABLE IF NOT EXISTS oidc_states (
  id TEXT PRIMARY KEY,
  state_hash TEXT NOT NULL UNIQUE,
  verifier TEXT NOT NULL,
  nonce TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  window_start TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_totals (
  node TEXT PRIMARY KEY,
  points REAL NOT NULL DEFAULT 0,
  generated_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
  node TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'volunteer',
  last_total REAL NOT NULL DEFAULT 0,
  last_generated_at TEXT NOT NULL DEFAULT '',
  linked_at TEXT NOT NULL,
  unlinked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes(user_id);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  points REAL NOT NULL,
  ref TEXT NOT NULL UNIQUE,
  memo TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id, created_at);

CREATE TABLE IF NOT EXISTS rewards (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  cost_points REAL NOT NULL,
  stock INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  description TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '',
  plan TEXT NOT NULL DEFAULT '',
  term_days INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS claim_codes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  node TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_claim_user ON claim_codes(user_id, created_at);

CREATE TABLE IF NOT EXISTS premium_grants (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  redemption_id TEXT NOT NULL,
  site TEXT NOT NULL,
  plan TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  token_enc TEXT NOT NULL DEFAULT '',
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_grants_user ON premium_grants(user_id, site);

CREATE TABLE IF NOT EXISTS redemptions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reward_slug TEXT NOT NULL REFERENCES rewards(slug),
  points REAL NOT NULL,
  status TEXT NOT NULL,
  address_enc TEXT NOT NULL DEFAULT '',
  code_hash TEXT NOT NULL DEFAULT '',
  code_enc TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  tracking TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_redemptions_user ON redemptions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_redemptions_status ON redemptions(status, created_at);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        # created_at on nodes is TEXT; keep the schema explicit rather than
        # relying on SQLite's flexible typing for the one typo-proof column.
        conn.execute("UPDATE nodes SET unlinked_at=NULL WHERE unlinked_at=''")
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', '1')"
        )
    finally:
        conn.close()


SEED_REWARDS = (
    {
        "slug": "premium-30",
        "title": "Kituwa Premium — 30 days",
        "kind": "digital",
        "cost_points": 15000,
        "stock": None,
        "description": "One month of Kituwa Premium: the member briefing, early access to new tools, and a verified token for participating Kituwa sites.",
        "site": "premium",
        "plan": "premium",
        "term_days": 30,
        "sort_order": 10,
    },
    {
        "slug": "premium-90",
        "title": "Kituwa Premium — 90 days",
        "kind": "digital",
        "cost_points": 36000,
        "stock": None,
        "description": "Three months of Kituwa Premium. Saves 20% over three monthly redemptions.",
        "site": "premium",
        "plan": "premium",
        "term_days": 90,
        "sort_order": 20,
    },
    {
        "slug": "premium-365",
        "title": "Kituwa Premium — 1 year",
        "kind": "digital",
        "cost_points": 120000,
        "stock": None,
        "description": "A full year of Kituwa Premium, the member briefing and early access to new tools.",
        "site": "premium",
        "plan": "premium",
        "term_days": 365,
        "sort_order": 30,
    },
)

def seed(conn: sqlite3.Connection) -> None:
    # The programme is digital-only. A physical row from an early draft is
    # never sold and is hidden and deactivated on upgrade.
    conn.execute("UPDATE rewards SET active=0 WHERE kind='physical'")
    for raw in SEED_REWARDS:
        item = {"site": "", "plan": "", "term_days": 0, "stock": None, **raw}
        conn.execute(
            "INSERT OR IGNORE INTO rewards"
            "(slug,title,kind,cost_points,stock,active,description,site,plan,term_days,sort_order)"
            " VALUES(:slug,:title,:kind,:cost_points,:stock,1,:description,"
            "COALESCE(:site,''),COALESCE(:plan,''),COALESCE(:term_days,0),:sort_order)",
            item,
        )
