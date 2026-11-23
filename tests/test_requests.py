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
        self.c = TestClient(main.app)
        self.h = {"user-agent": "Mozilla/5.0 (Macintosh) Safari/605"}

    def test_a_request_is_kept_once_and_has_a_private_status_page(self):
        self.assertEqual(self.c.get("/request").status_code, 200)
        a = self.c.post("/api/request", json={"text": "  Figure AI  "}, headers=self.h).json()["path"]
        b = self.c.post("/api/request", json={"text": "figure ai"}, headers=self.h).json()["path"]
        self.assertEqual(a, b)  # the same words asked twice are one request
        page = self.c.get(a)
        self.assertIn("Figure AI", page.text)
        self.assertIn("noindex", page.text)
        self.assertIn("Research starts within a day", page.text)
        db.run("UPDATE requests SET status='delivered', links=? WHERE id=1", ("/c/figure-ai\njavascript:x",))
        page = self.c.get(a).text
        self.assertIn('href="/c/figure-ai"', page)
        self.assertNotIn("javascript:", page)

    def test_scripts_links_and_filled_traps_are_refused(self):
        for body, headers in (({"text": "see https://x.example"}, self.h), ({"text": "Acme", "site": "x"}, self.h), ({"text": "a"}, self.h),
                              ({"text": "Acme"}, {"user-agent": "Googlebot/2.1"})):
            self.assertEqual(self.c.post("/api/request", json=body, headers=headers).status_code, 422, body)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM requests")["n"], 0)
        self.assertEqual(self.c.get("/request/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
