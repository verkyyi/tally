import unittest

import cited


class Cited(unittest.TestCase):
    def test_a_plain_figure_with_its_citation(self):
        p = cited.parse("160 (July 2023)<ref>{{cite news |last=Roose |date=2023-07-11 |title=Inside |url=https://www.nytimes.com/2023/07/11/technology/a.html |work=NYT}}</ref>")
        self.assertEqual((p["value"], p["approx"]), (160.0, False))
        self.assertEqual(p["cites"][0]["url"], "https://www.nytimes.com/2023/07/11/technology/a.html")
        self.assertEqual(p["cites"][0]["date"], "2023-07-11")

    def test_about_is_kept_as_approximate(self):
        p = cited.parse("{{circa|1,000}} (2025)<ref>{{cite magazine |title=If |url=https://www.wired.com/story/x/ |date=30 May 2024 |archive-url=https://web.archive.org/web/2024/https://www.wired.com/story/x/}}</ref>")
        self.assertEqual((p["value"], p["approx"], p["cites"][0]["date"]), (1000.0, True, "2024-05-30"))
        self.assertIn("web.archive.org", p["cites"][0]["archive"])

    def test_a_bound_a_range_or_a_bare_reference_is_not_a_figure(self):
        ref = "<ref>{{cite web |url=https://example.com/a |date=April 2024}}</ref>"
        self.assertIsNone(cited.parse(">700 (2024)" + ref))
        self.assertIsNone(cited.parse("over 700" + ref))
        self.assertIsNone(cited.parse("1,000–1,500" + ref))
        self.assertIsNone(cited.parse("160 (July 2023)<ref name=Roose/>"))

    def test_the_report_must_state_the_figure_about_people(self):
        words = cited.FIELDS[0][2]
        text = "The lab is growing fast. It raised $1,300 million last year. Today Anthropic has about 1,300 employees across three offices. It plans more."
        self.assertEqual(cited.stated(1300, text, words), ("Today Anthropic has about 1,300 employees across three offices.", False))
        self.assertEqual(cited.stated(1300, "It raised $1,300 million from investors and staff were pleased.", words), (None, False))
        self.assertEqual(cited.stated(130, "It has 1,300 employees.", words), (None, False))
        far = "Staff were pleased. " + "x " * 80 + "The building has 1,300 windows facing the bay."
        self.assertEqual(cited.stated(1300, far, words), (None, False))

    def test_a_bound_in_the_report_is_told_apart(self):
        words = cited.FIELDS[0][2]
        self.assertTrue(cited.stated(2000, "OpenAI currently has more than 2,000 employees.", words)[1])
        self.assertTrue(cited.stated(2500, "It is now employing over 2,500 people globally.", words)[1])
        self.assertFalse(cited.stated(2300, "Headcount went from around 500 employees to about 2,300 today.", words)[1])

    def test_a_company_database_is_not_a_report(self):
        self.assertIsNone(cited.parse("47,600 (2020)<ref>{{cite web |url=https://www.zoominfo.com/c/x/9012358 |title=x}}</ref>"))
        self.assertIsNone(cited.parse("375 (2024)<ref>{{cite web |url=https://www.forbes.com/companies/anthropic/?sh=7 |date=April 2024}}</ref>"))
        self.assertTrue(cited.parse("375 (2024)<ref>{{cite web |url=https://www.forbes.com/sites/x/2024/04/01/story/ |date=April 2024}}</ref>"))

    def test_which_numbers_it_applies_to(self):
        self.assertTrue(cited.field_for({"title": "Anthropic · employee count"}))
        self.assertTrue(cited.field_for({"title": "Airtable · headcount"}))
        self.assertIsNone(cited.field_for({"title": "Anthropic · latest valuation"}))


if __name__ == "__main__":
    unittest.main()
