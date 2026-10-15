#!/usr/bin/env python3
"""Harvest agent-authored commits across local clones in ~/dev.

Emits /tmp/commits_dev.json: [{repo, remote, sha, date, subject, adds, dels}]
Devin commits: 'Generated with [Devin]' or 'devin-ai-integration' trailer.
Codebuff commits: 'Generated with Codebuff' (separate count).
"""
import json, os, re, subprocess, collections

DEV = "/Users/tbuddy/dev"
PAT_DEVIN = r"Generated with \[Devin\]\|devin-ai-integration"
PAT_CB = r"Generated with Codebuff\|Generated with \[Codebuff"

def sh(repo, args):
    return subprocess.run(["git", "-C", repo] + args,
                          capture_output=True, text=True).stdout

def remote_url(repo):
    for r in ("origin",):
        u = sh(repo, ["remote", "get-url", r]).strip()
        if u:
            m = re.match(r"(?:git@|https://)github\.com[:/](.+?)(?:\.git)?$", u)
            if m:
                return f"https://github.com/{m.group(1)}"
    return ""

def commits(repo, pattern):
    out = sh(repo, ["log", "--all", f"--grep={pattern}", "--regexp-ignore-case",
                    "--numstat", "--format=@@@%H%x09%ad%x09%s", "--date=short"])
    cur = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            h, d, s = line[3:].split("\t", 2)
            cur = {"sha": h, "date": d, "subject": s, "adds": 0, "dels": 0}
            yield cur
        elif cur is not None and line.strip():
            p = line.split("\t")
            if len(p) == 3:
                if p[0].isdigit(): cur["adds"] += int(p[0])
                if p[1].isdigit(): cur["dels"] += int(p[1])

data = {}
for name in sorted(os.listdir(DEV)):
    path = os.path.join(DEV, name)
    if not os.path.isdir(path):
        continue
    if subprocess.run(["git", "-C", path, "rev-parse", "--git-dir"],
                      capture_output=True).returncode != 0:
        continue
    dev = [c for c in commits(path, PAT_DEVIN) if True]
    # dedupe identical shas
    seen = {}
    for c in dev: seen[c["sha"]] = c
    dev = sorted(seen.values(), key=lambda c: c["date"])
    cb = len(list(commits(path, PAT_CB)))
    if not dev and not cb:
        continue
    data[name] = {"repo": name, "remote": remote_url(path),
                  "commits": dev, "codebuff": cb}

json.dump(data, open("/tmp/commits_dev.json", "w"), indent=0)
tot = sum(len(v["commits"]) for v in data.values())
cbt = sum(v["codebuff"] for v in data.values())
print(f"{tot} devin commits / {cbt} codebuff commits across {len(data)} repos")
for r, v in sorted(data.items(), key=lambda kv: -len(kv[1]["commits"]))[:30]:
    print(f"  {len(v['commits']):4} devin  {v['codebuff']:4} cb  {r}")
