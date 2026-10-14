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


def group_stats(group):
    """PR count, author count, and median/average response seconds (None if empty)."""
    times = [pr["timeToFirstResponseSeconds"] for pr in group]
    return {
        "prs": len(times),
        "authors": len({pr["author"] for pr in group}),
        "median": statistics.median(times) if times else None,
        "average": statistics.mean(times) if times else None,
    }


def comparison_rows(prs, committers):
    """Committer vs. non-committer stats for each year opened, then for all years."""
    by_year = {}
    for pr in prs:
        by_year.setdefault(pr["createdAt"][:4], []).append(pr)
    periods = sorted(by_year.items()) + [("All", prs)]
    return [
        {
            "period": period,
            "committer": group_stats([pr for pr in period_prs if pr["author"] in committers]),
            "nonCommitter": group_stats(
                [pr for pr in period_prs if pr["author"] not in committers]),
        }
        for period, period_prs in periods
    ]


def bucket_rows(prs, num_buckets=100):
    """Sort PRs by time to first response and split into equal-size buckets."""
    times = sorted(pr["timeToFirstResponseSeconds"] for pr in prs)
    n = len(times)
    rows = []
    for i in range(num_buckets):
        bucket = times[i * n // num_buckets:(i + 1) * n // num_buckets]
        if bucket:
            rows.append({
                "bucket": i + 1,
                "prs": len(bucket),
                "shortest": bucket[0],
                "longest": bucket[-1],
                "median": statistics.median(bucket),
                "average": statistics.mean(bucket),
            })
    return rows


def fmt(seconds):
    return "-" if seconds is None else format_duration(seconds)


def print_group_row(period, label, stats):
    print(f"{period:<6} {label:<14} {stats['prs']:>7} {stats['authors']:>8} "
          f"{fmt(stats['median']):>12} {fmt(stats['average']):>12}")


def print_comparison(prs, committers):
    print("\nTime to first response by PR author, by year opened\n")
    print(f"{'Year':<6} {'Author':<14} {'PRs':>7} {'Authors':>8} {'Median':>12} {'Average':>12}")
    for row in comparison_rows(prs, committers):
        if row["period"] == "All":
            print()
        print_group_row(row["period"], "Committer", row["committer"])
        print_group_row("", "Non-committer", row["nonCommitter"])


def print_buckets(prs, num_buckets=100):
    print(f"\nTime to first response distribution ({len(prs)} PRs in {num_buckets} buckets)\n")
    print(f"{'Bucket':>6} {'PRs':>5} {'Shortest':>14} {'Longest':>14} "
          f"{'Median':>14} {'Average':>14}")
    for row in bucket_rows(prs, num_buckets):
        print(f"{row['bucket']:>6} {row['prs']:>5} {fmt(row['shortest']):>14} "
              f"{fmt(row['longest']):>14} {fmt(row['median']):>14} {fmt(row['average']):>14}")


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
