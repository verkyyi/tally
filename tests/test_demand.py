import os
os.environ['TALLY_AUTOSYNC'] = '0'
import tempfile
import unittest
from unittest.mock import patch

import db
import demand


class DemandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch('db.DB_PATH', tmp.name + '/test.db'); p.start(); self.addCleanup(p.stop)
        db.init()

    def ask(self, q, via, n, days_ago=0, found=0):
        db.run("INSERT INTO asks(day,q,via,found,n) VALUES(date('now', ?),?,?,?,?)", (f"-{days_ago} days", q, via, found, n))

    def test_wellformed(self):
        for q in ("telem ai latest valuation", "hume.ai valuation", "profound employee counts", "langfuse funding"):
            self.assertTrue(demand.wellformed(q), q)
        for q in ("msft nvida", "american", "openai vs anthropic revenue", "scale ai stock price", "${encodeuricomponent(q)}", "valuation",
                  "use edgar to find qualcomm's 10k filing as of september 26 and answer the following questions about revenue"):
            self.assertFalse(demand.wellformed(q), q)

    def test_one_visit_creates_nothing(self):
        self.ask("telem ai latest valuation", "site", 1)
        self.assertEqual(demand.waiting(lambda q: False), [])
        self.assertIsNone(demand.due(lambda q: False, lambda q: self.fail("must not create")))

    def test_repeated_asks_on_two_days_are_looked_up_once(self):
        self.ask("telem ai latest valuation", "site", 2, 1)
        self.ask("telem ai latest valuation", "request", 1, 0)
        self.ask("three asks one day funding", "site", 5, 0)
        self.assertEqual([q for q, _ in demand.waiting(lambda q: False)], ["telem ai latest valuation"])
        made = []
        self.assertEqual(demand.due(lambda q: False, lambda q: (made.append(q), ("failed", None))[1]), ("telem ai latest valuation", "failed"))
        self.assertIsNone(demand.due(lambda q: False, lambda q: self.fail("tried again too soon")))
        self.assertEqual(made, ["telem ai latest valuation"])

    def test_an_assistant_asking_counts_at_once_and_found_numbers_are_skipped(self):
        self.ask("langfuse funding", "agent", 1)
        self.ask("notion funding", "agent", 1)
        self.assertEqual([q for q, _ in demand.waiting(lambda q: q.startswith("notion"))], ["langfuse funding"])

    def test_daily_allowance(self):
        for i in range(8):
            self.ask(f"company{chr(97 + i)} valuation", "agent", 1)
        with patch.dict(os.environ, {"TALLY_DEMAND_PER_DAY": "2"}):
            done = [demand.due(lambda q: False, lambda q: ("published", {"id": 1})) for _ in range(4)]
        self.assertEqual(sum(1 for d in done if d), 2)


if __name__ == "__main__":
    unittest.main()
