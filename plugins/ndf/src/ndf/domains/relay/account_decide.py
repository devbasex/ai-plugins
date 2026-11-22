"""アカウントを替えるかの判断（#3336・#3337・#3864 の決定 5・11）。時計とファイルを読まない純粋な関数。

- 選ぶ順の組: 0 余裕の組（5 時間が閾値 A より下か窓が無く、7 日が閾値 B より下か窓が無い）・1 使用率の分からないもの・
  2 B 越えの組（5 時間が A より下か窓が無く、7 日が B 以上 C より下）・3 使い切りの組（5 時間が A 以上か 7 日が C 以上）。
  上限のものはどの組にも入れない。並びは組の番号 → 7 日の窓のリセットの早い順 → 7 日の使用率の大きい順 → 名前の順
- カットポイント（`before_limit`）: 今の組より番号の小さい組の候補があれば替える（同じ組なら替えない。切り替えの余裕）
- 見回り（`guard`）: 今が使い切りの組で、閾値の内側（組 0〜2）の候補があれば替える（I12）
- 上限の後（`after_limit`）: 背景の待ちが残っていても替える。替え先は使い切りの組も入れ、無ければ従量の接続、それも
  無ければ `wait_reset`。上限が解けた今のアカウントが最良の候補より悪い組でなければ同じアカウントで再開する。
  今のアカウントの行が無いとき（上限の観測の期限を過ぎ、窓も分からない）も解けたとして扱う（呼ぶ側は、上限の観測を
  書けなかったときに上限の行を足して渡す）
- 認証の失敗の後（`after_auth`）: 順の先頭、無ければ従量の接続、それも無ければ `stalled`

`candidates` は呼ぶ側が渡す起動できる登録済みアカウントの名前で、判断の関数は候補の外の名前を返さない。
"""

from __future__ import annotations

import math
from typing import NamedTuple, Optional

from ndf.domains.relay.account_registry import METERED

KEY_PREFIX = "key:"  # API キーの子の起動先の名前（`key:<キーの名前>`）
LIMITED = 4  # 今のアカウントが上限のときに比べる組の番号
DEFAULT_LOGIN = "既定のログイン"  # 今が既定のログイン（名前が None）のときに理由の文へ書く名
GROUP_NAMES = ("余裕の組", "使用率の分からないもの", "B 越えの組", "使い切りの組", "上限")


class Usage(NamedTuple):
    """アカウント 1 つの使用率。`percent` は窓の最大（一覧の表示）、`five`・`seven` は窓ごと（窓が無ければ None）。

    `percent` が None の行は、使用率は分からないが上限のもの（`limited` が真）だけである。
    """

    name: str
    percent: Optional[float]
    five: Optional[float]
    seven: Optional[float]
    seven_resets_at: Optional[float]
    limited: bool


class Guards(NamedTuple):
    """閾値 A・B・C（`relay.five-hour-guard`・`relay.weekly-prefer`・`relay.weekly-guard`）。"""

    five: float
    prefer: float
    seven: float


class Pick(NamedTuple):
    """`kind` は `keep`（替えない）・`switch`（`account` へ替える）・`wait_reset`（替える先が無い）・
    `stalled`（認証の失敗で替える先が無い）。"""

    kind: str
    account: Optional[str]
    reason: str


def metered(name: Optional[str]) -> bool:
    """従量の接続（`metered`・`key:<名前>`）の起動先か。"""
    return name is not None and (name == METERED or name.startswith(KEY_PREFIX))


def group(u: Optional[Usage], g: Guards) -> Optional[int]:
    """選ぶ順の組の番号。上限のものは None。"""
    if u is None:
        return 1
    if u.limited:
        return None
    if (u.five is not None and u.five >= g.five) or (u.seven is not None and u.seven >= g.seven):
        return 3
    return 0 if u.seven is None or u.seven < g.prefer else 2


def order(exclude: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> list[str]:
    """`candidates` から `exclude` と上限のものを除き、選ぶ順に並べた名前。"""
    by = {u.name: u for u in rows}
    keyed = []
    for name in dict.fromkeys(candidates):
        u = by.get(name)
        n = group(u, g)
        if name == exclude or n is None:
            continue
        resets = u.seven_resets_at if u is not None and u.seven_resets_at is not None else math.inf  # 分からないものは後
        seven = -u.seven if u is not None and u.seven is not None else math.inf
        keyed.append((n, resets, seven, name))
    return [key[-1] for key in sorted(keyed)]


def _group_of(name: str, rows: list[Usage], g: Guards) -> int:
    n = group(next((u for u in rows if u.name == name), None), g)
    return LIMITED if n is None else n


def _state(name: str, u: Usage, g: Guards) -> str:
    """今のアカウントの組と、組を決めた窓と閾値（名で書く）。"""
    if u.limited:
        return f"{name} が上限"
    if u.five is not None and u.five >= g.five:
        return f"{name} の 5 時間 {u.five:g}% が閾値 A {g.five:g}% 以上"
    if u.seven is not None and u.seven >= g.seven:
        return f"{name} の 7 日 {u.seven:g}% が閾値 C {g.seven:g}% 以上"
    if u.seven is not None and u.seven >= g.prefer:
        return f"{name} の 7 日 {u.seven:g}% が閾値 B {g.prefer:g}% 以上（B 越えの組）"
    return f"{name} は余裕の組"


def _to(name: str, rows: list[Usage], g: Guards) -> str:
    return f"{name}（{GROUP_NAMES[_group_of(name, rows, g)]}）"


def _back(current: str, ranked: list[str], rows: list[Usage], g: Guards) -> Pick:
    """従量の接続から登録済みアカウントへ戻る。候補が無ければそのまま。"""
    if not ranked:
        return Pick("keep", current, f"{current}（従量の接続）から戻れる登録済みアカウントが無い")
    return Pick("switch", ranked[0], f"{current}（従量の接続）から {_to(ranked[0], rows, g)} へ戻る")


def before_limit(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick:
    """カットポイントで、上限に当たる前に替えるか（決定 5）。"""
    ranked = order(current, rows, candidates, g)
    if metered(current):
        return _back(str(current), ranked, rows, g)
    now = next((u for u in rows if u.name == current), None)
    if current is None or now is None:
        return Pick("keep", current, "既定のログインか、今のアカウントの使用率が分からない（見回りと上限の後に任せる）")
    mine = _group_of(current, rows, g)
    if not ranked or _group_of(ranked[0], rows, g) >= mine:
        return Pick("keep", current, f"{_state(current, now, g)}。より前の組の候補が無いので替えない")
    return Pick("switch", ranked[0], f"{_state(current, now, g)}。{_to(ranked[0], rows, g)} へ替える")


def guard(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick:
    """見回りで、今のアカウントが使い切りの組なら閾値の内側の候補へ替えるか（決定 11・I12）。"""
    ranked = order(current, rows, candidates, g)
    if metered(current):
        return _back(str(current), ranked, rows, g)
    now = next((u for u in rows if u.name == current), None)
    if current is None or now is None or group(now, g) != 3:
        return Pick("keep", current, "今のアカウントは使い切りの組に無いか、使用率が分からない")
    if not ranked or _group_of(ranked[0], rows, g) > 2:
        return Pick("keep", current, f"{_state(current, now, g)}。閾値の内側の候補が無いので使い切る")
    return Pick("switch", ranked[0], f"{_state(current, now, g)}。{_to(ranked[0], rows, g)} へ替える")


def _fallback(current: Optional[str], fallbacks: list[str]) -> Optional[str]:
    return next((f for f in fallbacks if f != current), None)


def after_limit(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick:
    """上限に当たった後に替えるか（#3337・決定 8）。背景の待ちが残っていても替える。

    今のアカウントの行が無ければ、上限の観測の期限を過ぎて解けた（使用率の分からないもの）として扱う。
    """
    ranked = order(current, rows, candidates, g)
    now = next((u for u in rows if u.name == current), None)
    if current in candidates and (now is None or not now.limited):
        if not ranked or _group_of(current, rows, g) <= _group_of(ranked[0], rows, g):
            return Pick("switch", current, f"{current} の上限が解けた。同じアカウントで再開する")
    if ranked:
        return Pick(
            "switch", ranked[0], f"{current} が上限に当たった。{_to(ranked[0], rows, g)} で再開する。背景の待ちは再開した先が受け取る"
        )
    nxt = _fallback(current, fallbacks)
    if nxt is not None:
        return Pick("switch", nxt, f"{current} が上限に当たり、登録済みの替え先が無い。従量の接続 {nxt} で再開する")
    return Pick("wait_reset", current, f"{current} が上限に当たり、替える先が無い。リセットの後に同じアカウントで再開する")


def after_auth(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick:
    """認証の失敗の後の替え先（決定 6）。理由の文は、今が既定のログイン（None）なら名前の代わりに「既定のログイン」と書く。"""
    ranked = order(current, rows, candidates, g)
    who = DEFAULT_LOGIN if current is None else current
    if ranked:
        return Pick("switch", ranked[0], f"{who} の認証に失敗した。{_to(ranked[0], rows, g)} で再開する")
    nxt = _fallback(current, fallbacks)
    if nxt is not None:
        return Pick("switch", nxt, f"{who} の認証に失敗し、登録済みの替え先が無い。従量の接続 {nxt} で再開する")
    return Pick("stalled", current, f"{who} の認証に失敗し、替える先が無い")
