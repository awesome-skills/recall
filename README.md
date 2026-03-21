<div align="center">

# Memex

**Your AI conversations, instantly searchable.**

A local full-text search engine for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://openai.com/index/codex/) session history.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)]()
[![CI](https://github.com/awesome-skills/memex/actions/workflows/tests.yml/badge.svg)](https://github.com/awesome-skills/memex/actions)

[English](#english) · [中文](#中文)

</div>

---

<a id="english"></a>

## Why Memex?

You've had hundreds of conversations with Claude Code and Codex. Somewhere in that history is the exact discussion about that database migration, that tricky regex, or that architecture decision — but good luck finding it by scrolling through files.

Memex builds a local search index over all your past sessions. Find any conversation in seconds, then resume it right where you left off.

### Features

- **Full-text search** — BM25 ranking with FTS5, stemming, phrase/boolean/prefix queries
- **Smart ranking** — Blends relevance (80%) with recency (20%, 30-day half-life)
- **Incremental indexing** — Two-level mtime checkpointing; milliseconds on subsequent runs
- **CJK support** — Automatic substring fallback for Chinese, Japanese, and Korean queries
- **Session summaries** — First meaningful message stored per session for quick scanning
- **Resume anywhere** — Each result includes a ready-to-run resume command
- **Zero dependencies** — Pure Python 3.9+ stdlib, single SQLite file

### Install

Provide this repository URL to your agent — the skill installs automatically. Then use `/memex` or ask naturally:

> *"find the session where we discussed WebSocket reconnection"*

### Quick start

```bash
# Search across all sessions
recall.py "WebSocket reconnect"

# Browse recent sessions with summaries
recall.py --list

# Filter by keyword, source, project, and time range
recall.py "auth bug" --source claude --project ~/work/api --days 7

# Paginate through results
recall.py --list --limit 10 --offset 10

# Include subagent sessions (hidden by default)
recall.py --list --include-subagents

# Machine-readable JSON output
recall.py --json "deploy"

# Health check & auto-fix
recall.py --doctor --fix
```

### Search syntax

Queries use [FTS5 full-text query syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

| Pattern | Example | Description |
|:--------|:--------|:------------|
| Keyword | `websocket` | Stemmed matching — *discuss* matches *discussing* |
| Phrase | `"state machine"` | Exact phrase |
| Boolean | `rust AND async` | Both terms required |
| Negation | `auth NOT oauth` | Exclude a term |
| Prefix | `deploy*` | Matches deploy, deployment, deploying... |
| Combined | `"error handling" AND retry` | Mix any of the above |

> **Query tolerance** — Tokens with special characters (e.g. `local-command-caveat`) are auto-quoted. If FTS fails, results fall back to LIKE substring search.

### Resume a session

Each result includes a session ID:

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
  QUERY                     FTS5 search query (optional with --list)

Options:
  --list                    List sessions by recency; QUERY filters the list
  --project PATH            Match exact project path or child paths
  --days N                  Only sessions from the last N days
  --source claude|codex     Filter by source
  --limit N                 Max results (default: 10)
  --offset N                Skip first N results (for pagination)
  --summary-len N           Max summary length in output (default: 120)
  --no-summary              Hide per-session summary lines
  --include-subagents       Include subagent sessions in results
  --json                    Machine-readable JSON output
  --reindex                 Force full index rebuild
  --version                 Show version/schema/commit metadata and exit
  --doctor                  Run local health checks and exit
  --fix                     Apply safe auto-fixes (requires --doctor)
```

### How it works

```
~/.claude/projects/**/*.jsonl ─┐
                                ├──▶ Index ──▶ ~/.memex.db
~/.codex/sessions/**/*.jsonl ──┘       │
                                       ├─ dir-level mtime checkpoint
                                       ├─ per-file mtime tracking
                                       ├─ orphan cleanup
                                       └─ summary extraction
                                              │
Query ──▶ FTS5 MATCH ──▶ BM25 rank ──▶ recency boost ──▶ results
              │               │          (30-day half-life)
         Porter stemming   CJK fallback
         + unicode61       + LIKE fallback
```

| Aspect | Detail |
|:-------|:-------|
| **Storage** | `~/.memex.db` — SQLite FTS5 + WAL, permissions `0600` |
| **Indexing** | Two-level: dir mtime checkpoint skips unchanged dirs, then per-file mtime |
| **Ranking** | BM25 (80%) + recency boost (20%, 30-day half-life) |
| **Content** | User & assistant text only — system noise, tools, thinking, images filtered |
| **Summaries** | First meaningful user message per session, shown in `--list` and search |
| **Subagents** | Indexed with parent session ID; hidden by default |
| **Dependencies** | Zero — Python 3.9+ stdlib only |
| **Migration** | Auto-migrates from legacy `~/.claude/recall.db` and `~/.recall.db` |
| **Tests** | 40+ test classes, regression suite + GitHub Actions CI |

---

<a id="中文"></a>

## 中文

### 为什么选择 Memex？

你和 Claude Code、Codex 有过无数次对话。那次关于数据库迁移的讨论、那个棘手的正则、那个架构决策——都埋在历史记录的某个角落。

Memex 为所有历史会话建立本地全文搜索索引，几秒内找到任何对话，然后直接恢复继续。

### 特性

- **全文搜索** — 基于 FTS5 的 BM25 排序，支持词干匹配、短语/布尔/前缀查询
- **智能排序** — 融合相关性（80%）与时间衰减（20%，30 天半衰期）
- **增量索引** — 两级 mtime 检查点，后续运行毫秒级完成
- **中日韩支持** — 自动回退子串匹配，优化 CJK 查询召回率
- **会话摘要** — 每个会话存储首条有意义的用户消息，快速浏览
- **一键恢复** — 每条结果附带可直接执行的恢复命令
- **零依赖** — 纯 Python 3.9+ 标准库，单个 SQLite 文件

### 安装

把本仓库地址提供给 agent 即可自动安装，随后使用 `/memex` 或自然语言提问：

> *"找一下之前讨论 WebSocket 重连的会话"*

### 快速上手

```bash
# 全文搜索
recall.py "WebSocket 重连"

# 浏览最近的会话（含摘要）
recall.py --list

# 组合过滤：关键词 + 来源 + 项目 + 时间
recall.py "认证 bug" --source claude --project ~/work/api --days 7

# 翻页
recall.py --list --limit 10 --offset 10

# 显示子代理会话（默认隐藏）
recall.py --list --include-subagents

# JSON 输出
recall.py --json "部署"

# 健康检查 & 自动修复
recall.py --doctor --fix
```

### 搜索语法

使用 [FTS5 全文查询语法](https://www.sqlite.org/fts5.html#full_text_query_syntax)：

| 模式 | 示例 | 说明 |
|:-----|:-----|:-----|
| 关键词 | `websocket` | 词干匹配 — *discuss* 可匹配 *discussing* |
| 短语 | `"state machine"` | 精确短语匹配 |
| 布尔 | `rust AND async` | 同时包含两个词 |
| 排除 | `auth NOT oauth` | 排除指定词 |
| 前缀 | `deploy*` | 匹配 deploy、deployment、deploying... |
| 组合 | `"error handling" AND retry` | 以上任意组合 |

> **查询容错** — 含特殊字符的词会自动加引号；FTS 完全失败时回退到 LIKE 子串搜索。

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
  QUERY                     FTS5 搜索词（--list 模式下可选）

选项:
  --list                    按时间列出会话；可选 QUERY 过滤
  --project PATH            精确匹配项目路径或其子路径
  --days N                  仅显示最近 N 天的会话
  --source claude|codex     按来源过滤
  --limit N                 最大结果数（默认: 10）
  --offset N                跳过前 N 条结果（翻页）
  --summary-len N           摘要最大长度（默认: 120）
  --no-summary              隐藏每条会话摘要
  --include-subagents       显示子代理会话
  --json                    输出机器可读的 JSON
  --reindex                 强制重建索引
  --version                 显示版本/Schema/提交信息并退出
  --doctor                  运行本地健康检查并退出
  --fix                     执行安全自动修复（需与 --doctor 一起使用）
```

---

<div align="center">

### Acknowledgments / 致谢

Originally forked from [recall](https://github.com/arjunkmrm/recall) by [Arjun Kumar](https://github.com/arjunkmrm).

基于 [Arjun Kumar](https://github.com/arjunkmrm) 的 [recall](https://github.com/arjunkmrm/recall) 项目 fork 并重构。

### Contributing / 贡献

Found a bug or have an idea? / 发现 bug 或有新想法？

[Open an issue](https://github.com/awesome-skills/memex/issues) · [Submit a PR](https://github.com/awesome-skills/memex/pulls)

[MIT License](LICENSE)

</div>
