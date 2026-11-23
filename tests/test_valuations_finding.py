import unittest

import public_pages as pp


class ValuationsFinding(unittest.TestCase):
    def rows(self):
        m = lambda i, name: {"id": i, "title": f"{name} · latest valuation", "company": name, "unit": "USD", "question": "q"}
        p = lambda d, v: {"at": d + "T00:00:00Z", "value": v}
        return [(m(1, "Acme"), p("2025-09-01", 10e9), p("2026-09-01", 48e9), 4.8), (m(2, "Beta"), p("2025-08-01", 6e9), p("2026-08-01", 3e9), 0.5)]

    def test_page_states_both_valuations_and_the_change(self):
        h = pp.valuations_page(self.rows(), [])
        for want in ("Acme", "×4.8", "-50%", "Sep 2025", "Lower than a year ago: Beta", "/findings/ai-valuations"):
            self.assertIn(want, h)

    def test_lede_reads_as_a_sentence(self):
        s = pp._valuations_lede(self.rows())
        self.assertIn("Acme is valued at", s)
        self.assertIn("4.8 times", s)
        self.assertIn("50% below", s)


if __name__ == "__main__":
    unittest.main()
