#!/usr/bin/env python3
"""List open PRs where the author spoke last and has waited over N days.

Reads the repo list from artifacts/repos.json and the committer list from
artifacts/first_response.json (anyone who merged a PR there). Writes
artifacts/stale_prs.json.

"Comments" are issue comments and submitted reviews (a review with inline
comments counts once). Opening the PR counts as the author's first comment,
so PRs with no comments at all are included. Bot comments are ignored, and
PRs authored by bots or in draft status are skipped.

Use --limit N to process only the first N repos (for testing); output then
goes to artifacts/stale_prs_test.json instead.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from committers import load_prs, merge_counts
from first_response import (
    OUTPUT_FILE as FIRST_RESPONSE_FILE,
    REPOS_FILE,
    ROOT,
    GitHub,
    format_duration,
    is_bot,
    login_of,
    parse_time,
)

OUTPUT_FILE = ROOT / "artifacts" / "stale_prs.json"
TEST_OUTPUT_FILE = ROOT / "artifacts" / "stale_prs_test.json"
DEFAULT_DAYS = 10

ACTIVITY_FIELDS = """
  pageInfo { hasNextPage endCursor }
  nodes {
    __typename
    ... on IssueComment { createdAt author { login __typename } }
    ... on PullRequestReview { submittedAt author { login __typename } }
  }
"""

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $cursor, states: [OPEN],
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        isDraft
        createdAt
        author { login __typename }
        timelineItems(first: 100, itemTypes: [ISSUE_COMMENT, PULL_REQUEST_REVIEW]) {
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


def all_activity(gh, owner, name, pr):
    """Return every comment/review on the PR, paging past the first 100."""
    timeline = pr["timelineItems"]
    items = list(timeline["nodes"])
    while timeline["pageInfo"]["hasNextPage"]:
        data = gh.query(
            MORE_ACTIVITY_QUERY,
            {"owner": owner, "name": name, "number": pr["number"],
             "cursor": timeline["pageInfo"]["endCursor"]},
        )
        timeline = data["repository"]["pullRequest"]["timelineItems"]
        items.extend(timeline["nodes"])
    return items


def check_pr(gh, owner, name, pr, committers, cutoff, now):
    """Return a stale-PR record, or None if the PR doesn't qualify."""
    if pr["isDraft"] or is_bot(pr["author"]):
        return None
    author = login_of(pr["author"])

    # Opening the PR counts as the author's first comment.
    comments = [(pr["createdAt"], author)]
    for item in all_activity(gh, owner, name, pr):
        ts = item.get("createdAt") or item.get("submittedAt")
        if ts is None or is_bot(item["author"]):  # pending review, or bot
            continue
        comments.append((ts, login_of(item["author"])))
    comments.sort()

    last_at, last_by = comments[-1]
    if last_by != author or parse_time(last_at) > cutoff:
        return None

    others = [login for _, login in comments if login != author]
    waiting = (now - parse_time(last_at)).total_seconds()
    return {
        "repo": f"{owner}/{name}",
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "author": author,
        "authorIsCommitter": author in committers,
        "createdAt": pr["createdAt"],
        "lastAuthorCommentAt": last_at,
        "waitingSeconds": round(waiting),
        "waiting": format_duration(waiting),
        "authorComments": len(comments) - len(others) - 1,  # excludes opening
        "committerComments": sum(login in committers for login in others),
        "nonCommitterComments": sum(login not in committers for login in others),
    }


def process_repo(gh, owner, name, committers, cutoff, now):
    stale = []
    cursor = None
    while True:
        data = gh.query(OPEN_PRS_QUERY, {"owner": owner, "name": name, "cursor": cursor})
        conn = data["repository"]["pullRequests"]
        for pr in conn["nodes"]:
            record = check_pr(gh, owner, name, pr, committers, cutoff, now)
            if record:
                stale.append(record)
        if not conn["pageInfo"]["hasNextPage"]:
            return stale
        cursor = conn["pageInfo"]["endCursor"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="minimum days since the author's last comment "
                             f"(default: {DEFAULT_DAYS})")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="process only the first N repos (writes to "
                             f"{TEST_OUTPUT_FILE.relative_to(ROOT)})")
    args = parser.parse_args()
    output_file = TEST_OUTPUT_FILE if args.limit is not None else OUTPUT_FILE

    load_dotenv(ROOT / ".env")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN not set (expected in .env)")
    for path in (REPOS_FILE, FIRST_RESPONSE_FILE):
        if not path.exists():
            sys.exit(f"{path.relative_to(ROOT)} not found")

    gh = GitHub(token)
    committers = set(merge_counts(load_prs(FIRST_RESPONSE_FILE)))
    repo_list = json.loads(REPOS_FILE.read_text())
    all_repos = [r["nameWithOwner"] for org_repos in repo_list["orgs"].values()
                 for r in org_repos]
    if args.limit is not None:
        all_repos = all_repos[:args.limit]

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    stale = []
    for full_name in all_repos:
        owner, name = full_name.split("/")
        repo_stale = process_repo(gh, owner, name, committers, cutoff, now)
        stale.extend(repo_stale)
        if repo_stale:
            print(f"{full_name}: {len(repo_stale)} stale PRs")

    stale.sort(key=lambda r: r["waitingSeconds"], reverse=True)
    output = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "minDaysWaiting": args.days,
        "stalePrCount": len(stale),
        "pullRequests": stale,
    }
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"\n{len(stale)} open PRs waiting over {args.days} days on a response")
    print(f"Wrote {output_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
