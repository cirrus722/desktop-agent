"""最小 MCP 客户端：纯标准库实现 stdio 传输的 JSON-RPC 2.0。

覆盖 MCP 生命周期里必需的三步：
  1. initialize        —— 握手，声明协议版本与能力
  2. tools/list        —— 拉取这个 server 暴露的工具清单
  3. tools/call        —— 调用某个工具，取回 content

之所以手写而不用 SDK：① 课程要求能说清"它从哪来"；
② MCP 本质上就是一个 JSON-RPC 协议，一百行足够说清全部机制。

MCP 工具接入后统一加 `mcp__<server>__<tool>` 前缀，和内置工具在
代码里一眼可区分（工具注册表里也有 source 字段）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "desktop-agent", "version": "1.0.0"}


class McpConnection:
    """一个 stdio MCP server 的连接。每次 tools/call 独立带 id，串行使用。"""

    def __init__(self, name: str, command: str, args: list[str], cwd: str | Path = ".", env: dict | None = None):
        self.name = name
        self.tools: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        full_env = {**__import__("os").environ, **(env or {})}
        self.proc = subprocess.Popen(
            [command, *args], cwd=str(cwd), env=full_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self._initialize()

    # ---------- JSON-RPC 底层 ----------

    def _send(self, payload: dict):
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise ConnectionError(f"MCP server「{self.name}」已退出。stderr: {err[:500]}")
        return json.loads(line)

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            while True:
                msg = self._recv()
                # 忽略 server 发来的通知（如 notifications/list_changed）
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(f"MCP「{self.name}」{method} 出错：{msg['error']}")
                    return msg.get("result", {})

    # ---------- MCP 生命周期 ----------

    def _initialize(self):
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        server_name = result.get("serverInfo", {}).get("name", self.name)
        # 握手完成通知（无 id，纯通知）
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.server_info = server_name
        self._refresh_tools()

    def _refresh_tools(self):
        result = self._request("tools/list", {})
        self.tools = result.get("tools", [])

    def list_tools(self) -> list[dict]:
        return self.tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments})
        if result.get("isError"):
            parts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
            raise RuntimeError("MCP 工具执行出错：" + "\n".join(parts))
        # content 是多模态块列表，这里取全部文本块拼接
        parts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
        return "\n".join(parts) or "(工具无文本输出)"

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


class McpManager:
    """读 mcp.json，把所有 server 的工具收进一个统一注册表。

    注册表里的每个工具带 source 字段：'builtin' 或 'mcp:<server>'，
    前端据此渲染 MCP 徽章 —— 工具从哪来，代码里说得清。
    """

    def __init__(self, config_path: str | Path, workspace: str | Path):
        self.workspace = Path(workspace)
        self.connections: dict[str, McpConnection] = {}
        self.registry: dict[str, dict] = {}  # qualified_name -> {source, schema, conn}
        path = Path(config_path)
        if not path.exists():
            return
        config = json.loads(path.read_text(encoding="utf-8"))
        # mcp.json 里的 cwd 相对路径锚定在 mcp.json 所在目录（项目根），不锚定工作区
        base_dir = path.resolve().parent
        for name, spec in config.get("servers", {}).items():
            try:
                cwd = base_dir / spec.get("cwd", ".")
                command = spec["command"]
                if command in {"python", "python3"}:
                    command = os.environ.get("DESKTOP_AGENT_PYTHON") or sys.executable
                conn = McpConnection(name, command, spec.get("args", []), cwd=cwd)
                self.connections[name] = conn
                for tool in conn.list_tools():
                    qualified = f"mcp__{name}__{tool['name']}"
                    self.registry[qualified] = {
                        "source": f"mcp:{name}",
                        "conn": conn,
                        "remote_name": tool["name"],
                        "schema": {
                            "name": qualified,
                            "description": f"[MCP:{name}] {tool.get('description', '')}",
                            "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                        },
                    }
            except Exception as e:
                print(f"[mcp] server「{name}」启动失败：{e}", file=sys.stderr)

    def mcp_schemas(self) -> list[dict]:
        return [t["schema"] for t in self.registry.values()]

    def execute(self, qualified_name: str, args: dict) -> dict:
        entry = self.registry.get(qualified_name)
        if not entry:
            return {"content": [{"type": "text", "text": f"未知 MCP 工具：{qualified_name}"}],
                    "display": f"未知 MCP 工具 {qualified_name}", "error": True}
        try:
            text = entry["conn"].call_tool(entry["remote_name"], args)
            return {"content": [{"type": "text", "text": text}],
                    "display": f"MCP {entry['remote_name']}（{entry['source']}）调用成功"}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"MCP 调用错误：{e}"}],
                    "display": f"MCP {entry['remote_name']} 失败：{e}", "error": True}

    def close_all(self):
        for conn in self.connections.values():
            conn.close()
