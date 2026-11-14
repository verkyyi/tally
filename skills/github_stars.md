# Skill: GitHub stars
Read the star count of the repository named in the question.
- Prefer the GitHub API page (api.github.com/repos/{owner}/{repo}) and read `stargazers_count`: it is exact, while the web page rounds to "9.3k".
- If only a rounded figure is available, return null rather than a rounded number.
- The value must be for the repository itself, not an organization total or a fork.
