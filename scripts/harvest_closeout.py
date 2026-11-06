#!/usr/bin/env python3
"""Preserve merged PR receipts independently of Devin's session store."""

import argparse
import collections
import concurrent.futures
import csv
import datetime as dt
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
AGENTS = {
    "devin": r"generated\s+(?:with|by)\s+\[?devin\b|co-authored-by:[^\n]*devin-ai-integration|co-authored-by:\s*devin(?:\[bot\])?\s*<",
    "codebuff": r"generated\s+(?:with|by)\s+\[?codebuff\b|co-authored-by:[^\n]*codebuff",
    "factory": r"co-authored-by:[^\n]*factory-droid|generated\s+(?:with|by)\s+\[?(?:factory|droid)\b",
    "claude": r"generated\s+(?:with|by)\s+\[?claude\b|co-authored-by:\s*claude\b",
    "codex": r"generated\s+(?:with|by)\s+\[?codex\b|co-authored-by:\s*codex\b",
    "copilot": r"co-authored-by:[^\n]*copilot",
}
FIELDS = """
id number url title createdAt mergedAt updatedAt additions deletions changedFiles
repository { nameWithOwner isPrivate } author { login }
body mergeCommit { oid message }
commits(first:100) {
  totalCount pageInfo { hasNextPage endCursor }
  nodes { commit { oid message author { user { login } } committer { user { login } } } }
}
"""


def timestamp(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def atomic_write(path, content, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.chmod(tmp, 0o600 if private else 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def markers(text):
    return sorted(name for name, pattern in AGENTS.items()
                  if re.search(pattern, text or "", re.I))


def evidence(text, source, sha=None):
    return [dict(agent=agent, source=source, **({"sha": sha} if sha else {}))
            for agent in markers(text)]


def gh_json(arguments):
    for attempt in range(3):
        result = subprocess.run(["gh", *arguments], capture_output=True,
                                text=True, timeout=120)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and data.get("errors"):
                raise RuntimeError("GitHub returned GraphQL errors; refusing a partial archive")
            return data
        if attempt < 2:
            time.sleep(2 ** attempt)
    diagnostic = result.stderr.strip()[:500]
    raise RuntimeError(f"GitHub request failed (exit {result.returncode}): {diagnostic}; no public archive written")


def graphql(document, **variables):
    args = ["api", "graphql", "-f", f"query={document}"]
    for key, value in variables.items():
        if value is not None:
            args.extend(["-f", f"{key}={value}"])
    result = gh_json(args)
    if not result.get("data"):
        raise RuntimeError("GitHub returned no query data")
    return result["data"]


def normalize_pr(node, extra_commits=None):
    if not node or not node.get("mergedAt"):
        raise ValueError("Search returned a missing or unmerged PR")
    commits = list(node["commits"]["nodes"])
    commits.extend(extra_commits or [])
    if len(commits) != node["commits"]["totalCount"]:
        raise ValueError("Incomplete PR commit history")
    if len({item["commit"]["oid"] for item in commits}) != len(commits):
        raise ValueError("Duplicate PR commit page")
    merge = node.get("mergeCommit")
    signals = evidence(node.get("body"), "pr_body")
    bot_logins = {"devin-ai-integration[bot]", "devin-ai-integration", "devin[bot]"}
    if (node.get("author") or {}).get("login", "").lower() in bot_logins:
        signals.append({"agent": "devin", "source": "pr_author"})
    if re.search(r"https://(?:app\.)?devin\.ai/sessions?/", node.get("body") or "", re.I):
        signals.append({"agent": "devin-session-link", "source": "pr_body"})
    if merge:
        signals.extend(evidence(merge["message"], "merge_commit", merge["oid"]))
    for item in commits:
        commit = item["commit"]
        signals.extend(evidence(commit["message"], "pr_commit", commit["oid"]))
        for role in ("author", "committer"):
            login = ((commit.get(role) or {}).get("user") or {}).get("login", "")
            if login.lower() in bot_logins:
                signals.append({"agent": "devin", "source": f"pr_commit_{role}", "sha": commit["oid"]})
    return {
        "repository": node["repository"]["nameWithOwner"],
        "private": node["repository"]["isPrivate"],
        "number": node["number"], "url": node["url"], "title": node["title"],
        "created_at": node["createdAt"], "merged_at": node["mergedAt"],
        "updated_at": node["updatedAt"],
        "additions": node["additions"], "deletions": node["deletions"],
        "changed_files": node["changedFiles"],
        "merge_commit": merge["oid"] if merge else None,
        "commits": [item["commit"]["oid"] for item in commits],
        "evidence": signals,
    }


class Harvester:
    def __init__(self, cache):
        self.cache = cache
        self.searches = []

    def page(self, query, cursor=None):
        key = hashlib.sha256(encoded([query, cursor])).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        result = graphql("""
query($search:String!, $cursor:String) {
  search(query:$search, type:ISSUE, first:100, after:$cursor) {
    issueCount pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { """ + FIELDS + """ } }
  }
}""", search=query, cursor=cursor)["search"]
        rows = []
        # An over-cap first page is only used to decide how to split the interval.
        if result["issueCount"] < 1000:
            for node in result["nodes"]:
                extra = []
                connection = node["commits"]
                while connection["pageInfo"]["hasNextPage"]:
                    connection = graphql("""
query($id:ID!, $cursor:String!) {
  node(id:$id) { ... on PullRequest {
    commits(first:100, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { commit { oid message author { user { login } } committer { user { login } } } }
    }
  } }
}""", id=node["id"], cursor=connection["pageInfo"]["endCursor"])["node"]["commits"]
                    extra.extend(connection["nodes"])
                rows.append(normalize_pr(node, extra))
        page = {"count": result["issueCount"], "page_info": result["pageInfo"], "rows": rows}
        atomic_write(path, encoded(page), private=True)
        return page

    def interval(self, scope, start, end):
        query = f"is:pr is:merged {scope} merged:{timestamp(start)}..{timestamp(end)}"
        first = self.page(query)
        if first["count"] >= 1000:
            seconds = int((end - start).total_seconds())
            if seconds == 0:
                raise RuntimeError("One second exceeds GitHub's search cap; archive is incomplete")
            middle = start + dt.timedelta(seconds=seconds // 2)
            return (self.interval(scope, start, middle) +
                    self.interval(scope, middle + dt.timedelta(seconds=1), end))
        rows = list(first["rows"])
        page = first
        cursors = set()
        while page["page_info"]["hasNextPage"]:
            cursor = page["page_info"]["endCursor"]
            if not cursor or cursor in cursors:
                raise RuntimeError("GitHub returned a repeated pagination cursor")
            cursors.add(cursor)
            page = self.page(query, cursor)
            if page["count"] != first["count"]:
                raise RuntimeError("Search count changed during pagination; use a fresh backup directory")
            rows.extend(page["rows"])
        if len(rows) != first["count"] or len({row["url"] for row in rows}) != len(rows):
            raise RuntimeError("Search pagination did not recover every PR")
        self.searches.append({"query": query, "count": len(rows)})
        print(f"Captured {len(rows)} merged PRs, {timestamp(start)} to {timestamp(end)}", flush=True)
        return rows


def git(path, *args, optional=False):
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                            text=True, timeout=120)
    if result.returncode and not optional:
        raise RuntimeError("Local Git history could not be read completely")
    return result.stdout.strip() if result.returncode == 0 else ""


def local_repository(path, visibility, since, until):
    if not (path / ".git").exists():
        return None
    remote = git(path, "remote", "get-url", "origin", optional=True)
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)(.+?)(?:\.git)?", remote)
    if not match or match[1].lower() not in visibility:
        return None
    repository, private = visibility[match[1].lower()]
    default = git(path, "symbolic-ref", "refs/remotes/origin/HEAD", optional=True)
    if not default:
        for candidate in ("refs/remotes/origin/main", "refs/remotes/origin/master",
                          "refs/heads/main", "refs/heads/master"):
            if git(path, "rev-parse", "--verify", candidate, optional=True):
                default = candidate
                break
    default_shas = set(git(path, "rev-list", default).splitlines()) if default else set()
    log = git(path, "log", "--all", "--no-show-signature",
              f"--since={since}T00:00:00Z", f"--until={until}T23:59:59Z",
              "--format=%H%x00%cI%x00%s%x00%B%x00%an <%ae>%x00%cn <%ce>%x1e")
    rows = []
    for record in log.split("\x1e"):
        if not record.strip():
            continue
        fields = record.lstrip("\n").split("\x00")
        if len(fields) != 6:
            raise RuntimeError("Unexpected local Git record format")
        sha, date, subject, message, author, committer = fields
        signals = evidence(message, "local_commit", sha)
        if re.search(r"devin-ai-integration|^devin\[bot\]\s*<", author, re.I):
            signals.append({"agent": "devin", "source": "git_author", "sha": sha})
        if re.search(r"devin-ai-integration|^devin\[bot\]\s*<", committer, re.I):
            signals.append({"agent": "devin", "source": "git_committer", "sha": sha})
        if any(signal["agent"] == "devin" for signal in signals):
            rows.append({"repository": repository, "private": private,
                         "sha": sha, "committed_at": date, "subject": subject,
                         "on_local_default_branch": sha in default_shas,
                         "evidence": signals})
    return {"repository": repository, "private": private,
            "default_ref": default or None,
            "default_tip": git(path, "rev-parse", default) if default else None,
            "commits": rows}


def agent_names(row):
    return sorted({item["agent"] for item in row["evidence"]})


def link_local_evidence(prs, local):
    by_sha = {(row["repository"].lower(), row["sha"]): row for row in local}
    for pr in prs:
        existing = {(item["agent"], item["source"], item.get("sha")) for item in pr["evidence"]}
        for sha in set(pr["commits"] + ([pr["merge_commit"]] if pr["merge_commit"] else [])):
            commit = by_sha.get((pr["repository"].lower(), sha))
            if commit:
                for signal in commit["evidence"]:
                    key = (signal["agent"], signal["source"], signal.get("sha"))
                    if key not in existing:
                        pr["evidence"].append(signal)
                        existing.add(key)


def summary(prs, local):
    return {
        "merged_prs": len(prs),
        "repositories": len({row["repository"] for row in prs}),
        "devin_marker_prs": sum("devin" in agent_names(row) for row in prs),
        "devin_commit_only_prs": sum(
            "devin" in agent_names(row) and not any(
                item["agent"] == "devin" and item["source"] == "pr_body"
                for item in row["evidence"]) for row in prs),
        "no_recognized_agent_marker_prs": sum(not agent_names(row) for row in prs),
        "local_devin_commits": len(local),
        "local_default_branch_devin_commits": sum(row["on_local_default_branch"] for row in local),
    }


def spreadsheet_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def exports(prs, local, metadata):
    if not metadata["includes_private"] and any(row["private"] for row in prs + local):
        raise ValueError("Private record in public archive")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    columns = ["repository", "number", "title", "url", "created_at", "merged_at",
               "merge_commit", "additions", "deletions", "changed_files"]
    writer.writerow(columns + ["agent_evidence"])
    for row in prs:
        writer.writerow([spreadsheet_cell(row[key] if row[key] is not None else "") for key in columns] +
                        [", ".join(agent_names(row))])
    md = ["# Merged pull request receipts", "",
          f"Captured {metadata['captured_at']}. Merged {metadata['since']} through {metadata['until']} UTC.",
          "", "Agent markers are evidence attached to a PR, not proof of sole authorship.",
          "PR descriptions, commit bodies, and chat transcripts are not included.", ""]
    for row in prs:
        title = html.escape(row["title"]).replace("\n", " ").replace("[", r"\[").replace("]", r"\]")
        md.append(f"- {row['merged_at']} · {row['repository']} #{row['number']} · "
                  f"[{title}]({row['url']}) · {', '.join(agent_names(row)) or 'no recognized agent marker'} "
                  f"· merge `{row['merge_commit'] or 'unavailable'}`")
    readme = (
        f"# {'Private master' if metadata['includes_private'] else 'Public'} merge archive\n\n"
        f"Captured {metadata['captured_at']}, covering merges from {metadata['since']} through {metadata['until']} UTC.\n\n"
        "The scope is repositories owned by the named account, plus PRs authored by that account elsewhere. "
        "A Devin marker is provenance evidence, not sole authorship or a quality score.\n\n"
        "Start with `merged-prs.md`. The CSV and JSON preserve the same PR set. "
        "`local-commits.json` preserves Devin-marked local Git metadata; "
        "`manifest.json` records the scope, totals, and SHA-256 checksums.\n\n"
        + ("This is the private master, including private-repository receipts. Do not publish this directory. "
           "`original-report/` preserves the report's source JSON before the merged-work expansion.\n\n"
           if metadata["includes_private"] else
           "Private-repository records are excluded from this public copy.\n\n")
        + "These files survive removing Devin only if you keep this directory. "
        "They do not back up chat transcripts, uncommitted work, Git objects, or application data. "
        "Preserve those separately before deleting them.\n"
    )
    files = {"merged-prs.json": encoded(prs), "local-commits.json": encoded(local),
             "merged-prs.csv": out.getvalue().encode(), "merged-prs.md": ("\n".join(md) + "\n").encode(),
             "README.md": readme.encode()}
    manifest = dict(metadata, summary=summary(prs, local),
                    files={name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                           for name, content in files.items()})
    files["manifest.json"] = encoded(manifest)
    return files


def write_exports(directory, files, private=False):
    directory.mkdir(parents=True, exist_ok=True)
    # The manifest is last, so verification detects an interrupted replacement.
    for name, content in files.items():
        atomic_write(directory / name, content, private=private)


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, record in manifest["files"].items():
        content = (directory / name).read_bytes()
        if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"Receipt checksum failed: {name}")
    prs = json.loads((directory / "merged-prs.json").read_text())
    local = json.loads((directory / "local-commits.json").read_text())
    if not manifest["includes_private"] and any(row["private"] for row in prs + local):
        raise ValueError("Private record in public archive")
    if len(prs) != len({row["url"] for row in prs}):
        raise ValueError("Duplicate PR receipt")
    if len(local) != len({(row["repository"], row["sha"]) for row in local}):
        raise ValueError("Duplicate local commit receipt")
    for row in prs:
        if not manifest["since"] <= row["merged_at"][:10] <= manifest["until"]:
            raise ValueError("PR outside the merge-date window")
        if not row["merge_commit"]:
            raise ValueError("Merged PR is missing its merge SHA")
    if summary(prs, local) != manifest["summary"]:
        raise ValueError("Receipt summary does not match preserved records")
    return manifest, prs, local


def render(public):
    manifest, prs, local = verify(public)
    counts = collections.Counter(row["repository"] for row in prs)
    devin_counts = collections.Counter(row["repository"] for row in prs if "devin" in agent_names(row))
    relative = public.relative_to(ROOT / "docs").as_posix()
    s = manifest["summary"]
    lines = [
        "<!-- merged-work:start -->",
        "## WHAT ACTUALLY MERGED", "",
        f"This close-out was captured from GitHub on {manifest['captured_at']}. "
        f"It covers PRs <strong>merged from {manifest['since']} through {manifest['until']} UTC</strong>, "
        f"in repositories owned by <code>{html.escape(manifest['user'])}</code>, plus PRs authored by that account elsewhere. "
        "The search does not depend on a Devin session or a PR-body credit.", "",
        '<table class="statrow">',
        f"<tr><td>{s['merged_prs']:,}</td><td>{s['repositories']}</td><td>{s['devin_marker_prs']:,}</td><td>{s['devin_commit_only_prs']:,}</td></tr>",
        "<tr><td>public PRs merged</td><td>public repos</td><td>PRs with Devin markers</td><td>Devin markers outside the PR body only</td></tr>",
        "</table>", "",
        "These are merged PRs, not sessions, turns, commits, or unique features. A Devin marker can occur in the "
        "PR body, a constituent commit, the merge commit, a recognized Devin bot identity, or matching local Git history. A PR can carry credits "
        "for more than one agent. A Devin session link alone is recorded separately and does not count as an authorship marker. "
        "A missing marker does not prove that the work was human-written.", "",
        "### THE MERGED WORK, BY REPOSITORY", "",
    ]
    highlights = ROOT / "data/closeout-highlights.md"
    if highlights.exists():
        at = lines.index("### THE MERGED WORK, BY REPOSITORY")
        lines[at:at] = ["### WHAT THE MERGES COVERED", "", highlights.read_text().strip(), ""]
    top = counts.most_common(12)
    height = 36 * len(top) + 25
    lines.append(f'<svg viewBox="0 0 960 {height}" role="img" aria-label="Merged public pull requests by repository" style="width:100%;height:auto;font-family:monospace">')
    for i, (repo, n) in enumerate(top):
        y = i * 36 + 22
        width = round(430 * n / top[0][1], 1)
        lines.append(f'<text x="0" y="{y}" fill="#9fdcb4" font-size="16">{html.escape(repo.split("/")[-1])}</text>'
                     f'<rect x="360" y="{y-16}" width="{width}" height="21" fill="#58ff9d"/>'
                     f'<text x="{800}" y="{y}" fill="#ffb347" font-size="16">{n:,}</text>')
    lines.extend(["</svg>", "", '<table class="specs">',
                  "<tr><th>repository</th><th>merged PRs</th><th>with Devin markers</th><th>latest merge UTC</th></tr>"])
    for repo, n in counts.most_common():
        latest = max(row["merged_at"] for row in prs if row["repository"] == repo)
        lines.append(f'<tr><td><a href="https://github.com/{html.escape(repo)}">{html.escape(repo)}</a></td>'
                     f"<td>{n:,}</td><td>{devin_counts[repo]:,}</td><td>{latest[:10]}</td></tr>")
    lines.extend(["</table>", "", "### EVERY MERGED PR, WITH ITS RECEIPT", "",
                  "Open a repository to see every captured merge, its title, date, agent markers, and merge commit. "
                  "The downloadable records also include additions, deletions, changed-file counts, and constituent commit SHAs.", ""])
    for repo, n in counts.most_common():
        lines.append(f"<details><summary>{html.escape(repo)} · {n:,} merged PRs</summary>")
        lines.extend(['<table class="specs">', "<tr><th>merged UTC</th><th>pull request</th><th>agent evidence</th><th>merge commit</th></tr>"])
        for row in sorted((p for p in prs if p["repository"] == repo), key=lambda p: p["merged_at"], reverse=True):
            sha = row["merge_commit"]
            lines.append(f'<tr><td>{row["merged_at"][:10]}</td><td><a href="{html.escape(row["url"], quote=True)}">'
                         f'#{row["number"]} {html.escape(row["title"])}</a></td>'
                         f'<td>{html.escape(", ".join(agent_names(row)) or "no recognized marker")}</td>'
                         f'<td><a href="https://github.com/{html.escape(repo)}/commit/{sha}">{sha[:10]}</a></td></tr>')
        lines.extend(["</table></details>", ""])
    lines.extend(["### THE RECORDS THAT SURVIVE UNINSTALL", "",
                  f'<a href="{relative}/merged-prs.md">Readable merge inventory</a> · '
                  f'<a href="{relative}/merged-prs.csv">CSV</a> · '
                  f'<a href="{relative}/merged-prs.json">PR receipts as JSON</a> · '
                  f'<a href="{relative}/local-commits.json">Local Git commit receipts</a> · '
                  f'<a href="{relative}/manifest.json">Scope and SHA-256 checksums</a>', "",
                  f"The public Git export preserves {len(local):,} Devin-marked commits in the capture window. "
                  f"Of those, {s['local_default_branch_devin_commits']:,} are reachable from the local clone's recorded default branch. "
                  "Those refs were not fetched, so this is local reachability, not independent proof of a GitHub merge.", "",
                  "Private-repository receipts are excluded from this site and saved separately in a local backup outside "
                  "Devin's application data. The public records above live with this static site. Neither export is a backup "
                  "of full chat transcripts, uncommitted files, Git objects, or the software itself.", "",
                  "GitHub search was split below its 1,000-result cap, every result page and every PR commit page was read, "
                  "and each search scope's count was checked against the REST search endpoint. Coverage is limited to the "
                  "authenticated account's access and the stated account and merge-date window. Deleted PRs, other accounts' "
                  "work in unrelated repositories, direct pushes, and unmerged branches are not part of the merged-PR total.", "",
                  "<!-- merged-work:end -->"])
    section = "\n".join(lines)
    path = ROOT / "data/epilogue.json"
    data = json.loads(path.read_text())
    start, end = "<!-- merged-work:start -->", "<!-- merged-work:end -->"
    if start in data["body"]:
        a = data["body"].index(start)
        b = data["body"].index(end, a) + len(end)
        data["body"] = data["body"][:a] + section + data["body"][b:]
    else:
        anchor = "## THE ARCHIVED SNAPSHOT"
        if data["body"].count(anchor) != 1:
            raise ValueError("Cannot locate archived snapshot in epilogue")
        data["body"] = data["body"].replace(anchor, section + "\n\n" + anchor)
    atomic_write(path, encoded(data))
    print(f"Rendered the public end cap: {len(prs):,} merged PRs in {len(counts)} repositories")


def harvest(args):
    public, private = args.public_dir.resolve(), args.private_dir.resolve()
    if private.is_relative_to(ROOT) or private.is_relative_to(Path.home() / ".local/share/devin"):
        raise ValueError("Private backup must be outside this repository and Devin application data")
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    if private.stat().st_mode & 0o077:
        raise ValueError("Private backup directory must have mode 0700")
    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until)
    if until < since:
        raise ValueError("End date precedes start date")
    harvester = Harvester(private / "cache")
    scopes = [f"user:{args.user}", f"author:{args.user}"]
    collected = {}
    checks = []
    for scope in scopes:
        start = dt.datetime.combine(since, dt.time(), UTC)
        last = dt.datetime.combine(until, dt.time(23, 59, 59), UTC)
        scope_rows = []
        while start <= last:
            end = min(start + dt.timedelta(days=7) - dt.timedelta(seconds=1), last)
            scope_rows.extend(harvester.interval(scope, start, end))
            start = end + dt.timedelta(seconds=1)
        query = f"is:pr is:merged {scope} merged:{since}..{until}"
        rest = gh_json(["api", "--method", "GET", "search/issues",
                        "-f", f"q={query}", "-f", "per_page=1"])
        count = len({row["url"] for row in scope_rows})
        if rest.get("incomplete_results") or rest["total_count"] != count:
            raise RuntimeError("Independent REST count did not match the harvested scope")
        checks.append({"scope": scope, "count": count, "rest_count_verified": True})
        for row in scope_rows:
            old = collected.get(row["url"])
            if old is None or old["updated_at"] < row["updated_at"]:
                collected[row["url"]] = row
    prs = sorted(collected.values(), key=lambda row: (row["merged_at"], row["url"]))
    repos = gh_json(["repo", "list", args.user, "--limit", "1000",
                     "--json", "nameWithOwner,isPrivate"])
    if len(repos) >= 1000:
        raise RuntimeError("Repository listing limit reached")
    visibility = {row["nameWithOwner"].lower(): (row["nameWithOwner"], row["isPrivate"]) for row in repos}
    visibility.update({row["repository"].lower(): (row["repository"], row["private"]) for row in prs})
    candidates = [path for path in args.dev_root.iterdir() if path.is_dir() and (path / ".git").exists()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda p: local_repository(p, visibility, args.since, args.until), candidates))
    snapshots = [row for row in snapshots if row]
    deduped = {}
    for snapshot in snapshots:
        for row in snapshot["commits"]:
            key = (row["repository"], row["sha"])
            if key in deduped:
                row["on_local_default_branch"] |= deduped[key]["on_local_default_branch"]
            deduped[key] = row
    local = sorted(deduped.values(), key=lambda row: (row["repository"], row["committed_at"], row["sha"]))
    link_local_evidence(prs, local)
    metadata = {
        "schema_version": 1, "captured_at": timestamp(dt.datetime.now(UTC)),
        "user": args.user, "since": args.since, "until": args.until,
        "includes_private": True, "scope_checks": checks,
        "searches": harvester.searches,
        "local_refs": [{key: value for key, value in row.items() if key != "commits"} for row in snapshots],
        "limits": [
            "PR coverage is owner-or-author account scope, merged within the UTC date window.",
            "Markers are provenance evidence, not sole-authorship or quality claims.",
            "No raw PR bodies, commit messages, email addresses, or chat transcripts are saved.",
            "Local refs were read without fetching. Direct commits are not merged PRs.",
            "CSV cells starting with spreadsheet formula characters have an apostrophe prefix.",
        ],
    }
    for source in (ROOT / "data").glob("*.json"):
        target = private / "original-report" / source.name
        if not target.exists():
            atomic_write(target, source.read_bytes(), private=True)
    write_exports(private, exports(prs, local, metadata), private=True)
    verify(private)
    public_metadata = dict(metadata, includes_private=False)
    public_metadata["local_refs"] = [row for row in metadata["local_refs"] if not row["private"]]
    # Scope counts include private PRs, so publish only the fact that the checks passed.
    public_metadata["scope_checks"] = [{key: value for key, value in row.items() if key != "count"} for row in checks]
    public_metadata.pop("searches")
    write_exports(public, exports([row for row in prs if not row["private"]],
                                 [row for row in local if not row["private"]], public_metadata))
    manifest, _, _ = verify(public)
    print("Public archive verified:", json.dumps(manifest["summary"], sort_keys=True))
    print("A separate private receipt backup and original report snapshots were saved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="drawmeanelephant")
    parser.add_argument("--since", default="2026-10-01")
    parser.add_argument("--until", default="2026-11-05")
    parser.add_argument("--dev-root", type=Path, default=Path.home() / "dev")
    parser.add_argument("--public-dir", type=Path, default=ROOT / "docs/assets/closeout-2026-11-05")
    parser.add_argument("--private-dir", type=Path, default=Path.home() / "Documents/devin-closeout-2026-11-05")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        manifest, _, _ = verify(args.public_dir)
        print("Verified:", json.dumps(manifest["summary"], sort_keys=True))
        return
    if not args.render_only:
        harvest(args)
    render(args.public_dir.resolve())


if __name__ == "__main__":
    main()
