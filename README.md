# ros_prwatch

Tools to find GitHub pull requests in ROS organizations that are not getting
attention from maintainers.

The scripts use the GitHub GraphQL API to measure how long PRs wait for a first
response, compare the experience of committers and non-committers, and list
open PRs where the author is still waiting on a reply.

## Setup

Requires Python 3.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file with a GitHub token (a classic token with no scopes, or a
fine-grained token with read-only public repository access, is enough for
public repos):

```
GITHUB_TOKEN=ghp_...
```

List the GitHub organizations to examine in `orgs.txt`, one per line.

## Building the website

The data comes in two parts that are refreshed on different schedules:

1. **Closed-PR history (manual, occasional).** `first_response.py` fetches
   every closed PR (about 20 minutes) and writes the large
   `artifacts/first_response.json`, plus a small
   `artifacts/first_response_summary.json`. The summary holds everything the
   website needs from the closed PRs: the committer list and response-time
   statistics. It is tracked in git so regular rebuilds can use it without
   the large file.

   ```bash
   .venv/bin/python list_repos.py
   .venv/bin/python first_response.py
   ```

2. **Open PRs and the website (regular).** `build.sh` refreshes the repo list
   and open-PR data and rebuilds the site in `_site/` (a few minutes; creates
   the venv if needed). It uses the saved summary and never reads
   `first_response.json`.

   ```bash
   ./build.sh            # default waiting-PR threshold (see stale_prs.py --help)
   ./build.sh --days 30  # or choose a different threshold
   ```

## Scripts

Each step reads the output of the earlier ones. All output is written to
`artifacts/`.

| Script | Output | Purpose |
|---|---|---|
| `list_repos.py` | `repos.json` | Lists the non-archived repos in each organization. |
| `first_response.py` | `first_response.json`, `first_response_summary.json` | For every closed PR, finds the first response by someone other than the author, and computes median and average time to first response per repo and overall. A full run also writes the summary. |
| `summarize_first_response.py` | `first_response_summary.json` | Regenerates the summary from an existing `first_response.json` without fetching anything. |
| `committers.py` | (prints) | Reads `first_response.json`. Lists committers with merge counts, compares time to first response for committer and non-committer authors by year, and shows the distribution of response times in 100 equal-size buckets. |
| `stale_prs.py` | `stale_prs.json` | Lists open PRs where the author made the last comment and has been waiting more than a threshold number of days (`--days N`; see `--help` for the default). |
| `neglected_repos.py` | (prints) | Lists repos whose stale PRs have no comments from committers. |
| `build_site.py` | `_site/index.html` | Builds a static website from `repos.json`, the summary and `stale_prs.json`, with links to each waiting PR. |

Example:

```bash
.venv/bin/python list_repos.py
.venv/bin/python first_response.py     # slow: fetches every closed PR, writes summary
.venv/bin/python committers.py
.venv/bin/python stale_prs.py
.venv/bin/python neglected_repos.py
.venv/bin/python build_site.py
```

`first_response.py` and `stale_prs.py` accept `--limit N` to process only the
first N repos for testing. Limited runs write to `*_test.json` so the full
results are not overwritten, and do not update the summary.

## Definitions

- **Response**: a comment or submitted review by someone other than the PR
  author. Inline review comments count as part of their review. Closing or
  merging without a comment is not a response.
- **Bots**: comments by bot accounts are ignored, and PRs authored by bots
  (such as mergify backports and dependabot updates) are skipped.
- **Closed PRs**: both merged PRs and PRs closed without merging. Closed PRs
  that never received a response are counted but excluded from response-time
  statistics.
- **Committer**: anyone who merged at least one closed PR in the last
  `first_response.py` run, across all examined repos.
- **Stale PR**: an open, non-draft PR where the author (including by opening
  the PR) made the last comment more than a threshold number of days ago
  (set with `--days`; `DEFAULT_DAYS` in `stale_prs.py`).

## Caveats

- Response-time statistics cover only closed PRs that received a response, so
  they understate how long PRs actually wait. PRs still open or closed without
  any response are not included.
- Committer status is the current status applied to all years, and is not
  limited to the repo where the person merged PRs.
- Between `first_response.py` runs, the committer list and response-time
  statistics stay fixed. Repos added since then appear on the site without
  closed-PR statistics.
- `committers.py` identifies bot mergers by login name only.
