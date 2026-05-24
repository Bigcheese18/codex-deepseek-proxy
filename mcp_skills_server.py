"""
Codex Skills MCP Server — wraps dev tools + workflows as MCP tools.
Registers in config.toml [mcp_servers] section.

Tools:
  run_tests    — Run test command, return results
  search_code  — Regex search in codebase
  read_file    — Read file section with line numbers
  list_dir     — List directory contents
  git_diff     — Show staged + unstaged changes
  git_log      — Recent commit log
  code_stats   — Codebase stats: LOC, lang breakdown, file count
  run_shell    — Generic shell command runner
  todo_write   — Create a structured task list for current session
  cache_stats  — DeepSeek API cache hit rate & token savings monitor
  github_pr    — Create/view GitHub PRs via gh CLI
  github_issue — Create/list GitHub issues
  verify_changes — Pre-completion verification (tests, git, lint)
  check_deps   — Security audit dependencies (pip-audit / npm audit)
  spike_branch — Create throwaway experiment branch
"""

import asyncio
import json
import os
import subprocess
import sys
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("codex-dev-tools")


# ── Tool definitions ────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="run_tests",
            description="Run a test command in a directory. Returns stdout, stderr, exit code. Use this to verify code changes pass tests.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Test command (e.g., 'pytest -x', 'npm test', 'go test ./...')"},
                    "workdir": {"type": "string", "description": "Working directory"},
                    "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default: 120)", "default": 120}
                },
                "required": ["cmd", "workdir"]
            }
        ),
        types.Tool(
            name="search_code",
            description="Search for a regex pattern in files under a directory. Returns matching files + lines.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Directory to search (default: cwd)"},
                    "file_glob": {"type": "string", "description": "Glob filter (e.g., '*.py')"}
                },
                "required": ["pattern"]
            }
        ),
        types.Tool(
            name="read_file",
            description="Read a file section with line numbers. Paginate with start_line/end_line.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start_line": {"type": "integer", "description": "First line (1-indexed, default: 1)", "default": 1},
                    "end_line": {"type": "integer", "description": "Last line (default: start+200)"}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="list_dir",
            description="List directory contents with sizes. Quick filesystem overview.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list"}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="git_diff",
            description="Show git diff summary (staged + unstaged changes) in a repo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"}
                },
                "required": ["repo_path"]
            }
        ),
        types.Tool(
            name="git_log",
            description="Show recent git commit log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repository"},
                    "n": {"type": "integer", "description": "Number of entries (default: 10)", "default": 10}
                },
                "required": ["repo_path"]
            }
        ),
        types.Tool(
            name="code_stats",
            description="Analyze a codebase directory: LOC per language, file counts, and top-level structure. Use BEFORE starting work on an unfamiliar project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root directory to analyze"},
                    "exclude_dirs": {"type": "string", "description": "Comma-separated dirs to exclude (default: node_modules,__pycache__,.git,venv,.venv,dist,build)"}
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="run_shell",
            description="Run an arbitrary shell command. Use for git operations, builds, installs, or any CLI tool. Returns stdout, stderr, exit_code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run"},
                    "workdir": {"type": "string", "description": "Working directory (default: project root)"},
                    "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default: 60)", "default": 60}
                },
                "required": ["cmd"]
            }
        ),
        types.Tool(
            name="todo_write",
            description="Create a structured task list for the current coding session. Use for multi-step work: list what needs to be done, then check off as you go.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "Task items",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique id (e.g., '1', '2a')"},
                                "content": {"type": "string", "description": "Task description"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done"], "description": "Current status"}
                            },
                            "required": ["id", "content", "status"]
                        }
                    }
                },
                "required": ["tasks"]
            }
        ),
        types.Tool(
            name="cache_stats",
            description="Check DeepSeek API cache hit statistics from Moon Bridge metrics. Shows total requests, cache hits/misses, token savings, and cost savings. Use to monitor API efficiency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Look back N hours (default: 24)", "default": 24}
                }
            }
        ),
        types.Tool(
            name="github_pr",
            description="Create or view GitHub pull requests using gh CLI. Action: 'create' (new PR), 'view' (PR details), 'list' (open PRs). Requires gh CLI logged in.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'create', 'view', or 'list'"},
                    "repo_path": {"type": "string", "description": "Path to git repo"},
                    "title": {"type": "string", "description": "[create] PR title"},
                    "body": {"type": "string", "description": "[create] PR description"},
                    "base": {"type": "string", "description": "[create] Target branch (default: main)", "default": "main"},
                    "pr_number": {"type": "integer", "description": "[view] PR number to view"}
                },
                "required": ["action", "repo_path"]
            }
        ),
        types.Tool(
            name="github_issue",
            description="Create or list GitHub issues using gh CLI. Requires gh CLI logged in.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'create' or 'list'"},
                    "repo_path": {"type": "string", "description": "Path to git repo"},
                    "title": {"type": "string", "description": "[create] Issue title"},
                    "body": {"type": "string", "description": "[create] Issue body"},
                    "label": {"type": "string", "description": "[create] Comma-separated labels"},
                    "limit": {"type": "integer", "description": "[list] Max issues (default: 10)", "default": 10}
                },
                "required": ["action", "repo_path"]
            }
        ),
        types.Tool(
            name="verify_changes",
            description="Pre-completion verification checklist: check tests pass, git status clean, lint output. Use BEFORE telling user work is done.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to the repo to verify"},
                    "test_cmd": {"type": "string", "description": "Test command to run (e.g., 'pytest', 'npm test')"},
                    "check_lint": {"type": "boolean", "description": "Also run linter (default: true)", "default": True}
                },
                "required": ["repo_path"]
            }
        ),
        types.Tool(
            name="check_deps",
            description="Security audit project dependencies. Runs pip-audit (Python) or npm audit (Node). Returns vulnerability summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to project root"},
                    "ecosystem": {"type": "string", "description": "'python' or 'node' — auto-detected if omitted"}
                },
                "required": ["repo_path"]
            }
        ),
        types.Tool(
            name="spike_branch",
            description="Create a throwaway spike/experiment branch for testing ideas without polluting main branches. Auto-creates branch spike/<name> and checks it out.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repo"},
                    "name": {"type": "string", "description": "Spike name (becomes branch spike/<name>)"}
                },
                "required": ["repo_path", "name"]
            }
        ),
        types.Tool(
            name="brainstorm",
            description="Start a structured brainstorming session. Returns a template with categories: problem definition, constraints, wild ideas, risks, MVP scope. Use before creative design work.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "What to brainstorm about"}
                },
                "required": ["topic"]
            }
        ),
        types.Tool(
            name="diagnose",
            description="Start a disciplined bug diagnosis loop. Returns a checklist: reproduce, isolate, hypothesize, test, fix, verify. Use when stuck on a hard bug.",
            inputSchema={
                "type": "object",
                "properties": {
                    "bug_description": {"type": "string", "description": "What's the bug? Include error messages if any"}
                },
                "required": ["bug_description"]
            }
        ),
        types.Tool(
            name="tdd_cycle",
            description="Get TDD workflow guidance: RED (write failing test) → GREEN (minimal code) → REFACTOR (clean up). Returns step-by-step instructions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "Programming language / test framework (e.g., 'pytest', 'jest')", "default": "pytest"}
                }
            }
        ),
        types.Tool(
            name="code_review_checklist",
            description="Pre-commit code review checklist. Returns security, quality, and correctness checks to run before submitting code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "Primary language (python, javascript, go, rust, etc.)", "default": "python"}
                }
            }
        ),
        types.Tool(
            name="plan_workflow",
            description="Get implementation planning template. Breaks work into: goal, constraints, research, design, tasks, risks, verification.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What are you trying to build/fix?"}
                },
                "required": ["goal"]
            }
        ),
        types.Tool(
            name="git_worktree",
            description="Create an isolated git worktree for parallel feature work. Creates a new directory with its own branch, leaving main working tree untouched.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to main git repo"},
                    "branch": {"type": "string", "description": "New branch name"},
                    "path": {"type": "string", "description": "Directory for the new worktree"}
                },
                "required": ["repo_path", "branch", "path"]
            }
        ),
        types.Tool(
            name="finish_branch",
            description="Finish a feature branch: merge to base, push, delete local/remote branch. Clean end-to-end branch cleanup.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to git repo"},
                    "branch": {"type": "string", "description": "Branch to finish (default: current)"},
                    "base": {"type": "string", "description": "Target branch to merge into (default: main)", "default": "main"}
                },
                "required": ["repo_path"]
            }
        ),
        types.Tool(
            name="subagent_guide",
            description="Get guidance on using Codex's multi-agent system effectively: when to spawn sub-agents, how to structure parallel tasks, common pitfalls.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="caveman_mode",
            description="Get the caveman ultra-compressed communication prompt. Drops token usage ~75% by eliminating all fluff, pleasantries, and formatting.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="humanize",
            description="Get a prompt template that strips AI-isms and adds real human voice to text. Use to de-robot your writing.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="webapp_test",
            description="Test a local web application endpoint. Checks HTTP status, response time, and returns response body preview. Use to verify servers are running correctly.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to test (e.g., http://localhost:8000/health)"},
                    "method": {"type": "string", "description": "HTTP method (default: GET)", "default": "GET"},
                    "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default: 10)", "default": 10}
                },
                "required": ["url"]
            }
        ),
        types.Tool(
            name="mcp_guide",
            description="Get a guide on building MCP (Model Context Protocol) servers. Covers tool definitions, stdio transport, and best practices.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="skill_guide",
            description="Get a guide on creating effective skills/agents. Covers YAML frontmatter, trigger conditions, pitfalls, and testing.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="review_feedback",
            description="Get guidance on handling code review feedback professionally: how to respond, when to push back, and how to learn from reviews.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="github_auth",
            description="Get GitHub authentication setup guide: HTTPS tokens, SSH keys, gh CLI login. Use when setting up GitHub access.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="what_model",
            description="Report which DeepSeek model is currently active. Reads config.toml to show the real model name in use.",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]


# ── Tool implementations ────────────────────────────────────────────

def _run_sync(cmd: str, workdir: str, timeout: int) -> dict:
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=workdir or os.getcwd(),
            capture_output=True, text=True,
            timeout=timeout,
            encoding="utf-8", errors="replace"
        )
        return {
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-4000:],
            "exit_code": result.returncode,
            "truncated": len(result.stdout) > 8000 or len(result.stderr) > 4000
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": f"Command not found: {cmd.split()[0] if cmd else '?'}", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


def _search_code_sync(pattern: str, path: str, file_glob: str | None) -> dict:
    import glob as globmod
    import re

    if not path or not os.path.isdir(path):
        path = os.getcwd()

    cmd = ["rg", "--line-number", "--no-heading", "--color=never", "--max-count=50"]
    if file_glob:
        cmd.extend(["--glob", file_glob])
    cmd.append(pattern)
    cmd.append(path)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[:50]
        elif result.returncode == 1:
            lines = []
        else:
            lines = [f"rg error: {result.stderr.strip()}"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        try:
            matches = []
            compiled = re.compile(pattern)
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
                for f in files:
                    if file_glob and not globmod.fnmatch.fnmatch(f, file_glob):
                        continue
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                            for i, line in enumerate(fh, 1):
                                if compiled.search(line):
                                    matches.append(f"{fpath}:{i}:{line.rstrip()}")
                                    if len(matches) >= 50:
                                        raise StopIteration
                    except (OSError, UnicodeDecodeError, StopIteration):
                        if len(matches) >= 50:
                            break
                        continue
            lines = matches
        except Exception as e:
            lines = [f"Search error: {e}"]

    return {"matches": lines, "count": len(lines), "truncated": len(lines) >= 50}


def _read_file_sync(path: str, start: int, end: int | None) -> dict:
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}", "lines": [], "total_lines": 0}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"error": str(e), "lines": [], "total_lines": 0}

    total = len(all_lines)
    if end is None:
        end = min(start + 200, total)
    start = max(1, start)
    end = min(total, end)

    selected = all_lines[start-1:end]
    output = [f"{start+i}:{line.rstrip()}" for i, line in enumerate(selected)]

    return {"lines": output, "total_lines": total, "start": start, "end": end, "truncated": end < total}


def _code_stats_sync(path: str, exclude_dirs: str) -> dict:
    """Walk a directory and count files by language + rough LOC."""
    if not os.path.isdir(path):
        return {"error": f"Not a directory: {path}"}

    exclude = set(d.strip() for d in (exclude_dirs or "").split(",") if d.strip())
    exclude.update({"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build", ".next", "target"})

    ext_map = {  # extension -> language
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript React",
        ".jsx": "JavaScript React", ".go": "Go", ".rs": "Rust", ".java": "Java",
        ".cpp": "C++", ".c": "C", ".h": "C/C++ Header", ".hpp": "C++ Header",
        ".css": "CSS", ".scss": "SCSS", ".html": "HTML", ".json": "JSON",
        ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".md": "Markdown",
        ".sql": "SQL", ".sh": "Shell", ".bat": "Batch", ".ps1": "PowerShell",
        ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
        ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte",
    }

    stats = {}   # lang -> {"files": N, "lines": N}
    total_files = 0
    total_lines = 0
    top_dirs = []

    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                top_dirs.append(entry.name)
    except PermissionError:
        pass

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in exclude and not d.startswith(".")]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            lang = ext_map.get(ext, f"Other ({ext or 'no ext'})")
            fpath = os.path.join(root, fname)
            total_files += 1
            try:
                size = os.path.getsize(fpath)
                if size > 1_000_000:  # skip files > 1MB
                    line_count = 0
                else:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        line_count = sum(1 for _ in f)
            except (OSError, UnicodeDecodeError):
                line_count = 0

            if lang not in stats:
                stats[lang] = {"files": 0, "lines": 0}
            stats[lang]["files"] += 1
            stats[lang]["lines"] += line_count
            total_lines += line_count

    breakdown = [
        {"language": lang, "files": s["files"], "lines": s["lines"],
         "pct_lines": round(s["lines"] / total_lines * 100, 1) if total_lines else 0}
        for lang, s in sorted(stats.items(), key=lambda x: -x[1]["lines"])
    ]

    return {
        "root": path,
        "total_files": total_files,
        "total_lines": total_lines,
        "languages": len(breakdown),
        "breakdown": breakdown[:15],
        "top_dirs": top_dirs[:20]
    }


def _cache_stats_sync(hours: int) -> dict:
    """Query Moon Bridge SQLite metrics for cache hit stats."""
    import sqlite3
    db_path = os.path.expanduser("~/moon-bridge/data/moonbridge.db")
    if not os.path.exists(db_path):
        return {"error": f"Moon Bridge database not found: {db_path}"}

    try:
        conn = sqlite3.connect(db_path)
        cutoff = int(time.time() - hours * 3600)

        # 自动探测实际表名和列名，避免 schema 版本不一致
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        metrics_table = next((t for t in tables if "metric" in t.lower() or "request" in t.lower()), None)
        if not metrics_table:
            conn.close()
            return {"error": f"找不到 metrics 表，已有表: {tables}"}

        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({metrics_table})").fetchall()]

        # 动态映射列名（兼容不同 Moon Bridge 版本）
        def col(candidates):
            for c in candidates:
                if c in cols:
                    return c
            return None

        ts_col    = col(["timestamp", "created_at", "time"])
        in_col    = col(["input_tokens", "prompt_tokens", "tokens_in"])
        out_col   = col(["output_tokens", "completion_tokens", "tokens_out"])
        cr_col    = col(["cache_read", "cache_read_tokens", "cache_hits_tokens"])
        cw_col    = col(["cache_creation", "cache_write_tokens", "cache_writes_tokens"])
        cost_col  = col(["cost", "total_cost", "cost_usd"])
        lat_col   = col(["response_time_ms", "latency_ms", "duration_ms"])
        stat_col  = col(["status", "status_code", "http_status"])

        if not ts_col:
            conn.close()
            return {"error": f"找不到时间戳列，已有列: {cols}"}

        select_cols = ", ".join(c for c in [ts_col, in_col, out_col, cr_col, cw_col, cost_col, lat_col, stat_col] if c)
        rows = conn.execute(
            f"SELECT {select_cols} FROM {metrics_table} WHERE {ts_col} > ?",
            (cutoff,)
        ).fetchall()
        conn.close()

        total = len(rows)
        if total == 0:
            return {"total_requests": 0, "message": f"最近 {hours}h 无请求"}

        # 按列序号取值，而不是硬编码位置
        col_order = [c for c in [ts_col, in_col, out_col, cr_col, cw_col, cost_col, lat_col, stat_col] if c]
        def get(row, name):
            if name and name in col_order:
                return row[col_order.index(name)] or 0
            return 0

        total_in    = sum(get(r, in_col) for r in rows)
        total_out   = sum(get(r, out_col) for r in rows)
        total_cr    = sum(get(r, cr_col) for r in rows)
        total_cost  = sum(get(r, cost_col) for r in rows)
        avg_lat     = sum(get(r, lat_col) for r in rows) / total if lat_col else 0
        cache_hits  = sum(1 for r in rows if get(r, cr_col) > 0)
        errors      = sum(1 for r in rows if str(get(r, stat_col)) not in ("200", "0", "")) if stat_col else 0

        return {
            "period_hours": hours,
            "total_requests": total,
            "cache_hits": cache_hits,
            "cache_hit_rate": f"{cache_hits / total * 100:.1f}%",
            "cache_read_tokens": total_cr,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cache_savings": f"{total_cr / max(total_in, 1) * 100:.1f}% 命中",
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(avg_lat, 1),
            "errors": errors,
            "table": metrics_table,
            "columns_used": col_order,
        }
    except Exception as e:
        return {"error": str(e)}


def _github_pr_sync(action: str, repo_path: str, title: str = "", body: str = "", base: str = "main", pr_number: int = 0) -> dict:
    """GitHub PR operations via gh CLI."""
    if action == "create":
        cmd = f'gh pr create --title "{title}" --body "{body}" --base {base}'
    elif action == "view" and pr_number:
        cmd = f"gh pr view {pr_number} --json number,title,state,author,url,body"
    elif action == "list":
        cmd = "gh pr list --state open --limit 10"
    else:
        return {"error": f"Unknown action: {action}"}
    return _run_sync(cmd, repo_path, 30)


def _github_issue_sync(action: str, repo_path: str, title: str = "", body: str = "", label: str = "", limit: int = 10) -> dict:
    """GitHub issue ops via gh CLI."""
    if action == "create":
        label_flag = f' --label "{label}"' if label else ""
        cmd = f'gh issue create --title "{title}" --body "{body}"{label_flag}'
    elif action == "list":
        cmd = f"gh issue list --limit {limit} --state open"
    else:
        return {"error": f"Unknown action: {action}"}
    return _run_sync(cmd, repo_path, 30)


def _verify_sync(repo_path: str, test_cmd: str = "", check_lint: bool = True) -> dict:
    """Pre-completion verification: git status, tests, lint."""
    results = {}

    # 1. Git status
    r = _run_sync("git status --short", repo_path, 15)
    dirty = len([l for l in r.get("stdout", "").split("\n") if l.strip()]) if r["exit_code"] == 0 else -1
    results["git_dirty_files"] = dirty
    results["git_status"] = "clean" if dirty == 0 else f"{dirty} uncommitted file(s)" if dirty > 0 else "error"

    # 2. Tests
    if test_cmd:
        r = _run_sync(test_cmd, repo_path, 120)
        results["tests_passed"] = r["exit_code"] == 0
        results["test_output_tail"] = r["stdout"][-500:] if r["stdout"] else r["stderr"][-200:]

    # 3. Lint
    lint_ok = True
    if check_lint:
        # Try Python lint first, then Node
        r = _run_sync("ruff check --output-format concise . 2>&1 | head -20", repo_path, 30)
        if r["exit_code"] == 0 and "ruff:" not in r.get("stderr", ""):
            results["lint"] = "clean (ruff)"
        elif "ruff:" in r.get("stderr", ""):
            # Try eslint
            r = _run_sync("npx eslint . --max-warnings 0 2>&1 | head -20", repo_path, 30)
            if r["exit_code"] == 0:
                results["lint"] = "clean (eslint)"
            else:
                results["lint"] = r["stdout"][:300] or r["stderr"][:300]
                lint_ok = False
        else:
            results["lint"] = r["stdout"][:300] or r["stderr"][:300]
            lint_ok = False

    results["all_clear"] = results.get("git_dirty_files", -1) == 0 and results.get("tests_passed", True) and lint_ok
    return results


def _check_deps_sync(repo_path: str, ecosystem: str = "") -> dict:
    """Audit deps: pip-audit or npm audit."""
    if not ecosystem:
        if os.path.exists(os.path.join(repo_path, "requirements.txt")) or os.path.exists(os.path.join(repo_path, "pyproject.toml")):
            ecosystem = "python"
        elif os.path.exists(os.path.join(repo_path, "package.json")):
            ecosystem = "node"
        else:
            return {"error": "Could not detect ecosystem. Pass 'python' or 'node'."}

    if ecosystem == "python":
        r = _run_sync("pip-audit --format json 2>&1", repo_path, 60)
        if r["exit_code"] == 0 and r["stdout"]:
            try:
                data = json.loads(r["stdout"].split("\n")[-1] if "\n" in r["stdout"] else r["stdout"])
                vulns = data.get("vulnerabilities", []) if isinstance(data, dict) else data
                return {"ecosystem": "python", "vulnerabilities": len(vulns) if isinstance(vulns, list) else "?", "ok": len(vulns) == 0 if isinstance(vulns, list) else False}
            except:
                pass
        return _run_sync("pip-audit 2>&1 | tail -20", repo_path, 60)

    elif ecosystem == "node":
        return _run_sync("npm audit --json 2>&1 | python -c \"import sys,json; d=json.load(sys.stdin); v=d.get('vulnerabilities',{}); print(f'vulns={len(v)} high={v.get(\\\"high\\\",0)} critical={v.get(\\\"critical\\\",0)}')\" 2>&1", repo_path, 60)

    return {"error": f"Unknown ecosystem: {ecosystem}"}


def _spike_branch_sync(repo_path: str, name: str) -> dict:
    """Create spike branch and check it out."""
    branch = f"spike/{name}"
    # Check if branch already exists
    r = _run_sync(f"git branch --list {branch}", repo_path, 10)
    if branch in r.get("stdout", ""):
        r2 = _run_sync(f"git checkout {branch}", repo_path, 10)
        return {"branch": branch, "action": "checked_out_existing", "ok": r2["exit_code"] == 0}

    r = _run_sync(f"git checkout -b {branch}", repo_path, 10)
    return {"branch": branch, "action": "created_and_checked_out", "ok": r["exit_code"] == 0}


def _brainstorm_sync(topic: str) -> dict:
    return {"template": f"""## 🧠 Brainstorm: {topic}

### 1. Problem Definition
- What exactly are we solving?
- Who is the user?
- What does success look like?

### 2. Constraints
- Time / budget / technical limits
- Must-have vs nice-to-have
- Dependencies / blockers

### 3. Wild Ideas (no filter)
- 

### 4. Top 3 Approaches
| Approach | Pros | Cons | Effort |
|----------|------|------|--------|
| A | | | |
| B | | | |
| C | | | |

### 5. Risks
- 

### 6. MVP Scope
- v0.1 must include:
- v0.1 explicitly NOT include:
"""}


def _diagnose_sync(bug_description: str) -> dict:
    return {"checklist": f"""## 🔍 Bug Diagnosis Loop

**Bug:** {bug_description}

### Step 1: REPRODUCE
- [ ] Can I reliably trigger this bug?
- [ ] What are the exact steps?
- [ ] Does it happen in a clean environment?

### Step 2: ISOLATE  
- [ ] What changed recently? (git log, config changes)
- [ ] Can I narrow it to a specific module/function?
- [ ] Binary search: disable half the system, does it still happen?

### Step 3: HYPOTHESIZE
- [ ] Root cause hypothesis:
- [ ] Evidence for hypothesis:
- [ ] Evidence against hypothesis:

### Step 4: TEST
- [ ] Write a minimal test case that reproduces
- [ ] Test the hypothesis with a targeted change
- Result: PASS / FAIL

### Step 5: FIX
- [ ] Implement the fix
- [ ] Run full test suite
- [ ] Verify original bug no longer reproduces

### Step 6: VERIFY
- [ ] Are there similar patterns elsewhere that need the same fix?
- [ ] Add regression test
- [ ] Document the root cause
"""}


def _tdd_cycle_sync(language: str = "pytest") -> dict:
    return {"workflow": f"""## 🧪 TDD Cycle ({language})

### 🔴 RED — Write a failing test
```{language}
# 1. Write the smallest possible failing test
# 2. Run it → confirm it FAILS
# 3. Commit: "test: add failing test for <feature>"
```

### 🟢 GREEN — Make it pass
```{language}
# 1. Write MINIMAL code to pass the test
# 2. Don't optimize, don't refactor yet
# 3. Run test → confirm it PASSES
# 4. Commit: "feat: implement <feature>"
```

### 🔵 REFACTOR — Clean up
```{language}
# 1. Remove duplication
# 2. Improve names
# 3. Run tests → must still PASS
# 4. Commit: "refactor: clean up <feature>"
```

### ⚠️ Rules
- Never write production code before a failing test
- Only write enough test to fail
- Only write enough code to pass
- Run tests after EVERY change
"""}


def _code_review_checklist_sync(language: str = "python") -> dict:
    return {"checklist": f"""## 📋 Pre-Commit Review Checklist ({language})

### 🔒 Security
- [ ] No hardcoded secrets / API keys
- [ ] Input validation on all user-facing inputs
- [ ] SQL injection / XSS / path traversal protected
- [ ] Dependencies audited (pip-audit / npm audit)

### 🧹 Code Quality
- [ ] No dead code / commented-out blocks
- [ ] Names are clear and consistent
- [ ] Functions are small (< 30 lines ideally)
- [ ] No premature optimization
- [ ] Error handling is explicit (no bare except)

### ✅ Correctness
- [ ] All tests pass
- [ ] Edge cases handled (empty input, null, large values)
- [ ] Race conditions considered (if async/threaded)
- [ ] Logging is appropriate (not too noisy, not silent)

### 📦 Git Hygiene
- [ ] No unrelated changes in this commit
- [ ] Commit message follows convention
- [ ] No generated files / build artifacts committed
"""}


def _plan_workflow_sync(goal: str) -> dict:
    return {"template": f"""## 📐 Implementation Plan: {goal}

### Goal
{goal}

### Constraints
- 

### Research Needed
- [ ] 

### Architecture / Design
```
(high-level design sketch)
```

### Tasks (bite-sized, ordered)
1. [ ] 
2. [ ] 
3. [ ] 

### Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| | | | |

### Verification Plan
- [ ] All tests pass
- [ ] Manual smoke test:
- [ ] Edge cases checked:
"""}


def _git_worktree_sync(repo_path: str, branch: str, path: str) -> dict:
    r = _run_sync(f"git worktree add -b {branch} {path}", repo_path, 30)
    if r["exit_code"] == 0:
        return {"ok": True, "worktree_path": path, "branch": branch, "message": f"Worktree created at {path} on branch {branch}"}
    return {"ok": False, "error": r["stderr"]}


def _finish_branch_sync(repo_path: str, branch: str = "", base: str = "main") -> dict:
    """Finish a feature branch: checkout base, merge, push, delete branch."""
    results = []
    if not branch:
        r = _run_sync("git rev-parse --abbrev-ref HEAD", repo_path, 10)
        branch = r.get("stdout", "").strip()
        if not branch or branch == base:
            return {"ok": False, "error": f"Cannot finish: on {base} branch"}

    # 1. Checkout base
    r = _run_sync(f"git checkout {base}", repo_path, 15)
    results.append(("checkout_base", r["exit_code"] == 0, r.get("stderr", "")))
    if r["exit_code"] != 0:
        return {"ok": False, "error": f"Failed to checkout {base}: {r['stderr']}", "steps": results}

    # 2. Pull latest
    r = _run_sync(f"git pull origin {base}", repo_path, 30)
    results.append(("pull_latest", r["exit_code"] == 0, r.get("stderr", "")))

    # 3. Merge branch
    r = _run_sync(f"git merge {branch}", repo_path, 30)
    results.append(("merge", r["exit_code"] == 0, r.get("stderr", "")))

    # 4. Push
    r = _run_sync(f"git push origin {base}", repo_path, 30)
    results.append(("push", r["exit_code"] == 0, r.get("stderr", "")))

    # 5. Delete local + remote branch
    _run_sync(f"git branch -d {branch}", repo_path, 10)
    _run_sync(f"git push origin --delete {branch}", repo_path, 15)

    return {"ok": True, "branch": branch, "merged_into": base, "steps": results}


def _subagent_guide_sync() -> dict:
    return {"guide": """## 🤖 Multi-Agent Parallel Development Guide

### When to use sub-agents
- 2+ INDEPENDENT tasks that don't need sequential state
- Research + implementation can happen in parallel
- Code review + fixes can be parallelized
- Testing multiple configurations simultaneously

### How to structure
1. **Define clear boundaries** — each agent gets an isolated workspace
2. **Pass full context** — sub-agents have NO memory of your conversation
3. **Specify output format** — tell them exactly what to return
4. **Verify results** — sub-agent claims are self-reported, not verified

### Pitfalls
- Don't use for sequential work (A depends on B)
- Sub-agents can't ask you questions (no clarify tool)
- File conflicts if two agents edit the same file
- Max 3 concurrent agents (configurable)

### Example
```
Task 1: "Research best Rust HTTP frameworks, return top 3 with pros/cons"
Task 2: "Refactor auth.py to use async/await"
Task 3: "Write integration tests for the payment module"
```
"""}


def _caveman_mode_sync() -> dict:
    return {"prompt": """## 🦴 CAVEMAN MODE

COMMUNICATION RULES:
- NO fluff. NO pleasantries. NO markdown.
- Short sentences. Direct. Facts only.
- If code, show code. No explanation unless asked.
- Answer in 1-3 lines max. Delete everything unnecessary.
- If you need more context, ask ONE short question.
- No "I think", "I would suggest", "let me explain" — just the answer.

This drops token usage ~75%.
"""}


def _humanize_sync() -> dict:
    return {"prompt": """## ✍️ Humanize Mode

Rewrite the following text to sound like a real person:
- Strip ALL AI-isms: "delve into", "unleash", "game-changer", "moreover", "furthermore"
- No bullet-point lists unless genuinely needed
- Use contractions (don't → don't, it is → it's)
- Vary sentence length — some short. Some longer with natural rhythm.
- One idea per paragraph
- If it sounds like a LinkedIn post or corporate memo, rewrite it
- Read it out loud — if it doesn't sound like something you'd say to a friend, fix it
- Keep the same information, change ONLY the voice
"""}


def _webapp_test_sync(url: str, method: str = "GET", timeout_sec: int = 10) -> dict:
    """Test a web endpoint."""
    import urllib.request
    import urllib.error
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method=method)
        resp = urllib.request.urlopen(req, timeout=timeout_sec)
        body = resp.read().decode("utf-8", errors="replace")[:1000]
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "ok": True,
            "status": resp.status,
            "elapsed_ms": elapsed,
            "content_type": resp.headers.get("Content-Type", "unknown"),
            "body_preview": body[:500]
        }
    except urllib.error.HTTPError as e:
        elapsed = round((time.time() - t0) * 1000, 1)
        body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        return {"ok": False, "status": e.code, "elapsed_ms": elapsed, "error": str(e), "body_preview": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _mcp_guide_sync() -> dict:
    return {"guide": """## 🔧 MCP Server Building Guide

### Minimal Python MCP Server
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("my-tools")

@server.list_tools()
async def list_tools():
    return [types.Tool(name="hello", description="Say hello",
              inputSchema={"type":"object","properties":{"name":{"type":"string"}},"required":["name"]})]

@server.call_tool()
async def call_tool(name, args):
    if name == "hello":
        return [types.TextContent(type="text", text=f"Hello {args['name']}!")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

### Register in Codex config.toml
```toml
[mcp_servers.my_tools]
command = "python"
args = ["-u", "path/to/server.py"]
startup_timeout_sec = 30
```

### Best Practices
- Keep tools single-purpose
- tool descriptions should tell Codex WHEN to call them
- inputSchema must have clear descriptions for each field
- Test with `timeout 3 python server.py` (should hang, not crash)
"""}


def _skill_guide_sync() -> dict:
    return {"guide": """## 📝 Skill Creation Guide

### Structure (SKILL.md)
```yaml
---
name: my-skill
description: What this skill does and when to use it
category: development
tags: [python, testing]
---

# Skill Title

## Trigger
When the user wants to: ...

## Steps
1. First step
2. Second step

## Pitfalls
- Common mistake 1
- Common mistake 2

## Verification
- How to confirm it worked
```

### Best Practices
- **Trigger section is critical** — defines when the skill auto-loads
- Keep steps numbered and actionable
- Pitfalls section saves hours of debugging
- Test the skill yourself before sharing
- Update skills when you find new issues (patch, don't rewrite)
"""}


def _review_feedback_sync() -> dict:
    return {"guide": """## 📬 Handling Code Review Feedback

### DO
- Thank reviewers for their time (genuinely, not formulaically)
- Address EVERY comment — even if just "done" or "disagree because..."
- Link to the commit that fixes each issue
- Ask clarifying questions if feedback is unclear

### DON'T
- Get defensive — review is about the code, not you
- Fix things silently — comment "fixed in commit X"
- Batch unrelated fixes in one "review feedback" commit
- Argue in comments — if there's a real disagreement, hop on a call

### When to push back
- The suggestion makes things worse objectively (slower, less secure)
- It contradicts established team conventions
- It's purely stylistic preference with no standard
- **Always explain WHY** — "I prefer X because Y"

### Learning from reviews
- After 10 reviews, look for patterns
- If 3+ people flag the same thing, that's a habit to change
- Keep a personal "review gotchas" checklist
"""}


def _github_auth_sync() -> dict:
    guide = """## 🔑 GitHub Authentication Setup

### Option 1: HTTPS + Personal Access Token (easiest)
1. Create token: https://github.com/settings/tokens → Generate new token (classic)
   Scopes: repo, workflow
2. Use token as password when prompted:
   git clone https://github.com/user/repo.git
   Username: your-username
   Password: ghp_xxxxxxxxxxxxxxxxxxxx
3. Cache credentials: git config --global credential.helper cache

### Option 2: SSH Key (most secure)
1. Generate key: ssh-keygen -t ed25519 -C "your@email.com"
2. Add to ssh-agent:
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519
3. Add public key to GitHub:
   cat ~/.ssh/id_ed25519.pub
   → Copy to https://github.com/settings/keys
4. Test: ssh -T git@github.com

### Option 3: gh CLI (best for automation)
1. Install: https://cli.github.com
2. Login: gh auth login
   → Choose HTTPS, login with browser, done
"""
    return {"guide": guide}


# ── Tool dispatcher ─────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "run_tests":
        result = await asyncio.to_thread(_run_sync, arguments["cmd"], arguments["workdir"], arguments.get("timeout_sec", 120))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "search_code":
        result = await asyncio.to_thread(_search_code_sync, arguments["pattern"], arguments.get("path", os.getcwd()), arguments.get("file_glob"))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "read_file":
        result = await asyncio.to_thread(_read_file_sync, arguments["path"], arguments.get("start_line", 1), arguments.get("end_line"))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "list_dir":
        path = arguments["path"]
        try:
            items = []
            for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                try:
                    size = entry.stat().st_size if entry.is_file() else 0
                except OSError:
                    size = 0
                t = "DIR" if entry.is_dir() else "FILE"
                items.append(f"{t:4s} {size:>10,d}  {entry.name}")
            text = "\n".join(items[:100])
        except Exception as e:
            text = f"Error: {e}"
        return [types.TextContent(type="text", text=text)]

    elif name == "git_diff":
        result = await asyncio.to_thread(_run_sync, "git diff --stat && echo '---STAGED---' && git diff --cached --stat", arguments["repo_path"], 30)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "git_log":
        n = arguments.get("n", 10)
        result = await asyncio.to_thread(_run_sync, f"git log --oneline --decorate -n {n}", arguments["repo_path"], 30)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "code_stats":
        result = await asyncio.to_thread(_code_stats_sync, arguments["path"], arguments.get("exclude_dirs", ""))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "run_shell":
        result = await asyncio.to_thread(_run_sync, arguments["cmd"], arguments.get("workdir", os.getcwd()), arguments.get("timeout_sec", 60))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "todo_write":
        tasks = arguments.get("tasks", [])
        lines = []
        for t in tasks:
            icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅"}.get(t.get("status", "pending"), "❓")
            lines.append(f"{icon} [{t.get('id', '?')}] {t.get('content', '')}")
        summary = "\n".join(lines) if lines else "(empty)"
        msg = f"## Task List\n\n{summary}\n\n_Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}_"
        return [types.TextContent(type="text", text=msg)]

    elif name == "cache_stats":
        result = await asyncio.to_thread(_cache_stats_sync, arguments.get("hours", 24))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "github_pr":
        result = await asyncio.to_thread(_github_pr_sync, arguments["action"], arguments["repo_path"], arguments.get("title", ""), arguments.get("body", ""), arguments.get("base", "main"), arguments.get("pr_number", 0))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "github_issue":
        result = await asyncio.to_thread(_github_issue_sync, arguments["action"], arguments["repo_path"], arguments.get("title", ""), arguments.get("body", ""), arguments.get("label", ""), arguments.get("limit", 10))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "verify_changes":
        result = await asyncio.to_thread(_verify_sync, arguments["repo_path"], arguments.get("test_cmd", ""), arguments.get("check_lint", True))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "check_deps":
        result = await asyncio.to_thread(_check_deps_sync, arguments["repo_path"], arguments.get("ecosystem", ""))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "spike_branch":
        result = await asyncio.to_thread(_spike_branch_sync, arguments["repo_path"], arguments["name"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "brainstorm":
        result = await asyncio.to_thread(_brainstorm_sync, arguments["topic"])
        return [types.TextContent(type="text", text=result["template"])]

    elif name == "diagnose":
        result = await asyncio.to_thread(_diagnose_sync, arguments["bug_description"])
        return [types.TextContent(type="text", text=result["checklist"])]

    elif name == "tdd_cycle":
        result = await asyncio.to_thread(_tdd_cycle_sync, arguments.get("language", "pytest"))
        return [types.TextContent(type="text", text=result["workflow"])]

    elif name == "code_review_checklist":
        result = await asyncio.to_thread(_code_review_checklist_sync, arguments.get("language", "python"))
        return [types.TextContent(type="text", text=result["checklist"])]

    elif name == "plan_workflow":
        result = await asyncio.to_thread(_plan_workflow_sync, arguments["goal"])
        return [types.TextContent(type="text", text=result["template"])]

    elif name == "git_worktree":
        result = await asyncio.to_thread(_git_worktree_sync, arguments["repo_path"], arguments["branch"], arguments["path"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "finish_branch":
        result = await asyncio.to_thread(_finish_branch_sync, arguments["repo_path"], arguments.get("branch", ""), arguments.get("base", "main"))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "subagent_guide":
        result = _subagent_guide_sync()
        return [types.TextContent(type="text", text=result["guide"])]

    elif name == "caveman_mode":
        result = _caveman_mode_sync()
        return [types.TextContent(type="text", text=result["prompt"])]

    elif name == "humanize":
        result = _humanize_sync()
        return [types.TextContent(type="text", text=result["prompt"])]

    elif name == "webapp_test":
        result = await asyncio.to_thread(_webapp_test_sync, arguments["url"], arguments.get("method", "GET"), arguments.get("timeout_sec", 10))
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "mcp_guide":
        result = _mcp_guide_sync()
        return [types.TextContent(type="text", text=result["guide"])]

    elif name == "skill_guide":
        result = _skill_guide_sync()
        return [types.TextContent(type="text", text=result["guide"])]

    elif name == "review_feedback":
        result = _review_feedback_sync()
        return [types.TextContent(type="text", text=result["guide"])]

    elif name == "github_auth":
        result = _github_auth_sync()
        return [types.TextContent(type="text", text=result["guide"])]

    elif name == "what_model":
        import re
        cfg = os.path.expanduser("~/.codex/config.toml")
        try:
            with open(cfg) as f:
                text = f.read()
            m = re.search(r'^model\s*=\s*"(.+)"', text, re.MULTILINE)
            model = m.group(1) if m else "unknown"
        except:
            model = "error reading config"
        result = f"Current model: **{model}**（底层 API: DeepSeek, 经由 Moon Bridge 转发）"
        return [types.TextContent(type="text", text=result)]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Entry point ─────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
