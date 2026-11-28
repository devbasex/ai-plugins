"""MVV 判定の承認を確定するときに、担い手 `mvv` のマージの承認の証跡を書く（`ndf.progress.gate` の部品。#4520 の決定 7）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ndf.adapters.git import GitError
from ndf.adapters.github import GitHub, GitHubError
from ndf.core import approval_words, merge_approval
from ndf.progress import approval_decide, model
from ndf.progress.gate.asking import Target


def ensure(hub: Callable[[], GitHub], answered: Mapping[str, Any]) -> None:
    """`service:mvv` の承認の回答（事象の payload）の証跡を、判定した対象の PR へ書く（同じ回答の証跡があれば書かない）。

    呼び手は、回答と共有する排他の中で、承認でない回答が無いことを確かめてから打つ。本文は判定の記録の見出し、判定の記録の
    コメントへのリンク、証跡の行である。承認の対象の無い判定は何も書かない。書けなければ `model.Refused`。"""
    seen = Target.from_dict(answered.get("seen"))
    target = seen.binding()
    if target is None:
        return
    held = answered[model.MVV_KEY]
    preface = f"{merge_approval.MVV_NOTE_HEADING}{model.gate_label(answered['gate'])}）\n\n判定の記録: {approval_decide.judged_note_url(held)}\n\n"
    row = merge_approval.MergeRow(
        approval_words.APPROVED, merge_approval.MVV_BY, target, seen.production is True, approval_decide.trail_record_id(answered)
    )
    try:
        made = hub()
        merge_approval.write_merge_row(made, row, made.viewer(), preface=preface)
    except (merge_approval.TrailError, GitHubError, GitError) as exc:
        raise model.Refused(f"MVV 判定の承認の証跡を書けないので、承認ゲートを済ませない。ndf progress next を打ち直す: {exc}") from None
