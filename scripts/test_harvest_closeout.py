#!/usr/bin/env python3
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("closeout", Path(__file__).with_name("harvest_closeout.py"))
closeout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closeout)


def node(private=False):
    return {
        "repository": {"nameWithOwner": "example/private" if private else "example/public", "isPrivate": private},
        "id": "PR_fixture", "number": 1, "url": "https://github.com/example/public/pull/1",
        "title": "<script>alert(1)</script>", "body": "A description without a credit.",
        "createdAt": "2026-10-01T01:00:00Z", "mergedAt": "2026-11-04T02:00:00Z",
        "updatedAt": "2026-11-04T02:00:00Z", "additions": 2, "deletions": 1, "changedFiles": 1,
        "mergeCommit": {"oid": "a" * 40, "message": "Merge pull request"},
        "commits": {"totalCount": 1, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
            {"commit": {"oid": "b" * 40, "message": "Fix behavior\n\nGenerated with [Devin](https://devin.ai)"}}
        ]},
    }


class ReceiptTests(unittest.TestCase):
    def test_commit_credit_survives_missing_pr_body_credit(self):
        row = closeout.normalize_pr(node())
        self.assertEqual(closeout.agent_names(row), ["devin"])
        self.assertEqual(row["evidence"][0]["source"], "pr_commit")
        self.assertEqual(closeout.summary([row], [])["devin_commit_only_prs"], 1)
        serialized = json.dumps(row)
        self.assertNotIn("A description", serialized)
        self.assertNotIn("Fix behavior", serialized)

    def test_plain_markdown_and_bot_credits(self):
        for message in ("Generated with Devin", "Generated with [Devin](url)",
                        "Co-authored-by: devin-ai-integration[bot] <bot@example.com>"):
            with self.subTest(message=message):
                self.assertIn("devin", closeout.markers(message))
        self.assertNotIn("devin", closeout.markers("Fix the Devin integration"))
        self.assertEqual(closeout.markers("Co-authored-by: factory-droid[bot] <bot@example.com>"), ["factory"])

    def test_session_link_is_not_authorship_credit(self):
        item = node()
        item["body"] = "Reference https://app.devin.ai/sessions/abc"
        item["commits"]["nodes"][0]["commit"]["message"] = "No credit"
        row = closeout.normalize_pr(item)
        self.assertEqual(closeout.agent_names(row), ["devin-session-link"])
        self.assertEqual(closeout.summary([row], [])["devin_marker_prs"], 0)

    def test_mixed_agent_evidence_is_not_deduplicated_away(self):
        item = node()
        item["body"] = "Generated with Codebuff"
        row = closeout.normalize_pr(item)
        self.assertEqual(closeout.agent_names(row), ["codebuff", "devin"])

    def test_incomplete_commit_pages_fail(self):
        item = node()
        item["commits"]["totalCount"] = 101
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            closeout.normalize_pr(item)
        extra = [{"commit": {"oid": f"{n:040x}", "message": "Another commit"}} for n in range(100)]
        self.assertEqual(len(closeout.normalize_pr(item, extra)["commits"]), 101)

    def test_duplicate_commit_pages_fail(self):
        item = node()
        item["commits"]["totalCount"] = 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            closeout.normalize_pr(item, copy.deepcopy(item["commits"]["nodes"]))

    def test_unmerged_prs_fail(self):
        item = node()
        item["mergedAt"] = None
        with self.assertRaises(ValueError):
            closeout.normalize_pr(item)

    def test_trailerless_remote_bot_commit_is_preserved(self):
        item = node()
        item["commits"]["nodes"][0]["commit"].update(
            message="No credit", author={"user": {"login": "devin-ai-integration[bot]"}})
        row = closeout.normalize_pr(item)
        self.assertEqual(row["evidence"][0]["source"], "pr_commit_author")
        self.assertIn("devin", closeout.agent_names(row))

    def test_local_bot_identity_links_to_the_pr(self):
        row = closeout.normalize_pr(node())
        row["evidence"] = []
        local = [{"repository": row["repository"], "sha": row["commits"][0],
                  "evidence": [{"agent": "devin", "source": "git_author", "sha": row["commits"][0]}]}]
        closeout.link_local_evidence([row], local)
        closeout.link_local_evidence([row], local)
        self.assertEqual(len(row["evidence"]), 1)
        self.assertIn("devin", closeout.agent_names(row))

    def test_export_hashes_and_public_privacy_gate(self):
        metadata = {"captured_at": "2026-11-05T12:00:00Z", "since": "2026-10-01",
                    "until": "2026-11-05", "includes_private": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            closeout.write_exports(path, closeout.exports([closeout.normalize_pr(node())], [], metadata))
            closeout.verify(path)
            (path / "merged-prs.csv").write_text("modified")
            with self.assertRaisesRegex(ValueError, "checksum"):
                closeout.verify(path)
            with self.assertRaisesRegex(ValueError, "Private"):
                closeout.exports([closeout.normalize_pr(node(True))], [], metadata)

    def test_csv_formula_prefix(self):
        for value in ("=SUM(A1)", "+cmd", "-1", "@evil", "  =cmd"):
            self.assertTrue(closeout.spreadsheet_cell(value).startswith("'"))
        self.assertEqual(closeout.spreadsheet_cell("Normal title"), "Normal title")

    def test_csv_uses_git_safe_line_endings(self):
        metadata = {"captured_at": "2026-11-05T12:00:00Z", "since": "2026-10-01",
                    "until": "2026-11-05", "includes_private": False}
        content = closeout.exports([closeout.normalize_pr(node())], [], metadata)["merged-prs.csv"]
        self.assertNotIn(b"\r\n", content)
        self.assertTrue(content.endswith(b"\n"))

    def test_count_mismatch_fails(self):
        harvester = closeout.Harvester(Path("/unused"))
        start = dt.datetime(2026, 10, 1, tzinfo=closeout.UTC)
        with patch.object(harvester, "page", return_value={
            "count": 2, "rows": [{"url": "one"}], "page_info": {"hasNextPage": False}
        }):
            with self.assertRaisesRegex(RuntimeError, "every PR"):
                harvester.interval("user:example", start, start)

    def test_search_variable_does_not_overwrite_graphql_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            harvester = closeout.Harvester(Path(tmp))
            response = {"data": {"search": {
                "issueCount": 0, "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None}
            }}}
            with patch.object(closeout, "gh_json", return_value=response) as request:
                harvester.page("is:pr is:merged user:example")
            args = request.call_args.args[0]
            fields = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"]
            self.assertEqual(sum(field.startswith("query=") for field in fields), 1)
            self.assertIn("search=is:pr is:merged user:example", fields)

    def test_checkpoint_does_not_store_raw_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            harvester = closeout.Harvester(Path(tmp))
            response = {"search": {"issueCount": 1, "nodes": [node()],
                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}
            with patch.object(closeout, "graphql", return_value=response):
                harvester.page("is:pr")
            saved = next(Path(tmp).glob("*.json")).read_text()
            self.assertNotIn("A description without a credit", saved)
            self.assertNotIn("Fix behavior", saved)
            self.assertNotIn('"message"', saved)
            self.assertNotIn('"body"', saved)

    def test_pr_commit_pagination_preserves_the_last_page(self):
        item = node()
        item["commits"] = {
            "totalCount": 101, "pageInfo": {"hasNextPage": True, "endCursor": "next-commit"},
            "nodes": [{"commit": {"oid": f"{n:040x}", "message": "No marker"}} for n in range(100)],
        }
        responses = [
            {"search": {"issueCount": 1, "nodes": [item],
                        "pageInfo": {"hasNextPage": False, "endCursor": None}}},
            {"node": {"commits": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                  "nodes": [{"commit": {"oid": "f" * 40, "message": "Generated with Devin"}}]}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            harvester = closeout.Harvester(Path(tmp))
            with patch.object(closeout, "graphql", side_effect=responses):
                row = harvester.page("is:pr")["rows"][0]
            self.assertEqual(len(row["commits"]), 101)
            self.assertEqual(row["evidence"], [{"agent": "devin", "source": "pr_commit", "sha": "f" * 40}])

    def test_api_errors_cannot_be_treated_as_empty_results(self):
        result = subprocess.CompletedProcess([], 0, '{"errors":[{"message":"query failed"}]}', "")
        with patch.object(closeout.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "GraphQL errors"):
                closeout.gh_json(["api", "graphql"])

    def test_render_escapes_titles_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            source = root / "data/epilogue.json"
            source.write_text(json.dumps({"body": "## THE ARCHIVED SNAPSHOT\n\nPreserve this snapshot."}))
            public = root / "docs/assets/receipts"
            metadata = {"captured_at": "2026-11-05T12:00:00Z", "since": "2026-10-01",
                        "until": "2026-11-05", "includes_private": False, "user": "example"}
            closeout.write_exports(public, closeout.exports([closeout.normalize_pr(node())], [], metadata))
            with patch.object(closeout, "ROOT", root):
                closeout.render(public)
                first = source.read_bytes()
                closeout.render(public)
                self.assertEqual(source.read_bytes(), first)
            body = json.loads(first)["body"]
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
            self.assertNotIn("<script>", body)
            self.assertIn("Preserve this snapshot.", body)

    def test_search_pages_are_not_truncated(self):
        harvester = closeout.Harvester(Path("/unused"))
        start = dt.datetime(2026, 10, 1, tzinfo=closeout.UTC)
        pages = [
            {"count": 2, "rows": [{"url": "one"}], "page_info": {"hasNextPage": True, "endCursor": "next"}},
            {"count": 2, "rows": [{"url": "two"}], "page_info": {"hasNextPage": False}},
        ]
        with patch.object(harvester, "page", side_effect=pages):
            self.assertEqual(len(harvester.interval("user:example", start, start)), 2)

    def test_exact_search_cap_splits_without_overlap(self):
        harvester = closeout.Harvester(Path("/unused"))
        start = dt.datetime(2026, 10, 1, tzinfo=closeout.UTC)
        end = start + dt.timedelta(seconds=1)
        queries = []
        def page(query, cursor=None):
            queries.append(query)
            if len(queries) == 1:
                return {"count": 1000}
            return {"count": 1, "rows": [{"url": str(len(queries))}],
                    "page_info": {"hasNextPage": False}}
        with patch.object(harvester, "page", side_effect=page):
            self.assertEqual(len(harvester.interval("user:example", start, end)), 2)
        self.assertIn("00:00:00Z..2026-10-01T00:00:00Z", queries[1])
        self.assertIn("00:00:01Z..2026-10-01T00:00:01Z", queries[2])


if __name__ == "__main__":
    unittest.main()
