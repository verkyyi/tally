import unittest

import coverage
import public_pages as pp


class Coverage(unittest.TestCase):
    def sets(self):
        m = lambda i, t: {"id": i, "title": t, "company": t.split(" · ")[0]}
        ms = [m(1, "Acme · latest valuation"), m(2, "Acme · total funding raised"), m(3, "Acme · latest round size"), m(4, "Acme · open roles, all teams"),
              m(5, "Beta · latest valuation"), m(6, "Beta · total funding raised"),
              m(7, "Big · quarterly revenue"), m(8, "Big · latest round size"),
              m(9, "lib · GitHub stars"), m(10, "lib · npm downloads, weekly"), m(11, "City · population")]
        return coverage.table(ms, {1, 2, 3, 4, 5, 7, 9, 10})

    def test_subjects_land_in_one_set_and_the_share_counts_values_only(self):
        private, listed = self.sets()
        self.assertEqual([r[0] for r in private[3]], ["Acme", "Beta"])  # fullest first
        self.assertAlmostEqual(private[4], 5 / 8)  # Beta's funding is tracked but has no value
        self.assertEqual([r[0] for r in listed[3]], ["Big"])  # a company with filed revenue is listed, whatever else it carries
        self.assertNotIn("lib", [r[0] for s in self.sets() for r in s[3]])  # a project that is not a company is outside the scope

    def test_page_shows_each_state(self):
        h = pp.coverage_page(self.sets())
        for want in ("What Tally covers", "2 followed", "1 complete", "tracked, no value yet", "not tracked yet", "/c/acme", "/coverage"):
            self.assertIn(want, h)
        self.assertNotIn("City", h)


    def test_capitals_do_not_split_a_company(self):
        ms = [{"id": 1, "title": "N8N · total funding raised"}, {"id": 2, "title": "n8n · latest valuation"}]
        rows = coverage.table(ms, {1, 2})[0][3]
        self.assertEqual(len(rows), 1)
        self.assertEqual(sum(1 for c in rows[0][1] if c), 2)

if __name__ == "__main__":
    unittest.main()
