#!/usr/bin/env bash
# Refresh the open-PR data from GitHub and rebuild the website in docs/.
#
# Usage: ./build.sh [--days N]
#
#   --days N   list open PRs whose author has waited more than N days for a
#              response (default: see `stale_prs.py --help`)
#
# Requires GITHUB_TOKEN (in .env or the environment), orgs.txt listing the
# organizations to examine, and artifacts/first_response_summary.json. The
# summary comes from the slow closed-PR collection, which is run manually:
#   .venv/bin/python first_response.py

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

if [[ ! -f .env && -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Set GITHUB_TOKEN in the environment or in .env (GITHUB_TOKEN=...)" >&2
  exit 1
fi

if [[ ! -f artifacts/first_response_summary.json ]]; then
  echo "Missing artifacts/first_response_summary.json; run first_response.py first" >&2
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

echo "== Finding open PRs waiting on a response"
$PYTHON stale_prs.py ${STALE_ARGS[@]+"${STALE_ARGS[@]}"}

echo "== Building website"
$PYTHON build_site.py
