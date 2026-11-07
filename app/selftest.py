"""不依赖界面的最小自检：python -m app.selftest。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent.agents_md import load_agents_md
from agent.hooks import AutoTestRunner, HookManager
from agent.loop import Agent
from agent.reviewer import ReviewerAgent, ReviewerToolbox
from agent.skills import SkillRegistry
from agent.tools import SandboxError, ToolBox

ROOT = Path(__file__).resolve().parent.parent


def _check(name: str, fn, failures: list[str]) -> None:
    try:
        fn()
        print(f"ok  {name}")
    except Exception as exc:
        failures.append(f"{name}: {exc}")
        print(f"FAIL {name}: {exc}")


def test_tools() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-tools-") as tmp:
        ws = Path(tmp)
        (ws / "sub").mkdir()
        (ws / "sub" / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        box = ToolBox(ws)
        result = box.read("sub/a.txt")
        text = result["content"][0]["text"]
        assert "1\tone" in text and "2\ttwo" in text

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        (ws / "pixel.png").write_bytes(png)
        image = box.read("pixel.png")["content"][0]
        assert image["type"] == "image" and image["mime_type"] == "image/png"

        box.write("new/dir/b.txt", "hello\nworld\n")
        assert (ws / "new" / "dir" / "b.txt").is_file()
        box.edit("new/dir/b.txt", "hello", "hi")
        assert (ws / "new" / "dir" / "b.txt").read_text(encoding="utf-8").startswith("hi")

        box.write("new/dir/dup.txt", "x\nx\n")
        ambiguous = box.execute("edit", {"path": "new/dir/dup.txt", "old": "x", "new": "y"})
        assert ambiguous.get("error")

        shell = box.execute("bash", {"command": "echo hi"})
        assert shell.get("exit_code") == 0 and "hi" in shell["content"][0]["text"]
        try:
            box.resolve("../outside.txt")
        except SandboxError:
            pass
        else:
            raise AssertionError("路径越界没有被拒绝")


def test_skills() -> None:
    registry = SkillRegistry(ROOT / "skills")
    stats = registry.stats()
    assert len(stats["skills"]) >= 3
    assert stats["disclosure_ratio"] >= 5
    assert any(s["has_scripts"] for s in stats["skills"])
    assert any(s["uses_mcp"] for s in stats["skills"])
    assert registry.match("帮我跑一下测试") == "run-tests"
    assert registry.match("把这段 diff 审一遍") == "review-diff"
    assert registry.match("拉一下 issue #4") == "fetch-issue"
    assert registry.match("今天天气怎么样") is None
    loaded = registry.use_skill("review-diff")
    assert "五条清单" in loaded["content"][0]["text"]

    old = os.environ.get("ENABLE_SKILLS")
    os.environ["ENABLE_SKILLS"] = "0"
    try:
        disabled = SkillRegistry(ROOT / "skills")
        assert not disabled.enabled and disabled.schemas() == []
    finally:
        if old is None:
            os.environ.pop("ENABLE_SKILLS", None)
        else:
            os.environ["ENABLE_SKILLS"] = old


def test_agents_md() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-md-") as tmp:
        root = Path(tmp)
        ws = root / "workspace"
        ws.mkdir()
        (root / "AGENTS.md").write_text("# project map\nproject-rule\n", encoding="utf-8")
        (root / "global.md").write_text("# global\nglobal-rule\n", encoding="utf-8")
        old = os.environ.get("AGENT_GLOBAL_AGENTS")
        os.environ["AGENT_GLOBAL_AGENTS"] = str(root / "global.md")
        try:
            found = load_agents_md(ws)
        finally:
            if old is None:
                os.environ.pop("AGENT_GLOBAL_AGENTS", None)
            else:
                os.environ["AGENT_GLOBAL_AGENTS"] = old
        assert found["layers"] == ["global", "project"]
        assert found["text"].find("global-rule") < found["text"].find("project-rule")


def test_hooks_and_red_green() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-hooks-") as tmp:
        ws = Path(tmp)
        shutil.copy2(ROOT / "workspace" / "stats.py", ws / "stats.py")
        shutil.copy2(ROOT / "workspace" / "test_stats.py", ws / "test_stats.py")
        hooks = HookManager(ws)
        denied_env = hooks.before_tool("write", {"path": ".env", "content": "LLM_API_KEY=x"})
        denied_rm = hooks.before_tool("bash", {"command": "rm -rf build/"})
        assert denied_env and denied_env["reason"]
        assert denied_rm and denied_rm["alternative"]
        assert hooks.before_tool("write", {"path": ".env.example", "content": "x"}) is None

        runner = AutoTestRunner(ws)
        red = runner.run()
        assert red["status"] == "red" and len(red["summary"].splitlines()) == 3
        text = (ws / "stats.py").read_text(encoding="utf-8")
        text = text.replace("def mean(nums):\n    if not nums:\n        return 0\n",
                            "def mean(nums):\n    if not nums:\n        return 0.0\n", 1)
        (ws / "stats.py").write_text(text, encoding="utf-8")
        green = runner.run()
        assert green["status"] == "green" and runner.red_to_green == 1

        disabled = HookManager(ws, enabled=False)
        assert disabled.before_tool("bash", {"command": "rm -rf build/"}) is None


def test_reviewer() -> None:
    if shutil.which("git") is None:
        print("skip reviewer: git not found")
        return
    with tempfile.TemporaryDirectory(prefix="agent-review-") as tmp:
        repo = Path(tmp)
        (repo / "src").mkdir()
        (repo / "src" / "feature.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "selftest@example.local"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "selftest"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        (repo / "src" / "feature.py").write_text(
            "def add(a, b):\n    print('debug')\n    return a + b\n", encoding="utf-8")
        reviewer = ReviewerAgent(repo)
        summary = reviewer.run()
        assert "调试残留" in summary or "问题" in summary
        assert reviewer.main_context_delta <= 500
        try:
            ReviewerToolbox(repo).bash("echo not-allowed")
        except PermissionError:
            pass
        else:
            raise AssertionError("reviewer 没有限制 bash 命令")


def test_agent_integration() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-integration-") as tmp:
        root = Path(tmp)
        ws = root / "workspace"
        ws.mkdir()
        shutil.copytree(ROOT / "skills", root / "skills")
        shutil.copy2(ROOT / "workspace" / "stats.py", ws / "stats.py")
        shutil.copy2(ROOT / "workspace" / "test_stats.py", ws / "test_stats.py")
        (root / "mcp.json").write_text('{"servers": {}}', encoding="utf-8")
        agent = Agent(ws)
        try:
            names = {schema["name"] for schema in agent.all_schemas()}
            assert {"read", "write", "edit", "bash", "use_skill"} <= names
            loaded = agent.execute_tool("use_skill", {"name": "run-tests"})
            assert "怎么算做完了" in loaded["content"][0]["text"]
            denied = agent.execute_tool("write", {"path": ".env", "content": "x"})
            assert denied.get("error")
        finally:
            agent.mcp.close_all()

        old = os.environ.get("ENABLE_SKILLS")
        old_hooks = os.environ.get("ENABLE_HOOKS")
        os.environ["ENABLE_SKILLS"] = "0"
        os.environ["ENABLE_HOOKS"] = "0"
        try:
            disabled = Agent(ws)
            try:
                names = {schema["name"] for schema in disabled.all_schemas()}
                assert "use_skill" not in names
                assert disabled.hooks.before_tool("bash", {"command": "rm -rf build/"}) is None
            finally:
                disabled.mcp.close_all()
        finally:
            for key, old_value in (("ENABLE_SKILLS", old), ("ENABLE_HOOKS", old_hooks)):
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value


def main() -> int:
    failures: list[str] = []
    _check("tools", test_tools, failures)
    _check("skills", test_skills, failures)
    _check("agents_md", test_agents_md, failures)
    _check("hooks_red_green", test_hooks_and_red_green, failures)
    _check("reviewer", test_reviewer, failures)
    _check("agent_integration", test_agent_integration, failures)
    print(json.dumps({"ok": not failures, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
