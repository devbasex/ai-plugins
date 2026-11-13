"""既定ブランチの候補（`branch_flow.DEFAULT_BRANCH_CANDIDATES`）を 1 行 1 件で出す、シェルのスクリプト向けの入口。

`worktree-branch.sh` の `wt_default_branch` が、候補を自分で並べずにここから読む（#3309）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# `python3 -I` は自分の置き場を sys.path に足さない
sys.path.insert(0, str(Path(__file__).resolve().parent))
from branch_flow import DEFAULT_BRANCH_CANDIDATES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if sys.argv[1:] if argv is None else argv:
        print("usage: default_branch_candidates.py（引数を取らない）", file=sys.stderr)
        return 2
    print("\n".join(DEFAULT_BRANCH_CANDIDATES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
