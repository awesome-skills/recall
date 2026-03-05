#!/usr/bin/env python3
"""Regression tests for recall.py.

Covers: query sanitization, project matching, CJK fallback, orphan cleanup,
subagent filtering, slug deduplication, directory checkpointing, noise filtering.
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path so we can import recall
SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
import sys

sys.path.insert(0, SCRIPTS_DIR)

import recall
from recall_common import extract_text, is_noise


class DBTestCase(unittest.TestCase):
    """Base class that sets up an in-memory DB with the recall schema."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        recall.create_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert_session(self, session_id, source="claude", project="/test", slug="test-slug",
                        timestamp=None, summary="", is_subagent=0, parent_session_id="",
                        file_path=""):
        ts = timestamp or int(time.time() * 1000)
        self.conn.execute(
            "INSERT INTO sessions (session_id, source, file_path, project, slug, timestamp, mtime, summary, is_subagent, parent_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, source, file_path, project, slug, ts, time.time(), summary, is_subagent, parent_session_id),
        )

    def _insert_messages(self, session_id, messages):
        self.conn.executemany(
            "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
            [(session_id, role, text) for role, text in messages],
        )


# ── Query sanitization ────────────────────────────────────────────────────────

class TestSanitizeFtsQuery(unittest.TestCase):

    def test_plain_words_unchanged(self):
        self.assertEqual(recall.sanitize_fts_query("hello world"), "hello world")

    def test_dashes_auto_quoted(self):
        result = recall.sanitize_fts_query("local-command-caveat")
        self.assertEqual(result, '"local-command-caveat"')

    def test_explicit_fts_syntax_preserved(self):
        q = '"exact phrase" AND term'
        self.assertEqual(recall.sanitize_fts_query(q), q)

    def test_prefix_syntax_preserved(self):
        q = "buffer*"
        self.assertEqual(recall.sanitize_fts_query(q), q)

    def test_empty_returns_empty(self):
        self.assertEqual(recall.sanitize_fts_query(""), "")
        self.assertIsNone(recall.sanitize_fts_query(None))

    def test_mixed_tokens(self):
        result = recall.sanitize_fts_query("hello my-var world")
        self.assertEqual(result, 'hello "my-var" world')


# ── Project matching ──────────────────────────────────────────────────────────

class TestProjectMatchClause(unittest.TestCase):

    def test_exact_match(self):
        clause, params = recall.project_match_clause("/Users/admin/work", "s")
        self.assertIn("s.project = ?", clause)
        self.assertEqual(params[0], "/Users/admin/work")

    def test_child_path_match(self):
        clause, params = recall.project_match_clause("/Users/admin/work", "s")
        self.assertIn("LIKE", clause)
        # Second param should be the prefix pattern
        self.assertTrue(params[1].endswith("/%"))

    def test_trailing_slash_stripped(self):
        clause, params = recall.project_match_clause("/Users/admin/work/", "s")
        self.assertEqual(params[0], "/Users/admin/work")

    def test_no_sibling_match(self):
        """Ensure /Users/admin/work does NOT match /Users/admin/work2."""
        clause, params = recall.project_match_clause("/Users/admin/work", "s")
        # The LIKE pattern should be /Users/admin/work/% (with trailing slash)
        self.assertTrue(params[1].startswith("/Users/admin/work/"))


class TestProjectMatchIntegration(DBTestCase):

    def test_exact_project_found(self):
        self._insert_session("s1", project="/Users/admin/work")
        self._insert_messages("s1", [("user", "hello")])

        results = recall.list_sessions(self.conn, project="/Users/admin/work", include_subagents=True)
        self.assertEqual(len(results), 1)

    def test_child_project_found(self):
        self._insert_session("s1", project="/Users/admin/work/subdir")
        self._insert_messages("s1", [("user", "hello")])

        results = recall.list_sessions(self.conn, project="/Users/admin/work", include_subagents=True)
        self.assertEqual(len(results), 1)

    def test_sibling_project_excluded(self):
        self._insert_session("s1", project="/Users/admin/work2")
        self._insert_messages("s1", [("user", "hello")])

        results = recall.list_sessions(self.conn, project="/Users/admin/work", include_subagents=True)
        self.assertEqual(len(results), 0)


# ── Infer project from path ──────────────────────────────────────────────────

class TestInferProjectFromPath(unittest.TestCase):

    def test_standard_path(self):
        path = "/Users/test/.claude/projects/-Users-test-myproject/session.jsonl"
        self.assertEqual(recall.infer_project_from_path(path), "/Users/test/myproject")

    def test_nested_path(self):
        path = "/Users/admin/.claude/projects/-Users-admin-work/session.jsonl"
        self.assertEqual(recall.infer_project_from_path(path), "/Users/admin/work")

    def test_no_match(self):
        self.assertEqual(recall.infer_project_from_path("/tmp/session.jsonl"), "")

    def test_none_input(self):
        self.assertEqual(recall.infer_project_from_path(None), "")


class TestNormalizeProjectPath(unittest.TestCase):

    def test_trailing_slash_removed(self):
        self.assertEqual(
            recall.normalize_project_path("/Users/admin/work/"),
            "/Users/admin/work",
        )

    def test_symlink_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            link_dir = Path(tmpdir) / "link"
            real_dir.mkdir()
            os.symlink(real_dir, link_dir)
            self.assertEqual(
                recall.normalize_project_path(str(link_dir)),
                os.path.realpath(str(real_dir)),
            )


class TestResumeCommand(unittest.TestCase):

    def test_claude_resume_command(self):
        cmd = recall.build_resume_command("claude", "/tmp/my project", "abc-123")
        self.assertEqual(cmd, "cd '/tmp/my project' && claude --resume abc-123")

    def test_codex_resume_command(self):
        cmd = recall.build_resume_command("codex", "/tmp/work", "sid")
        self.assertEqual(cmd, "cd /tmp/work && codex resume sid")

    def test_unknown_source(self):
        self.assertEqual(recall.build_resume_command("unknown", "/tmp", "sid"), "")


class TestResultSerialization(unittest.TestCase):

    def test_summary_truncation_and_resume_command(self):
        row = (
            "sid-1234",
            "codex",
            "/tmp/file.jsonl",
            "/tmp/work",
            "slug",
            1709510400000,
            "",
            0.0,
            "a" * 40,
        )
        result = recall.result_to_dict(row, summary_len=10, include_summary=True)
        self.assertEqual(result["summary"], "aaaaaaa...")
        self.assertEqual(result["resume_command"], "cd /tmp/work && codex resume sid-1234")

    def test_summary_disabled(self):
        row = (
            "sid-1",
            "claude",
            "/tmp/file.jsonl",
            "/tmp/work",
            "slug",
            1709510400000,
            "",
            0.0,
            "hello world",
        )
        result = recall.result_to_dict(row, summary_len=20, include_summary=False)
        self.assertEqual(result["summary"], "")


# ── CJK support ──────────────────────────────────────────────────────────────

class TestCJKHelpers(unittest.TestCase):

    def test_contains_cjk_chinese(self):
        self.assertTrue(recall.contains_cjk("测试"))

    def test_contains_cjk_english(self):
        self.assertFalse(recall.contains_cjk("test"))

    def test_extract_cjk_terms(self):
        terms = recall.extract_cjk_terms("hello 你好 world 世界")
        self.assertEqual(terms, ["你好", "世界"])

    def test_extract_cjk_dedup(self):
        terms = recall.extract_cjk_terms("你好 test 你好")
        self.assertEqual(terms, ["你好"])


class TestCJKFallbackSearch(DBTestCase):

    def test_cjk_substring_match(self):
        self._insert_session("s1", project="/test")
        self._insert_messages("s1", [("user", "讨论WebSocket重连策略")])

        results = recall.search_cjk_fallback(self.conn, "重连", include_subagents=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "s1")

    def test_cjk_no_match(self):
        self._insert_session("s1", project="/test")
        self._insert_messages("s1", [("user", "讨论WebSocket")])

        results = recall.search_cjk_fallback(self.conn, "数据库", include_subagents=True)
        self.assertEqual(len(results), 0)


# ── LIKE fallback ─────────────────────────────────────────────────────────────

class TestLikeFallback(DBTestCase):

    def test_special_char_query(self):
        self._insert_session("s1", project="/test")
        self._insert_messages("s1", [("user", "check local-command-caveat handling")])

        results = recall.search_like_fallback(
            self.conn, "local-command-caveat", include_subagents=True
        )
        self.assertEqual(len(results), 1)

    def test_percent_in_query_escaped(self):
        self._insert_session("s1", project="/test")
        self._insert_messages("s1", [("user", "100% done")])

        results = recall.search_like_fallback(self.conn, "100%", include_subagents=True)
        self.assertEqual(len(results), 1)


# ── Escape helpers ────────────────────────────────────────────────────────────

class TestEscapeLike(unittest.TestCase):

    def test_percent_escaped(self):
        self.assertIn("\\%", recall.escape_like("100%"))

    def test_underscore_escaped(self):
        self.assertIn("\\_", recall.escape_like("my_var"))

    def test_plain_unchanged(self):
        self.assertEqual(recall.escape_like("hello"), "hello")


# ── Subagent filtering ────────────────────────────────────────────────────────

class TestSubagentFiltering(DBTestCase):

    def test_subagents_hidden_by_default(self):
        self._insert_session("parent1", project="/test", is_subagent=0)
        self._insert_messages("parent1", [("user", "hello")])
        self._insert_session("sub1", project="/test", is_subagent=1, parent_session_id="parent1")
        self._insert_messages("sub1", [("user", "subtask")])

        results = recall.list_sessions(self.conn, include_subagents=False)
        session_ids = [r[0] for r in results]
        self.assertIn("parent1", session_ids)
        self.assertNotIn("sub1", session_ids)

    def test_subagents_shown_with_flag(self):
        self._insert_session("parent1", project="/test", is_subagent=0)
        self._insert_messages("parent1", [("user", "hello")])
        self._insert_session("sub1", project="/test", is_subagent=1, parent_session_id="parent1")
        self._insert_messages("sub1", [("user", "subtask")])

        results = recall.list_sessions(self.conn, include_subagents=True)
        session_ids = [r[0] for r in results]
        self.assertIn("parent1", session_ids)
        self.assertIn("sub1", session_ids)


class TestSubagentDetection(unittest.TestCase):

    def test_subagent_path(self):
        path = "/Users/test/.claude/projects/-Users-test-work/abc123/subagents/agent-def456.jsonl"
        self.assertEqual(recall.subagent_parent_session_id(path), "abc123")

    def test_normal_path(self):
        path = "/Users/test/.claude/projects/-Users-test-work/session.jsonl"
        self.assertIsNone(recall.subagent_parent_session_id(path))


# ── Orphan cleanup ────────────────────────────────────────────────────────────

class TestOrphanCleanup(DBTestCase):

    def test_removes_orphaned_sessions(self):
        # Insert a session pointing to a non-existent file
        self._insert_session("orphan1", file_path="/nonexistent/path.jsonl")
        self._insert_messages("orphan1", [("user", "old")])

        count = recall.prune_orphan_sessions(self.conn)
        self.assertEqual(count, 1)

        remaining = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_keeps_existing_files(self):
        # Use a file that exists
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            existing_path = f.name
            f.write(b'{"type":"user"}\n')

        try:
            self._insert_session("real1", file_path=existing_path)
            self._insert_messages("real1", [("user", "current")])

            count = recall.prune_orphan_sessions(self.conn)
            self.assertEqual(count, 0)

            remaining = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self.assertEqual(remaining, 1)
        finally:
            os.unlink(existing_path)


# ── Slug deduplication ────────────────────────────────────────────────────────

class TestSlugDeduplication(unittest.TestCase):

    def test_unique_slugs_unchanged(self):
        results = [
            ("sess-aaa11111", "claude", "", "/test", "slug-a", 1000, "", 0.0, ""),
            ("sess-bbb22222", "claude", "", "/test", "slug-b", 1000, "", 0.0, ""),
        ]
        slugs = recall.deduplicate_slugs(results)
        self.assertEqual(slugs["sess-aaa11111"], "slug-a")
        self.assertEqual(slugs["sess-bbb22222"], "slug-b")

    def test_duplicate_slugs_get_suffix(self):
        results = [
            ("sess-aaa11111", "claude", "", "/test", "same-slug", 1000, "", 0.0, ""),
            ("sess-bbb22222", "claude", "", "/test", "same-slug", 1000, "", 0.0, ""),
        ]
        slugs = recall.deduplicate_slugs(results)
        self.assertIn("aaa11111", slugs["sess-aaa11111"])
        self.assertIn("bbb22222", slugs["sess-bbb22222"])
        self.assertNotEqual(slugs["sess-aaa11111"], slugs["sess-bbb22222"])

    def test_mixed_unique_and_duplicate(self):
        results = [
            ("sess-aaa11111", "claude", "", "/test", "dup", 1000, "", 0.0, ""),
            ("sess-bbb22222", "claude", "", "/test", "dup", 1000, "", 0.0, ""),
            ("sess-ccc33333", "claude", "", "/test", "unique", 1000, "", 0.0, ""),
        ]
        slugs = recall.deduplicate_slugs(results)
        self.assertEqual(slugs["sess-ccc33333"], "unique")
        self.assertIn("aaa11111", slugs["sess-aaa11111"])


# ── Noise filtering ──────────────────────────────────────────────────────────

class TestNoiseFiltering(unittest.TestCase):

    def test_system_reminder_is_noise(self):
        self.assertTrue(is_noise("<system-reminder>Some content here"))

    def test_local_command_caveat_is_noise(self):
        self.assertTrue(is_noise("<local-command-caveat>"))

    def test_user_instructions_is_noise(self):
        self.assertTrue(is_noise("<user_instructions>..."))

    def test_normal_text_not_noise(self):
        self.assertFalse(is_noise("Help me fix this bug"))

    def test_empty_is_noise(self):
        self.assertTrue(is_noise(""))
        self.assertTrue(is_noise(None))

    def test_leading_whitespace_handled(self):
        self.assertTrue(is_noise("  <system-reminder>content"))


# ── extract_text ──────────────────────────────────────────────────────────────

class TestExtractText(unittest.TestCase):

    def test_string_passthrough(self):
        self.assertEqual(extract_text("hello"), "hello")

    def test_text_block_list(self):
        content = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
        self.assertEqual(extract_text(content), "hello\nworld")

    def test_skips_non_text_blocks(self):
        content = [{"type": "tool_use", "name": "bash"}, {"type": "text", "text": "result"}]
        self.assertEqual(extract_text(content), "result")

    def test_empty_list(self):
        self.assertEqual(extract_text([]), "")

    def test_none(self):
        self.assertEqual(extract_text(None), "")


# ── Timestamp formatting ─────────────────────────────────────────────────────

class TestFormatTimestamp(unittest.TestCase):

    def test_date_only(self):
        ts_ms = 1709510400000  # 2024-03-04 approx
        result = recall.format_timestamp(ts_ms)
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")
        self.assertNotIn(":", result)

    def test_precise_includes_time(self):
        ts_ms = 1709510400000
        result = recall.format_timestamp(ts_ms, precise=True)
        self.assertIn(":", result)

    def test_zero_returns_unknown(self):
        self.assertEqual(recall.format_timestamp(0), "unknown")
        self.assertEqual(recall.format_timestamp(None), "unknown")


# ── make_excerpt ──────────────────────────────────────────────────────────────

class TestMakeExcerpt(unittest.TestCase):

    def test_short_text_unchanged(self):
        self.assertEqual(recall.make_excerpt("hello world"), "hello world")

    def test_long_text_truncated(self):
        text = "a" * 300
        result = recall.make_excerpt(text)
        self.assertTrue(result.endswith("..."))
        self.assertLessEqual(len(result), 210)

    def test_needle_centering(self):
        text = "x" * 200 + "NEEDLE" + "y" * 200
        result = recall.make_excerpt(text, "NEEDLE")
        self.assertIn("NEEDLE", result)


# ── Directory checkpoint ──────────────────────────────────────────────────────

class TestDirCheckpoint(DBTestCase):

    def test_checkpoint_table_created(self):
        # Verify the table exists
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dir_checkpoints'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_checkpoint_skips_unchanged_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a session file
            session_file = os.path.join(tmpdir, "test.jsonl")
            with open(session_file, "w") as f:
                f.write('{"type":"user","role":"user","message":{"content":"hello"}}\n')

            # First collection should find the file
            files1 = recall._collect_files_with_dir_checkpoint(
                self.conn, Path(tmpdir), "claude", force=False
            )
            self.assertEqual(len(files1), 1)

            # Second collection should skip (dir mtime unchanged)
            files2 = recall._collect_files_with_dir_checkpoint(
                self.conn, Path(tmpdir), "claude", force=False
            )
            self.assertEqual(len(files2), 0)

    def test_force_ignores_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_file = os.path.join(tmpdir, "test.jsonl")
            with open(session_file, "w") as f:
                f.write('{"type":"user","role":"user","message":{"content":"hello"}}\n')

            # First run
            recall._collect_files_with_dir_checkpoint(
                self.conn, Path(tmpdir), "claude", force=False
            )

            # Force should find the file again
            files = recall._collect_files_with_dir_checkpoint(
                self.conn, Path(tmpdir), "claude", force=True
            )
            self.assertEqual(len(files), 1)

    def test_nonexistent_dir(self):
        files = recall._collect_files_with_dir_checkpoint(
            self.conn, Path("/nonexistent/dir"), "claude", force=False
        )
        self.assertEqual(len(files), 0)


# ── Session parsing ───────────────────────────────────────────────────────────

class TestClaudeSessionParser(unittest.TestCase):

    def test_basic_parsing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            entries = [
                {"type": "user", "role": "user", "message": {"content": "hello world"}, "timestamp": "2026-03-04T10:00:00Z"},
                {"type": "assistant", "role": "assistant", "message": {"content": "hi there"}, "timestamp": "2026-03-04T10:00:01Z"},
            ]
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            path = f.name

        try:
            result = recall.parse_claude_session(path)
            self.assertIsNotNone(result)
            metadata, messages = result
            self.assertEqual(len(messages), 2)
            self.assertEqual(metadata["summary"], "hello world")
        finally:
            os.unlink(path)

    def test_noise_filtered(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            entries = [
                {"type": "user", "role": "user", "message": {"content": "<system-reminder>noise</system-reminder>"}},
                {"type": "user", "role": "user", "message": {"content": "real question"}},
            ]
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            path = f.name

        try:
            result = recall.parse_claude_session(path)
            metadata, messages = result
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0][1], "real question")
            self.assertEqual(metadata["summary"], "real question")
        finally:
            os.unlink(path)


# ── Concurrent safety ─────────────────────────────────────────────────────────

class TestConcurrentSafety(unittest.TestCase):

    def test_begin_immediate_used(self):
        """Verify that the main function pattern uses BEGIN IMMEDIATE for writes."""
        import inspect
        source = inspect.getsource(recall.main)
        self.assertIn("BEGIN IMMEDIATE", source)


# ── ISO timestamp parsing ────────────────────────────────────────────────────

class TestParseIsoTimestamp(unittest.TestCase):

    def test_z_suffix(self):
        ts = recall.parse_iso_timestamp("2026-03-04T10:00:00.000Z")
        self.assertIsNotNone(ts)
        self.assertIsInstance(ts, int)

    def test_numeric_passthrough(self):
        self.assertEqual(recall.parse_iso_timestamp(1234567890000), 1234567890000)

    def test_none_returns_none(self):
        self.assertIsNone(recall.parse_iso_timestamp(None))

    def test_invalid_returns_none(self):
        self.assertIsNone(recall.parse_iso_timestamp("not a date"))


# ── Schema migration ─────────────────────────────────────────────────────────

class TestSchemaMigration(unittest.TestCase):

    def test_migrate_adds_missing_columns(self):
        """Simulate an old schema without summary/subagent columns."""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                source TEXT,
                file_path TEXT,
                project TEXT,
                slug TEXT,
                timestamp INTEGER,
                mtime REAL
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE messages USING fts5(
                session_id UNINDEXED, role, text, tokenize='porter unicode61'
            )
        """)

        # Should not raise
        recall.migrate_schema(conn)

        # Verify new columns exist
        conn.execute("SELECT summary, is_subagent, parent_session_id FROM sessions LIMIT 1")
        conn.close()


class TestVersionHelpers(unittest.TestCase):

    def test_build_version_payload_without_db(self):
        with patch.object(recall, "DB_PATH", Path("/tmp/nonexistent-recall-db.sqlite")), \
             patch.object(recall, "detect_commit_sha", return_value="abc1234"):
            payload = recall.build_version_payload()
        self.assertEqual(payload["name"], "recall")
        self.assertEqual(payload["owner"], "awesome-skills")
        self.assertEqual(payload["version"], recall.SKILL_VERSION)
        self.assertEqual(payload["schema_version"], recall.SCHEMA_VERSION)
        self.assertEqual(payload["commit"], "abc1234")
        self.assertFalse(payload["db_exists"])
        self.assertIsNone(payload["db_schema_version"])

    def test_read_db_schema_version(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            conn.close()
            self.assertEqual(recall.read_db_schema_version(db_path), 7)
        finally:
            if db_path.exists():
                db_path.unlink()


class TestDoctorPayload(DBTestCase):

    def test_doctor_payload_contains_expected_fields(self):
        self._insert_session("s1", source="codex", summary="hello")
        self._insert_messages("s1", [("user", "hello")])
        payload = recall.build_doctor_payload(self.conn)
        self.assertEqual(payload["name"], "recall")
        self.assertIn("checks", payload)
        self.assertIn("index", payload)
        self.assertEqual(payload["index"]["total_sessions"], 1)
        self.assertEqual(payload["index"]["total_messages"], 1)
        self.assertEqual(payload["index"]["sessions_by_source"].get("codex"), 1)
        self.assertIn("latest_session_at", payload["index"])
        self.assertIn("latest_indexed_file_mtime", payload["index"])


class TestDoctorFixes(DBTestCase):

    def test_fix_indexes_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude" / "projects"
            codex_dir = Path(tmpdir) / ".codex" / "sessions"
            claude_dir.mkdir(parents=True)
            codex_dir.mkdir(parents=True)

            session_path = claude_dir / "sample.jsonl"
            session_path.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "role": "user",
                        "cwd": "/tmp/demo",
                        "message": {"content": "hello"},
                        "timestamp": "2026-03-05T10:00:00Z",
                    }
                ) + "\n",
                encoding="utf-8",
            )

            with patch.object(recall, "CLAUDE_PROJECTS_DIR", claude_dir), patch.object(
                recall, "CODEX_SESSIONS_DIR", codex_dir
            ):
                payload = recall.build_doctor_payload(self.conn)
                actions = recall.apply_doctor_fixes(self.conn, payload)
                refreshed = recall.build_doctor_payload(self.conn, fix_applied=True, actions=actions)

            self.assertGreaterEqual(refreshed["index"]["total_sessions"], 1)
            self.assertTrue(refreshed["actions"])


if __name__ == "__main__":
    unittest.main()
