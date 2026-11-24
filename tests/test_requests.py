import os
os.environ['TALLY_AUTOSYNC'] = '0'

import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import db
import main


class Requests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch('db.DB_PATH', tmp.name + '/test.db'); p.start(); self.addCleanup(p.stop)
        db.init()
        main._asked_by.clear()
        self.c = TestClient(main.app)
        self.h = {"user-agent": "Mozilla/5.0 (Macintosh) Safari/605"}

    def test_a_request_is_kept_once_and_has_a_private_status_page(self):
        self.assertEqual(self.c.get("/request", follow_redirects=False).status_code, 302)  # the search box is the one entrance
        self.assertEqual(self.c.get("/request?q=Figure+AI").status_code, 200)
        a = self.c.post("/api/request", json={"text": "  Figure AI  "}, headers=self.h).json()["path"]
        b = self.c.post("/api/request", json={"text": "figure ai"}, headers=self.h).json()["path"]
        self.assertEqual(a, b)  # the same words asked twice are one request
        page = self.c.get(a)
        self.assertIn("Figure AI", page.text)
        self.assertIn("noindex", page.text)
        self.assertIn("Research starts in a moment", page.text)
        db.run("UPDATE requests SET status='delivered', links=? WHERE id=1", ("/c/figure-ai\njavascript:x",))
        page = self.c.get(a).text
        self.assertIn('href="/c/figure-ai"', page)
        self.assertNotIn("javascript:", page)

    def test_progress_is_readable_while_the_research_runs(self):
        path = self.c.post("/api/request", json={"text": "Acme"}, headers=self.h).json()["path"]
        self.assertEqual(self.c.get("/api/request/open").json(), {"n": 1})
        db.run("UPDATE requests SET status='researching', steps=?, links=? WHERE id=1", ('["Searching: Acme valuation", "Reading: acme.com"]', "/c/acme"))
        d = self.c.get(path + ".json").json()
        self.assertEqual((d["status"], d["steps"][-1], d["links"]), ("researching", "Reading: acme.com", ["/c/acme"]))
        self.assertNotIn("email", d)
        self.assertEqual(self.c.get("/api/request/open").json(), {"n": 0})
        self.assertIn("Reading: acme.com", self.c.get(path).text)

    def test_a_delivered_request_is_a_page_of_the_companys_figures(self):
        path = self.c.post("/api/request", json={"text": "Acme"}, headers=self.h).json()["path"]
        mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,created_by,created_at,company,curated) VALUES('q','Acme · latest valuation','direct','USD','tally',?, 'Acme',1)", (db.now(),))
        for day, v in (("2024-01-10", 1e9), ("2025-06-01", 3e9)):
            db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote) VALUES(?,?,?,?,?,0,?)", (mid, v, day + "T00:00:00Z", "https://acme.example/news", "source_history", "valued at"))
        main.pp._PATHS.update(at=0.0)
        db.run("UPDATE requests SET status='delivered', note='No dated public record found for: revenue.', links='/c/acme', done_at=? WHERE id=1", (db.now(),))
        page = self.c.get(path).text
        for want in ("Acme: the numbers on record", "1 figures and 2 dated points", "latest valuation", "$3B", "history.csv", "What could not be verified", "revenue", "noindex"):
            self.assertIn(want, page)

    def test_continuing_a_research_is_for_pro_and_counts_who_reaches_for_it(self):
        path = self.c.post("/api/request", json={"text": "Acme"}, headers=self.h).json()["path"]
        tok = path.split("/")[-1]
        self.assertEqual(self.c.post(f"/api/request/{tok}/more", json={"text": "add revenue by year"}, headers=self.h).status_code, 404)  # not finished yet
        db.run("UPDATE requests SET status='delivered', links='/c/acme', session='s1' WHERE id=1")
        self.assertEqual(self.c.post(f"/api/request/{tok}/more", json={"text": "add revenue by year"}, headers=self.h).status_code, 401)
        self.assertEqual(db.one("SELECT n FROM fetches WHERE kind='refine_ask'")["n"], 1)
        with patch("main.viewer", return_value={"id": "u1", "plan": "free"}):
            self.assertEqual(self.c.post(f"/api/request/{tok}/more", json={"text": "add revenue by year"}, headers=self.h).status_code, 402)
        with patch("main.viewer", return_value={"id": "u1", "plan": "pro"}):
            new = self.c.post(f"/api/request/{tok}/more", json={"text": "add revenue by year"}, headers=self.h).json()["path"]
        row = db.one("SELECT * FROM requests WHERE token=?", (new.split("/")[-1],))
        self.assertEqual((row["parent"], row["user"], row["status"], row["links"]), (1, "u1", "received", "/c/acme"))

    def test_a_comparison_request_ends_on_the_comparison_page(self):
        path = self.c.post("/api/request", json={"text": "Acme vs Beta"}, headers=self.h).json()["path"]
        db.run("UPDATE requests SET status='delivered', links=? WHERE id=1", ("/vs/acme/beta\n/c/acme\n/c/beta",))
        r = self.c.get(path, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/vs/acme/beta"))

    def test_one_visitor_cannot_take_every_slot(self):
        main._asked_by.clear()
        codes = [self.c.post("/api/request", json={"text": f"Company {i}"}, headers=self.h | {"x-forwarded-for": "203.0.113.9"}).status_code for i in range(5)]
        self.assertEqual(codes, [200, 200, 200, 200, 429])
        self.assertEqual(self.c.post("/api/request", json={"text": "Another"}, headers=self.h | {"x-forwarded-for": "203.0.113.10"}).status_code, 200)

    def test_an_email_is_optional_checked_and_never_shown(self):
        self.assertEqual(self.c.post("/api/request", json={"text": "Acme", "email": "not an address"}, headers=self.h).status_code, 422)
        path = self.c.post("/api/request", json={"text": "Acme", "email": "reader@example.com"}, headers=self.h).json()["path"]
        self.assertEqual(db.one("SELECT email FROM requests")["email"], "reader@example.com")
        self.assertNotIn("reader@example.com", self.c.get(path).text)

    def test_scripts_links_and_filled_traps_are_refused(self):
        for body, headers in (({"text": "see https://x.example"}, self.h), ({"text": "Acme", "site": "x"}, self.h), ({"text": "a"}, self.h),
                              ({"text": "Acme"}, {"user-agent": "Googlebot/2.1"})):
            self.assertEqual(self.c.post("/api/request", json=body, headers=headers).status_code, 422, body)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM requests")["n"], 0)
        self.assertEqual(self.c.get("/request/nope").status_code, 404)


    def test_a_price_looked_up_for_the_reader_shows_on_their_page_only(self):
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("request_worker", os.path.join(os.path.dirname(main.__file__), "infra", "request_worker.py"))
        w = importlib.util.module_from_spec(spec); sys.modules["request_worker"] = w; spec.loader.exec_module(w)
        self.assertTrue(w.WANTS_NOW.search("Nvidia stock price"))
        self.assertTrue(w.WANTS_NOW.search("Nvidia revenue and the latest share price"))
        self.assertFalse(w.WANTS_NOW.search("Cursor pro plan price"))
        import core
        mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,created_by,created_at,curated,company) VALUES('q','Acme · shares outstanding','direct','shares','system',?,1,'Acme')", (db.now(),))
        db.run("INSERT INTO observations(metric_id,value,as_of,origin,seeded) VALUES(?,?,?,?,0)", (mid, 1000000, db.now(), "source_history"))
        path = self.c.post("/api/request", json={"text": "Acme stock price"}, headers=self.h).json()["path"]
        now = json.dumps({"label": "share price", "value": 12.5, "unit": "USD", "date": "2026-11-24", "url": "https://ir.acme.example/stock", "quote": "ACME closed at $12.50"})
        db.run("UPDATE requests SET status='delivered', links='/c/acme', now=? WHERE token=?", (now, path.split("/")[-1]))
        page = self.c.get(path)
        self.assertIn("Looked up for you: share price $12.5", page.text)
        self.assertIn("market value of about $12.5M", page.text)
        self.assertIn("noindex", page.headers.get("x-robots-tag", ""))
        self.assertNotIn("12.5", self.c.get("/c/acme").text)  # the company's public page never carries it

    def test_a_market_value_is_worked_out_from_a_stated_close_and_filed_shares(self):
        import core
        mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,created_by,created_at,curated,company) VALUES('q','Acme · shares outstanding','direct','shares','system',?,1,'Acme')", (db.now(),))
        db.run("INSERT INTO observations(metric_id,value,as_of,origin,seeded) VALUES(?,?,?,?,0)", (mid, 2000000, "2026-09-30T00:00:00Z", "source_history"))
        said = core.derived_market_cap("Acme", 12.5, "2026-11-20", "https://news.example/acme", "Acme closed at $12.50 on November 20, 2026")
        self.assertIn("25,000,000 written", said)
        o = db.one("SELECT o.* FROM observations o JOIN metrics m ON m.id=o.metric_id WHERE m.title='Acme · market capitalization'")
        self.assertEqual((o["value"], o["as_of"][:10], o["label"]), (25000000.0, "2026-11-20", core.APPROX))
        self.assertIn("closing price $12.50 x 2,000,000 shares", o["quote"])
        self.assertIn("already on record", core.derived_market_cap("Acme", 13, "2026-11-20", "https://news.example/acme", "x"))
        self.assertIn("no shares", core.derived_market_cap("Nobody", 13, "2026-11-20", "https://news.example/x", "x"))

if __name__ == "__main__":
    unittest.main()
