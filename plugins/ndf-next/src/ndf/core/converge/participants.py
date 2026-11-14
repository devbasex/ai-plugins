"""相手役の割り当てと、相手役の 1 回の起動の組み立て。

- 母集合はランタイムの表（`contracts/domains/runtimes.json`）の `default_seat` が真で `review_args` を持つ行で、
  表の行の順に並べる。修正担当（`claude -p` を打つランタイム）は相手役にしない。利用者が名指した相手役は
  `review_args` を持てば `default_seat` が偽でも入れる
- 相手役は読むだけの起動で、指摘を最後の 1 行の JSON（`{"findings": [...]}`）で返す。起動の引数は表の
  `review_args` が持つ（`{workdir}` は worktree に置き換える）。問いは最後の引数で渡す
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import agents, claude
from ndf.contracts import facts, limits

RUNTIMES = "runtimes"
REVIEW_ARGS = "review_args"
# 修正担当: `claude -p` を打つランタイム（claude のアダプターの実行ファイルの行）
FIXER = next(r["id"] for r in facts.rows(RUNTIMES) if r["executable"] == claude.CLAUDE)
# 相手役・修正担当 1 回の監視の上限
REVIEW_TIMEOUT = "monitor.phase.review"


class UnknownParticipant(ValueError):
    """名指した相手役がランタイムの表に無いか、読むだけの起動の引数を持たない。"""


def _check_requested(requested: Sequence[str], able: Sequence[str]) -> None:
    """名指した相手役が読むだけの起動を持つかを確かめる。持たないものがあれば `UnknownParticipant`。"""
    bad = [r for r in requested if r not in able]
    if bad:
        raise UnknownParticipant(f"相手役 {'・'.join(bad)} はランタイムの表に無いか、読むだけの起動を持たない（{'・'.join(able)}）")


def assign(requested: Sequence[str] = (), fixer: str = FIXER) -> tuple[str, ...]:
    """相手役を決める。`requested` が空なら表の既定の席から、読むだけの起動を持つものを修正担当を除いて並べる。"""
    able = [r["id"] for r in facts.rows(RUNTIMES) if REVIEW_ARGS in r]
    if requested:
        _check_requested(requested, able)
        return tuple(dict.fromkeys(r for r in requested if r != fixer))
    seats = set(facts.ids(RUNTIMES, default_seat=True))
    return tuple(r for r in able if r in seats and r != fixer)


def review_launch(agent: str, worktree: Path, prompt: str, stem: Path, timeout: Optional[float] = None) -> agents.Launch:
    """相手役 `agent` の読むだけの 1 回の起動。出力は `stem` の名前のファイルへ書く。"""
    row = facts.row(RUNTIMES, agent)
    args = tuple(a.replace("{workdir}", str(worktree)) for a in row[REVIEW_ARGS])
    limit = timeout if timeout is not None else limits.seconds(REVIEW_TIMEOUT)
    return agents.Launch(agent, (row["executable"], *args, prompt), worktree, agents.AgentPaths.at(stem), limit)


def _decode_at(decoder: json.JSONDecoder, text: str, at: int) -> Any:
    """`at` から始まる JSON の値。JSON の始まりでなければ None。"""
    try:
        found, _ = decoder.raw_decode(text, at)
    except json.JSONDecodeError:
        # 相手役の出力の途中の `{` は JSON の始まりでない。呼び手が前の `{` を試す
        found = None
    return found


def _listed_in(found: Any, key: str) -> Optional[list[dict[str, Any]]]:
    """`{key: [...]}` の形なら辞書の要素だけを返す。ほかは None。"""
    if isinstance(found, dict) and isinstance(found.get(key), list):
        return [f for f in found[key] if isinstance(f, dict)]
    return None


def listed_of(text: str, key: str) -> Optional[list[dict[str, Any]]]:
    """担当の出力の最後の `{key: [...]}` の要素。見つからなければ None（空の並びと分ける）。

    読むだけの起動の担当はファイルを書けないため、結果を標準出力の最後の JSON で返す（cross-refactoring の提案も同じ形）。
    """
    decoder = json.JSONDecoder()
    at = text.rfind("{")
    while at >= 0:
        found = _listed_in(_decode_at(decoder, text, at), key)
        if found is not None:
            return found
        at = text.rfind("{", 0, at)
    return None


def findings_of(text: str) -> Optional[list[dict[str, Any]]]:
    """相手役の出力の最後の `{"findings": [...]}` の指摘。見つからなければ None（空の指摘と分ける）。"""
    return listed_of(text, "findings")
