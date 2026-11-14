"""再開の位置・次のループの 1 回の設計の起点・問いの当て先を決める判断（N1）。

どれも外の値を読まず、渡された記録と状態から決める。外の値の読み手と参照の解決は `ndf.core.refs` が持つ。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.contracts.exit_codes import GATE, OK

# 再開の扱いの「流さない」。参照が空に解けて飛ばす流す前の判断も同じ語（`refs.WHEN_EMPTY_SKIP`）
SKIP = "skip"
# ループの 1 回の状態のうち、問いを当ててよいもの（進行中と、締め直しの途中の保留）
LAP_RUNNING, LAP_HELD = "running", "held"


# --- 再開の位置 ---------------------------------------------------------------------------


def match_plan(plans: Mapping[str, Path], given: str, cwd: Path) -> list[str]:
    """`--from` に渡された名前・フルパス・相対パスを、前の起動の一覧のプラン（名前 → 絶対パス）へ照らす（#2806）。

    プランの名前・ファイル名の stem・末尾のパスの一致・`cwd` から解いた絶対パスの一致のどれかで当たった名前を返す。
    """
    want = given.strip()
    absolute = Path(os.path.normpath(Path(want) if Path(want).is_absolute() else cwd / want))
    tail = "/" + want.removeprefix("./")
    return [
        name
        for name, path in plans.items()
        if want in (name, path.stem) or path.as_posix().endswith(tail) or Path(os.path.normpath(path)) == absolute
    ]


class ResumeTarget(NamedTuple):
    """再開のときのプラン 1 本の扱い。`action` は ok（流す）・`skip`（完了で流さない）・gate（人へ戻す）。"""

    name: str
    action: str
    start: Optional[str]
    reason: str


def resume_target(name: str, digest: str, recorded: Optional[Mapping[str, Any]], start: Optional[str]) -> ResumeTarget:
    """前の起動の記録から、プラン 1 本を流すか・流さないか・人へ戻すかを決める。`--from` の開始点は常に勝つ。

    完了でハッシュが同じなら流さない。完了の後にプランが変わった（ハッシュが違う・記録にハッシュが無い）なら、
    黙って飛ばさずに人へ戻す。
    """
    if start is not None:
        return ResumeTarget(name, OK, start, f"--from で {start} から流す")
    if recorded is None or recorded.get("status") != OK:
        return ResumeTarget(name, OK, None, "前の起動で完了していない")
    if "digest" not in recorded:
        return ResumeTarget(name, GATE, None, "完了の記録にプランのハッシュが無く、変わっていないかを確かめられない")
    if recorded["digest"] != digest:
        return ResumeTarget(name, GATE, None, "完了の後にプランが変わった（--from <プラン>=<ステップ> で流し直すか決める）")
    return ResumeTarget(name, SKIP, None, "前の起動で完了し、プランは変わっていない")


# --- 設計の起点と問いの当て先 -------------------------------------------------------------


class DesignPr(NamedTuple):
    """課題の設計 PR の状態。PR が無ければ `number` が None。"""

    number: Optional[int]
    merged: bool = False
    state: str = ""
    head_sha: str = ""
    head_ref: str = ""


class DesignStart(NamedTuple):
    """次のループの 1 回の設計の起点。`skip` なら設計を飛ばす。`commit` は設計を続ける起点のコミット（無ければ新しく始める）。"""

    skip: bool
    commit: Optional[str]
    reason: str


def design_start(pr: DesignPr) -> DesignStart:
    """設計 PR の状態から、次のループの 1 回の設計の起点を決める（#2528・#2489・#2665）。"""
    if pr.number is None:
        return DesignStart(False, None, "設計 PR が無い（設計から始める）")
    if pr.merged:
        return DesignStart(True, None, f"設計 PR #{pr.number} はマージ済み（設計を飛ばす）")
    if pr.state == "open" and pr.head_sha:
        return DesignStart(False, pr.head_sha, f"設計 PR #{pr.number} の最新 {pr.head_sha[:12]} から続ける")
    return DesignStart(False, None, f"設計 PR #{pr.number} はマージされずに閉じた（設計から始める）")


class Lap(NamedTuple):
    """ループの 1 回。`status` は running・held など、`closing` は保留の 1 回を締め直している途中か。"""

    name: str
    status: str
    plans: tuple[str, ...]
    closing: bool = False


def question_target(laps: Sequence[Lap], plan: str) -> Optional[Lap]:
    """問いを出したプランが属するループの 1 回（進行中か、締め直しの途中の保留）を返す。無ければ None（#2638・#2664）。"""
    wanted = os.path.normpath(plan)
    open_laps = [lap for lap in laps if lap.status == LAP_RUNNING or (lap.status == LAP_HELD and lap.closing)]
    found = [lap for lap in open_laps if wanted in {os.path.normpath(p) for p in lap.plans}]
    return found[-1] if found else None
