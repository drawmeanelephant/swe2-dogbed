# THE COGNITION ANNALS

A standalone showcase site for **dogbed** — the document compiler —
rendered as an eleven-page "phosphor CRT" museum of Cognition, Devin, and
the SWE model line (SWE-1 through SWE-2).

One `page.knap` template + eleven JSON files → eleven HTML documents.
One stylesheet. Zero JavaScript.

## Layout

```
bin/dogbed     vendored dogbed 0.1.0 binary (static, no deps)
src/page.knap  the single shared template (nav, statusbar, ticker, colophon)
data/*.json    one content file per page — Textile markup inside JSON strings
docs/          rendered output + style.css + assets
build.sh       renders every page (temp-file + rename, like upstream docs)
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

## Authoring notes (learned the hard way)

- `%(cls)span%` spans cannot contain a literal `%` or an unmatched `~`.
  Put the `%` outside the span, or use `*strong*`/`_em_` instead.
- `bc.`/`pre.` blocks cannot contain blank lines.
- Raw HTML in data is escaped — layout hooks come from block classes
  (`p(cls).`, `h2(cls).`, `table(cls).`) and `%(cls)%` spans only.
- Per-page extras (e.g. the active-nav highlight) go through `--head`
  flags in `build.sh`, which are spliced verbatim into `<head>`.
