#!/usr/bin/env python3
"""Search past Claude Code and Codex sessions using FTS5 full-text search."""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from glob import glob
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
DB_PATH = Path.home() / ".recall.db"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT,
            project TEXT,
            slug TEXT,
            timestamp INTEGER,
            mtime REAL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
            session_id UNINDEXED,
            role,
            text,
            tokenize='porter unicode61'
        );
    """)


def migrate_schema(conn):
    """Add source column if upgrading from an older schema."""
    try:
        conn.execute("SELECT source FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'claude'")
        conn.commit()


def migrate_db_location():
    """Move recall.db from ~/.claude/ to ~/ if it exists at the old path."""
    old_path = CLAUDE_DIR / "recall.db"
    if old_path.exists() and not DB_PATH.exists():
        old_path.rename(DB_PATH)
        # Also move the WAL/SHM files if they exist
        for suffix in ("-wal", "-shm"):
            old_extra = Path(str(old_path) + suffix)
            if old_extra.exists():
                old_extra.rename(Path(str(DB_PATH) + suffix))


def extract_text(content):
    """Extract plain text from message content (string or array format)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            # Skip non-text block types
            if btype in ("tool_result", "tool_use", "thinking", "image"):
                continue
            # Accept text from both Claude ("text") and Codex ("input_text", "output_text") formats
            if btype in ("text", "input_text", "output_text"):
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


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


# — Claude Code session parser —————————————————————————————————————————————

def parse_claude_session(path):
    """Parse a Claude Code JSONL session file, returning (metadata, messages)."""
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
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
                        project = cwd

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
                if text:
                    messages.append((role, text))

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    if not slug:
        slug = session_id[:12]

    metadata = {
        "session_id": session_id,
        "source": "claude",
        "project": project or "",
        "slug": slug,
        "timestamp": earliest_ts or 0,
    }
    return metadata, messages


# — Codex session parser ———————————————————————————————————————————————————

def parse_codex_session(path):
    """Parse a Codex JSONL session file, returning (metadata, messages).

    Codex sessions live in ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.
    Entries use OpenAI's Responses API format with {type: "message", role, content}.
    State snapshots ({record_type: "state"}) and instruction blocks are skipped.
    """
    session_id = Path(path).stem
    project = None
    slug = None
    earliest_ts = None
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

                # Skip state snapshots
                if entry.get("record_type") == "state":
                    continue

                # First entry may carry session-level metadata (id, timestamp, instructions)
                if not earliest_ts and "timestamp" in entry and "id" in entry:
                    ts_ms = parse_iso_timestamp(entry["timestamp"])
                    if ts_ms:
                        earliest_ts = ts_ms
                    # Prefer the entry's id over the filename
                    entry_id = entry.get("id", "")
                    if entry_id and session_id.startswith("rollout-"):
                        session_id = entry_id

                # Extract cwd from <environment_context> blocks
                if not project:
                    content = entry.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                text = block.get("text", "")
                                if "Current working directory:" in text:
                                    cwd_match = re.search(
                                        r"Current working directory:\s*(.+)", text
                                    )
                                    if cwd_match:
                                        project = cwd_match.group(1).strip()

                # Parse timestamp
                ts_raw = entry.get("timestamp")
                if ts_raw:
                    ts_ms = parse_iso_timestamp(ts_raw)
                    if ts_ms and (earliest_ts is None or ts_ms < earliest_ts):
                        earliest_ts = ts_ms

                # Determine role
                role = entry.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                # Extract text content (Codex uses content directly, not wrapped in message)
                content = entry.get("content", "")
                text = extract_text(content)

                # Skip system/instruction blocks injected as user messages
                if text and "<user_instructions>" in text:
                    continue
                if text and "<environment_context>" in text:
                    continue

                if text:
                    messages.append((role, text))

    except (OSError, PermissionError) as e:
        print(f"Warning: skipping {path}: {e}", file=sys.stderr)
        return None

    if not slug:
        short_id = uuid_match.group(1)[:8] if uuid_match else session_id[:8]
        slug = f"{date_slug}-{short_id}" if date_slug else short_id

    metadata = {
        "session_id": session_id,
        "source": "codex",
        "project": project or "",
        "slug": slug,
        "timestamp": earliest_ts or 0,
    }
    return metadata, messages


# — Indexing ———————————————————————————————————————————————————————————————

def index_sessions(conn, force=False):
    """Scan and index new/changed session files from all sources."""
    if force:
        conn.executescript("""
            DELETE FROM sessions;
            DELETE FROM messages;
        """)

    # Get existing mtimes
    existing = {}
    try:
        for row in conn.execute("SELECT session_id, mtime FROM sessions"):
            existing[row[0]] = row[1]
    except sqlite3.OperationalError:
        pass

    # Collect files from both sources
    sources = []

    # Claude Code: ~/.claude/projects/**/*.jsonl
    claude_pattern = str(CLAUDE_PROJECTS_DIR / "**" / "*.jsonl")
    for fpath in glob(claude_pattern, recursive=True):
        sources.append((fpath, "claude"))

    # Codex: ~/.codex/sessions/**/*.jsonl
    codex_pattern = str(CODEX_SESSIONS_DIR / "**" / "*.jsonl")
    for fpath in glob(codex_pattern, recursive=True):
        sources.append((fpath, "codex"))

    indexed = 0
    skipped = 0

    for fpath, source in sources:
        fname = Path(fpath).stem
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue

        if not force and fname in existing and existing[fname] == mtime:
            skipped += 1
            continue

        # Remove old data for this session if re-indexing
        if fname in existing:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (fname,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (fname,))

        if source == "claude":
            result = parse_claude_session(fpath)
        else:
            result = parse_codex_session(fpath)

        if result is None:
            continue

        metadata, messages = result

        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, source, project, slug, timestamp, mtime) VALUES (?, ?, ?, ?, ?, ?)",
            (metadata["session_id"], metadata["source"], metadata["project"],
             metadata["slug"], metadata["timestamp"], mtime),
        )

        for role, text in messages:
            conn.execute(
                "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
                (metadata["session_id"], role, text),
            )

        indexed += 1

    conn.commit()

    # Get totals
    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    return indexed, skipped, total_sessions, total_messages


# — Search —————————————————————————————————————————————————————————————————

def search(conn, query, project=None, days=None, source=None, limit=10):
    """Search indexed sessions."""
    # FTS5 auxiliary functions (bm25, snippet) don't work with GROUP BY.
    # Use a subquery to get the best-ranking rowid per session, then fetch snippets.
    fts_params = [query]
    session_filter = ""

    if project or days or source:
        subconds = []
        if project:
            subconds.append("s2.project LIKE ? || '%'")
            fts_params.append(project)
        if days:
            cutoff = int((time.time() - days * 86400) * 1000)
            subconds.append("s2.timestamp >= ?")
            fts_params.append(cutoff)
        if source:
            subconds.append("s2.source = ?")
            fts_params.append(source)
        session_filter = (
            " AND session_id IN "
            "(SELECT s2.session_id FROM sessions s2 WHERE " + " AND ".join(subconds) + ")"
        )

    fts_params.append(limit)

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
        print(f"Search error: {e}", file=sys.stderr)
        return []

    results = []
    for session_id, rank in ranked:
        # Get session metadata
        meta = conn.execute(
            "SELECT source, project, slug, timestamp FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not meta:
            continue

        # Get snippet from the best-matching row
        snippet_row = conn.execute(
            "SELECT snippet(messages, 2, '**', '**', '...', 20) FROM messages WHERE messages MATCH ? AND session_id = ? LIMIT 1",
            (query, session_id),
        ).fetchone()
        excerpt = snippet_row[0] if snippet_row else ""

        results.append((session_id, meta[0], meta[1], meta[2], meta[3], excerpt, rank))

    return results


def format_timestamp(ts_ms):
    """Format millisecond timestamp to date string."""
    if not ts_ms:
        return "unknown"
    try:
        ts = float(ts_ms) / 1000  # epoch ms to seconds
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError, TypeError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Search past Claude Code and Codex sessions")
    parser.add_argument("query", help="Search query (FTS5 syntax: quotes for phrases, AND/OR/NOT)")
    parser.add_argument("--project", help="Filter to sessions from a specific project path (prefix match)")
    parser.add_argument("--days", type=int, help="Only sessions from last N days")
    parser.add_argument("--source", choices=["claude", "codex"], help="Filter by source (claude or codex)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--reindex", action="store_true", help="Force full rebuild of the index")

    args = parser.parse_args()

    migrate_db_location()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    create_schema(conn)
    migrate_schema(conn)

    # Index
    t0 = time.time()
    indexed, skipped, total_sessions, total_messages = index_sessions(conn, force=args.reindex)
    index_time = time.time() - t0

    if indexed > 0:
        print(f"Indexed {indexed} sessions in {index_time:.1f}s", file=sys.stderr)

    # Search
    results = search(conn, args.query, project=args.project, days=args.days, source=args.source, limit=args.limit)

    if not results:
        print("No matching sessions found.")
        conn.close()
        return

    print(f"Found {len(results)} sessions (index: {total_sessions} sessions, {total_messages} messages):\n")

    for i, (session_id, source, project, slug, timestamp, excerpt, rank) in enumerate(results, 1):
        date = format_timestamp(timestamp)
        src_tag = f"[{source}]" if source else ""
        proj_name = Path(project).name if project else "unknown"
        print(f"[{i}] {date} | {slug} | {proj_name} {src_tag}")
        if project:
            print(f"    {project}")
        print(f"    ID: {session_id}")
        if excerpt:
            # Clean up excerpt for display
            excerpt_clean = excerpt.replace("\n", " ").strip()
            if len(excerpt_clean) > 200:
                excerpt_clean = excerpt_clean[:200] + "..."
            print(f"    > {excerpt_clean}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
