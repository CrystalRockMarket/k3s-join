#!/usr/bin/env python3
"""Offline integration tests for the Earn service. No network, no real mail."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet

from earn import create_app, db, domain
from earn.config import Config

CSRF_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')
CODE_RE = re.compile(r"k3sclaim_[A-Za-z0-9_-]+")
TOKEN_RE = re.compile(r"kprem_[A-Za-z0-9_-]+")


class EarnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="earn-test-"))
        self.snapshot = self.tmp / "points.json"
        self.write_snapshot(0, {"spare-pc": 0})
        cfg = Config(
            data_dir=self.tmp,
            db_path=self.tmp / "earn.db",
            secret_key="test-secret-key",
            fernet_key=Fernet.generate_key().decode(),
            public_origin="http://localhost",
            rp_id="localhost",
            rp_name="Kituwa Cluster",
            cookie_secure=False,
            cookie_name="k3s_dev_session",
            session_idle_minutes=30,
            session_days=7,
            smtp_host="",
            smtp_port=465,
            smtp_user="",
            smtp_password="",
            smtp_from="Kituwa <support@kituwa.com>",
            smtp_reply_to="support@kituwa.com",
            smtp_tls=True,
            smtp_ssl=True,
            oidc_issuer="https://auth.kituwa.com",
            oidc_client_id="",
            oidc_client_secret="",
            oidc_redirect_uri="http://localhost/admin/oidc/callback",
            oidc_consumer_client_id="",
            oidc_consumer_client_secret="",
            oidc_consumer_redirect_uri="http://localhost/account/oidc/callback",
            oidc_allowed_group="operators",
            oidc_allowed_sub="",
            points_snapshot=self.snapshot,
            welcome_points=500,
            max_pending_orders=3,
            trust_proxy=False,
            testing=True,
        )
        self.cfg = cfg
        self.app = create_app(cfg)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def csrf(self, path: str) -> str:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        match = CSRF_RE.search(response.get_data(as_text=True))
        self.assertIsNotNone(match, path)
        return match.group(1)

    def write_snapshot(self, seq: int, nodes: dict) -> None:
        stamp = (datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
                 + timedelta(minutes=5 * seq)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.snapshot.write_text(json.dumps({
            "generated_at": stamp,
            "total_points": sum(nodes.values()),
            "nodes": [{"node": name, "points": points} for name, points in nodes.items()],
        }))

    def ingest(self, seq: int, nodes: dict) -> None:
        self.write_snapshot(seq, nodes)
        conn = db.connect(self.cfg.db_path)
        try:
            domain.ingest(conn, domain.read_snapshot(self.snapshot))
        finally:
            conn.close()

    def db(self):
        return db.connect(self.cfg.db_path)

    def signup(self, email="worker@example.com", display="Worker One",
               password="a long unique passphrase 42"):
        token = self.csrf("/account/signup/")
        response = self.client.post("/account/signup/", data={
            "_csrf": token, "email": email, "display_name": display,
            "password": password, "accept": "1", "next": "/earn/",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conn = self.db()
        try:
            user = conn.execute("SELECT * FROM users WHERE email_lower=?", (email.lower(),)).fetchone()
            verify = conn.execute(
                "SELECT * FROM tokens WHERE user_id=? AND kind='verify'", (user["id"],)
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(user)
        self.assertIsNotNone(verify)
        return user, verify

    def verify(self, user, verify_row):
        # The plaintext token is not stored; reissue one directly for the test.
        from earn import security
        with self.app.app_context():
            token = security.issue_token(user["id"], "verify", 3600)
        page = self.client.get("/account/verify/?token=" + token)
        csrf = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        response = self.client.post("/account/verify/",
                                    data={"_csrf": csrf, "token": token})
        self.assertEqual(response.status_code, 302)
        conn = self.db()
        try:
            row = conn.execute("SELECT email_verified_at FROM users WHERE id=?", (user["id"],)).fetchone()
        finally:
            conn.close()
        self.assertTrue(row["email_verified_at"])

    def login(self, email="worker@example.com", password="a long unique passphrase 42"):
        token = self.csrf("/account/login/")
        response = self.client.post("/account/login/", data={
            "_csrf": token, "email": email, "password": password, "next": "/earn/",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        return response

    def claim(self):
        page = self.client.get("/earn/claim/")
        self.assertEqual(page.status_code, 200)
        match = CODE_RE.search(page.get_data(as_text=True))
        self.assertIsNotNone(match, "no claim code on page")
        response = self.client.post("/api/v1/earn/claim",
                                    json={"code": match.group(0), "node": "spare-pc"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])
        return match.group(0)

    def balance(self, user_id):
        conn = self.db()
        try:
            return domain.balance(conn, user_id)
        finally:
            conn.close()

    # -- tests -----------------------------------------------------------
    def test_public_pages_and_auth_methods(self):
        for path in ("/earn/", "/earn/rules/", "/earn/docs/", "/healthz"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        payload = self.client.get("/.well-known/auth-methods.json").get_json()
        self.assertEqual(payload["order"], ["passkey", "pocketid", "password"])
        self.assertTrue(payload["email_verification"])

    def test_signup_verify_login_and_csrf(self):
        # A write without a CSRF token is refused.
        response = self.client.post("/account/signup/", data={
            "email": "x@example.com", "display_name": "Nope Nope",
            "password": "a long unique passphrase 42", "accept": "1",
        })
        self.assertEqual(response.status_code, 403)

        user, _ = self.signup()
        self.verify(user, _)
        self.client.post("/account/logout/", data={"_csrf": self.csrf("/earn/")})
        self.login()
        self.assertIn("Your balance", self.client.get("/earn/").get_data(as_text=True))

    def test_claim_and_ingest_credit(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        # Baseline was 1000; 500 more work plus the 500 welcome credit.
        self.ingest(2, {"spare-pc": 1500})
        self.assertAlmostEqual(self.balance(user["id"]), 1000.0, places=1)
        # Same snapshot again is a no-op.
        self.ingest(2, {"spare-pc": 1500})
        self.assertAlmostEqual(self.balance(user["id"]), 1000.0, places=1)
        # A ledger reset never becomes a debt.
        self.ingest(3, {"spare-pc": 100})
        self.assertAlmostEqual(self.balance(user["id"]), 1000.0, places=1)

    def test_claim_code_is_single_use(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        code = self.claim()
        again = self.client.post("/api/v1/earn/claim", json={"code": code, "node": "other"})
        self.assertEqual(again.status_code, 400)
        bad = self.client.post("/api/v1/earn/claim", json={"code": "k3sclaim_nope", "node": "x"})
        self.assertEqual(bad.status_code, 400)

    def test_no_physical_rewards_exist(self):
        conn = self.db()
        try:
            kinds = {row["kind"] for row in conn.execute("SELECT kind FROM rewards")}
        finally:
            conn.close()
        self.assertEqual(kinds, {"digital"})
        page = self.client.get("/earn/").get_data(as_text=True)
        self.assertNotIn("sticker", page.lower())
        self.assertIn("Premium", page)

    def test_physical_row_is_rejected(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        self.ingest(2, {"spare-pc": 61000})
        conn = self.db()
        try:
            conn.execute("INSERT INTO rewards(slug,title,kind,cost_points,stock,active,"
                         "description) VALUES('mug','Mug','physical',100,10,1,'x')")
        finally:
            conn.close()
        token = self.csrf("/earn/redeem/mug/")
        response = self.client.post("/earn/redeem/mug/", data={"_csrf": token,
                                                               "name": "x", "line1": "y",
                                                               "city": "z", "postal": "1",
                                                               "country": "US"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only Kituwa Premium", response.get_data(as_text=True))

    def test_premium_cancel_refunds_and_revokes(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        self.ingest(2, {"spare-pc": 61000})  # 60,500
        token = self.csrf("/earn/redeem/premium-30/")
        response = self.client.post("/earn/redeem/premium-30/", data={"_csrf": token},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertAlmostEqual(self.balance(user["id"]), 45500.0, places=1)
        conn = self.db()
        try:
            grant = conn.execute("SELECT * FROM premium_grants").fetchone()
            redemption = conn.execute("SELECT * FROM redemptions").fetchone()
        finally:
            conn.close()
        from earn import security
        with self.app.app_context():
            plain = security.decrypt(grant["token_enc"])
        self.assertTrue(self.client.post("/api/v1/earn/premium/verify",
                                         json={"token": plain}).get_json()["active"])

        conn = self.db()
        try:
            conn.execute("UPDATE users SET is_operator=1 WHERE id=?", (user["id"],))
        finally:
            conn.close()
        self.client.post("/account/logout/", data={"_csrf": self.csrf("/earn/")})
        self.login()
        self.assertEqual(self.client.get("/admin/orders/").status_code, 200)
        csrf = self.csrf("/admin/orders/")
        response = self.client.post(f"/admin/orders/{redemption['id']}/status", data={
            "_csrf": csrf, "status": "cancelled", "note": "Test cancellation",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertAlmostEqual(self.balance(user["id"]), 60500.0, places=1)
        self.assertFalse(self.client.post("/api/v1/earn/premium/verify",
                                          json={"token": plain}).get_json()["active"])

    def test_insufficient_balance_and_isolation(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        token = self.csrf("/earn/redeem/premium-30/")
        response = self.client.post("/earn/redeem/premium-30/", data={"_csrf": token})
        self.assertEqual(response.status_code, 400)
        self.assertIn("more points", response.get_data(as_text=True))
        # Another member cannot read the first member's receipt.
        conn = self.db()
        try:
            conn.execute("INSERT INTO redemptions(id,user_id,reward_slug,points,status,created_at,updated_at)"
                         " VALUES('other',?, 'premium-30', 5000, 'issued', ?, ?)",
                         (user["id"], db.now(), db.now()))
        finally:
            conn.close()
        other = self.app.test_client()
        # An unauthenticated client is redirected, not shown the receipt.
        self.assertEqual(other.get("/earn/receipt/other/").status_code, 302)

    def test_premium_issue_stack_and_verify(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        self.ingest(2, {"spare-pc": 61000})  # 60,500 balance
        for _ in range(2):
            token = self.csrf("/earn/redeem/premium-30/")
            response = self.client.post("/earn/redeem/premium-30/", data={"_csrf": token})
            self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conn = self.db()
        try:
            grant = conn.execute("SELECT * FROM premium_grants WHERE user_id=? ORDER BY expires_at DESC LIMIT 1", (user["id"],)).fetchone()
        finally:
            conn.close()
        until = datetime.strptime(grant["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertGreater(until, datetime.now(timezone.utc) + timedelta(days=55))
        page = self.client.get("/earn/premium/")
        self.assertEqual(page.status_code, 200)
        match = TOKEN_RE.search(page.get_data(as_text=True))
        self.assertIsNotNone(match)
        good = self.client.post("/api/v1/earn/premium/verify", json={"token": match.group(0)})
        self.assertTrue(good.get_json()["active"])
        bad = self.client.post("/api/v1/earn/premium/verify", json={"token": "kprem_nope"})
        self.assertFalse(bad.get_json()["active"])

    def test_rate_limit_login(self):
        self.signup()
        self.client.post("/account/logout/", data={"_csrf": self.csrf("/earn/")})
        token = self.csrf("/account/login/")
        codes = []
        for _ in range(6):
            codes.append(self.client.post("/account/login/", data={
                "_csrf": token, "email": "worker@example.com", "password": "wrong password here",
            }).status_code)
        self.assertIn(429, codes)

    def test_unlink_stops_credit(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.ingest(1, {"spare-pc": 1000})
        self.claim()
        self.ingest(2, {"spare-pc": 2000})
        before = self.balance(user["id"])
        csrf = self.csrf("/earn/claim/")
        response = self.client.post("/earn/nodes/spare-pc/unlink/", data={"_csrf": csrf})
        self.assertEqual(response.status_code, 302)
        self.ingest(3, {"spare-pc": 9000})
        self.assertAlmostEqual(self.balance(user["id"]), before, places=1)

    def test_operator_is_not_reachable_by_consumer(self):
        user, verify_row = self.signup()
        self.verify(user, verify_row)
        self.assertEqual(self.client.get("/admin/").status_code, 404)
        self.assertEqual(self.client.get("/admin/oidc/start").status_code, 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)
