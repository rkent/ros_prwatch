#!/usr/bin/env bash
# Collect all data from GitHub and build the website in _site/.
#
# Requires a .env file containing GITHUB_TOKEN, and orgs.txt listing the
# organizations to examine. Collecting closed-PR data takes about 20 minutes.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing .env (it must contain GITHUB_TOKEN=...)" >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating virtual environment"
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet -r requirements.txt

PYTHON=.venv/bin/python

echo "== Listing repos"
$PYTHON list_repos.py

echo "== Measuring time to first response for closed PRs"
$PYTHON first_response.py

echo "== Finding open PRs waiting on a response"
$PYTHON stale_prs.py

echo "== Building website"
$PYTHON build_site.py
