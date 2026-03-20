#!/usr/bin/env python3
"""Search past Claude Code and Codex sessions using FTS5 full-text search."""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import math
import time
from datetime import datetime
from pathlib import Path
import shlex

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from recall_common import extract_text, is_noise

SKILL_NAME = "recall"
SKILL_OWNER = "awesome-skills"
SKILL_VERSION = "0.4.0"
SCHEMA_VERSION = 2

CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
DB_PATH = Path.home() / ".recall.db"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"

# — Tuning constants ————————————————————————————————————————————————————————
SUMMARY_MAX_LEN = 120           # max chars stored per session summary
CANDIDATE_OVERFETCH_FACTOR = 3  # fetch N× limit to allow recency re-ranking
RECENCY_WEIGHT = 0.2            # blended rank = bm25 * (1 + weight * boost)
RECENCY_HALF_LIFE_DAYS = 30     # exponential decay half-life
LN2 = 0.693147                  # math.log(2), used in decay formula
DAILY_USER_MSG_MAX_CHARS = 300  # truncate each user message at this many chars
DAILY_USER_MSG_MAX_COUNT = 100  # max user messages per session in daily report


def create_schema(conn):
    # Use individual execute() calls (not executescript) so the caller's
    # transaction context is preserved if one exists.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            file_path TEXT,
            project TEXT,
            slug TEXT,
            timestamp INTEGER,
            mtime REAL,
            summary TEXT DEFAULT '',
            is_subagent INTEGER DEFAULT 0,
            parent_session_id TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dir_checkpoints (
            dir_path TEXT PRIMARY KEY,
            mtime REAL
        )
    """)
    # FTS5 virtual tables cannot use IF NOT EXISTS with execute() in all
    # SQLite builds; guard with a table-existence check instead.
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "messages" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE messages USING fts5(
                session_id UNINDEXED,
                role,
                text,
                tokenize='porter unicode61'
            )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_subagent ON sessions(is_subagent)")


def migrate_schema(conn):
    """Add columns if upgrading from an older schema."""
    for col, col_type, default in [
        ("source", "TEXT", "'claude'"),
        ("file_path", "TEXT", "''"),
        ("summary", "TEXT", "''"),
        ("is_subagent", "INTEGER", "0"),
        ("parent_session_id", "TEXT", "''"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type} DEFAULT {default}")
            conn.commit()
    current_ver = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if current_ver < SCHEMA_VERSION:
        if current_ver < 2:
            # v1→v2: mark sessions with MCP-injected noise summaries for re-indexing
            # by zeroing mtime so the incremental indexer re-parses them.
            conn.execute(
                "UPDATE sessions SET mtime = 0 "
                "WHERE summary LIKE 'Tool result of `%' OR summary LIKE 'Unknown skill: %'"
            )
            conn.commit()
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    # Ensure indexes exist for databases created before indexes were added
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_subagent ON sessions(is_subagent)",
    ]:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    # Ensure metadata table exists for databases created before it was added
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    except sqlite3.OperationalError:
        pass


def get_db_schema_version(conn):
    """Read SQLite schema version from PRAGMA user_version."""
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None
    return None


def detect_commit_sha():
    """Best-effort git commit detection for local/source installs."""
    candidates = [SCRIPT_DIR, SCRIPT_DIR.parent]
    for candidate in candidates:
        try:
            commit = subprocess.check_output(
                ["git", "-C", str(candidate), "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if commit:
                return commit
        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError, OSError):
            continue
    return "unknown"


def read_db_schema_version(db_path):
    """Read schema version from an existing DB path without creating a new DB."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return get_db_schema_version(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def build_version_payload():
    """Create version payload for CLI/text output."""
    db_schema = read_db_schema_version(DB_PATH)
    return {
        "name": SKILL_NAME,
        "owner": SKILL_OWNER,
        "version": SKILL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "db_schema_version": db_schema,
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "commit": detect_commit_sha(),
    }


def print_version(json_mode=False):
    """Print version details and return."""
    payload = build_version_payload()
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    db_schema = payload["db_schema_version"]
    db_schema_str = str(db_schema) if db_schema is not None else "none"
    print(f"{payload['name']} {payload['version']}")
    print(f"owner: {payload['owner']}")
    print(f"schema: {payload['schema_version']} (db: {db_schema_str})")
    print(f"commit: {payload['commit']}")
    print(f"db: {payload['db_path']}")


def migrate_db_location():
    """Move recall.db from ~/.claude/ to ~/ if it exists at the old path."""
    old_path = CLAUDE_DIR / "recall.db"
    if old_path.exists() and not DB_PATH.exists():
        # Flush WAL into main file so only one rename is needed (avoids
        # crash-window where main file moved but WAL/SHM are left behind).
        try:
            tmp_conn = sqlite3.connect(str(old_path))
            tmp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            tmp_conn.close()
        except sqlite3.Error:
            pass
        try:
            old_path.rename(DB_PATH)
        except OSError as e:
            print(f"Warning: could not migrate {old_path} to {DB_PATH}: {e}", file=sys.stderr)
            return
        # Clean up any residual WAL/SHM files
        for suffix in ("-wal", "-shm"):
            old_extra = Path(str(old_path) + suffix)
            if old_extra.exists():
                try:
                    old_extra.unlink()
                except OSError:
                    pass


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
CJK_SEGMENT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
FTS_SPECIAL_QUERY_RE = re.compile(r'["*():]|(^|\s)(AND|OR|NOT)(\s|$)', re.IGNORECASE)
CLAUDE_SUBAGENT_RE = re.compile(r"/([^/]+)/subagents/agent-[^/]+\.jsonl$")
CLAUDE_PROJECT_DIR_RE = re.compile(r"/\.claude/projects/(-[^/]+)")
LIKE_ESCAPE = "\\"
# Characters that are FTS5 operators and need quoting when used literally
FTS_OPERATOR_CHARS = set('(){}[]^~:!@#$&|\\/-.<>=;,\'?')

def escape_like(value):
    """Escape LIKE wildcards in user-provided terms."""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def normalize_project_path(path):
    """Normalize a project path for stable indexing/filtering comparisons."""
    if path is None:
        return ""
    value = str(path).strip()
    if not value:
        return ""
    expanded = os.path.expanduser(value)
    resolved = os.path.realpath(expanded)
    normalized = os.path.normpath(resolved)
    # Treat filesystem root as "no project" — it means cwd was unset or ran in /.
    if normalized == "/":
        return ""
    return normalized


def build_resume_command(source, project, session_id):
    """Build a source-appropriate resume command for a session."""
    if not session_id:
        return ""
    if source == "claude":
        resume_cmd = f"claude --resume {shlex.quote(session_id)}"
    elif source == "codex":
        resume_cmd = f"codex resume {shlex.quote(session_id)}"
    else:
        return ""
    if project:
        return f"cd {shlex.quote(project)} && {resume_cmd}"
    return resume_cmd


# Markers that indicate the start of MCP-injected content appended to a user message.
# When present mid-message, everything from the marker onward is stripped before
# the text is used as a summary (the user's actual request precedes the marker).
_INLINE_NOISE_MARKERS = (
    "\n# The result of `",
    "\n\n# The result of `",
)


def strip_inline_noise(text):
    """Remove MCP-injected tail from a user message before using it as a summary."""
    for marker in _INLINE_NOISE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def truncate_summary(summary, max_len):
    """Trim summary text to max_len with ellipsis when needed."""
    if not summary:
        return ""
    if max_len is None or max_len <= 0:
        return summary
    clean = " ".join(summary.split())
    if len(clean) <= max_len:
        return clean
    if max_len <= 3:
        return clean[:max_len]
    return clean[: max_len - 3] + "..."


def subagent_parent_session_id(file_path):
    """Return parent session ID for Claude subagent transcript paths."""
    match = CLAUDE_SUBAGENT_RE.search(file_path or "")
    return match.group(1) if match else None


def deduplicate_slugs(results):
    """Return a mapping of session_id -> display_slug with suffixes for duplicates.

    When multiple results share the same slug, append a short session_id suffix
    (last 8 chars) to make them visually distinct.
    """
    slug_counts = {}
    for row in results:
        slug = row[4] or ""
        slug_counts[slug] = slug_counts.get(slug, 0) + 1

    display_slugs = {}
    for row in results:
        session_id, slug = row[0], row[4] or ""
        if slug_counts.get(slug, 1) > 1:
            suffix = session_id[-8:] if len(session_id) >= 8 else session_id
            display_slugs[session_id] = f"{slug}-{suffix}"
        else:
            display_slugs[session_id] = slug
    return display_slugs


def result_to_dict(row, display_slug=None, summary_len=120, include_summary=True):
    """Convert an internal result tuple to a serializable dict."""
    session_id, source, file_path, project, slug, timestamp, excerpt, rank, summary = row
    parent_sid = subagent_parent_session_id(file_path)
    summary_value = truncate_summary(summary or "", summary_len) if include_summary else ""
    return {
        "session_id": session_id,
        "source": source,
        "file_path": file_path,
        "project": project,
        "slug": display_slug or slug,
        "timestamp": timestamp,
        "date": format_timestamp(timestamp),
        "summary": summary_value,
        "excerpt": make_excerpt(excerpt) if excerpt else "",
        "rank": rank,
        "is_subagent": bool(parent_sid),
        "parent_session_id": parent_sid or "",
        "resume_command": build_resume_command(source, project, session_id),
    }


def parse_iso_timestamp(ts_str):
    """Parse ISO 8601 timestamp string to epoch milliseconds."""
    if not ts_str or not isinstance(ts_str, str):
        if isinstance(ts_str, (int, float)):
            return int(ts_str)
        return None
    try:
        # Handle "2026-03-03T00:26:57.352Z" format
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def contains_cjk(text):
    """Return True if text includes any CJK characters."""
    return bool(text and CJK_RE.search(text))


def is_simple_query(text):
    """Best-effort check for a plain-text query (no explicit FTS syntax)."""
    return bool(text and not FTS_SPECIAL_QUERY_RE.search(text))


def extract_cjk_terms(text):
    """Extract distinct CJK terms for LIKE fallback matching."""
    seen = set()
    terms = []
    for segment in CJK_SEGMENT_RE.findall(text or ""):
        if segment not in seen:
            seen.add(segment)
            terms.append(segment)
    return terms


def make_excerpt(text, needle=None, max_len=200):
    """Create a short readable excerpt, centered around a match when possible."""
    clean = (text or "").replace("\n", " ").strip()
    if not clean:
        return ""
    if needle:
        idx = clean.find(needle)
        if idx >= 0:
            start = max(idx - 60, 0)
            end = min(idx + len(needle) + 120, len(clean))
            excerpt = clean[start:end]
            if start > 0:
                excerpt = "..." + excerpt
            if end < len(clean):
                excerpt = excerpt + "..."
            return excerpt
    return clean[:max_len] + ("..." if len(clean) > max_len else "")


def infer_project_from_path(file_path):
    """Infer project path from Claude session file path.

    e.g. ~/.claude/projects/-Users-admin-work/SESSION.jsonl -> /Users/admin/work

    The encoded segment uses dashes for path separators, but real directory
    names may also contain dashes (e.g. ``my-project`` encodes as ``my-project``).
    This encoding is lossy: ``/a/b-c`` and ``/a-b/c`` both encode to ``a-b-c``.
    When ambiguous, we prefer more path segments (shortest-match-first), which
    is correct for the common case.  When ``cwd`` is present in the session
    file itself, this fallback is not used.
    """
    match = CLAUDE_PROJECT_DIR_RE.search(file_path or "")
    if not match:
        return ""
    encoded = match.group(1)  # e.g. "-Users-admin-work" or "-Users-admin-my-project"
    parts = encoded.lstrip("-").split("-")
    if not parts:
        return ""

    # Greedy reconstruction: try to resolve from left, preferring longer segments
    # that correspond to real directories.
    segments = _greedy_decode_segments(parts)
    inferred = "/" + "/".join(segments)
    return normalize_project_path(inferred)


def _greedy_decode_segments(parts):
    """Decode dash-separated parts into path segments using filesystem probing.

    Tries shortest match first (single part) so that ``/a/b/c`` is preferred
    over ``/a/b-c`` when both directories exist.  Only groups consecutive
    parts with dashes when no single-part directory is found at that level.
    """
    segments = []
    i = 0
    while i < len(parts):
        # Try shortest match first (1 part), extending only when needed.
        best = 0
        for j in range(i + 1, len(parts) + 1):
            candidate = "-".join(parts[i:j])
            test_path = "/" + "/".join(segments + [candidate])
            if os.path.isdir(test_path):
                best = j - i
                break
        if best == 0:
            best = 1  # no match at all — consume one part (naive fallback)
        segments.append("-".join(parts[i:i + best]))
        i += best
    return segments


def sanitize_fts_query(query):
    """Make a user query safe for FTS5.

    Wraps tokens containing special characters in double quotes to prevent
    FTS syntax errors (e.g. "local-command-caveat" has a dash which FTS5
    interprets as NOT).
    """
    if not query:
        return query
    # If user explicitly used FTS operators, trust them
    if FTS_SPECIAL_QUERY_RE.search(query):
        return query
    # Check if any token has operator chars that need quoting
    tokens = query.split()
    needs_quoting = False
    for token in tokens:
        if any(c in FTS_OPERATOR_CHARS for c in token):
            needs_quoting = True
            break
    if not needs_quoting:
        return query
    # Quote each token that contains special chars
    safe_tokens = []
    for token in tokens:
        if any(c in FTS_OPERATOR_CHARS for c in token):
            # Escape any existing double quotes inside the token
            safe_tokens.append('"' + token.replace('"', '""') + '"')
        else:
            safe_tokens.append(token)
    return " ".join(safe_tokens)


def project_match_clause(project, alias):
    """Build SQL clause+params to match an exact project or its child paths."""
    normalized = normalize_project_path(project)
    if not normalized:
        normalized = "/"

    like_prefix = escape_like(normalized)
    clause = f"({alias}.project = ? OR {alias}.project LIKE ? ESCAPE '{LIKE_ESCAPE}')"
    return clause, [normalized, like_prefix + "/%"]


# — Claude Code session parser —————————————————————————————————————————————

def parse_claude_session(path):
    """Parse a Claude Code JSONL session file, returning (metadata, messages)."""
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
    summary = ""
    messages = []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")

                # Extract cwd from any entry
                if not project:
                    cwd = entry.get("cwd", "")
                    if cwd:
                        project = normalize_project_path(cwd)

                # Extract slug from any entry
                if not slug:
                    slug = entry.get("slug", "") or entry.get("leafName", "")

                # Parse timestamp
                ts_raw = entry.get("timestamp")
                ts_ms = parse_iso_timestamp(ts_raw)
                if ts_ms and (earliest_ts is None or ts_ms < earliest_ts):
                    earliest_ts = ts_ms

                # Determine role: check both "type" and "role" fields
                role = entry.get("role", "")
                if role not in ("user", "assistant"):
                    if etype == "user" or etype == "human":
                        role = "user"
                    elif etype == "assistant":
                        role = "assistant"
                    else:
                        continue

                # Extract text content — handle multiple formats:
                # 1. {message: {content: "..."}} or {message: {content: [{type:"text",...}]}}
                # 2. {content: "..."} or {content: [...]}
                content = entry.get("message", {})
                if isinstance(content, dict):
                    content = content.get("content", "")
                elif isinstance(content, str):
                    # message field is a plain string
                    pass
                else:
                    content = entry.get("content", "")

                text = extract_text(content)
                if not text:
                    continue

                # Filter noise (same as Codex parser)
                if is_noise(text):
                    continue

                messages.append((role, text))

                # Capture first meaningful user message as summary
                if not summary and role == "user":
                    summary = strip_inline_noise(text).replace("\n", " ").strip()[:SUMMARY_MAX_LEN]

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    if not slug:
        slug = session_id[:12]

    # Infer project from file path if cwd was not found
    if not project:
        project = infer_project_from_path(path)
    else:
        project = normalize_project_path(project)

    # Detect subagent
    parent_sid = subagent_parent_session_id(path)

    metadata = {
        "session_id": session_id,
        "source": "claude",
        "file_path": path,
        "project": project or "",
        "slug": slug,
        "timestamp": earliest_ts,
        "summary": summary,
        "is_subagent": 1 if parent_sid else 0,
        "parent_session_id": parent_sid or "",
    }
    return metadata, messages


# — Codex session parser ———————————————————————————————————————————————————

def parse_codex_session(path):
    """Parse a Codex JSONL session file, returning (metadata, messages).

    Codex sessions live in ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.
    Supports two formats:
      - Legacy: flat entries with {role, content, record_type, id, ...}
      - Current: wrapped entries with {timestamp, type, payload: {role, content, ...}}
    """
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
    summary = ""
    messages = []

    # Extract date from path: sessions/YYYY/MM/DD/rollout-...
    path_match = re.search(r"sessions/(\d{4}/\d{2}/\d{2})/", path)
    date_slug = path_match.group(1).replace("/", "-") if path_match else None

    # Extract session UUID from filename: rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.jsonl
    uuid_match = re.search(
        r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        session_id,
    )

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Skip state snapshots (legacy format)
                if entry.get("record_type") == "state":
                    continue

                # Parse timestamp (present in both formats at top level)
                ts_raw = entry.get("timestamp")
                if ts_raw:
                    ts_ms = parse_iso_timestamp(ts_raw)
                    if ts_ms and (earliest_ts is None or ts_ms < earliest_ts):
                        earliest_ts = ts_ms

                etype = entry.get("type", "")

                # Current format: {type: "session_meta", payload: {id, cwd, ...}}
                if etype == "session_meta":
                    payload = entry.get("payload", {})
                    entry_id = payload.get("id", "")
                    if entry_id and session_id.startswith("rollout-"):
                        session_id = entry_id
                    if not project:
                        project = normalize_project_path(payload.get("cwd", ""))
                    continue

                # Current format: {type: "response_item", payload: {role, content, ...}}
                # Legacy format: {role, content, ...} (no type or type="message")
                if etype == "response_item":
                    payload = entry.get("payload", {})
                    role = payload.get("role", "")
                    content = payload.get("content", "")
                elif etype in ("event_msg", "turn_context"):
                    continue
                else:
                    # Legacy format — session metadata in first entry
                    if not project and "id" in entry and "instructions" in entry:
                        entry_id = entry.get("id", "")
                        if entry_id and session_id.startswith("rollout-"):
                            session_id = entry_id
                        continue

                    role = entry.get("role", "")
                    content = entry.get("content", "")

                    # Legacy: extract cwd from <environment_context> blocks
                    if not project and isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                text = block.get("text", "")
                                if "Current working directory:" in text:
                                    cwd_match = re.search(
                                        r"Current working directory:\s*(.+)", text
                                    )
                                    if cwd_match:
                                        project = normalize_project_path(cwd_match.group(1).strip())

                # Only index user and assistant messages (skip developer/system)
                if role not in ("user", "assistant"):
                    continue

                text = extract_text(content)

                # Skip system/instruction blocks injected as user messages
                if not text:
                    continue
                if is_noise(text):
                    continue

                messages.append((role, text))

                # Capture first meaningful user message as summary
                if not summary and role == "user":
                    summary = strip_inline_noise(text).replace("\n", " ").strip()[:SUMMARY_MAX_LEN]

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    if not slug:
        short_id = uuid_match.group(1)[:8] if uuid_match else session_id[:8]
        slug = f"{date_slug}-{short_id}" if date_slug else short_id

    metadata = {
        "session_id": session_id,
        "source": "codex",
        "file_path": path,
        "project": project or "",
        "slug": slug,
        "timestamp": earliest_ts,
        "summary": summary,
        "is_subagent": 0,
        "parent_session_id": "",
    }
    return metadata, messages


def build_session_constraints(project=None, days=None, source=None, alias="s2", include_subagents=False):
    """Build session-level SQL filter clauses and parameter list."""
    conds = []
    params = []
    if not include_subagents:
        conds.append(f"{alias}.is_subagent = 0")
    if project:
        project_clause, project_params = project_match_clause(project, alias)
        conds.append(project_clause)
        params.extend(project_params)
    if days:
        cutoff = int((time.time() - days * 86400) * 1000)
        conds.append(f"{alias}.timestamp >= ?")
        params.append(cutoff)
    if source:
        conds.append(f"{alias}.source = ?")
        params.append(source)
    return conds, params


_PRUNE_INTERVAL_SECONDS = 3600  # rate-limit orphan pruning to once per hour


def _should_skip_prune(conn):
    """Return True if the last prune ran less than _PRUNE_INTERVAL_SECONDS ago."""
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = '_prune_last_run'"
        ).fetchone()
        if row:
            return time.time() - float(row[0]) < _PRUNE_INTERVAL_SECONDS
    except (sqlite3.OperationalError, ValueError, TypeError):
        pass
    return False


def _record_prune_timestamp(conn):
    """Record the current time as the last prune timestamp."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('_prune_last_run', ?)",
            (str(time.time()),),
        )
    except sqlite3.OperationalError:
        pass


def prune_orphan_sessions(conn):
    """Remove indexed sessions whose source files no longer exist.

    Always performs a full scan.  Callers that want rate-limiting (e.g.
    ``index_sessions``) should check ``_should_skip_prune`` beforehand.
    """
    to_delete = []
    for session_id, file_path in conn.execute("SELECT session_id, file_path FROM sessions"):
        if file_path and not os.path.exists(file_path):
            to_delete.append((session_id,))

    if to_delete:
        conn.executemany("DELETE FROM sessions WHERE session_id = ?", to_delete)
        conn.executemany("DELETE FROM messages WHERE session_id = ?", to_delete)

    _record_prune_timestamp(conn)
    return len(to_delete)


# — Indexing ———————————————————————————————————————————————————————————————

def _collect_files_with_dir_checkpoint(conn, base_dir, source, force=False):
    """Walk directories under base_dir, skipping unchanged ones via dir_checkpoints.

    Returns list of (file_path, source) for files in new/changed directories,
    and updates dir_checkpoints for visited directories.
    """
    if not base_dir.is_dir():
        return []

    # Load existing directory checkpoints
    dir_mtimes = {}
    if not force:
        try:
            prefix = str(base_dir)
            for row in conn.execute(
                "SELECT dir_path, mtime FROM dir_checkpoints WHERE dir_path LIKE ? ESCAPE '\\'",
                [escape_like(prefix) + "%"],
            ):
                dir_mtimes[row[0]] = row[1]
        except sqlite3.OperationalError:
            pass

    files = []
    updated_dirs = []
    for dirpath, dirnames, filenames in os.walk(str(base_dir)):
        try:
            current_mtime = os.path.getmtime(dirpath)
        except OSError:
            continue

        # Skip directory if mtime unchanged (no files added/removed)
        if not force and dirpath in dir_mtimes and dir_mtimes[dirpath] == current_mtime:
            continue

        updated_dirs.append((dirpath, current_mtime))

        for fname in filenames:
            if fname.endswith(".jsonl"):
                files.append((os.path.join(dirpath, fname), source))

    # Update checkpoints for changed directories
    if updated_dirs:
        conn.executemany(
            "INSERT OR REPLACE INTO dir_checkpoints (dir_path, mtime) VALUES (?, ?)",
            updated_dirs,
        )

    return files


def index_sessions(conn, force=False):
    """Scan and index new/changed session files from all sources.

    Caller must wrap this in a transaction (e.g. BEGIN IMMEDIATE) for atomicity.
    """
    if force:
        # Use individual execute() calls instead of executescript() to
        # preserve the caller's BEGIN IMMEDIATE transaction.  executescript()
        # implicitly commits any pending transaction, breaking atomicity.
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM dir_checkpoints")

    orphaned = 0
    if not force and not _should_skip_prune(conn):
        orphaned = prune_orphan_sessions(conn)

    # Get existing mtimes keyed by file_path (stable across session_id changes)
    existing = {}
    try:
        for row in conn.execute("SELECT file_path, session_id, mtime, timestamp, source FROM sessions"):
            existing[row[0]] = (row[1], row[2], row[3], row[4])
    except sqlite3.OperationalError:
        pass

    # Collect files from changed directories only (fast path)
    sources = []
    sources.extend(_collect_files_with_dir_checkpoint(conn, CLAUDE_PROJECTS_DIR, "claude", force))
    sources.extend(_collect_files_with_dir_checkpoint(conn, CODEX_SESSIONS_DIR, "codex", force))

    # Check already-indexed files for content updates even when dir mtime is unchanged.
    # Appending to a JSONL file changes file mtime but not directory mtime.
    if not force:
        collected_paths = {fpath for fpath, _ in sources}
        for file_path, (sid, stored_mtime, ts, src) in existing.items():
            if file_path not in collected_paths:
                try:
                    current_mtime = os.path.getmtime(file_path)
                except OSError:
                    continue
                if current_mtime != stored_mtime:
                    sources.append((file_path, src))

    indexed = 0
    skipped = 0
    wrote_messages = False

    for fpath, source in sources:
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue

        if (
            not force
            and fpath in existing
            and existing[fpath][1] == mtime
            and (existing[fpath][2] or 0) > 0
        ):
            skipped += 1
            continue

        # Remove old data for this file if re-indexing
        if fpath in existing:
            old_sid = existing[fpath][0]
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (old_sid,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (old_sid,))

        if source == "claude":
            result = parse_claude_session(fpath)
        else:
            result = parse_codex_session(fpath)

        if result is None:
            continue

        metadata, messages = result

        # Disable FTS5 automerge only when we know we'll write new rows.
        if not wrote_messages:
            conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 0)")
            wrote_messages = True

        session_timestamp = metadata["timestamp"] if metadata["timestamp"] else int(mtime * 1000)

        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, source, file_path, project, slug, timestamp, mtime, summary, is_subagent, parent_session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (metadata["session_id"], metadata["source"], metadata["file_path"],
             metadata["project"], metadata["slug"], session_timestamp, mtime,
             metadata.get("summary", ""), metadata.get("is_subagent", 0),
             metadata.get("parent_session_id", "")),
        )

        conn.executemany(
            "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
            [(metadata["session_id"], role, text) for role, text in messages],
        )

        indexed += 1

    # Merge all FTS5 segments into one and restore automerge
    if indexed > 0:
        conn.execute("INSERT INTO messages(messages) VALUES('optimize')")
        conn.execute("INSERT INTO messages(messages, rank) VALUES('automerge', 4)")

    # Get totals
    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    return indexed, skipped, total_sessions, total_messages, orphaned


# — Search —————————————————————————————————————————————————————————————————

def list_sessions(conn, project=None, days=None, source=None, limit=10, query=None,
                   include_subagents=False, offset=0):
    """List sessions ordered by recency, optionally filtered by a text query."""
    conds, params = build_session_constraints(
        project=project, days=days, source=source, alias="s",
        include_subagents=include_subagents,
    )

    if not query:
        sql = "SELECT session_id, source, file_path, project, slug, timestamp, summary FROM sessions s"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY timestamp DESC, session_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [(sid, src, fpath, proj, slug, ts, "", 0.0, summ) for sid, src, fpath, proj, slug, ts, summ in rows]

    # Query-filtered list mode: match text, then sort by recency.
    safe_query = sanitize_fts_query(query)
    query_sql = "SELECT s.session_id, s.source, s.file_path, s.project, s.slug, s.timestamp, s.summary FROM sessions s WHERE "
    if conds:
        query_sql += " AND ".join(conds) + " AND "
    query_sql += "s.session_id IN (SELECT session_id FROM messages WHERE messages MATCH ?) "
    query_sql += "ORDER BY s.timestamp DESC, s.session_id DESC LIMIT ? OFFSET ?"

    query_params = params + [safe_query, limit, offset]
    try:
        rows = conn.execute(query_sql, query_params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"List query error, falling back to LIKE: {e}", file=sys.stderr)
        return search_like_fallback(
            conn, query, project=project, days=days, source=source,
            limit=limit, include_subagents=include_subagents, offset=offset,
        )

    results = [(sid, src, fpath, proj, slug, ts, "", 0.0, summ) for sid, src, fpath, proj, slug, ts, summ in rows]
    if results:
        return results

    # If FTS under-recalls for simple CJK query, use substring fallback and keep recency order.
    if contains_cjk(query) and is_simple_query(query):
        return search_cjk_fallback(
            conn,
            query,
            project=project,
            days=days,
            source=source,
            limit=limit,
            include_subagents=include_subagents,
            preserve_sql_order=True,
            offset=offset,
        )
    return results


def search_cjk_fallback(conn, query, project=None, days=None, source=None, limit=10,
                        include_subagents=False, preserve_sql_order=False, offset=0):
    """Fallback search for simple CJK queries using escaped substring matching."""
    terms = extract_cjk_terms(query)
    if not terms:
        return []

    like_conds = ["m.text LIKE ? ESCAPE '\\'" for _ in terms]
    like_params = [f"%{escape_like(term)}%" for term in terms]
    session_conds, session_params = build_session_constraints(
        project=project, days=days, source=source, alias="s",
        include_subagents=include_subagents,
    )
    session_filter = ""
    if session_conds:
        session_filter = " AND " + " AND ".join(session_conds)

    candidate_limit = (offset + limit) * CANDIDATE_OVERFETCH_FACTOR
    sql = f"""
        SELECT m.session_id, MAX(m.rowid) as match_rowid,
               s.source, s.file_path, s.project, s.slug, s.timestamp, s.summary
        FROM messages m
        JOIN sessions s ON s.session_id = m.session_id
        WHERE {' AND '.join(like_conds)}{session_filter}
        GROUP BY m.session_id
        ORDER BY s.timestamp DESC, match_rowid DESC
        LIMIT ?
    """
    params = like_params + session_params + [candidate_limit]

    try:
        matched = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"CJK fallback search error: {e}", file=sys.stderr)
        return []

    # Batch fetch excerpt texts (eliminates N per-row queries)
    rowids = [row[1] for row in matched]
    text_map = {}
    if rowids:
        ph = ",".join("?" * len(rowids))
        for rid, txt in conn.execute(f"SELECT rowid, text FROM messages WHERE rowid IN ({ph})", rowids):
            text_map[rid] = txt

    results = []
    now_ms = time.time() * 1000
    highlight_term = terms[0]
    for session_id, rowid, src, file_path, project_value, slug, timestamp, summary in matched:
        excerpt = make_excerpt(text_map.get(rowid, ""), highlight_term)

        if timestamp:
            age_days = max((now_ms - timestamp) / 86_400_000, 0)
            recency_boost = math.exp(-LN2 * age_days / RECENCY_HALF_LIFE_DAYS)
        else:
            recency_boost = 0.0

        # Keep FTS-ranked results ahead of fallback results; use recency within fallback set.
        blended_rank = 1.0 - RECENCY_WEIGHT * recency_boost
        results.append((session_id, src, file_path, project_value, slug, timestamp, excerpt, blended_rank, summary or ""))

    if not preserve_sql_order:
        results.sort(key=lambda r: r[7])
    return results[offset:offset + limit]


def search_like_fallback(conn, query, project=None, days=None, source=None, limit=10,
                         include_subagents=False, offset=0):
    """Fallback search using LIKE when FTS query fails (e.g. special characters)."""
    escaped = escape_like(query)
    session_conds, session_params = build_session_constraints(
        project=project, days=days, source=source, alias="s",
        include_subagents=include_subagents,
    )
    session_filter = ""
    if session_conds:
        session_filter = " AND " + " AND ".join(session_conds)

    sql = f"""
        SELECT m.session_id, MAX(m.rowid) as match_rowid,
               s.source, s.file_path, s.project, s.slug, s.timestamp, s.summary
        FROM messages m
        JOIN sessions s ON s.session_id = m.session_id
        WHERE m.text LIKE ? ESCAPE '\\'{session_filter}
        GROUP BY m.session_id
        ORDER BY s.timestamp DESC, match_rowid DESC
        LIMIT ? OFFSET ?
    """
    params = [f"%{escaped}%"] + session_params + [limit, offset]

    try:
        matched = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"LIKE fallback search error: {e}", file=sys.stderr)
        return []

    # Batch fetch excerpt texts (eliminates N per-row queries)
    rowids = [row[1] for row in matched]
    text_map = {}
    if rowids:
        ph = ",".join("?" * len(rowids))
        for rid, txt in conn.execute(f"SELECT rowid, text FROM messages WHERE rowid IN ({ph})", rowids):
            text_map[rid] = txt

    results = []
    for session_id, rowid, src, file_path, project_value, slug_val, timestamp, summary in matched:
        excerpt = make_excerpt(text_map.get(rowid, ""), query)
        results.append((session_id, src, file_path, project_value, slug_val, timestamp, excerpt, 0.0, summary or ""))

    return results


def search(conn, query, project=None, days=None, source=None, limit=10, include_subagents=False, offset=0):
    """Search indexed sessions."""
    safe_query = sanitize_fts_query(query)

    # FTS5 auxiliary functions (bm25, snippet) don't work with GROUP BY.
    # Use a subquery to get the best-ranking rowid per session, then fetch snippets.
    fts_params = [safe_query]
    session_filter = ""

    subconds, subparams = build_session_constraints(
        project=project, days=days, source=source, alias="s2",
        include_subagents=include_subagents,
    )
    if subconds:
        session_filter = (
            " AND session_id IN "
            "(SELECT s2.session_id FROM sessions s2 WHERE " + " AND ".join(subconds) + ")"
        )
        fts_params.extend(subparams)

    # Over-fetch candidates so recency re-ranking can surface recent results
    # that pure BM25 might have ranked just outside the cutoff.
    candidate_limit = (offset + limit) * CANDIDATE_OVERFETCH_FACTOR
    fts_params.append(candidate_limit)

    # First find best-ranking session_ids.
    # FTS5's rank column is auto-populated with bm25 when using ORDER BY rank.
    inner_sql = f"""
        SELECT session_id, MIN(rank) as best_rank
        FROM messages
        WHERE messages MATCH ?{session_filter}
        GROUP BY session_id
        ORDER BY best_rank
        LIMIT ?
    """

    try:
        # Two-pass: first get sessions+ranks, then fetch snippets individually
        ranked = conn.execute(inner_sql, fts_params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"FTS search error, falling back to LIKE: {e}", file=sys.stderr)
        return search_like_fallback(
            conn, query, project=project, days=days, source=source,
            limit=limit, include_subagents=include_subagents, offset=offset,
        )

    if not ranked:
        return []

    # Batch fetch session metadata (eliminates N per-session queries)
    session_ids = [sid for sid, _ in ranked]
    ph = ",".join("?" * len(session_ids))
    meta_rows = conn.execute(
        f"SELECT session_id, source, file_path, project, slug, timestamp, summary "
        f"FROM sessions WHERE session_id IN ({ph})",
        session_ids,
    ).fetchall()
    meta_map = {row[0]: row[1:] for row in meta_rows}

    # First pass: compute blended ranks WITHOUT fetching snippets.
    candidates = []
    now_ms = time.time() * 1000
    for session_id, rank in ranked:
        meta = meta_map.get(session_id)
        if not meta:
            continue

        # Apply recency bias: blend BM25 score with a time-decay boost.
        # BM25 rank is negative (more negative = better match).
        # Recency boost: 1.0 for today, decaying with a half-life of 30 days.
        # Multiplying negative rank by (1 + boost) makes it MORE negative = better.
        timestamp = meta[4]
        if timestamp:
            age_days = max((now_ms - timestamp) / 86_400_000, 0)
            recency_boost = math.exp(-LN2 * age_days / RECENCY_HALF_LIFE_DAYS)
        else:
            recency_boost = 0.0
        blended_rank = rank * (1 + RECENCY_WEIGHT * recency_boost)
        candidates.append((session_id, meta, blended_rank))

    # Sort and slice BEFORE fetching snippets (reduces snippet queries to ≤ limit).
    candidates.sort(key=lambda c: c[2])
    page = candidates[offset:offset + limit]

    # Second pass: fetch snippets only for the final result page.
    results = []
    for session_id, meta, blended_rank in page:
        snippet_row = conn.execute(
            "SELECT snippet(messages, 2, '**', '**', '...', 20) FROM messages WHERE messages MATCH ? AND session_id = ? LIMIT 1",
            (safe_query, session_id),
        ).fetchone()
        excerpt = snippet_row[0] if snippet_row else ""
        results.append((session_id, meta[0], meta[1], meta[2], meta[3], meta[4], excerpt, blended_rank, meta[5] or ""))

    return results


def format_timestamp(ts_ms, precise=False):
    """Format millisecond timestamp to date string."""
    if ts_ms is None or ts_ms <= 0:
        return "unknown"
    try:
        ts = float(ts_ms) / 1000  # epoch ms to seconds
        fmt = "%Y-%m-%d %H:%M" if precise else "%Y-%m-%d"
        return time.strftime(fmt, time.localtime(ts))
    except (OSError, ValueError, TypeError):
        return "unknown"


def format_epoch_seconds(ts_seconds, precise=True):
    """Format epoch seconds to a local time string."""
    if ts_seconds is None:
        return "unknown"
    try:
        fmt = "%Y-%m-%d %H:%M:%S" if precise else "%Y-%m-%d"
        return time.strftime(fmt, time.localtime(float(ts_seconds)))
    except (OSError, ValueError, TypeError):
        return "unknown"


def build_doctor_suggestions(payload):
    """Generate actionable next-step suggestions for doctor output."""
    suggestions = []
    checks = payload.get("checks", {})
    index = payload.get("index", {})
    warnings = payload.get("warnings", [])

    if not checks.get("db_writable", True):
        suggestions.append("Verify write permissions for ~/.recall.db and parent directory.")
    if not checks.get("claude_projects_dir_exists", False) and not checks.get("codex_sessions_dir_exists", False):
        suggestions.append("Ensure ~/.claude/projects or ~/.codex/sessions exists and contains session JSONL files.")
    if index.get("total_sessions", 0) == 0:
        suggestions.append("Run: recall.py --reindex --list --limit 5")
    if warnings and not suggestions:
        suggestions.append("Run: recall.py --doctor --json")
    return suggestions


def build_doctor_payload(conn, fix_applied=False, actions=None):
    """Collect health diagnostics for the local recall index."""
    can_write = True
    write_error = ""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.Error as e:
        can_write = False
        write_error = str(e)

    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    subagent_sessions = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE COALESCE(is_subagent, 0) = 1"
    ).fetchone()[0]
    source_rows = conn.execute(
        "SELECT source, COUNT(*) FROM sessions GROUP BY source ORDER BY source"
    ).fetchall()
    by_source = {source: count for source, count in source_rows}
    latest_session_ts = conn.execute("SELECT MAX(timestamp) FROM sessions").fetchone()[0]
    latest_mtime = conn.execute("SELECT MAX(mtime) FROM sessions").fetchone()[0]

    def _safe_size(p):
        try:
            return p.stat().st_size
        except OSError:
            return 0

    db_exists = DB_PATH.exists()
    db_size_bytes = _safe_size(DB_PATH) if db_exists else 0
    wal_path = Path(str(DB_PATH) + "-wal")
    shm_path = Path(str(DB_PATH) + "-shm")
    wal_size_bytes = _safe_size(wal_path)
    shm_size_bytes = _safe_size(shm_path)

    checks = {
        "db_exists": db_exists,
        "db_writable": can_write,
        "claude_projects_dir_exists": CLAUDE_PROJECTS_DIR.is_dir(),
        "codex_sessions_dir_exists": CODEX_SESSIONS_DIR.is_dir(),
    }
    warnings = []
    if not checks["db_writable"]:
        warnings.append(f"DB not writable: {write_error}")
    if not checks["claude_projects_dir_exists"] and not checks["codex_sessions_dir_exists"]:
        warnings.append("Neither Claude nor Codex session directory exists.")
    if total_sessions == 0:
        warnings.append("Index is empty. Run a search/list once to trigger indexing.")

    payload = {
        "name": SKILL_NAME,
        "owner": SKILL_OWNER,
        "version": SKILL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "db_schema_version": get_db_schema_version(conn),
        "commit": detect_commit_sha(),
        "checks": checks,
        "paths": {
            "db": str(DB_PATH),
            "claude_projects": str(CLAUDE_PROJECTS_DIR),
            "codex_sessions": str(CODEX_SESSIONS_DIR),
        },
        "sizes": {
            "db_bytes": db_size_bytes,
            "wal_bytes": wal_size_bytes,
            "shm_bytes": shm_size_bytes,
        },
        "index": {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "subagent_sessions": subagent_sessions,
            "sessions_by_source": by_source,
            "latest_session_at": format_timestamp(latest_session_ts, precise=True),
            "latest_indexed_file_mtime": format_epoch_seconds(latest_mtime, precise=True),
        },
        "warnings": warnings,
        "fix_applied": bool(fix_applied),
        "actions": actions or [],
    }
    payload["suggestions"] = build_doctor_suggestions(payload)
    return payload


def apply_doctor_fixes(conn, payload):
    """Apply safe, automatic fixes for common doctor findings."""
    actions = []
    checks = payload.get("checks", {})
    index = payload.get("index", {})

    if not checks.get("db_writable", False):
        actions.append("Skipped auto-fix: database is not writable.")
        return actions

    if index.get("total_sessions", 0) == 0:
        if not checks.get("claude_projects_dir_exists", False) and not checks.get("codex_sessions_dir_exists", False):
            actions.append("Skipped auto-fix: no source session directories found.")
            return actions
        t0 = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            indexed, skipped, total_sessions, total_messages, orphaned = index_sessions(conn, force=True)
            conn.commit()
            elapsed = time.time() - t0
            actions.append(
                f"Indexed {indexed} sessions (skipped {skipped}, pruned {orphaned}) in {elapsed:.1f}s "
                f"-> total {total_sessions} sessions / {total_messages} messages."
            )
        except Exception as exc:
            conn.rollback()
            actions.append(f"Auto-fix failed while indexing: {exc}")
        return actions

    actions.append("No automatic fixes were needed.")
    return actions


def print_doctor(payload, json_mode=False):
    """Print doctor diagnostics in text or JSON form."""
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    checks = payload["checks"]
    status = "OK" if not payload["warnings"] else "WARN"
    print(f"{payload['name']} doctor: {status}")
    print(f"version: {payload['version']}  commit: {payload['commit']}")
    print(
        "schema: "
        f"{payload['schema_version']} (db: {payload['db_schema_version'] if payload['db_schema_version'] is not None else 'none'})"
    )
    print(f"db: {payload['paths']['db']}")
    print(f"db writable: {'yes' if checks['db_writable'] else 'no'}")
    print(f"claude dir exists: {'yes' if checks['claude_projects_dir_exists'] else 'no'}")
    print(f"codex dir exists: {'yes' if checks['codex_sessions_dir_exists'] else 'no'}")
    print(
        "index: "
        f"{payload['index']['total_sessions']} sessions, "
        f"{payload['index']['total_messages']} messages, "
        f"{payload['index']['subagent_sessions']} subagents"
    )
    print(
        "latest: "
        f"session={payload['index']['latest_session_at']}, "
        f"indexed_file_mtime={payload['index']['latest_indexed_file_mtime']}"
    )
    if payload["warnings"]:
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"  - {warning}")
    if payload.get("actions"):
        print("actions:")
        for action in payload["actions"]:
            print(f"  - {action}")
    if payload.get("suggestions"):
        print("next:")
        for suggestion in payload["suggestions"]:
            print(f"  - {suggestion}")


def build_daily_report(conn, date_str=None):
    """Return a structured daily report dict for a given date (YYYY-MM-DD) or today.

    The report groups non-subagent sessions by project (cwd).  Sessions without
    a project path are grouped under path=None.  All user messages are fetched
    for each session, truncated to DAILY_USER_MSG_MAX_CHARS, capped at
    DAILY_USER_MSG_MAX_COUNT entries.  total_messages is the combined user +
    assistant message count for each session.
    """
    if date_str:
        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: invalid date '{date_str}', expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        report_date = datetime.today().date()

    # Epoch-ms bounds for the natural day in local timezone.
    start_ms = int(datetime(report_date.year, report_date.month, report_date.day).timestamp() * 1000)
    end_ms = start_ms + 86400 * 1000 - 1

    rows = conn.execute(
        "SELECT session_id, source, file_path, project, slug, timestamp "
        "FROM sessions "
        "WHERE is_subagent = 0 AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC",
        (start_ms, end_ms),
    ).fetchall()

    sources_count = {"claude": 0, "codex": 0}
    projects_map = {}  # project_path (or None) -> list of session dicts

    for session_id, source, file_path, project, slug, timestamp in rows:
        if source in sources_count:
            sources_count[source] += 1

        total_row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        total_messages = total_row[0] if total_row else 0

        user_msg_rows = conn.execute(
            "SELECT text FROM messages WHERE session_id = ? AND role = 'user' LIMIT ?",
            (session_id, DAILY_USER_MSG_MAX_COUNT),
        ).fetchall()

        user_messages = []
        for (text,) in user_msg_rows:
            if text:
                if len(text) > DAILY_USER_MSG_MAX_CHARS:
                    text = text[:DAILY_USER_MSG_MAX_CHARS] + "..."
                user_messages.append(text)

        session_dict = {
            "session_id": session_id,
            "slug": slug or "",
            "started_at": format_timestamp(timestamp, precise=True) if timestamp else None,
            "source": source,
            "total_messages": total_messages,
            "resume_command": build_resume_command(source, project, session_id),
            "user_messages": user_messages,
        }

        proj_key = project if project else None
        projects_map.setdefault(proj_key, []).append(session_dict)

    projects = [
        {"path": path, "sessions": sessions}
        for path, sessions in sorted(projects_map.items(), key=lambda x: (x[0] is None, x[0] or ""))
    ]

    return {
        "date": report_date.isoformat(),
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "total_sessions": len(rows),
        "sources": sources_count,
        "projects": projects,
    }


def main():
    parser = argparse.ArgumentParser(description="Search past Claude Code and Codex sessions")
    parser.add_argument("query", nargs="?", help="Search query; optional in --list mode to filter listed sessions")
    parser.add_argument("--list", action="store_true", help="List recent sessions; optional QUERY filters the list")
    parser.add_argument("--project", help="Filter to sessions from an exact project path or its child paths")
    parser.add_argument("--days", type=int, help="Only sessions from last N days")
    parser.add_argument("--source", choices=["claude", "codex"], help="Filter by source (claude or codex)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N results (for pagination)")
    parser.add_argument("--summary-len", type=int, default=120, help="Max summary length in output (default: 120)")
    parser.add_argument("--no-summary", action="store_true", help="Hide per-session summary lines")
    parser.add_argument("--include-subagents", action="store_true", help="Include subagent sessions in results")
    parser.add_argument("--reindex", action="store_true", help="Force full rebuild of the index")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--version", action="store_true", help="Show skill/version metadata and exit")
    parser.add_argument("--doctor", action="store_true", help="Run local health checks and exit")
    parser.add_argument("--fix", action="store_true", help="Apply safe auto-fixes (requires --doctor)")
    parser.add_argument("--daily", nargs="?", const="", metavar="DATE",
                        help="Generate daily report for DATE (YYYY-MM-DD) or today if omitted")

    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be > 0")
    if args.offset < 0:
        parser.error("--offset must be >= 0")
    if args.summary_len <= 0:
        parser.error("--summary-len must be > 0")
    if args.fix and not args.doctor:
        parser.error("--fix requires --doctor")
    if args.version:
        if args.list or args.query or args.doctor or args.reindex or args.fix:
            parser.error("--version cannot be combined with search/list/doctor options")
        print_version(json_mode=args.json)
        return
    if args.doctor and (args.list or args.query):
        parser.error("--doctor cannot be combined with search/list query arguments")
    if args.doctor and args.reindex:
        parser.error("--reindex cannot be combined with --doctor")
    if args.daily is not None and (args.list or args.query or args.doctor):
        parser.error("--daily cannot be combined with search/list/doctor options")
    if not args.list and not args.query and not args.doctor and args.daily is None:
        parser.error("QUERY is required unless --list, --daily, or --doctor is used")

    inc_sub = args.include_subagents
    show_summary = not args.no_summary

    # Expand --project . to the current working directory.
    if args.project == ".":
        args.project = os.getcwd()

    migrate_db_location()
    new_db = not DB_PATH.exists()
    old_umask = os.umask(0o077)
    try:
        conn = sqlite3.connect(str(DB_PATH))
    except sqlite3.Error as e:
        print(f"Error: cannot open database {DB_PATH}: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        os.umask(old_umask)
    if new_db:
        try:
            os.chmod(str(DB_PATH), 0o600)
        except OSError:
            pass
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        create_schema(conn)
        migrate_schema(conn)

        if args.doctor:
            payload = build_doctor_payload(conn)
            if args.fix:
                actions = apply_doctor_fixes(conn, payload)
                payload = build_doctor_payload(conn, fix_applied=True, actions=actions)
            print_doctor(payload, json_mode=args.json)
            return

        if args.daily is not None:
            # Index first so the report reflects the latest sessions.
            try:
                conn.execute("BEGIN IMMEDIATE")
                index_sessions(conn, force=False)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            date_arg = args.daily if args.daily else None
            payload = build_daily_report(conn, date_str=date_arg)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        # Index (single writer transaction for better concurrent safety)
        t0 = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            indexed, skipped, total_sessions, total_messages, orphaned = index_sessions(conn, force=args.reindex)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        index_time = time.time() - t0

        if indexed > 0:
            print(f"Indexed {indexed} sessions in {index_time:.1f}s", file=sys.stderr)
        if orphaned > 0:
            print(f"Pruned {orphaned} orphaned sessions", file=sys.stderr)

        # Search or list
        if args.list:
            results = list_sessions(
                conn,
                project=args.project,
                days=args.days,
                source=args.source,
                limit=args.limit,
                query=args.query,
                include_subagents=inc_sub,
                offset=args.offset,
            )
        else:
            results = search(
                conn, args.query, project=args.project, days=args.days,
                source=args.source, limit=args.limit, include_subagents=inc_sub,
                offset=args.offset,
            )

            # For simple Chinese queries, augment sparse FTS results with substring fallback.
            # Use offset=0 here because search() already applied the user's offset;
            # this path only fills gaps in the current page with additional matches.
            if contains_cjk(args.query) and is_simple_query(args.query) and len(results) < args.limit:
                fallback = search_cjk_fallback(
                    conn,
                    args.query,
                    project=args.project,
                    days=args.days,
                    source=args.source,
                    limit=args.limit,
                    include_subagents=inc_sub,
                )
                existing_ids = {row[0] for row in results}
                for row in fallback:
                    if row[0] not in existing_ids:
                        results.append(row)
                results.sort(key=lambda r: r[7])
                results = results[:args.limit]

        # Post-filter: remove results whose source file has been deleted.
        # This is O(limit) stat calls — cheap — and avoids showing stale
        # sessions between rate-limited full prune cycles.
        results = [r for r in results if not r[2] or os.path.exists(r[2])]

        if not results:
            if args.json:
                payload = {
                    "mode": "list" if args.list else "search",
                    "query": args.query,
                    "filters": {
                        "project": args.project,
                        "days": args.days,
                        "source": args.source,
                        "limit": args.limit,
                        "offset": args.offset,
                        "include_subagents": inc_sub,
                    },
                    "index": {
                        "total_sessions": total_sessions,
                        "total_messages": total_messages,
                    },
                    "output": {
                        "summary_len": args.summary_len,
                        "summary_enabled": show_summary,
                    },
                    "results": [],
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print("No sessions found." if args.list else "No matching sessions found.")
            return

        # Deduplicate slugs across results
        display_slugs = deduplicate_slugs(results)

        if args.json:
            payload = {
                "mode": "list" if args.list else "search",
                "query": args.query,
                "filters": {
                    "project": args.project,
                    "days": args.days,
                    "source": args.source,
                    "limit": args.limit,
                    "offset": args.offset,
                    "include_subagents": inc_sub,
                },
                "index": {
                    "total_sessions": total_sessions,
                    "total_messages": total_messages,
                },
                "output": {
                    "summary_len": args.summary_len,
                    "summary_enabled": show_summary,
                },
            }
            payload["results"] = [
                result_to_dict(
                    row,
                    display_slugs.get(row[0]),
                    summary_len=args.summary_len,
                    include_summary=show_summary,
                )
                for row in results
            ]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if args.list:
            mode_hint = "by recency" if not args.query else "by recency, filtered by query"
            verb = "Listed"
            active_filters = []
            if args.source:
                active_filters.append(f"source={args.source}")
            if args.days:
                active_filters.append(f"days={args.days}")
            if args.project:
                active_filters.append(f"project={args.project}")
            filter_str = f" | filters: {', '.join(active_filters)}" if active_filters else ""
        else:
            mode_hint = "by relevance"
            filter_str = ""
            verb = "Found"
        print(f"{verb} {len(results)} sessions [{mode_hint}{filter_str}] (index: {total_sessions} sessions, {total_messages} messages):\n")

        for i, (session_id, source, file_path, project, slug, timestamp, excerpt, rank, summary) in enumerate(results, 1):
            date = format_timestamp(timestamp, precise=True)
            src_tag = f"[{source}]" if source else ""
            parent_sid = subagent_parent_session_id(file_path)
            subagent_tag = f" [subagent of {parent_sid}]" if parent_sid else ""
            proj_name = Path(project).name if project else "unknown"
            display_slug = display_slugs.get(session_id, slug)
            print(f"[{i}] {date} | {proj_name} {src_tag}{subagent_tag}")
            summary_display = truncate_summary(summary, args.summary_len) if show_summary else ""
            if summary_display:
                print(f"    {summary_display}")
            if project:
                print(f"    Project: {project}")
            print(f"    ID: {session_id}")
            if display_slug and display_slug != session_id:
                print(f"    Slug: {display_slug}")
            if parent_sid:
                print(f"    Parent: {parent_sid}")
            if file_path:
                print(f"    File: {file_path}")
            resume_cmd = build_resume_command(source, project, session_id)
            if resume_cmd:
                print(f"    Resume: {resume_cmd}")
            if excerpt:
                excerpt_clean = make_excerpt(excerpt)
                print(f"    > {excerpt_clean}")
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
