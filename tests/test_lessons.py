import os
os.environ['TALLY_AUTOSYNC'] = '0'
import tempfile
import unittest
from unittest.mock import patch

import db
import lessons


class LessonsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch('db.DB_PATH', tmp.name + '/test.db'); p.start(); self.addCleanup(p.stop)
        db.init()

    def test_kinds(self):
        self.assertEqual(lessons.kind_of("Harvey latest valuation"), "valuation")
        self.assertEqual(lessons.kind_of("Suno · total funding raised"), "funding")
        self.assertEqual(lessons.kind_of("Cohere · latest round size"), "round")
        self.assertEqual(lessons.kind_of("how many people work at Lovable"), "headcount")
        self.assertIsNone(lessons.kind_of("something else entirely"))

    def test_good_sites_first_and_dead_ends_left_out(self):
        for _ in range(3):
            lessons.note("valuation", "https://seo.example/x", False, "The cited page must contain the quoted numeric evidence")
            lessons.note("valuation", "https://news.example/a", True)
        lessons.note("valuation", "https://mixed.example/a", True); lessons.note("valuation", "https://mixed.example/b", False, "no figure")
        res = [{"url": "https://seo.example/y"}, {"url": "https://unknown.example/"}, {"url": "https://mixed.example/c"}, {"url": "https://news.example/b"}]
        self.assertEqual([r["url"].split("/")[2] for r in lessons.rank("valuation", res)], ["news.example", "mixed.example", "unknown.example"])
        self.assertTrue(lessons.dead("valuation", "https://seo.example/z"))
        self.assertIsNone(lessons.dead("headcount", "https://seo.example/z"))   # a dead end for one kind is not one for another
        self.assertEqual(lessons.rank(None, res), res)

    def test_excluded_sources_are_never_learned(self):
        lessons.note("valuation", "https://sacra.com/c/x", True)
        self.assertFalse(db.q("SELECT * FROM lessons_source"))

    def test_brief_and_switch(self):
        for _ in range(2):
            lessons.note("valuation", "https://news.example/a", True)
        db.run("INSERT INTO lessons_entity(company,pages_json,status,ticker,at) VALUES(?,?,?,?,?)", ("Acme", '["https://acme.com/news/series-b"]', "listed", "ACME", "x"))
        b = lessons.brief("Acme latest valuation")
        self.assertIn("news.example (2)", b); self.assertIn("Acme is listed (ACME)", b); self.assertIn("https://acme.com/news/series-b", b)
        self.assertEqual(lessons.brief("something else entirely"), "")
        with patch.dict(os.environ, {"TALLY_LESSONS": "0"}):
            self.assertEqual(lessons.brief("Acme latest valuation"), "")


if __name__ == "__main__":
    unittest.main()
