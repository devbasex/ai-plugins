"""取り込み 1 回分の共通手順（#728 の決定 4）。

適用・修正・最終ゲートの修正の 3 つの取り込みは、結果を読めたときも読めなかった
ときも同じことを行う。起点から HEAD までの範囲を確め、通らなければ取り消し、
起点を取り消し後の HEAD へ進める。**違うのは起点の鍵と記録先だけ**なので、その差を
`IntakeScope` で受け取り、手順そのものはここ 1 か所に置く。

**`commands` 層に置かない。** 3 つのコマンドが読む層だからである（群の進行と同じ
理由。`commands` どうしの取り込みを作らない）。git の事実の読み取り
（`gitfacts`）にも置かない。取り消しは事実の読み取りではなく進行の手順である。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import pathlib
from typing import Any, Optional

import statefile

from . import info
from .gitfacts import (
    commits_in_range,
    push_with_retry_marker,
    revert_item_commits,
)
from .paths import git_out


@dataclass
class IntakeScope:
    """取り込み 1 つ分の「どこを見て、どこへ書くか」。

    `holder` は公開の保留の印と起点を持つ辞書（提案ラウンドの控えか最終ゲートの
    控え）、`records` は結末の記録を持つ辞書（群か最終ゲートの控え）である。
    `mirror` は起点を同じ値に揃える辞書で、適用の取り込みだけが群を渡す。
    """

    holder: dict[str, Any]
    base_key: str
    records: dict[str, Any]
    phase: str
    attempt: int
    impl: str
    label: str
    mirror: Optional[dict[str, Any]] = None


@dataclass
class ClosedAttempt:
    """結果なしを閉じた結果。

    `range_unknown` が真のときは取り消しも記録も行っていない。**何で終わるかは
    呼び出し側が決める。** 適用は中断、修正と最終ゲートは既存の扱いへ戻す。
    """

    reason: str
    detail: str
    reverted: int = 0
    relaunch_same_agent: bool = True
    range_unknown: bool = False
    tried: list[str] = field(default_factory=list)


def confirm_range(state: dict[str, Any], scope: IntakeScope) -> Optional[list[str]]:
    """起点から HEAD までのコミットを新しい順で返す。確定できなければ `None`。

    **空の配列と `None` を区別する。** 空は「1 件もコミットされていない」、`None` は
    「範囲を確定できなかった」である。混同すると、確定できないときに検査が素通りする。
    """
    work = state["worktrees"]["work"]
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    return commits_in_range(work, scope.holder.get(scope.base_key), head)


def discard_unverified(
    path: pathlib.Path,
    state: dict[str, Any],
    scope: IntakeScope,
    ordered_range: list[str],
    dry_run: bool = False,
) -> int:
    """検証を受けていない範囲を取り消し、起点を取り消し後の HEAD へ進める。

    順序は「印を立てて保存 → 新しい順に取り消す → 起点を書いて保存」である。
    **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに公開できずに終わると、
    未検証の変更が Pull Request に残ったままになる。**起点はその場で保存する。**
    保存せずに落ちると、次の実行が古い起点から範囲を取り直し、取り消しコミット自体を
    「未申告」と判定して取り消しを取り消してしまう。
    """
    if not ordered_range:
        return 0
    info(f"検証を通らない変更を残さないため、{scope.label} の範囲を取り消します")
    if not dry_run:
        scope.holder["pending_push"] = True
        statefile.save(path, state)
    revert_item_commits(
        state,
        {"item_id": scope.label, "commits": list(ordered_range)},
        dry_run=dry_run,
    )
    if not dry_run:
        head = git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
        scope.holder[scope.base_key] = head
        if scope.mirror is not None:
            scope.mirror["base_sha"] = head
        statefile.save(path, state)
    return len(ordered_range)


def already_closed(scope: IntakeScope) -> bool:
    """この工程・この試行番号の結末を既に記録しているか。

    **記録していれば結果ファイルを読まない。** 叩き直しのたびに読むと、後から
    現れた結果ファイルを、取り消し済みの範囲の申告として取り込んでしまう。
    """
    return any(
        record.get("phase") == scope.phase and record.get("attempt") == scope.attempt
        for record in (scope.records.get("failed_attempts") or [])
    )


def failed_impls(scope: IntakeScope) -> list[str]:
    """この工程で結果を残さなかった担当を、記録の順に返す。"""
    return [
        str(record.get("impl") or "")
        for record in (scope.records.get("failed_attempts") or [])
        if record.get("phase") == scope.phase
    ]


def close_without_result(
    path: pathlib.Path,
    state: dict[str, Any],
    scope: IntakeScope,
    outcome: Any,
) -> ClosedAttempt:
    """結果なしの起動を閉じる。範囲の確定 → 取り消し → 結末の記録の順で行う。

    記録は追記だけで、上書きしない。担当を替えると群の担当は書き換わるが、どの担当が
    どの試行で失敗したかは記録から読める。
    """
    reason = str(outcome.reason or "missing")
    detail = str(outcome.detail or "")
    ordered_range = confirm_range(state, scope)
    if ordered_range is None:
        return ClosedAttempt(
            reason=reason,
            detail=detail,
            relaunch_same_agent=bool(outcome.relaunch_same_agent),
            range_unknown=True,
        )
    reverted = discard_unverified(path, state, scope, ordered_range)
    scope.records.setdefault("failed_attempts", []).append({
        "phase": scope.phase,
        "attempt": scope.attempt,
        "impl": scope.impl,
        "reason": reason,
        "detail": detail,
        "at": statefile.now(),
        "reverted": reverted,
    })
    statefile.save(path, state)
    if reverted:
        push_with_retry_marker(path, state, scope.holder)
    info(
        f"⚠ {scope.impl} は結果を残しませんでした（{reason}）。"
        f"取り消したコミットは {reverted} 件です"
    )
    return ClosedAttempt(
        reason=reason,
        detail=detail,
        reverted=reverted,
        relaunch_same_agent=bool(outcome.relaunch_same_agent),
        tried=failed_impls(scope),
    )
