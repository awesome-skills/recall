# recall

Search and resume past [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://openai.com/index/codex/) conversations — right from your terminal.

Builds a local SQLite FTS5 full-text index over all your session transcripts, so you can instantly find that conversation from last week where you debugged the auth flow, or the one where you designed the database schema.

## Install

```bash
npx skills add arjunkmrm/recall
```

Restart your agent, then use `/recall` or just ask naturally:

> "find the session where we discussed WebSocket reconnection"

## Quick start

```bash
# Search across all sessions
recall.py "WebSocket reconnect"

# Browse recent sessions
recall.py --list

# List sessions mentioning a topic, sorted by recency
recall.py --list "database migration"

# Filter by source, project, or time window
recall.py "auth bug" --source claude --project ~/work/api --days 7

# Machine-readable output
recall.py --json "deploy"
```

## Search syntax

Queries use [FTS5 syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

| Pattern | Example | Matches |
|---------|---------|---------|
| Keyword | `websocket` | Stemmed variants (discuss → discussing) |
| Phrase | `"state machine"` | Exact phrase |
| Boolean | `rust AND async` | Both terms present |
| Negation | `auth NOT oauth` | Exclude term |
| Prefix | `deploy*` | deploy, deployment, deploying... |
| Combined | `"error handling" AND retry` | Phrase + keyword |

CJK (Chinese/Japanese/Korean) queries automatically fall back to substring matching when FTS recall is sparse.

## Resuming a session

Each result shows the session ID. Use it to pick up where you left off:

```bash
# Claude Code
cd /path/to/project
claude --resume SESSION_ID

# Codex
cd /path/to/project
codex resume SESSION_ID
```

To read a full transcript:

```bash
read_session.py /path/to/session.jsonl            # JSON output
read_session.py /path/to/session.jsonl --pretty    # Human-readable
```

## How it works

```
~/.claude/projects/**/*.jsonl ─┐
                                ├──▶ Index ──▶ ~/.recall.db
~/.codex/sessions/**/*.jsonl ──┘       │
                                       ├─ incremental (mtime-based)
                                       ├─ orphan cleanup
                                       └─ timestamp backfill
                                              │
Query ──▶ FTS5 MATCH ──▶ BM25 rank ──▶ recency boost ──▶ results
              │               │          (30-day half-life,
              │               │           20% weight)
              │               │
         Porter stemming    snippet
         + unicode61        extraction
```

**Indexing** — On first run, all `.jsonl` session files are parsed and indexed (a few seconds for thousands of sessions). Subsequent runs are incremental: only new or modified files are re-processed. Orphaned DB rows (deleted source files) are automatically pruned.

**Ranking** — Results are ranked by BM25 relevance with a slight recency bias. Recent sessions get up to a 20% boost that decays exponentially with a 30-day half-life. This keeps results relevant while surfacing fresh conversations.

**What gets indexed** — Only user and assistant message text. Tool calls, tool results, thinking blocks, images, and system instructions are skipped.

**Subagents** — Claude Code subagent transcripts are indexed and tagged with their parent session ID, so you can trace them back to the main conversation.

## CLI reference

```
recall.py [QUERY] [OPTIONS]

Arguments:
  QUERY                   Search query (FTS5 syntax); optional with --list

Options:
  --list                  List sessions by recency; QUERY filters the list
  --project PATH          Filter to exact project path or child paths
  --days N                Only sessions from the last N days
  --source claude|codex   Filter by session source
  --limit N               Max results (default: 10)
  --json                  Output machine-readable JSON
  --reindex               Force full index rebuild
```

## Details

- **Storage**: `~/.recall.db` (SQLite FTS5 + WAL mode, file permissions `0600`)
- **Dependencies**: None — Python 3.9+ stdlib only
- **Auto-migration**: DB moved from legacy `~/.claude/recall.db` on first run
- **Schema upgrades**: Columns added automatically when upgrading from older versions

## Contributing

Found a bug or have an idea? [Open an issue](https://github.com/awesome-skills/recall/issues) or submit a PR — contributions welcome!

## License

[MIT](LICENSE)
