"""LLM 客户端：统一接口，三种后端。

- anthropic：官方 Messages API（工具以 content block 形式返回）
- openai：任意 OpenAI 兼容 /chat/completions（DeepSeek / Qwen / Moonshot 等）
- mock：无 Key 本地联调用，按剧本返回固定的工具调用序列

统一约定：
  ask(messages, tools) -> {"text": str|None, "tool_calls": [{"id","name","arguments"}]}
配置全部从环境变量读（.env），Key 永远不进仓库。
"""

from __future__ import annotations

import json
import os
import time

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
REQUEST_TIMEOUT = 120  # LLM 请求超时（秒）—— 这也是一道护栏


class LlmError(Exception):
    pass


def _cfg() -> dict:
    return {
        "provider": os.environ.get("LLM_PROVIDER", "mock"),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        "model": os.environ.get("LLM_MODEL", ""),
    }


# ---------------------------------------------------------------- anthropic

def _ask_anthropic(messages: list[dict], tools: list[dict]) -> dict:
    cfg = _cfg()
    if not cfg["api_key"]:
        raise LlmError("未配置 LLM_API_KEY（.env）")
    # 历史里的工具结果要转回 Anthropic 的 tool_result 块格式；
    # assistant 的 tool_calls 也必须还原成 tool_use 块，否则下一轮会被拒绝。
    system_parts = []
    api_messages = []
    last_was_tool = False
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if last_was_tool:
                api_messages[-1]["content"].append(block)
            else:
                api_messages.append({"role": "user", "content": [block]})
            last_was_tool = True
            continue
        elif m["role"] == "assistant" and m.get("tool_calls"):
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for c in m["tool_calls"]:
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]})
            api_messages.append({"role": "assistant", "content": blocks})
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})
        last_was_tool = False
    body = {
        "model": cfg["model"] or "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "messages": api_messages,
        "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools],
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    url = (cfg["base_url"] or ANTHROPIC_URL).rstrip("/")
    if not url.endswith("/v1/messages"):
        url = url.removesuffix("/v1") + "/v1/messages"
    r = httpx.post(
        url, json=body, timeout=REQUEST_TIMEOUT,
        headers={"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"},
    )
    if r.status_code != 200:
        raise LlmError(f"Anthropic API {r.status_code}: {r.text[:300]}")
    data = r.json()
    text, tool_calls = "", []
    for block in data.get("content", []):
        if block["type"] == "text":
            text += block["text"]
        elif block["type"] == "tool_use":
            tool_calls.append({"id": block["id"], "name": block["name"], "arguments": block.get("input", {})})
    return {"text": text or None, "tool_calls": tool_calls}


# ------------------------------------------------------------------- openai

def _ask_openai(messages: list[dict], tools: list[dict]) -> dict:
    cfg = _cfg()
    if not cfg["api_key"]:
        raise LlmError("未配置 LLM_API_KEY（.env）")
    base = cfg["base_url"] or "https://api.openai.com/v1"
    api_messages = []
    for m in messages:
        if m["role"] == "tool":
            api_messages.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            api_messages.append({
                "role": "assistant",
                "content": m["content"] or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"],
                                             "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                               for c in m["tool_calls"]],
            })
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})
    body = {
        "model": cfg["model"] or "gpt-4o-mini",
        "messages": api_messages,
        "tools": [{"type": "function",
                   "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                  for t in tools],
    }
    r = httpx.post(f"{base}/chat/completions", json=body, timeout=REQUEST_TIMEOUT,
                   headers={"Authorization": f"Bearer {cfg['api_key']}"})
    if r.status_code != 200:
        raise LlmError(f"LLM API {r.status_code}: {r.text[:300]}")
    msg = r.json()["choices"][0]["message"]
    tool_calls = [
        {"id": c["id"], "name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")}
        for c in (msg.get("tool_calls") or [])
    ]
    return {"text": msg.get("content"), "tool_calls": tool_calls}


# --------------------------------------------------------------------- mock

_MOCK_SCRIPT = [
    # 第 1 步：先读 stats.py
    {"tool_calls": [{"id": "mock_1", "name": "read", "arguments": {"path": "stats.py"}}], "text": None},
    # 第 2 步：edit 故意失败一次（"return 0" 在文件里出现 2 次：mean 的 0 和
    # variance 的 0.0 都含它）—— 演示唯一性检查把不明确的修改挡回去
    {"tool_calls": [{"id": "mock_2", "name": "edit",
                     "arguments": {"path": "stats.py", "old": "return 0", "new": "return 0.0"}}],
     "text": None},
    # 第 3 步：改成正确的 edit
    {"tool_calls": [
        {"id": "mock_3", "name": "edit",
         "arguments": {"path": "stats.py", "old": "def mean(nums):\n    if not nums:\n        return 0\n",
                       "new": "def mean(nums):\n    if not nums:\n        return 0.0\n"}},
    ], "text": None},
    # 第 4 步：跑测试验证
    {"tool_calls": [{"id": "mock_4", "name": "bash", "arguments": {"command": "python test_stats.py", "timeout": 20}}],
     "text": None},
    # 第 5 步：MCP 调用（若已接入 echo server）
    {"tool_calls": [{"id": "mock_5", "name": "mcp__filesystem-echo__echo",
                     "arguments": {"text": "MCP 链路正常"}}], "text": None},
    # 第 6 步：收尾
    {"tool_calls": [], "text": "已完成：mean 对空列表返回 0.0（浮点，保持类型一致），测试全部通过。MCP echo 调用成功。"},
]


def _ask_mock(messages: list[dict], tools: list[dict]) -> dict:
    """按已完成步数取剧本。只认剧本，不真理解 —— 联调专用。"""
    delay = float(os.environ.get("MOCK_STEP_DELAY", "0"))
    if delay > 0:
        time.sleep(delay)
    steps_done = sum(1 for m in messages if m["role"] == "tool")
    if steps_done < len(_MOCK_SCRIPT):
        return _MOCK_SCRIPT[steps_done]
    return {"tool_calls": [], "text": "（mock）剧本已演完。"}


# ------------------------------------------------------------------- 统一入口

def ask(messages: list[dict], tools: list[dict]) -> dict:
    provider = _cfg()["provider"]
    if provider == "anthropic":
        return _ask_anthropic(messages, tools)
    if provider == "openai":
        return _ask_openai(messages, tools)
    if provider == "mock":
        return _ask_mock(messages, tools)
    raise LlmError(f"未知 LLM_PROVIDER：{provider}（可选 anthropic / openai / mock）")
