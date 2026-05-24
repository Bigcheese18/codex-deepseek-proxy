"""
Codex Proxy Dashboard — 优化版
────────────────────────────────────────
端口分配：
  38441  →  控制台 UI（本文件）
  38442  →  Tool 过滤代理（本文件，已内置，无需单独启动）
  38440  →  Moon Bridge（Go 进程，本文件负责启停）

主要改动：
  1. 过滤代理内置，/v1/* 直接拦截过滤后转发 38440，不再需要 tool_filter_proxy.py
  2. Moon Bridge 存活检查改为 socket 端口探测，不再依赖 tasklist
  3. 进程追踪用全局变量，stop 时直接 terminate()
  4. 修复 /api/proxy/test 解析（DeepSeek 返回 data.data 不是 data.models）
  5. UI 新增过滤器统计卡片
  6. 补全更多模型选项
  7. 双端口同进程启动（38441 + 38442）
"""

import json
import os
import re
import socket
import subprocess
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx
import uvicorn

HOME = Path.home()
BASE = HOME / "Desktop" / "Codex"
CODEX_CONFIG = BASE / "config.toml"
CODEX_REAL_CONFIG = HOME / ".codex" / "config.toml"
MOON_DIR = HOME / "moon-bridge"
MOON_CONFIG = MOON_DIR / "config.yml"
GO_EXE = str(HOME / "go1.25.10" / "bin" / "go.exe")
MOON_PORT = 38440
UI_PORT = 38441
FILTER_PORT = 38442

app = FastAPI()

# ── 全局状态 ────────────────────────────────────────────────
_moon_bridge_proc: subprocess.Popen | None = None
log_buffer: list[str] = ["📋 控制台已就绪"]
filter_stats = {"total": 0, "filtered": 0}   # 过滤器累计统计


# ── Tool 过滤核心 ────────────────────────────────────────────
def _filter_tools(body: dict) -> tuple[dict, int]:
    """剔除 function.name 为空的 tool，返回 (处理后body, 被过滤数量)"""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body, 0
    before = len(tools)
    tools = [
        t for t in tools
        if isinstance(t, dict) and t.get("function", {}).get("name", "").strip()
    ]
    removed = before - len(tools)
    if tools:
        body["tools"] = tools
    else:
        body.pop("tools", None)
    return body, removed


# ── 过滤代理路由（供端口 38442 使用）──────────────────────────
OVERLOAD_PHRASES = (b"high demand", b"overloaded", b"rate limit", b"too many requests", b"529", b"503")
MAX_RETRIES = 4
RETRY_DELAYS = [2.0, 5.0, 10.0, 20.0]   # 秒，指数退避

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def filter_proxy(request: Request, path: str):
    url = f"http://127.0.0.1:{MOON_PORT}/v1/{path}"
    raw = await request.read()
    content_type = request.headers.get("Content-Type", "")
    removed = 0

    if "json" in content_type and raw:
        try:
            body = json.loads(raw)
            filter_stats["total"] += 1
            body, removed = _filter_tools(body)
            if removed:
                filter_stats["filtered"] += removed
                _log(f"🔧 过滤 {removed} 个空名 tool（/v1/{path}）")
            raw = json.dumps(body, ensure_ascii=False).encode()
        except Exception as e:
            _log(f"⚠️ JSON 解析失败，原样转发: {e}", "error")

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }
    fwd_headers["Content-Length"] = str(len(raw))

    for attempt in range(MAX_RETRIES + 1):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0)
        )
        try:
            upstream_req = client.build_request(
                method=request.method, url=url,
                headers=fwd_headers, content=raw,
            )
            upstream_resp = await client.send(upstream_req, stream=True)

            # ── 检测过载响应 ──────────────────────────────
            status = upstream_resp.status_code
            is_overload = status in (429, 500, 503, 529)
            if is_overload and attempt < MAX_RETRIES:
                snippet = await upstream_resp.aread()
                await upstream_resp.aclose()
                low = (snippet or b"").lower()
                if any(p.encode() in low for p in OVERLOAD_PHRASES):
                    delay = RETRY_DELAYS[attempt]
                    _log(f"⚡ DeepSeek 过载（{status}），{delay:.0f}s 后重试 [{attempt+1}/{MAX_RETRIES}]", "error")
                    filter_stats["retries"] = filter_stats.get("retries", 0) + 1
                    await asyncio.sleep(delay)
                    continue
                return JSONResponse(json.loads(snippet) if snippet else {}, status_code=status)

            # ── 正常流式透传（真流，边收边发）────────────
            async def stream_body():
                try:
                    async for chunk in upstream_resp.aiter_bytes(chunk_size=4096):
                        yield chunk
                finally:
                    await upstream_resp.aclose()

            resp_headers = {
                k: v for k, v in upstream_resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
            }
            return StreamingResponse(
                content=stream_body(),
                status_code=status,
                headers=resp_headers,
                media_type=upstream_resp.headers.get("content-type", "application/json"),
            )

        except Exception as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                _log(f"❌ 连接失败，{delay:.0f}s 后重试 [{attempt+1}/{MAX_RETRIES}]: {e}", "error")
                await asyncio.sleep(delay)
                continue
            _log(f"❌ 全部重试失败 /v1/{path}: {e}", "error")
            return JSONResponse({"error": str(e)}, status_code=502)

    return JSONResponse({"error": "DeepSeek 服务器繁忙，已重试多次，请稍后再试"}, status_code=503)


# ── 工具函数 ─────────────────────────────────────────────────
def _log(msg: str, level: str = "info"):
    prefix = {"error": "❌ ", "success": "✅ ", "info": ""}.get(level, "")
    log_buffer.append(f"{prefix}{msg}")
    if len(log_buffer) > 200:
        log_buffer.pop(0)


def _check_port_open(port: int, timeout: float = 1.0) -> bool:
    """比 HTTP 请求更快更可靠的端口存活检测"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _get_active_model() -> str:
    try:
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        m = re.search(r'^model\s*=\s*"(.+)"', text, re.MULTILINE)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def _find_codex_exe() -> str | None:
    known = Path("C:/Program Files/WindowsApps/OpenAI.Codex_26.519.5221.0_x64__2p2nqsd0c76g0/app/Codex.exe")
    if known.exists():
        return str(known)
    base = HOME / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
    if base.exists():
        for subdir in sorted(base.glob("*"), reverse=True):
            exe = subdir / "codex.exe"
            if exe.exists():
                return str(exe)
    try:
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        m = re.search(r"CODEX_CLI_PATH\s*=\s*'(.+?)'", text)
        if m:
            p = Path(m.group(1))
            if p.exists():
                return str(p)
    except Exception:
        pass
    return None


def _check_codex_running() -> bool:
    try:
        for name in ("Codex.exe", "codex.exe"):
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True
            )
            if name.lower() in r.stdout.lower():
                return True
    except Exception:
        pass
    return False


# ── Dashboard HTML ────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex Proxy 控制台</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--orange:#d29922}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
.header{background:var(--card);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
.header h1{font-size:18px;font-weight:600}
.status-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px}
.status-dot.running{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.status-dot.stopped{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.container{max-width:960px;margin:0 auto;padding:20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:0.5px;color:var(--accent);margin-bottom:14px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
select,input,button{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit}
select{cursor:pointer;min-width:220px}
button{cursor:pointer;font-weight:500;transition:all .15s}
button:hover{opacity:.85}
.btn-start{background:#238636;border-color:#2ea043;color:#fff}
.btn-stop{background:#da3633;border-color:#f85149;color:#fff}
.btn-action{background:#1f6feb;border-color:var(--accent);color:#fff}
.log-box{background:#0d1117;border:1px solid var(--border);border-radius:6px;padding:12px;font-family:'Cascadia Code',Consolas,monospace;font-size:12px;max-height:340px;overflow-y:auto;white-space:pre-wrap;line-height:1.6}
.log-line{color:#8b949e}
.log-line.error{color:var(--red)}
.log-line.success{color:var(--green)}
.log-line.info{color:var(--accent)}
.meta{font-size:12px;color:#8b949e;margin-top:8px}
.tag{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.tag.on{background:#1a3a2a;color:var(--green);border:1px solid #238636}
.tag.off{background:#3a1a1a;color:var(--red);border:1px solid #da3633}
.stat{display:flex;flex-direction:column;align-items:center;padding:12px;background:var(--bg);border-radius:6px;border:1px solid var(--border);min-width:100px}
.stat-num{font-size:28px;font-weight:700;color:var(--accent)}
.stat-label{font-size:11px;color:#8b949e;margin-top:2px}
.arch{font-family:monospace;font-size:12px;background:var(--bg);padding:10px 14px;border-radius:6px;border:1px solid var(--border);color:#8b949e;letter-spacing:0.3px}
.arch span{color:var(--accent)}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1><span id="status-dot" class="status-dot running"></span>Codex Proxy 控制台</h1>
    <div class="meta">Moon Bridge → DeepSeek API</div>
  </div>
  <div id="pid-info" style="font-size:12px;color:#8b949e"></div>
</div>

<div class="container">

  <!-- 架构说明 -->
  <div class="card">
    <h2>🔗 链路架构</h2>
    <div class="arch">
      Codex &nbsp;→&nbsp; <span>:38442 过滤层（内置）</span> &nbsp;→&nbsp; <span>:38440 Moon Bridge</span> &nbsp;→&nbsp; DeepSeek API
    </div>
    <div class="meta" style="margin-top:8px">过滤层与控制台同进程运行，无需单独启动 tool_filter_proxy.py</div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>📡 Moon Bridge 状态</h2>
      <div class="row" style="margin-bottom:10px">
        <button class="btn-start" onclick="startProxy()">▶ 启动</button>
        <button class="btn-stop" onclick="stopProxy()">⏹ 停止</button>
        <button class="btn-action" onclick="testProxy()">🔍 测试</button>
        <button class="btn-action" onclick="toggleSkills()" style="background:#6e40c9;border-color:#6e40c9">🔌 技能</button>
      </div>
      <div id="proxy-status"></div>
      <div class="meta" id="pid-display"></div>
    </div>

    <div class="card">
      <h2>🧠 模型切换</h2>
      <div class="row">
        <select id="model-select">
          <option value="deepseek-v4-pro">DeepSeek V4 Pro（推理增强）</option>
          <option value="deepseek-v4-flash">DeepSeek V4 Flash（快速低延迟）</option>
          <option value="deepseek-v3">DeepSeek V3</option>
          <option value="deepseek-r1">DeepSeek R1（慢思考）</option>
        </select>
        <button class="btn-action" onclick="switchModel()">切换</button>
        <span id="model-status" style="font-size:12px;color:#8b949e"></span>
      </div>
      <div class="meta">切换后需重启 Codex 生效 · config: ~/.codex/config.toml</div>
    </div>
  </div>

  <!-- 技能总览 -->
  <div class="card" id="skills-card" style="display:none">
    <h2>🔌 技能总览</h2>
    <div class="row" style="gap:24px;align-items:flex-start">
      <div style="flex:1">
        <div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border)">🏠 Codex 内置工具</div>
        <table style="width:100%;font-size:12px;color:var(--text);border-collapse:collapse">
          <tr><td style="padding:2px 0">exec_command</td><td style="color:#8b949e;font-size:11px">运行 shell 命令</td></tr>
          <tr><td style="padding:2px 0">apply_patch_*</td><td style="color:#8b949e;font-size:11px">增/删/改/替换/批量文件</td></tr>
          <tr><td style="padding:2px 0">web_search</td><td style="color:#8b949e;font-size:11px">网页搜索</td></tr>
          <tr><td style="padding:2px 0">view_image</td><td style="color:#8b949e;font-size:11px">查看图片</td></tr>
          <tr><td style="padding:2px 0">multi_agent_*</td><td style="color:#8b949e;font-size:11px">多 Agent 并行</td></tr>
          <tr><td style="padding:2px 0">mcp__node_repl__*</td><td style="color:#8b949e;font-size:11px">Node.js REPL 执行</td></tr>
        </table>
      </div>
      <div style="flex:1">
        <div style="font-size:12px;font-weight:600;color:var(--green);margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border)">🔧 自建 MCP 工具</div>
        <table style="width:100%;font-size:12px;color:var(--text);border-collapse:collapse">
          <tr><td style="padding:2px 0">code_stats</td><td style="color:#8b949e;font-size:11px">代码库体检（LOC、语言占比）</td></tr>
          <tr><td style="padding:2px 0">search_code</td><td style="color:#8b949e;font-size:11px">正则搜索代码</td></tr>
          <tr><td style="padding:2px 0">run_tests</td><td style="color:#8b949e;font-size:11px">运行测试命令</td></tr>
          <tr><td style="padding:2px 0">run_shell</td><td style="color:#8b949e;font-size:11px">通用 Shell 执行</td></tr>
          <tr><td style="padding:2px 0">git_diff / git_log</td><td style="color:#8b949e;font-size:11px">Git 改动 & 提交记录</td></tr>
          <tr><td style="padding:2px 0">list_dir / read_file</td><td style="color:#8b949e;font-size:11px">列目录 / 读文件</td></tr>
          <tr><td style="padding:2px 0">cache_stats</td><td style="color:#8b949e;font-size:11px">DeepSeek 缓存命中统计</td></tr>
          <tr><td style="padding:2px 0">todo_write</td><td style="color:#8b949e;font-size:11px">创建任务清单</td></tr>
          <tr><td style="padding:2px 0;border-top:1px solid var(--border);padding-top:6px">github_pr</td><td style="color:#8b949e;font-size:11px;border-top:1px solid var(--border);padding-top:6px">创建/查看 GitHub PR</td></tr>
          <tr><td style="padding:2px 0">github_issue</td><td style="color:#8b949e;font-size:11px">创建/列表 Issues</td></tr>
          <tr><td style="padding:2px 0">verify_changes</td><td style="color:#8b949e;font-size:11px">提交前验证（测试+Git+Lint）</td></tr>
          <tr><td style="padding:2px 0">check_deps</td><td style="color:#8b949e;font-size:11px">依赖安全审计</td></tr>
          <tr><td style="padding:2px 0">spike_branch</td><td style="color:#8b949e;font-size:11px">创建实验分支</td></tr>
          <tr><td style="padding:2px 0;border-top:1px solid var(--border);padding-top:6px">brainstorm</td><td style="color:#8b949e;font-size:11px;border-top:1px solid var(--border);padding-top:6px">结构化头脑风暴</td></tr>
          <tr><td style="padding:2px 0">diagnose</td><td style="color:#8b949e;font-size:11px">Bug 诊断循环</td></tr>
          <tr><td style="padding:2px 0">tdd_cycle</td><td style="color:#8b949e;font-size:11px">TDD 红绿重构流程</td></tr>
          <tr><td style="padding:2px 0">code_review_checklist</td><td style="color:#8b949e;font-size:11px">提交前审查清单</td></tr>
          <tr><td style="padding:2px 0">plan_workflow</td><td style="color:#8b949e;font-size:11px">实现计划模板</td></tr>
          <tr><td style="padding:2px 0">git_worktree</td><td style="color:#8b949e;font-size:11px">创建隔离工作树</td></tr>
          <tr><td style="padding:2px 0;border-top:1px solid var(--border);padding-top:6px">finish_branch</td><td style="color:#8b949e;font-size:11px;border-top:1px solid var(--border);padding-top:6px">分支收尾（merge+push+清理）</td></tr>
          <tr><td style="padding:2px 0">webapp_test</td><td style="color:#8b949e;font-size:11px">测试 Web 端点</td></tr>
          <tr><td style="padding:2px 0">subagent_guide</td><td style="color:#8b949e;font-size:11px">多 Agent 并行指南</td></tr>
          <tr><td style="padding:2px 0">caveman_mode</td><td style="color:#8b949e;font-size:11px">🦴 极简压缩 省75%token</td></tr>
          <tr><td style="padding:2px 0">humanize</td><td style="color:#8b949e;font-size:11px">去 AI 腔调</td></tr>
          <tr><td style="padding:2px 0">mcp_guide</td><td style="color:#8b949e;font-size:11px">MCP Server 构建教程</td></tr>
          <tr><td style="padding:2px 0">skill_guide</td><td style="color:#8b949e;font-size:11px">技能创建指南</td></tr>
          <tr><td style="padding:2px 0">review_feedback</td><td style="color:#8b949e;font-size:11px">审查反馈处理</td></tr>
          <tr><td style="padding:2px 0">github_auth</td><td style="color:#8b949e;font-size:11px">GitHub 认证设置</td></tr>
        </table>
      </div>
    </div>
    <div class="meta" style="margin-top:10px">MCP 工具通过 <code>mcp_skills_server.py</code> 加载 · 无需审核/签名 · 重启 Codex 生效</div>
  </div>

  <!-- Codex 控制 -->
  <div class="card">
    <h2>🖥 Codex 控制</h2>
    <div class="row">
      <button class="btn-action" onclick="restartCodex()" style="background:#6e40c9;border-color:#6e40c9">🔁 重启 Codex</button>
      <span id="codex-status"></span>
    </div>
    <div class="meta" id="codex-path"></div>
  </div>

  <!-- 日志 -->
  <div class="card">
    <h2>📋 运行日志</h2>
    <div class="row" style="margin-bottom:10px">
      <button class="btn-action" onclick="refreshLogs()">🔄 刷新</button>
      <button onclick="clearLogs()">🗑 清空</button>
    </div>
    <div class="log-box" id="log-box">
      <div class="log-line info">📋 控制台已就绪</div>
    </div>
  </div>

</div>

<script>
const API = 'http://127.0.0.1:38441';

function addLog(msg, cls='') {
  const box = document.getElementById('log-box');
  const time = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = 'log-line ' + cls;
  div.textContent = `[${time}] ${msg}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function refresh() {
  try {
    const r = await fetch(API + '/api/status');
    const s = await r.json();

    const dot = document.getElementById('status-dot');
    dot.className = 'status-dot ' + (s.proxy_running ? 'running' : 'stopped');

    document.getElementById('proxy-status').innerHTML = s.proxy_running
      ? '<span class="tag on">运行中</span>'
      : '<span class="tag off">已停止</span>';
    document.getElementById('pid-display').textContent = s.proxy_pid ? `PID: ${s.proxy_pid}` : '';
    document.getElementById('pid-info').textContent = s.proxy_pid ? `Moon Bridge PID: ${s.proxy_pid}` : '';

    const sel = document.getElementById('model-select');
    if (s.active_model && [...sel.options].some(o => o.value === s.active_model)) {
      sel.value = s.active_model;
    }
    document.getElementById('model-status').textContent = '当前: ' + s.active_model;

    document.getElementById('codex-status').innerHTML = s.codex_running
      ? '<span class="tag on">运行中</span>'
      : '<span class="tag off">未启动</span>';
    document.getElementById('codex-path').textContent = s.codex_path || '';

  } catch(e) {
    document.getElementById('status-dot').className = 'status-dot stopped';
    document.getElementById('proxy-status').innerHTML = '<span class="tag off">控制台离线</span>';
  }
}

async function startProxy() {
  addLog('🚀 正在启动 Moon Bridge...', 'info');
  try {
    const r = await fetch(API + '/api/proxy/start', {method:'POST'});
    const d = await r.json();
    addLog(d.ok ? `✅ Moon Bridge 已启动 (PID: ${d.pid})` : `❌ 启动失败: ${d.error}`, d.ok ? 'success' : 'error');
  } catch(e) { addLog('❌ ' + e, 'error'); }
  setTimeout(refresh, 1200);
}

async function stopProxy() {
  addLog('⏹ 正在停止 Moon Bridge...', 'info');
  try {
    const r = await fetch(API + '/api/proxy/stop', {method:'POST'});
    const d = await r.json();
    addLog(d.ok ? '⏹ Moon Bridge 已停止' : `⚠️ ${d.error}`, d.ok ? 'success' : 'error');
  } catch(e) { addLog('❌ ' + e, 'error'); }
  setTimeout(refresh, 1000);
}

async function testProxy() {
  addLog('🔍 正在测试 Moon Bridge 连接...', 'info');
  try {
    const r = await fetch(API + '/api/proxy/test');
    const d = await r.json();
    addLog(d.ok ? `✅ 代理正常，可用模型: ${d.models.join(', ')}` : `❌ ${d.error}`, d.ok ? 'success' : 'error');
  } catch(e) { addLog('❌ ' + e, 'error'); }
}

function toggleSkills() {
  const card = document.getElementById('skills-card');
  card.style.display = card.style.display === 'none' ? 'block' : 'none';
}

async function switchModel() {
  const model = document.getElementById('model-select').value;
  addLog(`🔄 切换模型 → ${model}`, 'info');
  try {
    const r = await fetch(API + '/api/model/switch', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({model})
    });
    const d = await r.json();
    addLog(d.ok ? `✅ 已切换至 ${model}（重启 Codex 后生效）` : `❌ ${d.error}`, d.ok ? 'success' : 'error');
  } catch(e) { addLog('❌ ' + e, 'error'); }
  setTimeout(refresh, 500);
}

async function restartCodex() {
  addLog('🔁 正在重启 Codex...', 'info');
  try {
    await fetch(API + '/api/codex/stop', {method:'POST'});
    addLog('  ⏹ Codex 进程已关闭', 'info');
    await new Promise(r => setTimeout(r, 2000));
    const r2 = await fetch(API + '/api/codex/start', {method:'POST'});
    const d2 = await r2.json();
    addLog(d2.ok ? '✅ Codex 已重启' : `❌ ${d2.error}`, d2.ok ? 'success' : 'error');
  } catch(e) { addLog('❌ ' + e, 'error'); }
  setTimeout(refresh, 3000);
}

async function refreshLogs() {
  try {
    const r = await fetch(API + '/api/logs');
    const d = await r.json();
    const box = document.getElementById('log-box');
    box.innerHTML = d.lines.map(l => {
      const cls = l.startsWith('❌') ? 'error' : l.startsWith('✅') ? 'success' : 'info';
      return `<div class="log-line ${cls}">${escapeHtml(l)}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  } catch(e) {}
}

async function clearLogs() {
  await fetch(API + '/api/logs/clear', {method:'POST'});
  document.getElementById('log-box').innerHTML = '<div class="log-line info">📋 日志已清空</div>';
}

function escapeHtml(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


# ── API 路由 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


@app.get("/api/status")
async def api_status():
    moon_running = _check_port_open(MOON_PORT)
    pid = _moon_bridge_proc.pid if (_moon_bridge_proc and _moon_bridge_proc.poll() is None) else None
    return {
        "proxy_running": moon_running,
        "proxy_pid": pid,
        "active_model": _get_active_model(),
        "codex_running": _check_codex_running(),
        "codex_path": _find_codex_exe() or "未找到",
    }


@app.post("/api/proxy/start")
async def api_start():
    global _moon_bridge_proc
    if _check_port_open(MOON_PORT):
        return {"ok": False, "error": "Moon Bridge 已在运行"}
    try:
        env = os.environ.copy()
        env["PATH"] = f"{HOME / 'go1.25.10' / 'bin'};{env.get('PATH', '')}"
        env["GOROOT"] = str(HOME / "go1.25.10")
        env["GOPATH"] = str(HOME / "go")
        env["GOPROXY"] = "https://goproxy.cn,direct"
        _moon_bridge_proc = subprocess.Popen(
            [GO_EXE, "run", "./cmd/moonbridge", "--config", "config.yml"],
            cwd=str(MOON_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _log(f"Moon Bridge 已启动 PID={_moon_bridge_proc.pid}", "success")
        return {"ok": True, "pid": _moon_bridge_proc.pid}
    except Exception as e:
        _log(f"Moon Bridge 启动失败: {e}", "error")
        return {"ok": False, "error": str(e)}


@app.post("/api/proxy/stop")
async def api_stop():
    global _moon_bridge_proc
    if _moon_bridge_proc and _moon_bridge_proc.poll() is None:
        _moon_bridge_proc.terminate()
        _log("Moon Bridge 已停止", "success")
        _moon_bridge_proc = None
        return {"ok": True}
    # 兜底：taskkill 清理残留进程
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "moonbridge.exe"], capture_output=True)
    return {"ok": True}


@app.post("/api/model/switch")
async def api_switch_model(request: Request):
    body = await request.json()
    model = body.get("model", "deepseek-v4-pro")
    try:
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        text = re.sub(r'^model\s*=\s*".*"', f'model = "{model}"', text, flags=re.MULTILINE)
        CODEX_CONFIG.write_text(text, encoding="utf-8")
        CODEX_REAL_CONFIG.write_text(text, encoding="utf-8")  # 同步到 Codex 实际读取的路径
        _log(f"模型已切换 → {model}", "success")
        return {"ok": True, "model": model}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.api_route("/api/proxy/test", methods=["GET", "POST"])
async def api_test():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"http://127.0.0.1:{MOON_PORT}/v1/models")
            if r.status_code == 200:
                data = r.json()
                # DeepSeek 返回 {"object":"list","data":[...]} 不是 {"models":[...]}
                items = data.get("data", data.get("models", []))
                models = [m.get("id", m.get("name", m.get("model", "?"))) for m in items]
                return {"ok": True, "models": models or ["(无模型列表，但连接正常)"]}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/logs")
async def api_logs():
    return {"lines": log_buffer[-100:]}


@app.post("/api/logs/clear")
async def api_clear_logs():
    log_buffer.clear()
    log_buffer.append("📋 日志已清空")
    return {"ok": True}


@app.get("/api/filter/stats")
async def api_filter_stats():
    return filter_stats


# ── 迷你模型切换器（小窗口用） ───────────────────────────────
QUICK_MODELS = [
    ("deepseek-v4-pro",   "V4 Pro ⚡",   "#1a6b3a", "推理增强"),
    ("deepseek-v4-flash", "V4 Flash 🚀", "#1a3a6b", "极速低延迟"),
    ("deepseek-v3",       "V3",          "#3a1a6b", "通用稳定"),
    ("deepseek-r1",       "R1 🧠",       "#6b3a1a", "慢思考推理"),
]

QUICK_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>模型切换</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     padding:12px;user-select:none}
.title{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px}
.btn{display:flex;align-items:center;justify-content:space-between;width:100%;padding:10px 14px;
     margin-bottom:6px;border:1px solid #30363d;border-radius:8px;cursor:pointer;
     font-size:13px;font-weight:600;color:#fff;transition:all .15s;text-align:left}
.btn:hover{filter:brightness(1.3);border-color:#58a6ff}
.btn.active{outline:2px solid #58a6ff;outline-offset:1px}
.sub{font-size:10px;font-weight:400;color:rgba(255,255,255,0.6)}
.status{font-size:11px;color:#58a6ff;text-align:center;padding:6px 0 2px;min-height:20px}
</style>
</head>
<body>
<div class="title">⚡ 快速切换模型</div>
BUTTONS_PLACEHOLDER
<div class="status" id="st"></div>
<script>
const API = 'http://127.0.0.1:38441';
let current = '';

async function load() {
  try {
    const r = await fetch(API + '/api/status');
    const d = await r.json();
    current = d.active_model;
    document.querySelectorAll('.btn').forEach(b => {
      b.classList.toggle('active', b.dataset.model === current);
    });
  } catch(e) {}
}

async function pick(model, name) {
  document.getElementById('st').textContent = '切换中...';
  try {
    const r = await fetch(API + '/api/model/switch', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({model})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('st').textContent = '✅ 已切换 → ' + name;
      current = model;
      document.querySelectorAll('.btn').forEach(b => {
        b.classList.toggle('active', b.dataset.model === model);
      });
    } else {
      document.getElementById('st').textContent = '❌ ' + d.error;
    }
  } catch(e) {
    document.getElementById('st').textContent = '❌ 连接失败';
  }
}

load();
setInterval(load, 3000);
</script>
</body>
</html>"""


@app.get("/quick", response_class=HTMLResponse)
async def quick_switcher():
    buttons = ""
    for model_id, label, color, desc in QUICK_MODELS:
        buttons += (
            f'<button class="btn" data-model="{model_id}" '
            f'style="background:{color}" onclick="pick(\'{model_id}\',\'{label}\')">'
            f'{label}<span class="sub">{desc}</span></button>\n'
        )
    return QUICK_HTML.replace("BUTTONS_PLACEHOLDER", buttons)


@app.post("/api/codex/stop")
async def api_codex_stop():
    try:
        if os.name == "nt":
            for name in ("Codex.exe", "codex.exe"):
                subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "codex"], capture_output=True)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/codex/start")
async def api_codex_start():
    exe = _find_codex_exe()
    if not exe:
        return {"ok": False, "error": "找不到 Codex.exe"}
    try:
        os.startfile(exe) if os.name == "nt" else subprocess.Popen([exe])
        return {"ok": True, "path": exe}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 双端口启动 ────────────────────────────────────────────────
if __name__ == "__main__":
    import threading

    async def serve_all():
        ui_cfg = uvicorn.Config(app, host="127.0.0.1", port=UI_PORT, log_level="warning")
        filter_cfg = uvicorn.Config(app, host="127.0.0.1", port=FILTER_PORT, log_level="warning")
        ui_server = uvicorn.Server(ui_cfg)
        filter_server = uvicorn.Server(filter_cfg)
        # 共用同一个 asyncio loop 同时监听两个端口
        await asyncio.gather(ui_server.serve(), filter_server.serve())

    print(f"🎛️  控制台 UI    → http://127.0.0.1:{UI_PORT}")
    print(f"🔧 Tool 过滤层  → http://127.0.0.1:{FILTER_PORT}  (Codex 指向这里)")
    print(f"⛓️  Moon Bridge  → http://127.0.0.1:{MOON_PORT}")
    asyncio.run(serve_all())
