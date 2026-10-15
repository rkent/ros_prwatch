#!/usr/bin/env bash
# Collect all data from GitHub and build the website in _site/.
#
# Usage: ./build.sh [--days N]
#
#   --days N   list open PRs whose author has waited more than N days for a
#              response (default: stale_prs.py's default, 10)
#
# Requires a .env file containing GITHUB_TOKEN, and orgs.txt listing the
# organizations to examine. Collecting closed-PR data takes about 20 minutes.

set -euo pipefail

STALE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --days)
      STALE_ARGS+=(--days "$2")
      shift 2
      ;;
    *)
      echo "Usage: $0 [--days N]" >&2
      exit 1
      ;;
  esac
done

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
$PYTHON stale_prs.py "${STALE_ARGS[@]}"

echo "== Building website"
$PYTHON build_site.py
