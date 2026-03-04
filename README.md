<div align="center">

# 🔍 recall

**Search and resume past conversations — right from your terminal.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)]()

[English](#english) · [中文](#中文)

<br>

```
~/.claude/projects/**/*.jsonl ─┐
                                ├──▶ Index ──▶ ~/.recall.db
~/.codex/sessions/**/*.jsonl ──┘       │
                                       ├─ incremental (mtime)
                                       ├─ orphan cleanup
                                       └─ timestamp backfill
                                              │
Query ──▶ FTS5 MATCH ──▶ BM25 rank ──▶ recency boost ──▶ results
              │                          (30-day half-life)
         Porter stemming
         + unicode61
```

</div>

---

<a id="english"></a>

## English

A skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://openai.com/index/codex/) that builds a local full-text search index over all your past session transcripts. Find any conversation in seconds.

### Install

```bash
npx skills add arjunkmrm/recall
```

Restart your agent, then use `/recall` or ask naturally:

> *"find the session where we discussed WebSocket reconnection"*

### Quick start

```bash
# Search across all sessions
recall.py "WebSocket reconnect"

# Browse recent sessions
recall.py --list

# List + filter by keyword (sorted by recency)
recall.py --list "database migration"

# Combine filters
recall.py "auth bug" --source claude --project ~/work/api --days 7

# Machine-readable output
recall.py --json "deploy"
```

### Search syntax

Queries use [FTS5 full-text query syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

| Pattern | Example | Description |
|:--------|:--------|:------------|
| Keyword | `websocket` | Stemmed matching — *discuss* matches *discussing* |
| Phrase | `"state machine"` | Exact phrase |
| Boolean | `rust AND async` | Both terms required |
| Negation | `auth NOT oauth` | Exclude a term |
| Prefix | `deploy*` | Matches deploy, deployment, deploying… |
| Combined | `"error handling" AND retry` | Mix any of the above |

> **CJK support** — Chinese / Japanese / Korean queries automatically fall back to substring matching when FTS recall is sparse.

### Resume a session

Each result includes a session ID you can use to pick up where you left off:

```bash
# Claude Code
cd /path/to/project && claude --resume SESSION_ID

# Codex
cd /path/to/project && codex resume SESSION_ID
```

Read a full transcript:

```bash
read_session.py /path/to/session.jsonl            # JSON
read_session.py /path/to/session.jsonl --pretty    # human-readable
```

### CLI reference

```
recall.py [QUERY] [OPTIONS]

Positional:
  QUERY                   FTS5 search query (optional with --list)

Options:
  --list                  List sessions by recency; QUERY filters the list
  --project PATH          Match exact project path or child paths
  --days N                Only sessions from the last N days
  --source claude|codex   Filter by source
  --limit N               Max results (default: 10)
  --json                  Machine-readable JSON output
  --reindex               Force full index rebuild
```

### Under the hood

| Aspect | Detail |
|:-------|:-------|
| **Storage** | `~/.recall.db` — SQLite FTS5 + WAL, permissions `0600` |
| **Indexing** | Incremental via mtime; first run ~7 s for 2 000 sessions |
| **Ranking** | BM25 (80%) + recency boost (20%, 30-day half-life) |
| **Content** | User & assistant text only — skips tools, thinking, images |
| **Subagents** | Indexed and tagged with parent session ID |
| **Dependencies** | Zero — Python 3.9+ stdlib only |
| **Migration** | Auto-migrates from legacy `~/.claude/recall.db` |

---

<a id="中文"></a>

## 中文

一个 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 和 [Codex](https://openai.com/index/codex/) 的 skill，在本地为所有历史会话建立全文搜索索引，几秒内找到任何一段对话。

### 安装

```bash
npx skills add arjunkmrm/recall
```

重启 agent 后使用 `/recall`，或者直接自然语言提问：

> *"找一下之前讨论 WebSocket 重连的会话"*

### 快速上手

```bash
# 全文搜索
recall.py "WebSocket 重连"

# 浏览最近的会话
recall.py --list

# 列出 + 关键词过滤（按时间倒序）
recall.py --list "数据库迁移"

# 组合过滤条件
recall.py "认证 bug" --source claude --project ~/work/api --days 7

# 输出 JSON（方便脚本消费）
recall.py --json "部署"
```

### 搜索语法

使用 [FTS5 全文查询语法](https://www.sqlite.org/fts5.html#full_text_query_syntax)：

| 模式 | 示例 | 说明 |
|:-----|:-----|:-----|
| 关键词 | `websocket` | 词干匹配 — *discuss* 可匹配 *discussing* |
| 短语 | `"state machine"` | 精确短语匹配 |
| 布尔 | `rust AND async` | 同时包含两个词 |
| 排除 | `auth NOT oauth` | 排除指定词 |
| 前缀 | `deploy*` | 匹配 deploy、deployment、deploying… |
| 组合 | `"error handling" AND retry` | 以上任意组合 |

> **中日韩支持** — 当 FTS 召回率不足时，中文 / 日文 / 韩文查询会自动回退到子串匹配。

### 恢复会话

搜索结果包含 session ID，可以直接恢复：

```bash
# Claude Code
cd /path/to/project && claude --resume SESSION_ID

# Codex
cd /path/to/project && codex resume SESSION_ID
```

查看完整对话记录：

```bash
read_session.py /path/to/session.jsonl            # JSON 格式
read_session.py /path/to/session.jsonl --pretty    # 可读格式
```

### 命令参考

```
recall.py [QUERY] [选项]

位置参数:
  QUERY                   FTS5 搜索词（--list 模式下可选）

选项:
  --list                  按时间列出会话；可选 QUERY 过滤
  --project PATH          精确匹配项目路径或其子路径
  --days N                仅显示最近 N 天的会话
  --source claude|codex   按来源过滤
  --limit N               最大结果数（默认: 10）
  --json                  输出机器可读的 JSON
  --reindex               强制重建索引
```

### 技术细节

| 项目 | 说明 |
|:-----|:-----|
| **存储** | `~/.recall.db` — SQLite FTS5 + WAL 模式，权限 `0600` |
| **索引** | 基于 mtime 增量更新；首次 ~7 秒 / 2000 个会话 |
| **排序** | BM25（80%）+ 时间衰减（20%，30 天半衰期） |
| **内容** | 仅索引用户和助手的文本 — 跳过工具调用、思考过程、图片 |
| **子代理** | 会被索引并标注父会话 ID |
| **依赖** | 零依赖 — 仅使用 Python 3.9+ 标准库 |
| **迁移** | 自动从旧路径 `~/.claude/recall.db` 迁移 |

---

<div align="center">

### Contributing / 贡献

Found a bug or have an idea? / 发现 bug 或有新想法？

[Open an issue](https://github.com/awesome-skills/recall/issues) · [Submit a PR](https://github.com/awesome-skills/recall/pulls)

[MIT License](LICENSE)

</div>
