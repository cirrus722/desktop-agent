"""本地 issue-tracker MCP server（stdio，纯标准库）。

`fetch-issue` 技能通过它验证：技能可以编排已经接好的 MCP，而不是所有事情都
自己写进提示词。数据放在同目录的 issues.json，离线也能演示。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


TOOLS = [
    {
        "name": "fetch_issue",
        "description": "按编号读取本地 issue 的标题、正文、状态和标签。",
        "inputSchema": {
            "type": "object",
            "properties": {"issue_id": {"type": "string", "description": "issue 编号，例如 1 或 #1"}},
            "required": ["issue_id"],
        },
    },
    {
        "name": "list_issues",
        "description": "列出本地 issue 的编号、标题和状态。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _issues() -> dict:
    path = Path(__file__).with_name("issues.json")
    return json.loads(path.read_text(encoding="utf-8"))


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    if "id" not in msg:
        return None
    rid = msg["id"]
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "issue-tracker", "version": "1.0.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        issues = _issues()
        if name == "list_issues":
            rows = [f"#{key}: {value['title']} [{value['state']}]" for key, value in issues.items()]
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "\n".join(rows)}], "isError": False}}
        if name == "fetch_issue":
            key = str(args.get("issue_id", "")).strip().lstrip("#")
            issue = issues.get(key)
            if issue is None:
                return {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"找不到 issue #{key}"}], "isError": True}}
            text = json.dumps(issue, ensure_ascii=False, indent=2)
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
