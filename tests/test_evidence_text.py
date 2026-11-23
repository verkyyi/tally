import unittest

import evidence


class QuoteText(unittest.TestCase):
    def test_typography_does_not_hide_a_quote(self):
        page = "This round brings Tailscale’s total funding to **$275 million** — a record."
        quote = "This round brings Tailscale's total funding to $275 million - a record."
        self.assertIn(evidence._squash(quote), evidence._squash(page))

    def test_other_words_still_differ(self):
        self.assertNotIn(evidence._squash("total funding to $285 million"), evidence._squash("total funding to $275 million"))

    def test_the_sentence_that_states_a_value(self):
        page = "Sierra is an AI company.\nThe deal valued Sierra at $4.5 billion, up from $1 billion in January. More below."
        self.assertEqual(evidence.sentence(4500000000, page), "The deal valued Sierra at $4.5 billion, up from $1 billion in January.")
        self.assertIsNone(evidence.sentence(10000000000, page))

    def test_of_two_sentences_the_one_like_the_summary(self):
        page = "Rival Corp raised $400 million last year.\nSuno pocketed $400 million in its latest funding round, valuing it at $5.4 billion."
        self.assertTrue(evidence.sentence(400000000, page, "Suno Series D, $400 million at a $5.4 billion valuation").startswith("Suno pocketed"))

    def test_a_footnote_mark_is_not_the_figure(self):
        page = "After that, the founders contacted the general counsel about their results.[5]\nIn June 2025 the company raised $300 million at a $5 billion valuation.[9]"
        self.assertTrue(evidence.sentence(5000000000, page).startswith("In June 2025"))
        self.assertIsNone(evidence.sentence(5000000000, "They contacted the general counsel about their results.[5]"))


if __name__ == "__main__":
    unittest.main()
