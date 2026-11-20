import os, unittest, unittest.mock
os.environ.setdefault("TALLY_AUTOSYNC", "0")
import main


class SearchWords(unittest.TestCase):
    HAY = "profound · employee count how many employees does profound have? profound employees"

    def test_other_words_for_the_same_number(self):
        for q in ("profound employee counts", "how many people work at profound", "profound headcount", "Profound's staff"):
            self.assertTrue(main._matches(q.split(), self.HAY), q)

    def test_punctuation_and_domains(self):
        self.assertTrue(main._matches("hume.ai valuation".split(), "hume ai · latest valuation what is hume ai's latest reported valuation? usd"))
        self.assertTrue(main._matches(["what", "is", "anthropic's", "latest", "valuation?"], "anthropic · latest valuation what is anthropic's latest reported valuation?"))

    def test_a_different_company_or_kind_does_not_match(self):
        self.assertFalse(main._matches("openai employees".split(), self.HAY))
        self.assertFalse(main._matches("profound valuation".split(), self.HAY))


if __name__ == "__main__":
    unittest.main()


class TwoCompanies(unittest.TestCase):
    NAMES = {"anthropic": "Anthropic", "anthropic sdk": "Anthropic SDK", "openai": "OpenAI", "open webui": "Open WebUI", "hugging face": "Hugging Face", "microsoft": "Microsoft"}
    SIZES = {"Anthropic": 39, "Anthropic SDK": 2, "OpenAI": 26, "Open WebUI": 1, "Hugging Face": 3, "Microsoft": 5}

    def who(self, text):
        return main._subject_for(text.split(), self.NAMES, self.SIZES, {"msft": "Microsoft"})

    def test_the_start_of_a_name_finds_the_company_most_tracked(self):
        self.assertEqual(self.who("ant"), "Anthropic")
        self.assertEqual(self.who("open"), "OpenAI")
        self.assertEqual(self.who("hugging"), "Hugging Face")
        self.assertEqual(self.who("msft"), "Microsoft")

    def test_too_little_or_nothing_like_a_name_finds_no_one(self):
        self.assertIsNone(self.who("ai"))
        self.assertIsNone(self.who("valuation"))


class DatesInSearch(unittest.TestCase):
    def test_a_date_among_the_words(self):
        with unittest.mock.patch("db.now", return_value="2026-11-20T00:00:00Z"):
            self.assertEqual(main._when("anthropic valuation 2025".split())[:2], ("2025-12-31", "2025"))
            self.assertEqual(main._when("openai revenue march 2025".split()), ("2025-03-31", "Mar 2025", ["openai", "revenue"]))
            self.assertEqual(main._when("cursor stars q2 2025".split())[:2], ("2025-06-30", "Q2 2025"))
            self.assertEqual(main._when("cursor stars 2024-06-01".split())[0], "2024-06-01")
            self.assertEqual(main._when("anthropic valuation in 2026".split()), ("2026-11-20", "2026", ["anthropic", "valuation"]))
            self.assertEqual(main._when("anthropic valuation".split())[0], None)
            self.assertEqual(main._when(["2025"])[0], None)      # a year alone asks for nothing
            self.assertEqual(main._when("windows 1999 price".split())[0], None)
