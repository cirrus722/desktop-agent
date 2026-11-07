"""只读 reviewer 子 agent。

它拿到的上下文只有固定清单 + `git diff` 输出；主 agent 只接收最后的摘要，
不接收中间读取和命令输出。这是为了证明子 agent 的隔离不是摆设。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .skills import estimate_tokens
from .tools import ToolBox


REVIEW_CHECKLIST = """固定检查清单：
1. 有没有改到不该改的文件（.env / *.pem / *.key / dist / build / node_modules）
2. 有没有新增依赖（requirements.txt / pyproject.toml / package.json）
3. 有没有 except: pass
4. 测试有没有覆盖新分支（改了源码却没有测试改动）
5. diff 里有没有调试残留（print( / TODO / FIXME / breakpoint(）
只回最后一条摘要，不回传中间命令输出。
"""


class ReviewerToolbox:
    """reviewer 的只读工具面：read 任意工作区文件，bash 只允许 git diff。"""

    def __init__(self, workspace: str | Path):
        self.box = ToolBox(workspace)

    def read(self, path: str, offset: int = 1, limit: int = 200) -> dict:
        return self.box.read(path, offset=offset, limit=limit)

    def bash(self, command: str, timeout: int = 20) -> dict:
        text = command.strip()
        if not re.match(r"^git\s+diff(?:\s|$)", text, re.I):
            raise PermissionError("reviewer 的 bash 只允许 git diff")
        if re.search(r"[;&|<>`]|\$\(", text):
            raise PermissionError("reviewer 的 git diff 命令不允许 shell 串联")
        return self.box.bash(text, timeout=timeout)


class ReviewerAgent:
    def __init__(self, workspace: str | Path, emit=None):
        self.workspace = Path(workspace).resolve()
        self.tools = ReviewerToolbox(self.workspace)
        self._emit = emit or (lambda type, **data: None)
        self.runs = 0
        self.issues_found = 0
        self.main_context_delta = 0
        self.subagent_tokens = 0
        self.last_summary = ""

    @staticmethod
    def _added_lines(diff: str) -> list[str]:
        return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]

    @staticmethod
    def _changed_files(diff: str) -> list[str]:
        files = []
        for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", diff, re.M):
            path = match.group(2)
            if path not in files:
                files.append(path)
        return files

    @staticmethod
    def _is_test_file(path: str) -> bool:
        name = Path(path).name
        return ("test" in name.lower() and name.endswith(".py")) or "/tests/" in path.replace("\\", "/")

    def _issues(self, diff: str) -> list[str]:
        issues: list[str] = []
        files = self._changed_files(diff)
        added = self._added_lines(diff)

        forbidden = [p for p in files if re.search(r"(^|/)(\.env|.*\.pem|.*\.key)$", p) or
                     any(part in p.split("/") for part in {"dist", "build", "node_modules", ".venv"})]
        for path in forbidden:
            issues.append(f"不该改的文件：{path}")

        for line in added:
            if re.search(r"^[ \t]*[A-Za-z0-9_.-]+\s*(==|>=|~=|!=|<=|>|<)", line) or \
                    re.search(r"^[ \t]*[\"'][A-Za-z0-9_.-]+[\"']\s*[:=]", line):
                issues.append(f"新增依赖：{line.strip()[:100]}")

        for line in added:
            if re.search(r"except(?:\s+\w+(?:\s+as\s+\w+)?)?\s*:\s*pass\b", line):
                issues.append("吞异常：出现 except: pass")
                break

        if any(not self._is_test_file(path) for path in files) and not any(self._is_test_file(path) for path in files):
            issues.append("测试缺口：源码有改动，但 diff 里没有测试改动")

        for line in added:
            if any(marker in line for marker in ("print(", "TODO", "FIXME", "breakpoint(")):
                issues.append(f"调试残留：{line.strip()[:100]}")
                break
        return issues

    def run(self, base: str = "HEAD", staged: bool = False) -> str:
        self.runs += 1
        args = "git diff --no-ext-diff --unified=3"
        if staged:
            args += " --cached"
        if base:
            args += f" {base}"
        result = self.tools.bash(args, timeout=20)
        if result.get("exit_code") != 0:
            summary = "Reviewer 无法读取 diff：当前目录没有可用的 Git 历史。"
            self.last_summary = summary
            self.main_context_delta = estimate_tokens(summary)
            self._emit("reviewer_result", status="error", summary=summary)
            return summary

        diff = "\n".join(block.get("text", "") for block in result.get("content", []))
        issues = self._issues(diff)
        self.issues_found += len(issues)
        self.subagent_tokens = estimate_tokens(REVIEW_CHECKLIST + diff)
        if issues:
            lines = [f"Reviewer 结论：发现 {len(issues)} 个问题"]
            lines.extend(f"{i}. {issue}" for i, issue in enumerate(issues, 1))
        else:
            lines = ["Reviewer 结论：固定清单内未发现问题。"]
        summary = "\n".join(lines)
        self.last_summary = summary
        self.main_context_delta = estimate_tokens(summary)
        self._emit("reviewer_result", status="ok", summary=summary,
                   issues=len(issues), subagent_tokens=self.subagent_tokens)
        return summary

    def stats(self) -> dict:
        return {
            "runs": self.runs,
            "issues_found": self.issues_found,
            "main_context_delta": self.main_context_delta,
            "subagent_tokens": self.subagent_tokens,
        }
