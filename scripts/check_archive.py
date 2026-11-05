#!/usr/bin/env python3
"""Check rendered pages and the preserved merged-work receipts."""

import argparse
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from harvest_closeout import ROOT, verify


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.receipt_links = []
        self.detail_depth = 0
        self.ids = set()
        self.scripts = 0
        self.headings = []
        self.heading = None
        self.text = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.detail_depth += 1
        self.scripts += tag == "script"
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])
                if self.detail_depth:
                    self.receipt_links.append(attrs[key])
        if tag in ("h1", "h2", "h3"):
            self.heading = [tag, []]

    def handle_endtag(self, tag):
        if tag == "details":
            self.detail_depth -= 1
        if self.heading and tag == self.heading[0]:
            self.headings.append((tag, "".join(self.heading[1])))
            self.heading = None

    def handle_data(self, text):
        self.text.append(text)
        if self.heading:
            self.heading[1].append(text)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path)
    args = parser.parse_args()
    docs = ROOT / "docs"
    archive = docs / "assets/closeout-2026-11-05"
    manifest, prs, local = verify(archive)
    sources = sorted((ROOT / "data").glob("*.json"))
    pages = {path.resolve(): Page(path.read_text()) for path in docs.glob("*.html")}
    require(len(sources) == len(pages) == 15, "Expected fifteen sources and rendered pages")
    local_links = 0
    for source in sources:
        data = json.loads(source.read_text())
        target = (docs / f"{source.stem}.html").resolve()
        require(target in pages, f"Missing rendered page: {source.name}")
        page = pages[target]
        require([value for tag, value in page.headings if tag == "h1"] == [data["title"]],
                f"Wrong title: {source.name}")
        require(not page.scripts, f"Script tag in {source.name}")
        for link in page.links:
            url = urlsplit(link)
            require(url.scheme.lower() != "javascript", f"JavaScript URL in {source.name}")
            if url.scheme or url.netloc:
                continue
            path = (target.parent / unquote(url.path)).resolve() if url.path else target
            require(path.exists(), f"Missing local link: {source.name}: {link}")
            if url.fragment and path in pages:
                require(unquote(url.fragment) in pages[path].ids,
                        f"Missing fragment: {source.name}: {link}")
            local_links += 1
    epilogue = pages[(docs / "epilogue.html").resolve()]
    require(("h2", "WHAT ACTUALLY MERGED") in epilogue.headings, "Merged-work section did not render")
    require(("h3", "THE RECORDS THAT SURVIVE UNINSTALL") in epilogue.headings, "Export section did not render")
    for row in prs:
        require(epilogue.receipt_links.count(row["url"]) == 1, "PR receipt missing or duplicated in rendered inventory")
        require(f'https://github.com/{row["repository"]}/commit/{row["merge_commit"]}' in epilogue.receipt_links,
                "Merge commit link missing in rendered inventory")
        require("body" not in row and "message" not in row, "Raw PR content in receipt")
    with (archive / "merged-prs.csv").open(newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    require(len(csv_rows) == len(prs), "CSV lost PR rows")
    require({row["url"] for row in csv_rows} == {row["url"] for row in prs}, "CSV receipt set differs")
    if args.private_dir:
        _, all_prs, all_local = verify(args.private_dir)
        require(args.private_dir.stat().st_mode & 0o077 == 0, "Private directory permissions are too broad")
        for name in ("manifest.json", *json.loads((args.private_dir / "manifest.json").read_text())["files"]):
            require((args.private_dir / name).stat().st_mode & 0o077 == 0, "Private file permissions are too broad")
        require(prs == [row for row in all_prs if not row["private"]], "Public PR export differs from private master")
        require(local == [row for row in all_local if not row["private"]], "Public commit export differs from private master")
        section = json.loads((ROOT / "data/epilogue.json").read_text())["body"].split(
            "<!-- merged-work:start -->", 1)[1].split("<!-- merged-work:end -->", 1)[0]
        public_text = "\n".join(path.read_text() for path in archive.iterdir() if path.is_file()) + section
        for repo in {row["repository"] for row in all_prs + all_local if row["private"]}:
            require(repo not in public_text, "Private repository identifier in new public archive")
        site_manifest = args.private_dir / "site-manifest.json"
        if site_manifest.exists():
            site = (args.private_dir / "site").resolve()
            snapshot = json.loads(site_manifest.read_text())
            for name, checksum in snapshot["files"].items():
                path = (site / name).resolve()
                require(path.is_relative_to(site), "Offline snapshot path escapes its directory")
                require(hashlib.sha256(path.read_bytes()).hexdigest() == checksum, "Offline site copy failed its checksum")
            require(len(list(site.glob("*.html"))) == 15, "Offline site copy is incomplete")
            print("PASS: complete offline site copy and all copied-file checksums.")
        print("PASS: private backup permissions, public/private separation, and master receipt checksums.")
    print(f"PASS: 15 rendered pages, {local_links} local links, zero script tags.")
    print(f"PASS: every one of {len(prs):,} public merged PRs renders once, with a merge-commit link.")
    print("PASS: JSON, CSV, source totals, and SHA-256 checksums agree.")
    print("Public capture:", manifest["captured_at"])


if __name__ == "__main__":
    main()
