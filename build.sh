#!/usr/bin/env bash
# Collect all data from GitHub and build the website in _site/.
#
# Usage: ./build.sh [--days N]
#
#   --days N   list open PRs whose author has waited more than N days for a
#              response (default: see `stale_prs.py --help`)
#
# Requires a .env file containing GITHUB_TOKEN, and orgs.txt listing the
# organizations to examine. Collecting closed-PR data takes about 20 minutes.

set -euo pipefail

usage() {
  echo "Usage: $0 [--days N]"
}

# Check arguments before the long data-collection steps run.
STALE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --days=*)
      days="${1#--days=}"
      shift
      ;;
    --days)
      days="${2-}"
      shift $(( $# > 1 ? 2 : 1 ))
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
  if [[ ! "$days" =~ ^[0-9]+$ ]]; then
    echo "--days needs a whole number of days, got '$days'" >&2
    usage >&2
    exit 1
  fi
  STALE_ARGS+=(--days "$days")
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
$PYTHON stale_prs.py ${STALE_ARGS[@]+"${STALE_ARGS[@]}"}

echo "== Building website"
$PYTHON build_site.py
