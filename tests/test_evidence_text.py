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


if __name__ == "__main__":
    unittest.main()
