"""検査のトリガーの行数の判定（旧 `scripts/check-trigger.py` の `lines`。#3087）。

    ndf loop check-trigger --base <範囲の始め> [--head <範囲の終わり>] [--exclude <SHA>...] [--threshold <行数>]

- 立てば ok（items に立ったトリガー）、立たなければ not_applicable で止まる。範囲か宣言を読めなければ invalid
- 閾値の既定は対象のリポジトリの `.ndf/pace.json` の `triggers.lines`。`--threshold` を渡せば宣言を読まない
- `--exclude` は数えない PR（検査・リリース）の取り込みのコミット

範囲（前回の検査から今まで）の変更行数（追加 + 削除）が `.ndf/pace.json` の `triggers.lines` を超えたら立つ。
行数は `Git.changed_lines` で、範囲の first-parent の各コミットの変更行数を足して数え、除く PR（検査・リリース）の
コミットの分は数えない。範囲全体の差分から除く PR の差分を引くと、同じファイルを順に書き換えるリリースの PR の
重なりを二重に引き、行数が実際より小さくなる（0 へ潰れる）ためである。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.adapters.git import Git, GitError
from ndf.contracts import result

# 結果の tool は親の副命令 `ndf loop` の名前
TOOL = cli.tool_of(f"{__package__}.cli")
LINES = "lines"
# 閾値の宣言（対象のリポジトリのルートからの位置）
PACE = Path(identity.FIXED_WORDS["declaration_dir"]) / "pace.json"


@dataclass(frozen=True)
class Fired:
    """立ったトリガー（名前・測った値・閾値）。"""

    trigger: str
    value: int
    threshold: int


@dataclass(frozen=True)
class LinesJudgement:
    """範囲の行数と、行数のトリガーが立ったか（立たなければ `fired` は None）。"""

    lines: int
    fired: Optional[Fired]


def lines_fired(lines: int, threshold: int) -> Optional[Fired]:
    """行数が閾値を超えたら（等しいときは立たない）行数のトリガーを返す。"""
    return Fired(LINES, lines, threshold) if lines > threshold else None


def judge_lines(git: Git, base: str, head: str, exclude: Iterable[str], threshold: int) -> LinesJudgement:
    """`base..head` の行数（`exclude` のコミットの分を除く）を数え、行数のトリガーを判定する。"""
    lines = git.changed_lines(base, head, exclude)
    return LinesJudgement(lines, lines_fired(lines, threshold))


def declared_threshold(root: Path) -> int:
    """`.ndf/pace.json` の `triggers.lines`。無い・形が違えば ValueError。"""
    try:
        value = json.loads((root / PACE).read_text(encoding="utf-8"))["triggers"][LINES]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{PACE} の triggers.{LINES} を読めない: {exc!r}") from None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{PACE} の triggers.{LINES} が整数でない: {value!r}")
    return value


def run(a: argparse.Namespace) -> dict[str, Any]:
    g = Git(a.cwd)
    try:
        threshold = a.threshold if a.threshold is not None else declared_threshold(Path(g.run("rev-parse", "--show-toplevel").strip()))
        got = judge_lines(g, a.base, a.head, a.exclude, threshold)
    except (ValueError, GitError) as exc:
        return result.stopped(TOOL, "invalid", f"検査のトリガーを判定できない: {exc}")
    span = f"{a.base}..{a.head}"
    metrics = {LINES: got.lines, "threshold": threshold, "excluded": len(a.exclude)}
    if got.fired is None:
        return result.stopped(
            TOOL, "not_applicable", f"検査のトリガーは立たない: 範囲 {span} の変更 {got.lines} 行 ≤ {threshold} 行", metrics=metrics
        )
    fired = {"trigger": got.fired.trigger, "value": got.fired.value, "threshold": got.fired.threshold}
    return result.result(
        TOOL, result.OK, f"検査のトリガーが立った（{LINES}）: 範囲 {span} の変更 {got.lines} 行 > {threshold} 行", [fired], metrics
    )


def add_parser(sub: Any) -> None:
    """`ndf loop` の副命令の並びへ `check-trigger` を足す。"""
    p = sub.add_parser("check-trigger", help="範囲の変更行数で検査のトリガーが立つかを判定する")
    p.add_argument("--base", required=True, help="範囲の始め（前回の検査の位置）")
    p.add_argument("--head", default="HEAD", help="範囲の終わり")
    p.add_argument("--exclude", nargs="*", default=[], help="数えない PR（検査・リリース）の取り込みのコミット")
    p.add_argument("--threshold", type=int, default=None, help=f"行数の閾値（既定は {PACE} の triggers.lines）")
    p.set_defaults(handler=run)
