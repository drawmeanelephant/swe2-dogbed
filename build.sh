#!/bin/sh
# cognition-archive — build the site with dogbed.
# Usage: ./build.sh            (uses ./bin/dogbed)
#        DOGBED=/path/dogbed ./build.sh
set -eu
cd "$(dirname "$0")"
: "${DOGBED:=bin/dogbed}"

mkdir -p docs

tmp=""
cleanup() { if [ -n "$tmp" ]; then rm -f "$tmp"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

render() { # page "title"
    page="$1"; title="$2"
    tmp="docs/.$page.html.$$"
    "$DOGBED" render "src/page.knap" \
        -d "data/$page.json" \
        --title "$title" --css style.css --lang en \
        --head '<link rel="icon" type="image/svg+xml" href="assets/favicon.svg">' \
        --head '<meta property="og:title" content="The Cognition Annals">' \
        --head '<meta property="og:description" content="A dogbed-compiled field archive of Cognition, Devin, and the SWE model line.">' \
        --head '<meta property="og:image" content="https://drawmeanelephant.github.io/swe2-dogbed/assets/burn-ledger-october-2026.png">' \
        --head "<style>.nav a[href=\"$page.html\"]{color:#02120a;background:#ffb347;border-color:#ffb347;box-shadow:0 0 18px rgba(255,179,71,.45)}</style>" \
        > "$tmp"
    mv -f "$tmp" "docs/$page.html"
    tmp=""
    echo "  rendered $page.html"
}

render index     "ANNALS // cognition — the archive"
render cognition "ANNALS // the company"
render devin     "ANNALS // devin, the ai software engineer"
render windsurf  "ANNALS // the windsurf gambit"
render products  "ANNALS // museum of products"
render swe-1     "ANNALS // swe-1 — the first family"
render swe-15    "ANNALS // swe-1.5 — the speed era"
render swe-16    "ANNALS // swe-1.6 — model ux"
render swe-17    "ANNALS // swe-1.7 — harness-native"
render swe-2     "ANNALS // swe-2 — the flagship"
render timeline  "ANNALS // chronology 2023→now"
render ledger    "ANNALS // the burn ledger"
render commits   "ANNALS // all the commits"

echo "done — open docs/index.html"
