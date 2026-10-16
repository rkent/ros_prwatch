#!/usr/bin/env python3
"""List open PRs where the author spoke last and has waited over N days, and
PRs whose long wait ended with a response in the last M days.

Reads the repo list from artifacts/repos.json and the committer list from
artifacts/first_response_summary.json (anyone who merged a closed PR), adding
anyone who merged a PR in the last M days. Writes artifacts/stale_prs.json.

"Comments" are issue comments and submitted reviews (a review with inline
comments counts once). Opening the PR counts as the author's first comment,
so PRs with no comments at all are included. Bot comments are ignored, and
PRs authored by bots or in draft status are skipped.

A "recently answered" PR is open, merged or closed, and had a response in
the last M days after the author waited over N days. A merge or close by
someone other than the author counts as a response. Each such PR records the
latest qualifying response from anyone, and from a committer. A committer's
response ends a wait only if no committer has responded since the author's
last comment, so comments from non-committers don't end it.

Use --limit N to process only the first N repos (for testing); output then
goes to artifacts/stale_prs_test.json instead.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from first_response import (
    REPOS_FILE,
    ROOT,
    GitHub,
    PermanentQueryError,
    format_duration,
    is_bot,
    login_of,
    parse_time,
)
from summarize_first_response import load_summary

OUTPUT_FILE = ROOT / "artifacts" / "stale_prs.json"
TEST_OUTPUT_FILE = ROOT / "artifacts" / "stale_prs_test.json"
DEFAULT_DAYS = 10
DEFAULT_WINDOW = 30

# login_of() maps every deleted account to this login, so it is not one person.
DELETED_LOGIN = "ghost"

ACTIVITY_FIELDS = """
  pageInfo { hasNextPage endCursor }
  nodes {
    __typename
    ... on IssueComment { createdAt author { login __typename } }
    ... on PullRequestReview { submittedAt author { login __typename } }
  }
"""

PR_FIELDS = """
  number
  title
  url
  isDraft
  state
  createdAt
  author { login __typename }
  timelineItems(first: 100, itemTypes: [ISSUE_COMMENT, PULL_REQUEST_REVIEW]) {
    %s
  }
""" % ACTIVITY_FIELDS

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $cursor, states: [OPEN],
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        %s
      }
    }
  }
}
""" % PR_FIELDS

RECENTLY_CLOSED_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $cursor, states: [CLOSED, MERGED],
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        %s
        updatedAt
        mergedAt
        mergedBy { login __typename }
        closedAt
        closedEvents: timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
          nodes { ... on ClosedEvent { actor { login __typename } } }
        }
      }
    }
  }
}
""" % PR_FIELDS

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


def pr_events(gh, owner, name, pr):
    """Return the PR's (time, login, kind) events in time order.

    Kinds are "open", "comment", "merge" and "close". Bots are ignored.
    """
    events = [(pr["createdAt"], login_of(pr["author"]), "open")]
    for item in all_activity(gh, owner, name, pr):
        ts = item.get("createdAt") or item.get("submittedAt")
        if ts is None or is_bot(item["author"]):  # pending review, or bot
            continue
        events.append((ts, login_of(item["author"]), "comment"))
    if pr["state"] == "MERGED":
        if pr["mergedBy"] and not is_bot(pr["mergedBy"]):
            events.append((pr["mergedAt"], login_of(pr["mergedBy"]), "merge"))
    elif pr["state"] == "CLOSED":
        closed_events = pr["closedEvents"]["nodes"]
        closer = closed_events[0]["actor"] if closed_events else None
        if closer and not is_bot(closer):
            events.append((pr["closedAt"], login_of(closer), "close"))
    events.sort()
    return events


def recent_merger(pr, window_start):
    """Return the login of whoever merged the PR within the window, or None."""
    if pr["state"] != "MERGED" or parse_time(pr["mergedAt"]) < window_start:
        return None
    merger = pr["mergedBy"]
    if merger is None or is_bot(merger) or merger["login"] == DELETED_LOGIN:
        return None
    return merger["login"]


def pr_record(owner, name, pr, committers):
    """Fields shared by the stale and recently answered records."""
    author = login_of(pr["author"])
    return {
        "repo": f"{owner}/{name}",
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "author": author,
        "authorIsCommitter": author in committers,
        "state": pr["state"],
        "createdAt": pr["createdAt"],
    }


def stale_record(owner, name, pr, events, committers, cutoff, now):
    """Return a stale-PR record, or None if the PR doesn't qualify."""
    author = login_of(pr["author"])
    last_at, last_by, _ = events[-1]
    if last_by != author or parse_time(last_at) > cutoff:
        return None

    others = [login for _, login, _ in events if login != author]
    waiting = (now - parse_time(last_at)).total_seconds()
    return {
        **pr_record(owner, name, pr, committers),
        "lastAuthorCommentAt": last_at,
        "waitingSeconds": round(waiting),
        "waiting": format_duration(waiting),
        "authorComments": len(events) - len(others) - 1,  # excludes opening
        "committerComments": sum(login in committers for login in others),
        "nonCommitterComments": sum(login not in committers for login in others),
    }


def response(at, login, kind, waited_from, committers):
    waited = (parse_time(at) - waited_from).total_seconds()
    return {
        "at": at,
        "by": login,
        "byCommitter": login in committers,
        "kind": kind,
        "waitedSeconds": round(waited),
        "waited": format_duration(waited),
    }


def responded_record(owner, name, pr, events, committers, window_start, min_wait):
    """Return a recently-answered record, or None if the PR doesn't qualify."""
    author = login_of(pr["author"])
    any_response = committer_response = None
    prev_at, prev_by = None, None
    author_at = None
    committer_since_author = False
    for at, login, kind in events:
        t = parse_time(at)
        if login == author:
            author_at = t
            committer_since_author = False
        else:
            in_window = t >= window_start
            if prev_by == author and in_window and t - prev_at >= min_wait:
                any_response = response(at, login, kind, prev_at, committers)
            if login in committers and not committer_since_author:
                if author_at and in_window and t - author_at >= min_wait:
                    committer_response = response(at, login, kind, author_at, committers)
                committer_since_author = True
        prev_at, prev_by = t, login

    if any_response is None and committer_response is None:
        return None
    return {
        **pr_record(owner, name, pr, committers),
        "anyResponse": any_response,
        "committerResponse": committer_response,
    }


def fetch_repo(gh, owner, name, cutoff, window_start):
    """Fetch the open PRs and the PRs closed or updated within the window.

    Returns (open, closed, mergers): lists of (pr, events) that could qualify,
    and the logins of anyone who merged a PR within the window.
    """
    # The last comment can't predate the opening, so PRs opened after the
    # cutoff can't have waited long enough for either list.
    def could_qualify(pr):
        return (not pr["isDraft"] and not is_bot(pr["author"])
                and parse_time(pr["createdAt"]) <= cutoff)

    def pages(query):
        cursor = None
        while True:
            data = gh.query(query, {"owner": owner, "name": name, "cursor": cursor})
            if data["repository"] is None:
                raise PermanentQueryError(f"Repository not found: {owner}/{name}")
            conn = data["repository"]["pullRequests"]
            yield from conn["nodes"]
            if not conn["pageInfo"]["hasNextPage"]:
                return
            cursor = conn["pageInfo"]["endCursor"]

    open_prs = [(pr, pr_events(gh, owner, name, pr))
                for pr in pages(OPEN_PRS_QUERY) if could_qualify(pr)]

    closed_prs = []
    mergers = set()
    for pr in pages(RECENTLY_CLOSED_QUERY):
        if parse_time(pr["updatedAt"]) < window_start:
            break  # sorted by most recently updated
        merger = recent_merger(pr, window_start)
        if merger:
            mergers.add(merger)
        if could_qualify(pr):
            closed_prs.append((pr, pr_events(gh, owner, name, pr)))
    return open_prs, closed_prs, mergers


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="minimum days since the author's last comment "
                             f"(default: {DEFAULT_DAYS})")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help="list waits that ended in a response within this "
                             f"many days (default: {DEFAULT_WINDOW})")
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
    saved_committers = set(load_summary()["committers"])
    repo_list = json.loads(REPOS_FILE.read_text())
    all_repos = [r["nameWithOwner"] for org_repos in repo_list["orgs"].values()
                 for r in org_repos]
    if args.limit is not None:
        all_repos = all_repos[:args.limit]

    now = datetime.now(timezone.utc)
    min_wait = timedelta(days=args.days)
    cutoff = now - min_wait
    window_start = now - timedelta(days=args.window)

    # Fetch everything first: the committer list isn't complete until every
    # repo's recent mergers are known.
    fetched = []
    mergers = set()
    for full_name in all_repos:
        owner, name = full_name.split("/")
        try:
            open_prs, closed_prs, repo_mergers = fetch_repo(
                gh, owner, name, cutoff, window_start)
        except PermanentQueryError as e:
            print(f"{full_name}: skipped ({e})", file=sys.stderr)
            continue
        fetched.append((owner, name, open_prs, closed_prs))
        mergers |= repo_mergers
    new_committers = sorted(mergers - saved_committers)
    committers = saved_committers | mergers
    if new_committers:
        print(f"Recent mergers added as committers: {', '.join(new_committers)}")

    stale = []
    responded = []
    for owner, name, open_prs, closed_prs in fetched:
        repo_stale = []
        for pr, events in open_prs:
            record = stale_record(owner, name, pr, events, committers, cutoff, now)
            if record:
                repo_stale.append(record)
        for pr, events in open_prs + closed_prs:
            record = responded_record(owner, name, pr, events, committers,
                                      window_start, min_wait)
            if record:
                responded.append(record)
        stale.extend(repo_stale)
        if repo_stale:
            print(f"{owner}/{name}: {len(repo_stale)} stale PRs")

    stale.sort(key=lambda r: r["waitingSeconds"], reverse=True)
    responded.sort(key=lambda r: max(resp["at"] for resp in
                                     (r["anyResponse"], r["committerResponse"]) if resp),
                   reverse=True)
    output = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "minDaysWaiting": args.days,
        "responseWindowDays": args.window,
        "recentMergerCommitters": new_committers,
        "stalePrCount": len(stale),
        "pullRequests": stale,
        "respondedPrCount": len(responded),
        "respondedPullRequests": responded,
    }
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    print(f"\n{len(stale)} open PRs waiting over {args.days} days on a response")
    print(f"{len(responded)} PRs answered in the last {args.window} days "
          f"after waiting over {args.days} days")
    print(f"Wrote {output_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
