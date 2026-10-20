#!/usr/bin/env python3
"""Build a static website of the results in docs/.

Reads artifacts/repos.json, artifacts/first_response_summary.json and
artifacts/stale_prs.json (waiting and recently answered PRs), and writes a single self-contained page,
docs/index.html. It does not need the full first_response.json.
"""

import json
import shutil
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path

from first_response import REPOS_FILE, ROOT, format_duration
from stale_prs import OUTPUT_FILE as STALE_FILE
from summarize_first_response import load_summary

SITE_DIR = ROOT / "docs"
FAVICON = ROOT / "favicon.ico"


def fmt(seconds):
    return "–" if seconds is None else format_duration(seconds)


def fmt_date(iso):
    return iso[:10]


def gh_link(path, text=None):
    return (f'<a href="https://github.com/{escape(path)}">'
            f'{escape(text if text is not None else path)}</a>')


def stat_card(value, label):
    return (f'<div class="card"><div class="value">{escape(str(value))}</div>'
            f'<div class="label">{escape(label)}</div></div>')


def stale_table(stale_prs):
    rows = []
    for pr in sorted(stale_prs, key=lambda pr: pr["createdAt"], reverse=True):
        neglected = pr["committerComments"] == 0
        days = pr["waitingSeconds"] // 86400
        role = "committer" if pr["authorIsCommitter"] else "contributor"
        rows.append(f"""<tr data-repo="{escape(pr['repo'])}" data-neglected="{int(neglected)}"
    data-role="{role}" data-search="{escape((pr['repo'] + ' ' + pr['title'] + ' ' + pr['author']).lower())}">
  <td>{gh_link(pr['repo'])}</td>
  <td><a href="{escape(pr['url'])}">#{pr['number']}</a> {escape(pr['title'])}</td>
  <td>{gh_link(pr['author'])}{' <span class="tag">committer</span>' if pr['authorIsCommitter'] else ''}</td>
  <td class="nowrap" data-sort="{pr['createdAt']}">{fmt_date(pr['createdAt'])}</td>
  <td class="num" data-sort="{pr['waitingSeconds']}">{days}</td>
  <td class="num">{pr['committerComments']}</td>
  <td class="num">{pr['nonCommitterComments']}</td>
</tr>""")
    return "\n".join(rows)


RESPONSE_KIND = {"comment": "", "merge": " (merged)", "close": " (closed)"}


def responded_table(responded_prs):
    """One row per PR and response mode ("any" or "committer")."""
    rows = []
    for pr in responded_prs:
        role = "committer" if pr["authorIsCommitter"] else "contributor"
        for mode, resp in (("any", pr["anyResponse"]), ("committer", pr["committerResponse"])):
            if resp is None:
                continue
            rows.append(f"""<tr data-mode="{mode}" data-repo="{escape(pr['repo'])}"
    data-role="{role}" data-search="{escape((pr['repo'] + ' ' + pr['title'] + ' ' + pr['author'] + ' ' + resp['by']).lower())}">
  <td>{gh_link(pr['repo'])}</td>
  <td><a href="{escape(pr['url'])}">#{pr['number']}</a> {escape(pr['title'])}</td>
  <td>{gh_link(pr['author'])}{' <span class="tag">committer</span>' if pr['authorIsCommitter'] else ''}</td>
  <td>{pr['state'].lower()}</td>
  <td class="num" data-sort="{resp['waitedSeconds']}">{resp['waitedSeconds'] // 86400}</td>
  <td class="nowrap" data-sort="{resp['at']}">{fmt_date(resp['at'])}</td>
  <td>{gh_link(resp['by'])}{RESPONSE_KIND[resp['kind']]}{' <span class="tag">committer</span>' if resp['byCommitter'] else ''}</td>
</tr>""")
    return "\n".join(rows)


def repo_options(repos):
    return "\n".join(f'<option value="{escape(r)}">{escape(r)}</option>' for r in repos)


def repo_table(repo_names, repo_stats, stale_prs):
    stale = Counter(pr["repo"] for pr in stale_prs)
    neglected = Counter(pr["repo"] for pr in stale_prs if pr["committerComments"] == 0)
    # Repos added since the summary was made have no closed-PR statistics yet.
    no_stats = {"closedPrCount": 0, "respondedPrCount": 0,
                "medianSeconds": None, "averageSeconds": None}
    rows = []
    for repo in repo_names:
        data = repo_stats.get(repo, no_stats)
        if data["closedPrCount"] == 0 and not stale[repo]:
            continue
        rows.append(f"""<tr>
  <td>{gh_link(repo)}</td>
  <td class="num">{data['closedPrCount']}</td>
  <td class="num">{data['respondedPrCount']}</td>
  <td class="num" data-sort="{data['medianSeconds'] if data['medianSeconds'] is not None else -1}">{fmt(data['medianSeconds'])}</td>
  <td class="num" data-sort="{data['averageSeconds'] if data['averageSeconds'] is not None else -1}">{fmt(data['averageSeconds'])}</td>
  <td class="num">{stale[repo]}</td>
  <td class="num">{neglected[repo]}</td>
</tr>""")
    return "\n".join(rows)


def comparison_table(rows):
    out = []
    for row in rows:
        c, n = row["committer"], row["nonCommitter"]
        cls = ' class="total"' if row["period"] == "All" else ""
        out.append(f"""<tr{cls}>
  <td>{escape(row['period'])}</td>
  <td class="num">{c['prs']}</td><td class="num">{fmt(c['median'])}</td><td class="num">{fmt(c['average'])}</td>
  <td class="num">{n['prs']}</td><td class="num">{fmt(n['median'])}</td><td class="num">{fmt(n['average'])}</td>
</tr>""")
    return "\n".join(out)


def percentile_table(percentiles):
    if not percentiles:
        return '<tr><td colspan="2" class="muted">No closed PRs with a response</td></tr>'
    return "\n".join(
        f'<tr><td>{p}%</td><td class="num">{fmt(seconds)}</td></tr>'
        for p, seconds in percentiles.items()
    )


def build():
    summary = load_summary()
    repo_list = json.loads(REPOS_FILE.read_text())
    stale = json.loads(STALE_FILE.read_text())
    stale_prs = stale["pullRequests"]
    responded_prs = stale["respondedPullRequests"]
    overall = summary["overall"]
    repo_names = [r["nameWithOwner"] for org_repos in repo_list["orgs"].values()
                  for r in org_repos]
    neglected_count = sum(pr["committerComments"] == 0 for pr in stale_prs)
    orgs = sorted(repo_list["orgs"])
    repos_with_stale = sorted({pr["repo"] for pr in stale_prs})
    generated = datetime.fromisoformat(stale["generatedAt"]).strftime("%Y-%m-%d")
    days = stale["minDaysWaiting"]
    window = stale["responseWindowDays"]

    cards = "".join([
        stat_card(len(repo_names), "repos tracked"),
        stat_card(f"{overall['respondedPrCount']:,}", "closed PRs with a response"),
        stat_card(fmt(overall["medianSeconds"]), "median time to first response"),
        stat_card(len(stale_prs), f"open PRs waiting > {days} days"),
        stat_card(neglected_count, "of those with no committer comments"),
        stat_card(sum(pr["committerResponse"] is not None for pr in responded_prs),
                  f"answered by a committer after > {days} days, last {window} days"),
    ])

    html = TEMPLATE.format(
        generated=generated,
        orgs=", ".join(gh_link(o) for o in orgs),
        cards=cards,
        days=days,
        window=window,
        repo_options=repo_options(repos_with_stale),
        answered_repo_options=repo_options(sorted({pr["repo"] for pr in responded_prs})),
        stale_rows=stale_table(stale_prs),
        responded_rows=responded_table(responded_prs),
        repo_rows=repo_table(repo_names, summary["repos"], stale_prs),
        comparison_rows=comparison_table(summary["committerComparison"]),
        committer_count=len(summary["committers"]),
        percentile_rows=percentile_table(summary["percentiles"]),
        responded=f"{overall['respondedPrCount']:,}",
    )
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "index.html").write_text(html)
    shutil.copy(FAVICON, SITE_DIR / FAVICON.name)
    # Tell GitHub Pages to serve the files as-is instead of running Jekyll.
    (SITE_DIR / ".nojekyll").touch()
    print(f"Wrote {(SITE_DIR / 'index.html').relative_to(ROOT)}")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ROS PR Watch</title>
<link rel="icon" href="favicon.ico">
<style>
:root {{
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --surface: #f6f8fa; --accent: #0969da; --tag-bg: #ddf4ff; --tag-fg: #0550ae;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
    --surface: #151b23; --accent: #4493f8; --tag-bg: #12263f; --tag-fg: #79c0ff;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}}
main {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ margin: 0 0 4px; font-size: 28px; }}
h2 {{ margin: 48px 0 8px; font-size: 21px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
p {{ margin: 8px 0; }}
.muted {{ color: var(--muted); }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
nav a {{ margin-right: 16px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 24px 0; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }}
.card .value {{ font-size: 24px; font-weight: 600; }}
.card .label {{ color: var(--muted); font-size: 13px; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 12px 0; }}
.controls input[type=search], .controls select {{
  font: inherit; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg); color: var(--fg); max-width: 100%;
}}
.table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
th {{ background: var(--surface); position: sticky; top: 0; white-space: nowrap; }}
th.sortable {{ cursor: pointer; user-select: none; }}
th.sortable::after {{ content: " ↕"; color: var(--muted); }}
th.asc::after {{ content: " ↑"; color: var(--fg); }}
th.desc::after {{ content: " ↓"; color: var(--fg); }}
.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
tr.total td {{ font-weight: 600; background: var(--surface); }}
.nowrap {{ white-space: nowrap; }}
.tag {{ background: var(--tag-bg); color: var(--tag-fg); border-radius: 10px; padding: 0 7px; font-size: 12px; white-space: nowrap; }}
.scroll {{ max-height: 640px; overflow-y: auto; }}
.count {{ font-weight: 600; }}
</style>
</head>
<body>
<main>
<h1>ROS PR Watch</h1>
<p class="muted">Pull requests waiting on maintainers in {orgs}. Data collected {generated}.</p>
<nav><a href="#stale">Waiting PRs</a><a href="#answered">Recently answered</a><a href="#repos">Repos</a><a href="#committers">Committers vs. contributors</a><a href="#distribution">Response times</a><a href="#definitions">Definitions</a></nav>

<div class="cards">{cards}</div>

<h2 id="stale">Open PRs waiting on a response</h2>
<p>Open, non-draft PRs where the author made the last comment and has waited more than {days} days for a response.
By default only PRs with <strong>no comments from any committer</strong> are shown.</p>
<div class="controls">
  <label><input type="checkbox" id="only-neglected" checked> No committer comments only</label>
  <select id="repo-filter" aria-label="Filter by repo"><option value="">All repos</option>
{repo_options}
  </select>
  <select id="role-filter" aria-label="Filter by author">
    <option value="">All authors</option><option value="contributor">Non-committer authors</option>
    <option value="committer">Committer authors</option>
  </select>
  <input type="search" id="search" placeholder="Search title, author, repo">
  <span class="muted"><span id="stale-count" class="count"></span> PRs shown</span>
</div>
<div class="table-wrap scroll">
<table id="stale-table">
<thead><tr>
  <th class="sortable">Repo</th><th class="sortable">Pull request</th><th class="sortable">Author</th>
  <th class="sortable desc">Opened</th><th class="sortable num">Days<br>waiting</th>
  <th class="sortable num">Committer<br>comments</th><th class="sortable num">Other<br>comments</th>
</tr></thead>
<tbody>
{stale_rows}
</tbody>
</table>
</div>

<h2 id="answered">Recently answered PRs</h2>
<p>PRs (open, merged or closed) whose author had waited more than {days} days, where the wait ended with a
response in the last {window} days. Here a merge or close by someone other than the author counts as a response.
With <strong>Committers only</strong>, only waits ended by a committer are listed.</p>
<div class="controls">
  <select id="answered-mode" aria-label="Response from">
    <option value="committer">Response from a committer</option><option value="any">Response from anyone</option>
  </select>
  <select id="answered-repo-filter" aria-label="Filter by repo"><option value="">All repos</option>
{answered_repo_options}
  </select>
  <select id="answered-role-filter" aria-label="Filter by author">
    <option value="">All authors</option><option value="contributor">Non-committer authors</option>
    <option value="committer">Committer authors</option>
  </select>
  <input type="search" id="answered-search" placeholder="Search title, people, repo">
  <span class="muted"><span id="answered-count" class="count"></span> PRs shown</span>
</div>
<div class="table-wrap scroll">
<table id="answered-table">
<thead><tr>
  <th class="sortable">Repo</th><th class="sortable">Pull request</th><th class="sortable">Author</th>
  <th class="sortable">State</th><th class="sortable num">Days<br>waited</th>
  <th class="sortable desc">Responded</th><th class="sortable">Responder</th>
</tr></thead>
<tbody>
{responded_rows}
</tbody>
</table>
</div>

<h2 id="repos">Repos</h2>
<p>Time to first response for closed PRs, and counts of open PRs waiting more than {days} days.</p>
<div class="table-wrap scroll">
<table class="sortable-table">
<thead><tr>
  <th class="sortable">Repo</th><th class="sortable num">Closed PRs</th><th class="sortable num">With response</th>
  <th class="sortable num">Median response</th><th class="sortable num">Average response</th>
  <th class="sortable num">Waiting PRs</th><th class="sortable num">No committer comments</th>
</tr></thead>
<tbody>
{repo_rows}
</tbody>
</table>
</div>

<h2 id="committers">Committers vs. other contributors</h2>
<p>Time to first response for closed PRs, by the year the PR was opened and whether its author is one of the
{committer_count} committers.</p>
<div class="table-wrap">
<table>
<thead>
<tr><th rowspan="2">Year</th><th colspan="3">Committer authors</th><th colspan="3">Other authors</th></tr>
<tr><th class="num">PRs</th><th class="num">Median</th><th class="num">Average</th>
    <th class="num">PRs</th><th class="num">Median</th><th class="num">Average</th></tr>
</thead>
<tbody>
{comparison_rows}
</tbody>
</table>
</div>

<h2 id="distribution">How long PRs wait for a first response</h2>
<p>Share of the {responded} closed PRs that received a first response within each time.</p>
<div class="table-wrap" style="max-width: 360px">
<table>
<thead><tr><th>Percentile</th><th class="num">Responded within</th></tr></thead>
<tbody>
{percentile_rows}
</tbody>
</table>
</div>

<h2 id="definitions">Definitions</h2>
<ul>
  <li><strong>Response</strong>: a comment or submitted review by someone other than the PR author. Bot comments are ignored.</li>
  <li><strong>Committer</strong>: anyone who has merged at least one PR in the tracked repos, including in the last {window} days.</li>
  <li><strong>Waiting PR</strong>: an open, non-draft PR where the author (counting the PR opening) made the last comment and has waited more than {days} days. The wait runs from the first of the author's comments since anyone else last commented.</li>
  <li><strong>Recently answered PR</strong>: a PR whose author waited more than {days} days, where the wait ended
    with a response (or a merge or close by someone else) in the last {window} days. The wait runs from the first of the
    author's comments since the previous response, and is ended by any response.</li>
  <li>PRs authored by bots are excluded. Response-time statistics cover only closed PRs that received a response.</li>
</ul>
</main>
<script>
(() => {{
  function cellValue(row, index) {{
    const cell = row.cells[index];
    const v = cell.dataset.sort ?? cell.textContent.trim();
    const n = Number(v);
    return v !== "" && !isNaN(n) ? n : v.toLowerCase();
  }}
  function makeSortable(table) {{
    const headers = table.tHead.rows[table.tHead.rows.length - 1].cells;
    Array.from(headers).forEach((th, index) => {{
      if (!th.classList.contains("sortable")) return;
      th.addEventListener("click", () => {{
        const asc = !th.classList.contains("asc");
        Array.from(headers).forEach(h => h.classList.remove("asc", "desc"));
        th.classList.add(asc ? "asc" : "desc");
        const body = table.tBodies[0];
        const rows = Array.from(body.rows);
        rows.sort((a, b) => {{
          const x = cellValue(a, index), y = cellValue(b, index);
          return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
        }});
        rows.forEach(r => body.appendChild(r));
      }});
    }});
  }}
  document.querySelectorAll("#stale-table, #answered-table, .sortable-table").forEach(makeSortable);

  // Filter a table's rows by repo, author role and search text, plus an
  // optional extra test, keeping the shown count up to date.
  function filterTable(prefix, table, extraInputs, extraTest) {{
    const byId = id => document.getElementById(prefix + id);
    const repoFilter = byId("repo-filter");
    const roleFilter = byId("role-filter");
    const search = byId("search");
    const count = document.getElementById(table + "-count");
    function applyFilters() {{
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll("#" + table + "-table tbody tr").forEach(row => {{
        const visible = extraTest(row)
          && (!repoFilter.value || row.dataset.repo === repoFilter.value)
          && (!roleFilter.value || row.dataset.role === roleFilter.value)
          && (!q || row.dataset.search.includes(q));
        row.hidden = !visible;
        if (visible) shown++;
      }});
      count.textContent = shown;
    }}
    [repoFilter, roleFilter, ...extraInputs].forEach(el => el.addEventListener("change", applyFilters));
    search.addEventListener("input", applyFilters);
    applyFilters();
  }}

  const onlyNeglected = document.getElementById("only-neglected");
  filterTable("", "stale", [onlyNeglected],
    row => !onlyNeglected.checked || row.dataset.neglected === "1");
  const answeredMode = document.getElementById("answered-mode");
  filterTable("answered-", "answered", [answeredMode],
    row => row.dataset.mode === answeredMode.value);
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
