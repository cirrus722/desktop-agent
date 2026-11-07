"""示例 MCP server（stdio 传输，纯标准库）。

暴露一个工具 echo(text) -> "echo: <text>"，用于离线演示 MCP 调用全流程：
initialize -> tools/list -> tools/call。mcp.json 默认指向它。

想换真的外部 server？改 mcp.json 即可，客户端逻辑一行不用动。
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "原样返回输入文本，前面加上 echo: 前缀。用于验证 MCP 链路是否通。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    }
]


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    if "id" not in msg:  # 通知，无需应答
        return None
    rid = msg["id"]
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo-server", "version": "1.0.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "echo":
            text = f"echo: {args.get('text', '')}"
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"未知方法：{method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
