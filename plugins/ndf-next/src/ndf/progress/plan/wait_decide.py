"""オーガナイザーの `ndf lanes wait` の判断（#3097・SF9）。DB も時計も読まない純粋な関数である。

- 知らせは、渡した番号（`--after`）より後の計画の進行表の事象のうち、オーガナイザーが自分で書いたのでないもの
  （依頼が開いた・計画が完了した）をすべて返す。最新の 1 件だけを返して途中の知らせを読み飛ばさない。開いた後に
  閉じた依頼（レーンが動き出して耐久ワークフローが閉じた止まりの知らせなど。#2585）は返さない
- 計画が終わっていれば（完了・中止）、知らせが無くても待ちを終える
- 答えが載った後に conductor が next を打たないまま猶予を過ぎた行（止まり）は、まだ知らせていないもの
  （`--known-stalls` に無いもの）があれば待ちを終える
- 次に打つ `wait` は結果の `next` に載せる。番号は返した知らせの最後の番号で、答えの後も同じ規則で選ぶ
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ndf.contracts import result

# 待ちを終える計画の事象（オーガナイザー以外が書くもの）
NOTICE_KINDS = ("request_opened", "plan_completed")


@dataclass(frozen=True)
class Seen:
    """1 回の読み: 計画の状態とそれが終わり（完了か中止）か・番号より後の知らせ・最後の番号・止まりの鍵（`<レーン>/<行>`）。"""

    state: str
    ended: bool
    notices: tuple[dict[str, Any], ...]
    last_seq: int
    stalls: tuple[str, ...]


def notices(events: Iterable[dict[str, Any]], after: int) -> list[dict[str, Any]]:
    """番号 `after` より後の、待ちを終える事象（`model.Event.as_dict` の形）をすべて番号の順で返す。閉じた依頼の
    `request_opened` は返さない（もう答える要が無い）。"""
    seen = list(events)
    closed = {e["row_key"] for e in seen if e["kind"] == "request_closed"}
    return [
        e for e in seen if e["seq"] > after and e["kind"] in NOTICE_KINDS and not (e["kind"] == "request_opened" and e["row_key"] in closed)
    ]


def done(seen: Seen, known: Iterable[str]) -> bool:
    """待ちを終えるか。"""
    return bool(seen.notices) or seen.ended or bool(set(seen.stalls) - set(known))


def next_wait(plan: str, seen: Seen) -> dict[str, Any]:
    """次に打つ wait（返した番号の後から、知らせた止まりを渡して待つ）。"""
    stalls = ["--known-stalls", *seen.stalls] if seen.stalls else []
    return result.command("lanes", "wait", plan, "--after", str(seen.last_seq), *stalls)
