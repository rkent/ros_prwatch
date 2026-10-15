#!/usr/bin/env python3
"""Compute time to first non-author response for closed PRs.

Reads the repo list from artifacts/repos.json (produced by list_repos.py) and
writes artifacts/first_response.json.

A "response" is a comment or review by someone other than the PR author.
Bot accounts are not counted as responders. "Closed" PRs include both merged
and closed-without-merge PRs. PRs authored by bots (e.g. mergify backports,
dependabot) are skipped entirely (but counted). PRs closed with no non-author response are
excluded from the listing and statistics (but counted).

Use --limit N to process only the first N repos (for testing); output then
goes to artifacts/first_response_test.json instead.
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
REPOS_FILE = ROOT / "artifacts" / "repos.json"
OUTPUT_FILE = ROOT / "artifacts" / "first_response.json"
TEST_OUTPUT_FILE = ROOT / "artifacts" / "first_response_test.json"
GRAPHQL_URL = "https://api.github.com/graphql"

ACTIVITY_FIELDS = """
  pageInfo { hasNextPage endCursor }
  nodes {
    __typename
    ... on IssueComment { createdAt author { login __typename } }
    ... on PullRequestReview { createdAt submittedAt author { login __typename } }
  }
"""

PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $cursor, states: [CLOSED, MERGED],
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        url
        state
        createdAt
        closedAt
        author { login __typename }
        mergedBy { login }
        closedEvents: timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
          nodes { ... on ClosedEvent { actor { login } } }
        }
        timelineItems(first: 25, itemTypes: [ISSUE_COMMENT, PULL_REQUEST_REVIEW]) {
          %s
        }
      }
    }
  }
}
""" % ACTIVITY_FIELDS

MORE_ACTIVITY_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(first: 100, after: $cursor, itemTypes: [ISSUE_COMMENT, PULL_REQUEST_REVIEW]) {
        %s
      }
    }
  }
}
""" % ACTIVITY_FIELDS


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def format_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s"


def login_of(actor):
    return actor["login"] if actor else "ghost"  # deleted accounts


def is_bot(actor):
    return actor is not None and (
        actor["__typename"] == "Bot" or actor["login"].endswith("[bot]")
    )


# GraphQL error types that retrying cannot fix.
PERMANENT_ERROR_TYPES = {"NOT_FOUND", "FORBIDDEN", "INSUFFICIENT_SCOPES"}


class PermanentQueryError(RuntimeError):
    """A query failed in a way that retrying will not fix (e.g. repo not found)."""


class GitHub:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def query(self, query, variables, retries=5):
        err = None
        for attempt in range(retries):
            try:
                resp = self.session.post(
                    GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=60
                )
            except requests.RequestException as e:
                err = str(e)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" not in data:
                        self._throttle(data["data"]["rateLimit"])
                        return data["data"]
                    err = f"GraphQL errors: {data['errors']}"
                    if all(e.get("type") in PERMANENT_ERROR_TYPES for e in data["errors"]):
                        raise PermanentQueryError(err)
                elif resp.status_code in (403, 429) and "retry-after" in resp.headers:
                    wait = int(resp.headers["retry-after"])
                    err = f"secondary rate limit (HTTP {resp.status_code})"
                    print(f"  secondary rate limit, sleeping {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                else:
                    err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            wait = 2 ** attempt * 5
            print(f"  request failed ({err}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
        raise RuntimeError(f"Query failed after {retries} attempts: {err}")

    def _throttle(self, rate_limit):
        if rate_limit["remaining"] < 50:
            reset = parse_time(rate_limit["resetAt"])
            wait = max(0, (reset - datetime.now(timezone.utc)).total_seconds()) + 5
            print(f"  rate limit low, sleeping {int(wait)}s", file=sys.stderr)
            time.sleep(wait)


def first_response_in(items, author_login):
    """Return (time, login) of the earliest non-author, non-bot activity, or None."""
    best = None
    for item in items:
        actor = item["author"]
        login = login_of(actor)
        if login == author_login or is_bot(actor):
            continue
        if item["__typename"] == "PullRequestReview":
            ts = item["submittedAt"]
            if ts is None:  # pending review, not visible to the author
                continue
        else:
            ts = item["createdAt"]
        if best is None or ts < best[0]:
            best = (ts, login)
    return best


def find_first_response(gh, owner, name, pr):
    author_login = pr["author"]["login"] if pr["author"] else None
    timeline = pr["timelineItems"]
    found = first_response_in(timeline["nodes"], author_login)
    # Timeline is chronological, so only page further if nothing found yet.
    while found is None and timeline["pageInfo"]["hasNextPage"]:
        data = gh.query(
            MORE_ACTIVITY_QUERY,
            {"owner": owner, "name": name, "number": pr["number"],
             "cursor": timeline["pageInfo"]["endCursor"]},
        )
        timeline = data["repository"]["pullRequest"]["timelineItems"]
        found = first_response_in(timeline["nodes"], author_login)
    return found


def summarize(seconds_list):
    if not seconds_list:
        return {"medianSeconds": None, "median": None,
                "averageSeconds": None, "average": None}
    median = statistics.median(seconds_list)
    mean = statistics.mean(seconds_list)
    return {
        "medianSeconds": round(median),
        "median": format_duration(median),
        "averageSeconds": round(mean),
        "average": format_duration(mean),
    }


def process_repo(gh, owner, name):
    prs = []
    closed_count = 0
    bot_count = 0
    cursor = None
    while True:
        data = gh.query(PRS_QUERY, {"owner": owner, "name": name, "cursor": cursor})
        if data["repository"] is None:
            raise PermanentQueryError(f"Repository not found: {owner}/{name}")
        conn = data["repository"]["pullRequests"]
        for pr in conn["nodes"]:
            if is_bot(pr["author"]):
                bot_count += 1
                continue
            closed_count += 1
            found = find_first_response(gh, owner, name, pr)
            if found is None:
                continue
            responded_at, responder = found
            delta = (parse_time(responded_at) - parse_time(pr["createdAt"])).total_seconds()
            closed_events = pr["closedEvents"]["nodes"]
            closer = closed_events[0]["actor"] if closed_events else None
            prs.append({
                "number": pr["number"],
                "title": pr["title"],
                "description": pr["body"],
                "url": pr["url"],
                "state": pr["state"],
                "author": login_of(pr["author"]),
                "createdAt": pr["createdAt"],
                "closedAt": pr["closedAt"],
                "mergedBy": login_of(pr["mergedBy"]) if pr["state"] == "MERGED" else None,
                "closedBy": login_of(closer) if closed_events else None,
                "firstResponder": responder,
                "firstResponseAt": responded_at,
                "timeToFirstResponseSeconds": round(delta),
                "timeToFirstResponse": format_duration(delta),
            })
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    times = [p["timeToFirstResponseSeconds"] for p in prs]
    return {
        "closedPrCount": closed_count,
        "respondedPrCount": len(prs),
        "noResponsePrCount": closed_count - len(prs),
        "botPrCount": bot_count,
        **summarize(times),
        "pullRequests": prs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, metavar="N",
                        help="process only the first N repos (writes to "
                             f"{TEST_OUTPUT_FILE.relative_to(ROOT)})")
    args = parser.parse_args()
    output_file = TEST_OUTPUT_FILE if args.limit is not None else OUTPUT_FILE

    load_dotenv(ROOT / ".env")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN not set (expected in .env)")
    if not REPOS_FILE.exists():
        sys.exit(f"{REPOS_FILE.relative_to(ROOT)} not found; run list_repos.py first")

    gh = GitHub(token)
    repo_list = json.loads(REPOS_FILE.read_text())

    all_repos = [r["nameWithOwner"] for org_repos in repo_list["orgs"].values()
                 for r in org_repos]
    if args.limit is not None:
        all_repos = all_repos[:args.limit]

    repos = {}
    for full_name in all_repos:
        owner, name = full_name.split("/")
        try:
            result = process_repo(gh, owner, name)
        except PermanentQueryError as e:
            print(f"{full_name}: skipped ({e})", file=sys.stderr)
            continue
        repos[full_name] = result
        print(f"{full_name}: {result['respondedPrCount']}/{result['closedPrCount']} "
              f"responded, median {result['median']}")

    all_times = [p["timeToFirstResponseSeconds"]
                 for r in repos.values() for p in r["pullRequests"]]
    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": {
            "closedPrCount": sum(r["closedPrCount"] for r in repos.values()),
            "respondedPrCount": len(all_times),
            "botPrCount": sum(r["botPrCount"] for r in repos.values()),
            **summarize(all_times),
        },
        "repos": repos,
    }

    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"\nOverall: {output['overall']['respondedPrCount']} PRs, "
          f"average {output['overall']['average']}, median {output['overall']['median']}")
    print(f"Wrote {output_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
