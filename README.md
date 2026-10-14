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

## Scripts

Run the scripts in this order; each step reads the output of the earlier ones.
All output is written to `artifacts/`.

| Script | Output | Purpose |
|---|---|---|
| `list_repos.py` | `repos.json` | Lists the non-archived repos in each organization. |
| `first_response.py` | `first_response.json` | For every closed PR, finds the first response by someone other than the author, and computes median and average time to first response per repo and overall. |
| `committers.py` | (prints) | Lists committers with merge counts, compares time to first response for committer and non-committer authors by year, and shows the distribution of response times in 100 equal-size buckets. |
| `stale_prs.py` | `stale_prs.json` | Lists open PRs where the author made the last comment and has been waiting more than 30 days (`--days N` to change). |
| `neglected_repos.py` | (prints) | Lists repos whose stale PRs have no comments from committers. |

Example:

```bash
.venv/bin/python list_repos.py
.venv/bin/python first_response.py     # slow: fetches every closed PR
.venv/bin/python committers.py
.venv/bin/python stale_prs.py
.venv/bin/python neglected_repos.py
```

`first_response.py` and `stale_prs.py` accept `--limit N` to process only the
first N repos for testing. Limited runs write to `*_test.json` so the full
results are not overwritten.

## Definitions

- **Response**: a comment or submitted review by someone other than the PR
  author. Inline review comments count as part of their review. Closing or
  merging without a comment is not a response.
- **Bots**: comments by bot accounts are ignored, and PRs authored by bots
  (such as mergify backports and dependabot updates) are skipped.
- **Closed PRs**: both merged PRs and PRs closed without merging. Closed PRs
  that never received a response are counted but excluded from response-time
  statistics.
- **Committer**: anyone who merged at least one PR in `first_response.json`,
  across all examined repos.
- **Stale PR**: an open, non-draft PR where the author (including by opening
  the PR) made the last comment more than 30 days ago.

## Caveats

- Response-time statistics cover only closed PRs that received a response, so
  they understate how long PRs actually wait. PRs still open or closed without
  any response are not included.
- Committer status is the current status applied to all years, and is not
  limited to the repo where the person merged PRs.
- `committers.py` identifies bot mergers by login name only.
