# THE COGNITION ANNALS

A standalone showcase site for **dogbed** — the document compiler —
rendered as a fifteen-page "phosphor CRT" museum of Cognition, Devin, and
the SWE model line (SWE-1 through SWE-2).

One `page.knap` template + fifteen JSON files → fifteen HTML documents.
One stylesheet. Zero JavaScript.

## Layout

```
bin/dogbed     vendored dogbed binary (static, no deps) — main @ d96b125
               (k4o aeba163 + oliver 1.3, oliver-format Markdown):
               knap → k4o → Markdown → oliver (Obsidian ext set) → HTML
src/page.knap  the single shared template (nav, statusbar, ticker, colophon)
data/*.json    one content file per page — Markdown + raw-HTML in JSON strings
docs/          rendered output + style.css + assets
build.sh       renders every page (temp-file + rename, like upstream docs)
scripts/regen.py      rebuilds data/ledger.json + commits.json from live sources
scripts/migrate_textile.py  the one-shot Textile→Markdown converter used for
                      the dogbed k4o/oliver bump (kept for reference)
```

## Build

```
./build.sh                          # uses ./bin/dogbed
DOGBED=/path/to/dogbed ./build.sh   # or your own binary
```

Then open `docs/index.html`, or serve it:

```
cd docs && python3 -m http.server 8471
```

## Pages

| page | contents |
|---|---|
| `index` | landing, ASCII hero, directory, stats |
| `cognition` | founders, funding ledger, company shape |
| `devin` | the agent — demo, GA, pricing collapse, surfaces |
| `windsurf` | the July 2025 weekend acquisition |
| `products` | DeepWiki, Kevin-32B, swe-grep, swe-check, API |
| `swe-1` | the first family: SWE-1 / -lite / -mini |
| `swe-15` | the speed era, 950 tok/s, Falcon Alpha |
| `swe-16` | model-UX era, mid-run preview |
| `swe-17` | harness-native RL inside Devin |
| `swe-2` | flagship — Kimi K3 base, effort levels |
| `timeline` | the full 2023→2026 chronology |
| `ledger` | the operator's burn ledger — tokens by model/day/project |
| `commits` | every Devin-authored commit across local clones |
| `debacle` | the wrong-model incident — a swe-2-high review served by terra |
| `epilogue` | the sign-off — where the museum turns its lights off |

## Authoring notes (learned the hard way)

- dogbed's IR is now **Markdown, not Textile** (k4o fidelity rewrite +
  oliver 1.3). Body content is CommonMark-ish; `*item` bullets, `1.`
  numbered items, `>` quotes, `##`/`###` headings work as usual.
- Raw HTML in data now passes straight through — that is the layout
  hook. Classes come from real tags: `<p class="lede">`,
  `<table class="specs">`, `<pre class="ascii"><code>`,
  `<span class="ok">`. Escape `&`, `<`, `>` in prose yourself.
- HTML blocks run to the next blank line — nothing between
  `</table>` and the following block or it gets swallowed.
- No textile-style constructs survive: `p(cls).`, `%(cls)%`,
  `"text":url`, `table(cls).`, `bc.` are all literal text now.
- Per-page extras (e.g. the active-nav highlight) go through `--head`
  flags in `build.sh`, which are spliced verbatim into `<head>`.

## The operator pages

`ledger` reads `~/.local/share/devin/cli/sessions.db` read-only — same DB
`otter-stats` uses — and `commits` is regenerated from
`scripts/harvest_commits.py` (git-log trailer + author/committer union
across every clone in `~/dev`). `python3 scripts/regen.py` refreshes both
data files; `commits` expects the harvested list at
`/tmp/commits_dev.json`.

## Final merged-work archive

The November 5 close-out in `epilogue` has a separate GitHub inventory.
It searches merged PRs without requiring a Devin credit in the PR body,
then checks PR commits, merge commits, and local Git history for agent
credits. The window is October 1 through November 5, 2026, by **merge date**,
not session date or PR creation date.

The account scope is repositories owned by `drawmeanelephant`, plus PRs
authored by that account elsewhere. A marker records provenance evidence,
not sole authorship or correctness. Direct pushes are not counted as PRs.

Public receipts live in `docs/assets/closeout-2026-11-05/`:

- `merged-prs.md`: the readable inventory.
- `merged-prs.csv`: spreadsheet export.
- `merged-prs.json`: titles, URLs, merge dates, merge SHAs, constituent
  commit SHAs, diff counts, and agent evidence.
- `local-commits.json`: Devin-marked local commits and default-branch
  reachability, without fetching refs.
- `manifest.json`: capture scope, totals, and SHA-256 checksums.
- `README.md`: what the export preserves and what it does not.

The hand-selected examples in `data/closeout-highlights.md` summarize PR
titles. The generator keeps the complete receipt inventory below them.

Private-repository receipts stay outside this repository, in
`~/Documents/devin-closeout-2026-11-05/`. That directory also preserves the
original report's JSON files. It has mode `0700`; receipt files have mode
`0600`. Neither location is inside Devin's application data.

The completed capture also has an offline copy of the rendered site in
`site/` inside the private backup. `site-manifest.json` records a checksum
for every copied file. That copy is a finalization step, not part of running
the harvester.

These exports **do not back up chat transcripts, uncommitted work, or Git
objects**. Keep the private backup and this repository when uninstalling
Devin. If you need transcripts too, preserve the application data separately
before deleting it.

To harvest, use authenticated, read-only GitHub access through `gh`:

```
python3 -B scripts/harvest_closeout.py
./build.sh
```

The harvester checkpoints sanitized pages in the private backup so an
interrupted capture can resume. It rejects truncated searches, missing
commit pages, and counts that disagree with REST search. Use a new
`--private-dir` for a fresh capture rather than reusing an old page cache.
No PR descriptions, full commit messages, or email addresses are exported.

To rebuild the end cap from the preserved receipts without GitHub:

```
python3 -B scripts/harvest_closeout.py --render-only
./build.sh
```

To check the receipts, rendered pages, and local links:

```
python3 -B -m unittest discover -s scripts -p 'test_harvest_closeout.py' -v
python3 -B scripts/check_archive.py
python3 -B scripts/check_archive.py --private-dir "$HOME/Documents/devin-closeout-2026-11-05"
```
