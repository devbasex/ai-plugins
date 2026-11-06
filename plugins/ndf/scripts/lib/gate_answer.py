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


def gates_of(state: str | Path) -> list[dict]:
    """状態ファイルの承認ゲートの記録。読めなければ空。"""
    data = jsonio.read(state, missing={}, broken={})
    gates = data.get("gates") if isinstance(data, dict) else None
    return [g for g in gates or [] if isinstance(g, dict)]


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
