#!/usr/bin/env python3
"""Report committers, compare first-response times for committer vs.
non-committer PR authors, and show the distribution of first-response times.

Reads artifacts/first_response.json (produced by first_response.py). A
committer is anyone who merged at least one PR in that file, excluding bots.
"""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from first_response import OUTPUT_FILE, ROOT, format_duration

# Bot accounts that can appear as mergers. The JSON stores only logins, not
# account types, so these are listed explicitly.
BOT_LOGINS = {"mergify", "dependabot", "github-actions"}


def is_bot_login(login):
    return login in BOT_LOGINS or login.endswith("[bot]")


def load_prs(path):
    data = json.loads(path.read_text())
    return [pr for repo in data["repos"].values() for pr in repo["pullRequests"]]


def merge_counts(prs):
    return Counter(
        pr["mergedBy"] for pr in prs
        if pr.get("mergedBy") and not is_bot_login(pr["mergedBy"])
    )


def print_committers(counts):
    print(f"Committers ({len(counts)}, {sum(counts.values())} merged PRs)\n")
    print(f"{'Merged':>7}  Handle")
    for login, count in counts.most_common():
        print(f"{count:>7}  {login}")


def print_group_row(period, label, group):
    times = [pr["timeToFirstResponseSeconds"] for pr in group]
    if not times:
        print(f"{period:<6} {label:<14} {0:>7} {0:>8} {'-':>12} {'-':>12}")
        return
    authors = len({pr["author"] for pr in group})
    median = format_duration(statistics.median(times))
    average = format_duration(statistics.mean(times))
    print(f"{period:<6} {label:<14} {len(times):>7} {authors:>8} {median:>12} {average:>12}")


def print_comparison(prs, committers):
    by_year = {}
    for pr in prs:
        by_year.setdefault(pr["createdAt"][:4], []).append(pr)
    periods = sorted(by_year.items()) + [("All", prs)]

    print("\nTime to first response by PR author, by year opened\n")
    print(f"{'Year':<6} {'Author':<14} {'PRs':>7} {'Authors':>8} {'Median':>12} {'Average':>12}")
    for period, period_prs in periods:
        if period == "All":
            print()
        print_group_row(period, "Committer",
                        [pr for pr in period_prs if pr["author"] in committers])
        print_group_row("", "Non-committer",
                        [pr for pr in period_prs if pr["author"] not in committers])


def print_buckets(prs, num_buckets=100):
    """Sort PRs by time to first response and split into equal-size buckets."""
    times = sorted(pr["timeToFirstResponseSeconds"] for pr in prs)
    n = len(times)
    print(f"\nTime to first response distribution ({n} PRs in {num_buckets} buckets)\n")
    print(f"{'Bucket':>6} {'PRs':>5} {'Shortest':>14} {'Longest':>14} "
          f"{'Median':>14} {'Average':>14}")
    for i in range(num_buckets):
        bucket = times[i * n // num_buckets:(i + 1) * n // num_buckets]
        if not bucket:
            continue
        print(f"{i + 1:>6} {len(bucket):>5} {format_duration(bucket[0]):>14} "
              f"{format_duration(bucket[-1]):>14} "
              f"{format_duration(statistics.median(bucket)):>14} "
              f"{format_duration(statistics.mean(bucket)):>14}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", nargs="?", type=Path, default=OUTPUT_FILE,
                        help=f"first-response JSON (default: {OUTPUT_FILE.relative_to(ROOT)})")
    args = parser.parse_args()

    prs = load_prs(args.file)
    counts = merge_counts(prs)
    print_committers(counts)
    print_comparison(prs, set(counts))
    print_buckets(prs)


if __name__ == "__main__":
    main()
