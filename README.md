# Codex + DeepSeek Proxy

OpenAI Codex 桌面端通过 Moon Bridge 连接 DeepSeek API，附带 30 个自建 MCP 技能。

## 架构

```
Codex 桌面端 → :38440 (Moon Bridge) → DeepSeek API
                    ↑
              协议翻译层 (Responses API → Chat Completions)
```

## 功能

- **多模型支持**：V4 Pro / V4 Flash / V3 / R1，一键切换
- **30 个 MCP 工具**：代码体检、TDD 流程、Git 工作流、GitHub PR/Issue、依赖审计、缓存命中监控、头脑风暴、Bug 诊断等
- **Web 控制台**：`http://127.0.0.1:38441` — 启停代理、切换模型、技能总览、运行日志
- **自动缓存**：提示词命中率 61%+，token 节省 50%+

## 快速开始

### 前置条件
- Windows 系统
- Go 1.25+（解压到 `~/go1.25.10/`）
- Python 3.11+（需 `mcp` 库）
- DeepSeek API Key

### 1. 启动 Moon Bridge
```bash
cd ~/moon-bridge
GOPROXY=https://goproxy.cn,direct go run ./cmd/moonbridge --config config.yml
```

### 2. 启动控制台
```bash
cd ~/Desktop/Codex
python dashboard.py
```

### 3. 配置 Codex
编辑 `~/.codex/config.toml`：
```toml
model = "deepseek-v4-pro"
model_provider = "deepseek"

[model_providers.deepseek]
name = "Moon Bridge Proxy"
wire_api = "responses"
base_url = "http://127.0.0.1:38440/v1"
```

### 4. 一键启动
双击 `打开控制台.bat` — 自动检测后台状态，没跑就拉起，跑着就打开浏览器。

## MCP 工具列表

| 类别 | 工具 |
|------|------|
| 代码分析 | `code_stats` `search_code` `read_file` `list_dir` |
| 测试 | `run_tests` `verify_changes` |
| Git | `git_diff` `git_log` `spike_branch` `git_worktree` `finish_branch` |
| GitHub | `github_pr` `github_issue` `github_auth` |
| Shell | `run_shell` |
| 安全 | `check_deps` |
| 监控 | `cache_stats` |
| 流程 | `brainstorm` `diagnose` `tdd_cycle` `code_review_checklist` `plan_workflow` |
| 指南 | `subagent_guide` `mcp_guide` `skill_guide` `review_feedback` |
| 工具 | `todo_write` `webapp_test` `caveman_mode` `humanize` |

## 切换模型

在控制台下拉选择 → 切换 → 重启 Codex。或快捷键 `Ctrl+Alt+1~4`（需运行 `model_switch.ahk`）。

## 注意事项

- `config.yml` 含 API Key，已 `.gitignore` 排除
- 国内网络需开启代理（TUN 模式）以通过 GitHub 认证
- 过滤层（`:38442`）偶发不稳定，可直接使用 `:38440`
