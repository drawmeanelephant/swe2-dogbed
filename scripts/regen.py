#!/usr/bin/env python3
"""Regenerate data/ledger.json and data/commits.json from live sources.

ledger: ~/.local/share/devin/cli/sessions.db (read-only) — assistant turns
deduped by session/message key, same as otter-stats' structural method.
commits: /tmp/commits_dev.json produced by scripts/harvest_commits.py
(trailer + author/committer union across local clones).
Body markup is Markdown + raw HTML (dogbed's post-k4o-rewrite pipeline).
"""
import json, os, sqlite3, collections, html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.local/share/devin/cli/sessions.db")

def esc(s):
    return html.escape(s).replace("|", "/").replace('"', "'")

def bignum(n):
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.1f}k"
    return str(n)

def bar(frac, width=24):
    return "▓" * max(1, round(frac * width)) if frac > 0 else "░"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

TURNS_SQL = """
WITH t AS (
  SELECT session_id,
         COALESCE(json_extract(chat_message,'$.message_id'),
                  'req:'||json_extract(chat_message,'$.metadata.request_id'),
                  'row:'||rowid) AS k,
         json_extract(chat_message,'$.metadata.generation_model') AS model,
         CASE WHEN created_at > 1000000000000
              THEN date(created_at/1000,'unixepoch')
              ELSE date(created_at,'unixepoch') END AS day,
         COALESCE(json_extract(chat_message,'$.metadata.metrics.input_tokens'),0) AS i,
         COALESCE(json_extract(chat_message,'$.metadata.metrics.output_tokens'),0) AS o,
         COALESCE(json_extract(chat_message,'$.metadata.metrics.cache_read_tokens'),0) AS cr,
         COALESCE(json_extract(chat_message,'$.metadata.metrics.cache_creation_tokens'),0) AS cc,
         ROW_NUMBER() OVER (PARTITION BY session_id,
              COALESCE(json_extract(chat_message,'$.message_id'),
                       'req:'||json_extract(chat_message,'$.metadata.request_id'),
                       'row:'||rowid) ORDER BY rowid) AS rn
  FROM message_nodes
  WHERE json_extract(chat_message,'$.role')='assistant'
    AND json_extract(chat_message,'$.metadata.generation_model') IS NOT NULL
)
SELECT model, day, session_id, i, o, cr, cc FROM t WHERE rn=1
"""

models = collections.defaultdict(lambda: [0,0,0,0,0])  # turns,i,o,cr,cc
days = collections.defaultdict(lambda: [0,0])
proj_tok = collections.Counter()
sess_per_model = collections.Counter()
n_sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
span = con.execute(
    "SELECT MIN(created_at), MAX(last_activity_at) FROM sessions").fetchone()
for wd, mdl, n in con.execute(
        "SELECT working_directory, model, COUNT(*) FROM sessions GROUP BY working_directory, model"):
    sess_per_model[mdl] += n
wd_of = dict(con.execute("SELECT id, working_directory FROM sessions"))

total_turns = total_tok = 0
cr_total = 0
for model, day, sid, i, o, cr, cc in con.execute(TURNS_SQL):
    a = models[model]
    a[0] += 1; a[1] += i; a[2] += o; a[3] += cr; a[4] += cc
    tok = i + o + cr + cc
    total_tok += tok; total_turns += 1; cr_total += cr
    days[day][0] += 1; days[day][1] += tok
    wd = wd_of.get(sid) or "?"
    proj = wd.replace("/Users/tbuddy/dev/", "")
    proj_tok[proj] += tok
con.close()

grand = total_tok
model_rows = []
for m, (t, i, o, cr, cc) in sorted(models.items(),
                                   key=lambda kv: sum(kv[1][1:]), reverse=True):
    tot = i + o + cr + cc
    name = m or "(unset)"
    model_rows.append(
        f'<tr><td><span class="cyan">{name}</span></td><td>{sess_per_model.get(m,0)}</td><td>{t:,}</td><td>{bignum(i)}</td><td>{bignum(o)}</td><td>{bignum(cr)}</td><td>{bignum(cc)}</td><td><strong>{bignum(tot)}</strong></td><td>{tot/grand*100:.1f}% {bar(tot/grand,10)}</td></tr>')

day_rows = []
dmax = max(v[1] for v in days.values())
for d in sorted(days):
    t, tok = days[d]
    day_rows.append(f"<tr><td>{d}</td><td>{t:,}</td><td><strong>{bignum(tok)}</strong></td><td>{bar(tok/dmax,30)}</td></tr>")

proj_rows = []
for p, tok in proj_tok.most_common(15):
    proj_rows.append(f"<tr><td>{esc(p)}</td><td><strong>{bignum(tok)}</strong></td><td>{tok/grand*100:.1f}%</td></tr>")

first_day = sorted(days)[0]; last_day = sorted(days)[-1]
peak_day = max(days.items(), key=lambda kv: kv[1][1])

ledger_body = f"""## THE TOP LINE

<table class="statrow">
<tr><td>{bignum(total_tok)}</td><td>{total_turns:,}</td><td>{n_sessions}</td><td>{len(days)}</td></tr>
<tr><td>tokens burned</td><td>assistant turns</td><td>sessions</td><td>days on record</td></tr>
</table>

Nine days of local Devin CLI/Desktop telemetry — every assistant turn in every session, deduplicated by message key the same way otter-stats does it. The window is {first_day} to {last_day}; the peak was <span class="ok">{peak_day[0]}</span> at <strong>{bignum(peak_day[1][1])} tokens in a single day</strong>.

## TOKENS BY MODEL

<table class="specs">
<tr><th>model</th><th>sessions</th><th>turns</th><th>input</th><th>output</th><th>cache read</th><th>cache write</th><th>total</th><th>share</th></tr>
{chr(10).join(model_rows)}
</table>

<p class="dim-note"><span class="dim">Honest footnote: cache-read dominates the ledger — it is context re-read from the provider's cache, not fresh reasoning. Of swe-2-high's {bignum(models['swe-2-high'][1]+models['swe-2-high'][2]+models['swe-2-high'][3])} tokens, {bignum(models['swe-2-high'][3])} are cache reads. Output tokens — the part that is actually authored — total {bignum(sum(v[2] for v in models.values()))} across all models.</span></p>

## BURN BY DAY

<table class="specs">
<tr><th>date</th><th>turns</th><th>tokens</th><th>load</th></tr>
{chr(10).join(day_rows)}
</table>

## WHERE IT WENT — TOP PROJECTS

<table class="specs">
<tr><th>working directory</th><th>tokens</th><th>share</th></tr>
{chr(10).join(proj_rows)}
</table>

## SESSION MODES

<table class="specs">
<tr><th>mode</th><th>sessions</th></tr>
<tr><td>accept-edits</td><td>532</td></tr>
<tr><td>bypass</td><td>69</td></tr>
<tr><td>smart</td><td>58</td></tr>
<tr><td>(unset)</td><td>2</td></tr>
</table>

> The operator ran <span class="amber">{n_sessions} sessions</span> in nine days and let the agents keep the receipts. Most of them ran in <span class="cyan">accept-edits</span> mode — trust, but verify the diff.

<p class="dim-note">Source: ~/.local/share/devin/cli/sessions.db, opened read-only, same database otter-stats reads. Token counts are provider-reported per assistant turn; metrics-missing turns (rare) count as zero. Compiled {last_day}.</p>"""

ledger = {
    "kicker": "transmission 11 · the operator's ledger",
    "title": "THE BURN LEDGER",
    "lede": "Not Cognition's history — the operator's. This is the local accounting of one user's Devin usage: every assistant turn, every model, every project, pulled straight from the sessions database on this machine.",
    "body": ledger_body,
}
json.dump(ledger, open(f"{ROOT}/data/ledger.json", "w"), indent=2)

# ---------------- commits ----------------

raw = json.load(open("/tmp/commits_dev.json"))
repos = {k: v for k, v in raw.items()
         if v["commits"] or v["codebuff"]
         if "chris-wozniczek" not in v["remote"]}
all_c = [c | {"repo": k, "remote": v["remote"]}
         for k, v in repos.items() for c in v["commits"]]
adds = sum(c["adds"] for c in all_c)
dels = sum(c["dels"] for c in all_c)
span0 = min(c["date"] for c in all_c); span1 = max(c["date"] for c in all_c)
cb_total = sum(v["codebuff"] for v in repos.values())
by_day = collections.Counter(c["date"] for c in all_c)

def sha_cell(c):
    s = c["sha"][:10]
    return (f'<a href="{c["remote"]}/commit/{c["sha"]}">{s}</a>'
            if c["remote"] else f'<span class="cyan">{s}</span>')

sections = []
for name in sorted(repos, key=lambda r: -len(repos[r]["commits"])):
    v = repos[name]
    if not v["commits"]:
        continue
    ra = sum(c["adds"] for c in v["commits"])
    rd = sum(c["dels"] for c in v["commits"])
    head = (f'### <a href="{v["remote"]}">{esc(name)}</a>'
            if v["remote"] else f"### {esc(name)}")
    head += f" — {len(v['commits'])} commits, +{ra:,}/−{rd:,}"
    if v["codebuff"]:
        head += f' <span class="dim">(+{v["codebuff"]} codebuff)</span>'
    rows = "\n".join(
        f'<tr><td>{sha_cell(c | {"remote": v["remote"]})}</td><td>{c["date"]}</td><td>+<span class="ok">{c["adds"]:,}</span>/−<span class="alert">{c["dels"]:,}</span></td><td>{"<span class=\"amber\">◆bot-id</span> " if c.get("bot") else ""}{esc(c["subject"])}</td></tr>'
        for c in v["commits"])
    sections.append(
        f'{head}\n\n<table class="specs">\n<tr><th>sha</th><th>date</th><th>diff</th><th>subject</th></tr>\n{rows}\n</table>')

top_days = sorted(by_day.items(), key=lambda kv: -kv[1])[:12]
day_rows = "\n".join(f"<tr><td>{d}</td><td>{n}</td><td>{bar(n/top_days[0][1],24)}</td></tr>" for d, n in sorted(top_days))

cb_rows = "\n".join(
    f"<tr><td>{esc(k)}</td><td>{v['codebuff']}</td></tr>"
    for k, v in sorted(repos.items(), key=lambda kv: -kv[1]["codebuff"])
    if v["codebuff"])

commit_body = f"""## THE SCOREBOARD

<table class="statrow">
<tr><td>{len(all_c)}</td><td>{len([v for v in repos.values() if v['commits']])}</td><td>+{adds:,}</td><td>−{dels:,}</td></tr>
<tr><td>devin commits</td><td>repos touched</td><td>lines added</td><td>lines deleted</td></tr>
</table>

Not the PR feed — the real ledger. Every commit in every local clone carrying a <span class="ok">Generated with Devin</span> trailer (or the <code>devin-ai-integration</code> co-author stamp), pulled straight out of the object stores under <code>~/dev</code>. PRs miss all of this because local sessions push under the operator's own name. The window: <span class="amber">{span0} → {span1}</span>.

## PACE — BUSIEST DAYS

<table class="specs">
<tr><th>date</th><th>commits</th><th>rate</th></tr>
{day_rows}
</table>

## THE OTHER HORSE IN THE BARN

The same object stores hold <span class="hl">{cb_total} Codebuff commits</span> — another agent, same workflow, overlapping repos. For the record, so nobody claims the ledger is curated:

<table class="specs">
<tr><th>repo</th><th>codebuff commits</th></tr>
{cb_rows}
</table>

{chr(10).join(sections)}

<p class="dim-note">Method: git log --all --grep for the trailer, unioned with --author/--committer=devin for trailerless bot-authored commits (marked <span class="amber">◆bot-id</span>), across every git clone in <code>~/dev</code>; deduped by sha; diffstats from --numstat. Commits link to GitHub only where a github.com remote exists — the rest are local-only. Excludes repos whose remote belongs to the operator's other account.</p>"""

commits_json = {
    "kicker": "transmission 12 · the commit log",
    "title": "ALL THE COMMITS",
    "lede": f"{len(all_c)} and counting. Every Devin-authored commit in every repo on this machine — the work that never showed up in a PR feed because it pushed straight under the operator's own login. Per repo, per day, per diff.",
    "body": commit_body,
}
json.dump(commits_json, open(f"{ROOT}/data/commits.json", "w"), indent=2)
print("ledger:", len(ledger_body), "chars ·", "commits:", len(commit_body), "chars ·", len(all_c), "commits ·", cb_total, "codebuff")
