"""承認ゲートの利用者の承認の記録（承認の証跡）をスプリント状態ファイルから読む・書き込みの置き換えを決める（#1426 の決定 1）。

書くのは `sprint-state.py gate`、読むのは `supervise.py design-approved`（設計承認ゲート）と
`release-steps.py approve --answer-from`（リリース承認ゲート）である。利用者の承認の記録は、`by` が無いか `user` で、
`outcome` が無いか `approved` のもの（`--outcome` を省いた既定の打ち方と、この変更より前の記録を含む。
`sprint-state.py` の `gate_word` と同じ読み方）。設計承認ゲートは `pr` も一致するものだけを数える。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sys

import clock
import jsonio
import legacy_names

DESIGN_GATE, RELEASE_GATE = (
    "設計承認ゲート",
    "リリース承認ゲート",
)  # sprint-state.py gate が書き、approved が名前で読む承認ゲートの記録の名前
_CANONICAL = {old: new for key, new in (("design", DESIGN_GATE), ("release", RELEASE_GATE)) for old in legacy_names.GATE_NAMES[key]}


@dataclass(frozen=True)
class Answer:
    """承認ゲートの利用者の承認の記録 1 件。"""

    gate: str
    by: str
    outcome: str
    pr: int | None
    at: str


def canonical(name):
    """承認ゲートの名前を正の名前へ寄せる。旧名でない名前（`MVV` を含む）はそのまま返す。"""
    return _CANONICAL.get(name, name) if isinstance(name, str) else name


def accept(name: str) -> str:
    """呼び出しで受けた名前を正の名前へ寄せる。旧名なら案内を標準エラーへ 1 行出す（`legacy_names.MODE` に関わらず受ける）。"""
    new = canonical(name)
    if new != name:
        print(legacy_names.gate_rename_notice(name, new), file=sys.stderr)
    return new


def is_user_approval(g: dict) -> bool:
    """利用者の承認の記録か（by が無いか user で、outcome が無いか approved）。"""
    return g.get("by") in (None, "user") and g.get("outcome") in (None, "approved")


def replaces(old: dict, name: str, pr: int | None) -> bool:
    """同じ承認ゲートの名（旧名の記録を含む）と同じ pr（どちらも無いを含む）の記録だけを置き換える。別の pr の記録は残す。"""
    return canonical(old.get("name")) == canonical(name) and old.get("pr") == pr


def user_keys(outcome: str | None, pr: int | None) -> dict:
    """`sprint-state.py gate --by user` の記録に足す鍵。利用者の承認は by と答えた PR を書き、承認ゲートの名と PR の組で
    置き換える（`replaces`）。"""
    return {**({"outcome": "approved", "by": "user"} if outcome else {}), **({"pr": pr} if pr else {})}


def mvv_keys(judged: dict, pr: int | None, outcome: str | None) -> dict:
    """`sprint-state.py gate --by mvv` の記録に足す鍵（MVV 判定の鍵 `judged`）。`pr` があれば答えた PR と承認を残す
    （設計 PR のマージのプランが PR ごとに引く。#3603 の I9）。`pr` が無ければ判定の鍵だけ。"""
    if not pr:
        return judged
    return {**judged, "pr": pr, **({"outcome": "approved"} if outcome == "approved" else {})}


def gates_of(state: str | Path) -> list[dict]:
    """状態ファイルの承認ゲートの記録。読めなければ空。"""
    data = jsonio.read(state, missing={}, broken={})
    gates = data.get("gates") if isinstance(data, dict) else None
    return [g for g in gates or [] if isinstance(g, dict)]


def design_gate_record(state: str | Path | None, pr: int, by: str, outcome: str) -> dict | None:
    """スプリント状態の設計承認ゲートの記録のうち、`pr`・`by`・`outcome` が一致する最後の 1 件。無ければ None。
    `parallel.py design-answer` が書く前に確かめ、`supervise.py design-approved` が MVV 判定の承認を読む（同じ条件で読み書きする）。"""
    if not state:
        return None
    hits = [
        g for g in gates_of(state)
        if canonical(g.get("name")) == DESIGN_GATE and g.get("pr") == pr and g.get("by") == by and g.get("outcome") == outcome
    ]  # fmt: skip
    return hits[-1] if hits else None


def approved(state: str | Path | None, gate: str, pr: int | None = None, after: str | None = None) -> Answer | None:
    """承認ゲート `gate` の利用者の承認の記録。旧名の記録も同じ承認ゲートとして読む。`pr` を渡せばその PR の記録だけ、`after`（ISO 8601）を渡せば
    それより後に書かれた記録だけを数える。無ければ None（状態のファイルが無い・読めないときも None）。"""
    if not state:
        return None
    since = clock.parse(after, naive="utc") if after else None
    for g in reversed(gates_of(state)):
        if canonical(g.get("name")) != canonical(gate) or not is_user_approval(g) or (pr is not None and g.get("pr") != pr):
            continue
        at = clock.parse(g.get("at"), naive="utc")
        if since is not None and (at is None or at <= since):
            continue
        return Answer(gate, g.get("by") or "user", g.get("outcome") or "approved", g.get("pr"), g.get("at") or "")
    return None
