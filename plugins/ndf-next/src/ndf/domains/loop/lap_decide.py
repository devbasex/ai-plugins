"""周の結果の判断（RF7 の I11）。

周の結果は、リリースの事実と手順の結果から決める。上の行ほど先に当たる。

| 事実 | 結果 |
| --- | --- |
| その周で版をリリースした（記録の出した版か、締めるときに渡した版） | 完了。手順の失敗があれば理由に失敗の鍵を添える |
| リリースを承認されなかった | 人へ戻した |
| 止める要求で周を途中で締めた | 保留 |
| 失敗の印・失敗の鍵のどれかがある | 失敗 |
| 上のどれでもない | 完了 |

リリースまで届いた周を、後の手順が 1 つ落ちたことで失敗と記録しない（#2905 の lproc の 2・5・9 周目）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ndf.domains.loop import record_decide as rec


@dataclass(frozen=True)
class LapFacts:
    """周を締めるときに集めた事実。"""

    failed: bool = False
    failure_key: Optional[str] = None
    released: tuple[str, ...] = ()
    returned: bool = False
    held: bool = False
    unreadable: Optional[str] = None


def lap_outcome(facts: LapFacts) -> tuple[str, str]:
    """周の結果と理由。"""
    failure = facts.failure_key if facts.failure_key is not None else ("失敗の印" if facts.failed else None)
    note = f"（版の記録を読めない: {facts.unreadable}）" if facts.unreadable is not None else ""
    if facts.released:
        versions = "・".join(facts.released)
        tail = f"。手順の失敗 {failure} は結果を変えない" if failure is not None else ""
        return rec.DONE, f"版をリリースした（{versions}）{tail}{note}"
    if facts.returned:
        return rec.RETURNED, f"リリースを承認されずに人へ戻した{note}"
    if facts.held:
        return rec.HELD, f"止める要求で周の途中で締めた{note}"
    if failure is not None:
        return rec.FAILED, f"{failure}{note}"
    return rec.DONE, f"手順の失敗が無い{note}"


def failure_streak(state: rec.LoopState) -> int:
    """最後の周から数えた、続けて失敗した周の数。保留は数えずに読み飛ばす。"""
    count = 0
    for lap in reversed([lap for lap in state.laps if not lap.open]):
        if lap.outcome == rec.HELD:
            continue
        if lap.outcome != rec.FAILED:
            break
        count += 1
    return count
