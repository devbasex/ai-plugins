"""承認ゲートの利用者の承認の記録（承認の証跡）をスプリント状態ファイルから読む・書き込みの置き換えを決める（#1426 の決定 1）。

書くのは `sprint-state.py gate`、読むのは `supervise.py design-approved`（承認ゲート 1）と
`release-steps.py approve --answer-from`（承認ゲート 2）である。利用者の承認の記録は、`by` が無いか `user` で、
`outcome` が無いか `approved` のもの（`--outcome` を省いた既定の打ち方と、この変更より前の記録を含む。
`sprint-state.py` の `gate_word` と同じ読み方）。承認ゲート 1 は `pr` も一致するものだけを数える。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import clock
import jsonio


@dataclass(frozen=True)
class Answer:
    """承認ゲートの利用者の承認の記録 1 件。"""

    gate: str
    by: str
    outcome: str
    pr: int | None
    at: str


def is_user_approval(g: dict) -> bool:
    """利用者の承認の記録か（by が無いか user で、outcome が無いか approved）。"""
    return g.get("by") in (None, "user") and g.get("outcome") in (None, "approved")


def replaces(old: dict, name: str, pr: int | None) -> bool:
    """同じ承認ゲートの名と同じ pr（どちらも無いを含む）の記録だけを置き換える。別の pr の記録は残す。"""
    return old.get("name") == name and old.get("pr") == pr


def gates_of(state: str | Path) -> list[dict]:
    """状態ファイルの承認ゲートの記録。読めなければ空。"""
    data = jsonio.read(state, missing={}, broken={})
    gates = data.get("gates") if isinstance(data, dict) else None
    return [g for g in gates or [] if isinstance(g, dict)]


def approved(state: str | Path | None, gate: str, pr: int | None = None, after: str | None = None) -> Answer | None:
    """承認ゲート `gate` の利用者の承認の記録。`pr` を渡せばその PR の記録だけ、`after`（ISO 8601）を渡せば
    それより後に書かれた記録だけを数える。無ければ None（状態のファイルが無い・読めないときも None）。"""
    if not state:
        return None
    since = clock.parse(after, naive="utc") if after else None
    for g in reversed(gates_of(state)):
        if g.get("name") != gate or not is_user_approval(g) or (pr is not None and g.get("pr") != pr):
            continue
        at = clock.parse(g.get("at"), naive="utc")
        if since is not None and (at is None or at <= since):
            continue
        return Answer(gate, g.get("by") or "user", g.get("outcome") or "approved", g.get("pr"), g.get("at") or "")
    return None
