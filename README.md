# recall

Search past Claude Code sessions with full-text search. Builds a SQLite FTS5 index over `~/.claude/projects/` JSONL files with BM25 ranking, Porter stemming, and incremental updates.

## Install

```bash
npx @anthropic-ai/claude-code skills add arjunkmrm/recall
```

Then use `/recall` in Claude Code or ask "find a past session about X".

## Usage

```bash
python3 ~/.claude/skills/recall/scripts/recall.py QUERY [--project PATH] [--days N] [--limit N] [--reindex]
```

### Examples

```bash
# Keyword search
python3 ~/.claude/skills/recall/scripts/recall.py "bufferStore"

# Phrase search
python3 ~/.claude/skills/recall/scripts/recall.py '"ACP protocol"'

# Boolean
python3 ~/.claude/skills/recall/scripts/recall.py "rust AND async"

# Prefix
python3 ~/.claude/skills/recall/scripts/recall.py "buffer*"

# Filter by project and recency
python3 ~/.claude/skills/recall/scripts/recall.py "state machine" --project ~/my-project --days 7

# Force reindex
python3 ~/.claude/skills/recall/scripts/recall.py --reindex "test"
```

## How it works

- Scans `~/.claude/projects/**/*.jsonl` and indexes user/assistant messages into a SQLite FTS5 database at `~/.claude/recall.db`
- First run indexes all sessions (a few seconds); subsequent runs only process new/modified files
- Skips tool_use, tool_result, thinking, and image blocks
- Returns results ranked by BM25 with highlighted excerpts
- No dependencies — Python 3.9+ stdlib only (sqlite3, json, argparse)

## Query syntax

| Pattern | Example | Description |
|---------|---------|-------------|
| Words | `bufferStore` | Stemmed match ("discussing" → "discuss") |
| Phrases | `"ACP protocol"` | Exact phrase |
| Boolean | `rust AND async` | AND, OR, NOT |
| Prefix | `buffer*` | Wildcard suffix |
| Combined | `"state machine" AND test` | Mix freely |
