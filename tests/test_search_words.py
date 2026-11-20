import os, unittest
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
