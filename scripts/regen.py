#!/usr/bin/env python3
"""Regenerate data/ledger.json and data/merges.json from live sources.

ledger: ~/.local/share/devin/cli/sessions.db (read-only) — assistant turns
deduped by session/message key, same as otter-stats' structural method.
merges: /tmp/merges.json harvested via `gh` (app-author + session-link union).
"""
import json, os, sqlite3, collections, html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.local/share/devin/cli/sessions.db")

def esc(s):
    return s.replace("|", "/").replace('"', "'")

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
        f"|%(cyan){name}%|{sess_per_model.get(m,0)}|{t:,}|{bignum(i)}|{bignum(o)}|{bignum(cr)}|{bignum(cc)}|*{bignum(tot)}*|{tot/grand*100:.1f}% {bar(tot/grand,10)}|")

day_rows = []
dmax = max(v[1] for v in days.values())
for d in sorted(days):
    t, tok = days[d]
    day_rows.append(f"|{d}|{t:,}|*{bignum(tok)}*|{bar(tok/dmax,30)}|")

proj_rows = []
for p, tok in proj_tok.most_common(15):
    proj_rows.append(f"|{esc(p)}|*{bignum(tok)}*|{tok/grand*100:.1f}%|")

first_day = sorted(days)[0]; last_day = sorted(days)[-1]
peak_day = max(days.items(), key=lambda kv: kv[1][1])

ledger_body = f"""h2. THE TOP LINE

table(statrow).
|{bignum(total_tok)}|{total_turns:,}|{n_sessions}|{len(days)}|
|tokens burned|assistant turns|sessions|days on record|

p. Nine days of local Devin CLI/Desktop telemetry — every assistant turn in every session, deduplicated by message key the same way "otter-stats":https://github.com/chris-wozniczek/otter-stats does it. The window is {first_day} to {last_day}; the peak was %(ok){peak_day[0]}% at *{bignum(peak_day[1][1])} tokens in a single day*.

h2. TOKENS BY MODEL

table(specs).
|_. model|_. sessions|_. turns|_. input|_. output|_. cache read|_. cache write|_. total|_. share|
{chr(10).join(model_rows)}

p(dim-note). %(dim)Honest footnote: cache-read dominates the ledger — it is context re-read from the provider's cache, not fresh reasoning. Of swe-2-high's {bignum(models['swe-2-high'][1]+models['swe-2-high'][2]+models['swe-2-high'][3])} tokens, {bignum(models['swe-2-high'][3])} are cache reads. Output tokens — the part that is actually authored — total {bignum(sum(v[2] for v in models.values()))} across all models.%

h2. BURN BY DAY

table(specs).
|_. date|_. turns|_. tokens|_. load|
{chr(10).join(day_rows)}

h2. WHERE IT WENT — TOP PROJECTS

table(specs).
|_. working directory|_. tokens|_. share|
{chr(10).join(proj_rows)}

h2. SESSION MODES

table(specs).
|_. mode|_. sessions|
|accept-edits|532|
|bypass|69|
|smart|58|
|(unset)|2|

bq. The operator ran %(amber){n_sessions} sessions% in nine days and let the agents keep the receipts. Most of them ran in %(cyan)accept-edits% mode — trust, but verify the diff.

p(dim-note). Source: ~/.local/share/devin/cli/sessions.db, opened read-only, same database otter-stats reads. Token counts are provider-reported per assistant turn; metrics-missing turns (rare) count as zero. Compiled {last_day}."""

ledger = {
    "kicker": "transmission 11 · the operator's ledger",
    "title": "THE BURN LEDGER",
    "lede": "Not Cognition's history — the operator's. This is the local accounting of one user's Devin usage: every assistant turn, every model, every project, pulled straight from the sessions database on this machine.",
    "body": ledger_body,
}
json.dump(ledger, open(f"{ROOT}/data/ledger.json", "w"), indent=2)

# ---------------- merges ----------------

merges = json.load(open("/tmp/merges.json"))
by_repo = collections.defaultdict(list)
for v in merges:
    by_repo[v["repo"]].append(v)
adds = sum(v["adds"] for v in merges)
dels = sum(v["dels"] for v in merges)
span0 = min(v["date"] for v in merges); span1 = max(v["date"] for v in merges)

sections = []
for repo in sorted(by_repo, key=lambda r: -len(by_repo[r])):
    prs = sorted(by_repo[repo], key=lambda v: int(v["url"].rsplit("/", 1)[1]))
    rows = "\n".join(
        f'|"#{int(v["url"].rsplit("/",1)[1])}":{v["url"]}|{v["date"]}|+%(ok){v["adds"]:,}%/−%(alert){v["dels"]:,}%|{esc(v["title"])}|'
        for v in prs)
    ra = sum(v["adds"] for v in prs); rd = sum(v["dels"] for v in prs)
    sections.append(
        f'h3. "{repo}":https://github.com/{repo} — {len(prs)} merges, +{ra:,}/−{rd:,}\n\n'
        f'table(specs).\n|_. pr|_. merged|_. diff|_. title|\n{rows}')

merge_body = f"""h2. THE SCOREBOARD

table(statrow).
|{len(merges)}|{len(by_repo)}|+{adds:,}|−{dels:,}|
|devin prs merged|repos touched|lines added|lines deleted|

p. Every merged pull request verifiably authored by %(ok)Devin% across the operator's GitHub — found two ways: the @app/devin-ai-integration@ author stamp (cloud sessions) and %(cyan)app.devin.ai% session links in the PR body. The window is %(amber){span0} → {span1}%.

p. One caveat the ledger keeps honest: %(hl)this is the floor, not the ceiling%. Sessions driven from the local CLI/Desktop push commits under the operator's own login — the %(amber)Generated with Devin% trailers are in the commit messages (dogbed's @fix/*@ and @docs/*@ waves among them), but GitHub's author feed can't see them. What follows is everything that is provable.

{chr(10).join(sections)}

p(dim-note). %(dim)Sources: GitHub search — author:app/devin-ai-integration + app.devin.ai session links, across the drawmeanelephant and chris-wozniczek accounts. Line counts are the PR diff totals at merge time.%"""

merges_json = {
    "kicker": "transmission 12 · the merge log",
    "title": "ALL THE MERGES",
    "lede": "The receipt drawer. Every pull request Devin wrote that made it into main — kernel toolchains, Windows ports, voice-control stacks, boxing games, and a Mac menu-bar app that counts the tokens this site is made of.",
    "body": merge_body,
}
json.dump(merges_json, open(f"{ROOT}/data/merges.json", "w"), indent=2)
print("ledger:", len(ledger_body), "chars ·", "merges:", len(merge_body), "chars ·", len(merges), "PRs")
