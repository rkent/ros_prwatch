#!/usr/bin/env python3
"""List non-archived repositories for each GitHub organization in orgs.txt.

Results are written to artifacts/repos.json.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ORGS_FILE = ROOT / "orgs.txt"
OUTPUT_FILE = ROOT / "artifacts" / "repos.json"
GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(first: 100, after: $cursor, orderBy: {field: NAME, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        nameWithOwner
        url
        isArchived
        isFork
        isPrivate
        defaultBranchRef { name }
        pushedAt
      }
    }
  }
}
"""


def read_orgs(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def graphql(session, query, variables):
    resp = session.post(GRAPHQL_URL, json={"query": query, "variables": variables})
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def list_org_repos(session, org):
    repos = []
    cursor = None
    while True:
        data = graphql(session, QUERY, {"org": org, "cursor": cursor})
        if data["organization"] is None:
            raise RuntimeError(f"Organization not found: {org}")
        conn = data["organization"]["repositories"]
        for node in conn["nodes"]:
            if node["isArchived"]:
                continue
            repos.append(
                {
                    "name": node["name"],
                    "nameWithOwner": node["nameWithOwner"],
                    "url": node["url"],
                    "isFork": node["isFork"],
                    "isPrivate": node["isPrivate"],
                    "defaultBranch": (node["defaultBranchRef"] or {}).get("name"),
                    "pushedAt": node["pushedAt"],
                }
            )
        if not conn["pageInfo"]["hasNextPage"]:
            return repos
        cursor = conn["pageInfo"]["endCursor"]


def main():
    load_dotenv(ROOT / ".env")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN not set (expected in .env)")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "orgs": {},
    }
    for org in read_orgs(ORGS_FILE):
        repos = list_org_repos(session, org)
        result["orgs"][org] = repos
        print(f"{org}: {len(repos)} non-archived repos")

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(f"Wrote {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
