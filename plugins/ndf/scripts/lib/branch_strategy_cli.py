"""branch_strategy_cli.py: ブランチ戦略の宣言の値をシェルから読む入口（#3672。本体は `branch_strategy.py`）。

    python3 branch_strategy_cli.py get <pointer> [--ref <ブランチ>] [--root DIR]
    python3 branch_strategy_cli.py start-ref [--root DIR]
    python3 branch_strategy_cli.py pr-target {sprint|doc|release} [--sprint-branch <名前>] [--root DIR]

宣言が無い・壊れている・旧いキーが残るときは理由（ファイル・キーの位置・打つコマンド）を標準エラーへ出して止まる。
既定値へ倒さない（I3・I4）。`--ref` の無い読み取りは、`--root` の作業ツリーでなくメインディレクトリの宣言を読む
（`repo.branch_strategy_of`。PR の変更でベースブランチなどを差し替えられないようにする。#2363）。`deps.require` を呼ぶのはこの入口だけで、読む部品（hook の経路でも読む）は呼ばない。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import deps  # noqa: E402


def _print_value(v: Any) -> None:
    sys.stdout.write((v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) + "\n")


def main(argv: list[str] | None = None) -> int:
    deps.require("jsonschema")
    import branch_strategy as bs
    import repo
    from branch_strategy import StrategyError
    from exit_codes import EXIT_UNREADABLE

    ap = argparse.ArgumentParser(prog="branch_strategy_cli.py", description="ブランチ戦略の宣言の値を 1 つ読む")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="セクションの値を JSON Pointer で 1 つ読む")
    g.add_argument("pointer")
    g.add_argument("--ref", help="このブランチ（origin/<名前>）のセクションを読む")
    g.add_argument("--root", default=".")
    s = sub.add_parser("start-ref", help="スプリントの起点のブランチ名を出す")
    s.add_argument("--root", default=".")
    t = sub.add_parser("pr-target", help="PR の種類ごとの宛先のブランチ名を出す")
    t.add_argument("kind", choices=bs.PR_KINDS)
    t.add_argument("--sprint-branch", help="doc_prs が sprint の宣言で、設計 PR の宛先にするスプリントブランチ")
    t.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "get":
            if not a.pointer.startswith("/"):
                raise StrategyError(f"pointer は / で始める: {a.pointer!r}", EXIT_UNREADABLE)
            strategy = bs.load_at_ref(Path(a.root), a.ref) if a.ref else repo.branch_strategy_of(Path(a.root))
            _print_value(bs.get_value(strategy, a.pointer))
        elif a.cmd == "start-ref":
            _print_value(repo.branch_strategy_of(Path(a.root)).sprint_start_ref())
        else:
            _print_value(repo.branch_strategy_of(Path(a.root)).pr_target(a.kind, a.sprint_branch))
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return EXIT_UNREADABLE
    except StrategyError as e:
        sys.stderr.write(e.reason + "\n")
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
