#!/usr/bin/env python3
"""Condense artifacts/first_response.json into a small summary file.

The summary holds everything later steps need from the closed-PR data
(the committer list, response-time statistics per repo and overall, the
committer vs. non-committer comparison by year, and percentiles), so that
stale_prs.py and build_site.py can run regularly without the large
first_response.json. first_response.py writes the summary automatically
after a full run; run this script to regenerate it from an existing file.

Writes artifacts/first_response_summary.json.
"""

import json
import statistics
import sys
from datetime import datetime, timezone

from committers import comparison_rows, merge_counts
from first_response import OUTPUT_FILE as FIRST_RESPONSE_FILE, ROOT

SUMMARY_FILE = ROOT / "artifacts" / "first_response_summary.json"
PERCENTILES = [25, 50, 75, 90, 95, 99]

REPO_FIELDS = ["closedPrCount", "respondedPrCount", "noResponsePrCount", "botPrCount",
               "medianSeconds", "averageSeconds"]


def percentile(sorted_values, pct):
    index = min(len(sorted_values) - 1, round(pct / 100 * (len(sorted_values) - 1)))
    return sorted_values[index]


def summarize(first_response):
    prs = [pr for repo in first_response["repos"].values() for pr in repo["pullRequests"]]
    counts = merge_counts(prs)
    times = sorted(pr["timeToFirstResponseSeconds"] for pr in prs)
    overall = first_response["overall"]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceGeneratedAt": first_response["generatedAt"],
        "overall": {
            "closedPrCount": overall["closedPrCount"],
            "respondedPrCount": overall["respondedPrCount"],
            "botPrCount": overall["botPrCount"],
            "medianSeconds": overall["medianSeconds"],
            "averageSeconds": overall["averageSeconds"],
        },
        "repos": {
            name: {field: repo[field] for field in REPO_FIELDS}
            for name, repo in first_response["repos"].items()
        },
        # Login -> number of PRs merged, most first.
        "committers": dict(counts.most_common()),
        "committerComparison": comparison_rows(prs, set(counts)),
        "percentiles": {
            str(p): round(percentile(times, p)) for p in PERCENTILES
        } if times else {},
    }


def write_summary(first_response):
    summary = summarize(first_response)
    SUMMARY_FILE.parent.mkdir(exist_ok=True)
    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(f"Wrote {SUMMARY_FILE.relative_to(ROOT)}")


def load_summary():
    """Load the summary, exiting with instructions if it doesn't exist."""
    if not SUMMARY_FILE.exists():
        sys.exit(f"{SUMMARY_FILE.relative_to(ROOT)} not found; run first_response.py "
                 "(or summarize_first_response.py if first_response.json exists)")
    return json.loads(SUMMARY_FILE.read_text())


def main():
    if not FIRST_RESPONSE_FILE.exists():
        sys.exit(f"{FIRST_RESPONSE_FILE.relative_to(ROOT)} not found; run first_response.py")
    write_summary(json.loads(FIRST_RESPONSE_FILE.read_text()))


if __name__ == "__main__":
    main()
