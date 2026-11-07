"""四个内置工具：read / write / edit / bash。

设计要点（对应课件 P36 的规格）：
- read：文本按行返回、带行号；超大文件分页并告知剩余行数；图片返回 base64 多模态内容块（不塞进 text）。
- write：自动创建父目录；临时文件 + rename 原子写；覆盖时报告原来/现在行数。
- edit：精确字符串替换 + 唯一性检查 —— 出现 0 次或 2 次以上都报错，不许猜。
- bash：stdout + stderr + exit_code，非 0 正常返回；必须有超时；输出截断防上下文爆炸。

所有文件工具都走同一个沙箱：resolve() + 工作区前缀校验。模型可以"想"读任何
文件，但路径越界会被挡在目录外 —— 这一行代码比任何提示词都可靠。
"""

from __future__ import annotations

import base64
import difflib
import os
import subprocess
import sys
from pathlib import Path

# 图片扩展名 -> MIME（read 工具据此返回多模态内容块）
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

MAX_TEXT_BYTES = 256 * 1024      # read 单次返回上限
MAX_BASH_OUTPUT = 16 * 1024      # bash 输出截断阈值


class SandboxError(Exception):
    """路径越界 —— 工具层直接拒绝，不劳模型操心。"""


class ToolBox:
    """内置工具集合。绑定一个工作区根目录，全部路径校验在 resolve 里完成。"""

    def __init__(self, workspace: str | Path, cancel_event=None):
        self.workspace = Path(workspace).resolve()
        self.cancel_event = cancel_event
        # 正在运行的 bash 进程句柄，中断时用来真的杀掉它
        self.running_proc: subprocess.Popen | None = None

    # ---------- 路径沙箱 ----------

    def resolve(self, path: str) -> Path:
        """相对路径锚定在工作区；绝对路径必须落在工作区内，否则拒绝。"""
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        p = p.resolve()
        # 前缀校验：resolve 之后必须仍在工作区目录内
        if os.path.commonpath([str(p), str(self.workspace)]) != str(self.workspace):
            raise SandboxError(f"路径越界：{path} 不在工作区内（工作区：{self.workspace}）")
        return p

    # ---------- 工具 schema（发给模型看的 description 就是提示词） ----------

    def schemas(self) -> list[dict]:
        return [
            {
                "name": "read",
                "description": (
                    "读取工作区内的文件。文本文件按行返回并带行号，支持 offset/limit 分页；"
                    "图片文件返回 base64 图像内容块。文件不存在或路径越界会报错。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "工作区内相对路径，如 src/stats.py"},
                        "offset": {"type": "integer", "description": "起始行号（1-based），默认 1"},
                        "limit": {"type": "integer", "description": "最多读取行数，默认 200"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write",
                "description": (
                    "把完整内容写入文件（整体覆盖）。自动创建父目录；原子写入；"
                    "覆盖已有文件时返回原来/现在的行数。要局部修改请优先用 edit。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "工作区内相对路径"},
                        "content": {"type": "string", "description": "完整文件内容"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit",
                "description": (
                    "精确字符串替换：old 必须在文件中恰好出现一次；出现 0 次或多次都会报错，"
                    "需要补充更多上下文让它唯一。绝不整文件重写。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "工作区内相对路径"},
                        "old": {"type": "string", "description": "要被替换的原文（含足够上下文保证唯一）"},
                        "new": {"type": "string", "description": "替换后的文本"},
                    },
                    "required": ["path", "old", "new"],
                },
            },
            {
                "name": "bash",
                "description": (
                    "在工作区目录执行 shell 命令，返回 stdout/stderr/exit_code。"
                    "搜索、git、安装依赖、跑测试都用它。命令超时会被终止。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的命令"},
                        "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                    },
                    "required": ["command"],
                },
            },
        ]

    # ---------- 工具实现 ----------

    def read(self, path: str, offset: int = 1, limit: int = 200) -> dict:
        p = self.resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在：{path}")

        mime = IMAGE_MIME.get(p.suffix.lower())
        if mime:
            data = base64.b64encode(p.read_bytes()).decode()
            # 图片以多模态内容块返回，绝不塞进 text 字段
            return {"content": [{"type": "image", "mime_type": mime, "data": data}],
                    "display": f"读取图片 {path}（{p.stat().st_size} 字节）"}

        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, offset)
        chunk = lines[offset - 1 : offset - 1 + limit]
        remaining = len(lines) - (offset - 1 + len(chunk))
        body = "\n".join(f"{offset + i:>5}\t{ln}" for i, ln in enumerate(chunk))
        note = f"（还有 {remaining} 行未显示，请用 offset 继续）" if remaining > 0 else ""
        return {
            "content": [{"type": "text", "text": f"{path} 共 {len(lines)} 行，显示第 {offset}–{offset + len(chunk) - 1} 行{note}\n{body}"}],
            "display": f"读取 {path}：{len(chunk)}/{len(lines)} 行" + (f"，还有 {remaining} 行" if remaining > 0 else ""),
        }

    def write(self, path: str, content: str) -> dict:
        p = self.resolve(path)
        old_lines = len(p.read_text(encoding="utf-8", errors="replace").splitlines()) if p.exists() else 0
        p.parent.mkdir(parents=True, exist_ok=True)  # 自动创建父目录
        # 临时文件 + rename：写一半崩了也不会损坏原文件
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)
        new_lines = len(content.splitlines())
        msg = f"写入 {path}：{new_lines} 行" + (f"（原来 {old_lines} 行）" if old_lines else "（新文件）")
        return {"content": [{"type": "text", "text": msg}], "display": msg}

    def edit(self, path: str, old: str, new: str) -> dict:
        p = self.resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            raise ValueError(f"old 在 {path} 中出现 0 次，找不到目标（请先 read 核对原文）")
        if count > 1:
            raise ValueError(f"old 在 {path} 中出现 {count} 次，不唯一 —— 请补充上下文，不许猜")
        new_text = text.replace(old, new, 1)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, p)
        diff = "\n".join(
            difflib.unified_diff(text.splitlines(), new_text.splitlines(), fromfile=path, tofile=path, lineterm="", n=2)
        )
        msg = f"edit {path}：1 处替换成功"
        return {"content": [{"type": "text", "text": msg + "\n" + diff}], "display": msg, "diff": diff}

    def bash(self, command: str, timeout: int = 30) -> dict:
        timeout = max(1, min(int(timeout), 120))
        shell_exe = os.environ.get("SHELL") if sys.platform != "win32" else None
        proc = subprocess.Popen(
            command, shell=True, cwd=str(self.workspace),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            executable=shell_exe, text=True, encoding="utf-8", errors="replace",
        )
        self.running_proc = proc
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return {"content": [{"type": "text", "text": f"命令超时（{timeout}s），已终止。\nstdout:\n{out}\nstderr:\n{err}"}],
                    "display": f"bash 超时（{timeout}s）：{command}", "exit_code": -1}
        finally:
            self.running_proc = None
        if len(out) > MAX_BASH_OUTPUT:
            out = out[:MAX_BASH_OUTPUT] + f"\n…（输出截断，共 {len(out)} 字符）"
        if len(err) > MAX_BASH_OUTPUT:
            err = err[:MAX_BASH_OUTPUT] + f"\n…（输出截断）"
        text = f"exit_code: {proc.returncode}\nstdout:\n{out or '(空)'}\nstderr:\n{err or '(空)'}"
        return {"content": [{"type": "text", "text": text}],
                "display": f"bash：{command} → exit {proc.returncode}", "exit_code": proc.returncode}

    # ---------- 统一入口 ----------

    def execute(self, name: str, args: dict) -> dict:
        """执行一个内置工具。异常统一转成错误结果（不抛出），让模型能看到并自救。"""
        try:
            fn = {"read": self.read, "write": self.write, "edit": self.edit, "bash": self.bash}[name]
        except KeyError:
            return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "display": f"未知工具 {name}", "error": True}
        try:
            result = fn(**args)
        except Exception as e:  # 工具层错误 -> 文本错误，交给模型
            return {"content": [{"type": "text", "text": f"错误：{e}"}], "display": f"{name} 失败：{e}", "error": True}
        result.setdefault("display", name)
        return result

    def interrupt(self):
        """真正中断：杀掉正在跑的 bash 子进程。循环层负责停下 LLM 调用。"""
        proc = self.running_proc
        if proc and proc.poll() is None:
            proc.kill()
