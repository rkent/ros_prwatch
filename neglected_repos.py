#!/usr/bin/env python3
"""List repos with stale PRs that have no comments from committers.

Reads artifacts/stale_prs.json (produced by stale_prs.py).
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from stale_prs import OUTPUT_FILE, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", nargs="?", type=Path, default=OUTPUT_FILE,
                        help=f"stale PR JSON (default: {OUTPUT_FILE.relative_to(ROOT)})")
    args = parser.parse_args()

    prs = json.loads(args.file.read_text())["pullRequests"]
    all_stale = defaultdict(int)
    neglected = defaultdict(list)
    for pr in prs:
        all_stale[pr["repo"]] += 1
        if pr["committerComments"] == 0:
            neglected[pr["repo"]].append(pr)

    total = sum(len(v) for v in neglected.values())
    print(f"{len(neglected)} repos with {total} stale PRs that have no committer "
          f"comments (of {len(prs)} stale PRs)\n")
    print(f"{'No committer':>12} {'All stale':>10} {'Oldest':>8}  Repo")
    print(f"{'comments':>12} {'PRs':>10} {'(days)':>8}")
    for repo, repo_prs in sorted(neglected.items(), key=lambda x: (-len(x[1]), x[0])):
        oldest_days = max(pr["waitingSeconds"] for pr in repo_prs) // 86400
        print(f"{len(repo_prs):>12} {all_stale[repo]:>10} {oldest_days:>8}  {repo}")


if __name__ == "__main__":
    main()
