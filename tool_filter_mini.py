"""
Minimal filter proxy — strips empty tool names from requests.
Runs on :38442, forwards to :38440. Zero magic.
"""
import asyncio, json, re, sys
from aiohttp import web, ClientSession

UPSTREAM = "http://127.0.0.1:38440"

async def handler(request):
    body = await request.read()
    try:
        data = json.loads(body)
        if "tools" in data:
            before = len(data["tools"])
            data["tools"] = [
                t for t in data["tools"]
                if t.get("function", {}).get("name", "").strip()
            ]
            after = len(data["tools"])
            if before != after:
                print(f"[filter] stripped {before - after} empty-name tools ({before}→{after})")
            body = json.dumps(data).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    async with ClientSession() as session:
        async with session.request(
            method=request.method,
            url=f"{UPSTREAM}{request.path_qs}",
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in ("host", "content-length")},
            data=body,
        ) as resp:
            return web.Response(
                status=resp.status,
                body=await resp.read(),
                headers={k: v for k, v in resp.headers.items()
                         if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")},
            )

app = web.Application()
app.router.add_route("*", "/{tail:.*}", handler)
web.run_app(app, host="127.0.0.1", port=38442, print=None)
