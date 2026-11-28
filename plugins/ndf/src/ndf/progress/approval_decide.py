"""記録した回答が、承認ゲートを通す有効な承認かを決める（純粋な処理。ファイルもプロセスも時刻も読まない。#4520）。

有効な承認は、境界（直近の開き直し・手で終えた回答待ち・質問の出し直し）より後の、承認ゲートの最新の回答が承認で、
回答が見た対象が今の対象と一致するものである。対象を読むのは呼び手（`ndf.progress.gate`）で、ここは値を比べる。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.progress import invoke, model, stages
from ndf.progress.run_view import ANSWERED, FINISH_HELD, REOPENED, STEP_EVENT, View, event_payload

# 設計承認ゲートの区分（承認した PR のマージまで確かめる承認ゲート）
DESIGN_GATE = next(g for g in model.table()["gates"] if g == "design")


class Standing(NamedTuple):
    """承認の回答を今の対象に照らした結果。`differences` が空なら有効、`merged` は PR がマージ済みか、`head` は今の head。"""

    differences: list[str]
    merged: bool
    head: Optional[str] = None


# 照らす口（ステートマシン・承認ゲートの区分・対象の PR・承認の回答の事象）
StandingOf = Callable[[View, Optional[str], Optional[int], model.Event], Standing]


def unreadable(v: View, gate: Optional[str], pr: Optional[int], answered: model.Event) -> Standing:
    """対象を読む口を持たない呼び手の既定。承認を数えずに止める。"""
    raise model.Refused(f"承認の対象を読めないので、ステートマシン {v.machine_id} の承認を照らせない")


def gate_pr(
    context: Mapping[str, Any], stage: stages.StageDef, done: Mapping[str, Any], gate: Optional[str], *, by_hand: bool
) -> Optional[int]:
    """承認ゲート `gate` の対象の PR。設計承認ゲートは `design_pr`、ほかは `pr`。

    工程の出力は工程が終わるまで文脈に無いので、済んだステップの出力を重ねる。手で終えた工程（`by_hand`）は、
    手で書いた出力を持つ文脈を、前に流したステップの出力より先に使う。"""
    steps = stages.step_outputs(stage, done)
    merged = {**steps, **context} if by_hand else {**context, **steps}
    key = stages.DESIGN_PR if gate == DESIGN_GATE else "pr"
    number = merged.get(key)
    return number if isinstance(number, int) else None


def approval_boundary(rows: Sequence[model.Event], gate_step: str) -> int:
    """工程の事象の列で、承認を数え始める事象の番号（直近の開き直し・手で終えた回答待ち・質問の出し直し。無ければ 0）。"""
    cut = 0
    for ev in rows:
        p = event_payload(ev)
        reasked = ev.draft.kind == STEP_EVENT and p.get("step") == gate_step and p.get("status") == "waiting" and p.get("report") is None
        if ev.draft.kind in (REOPENED, FINISH_HELD) or reasked:
            cut = ev.seq
    return cut


def latest_answer(rows: Sequence[model.Event], gate: Optional[str], after: int) -> Optional[model.Event]:
    """境界より後の、承認ゲート `gate` の最新の回答。承認でなくても、前の回答へはさかのぼらない。"""
    found = None
    for ev in rows:
        p = event_payload(ev)
        if ev.seq > after and ev.draft.kind == ANSWERED and p.get("kind") == invoke.LISTEN and p.get("gate") == gate:
            found = ev
    return found


def content_differences(now: Mapping[str, Any], seen: Mapping[str, Any]) -> list[str]:
    """見た対象 `seen` と今の対象 `now`（事象の `seen` の形）の中身の違い（今の側が分かる項目だけを比べる）。

    本文と題は、見た側も値を持つときだけ比べる。MVV 判定で省いた回答は head だけを見て書くためである。"""
    out = []
    for name, key, needs_seen in (
        ("head の SHA", "head", False),
        ("承認資料の sha256", model.MATERIAL_KEY, False),
        ("PR の本文の sha256", "body_sha256", True),
        ("PR の題の sha256", "title_sha256", True),
    ):
        current, then = now.get(key), seen.get(key)
        if current is not None and then != current and (then is not None or not needs_seen):
            out.append(f"{name}（見た値 {then or '無し'}・今の値 {current}）")
    return out


def _binding_names() -> dict[str, str]:
    """承認の対象の項目（事象の `seen` のキー）→ 違いの文に書く名前。"""
    return dict(repo="リポジトリ", pr="PR の番号", base="マージ先", production="本番へ反映されるか")


def binding_differences(now: Mapping[str, Any], seen: Mapping[str, Any]) -> list[str]:
    """見た対象が持つ承認の対象（リポジトリ・PR・マージ先・本番へ反映されるか）と、今の値の違い。今読めなければ違いにする。"""
    return [
        f"{name}（見た値 {seen[key]}・今の値 {now[key] if key in now else '無し'}）"
        for key, name in _binding_names().items()
        if seen.get(key) is not None and seen[key] != now.get(key)
    ]


def differences(
    answer: Mapping[str, Any], now: Mapping[str, Any], *, gate: Optional[str], carried_head: Optional[str]
) -> list[str]:
    """回答の payload が承認として数えられない理由と、見た対象と今の対象の違い。空なら有効。

    `carried_head` は、承認の後に文書だけが加わって出し直さなかった head。今の側だけが承認の対象を持つ回答
    （対象に結び付かずに記録した承認）も違いにする。承認資料の比較は呼び手が足す（保存したファイルを読むため）。"""
    if answer.get("answer") != invoke.APPROVED:
        return ["承認の回答でない"]
    if model.mvv_answer_void(answer, gate):
        return ["数えない MVV 判定の回答"]
    held = answer.get("seen")
    seen: Mapping[str, Any] = held if isinstance(held, Mapping) else {}
    if carried_head is not None and now.get("head") == carried_head:
        seen = {**seen, "head": carried_head}
    unbound = [f"{name}（見た値 無し・今の値 {now[key]}）" for key, name in _binding_names().items() if key in now and seen.get(key) is None]
    contents = content_differences({**now, model.MATERIAL_KEY: None}, seen)
    return [*unbound, *binding_differences(now, seen), *contents]
