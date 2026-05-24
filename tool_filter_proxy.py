"""
tool_filter_proxy.py
─────────────────────────────────────────────────────────────
解决 Codex → DeepSeek 报错：
  "tools[N].function.name: empty string"

原理：在 Codex Proxy (38440) 前面加一层过滤，
把所有 function.name 为空的 tool 自动剔除再转发。

使用方法：
  pip install aiohttp
  python tool_filter_proxy.py

启动后把 Codex 的 base_url 改成 http://127.0.0.1:38442
原来的 Codex Proxy 保持在 38440 不动。
─────────────────────────────────────────────────────────────
"""

import json
import aiohttp
from aiohttp import web

# ── 配置 ──────────────────────────────────────────────────
LISTEN_PORT   = 38442          # 本过滤层监听的端口（Codex 改指向这里）
UPSTREAM      = "http://127.0.0.1:38440"  # 原 Codex Proxy 地址
# ──────────────────────────────────────────────────────────


def filter_tools(body: dict) -> dict:
    """删除 tools 列表中 function.name 为空的条目"""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body
    before = len(tools)
    tools = [
        t for t in tools
        if isinstance(t, dict)
        and t.get("function", {}).get("name", "").strip()
    ]
    after = len(tools)
    if before != after:
        print(f"[filter] 过滤掉 {before - after} 个空名 tool（剩余 {after} 个）")
    body["tools"] = tools or None   # 全空时直接删掉 tools 字段
    if body["tools"] is None:
        del body["tools"]
    return body


async def proxy(request: web.Request) -> web.StreamResponse:
    url = UPSTREAM + request.path_qs

    # 读取请求体
    raw = await request.read()

    # 尝试解析并过滤 tools
    content_type = request.headers.get("Content-Type", "")
    if "json" in content_type and raw:
        try:
            body = json.loads(raw)
            body = filter_tools(body)
            raw = json.dumps(body, ensure_ascii=False).encode()
        except Exception as e:
            print(f"[filter] JSON 解析失败，原样转发：{e}")

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }
    headers["Content-Length"] = str(len(raw))

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=request.method,
            url=url,
            headers=headers,
            data=raw,
        ) as upstream_resp:

            # 流式透传响应
            resp = web.StreamResponse(
                status=upstream_resp.status,
                headers={
                    k: v for k, v in upstream_resp.headers.items()
                    if k.lower() not in ("transfer-encoding",)
                },
            )
            await resp.prepare(request)
            async for chunk in upstream_resp.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
            return resp


app = web.Application()
app.router.add_route("*", "/{path_info:.*}", proxy)

if __name__ == "__main__":
    print(f"✅ 过滤代理已启动")
    print(f"   监听端口  : {LISTEN_PORT}")
    print(f"   上游代理  : {UPSTREAM}")
    print(f"   把 Codex 的 base_url 改为 http://127.0.0.1:{LISTEN_PORT}")
    web.run_app(app, host="127.0.0.1", port=LISTEN_PORT)
