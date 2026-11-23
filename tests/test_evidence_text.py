import unittest

import evidence


class QuoteText(unittest.TestCase):
    def test_typography_does_not_hide_a_quote(self):
        page = "This round brings Tailscale’s total funding to **$275 million** — a record."
        quote = "This round brings Tailscale's total funding to $275 million - a record."
        self.assertIn(evidence._squash(quote), evidence._squash(page))

    def test_other_words_still_differ(self):
        self.assertNotIn(evidence._squash("total funding to $285 million"), evidence._squash("total funding to $275 million"))


if __name__ == "__main__":
    unittest.main()
