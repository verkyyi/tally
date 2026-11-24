import unittest

import race


class Race(unittest.TestCase):
    def rows(self):
        return [("A", "/c/a/x", [("2024-01-01T00:00:00Z", 10), ("2025-01-01T00:00:00Z", 50)]),
                ("B", "/c/b/x", [("2024-01-01T00:00:00Z", 20), ("2024-06-01T00:00:00Z", 30)]),
                ("C", "/c/c/x", [("2023-01-01T00:00:00Z", 5), ("2024-06-01T00:00:00Z", 40), ("2024-06-02T00:00:00Z", 40)]),
                ("D", "/c/d/x", [("2025-01-01T00:00:00Z", 1)])]

    def test_values_hold_until_the_next_disclosure_and_nothing_is_invented(self):
        f = race.frames(self.rows(), most=5)
        self.assertEqual(f["dates"], ["2024-01-01", "2024-04-02", "2024-07-02", "2024-10-01", "2025-01-01"])  # one pace, from when three have a figure
        by = {r["n"]: r["v"] for r in f["rows"]}
        self.assertEqual(by["A"], [10, 10, 10, 10, 50])
        self.assertEqual(by["B"], [20, 20, 30, 30, 30])
        self.assertEqual(by["C"], [5, 5, 40, 40, 40])
        self.assertEqual(by["D"], [None, None, None, None, 1])

    def test_too_little_history_gives_no_replay(self):
        self.assertIsNone(race.frames(self.rows()[:2]))
        self.assertIsNone(race.frames([(n, p, pts[:1]) for n, p, pts in self.rows()]))

    def test_long_histories_are_thinned_and_only_ever_ranked_companies_stay(self):
        rows = [(f"N{i}", f"/c/n{i}/x", [(f"20{10 + d // 12:02d}-{d % 12 + 1:02d}-01", (i + 1) * (d + 1)) for d in range(120)]) for i in range(15)]
        f = race.frames(rows, top=5, most=40)
        self.assertEqual(len(f["dates"]), 40)
        self.assertEqual((f["dates"][0], f["dates"][-1]), ("2010-01-01", "2019-12-01"))
        self.assertEqual({r["n"] for r in f["rows"]}, {f"N{i}" for i in range(10, 15)})


if __name__ == "__main__":
    unittest.main()
