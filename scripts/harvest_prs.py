#!/usr/bin/env python3
"""Harvest Devin-authored PRs across the operator's GitHub account.

Emits /tmp/prs_dev.json: [{repo, number, title, url, created, merged}]
PRs are found by the 'Generated with Devin' body trailer via the GitHub
search API — the same convention harvest_commits.py greps for in git log.
The search API caps at 1000 results per query, so the harvest walks
weekly date buckets, splitting to days if a bucket ever hits the cap.
"""
import json, subprocess, datetime, sys

USER = "drawmeanelephant"
START = datetime.date(2026, 10, 1)
END = datetime.date.today()
OUT = "/tmp/prs_dev.json"

JQ = ('.items[] | {repo: .repository_url, number, title, state, '
      'url: .html_url, created: .created_at, merged: .pull_request.merged_at}')


def search(d0, d1):
    q = (f'is:pr user:{USER} "Generated with Devin" '
         f'created:{d0.isoformat()}..{d1.isoformat()}')
    out = subprocess.run(
        ["gh", "api", "--paginate",
         f"search/issues?q={q.replace(' ', '+')}&per_page=100",
         "--jq", JQ],
        capture_output=True, text=True)
    if out.returncode != 0:
        print(f"search failed for {d0}..{d1}: {out.stderr.strip()}",
              file=sys.stderr)
        return []
    items = []
    for line in out.stdout.splitlines():  # --jq emits one compact object per line
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def week_ranges(start, end):
    d = start
    while d <= end:
        e = min(d + datetime.timedelta(days=6), end)
        yield d, e
        d = e + datetime.timedelta(days=1)


prs = {}
for d0, d1 in week_ranges(START, END):
    items = search(d0, d1)
    if len(items) >= 1000:  # hit the cap — split the week into days
        items = []
        d = d0
        while d <= d1:
            items += search(d, d)
            d += datetime.timedelta(days=1)
    for it in items:
        repo = it["repo"].rsplit("/", 1)[-1]
        prs[it["url"]] = {
            "repo": repo,
            "number": it["number"],
            "title": it["title"],
            "url": it["url"],
            "created": it["created"][:10],
            "state": it["state"],
            "merged": bool(it["merged"]),
        }

out = sorted(prs.values(), key=lambda p: (p["created"], p["url"]))
json.dump(out, open(OUT, "w"), indent=2)
n_merged = sum(1 for p in out if p["merged"])
print(f"{len(out)} PRs ({n_merged} merged) across "
      f"{len({p['repo'] for p in out})} repos → {OUT}")
