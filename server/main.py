"""FastAPI 后端：HTTP + WebSocket + SSE 三条通道。

关键设计 —— 「关掉前端，curl -N 也要能看到事件流」：
  所有 agent 事件先进 EventBus，再由三条通道（SSE / WS / 页面）各自订阅。
  前端只是订阅者之一，不是事件的必经之路。

中断也是后端的事：POST /api/stop 置位 cancel_event 并杀掉正在跑的 bash，
前端按钮只是调了这个接口。点了之后还在转圈，等于没有这个按钮 ——
所以中断事件（interrupted）也走同一条事件流，前端据此立刻把状态收住。

会话持久化：每轮对话追加写 .session/history.json，重启后自动恢复。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.loop import Agent
from paths import prepare_runtime, resource_dir, env_file, session_dir, workspace_dir

PREPARED = prepare_runtime()
ROOT = resource_dir()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", workspace_dir()))
SESSION_DIR = session_dir()
MCP_CONFIG = Path(os.environ.get("AGENT_PROJECT_ROOT", ROOT)) / "mcp.json"

load_dotenv(env_file())


# ---------------------------------------------------------------- 事件总线

class EventBus:
    """线程安全的事件广播：一个发布者，N 个订阅者（SSE / WS / 页面）。"""

    def __init__(self):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


bus = EventBus()
current_agent: Agent | None = None
agent_lock = threading.Lock()  # 同一时刻只跑一个 agent


def emit(type: str, **data):
    import time as _t
    bus.publish({"type": type, "ts": _t.time(), **data})


# ---------------------------------------------------------------- 会话持久化

def _history_path() -> Path:
    SESSION_DIR.mkdir(exist_ok=True)
    return SESSION_DIR / "history.json"


def load_history() -> list[dict]:
    p = _history_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def append_history(role: str, text: str):
    import time as _t
    hist = load_history()
    hist.append({"role": role, "text": text, "ts": _t.time()})
    _history_path().write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- FastAPI

app = FastAPI(title="desktop-agent")


@app.post("/api/message")
async def post_message(req: Request):
    """接收用户消息，起一个后台线程跑 agent。响应立刻返回，过程看事件流。"""
    body = await req.json()
    return await handle_message(body)


async def handle_message(body: dict):
    global current_agent
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)
    with agent_lock:
        if current_agent and not current_agent.cancel_event.is_set() and getattr(current_agent, "_running", False):
            return JSONResponse({"error": "上一轮还在运行，请先中断"}, status_code=409)
        agent = Agent(WORKSPACE, emit=emit, mcp_config=MCP_CONFIG)
        agent._running = True
        current_agent = agent
        history = load_history()

    def worker():
        try:
            reply = agent.run(text, history=history)
            append_history("user", text)
            append_history("assistant", reply)
        except Exception as e:
            emit("llm_error", message=str(e))
            emit("run_finished", text=f"运行异常：{e}")
        finally:
            agent._running = False

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse({"ok": True})


@app.post("/api/stop")
def stop():
    if current_agent:
        current_agent.interrupt()
        return JSONResponse({"ok": True, "stopped": True})
    return JSONResponse({"ok": True, "stopped": False})


@app.get("/api/events")
def sse(request: Request):
    """SSE 事件流。curl -N http://127.0.0.1:8765/api/events 就能看到。"""
    q = bus.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.get_running_loop().run_in_executor(None, q.get, True, 1.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"  # 心跳，防代理断开
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.websocket("/ws")
async def ws(sock: WebSocket):
    """WebSocket 通道：页面用它收事件 + 发消息（双工）。"""
    await sock.accept()
    q = bus.subscribe()

    async def reader():
        try:
            while True:
                msg = json.loads(await sock.receive_text())
                if msg.get("type") == "message":
                    await handle_message(msg)
                elif msg.get("type") == "stop":
                    stop()
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    import asyncio as _a
    reader_task = _a.create_task(reader())
    try:
        while True:
            try:
                event = await _a.get_running_loop().run_in_executor(None, q.get, True, 1.0)
                await sock.send_text(json.dumps(event, ensure_ascii=False))
            except queue.Empty:
                await sock.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, Exception):
        reader_task.cancel()


# ---------------------------------------------------------------- 文件树 / 文件内容

def _tree(path: Path, depth: int = 0) -> list[dict]:
    SKIP = {"__pycache__", ".git", ".session", "node_modules", "dist", "build", ".venv"}
    items = []
    try:
        entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except OSError:
        return items
    for e in entries:
        if e.name in SKIP or e.name.startswith(".env"):
            continue
        if e.is_dir():
            items.append({"name": e.name, "type": "dir", "children": _tree(e, depth + 1) if depth < 6 else []})
        else:
            items.append({"name": e.name, "type": "file", "size": e.stat().st_size})
    return items


@app.get("/api/tree")
def tree():
    return JSONResponse(_tree(WORKSPACE))


@app.get("/api/file")
def file(path: str):
    from agent.tools import ToolBox
    box = ToolBox(WORKSPACE)
    try:
        p = box.resolve(path)
        if not p.exists() or p.is_dir():
            return JSONResponse({"error": "文件不存在"}, status_code=404)
        return JSONResponse({"path": path, "content": p.read_text(encoding="utf-8", errors="replace")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/history")
def history():
    return JSONResponse(load_history())


# ---------------------------------------------------------------- 页面

@app.get("/")
def index():
    return FileResponse(ROOT / "ui" / "index.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/static", StaticFiles(directory=ROOT / "ui"), name="static")
