"""PreToolUse / PostToolUse hooks。

这里不是提示词建议，而是工具执行前的代码闸门：
  1. 拒绝写 .env / *.pem / *.key
  2. 拒绝 rm -rf、git push --force、git reset --hard，并给出替代方案
  3. write / edit 成功后自动跑测试，只把三行失败摘要回注上下文
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _sensitive_path(path: str) -> bool:
    name = Path(path).name.lower()
    if name == ".env":
        return True
    if name.startswith(".env.") and name not in {".env.example", ".env.sample"}:
        return True
    return name.endswith((".pem", ".key"))


def _dangerous_command(command: str) -> tuple[str, str] | None:
    text = (command or "").strip().lower()
    if re.search(r"(^|[;&|]\s*)rm\s+[^;&|\n]*-[a-z]*r[a-z]*f", text) or \
            re.search(r"(^|[;&|]\s*)rm\s+[^;&|\n]*-[a-z]*f[a-z]*r", text) or \
            re.search(r"(^|[;&|]\s*)rm\s+[^;&|\n]*--recursive[^;&|\n]*--force", text) or \
            re.search(r"remove-item[^\n]*-recurse[^\n]*-force", text):
        return "删除不可逆，先确认目标", "先运行 git clean -nd 查看会删什么；需要恢复文件用 git restore <file>"
    if re.search(r"git\s+push[^\n]*--force(?!-with-lease)", text) or \
            re.search(r"git\s+push[^\n]*\s-f(?:\s|$)", text):
        return "强制推送会覆盖远端历史", "先 git log 核对；优先用 git revert 生成反向提交，确需覆盖时用 --force-with-lease"
    if re.search(r"git\s+reset\s+--hard", text):
        return "reset --hard 会丢弃未提交改动", "先用 git status 看改动；只撤销单个文件用 git restore <file>，已提交的回退用 git revert"
    return None


class AutoTestRunner:
    """写文件之后跑一次测试，并把失败压成三行。"""

    def __init__(self, workspace: str | Path, command: list[str] | None = None, timeout: int = 20):
        self.workspace = Path(workspace).resolve()
        self.command = command or self.detect_command()
        self.timeout = timeout
        self.runs = 0
        self.red = 0
        self.green = 0
        self.red_to_green = 0
        self.injected_lines = 0
        self.last_status: str | None = None
        self._previous_status: str | None = None
        self.last_summary = ""

    def detect_command(self) -> list[str]:
        configured = os.environ.get("AGENT_TEST_CMD")
        if configured:
            return shlex.split(configured)
        test_files = sorted(self.workspace.glob("test_*.py"))
        if len(test_files) == 1:
            return [sys.executable, test_files[0].name]
        tests_dir = self.workspace / "tests"
        if tests_dir.is_dir():
            return [sys.executable, "-m", "pytest", "-q"]
        return [sys.executable, "-m", "pytest", "-q"]

    @staticmethod
    def _summary(output: str, returncode: int) -> str:
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        error_line = next(
            (ln for ln in lines if "AssertionError" in ln or ln.startswith("E   ") or "FAILED" in ln),
            lines[-1] if lines else f"测试命令退出码 {returncode}",
        )
        case_match = re.search(r"(case\s+[\w.-]+)", error_line, re.I)
        case = case_match.group(1) if case_match else "测试脚本"
        pair = re.search(r":\s*(.+?)\s*!=\s*(.+)", error_line)
        if pair:
            actual = pair.group(1).strip().split(":", 1)[-1].strip()
            expected_actual = f"期望 {pair.group(2).strip()}，实际 {actual}"
            cause = "返回类型或返回值与测试断言不一致"
        else:
            expected_actual = error_line[:220]
            cause = "按失败用例定位实际返回值与预期值的差异"
        return "\n".join([
            f"- 用例：{case}",
            f"- 期望/实际：{expected_actual}",
            f"- 原因：{cause}",
        ])

    def run(self) -> dict:
        self.runs += 1
        try:
            proc = subprocess.run(
                self.command,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            failed = proc.returncode != 0
        except subprocess.TimeoutExpired:
            output = f"测试命令超时（{self.timeout}s）"
            failed = True
            proc = None

        status = "red" if failed else "green"
        self.last_status = status
        if failed:
            self.red += 1
            self.last_summary = self._summary(output, proc.returncode if proc else -1)
            self.injected_lines = max(self.injected_lines, len(self.last_summary.splitlines()))
        else:
            self.green += 1
            self.last_summary = "测试通过"
        if status == "green" and self._previous_status == "red":
            self.red_to_green += 1
        self._previous_status = status
        return {
            "status": status,
            "exit_code": proc.returncode if proc else -1,
            "summary": self.last_summary,
            "command": " ".join(self.command),
        }

class HookManager:
    """工具调用前后的硬约束。ENABLE_HOOKS=0 时完全退回 v1.0 行为。"""

    def __init__(self, workspace: str | Path, enabled: bool | None = None, emit=None):
        self.workspace = Path(workspace).resolve()
        self.enabled = _env_enabled("ENABLE_HOOKS") if enabled is None else enabled
        self._emit = emit or (lambda type, **data: None)
        self.denied: list[dict] = []
        self.autotest = AutoTestRunner(self.workspace)

    def _deny(self, tool: str, reason: str, alternative: str, **details) -> dict:
        record = {"tool": tool, "reason": reason, "alternative": alternative, **details, "at": time.time()}
        self.denied.append(record)
        self._emit("hook_denied", hook="PreToolUse", tool=tool, reason=reason,
                   alternative=alternative, **details)
        target = details.get("path") or details.get("command") or ""
        message = (
            f"操作被 PreToolUse hook 拒绝：{target}\n"
            f"原因：{reason}\n替代方案：{alternative}"
        )
        return {
            "content": [{"type": "text", "text": message}],
            "display": f"hook 拒绝：{reason}",
            "error": True,
            "hook": "PreToolUse",
            "reason": reason,
            "alternative": alternative,
        }

    def before_tool(self, name: str, args: dict) -> dict | None:
        if not self.enabled:
            return None
        if name in {"write", "edit"}:
            path = str(args.get("path", ""))
            if _sensitive_path(path):
                return self._deny(
                    name,
                    "密钥不入库，用环境变量",
                    "把占位值写入 .env.example，真实值从 LLM_API_KEY 环境变量读取",
                    path=path,
                )
        if name == "bash":
            command = str(args.get("command", ""))
            danger = _dangerous_command(command)
            if danger:
                return self._deny(name, danger[0], danger[1], command=command)
        return None

    def after_tool(self, name: str, args: dict, result: dict) -> dict | None:
        if not self.enabled or name not in {"write", "edit"} or result.get("error"):
            return None
        test = self.autotest.run()
        self._emit("hook_autotest", hook="PostToolUse", status=test["status"],
                   command=test["command"], summary=test["summary"])
        if test["status"] != "red":
            return None
        return {
            "content": [{"type": "text", "text": "PostToolUse hook 自动测试失败，回注摘要：\n" + test["summary"]}],
            "display": "自动测试红灯，已回注 3 行摘要",
            "hook": "PostToolUse",
            "status": "red",
            "summary": test["summary"],
        }

    def stats(self) -> dict:
        return {
            "runs": self.autotest.runs,
            "red": self.autotest.red,
            "green": self.autotest.green,
            "red_to_green": self.autotest.red_to_green,
            "injected_lines": self.autotest.injected_lines,
        }
