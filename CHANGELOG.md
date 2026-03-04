# Changelog

## Unreleased

### Session overview
- Store first meaningful user message as `summary` in sessions table
- Show summary line in `--list` and search output — no more guessing what a session was about
- Timestamps now display `YYYY-MM-DD HH:MM` for easier identification

### Subagent handling
- Index `is_subagent` and `parent_session_id` in sessions table
- Default: hide subagent sessions from `--list` and search results
- Add `--include-subagents` flag to show them when needed

### Query robustness
- Auto-quote tokens with special characters (e.g. `local-command-caveat`) to prevent FTS5 syntax errors
- On FTS query failure, fall back to LIKE substring search instead of returning empty results
- Apply same `is_noise()` filtering to Claude parser (was Codex-only); filters `<local-command-caveat>`, `<system-reminder>`, `<command-name>`, etc.

### Data quality
- Infer project path from Claude file path when `cwd` is missing (e.g. `-Users-admin-work` → `/Users/admin/work`)
- Add `--offset` for result pagination

### Previous (carried from earlier PRs)
- Add `--list` mode to list sessions by recency without a full-text query
- Allow `--list` to take an optional query filter
- Add `--json` output mode for machine-readable search/list results
- Add orphan cleanup during indexing (removes DB rows whose source JSONL no longer exists)
- Add CJK substring fallback for simple Chinese/Japanese/Korean queries to improve recall
- Fix `--project` matching to include exact path or child paths only (avoid sibling false positives)
- Escape `%`/`_` in CJK fallback LIKE terms
- Use session timestamp (instead of message rowid) to order CJK fallback candidates
- Backfill missing session timestamps from file mtime during indexing
- Wrap indexing in `BEGIN IMMEDIATE` transaction for safer concurrent writes
- Mark Claude subagent transcripts with parent session IDs in text output
- Avoid unnecessary FTS automerge writes when no files changed
- Share message text extraction/skip markers across scripts

## 0.2.2

- Add slight recency bias to search ranking
- Blend BM25 relevance with time-decay boost (half-life: 30 days, 20% weight)
- Over-fetch 3x candidates before re-ranking to avoid cutting off recent results

## 0.2.1

- Batch message inserts with `executemany`
- Disable FTS5 automerge during bulk insert, optimize after
- Add MIT license

### Reindex benchmarks (1939 sessions, ~50K messages)

| Version | Time |
|---|---|
| 0.2.0 | ~10.4s |
| 0.2.1 | ~7.4s |

## 0.2.0

- Add Codex session support — indexes both `~/.claude/projects/` and `~/.codex/sessions/`
- Unified search across Claude Code and Codex sessions
- Results tagged with `[claude]` or `[codex]` to show origin
- New `--source claude|codex` flag to filter by tool
- DB moved from `~/.claude/recall.db` to `~/.recall.db` (auto-migrated on first run)
- Schema migration adds `source` and `file_path` columns to existing databases
- Results now show full `File:` path — works with subagent sessions nested in subdirectories
- New `read_session.py` script for reading transcripts (auto-detects format, JSON by default, `--pretty` for human-readable)
- Concise `extract_text` using list comprehension and `TEXT_BLOCK_TYPES` set

### Backward compatibility
- DB auto-migrated from `~/.claude/recall.db` to `~/.recall.db` on first run
- `source` column defaults to `"claude"` for existing rows
- If results are missing `File:` paths, run `--reindex` to backfill

## 0.1.0

- Initial release
- FTS5 full-text search over Claude Code sessions
- BM25 ranking with snippet extraction
- Incremental indexing via file mtime tracking
- `--project`, `--days`, `--limit`, `--reindex` filters
