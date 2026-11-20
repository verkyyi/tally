import os, unittest
os.environ.setdefault("TALLY_AUTOSYNC", "0")
import archive, free_sources


class Boards(unittest.TestCase):
    OLD = '<h3>Engineering</h3><a href="/acme/jobs/1234567">A</a><a href="/acme/jobs/1234568">B</a><h3>Sales</h3><a href="/acme/jobs/1234569">C</a>'
    NEW = ('<h2 data-testid="job-count-header">85 jobs</h2><h3>Engineering</h3><a href="/acme/jobs/1234567">A</a>'
           '<script>{"jobPosts":{"count":50,"page":1,"total":85,"total_pages":2,"data":[]}}</script>')

    def test_a_board_on_one_page_is_counted_by_its_links(self):
        self.assertEqual(archive.board_count(self.OLD), 3)
        self.assertEqual(archive.board_count(self.OLD, "engineering"), 2)
        self.assertEqual(archive.board_count(self.OLD, "sales"), 1)

    def test_a_board_of_several_pages_gives_its_stated_total_and_no_team_count(self):
        self.assertEqual(archive.board_count(self.NEW), 85)
        self.assertIsNone(archive.board_count(self.NEW, "engineering"))

    def test_ashby_and_lever_pages(self):
        page = '<script>window.__appData = {"jobBoard":{"jobPostings":[{"id":"a"},{"id":"b","isListed":false},{"id":"c"}]}};</script>'
        self.assertEqual(archive.ashby_count(page), 2)
        self.assertIsNone(archive.ashby_count("<html>nothing</html>"))
        u = "https://jobs.lever.co/acme/6ed76ce8-4156-4b60-b120-403538bd66cd"
        self.assertEqual(archive.lever_count(f'<a href="{u}">x</a><a href="{u}/apply">y</a>', "acme"), 1)

    def test_every_board_is_named_once(self):
        self.assertEqual(len({n for n, _, _ in free_sources.JOBS}), len(free_sources.JOBS))
        self.assertEqual(free_sources.jobs_number("Vercel", "greenhouse", "vercel")[3], "free|jobs|greenhouse|vercel")


if __name__ == "__main__":
    unittest.main()
