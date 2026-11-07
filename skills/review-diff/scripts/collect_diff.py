"""收集当前 Git diff 的摘要，供 review-diff 技能使用。

只读脚本：不写文件，不提交，不替换用户当前分支。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD", help="对比的 Git revision")
    parser.add_argument("--staged", action="store_true", help="审查暂存区")
    args = parser.parse_args()

    root = find_repo_root(Path.cwd())
    if root is None:
        print(json.dumps({"error": "当前目录不在 Git 仓库内"}, ensure_ascii=False))
        return 1

    diff_args = ["diff", "--no-ext-diff", "--unified=0"]
    if args.staged:
        diff_args.append("--cached")
    if args.base:
        diff_args.append(args.base)

    stat_args = ["diff", "--stat"]
    if args.staged:
        stat_args.append("--cached")
    if args.base:
        stat_args.append(args.base)
    stat = git(root, *stat_args)
    diff = git(root, *diff_args)
    if diff.returncode != 0:
        print(json.dumps({"error": diff.stderr.strip() or "git diff 失败"}, ensure_ascii=False))
        return diff.returncode

    changed = []
    for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", diff.stdout, re.M):
        if match.group(2) not in changed:
            changed.append(match.group(2))

    suspicious = []
    for line in diff.stdout.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:].strip()
        if any(marker in text for marker in ("print(", "TODO", "FIXME", "breakpoint(")) or \
                re.search(r"except(?:\s+\w+(?:\s+as\s+\w+)?)?\s*:\s*pass\b", text):
            suspicious.append(text[:200])

    print(json.dumps({
        "repo_root": str(root),
        "base": args.base,
        "staged": args.staged,
        "changed_files": changed,
        "stat": stat.stdout.strip(),
        "suspicious_added_lines": suspicious[:20],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
