<div align="center">

<img src="https://img.shields.io/badge/Memex-Search%20Your%20AI%20Conversations-8A2BE2?style=for-the-badge&logo=searchengin&logoColor=white" alt="Memex" />

<br><br>

<strong>Your AI conversations, instantly searchable.</strong>

<p>A local full-text search engine for <img src="https://img.shields.io/badge/Claude%20Code-D97706?logo=anthropic&logoColor=white" alt="Claude Code" valign="middle" /> and <img src="https://img.shields.io/badge/Codex-412991?logo=openai&logoColor=white" alt="Codex" valign="middle" /> session history.</p>

<br>

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen?style=flat-square)]()
[![Tests](https://img.shields.io/badge/tests-passing-success?style=flat-square&logo=githubactions&logoColor=white)](https://github.com/awesome-skills/memex/actions)
[![CI](https://github.com/awesome-skills/memex/actions/workflows/tests.yml/badge.svg)](https://github.com/awesome-skills/memex/actions)

[English](#english) · [中文](#中文)

</div>

<br>

---

<a id="english"></a>

## Why Memex?

You've had hundreds of conversations with <img src="https://img.shields.io/badge/Claude%20Code-D97706?logo=anthropic&logoColor=white" alt="Claude Code" valign="middle" /> and <img src="https://img.shields.io/badge/Codex-412991?logo=openai&logoColor=white" alt="Codex" valign="middle" />. Somewhere in that history is the exact discussion about that database migration, that tricky regex, or that architecture decision — but good luck finding it by scrolling through files.

Memex builds a local search index over all your past sessions. Find any conversation in seconds, then resume it right where you left off.

### Features

| | Feature | Description |
|:--|:--------|:------------|
| :mag: | **Full-text search** | BM25 ranking with FTS5, stemming, phrase/boolean/prefix queries |
| :chart_with_upwards_trend: | **Smart ranking** | Blends relevance (80%) with recency (20%, 30-day half-life) |
| :zap: | **Incremental indexing** | Two-level mtime checkpointing; milliseconds on subsequent runs |
| :earth_asia: | **CJK support** | Automatic substring fallback for Chinese, Japanese, and Korean queries |
| :bookmark: | **Session summaries** | First meaningful message stored per session for quick scanning |
| :arrow_forward: | **Resume anywhere** | Each result includes a ready-to-run resume command |
| :package: | **Zero dependencies** | Pure Python 3.9+ stdlib, single SQLite file |

### Install

#### Option 1 — Ask your agent (recommended)

Just paste this into Claude Code or Codex:

> Install the memex skill from https://github.com/awesome-skills/memex

Done — the agent should clone the repo and register it in the host tool's skill directory automatically. Then use `/memex` or ask naturally:

> *"find the session where we discussed WebSocket reconnection"*

> **Codex note:** If you install Memex while a Codex session is already running, restart or reopen the session before expecting `/memex` to appear in the loaded skill list.

#### Option 2 — One-line install

Pick the install path that matches your host tool:

**Claude Code — macOS / Linux**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.claude/skills/memex
```

**Claude Code — Windows (PowerShell)**
```powershell
git clone https://github.com/awesome-skills/memex.git "$env:USERPROFILE\.claude\skills\memex"
```

**Codex — macOS / Linux**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.agents/skills/memex
```

**Codex — alternate path used by some setups**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.codex/skills/memex
```

**Codex — Windows (PowerShell)**
```powershell
git clone https://github.com/awesome-skills/memex.git "$env:USERPROFILE\.agents\skills\memex"
```

After installing into a Codex skill directory, start a new Codex session or resume again so the refreshed skill list is loaded.

#### Option 3 — Standalone CLI

Clone anywhere and run the scripts directly — no skill registration needed:

```bash
git clone https://github.com/awesome-skills/memex.git
cd memex

# macOS / Linux
python3 scripts/recall.py --list

# Windows
python scripts/recall.py --list
```

> **Note:** On Windows, use `python` instead of `python3`. Requires Python 3.9+.

### Quick start

```bash
# Search across all sessions
python3 scripts/recall.py "WebSocket reconnect"

# Browse recent sessions with summaries
python3 scripts/recall.py --list

# Filter by keyword, source, project, and time range
python3 scripts/recall.py "auth bug" --source claude --project ~/work/api --days 7

# Paginate through results
python3 scripts/recall.py --list --limit 10 --offset 10

# Include subagent sessions (hidden by default)
python3 scripts/recall.py --list --include-subagents

# Machine-readable JSON output
python3 scripts/recall.py --json "deploy"

# Health check & auto-fix
python3 scripts/recall.py --doctor --fix
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
python3 scripts/read_session.py /path/to/session.jsonl            # JSON
python3 scripts/read_session.py /path/to/session.jsonl --pretty    # human-readable
```

### CLI reference

```
python3 scripts/recall.py [QUERY] [OPTIONS]

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
| **Tests** | unittest suite + GitHub Actions CI |

---

<a id="中文"></a>

## 中文

### 为什么选择 Memex？

你和 <img src="https://img.shields.io/badge/Claude%20Code-D97706?logo=anthropic&logoColor=white" alt="Claude Code" valign="middle" />、<img src="https://img.shields.io/badge/Codex-412991?logo=openai&logoColor=white" alt="Codex" valign="middle" /> 有过无数次对话。那次关于数据库迁移的讨论、那个棘手的正则、那个架构决策——都埋在历史记录的某个角落。

Memex 为所有历史会话建立本地全文搜索索引，几秒内找到任何对话，然后直接恢复继续。

### 特性

| | 特性 | 说明 |
|:--|:-----|:-----|
| :mag: | **全文搜索** | 基于 FTS5 的 BM25 排序，支持词干匹配、短语/布尔/前缀查询 |
| :chart_with_upwards_trend: | **智能排序** | 融合相关性（80%）与时间衰减（20%，30 天半衰期） |
| :zap: | **增量索引** | 两级 mtime 检查点，后续运行毫秒级完成 |
| :earth_asia: | **中日韩支持** | 自动回退子串匹配，优化 CJK 查询召回率 |
| :bookmark: | **会话摘要** | 每个会话存储首条有意义的用户消息，快速浏览 |
| :arrow_forward: | **一键恢复** | 每条结果附带可直接执行的恢复命令 |
| :package: | **零依赖** | 纯 Python 3.9+ 标准库，单个 SQLite 文件 |

### 安装

#### 方式一 — 让 agent 帮你装（推荐）

在 Claude Code 或 Codex 里直接说：

> 安装 memex skill：https://github.com/awesome-skills/memex

agent 会自动 clone 并注册到当前宿主工具对应的 skill 目录，之后用 `/memex` 或自然语言提问：

> *"找一下之前讨论 WebSocket 重连的会话"*

> **Codex 提示：** 如果是在一个已经运行中的 Codex 会话里安装 Memex，需要重开当前会话，新的 skill 列表才会生效。

#### 方式二 — 一行命令

请按宿主工具选择安装路径：

**Claude Code — macOS / Linux**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.claude/skills/memex
```

**Claude Code — Windows (PowerShell)**
```powershell
git clone https://github.com/awesome-skills/memex.git "$env:USERPROFILE\.claude\skills\memex"
```

**Codex — macOS / Linux**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.agents/skills/memex
```

**Codex — 某些环境的备用路径**
```bash
git clone https://github.com/awesome-skills/memex.git ~/.codex/skills/memex
```

**Codex — Windows (PowerShell)**
```powershell
git clone https://github.com/awesome-skills/memex.git "$env:USERPROFILE\.agents\skills\memex"
```

如果安装到了 Codex 的 skill 目录，请重新打开一个 Codex 会话，或重新执行 `codex resume ...`，这样 `/memex` 才会出现在新会话里。

#### 方式三 — 独立 CLI 使用

Clone 到任意目录直接运行，无需注册 skill：

```bash
git clone https://github.com/awesome-skills/memex.git
cd memex

# macOS / Linux
python3 scripts/recall.py --list

# Windows
python scripts/recall.py --list
```

> **注意：** Windows 下用 `python` 而非 `python3`，需要 Python 3.9+。

### 快速上手

```bash
# 全文搜索
python3 scripts/recall.py "WebSocket 重连"

# 浏览最近的会话（含摘要）
python3 scripts/recall.py --list

# 组合过滤：关键词 + 来源 + 项目 + 时间
python3 scripts/recall.py "认证 bug" --source claude --project ~/work/api --days 7

# 翻页
python3 scripts/recall.py --list --limit 10 --offset 10

# 显示子代理会话（默认隐藏）
python3 scripts/recall.py --list --include-subagents

# JSON 输出
python3 scripts/recall.py --json "部署"

# 健康检查 & 自动修复
python3 scripts/recall.py --doctor --fix
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
python3 scripts/read_session.py /path/to/session.jsonl            # JSON 格式
python3 scripts/read_session.py /path/to/session.jsonl --pretty    # 可读格式
```

### 命令参考

```
python3 scripts/recall.py [QUERY] [选项]

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
