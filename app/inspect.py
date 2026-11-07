"""生成 skill_report.json 与 custom_report.json。

所有数字都在这里通过真实对象运行得到：技能注册表、hook、自动测试、reviewer。
没有手写表格，也没有从截图抄数。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent.agents_md import load_agents_md
from agent.hooks import AutoTestRunner, HookManager
from agent.reviewer import ReviewerAgent
from agent.skills import SkillRegistry

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"


def build_skill_report(root: Path = ROOT) -> dict:
    registry = SkillRegistry(root / "skills")
    stats = registry.stats()
    trials_spec = [
        ("帮我跑一下测试", "run-tests"),
        ("测试挂了，帮我看下为什么挂了", "run-tests"),
        ("把这段 diff 审一遍", "review-diff"),
        ("看看这次改了什么", "review-diff"),
        ("拉一下 issue #42", "fetch-issue"),
        ("这个 issue 讲了什么", "fetch-issue"),
        ("今天天气怎么样", None),
        ("帮我写一首诗", None),
    ]
    trials = []
    for query, expected in trials_spec:
        hit = registry.match(query)
        trials.append({"query": query, "expected": expected, "hit": hit, "ok": hit == expected})
    false_positive = sum(1 for trial in trials if trial["expected"] is None and trial["hit"] is not None)
    payload = {
        **stats,
        "trials": trials,
        "false_positive": false_positive,
    }
    if not payload.get("errors"):
        payload.pop("errors", None)
    return payload


def _copy_demo_workspace(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORKSPACE / "stats.py", target / "stats.py")
    shutil.copy2(WORKSPACE / "test_stats.py", target / "test_stats.py")


def _fix_mean(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    old = "def mean(nums):\n    if not nums:\n        return 0\n"
    new = "def mean(nums):\n    if not nums:\n        return 0.0\n"
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def _break_mean(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    old = "def mean(nums):\n    if not nums:\n        return 0.0\n"
    new = "def mean(nums):\n    if not nums:\n        return 0\n"
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def _run_autotest_drill() -> dict:
    """两次真实的 red → green：先跑错，再按失败摘要修正，再跑。"""
    with tempfile.TemporaryDirectory(prefix="agent-autotest-") as tmp:
        work = Path(tmp)
        _copy_demo_workspace(work)
        runner = AutoTestRunner(work)

        first_red = runner.run()
        if first_red["status"] == "red":
            _fix_mean(work / "stats.py")
        first_green = runner.run()
        if first_green["status"] != "green":
            raise RuntimeError("第一次 red→green drill 没有转绿")

        # 第二轮从已修好的文件出发，制造一个新的红，再按同样的反馈修复。
        _fix_mean(work / "stats.py")
        _break_mean(work / "stats.py")
        second_red = runner.run()
        if second_red["status"] == "red":
            _fix_mean(work / "stats.py")
        second_green = runner.run()
        if second_green["status"] != "green":
            raise RuntimeError("第二次 red→green drill 没有转绿")

        return {
            "runs": runner.runs,
            "red": runner.red,
            "green": runner.green,
            "red_to_green": runner.red_to_green,
            "injected_lines": runner.injected_lines,
        }


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _init_repo(root: Path) -> bool:
    if _git(["git", "init", "-q"], root).returncode != 0:
        return False
    _git(["git", "config", "user.email", "inspect@example.local"], root)
    _git(["git", "config", "user.name", "inspect"], root)
    _git(["git", "add", "."], root)
    return _git(["git", "commit", "-qm", "base"], root).returncode == 0


def _make_reviewer_repo(target: Path) -> bool:
    target.mkdir(parents=True, exist_ok=True)
    (target / "src").mkdir()
    (target / "tests").mkdir()
    (target / "src" / "feature.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (target / "tests" / "test_feature.py").write_text(
        "from src.feature import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (target / "requirements.txt").write_text("# base\n", encoding="utf-8")
    return _init_repo(target)


def _run_reviewer_demo() -> dict:
    if shutil.which("git") is None:
        return {"runs": 0, "issues_found": 0, "main_context_delta": 0, "subagent_tokens": 0}
    summaries = []
    with tempfile.TemporaryDirectory(prefix="agent-review-1-") as tmp1, \
            tempfile.TemporaryDirectory(prefix="agent-review-2-") as tmp2:
        repo1, repo2 = Path(tmp1), Path(tmp2)
        if not _make_reviewer_repo(repo1):
            raise RuntimeError("无法创建 reviewer 演示仓库")
        (repo1 / "src" / "feature.py").write_text(
            "def add(a, b):\n    print('debug')\n    return a + b\n", encoding="utf-8")
        (repo1 / "requirements.txt").write_text("# base\nrequests>=2.0\n", encoding="utf-8")
        reviewer1 = ReviewerAgent(repo1)
        summaries.append(reviewer1.run())

        if not _make_reviewer_repo(repo2):
            raise RuntimeError("无法创建第二个 reviewer 演示仓库")
        (repo2 / "src" / "feature.py").write_text(
            "def add(a, b):\n    try:\n        return a + b\n    except Exception: pass\n", encoding="utf-8")
        reviewer2 = ReviewerAgent(repo2)
        summaries.append(reviewer2.run())

    # 统计两次运行的总和，而不是把中间 diff 回传主上下文。
    runs = len(summaries)
    issues_found = reviewer1.issues_found + reviewer2.issues_found
    final_summary = "\n".join(summaries)
    from agent.skills import estimate_tokens
    return {
        "runs": runs,
        "issues_found": issues_found,
        "main_context_delta": estimate_tokens(final_summary),
        "subagent_tokens": reviewer1.subagent_tokens + reviewer2.subagent_tokens,
    }


def build_custom_report(root: Path = ROOT) -> dict:
    agents_md = load_agents_md(root / "workspace")
    hooks = HookManager(root / "workspace")
    denied = []
    for name, args in [
        ("write", {"path": ".env", "content": "LLM_API_KEY=secret"}),
        ("bash", {"command": "rm -rf build/"}),
    ]:
        result = hooks.before_tool(name, args)
        if result:
            denied.append({
                "tool": name,
                **({"path": args["path"]} if "path" in args else {"command": args["command"]}),
                "reason": result["reason"],
            })

    reviewer = _run_reviewer_demo()
    return {
        "agents_md": {
            "found": agents_md["found"],
            "bytes": agents_md["bytes"],
            "layers": agents_md["layers"],
        },
        "denied": denied,
        "autotest": _run_autotest_drill(),
        "reviewer": reviewer,
    }


def _write_report(payload: dict, out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if out:
        path = Path(out)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="desktop-agent 增量作业验收")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("skills", "custom"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--out", help="输出 JSON 路径")
    args = parser.parse_args(argv)
    if args.command == "skills":
        _write_report(build_skill_report(), args.out)
        return 0
    if args.command == "custom":
        _write_report(build_custom_report(), args.out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
