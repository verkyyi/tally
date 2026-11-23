import unittest

import research_import as ri


class ResearchImport(unittest.TestCase):
    def test_a_quote_states_the_value_however_the_page_writes_it(self):
        for value, quote in ((143e6, "said it had raised $143m to sit in the gap"), (89e9, "Data Center revenue of $89.0 billion, up 117%"),
                             (200e6, "ecosystem of 200+ million monthly active users"), (4, "the private beta month, produced four questions"),
                             (49000, "World,OWID_WRL,2011,49000"), (600e6, "superó los US$600 millones de ingresos"), (510, "now has 510 customers with trailing"),
                             (19.99, "the Standard plan now costs $19.99 a month")):
            self.assertTrue(ri.states(value, quote), quote)
        for value, quote in ((5e9, "raised $5 million"), (510, "now has 5,100 customers"), (20, "priced at $200 a month"), (2011, "no figure here")):
            self.assertFalse(ri.states(value, quote), quote)

    def test_only_points_whose_quote_is_on_the_page_are_kept_and_none_twice(self):
        page = "Acme said it now has 510 customers. A year earlier it had 400 customers."
        pts = [{"date": "2026-01-31", "value": 510, "source_url": "u", "quote": "it now has 510 customers"},
               {"date": "2025-01-31", "value": 400, "source_url": "u", "quote": "it had 400 customers"},
               {"date": "2024-01-31", "value": 300, "source_url": "u", "quote": "it had 300 customers"},
               {"date": "2023-01-31", "value": 200, "source_url": "gone", "quote": "200 customers"},
               {"date": "2022", "value": 100, "source_url": "u", "quote": "100 customers"}]
        checked = ri.check(pts, lambda url: page if url == "u" else None)
        self.assertEqual([v for _, v in checked], ["ok", "ok", "the quote is not on the page", "the page could not be opened", "no date or value"])
        self.assertEqual([p["date"] for p in ri.new_points(checked, {("2026-01-31", 510.0)})], ["2025-01-31"])


    def test_a_repeated_value_and_a_collector_of_figures_are_not_points(self):
        page = "valued at $5 billion. later valued at $32 billion. still valued at $32 billion."
        pts = [{"date": "2024-09-04", "value": 5e9, "source_url": "u", "quote": "valued at $5 billion"},
               {"date": "2025-04-11", "value": 32e9, "source_url": "u", "quote": "later valued at $32 billion"},
               {"date": "2026-07-27", "value": 32e9, "source_url": "u", "quote": "still valued at $32 billion"},
               {"date": "2026-08-01", "value": 40e9, "source_url": "https://www.crunchbase.com/organization/x", "quote": "valued at $40 billion"}]
        checked = ri.check(pts, lambda url: page)
        self.assertIn("collects figures", checked[3][1])
        self.assertEqual([p["date"] for p in ri.new_points(checked, set())], ["2024-09-04", "2025-04-11"])
        self.assertEqual(ri.new_points(checked, {("2025-04-11", 32e9)})[0]["date"], "2024-09-04")
        self.assertEqual(len(ri.new_points(checked, {("2025-04-11", 32e9)})), 1)  # the later repeat does not follow a value already on record


if __name__ == "__main__":
    unittest.main()
