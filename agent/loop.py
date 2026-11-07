"""ReAct 循环：决策 -> 动作 -> 观察 -> 再决策。

写智能体最难的不是让它动，是让它停。三道护栏都在这里：
  1. max_steps   步数上限 —— 到顶就停，转人工
  2. cancel      用户中断 —— 每一步都检查，bash 进程真的被杀掉
  3. LLM 超时    请求层 120s 超时（见 llm.py）；bash 工具有自己的超时

每个关键节点都通过 emit() 发事件 —— 前端看到的每一个徽章、每一段输出，
都来自这里。"工具调用必须看得见"不是前端的事，是循环把过程吐了出来。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import llm
from .agents_md import load_agents_md
from .hooks import HookManager
from .mcp_client import McpManager
from .skills import SkillRegistry
from .tools import ToolBox

SYSTEM_PROMPT = """你是工作区里的编程助手。你可以使用这些工具：
{tool_list}

规则：
1. 改代码用 edit（精确替换），只有新建/整体重写文件才用 write；改完跑测试验证。
2. read 返回带行号，超大文件会分页 —— 注意"还有 N 行"提示。
3. 工具报错是正常信号：edit 不唯一就补充上下文重试，不要换方案硬绕。
4. 回答用中文，简洁，说明你改了什么、怎么验证的。"""


class Agent:
    def __init__(self, workspace: str | Path, emit=None, mcp_config: str | Path | None = None):
        self.workspace = Path(workspace).resolve()
        self._emit = emit or (lambda type, **data: None)
        config_path = Path(mcp_config).resolve() if mcp_config else None
        self.project_root = self._find_project_root(self.workspace, config_path)
        if config_path is None:
            config_path = self.project_root / "mcp.json"
        self.agents_md = load_agents_md(self.workspace)
        self.skills = SkillRegistry(self.project_root / "skills")
        self.hooks = HookManager(self.workspace, emit=self._emit)
        self.cancel_event = threading.Event()
        self.box = ToolBox(self.workspace, self.cancel_event)
        self.mcp = McpManager(config_path, self.workspace)

    @staticmethod
    def _find_project_root(workspace: Path, mcp_config: Path | None) -> Path:
        configured = os.environ.get("AGENT_PROJECT_ROOT")
        if configured:
            return Path(configured).expanduser().resolve()
        if mcp_config:
            return mcp_config.parent
        for parent in [workspace, *workspace.parents]:
            if (parent / "pyproject.toml").is_file() or (parent / "skills").is_dir():
                return parent
        return workspace.parent

    # ---------- 工具注册 ----------

    def all_schemas(self) -> list[dict]:
        return self.box.schemas() + self.mcp.mcp_schemas() + self.skills.schemas()

    def execute_tool(self, name: str, args: dict) -> dict:
        if name == "use_skill":
            return self.skills.use_skill(str(args.get("name", "")))
        denied = self.hooks.before_tool(name, args)
        if denied:
            return denied
        if name.startswith("mcp__"):
            result = self.mcp.execute(name, args)
        else:
            result = self.box.execute(name, args)
        feedback = self.hooks.after_tool(name, args, result)
        if feedback:
            result["content"] = result.get("content", []) + feedback.get("content", [])
            result["display"] = f"{result.get('display', name)}；{feedback.get('display', '')}".strip("；")
            result["hook_feedback"] = feedback
        return result

    def interrupt(self):
        """真中断：置位 + 杀 bash。下一次循环检查到就停。"""
        self.cancel_event.set()
        self.box.interrupt()

    # ---------- 主循环 ----------

    def run(self, user_message: str, max_steps: int | None = None, history: list[dict] | None = None):
        max_steps = max_steps or int(os.environ.get("AGENT_MAX_STEPS", 15))
        tool_names = ", ".join(t["name"] for t in self.all_schemas())
        system = SYSTEM_PROMPT.format(tool_list=tool_names)
        if self.agents_md["text"]:
            system += "\n\n" + self.agents_md["text"]
        if self.skills.enabled and self.skills.catalog_text():
            system += "\n\n" + self.skills.catalog_text()
        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            max_chars = int(os.environ.get("AGENT_HISTORY_CHARS", 12000))
            kept: list[dict] = []
            used = 0
            for item in reversed(history):
                role = item.get("role")
                content = (item.get("text") or "").strip()
                if role not in ("user", "assistant") or not content:
                    continue
                if role == "assistant" and (
                    content.startswith("模型调用失败") or content.startswith("运行异常")
                    or content.startswith("达到步数上限") or content == "(空回答)"
                ):
                    continue
                if kept and used + len(content) > max_chars:
                    break
                kept.append({"role": role, "content": content})
                used += len(content)
            for item in reversed(kept):
                if messages[-1]["role"] == item["role"]:
                    messages[-1]["content"] += "\n\n" + item["content"]
                else:
                    messages.append(item)
        messages.append({"role": "user", "content": user_message})
        self._emit("run_started", max_steps=max_steps, workspace=str(self.workspace),
                   skills=len(self.skills), agents_md_layers=self.agents_md["layers"])
        final_text = None

        for step in range(1, max_steps + 1):
            if self.cancel_event.is_set():
                self._emit("interrupted", step=step)
                final_text = "已按你的要求中断。"
                break
            try:
                resp = llm.ask(messages, self.all_schemas())
            except llm.LlmError as e:
                self._emit("llm_error", step=step, message=str(e))
                final_text = f"模型调用失败：{e}"
                break

            if resp["tool_calls"]:
                self._emit("assistant_message", step=step, text=resp["text"] or "")
                messages.append({"role": "assistant", "content": resp["text"] or "",
                                 "tool_calls": resp["tool_calls"]})
                for call in resp["tool_calls"]:
                    if self.cancel_event.is_set():
                        break
                    is_mcp = call["name"].startswith("mcp__")
                    source = "mcp" if is_mcp else ("skill" if call["name"] == "use_skill" else "builtin")
                    self._emit("tool_call", step=step, name=call["name"],
                               arguments=call["arguments"], source=source)
                    t0 = time.time()
                    result = self.execute_tool(call["name"], call["arguments"])
                    result["elapsed"] = round(time.time() - t0, 2)
                    self._emit("tool_result", step=step, name=call["name"],
                               display=result.get("display", ""), content=result["content"],
                               error=result.get("error", False), diff=result.get("diff"),
                               exit_code=result.get("exit_code"), elapsed=result["elapsed"])
                    # 工具结果进历史：内容块转纯文本，控制上下文体积
                    text_out = "\n".join(b.get("text", "[图像内容]") for b in result["content"])
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": text_out[:8000]})
                continue

            # 没有工具调用 => 最终回答
            final_text = resp["text"] or "(空回答)"
            self._emit("assistant_message", step=step, text=final_text, final=True)
            break
        else:
            self._emit("max_steps_reached", max_steps=max_steps)
            final_text = f"达到步数上限（{max_steps}），任务未完成 —— 转人工。"

        if self.cancel_event.is_set() and final_text is None:
            self._emit("interrupted")
            final_text = "已中断。"
        self._emit("run_finished", text=final_text or "")
        return final_text or ""


def run_cli():
    """python -m agent.loop "任务" —— 证明 agent 能脱离界面单独跑。"""
    import sys
    ws = Path(os.environ.get("AGENT_WORKSPACE", "."))
    task = sys.argv[1] if len(sys.argv) > 1 else "列出工作区文件并总结这个项目是做什么的"
    agent = Agent(ws, emit=lambda type, **d: print(f"[{type}]", d.get("name", "") + (" → " + d["display"] if "display" in d else "") if type in ("tool_call", "tool_result") else d if type != "run_finished" else ""))
    print("== 最终回答 ==")
    print(agent.run(task))


if __name__ == "__main__":
    run_cli()
