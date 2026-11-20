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
