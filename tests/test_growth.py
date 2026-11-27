import os
os.environ['TALLY_AUTOSYNC'] = '0'

import base64
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import db
import demand
import growth
import main

BROWSER = {"user-agent": "Mozilla/5.0 (Macintosh) Safari/605"}


class GrowthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        p = patch('db.DB_PATH', tmp.name + '/test.db'); p.start(); self.addCleanup(p.stop)
        db.init()
        main._years.update(at=0.0, set=set())
        self.c = TestClient(main.app)

    def landings(self):
        return {(r["path"], r["ref"]): r["n"] for r in db.q("SELECT path, ref, n FROM landings")}

    def test_landings_keep_the_exact_page_from_other_sites_and_assistants_only(self):
        self.c.get("/privacy", headers={**BROWSER, "referer": "https://www.google.com/"})
        self.c.get("/privacy", headers={**BROWSER, "referer": "https://www.google.com/"})
        self.c.get("/terms", headers={"user-agent": "Mozilla/5.0 AppleWebKit/537.36; compatible; ChatGPT-User/1.0; +https://openai.com/bot"})
        self.c.get("/terms", headers={"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
        self.c.get("/terms", headers=BROWSER)  # direct
        self.c.get("/compare?a=1&b=2", headers={**BROWSER, "referer": "https://www.google.com/"})  # never reached from a search result
        self.assertEqual(self.landings(), {("/privacy", "www.google.com"): 2, ("/terms", "bot:chatgpt-user"): 1})

    def test_token_is_a_signed_request(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        sent = {}

        def post(url, data, timeout):
            sent.update(url=url, **data)
            return MagicMock(json=lambda: {"access_token": "tok"}, raise_for_status=lambda: None)
        with patch("growth.httpx.post", post):
            self.assertEqual(growth._token({"client_email": "sa@x.iam", "private_key": pem}), "tok")
        head, claim, sig = sent["assertion"].split(".")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        k.public_key().verify(pad(sig), f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256())  # raises if wrong
        self.assertEqual(json.loads(pad(claim))["scope"], growth.SCOPE)
        self.assertEqual(sent["url"], "https://oauth2.googleapis.com/token")

    def test_token_from_an_owners_consent(self):
        sent = {}

        def post(url, data, timeout):
            sent.update(url=url, **data)
            return MagicMock(json=lambda: {"access_token": "tok2"}, raise_for_status=lambda: None)
        with patch("growth.httpx.post", post):
            self.assertEqual(growth._token({"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r"}), "tok2")
        self.assertEqual((sent["grant_type"], sent["refresh_token"]), ("refresh_token", "r"))

    def test_pull_stores_days_and_rows_and_unanswered_queries_become_asks(self):
        key = self.tmp + "/k.json"
        with open(key, "w") as f:
            json.dump({"client_email": "sa@x.iam", "private_key": "unused"}, f)
        days = [growth._ago(n) for n in (3, 2, 1)]
        totals = [{"keys": [d], "clicks": 1, "impressions": 100} for d in days]
        rows = [{"keys": [d, q, "https://tallycatalog.com/c/acme/valuation"], "clicks": 0, "impressions": 5, "position": 12.0}
                for d in days for q in ("acme valuation", "widgetco valuation", "what is acme")]
        rows += [{"keys": [days[0], "rareco funding", "https://tallycatalog.com/"], "clicks": 0, "impressions": 1, "position": 40.0}]
        with patch.dict(os.environ, {"TALLY_GSC_KEY": key}), patch("growth._token", lambda k: "t"), \
                patch("growth._query", lambda t, body: totals if body["dimensions"] == ["date"] else rows):
            self.assertEqual(growth.pull(), (3, 10))
            self.assertEqual(growth.pull(), (3, 10))  # read again: corrected, not doubled
        self.assertEqual(db.one("SELECT SUM(impressions) n FROM gsc")["n"], 300)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM gsc_rows")["n"], 10)
        self.assertEqual(db.one("SELECT path FROM gsc_rows LIMIT 1")["path"], "/c/acme/valuation")
        growth.to_asks(lambda q: q.startswith("acme"))
        growth.to_asks(lambda q: q.startswith("acme"))  # once a day each, however often it runs
        asks = {r["q"]: r["n"] for r in db.q("SELECT q, SUM(n) n FROM asks WHERE via='google' GROUP BY q")}
        self.assertEqual(asks, {"widgetco valuation": 3})  # shown on three days, unanswered, reads like a number; one day is not enough
        self.assertEqual([q for q, _ in demand.waiting(lambda q: False)], ["widgetco valuation"])

    def test_no_key_reads_nothing_and_daily_runs_once(self):
        with patch.dict(os.environ, {"TALLY_GSC_KEY": ""}):
            self.assertIsNone(growth.pull())
            self.assertEqual(growth.daily(lambda q: False), {"gsc": None, "asks": 0})
            self.assertIsNone(growth.daily(lambda q: False))

    def test_daily_survives_a_failing_read(self):
        with patch("growth.pull", side_effect=RuntimeError("revoked")):
            self.assertIn("revoked", growth.daily(lambda q: False)["error"])

    def test_ops_snapshot_needs_its_token(self):
        self.assertEqual(self.c.get("/ops/traffic.json").status_code, 404)
        with patch.dict(os.environ, {"TALLY_OPS_TOKEN": "s3cret"}):
            self.assertEqual(self.c.get("/ops/traffic.json", headers={"authorization": "Bearer nope"}).status_code, 404)
            r = self.c.get("/ops/traffic.json", headers={"authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"generated_at", "since", "search_console", "people", "assistants", "crawlers", "asked_not_found", "demand", "accounts"})

    def test_a_year_page_is_indexed_with_enough_values_or_when_google_shows_it(self):
        def obs(mid, day):
            db.run("INSERT INTO observations(metric_id, value, as_of, seeded) VALUES(?,?,?,0)", (mid, 1, day))
        for d in ("2024-01-01", "2024-05-01", "2024-09-01", "2025-02-01"):
            obs(1, d)
        self.assertEqual(main._indexed_years(), {(1, 2024)})
        db.run("INSERT INTO gsc_rows(day,query,path,clicks,impressions,position) VALUES('2026-11-20','acme 2025','/c/acme/valuation/2025',0,3,30)")
        main._years.update(at=0.0)
        with patch("main.pp.paths", lambda: {"by_path": {"/c/acme/valuation": 1}}):
            self.assertEqual(main._indexed_years(), {(1, 2024), (1, 2025)})

    def test_sitemap_does_not_list_what_robots_keeps_out(self):
        self.assertNotIn("/track<", main._sitemap_body())


if __name__ == "__main__":
    unittest.main()
