"""完了した回を返すかと、その案内（#2057）。

完了の記録が残っているとき、記録の head と結果を変える引数が今と同じなら前回の結果を返す。比べられないときも前回の結果を返し、
打ち直しを強いる手段を案内する。案内に書くのは、呼ぶ側の drive が実際に持つ手段（`reopen_flag`）だけである。
cross-refactoring の drive は打ち直しの引数を持たないため、案内は記録を消して打ち直す手順を指す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Finished:
    """前回の結果を返すか（`keep`）と、標準エラーへ出す案内（`notes`）。"""

    keep: bool
    notes: tuple[str, ...] = ()


def _how(reopen_flag: str | None, discard_hint: str) -> str:
    return f"打ち直すなら {reopen_flag}" if reopen_flag else f"打ち直すなら {discard_hint}"


def finished(
    recorded: tuple[str | None, Sequence[str] | None],
    current: tuple[str | None, Sequence[str]],
    *,
    reopen_flag: str | None,
    discard_hint: str,
) -> Finished:
    """記録の (head, 引数) と今の (head, 引数) から決める。

    `reopen_flag` は drive が持つ打ち直しの引数（持たなければ None）、`discard_hint` は持たないときに案内する手順。
    """
    rec_head, rec_args = recorded
    cur_head, cur_args = current
    notes: list[str] = []
    if rec_args is None:
        notes.append(f"ℹ 完了の記録が引数を持たないため、引数を比べない（{_how(reopen_flag, discard_hint)}）")
    elif list(rec_args) != list(cur_args):
        return Finished(False, ("ℹ 結果を変える引数が前回と違うため、打ち直す",))
    if rec_head is None or cur_head is None:
        notes.append(f"ℹ head を比べられないため前回の結果を返す（{_how(reopen_flag, discard_hint)}）")
    elif rec_head != cur_head:
        return Finished(False, ("ℹ head が前回と違うため、打ち直す",))
    return Finished(True, tuple(notes))
