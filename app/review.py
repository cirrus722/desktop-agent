"""手动运行 reviewer 子 agent：python -m app.review [--base HEAD]"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.reviewer import ReviewerAgent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    reviewer = ReviewerAgent(Path(args.workspace))
    print(reviewer.run(base=args.base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
