<div align="center">

# 🔍 memex

**Search and resume past conversations — right from your terminal.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)]()

[English](#english) · [中文](#中文)

<br>

```
~/.claude/projects/**/*.jsonl ─┐
                                ├──▶ Index ──▶ ~/.memex.db
~/.codex/sessions/**/*.jsonl ──┘       │
                                       ├─ dir-level mtime checkpoint
                                       ├─ incremental per-file mtime
                                       ├─ orphan cleanup
                                       └─ summary extraction
                                              │
Query ──▶ FTS5 MATCH ──▶ BM25 rank ──▶ recency boost ──▶ results
              │               │          (30-day half-life)
         Porter stemming   CJK fallback
         + unicode61       + LIKE fallback
```

</div>

---

<a id="english"></a>

## English

A skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://openai.com/index/codex/) that builds a local full-text search index over all your past session transcripts. Find any conversation in seconds.

Provide this repository URL to your agent to install the skill automatically, then use `/memex` or ask naturally:

> *"find the session where we discussed WebSocket reconnection"*

### Quick start

```bash
# Search across all sessions
memex.py "WebSocket reconnect"

# Browse recent sessions (with one-line summaries)
memex.py --list

# List + filter by keyword (sorted by recency)
memex.py --list "database migration"

# Combine filters
memex.py "auth bug" --source claude --project ~/work/api --days 7

# Paginate results
memex.py --list --limit 10 --offset 10

# Include subagent sessions (hidden by default)
memex.py --list --include-subagents

# Hide summary lines
memex.py --list --no-summary

# Customize summary length
memex.py --list --summary-len 80

# Show installed version/build metadata
memex.py --version

# Run local health checks
memex.py --doctor

# Run doctor with safe auto-fixes
memex.py --doctor --fix

# Machine-readable output
memex.py --json "deploy"
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

> **Query tolerance** — Tokens with special characters (e.g. `local-command-caveat`) are auto-quoted. If FTS fails entirely, results fall back to LIKE substring search.

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

JSON output includes a `resume_command` field per result, ready to run.

### CLI reference

```
memex.py [QUERY] [OPTIONS]

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

### Under the hood

| Aspect | Detail |
|:-------|:-------|
| **Storage** | `~/.memex.db` — SQLite FTS5 + WAL, permissions `0600` |
| **Indexing** | Two-level: dir mtime checkpoint skips unchanged dirs, then per-file mtime |
| **Ranking** | BM25 (80%) + recency boost (20%, 30-day half-life) |
| **Content** | User & assistant text only — system noise, tools, thinking, images filtered |
| **Summaries** | First meaningful user message stored per session, shown in `--list` and search |
| **Subagents** | Indexed with parent session ID; hidden by default, `--include-subagents` to show |
| **Dependencies** | Zero — Python 3.9+ stdlib only |
| **Migration** | Auto-migrates from legacy `~/.claude/recall.db` |
| **Tests** | Regression tests (unittest) + GitHub Actions CI |

---

<a id="中文"></a>

## 中文

一个 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 和 [Codex](https://openai.com/index/codex/) 的 skill，在本地为所有历史会话建立全文搜索索引，几秒内找到任何一段对话。

把本仓库地址提供给 agent 后可自动安装，随后使用 `/memex`，或者直接自然语言提问：

> *"找一下之前讨论 WebSocket 重连的会话"*

### 快速上手

```bash
# 全文搜索
memex.py "WebSocket 重连"

# 浏览最近的会话（含一行摘要）
memex.py --list

# 列出 + 关键词过滤（按时间倒序）
memex.py --list "数据库迁移"

# 组合过滤条件
memex.py "认证 bug" --source claude --project ~/work/api --days 7

# 翻页
memex.py --list --limit 10 --offset 10

# 显示子代理会话（默认隐藏）
memex.py --list --include-subagents

# 隐藏摘要行
memex.py --list --no-summary

# 自定义摘要长度
memex.py --list --summary-len 80

# 查看安装版本/构建信息
memex.py --version

# 运行本地健康检查
memex.py --doctor

# 运行健康检查并尝试安全自动修复
memex.py --doctor --fix

# 输出 JSON（方便脚本消费）
memex.py --json "部署"
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

> **查询容错** — 含特殊字符的词（如 `local-command-caveat`）会自动加引号；FTS 完全失败时回退到 LIKE 子串搜索。

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

JSON 输出中每条结果都带有可直接执行的 `resume_command` 字段。

### 命令参考

```
memex.py [QUERY] [选项]

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

### 技术细节

| 项目 | 说明 |
|:-----|:-----|
| **存储** | `~/.memex.db` — SQLite FTS5 + WAL 模式，权限 `0600` |
| **索引** | 两级：目录 mtime 检查点跳过未变更目录，再按文件 mtime 增量更新 |
| **排序** | BM25（80%）+ 时间衰减（20%，30 天半衰期） |
| **内容** | 仅索引用户和助手的文本 — 过滤系统噪音、工具调用、思考过程、图片 |
| **摘要** | 每个会话存储首条有意义的用户消息，在 `--list` 和搜索结果中展示 |
| **子代理** | 索引并标注父会话 ID；默认隐藏，`--include-subagents` 显示 |
| **依赖** | 零依赖 — 仅使用 Python 3.9+ 标准库 |
| **迁移** | 自动从旧路径 `~/.claude/recall.db` 迁移 |
| **测试** | 回归测试（unittest）+ GitHub Actions CI |

---

<div align="center">

### Contributing / 贡献

Found a bug or have an idea? / 发现 bug 或有新想法？

[Open an issue](https://github.com/awesome-skills/memex/issues) · [Submit a PR](https://github.com/awesome-skills/memex/pulls)

Release process: see [RELEASE.md](RELEASE.md)

[MIT License](LICENSE)

</div>
