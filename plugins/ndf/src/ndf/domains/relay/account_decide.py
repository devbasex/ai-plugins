"""アカウントの選択（#4404 #4527）。どの場面も `choose` 1 つで決める。時計とファイルを読まない純粋な関数。

基準は「できるだけ長く続けられる」で、無駄な切り替えをしない。

- 使えるのは、使用率の API の結果（取得の記録。`Usage`）で上限でない登録済みアカウント。画面の行では決めない
- 並び（`ranked`）: 閾値の内側（5 時間 < A かつ 7 日 < C）を外側より先に、その中では明示（環境変数 → 指定）を先に、
  残りは余裕（100 − 窓の使用率の最大）の大きい順 → 名前の順。使用率の分からない（記録の無い）ものは同じ側の最後
- 替えるのは、今の起動先で続けられないときだけ:

| 場面 | 判断 |
| --- | --- |
| `start`（最初の起動） | 並びの先頭。候補が無ければ既定のログイン |
| `running`（区切り・見回り） | 今が閾値の内側なら替えない。外側なら内側の候補の先頭へ、従量の接続なら上限でない候補の先頭へ戻る |
| `limit`（上限の後） | 今が API の結果で上限でなければ同じアカウントで続ける。上限なら並びの先頭、無ければ従量の接続、無ければ `wait_reset` |
| `auth`（認証の失敗の後） | 今を除いた並びの先頭、無ければ従量の接続、無ければ `stalled` |

返す `Pick.order` が起動の順（先頭が `account`）で、呼ぶ側はこれをそのまま起動の要求にする。
`Pool.candidates` は呼ぶ側が渡す起動できる登録済みアカウントの名前で、判断は候補と `Pool.fallbacks` の外の名前を返さない。
"""

from __future__ import annotations

import math
from typing import NamedTuple, Optional, Sequence

from ndf.domains.relay.account_registry import METERED

KEY_PREFIX = "key:"  # API キーの子の起動先の名前（`key:<キーの名前>`）
DEFAULT_LOGIN = "既定のログイン"  # 今が既定のログイン（名前が None）のときに理由の文へ書く名
SCENES = ("start", "running", "limit", "auth")


class Usage(NamedTuple):
    """アカウント 1 つの使用率（使用率の API の取得の記録）。`percent` は窓の最大、`five`・`seven` は窓ごと（窓が無ければ None）。

    `percent` が None の行は、使用率は分からないが上限のもの（`limited` が真）だけである。
    """

    name: str
    percent: Optional[float]
    five: Optional[float]
    seven: Optional[float]
    seven_resets_at: Optional[float]
    limited: bool


class Guards(NamedTuple):
    """閾値 A・C（`relay.five-hour-guard`・`relay.weekly-guard`）。どちらかの窓が閾値以上なら閾値の外側。"""

    five: float
    seven: float


class Pick(NamedTuple):
    """`kind` は `keep`（替えない）・`switch`（`account` へ替える）・`wait_reset`（替える先が無い）・
    `stalled`（認証の失敗で替える先が無い）。`order` は起動の順（`switch` の先頭は `account`）。"""

    kind: str
    account: Optional[str]
    reason: str
    order: tuple[str, ...] = ()


def metered(name: Optional[str]) -> bool:
    """従量の接続（`metered`・`key:<名前>`）の起動先か。"""
    return name is not None and (name == METERED or name.startswith(KEY_PREFIX))


def inside(u: Optional[Usage], g: Guards) -> bool:
    """閾値の内側か（上限でなく、5 時間が A より下か窓が無く、7 日が C より下か窓が無い）。分からないものは内側。"""
    if u is None:
        return True
    if u.limited:
        return False
    return not ((u.five is not None and u.five >= g.five) or (u.seven is not None and u.seven >= g.seven))


def _headroom(u: Optional[Usage]) -> float:
    """余裕（100 − 窓の使用率の最大）。分からないものは -inf（同じ側の最後）。"""
    if u is None or u.percent is None:
        return -math.inf
    return 100.0 - max(v for v in (u.five, u.seven, u.percent) if v is not None)


def ranked(exclude: Optional[str], rows: Sequence[Usage], candidates: Sequence[str], g: Guards, explicit: Sequence[str] = ()) -> list[str]:
    """`candidates` から `exclude` と上限のものを除き、選ぶ順に並べた名前（モジュールの説明の「並び」）。"""
    by = {u.name: u for u in rows}
    first = {name: i for i, name in enumerate(dict.fromkeys(n for n in explicit if n))}
    keyed = []
    for name in dict.fromkeys(candidates):
        u = by.get(name)
        if name == exclude or (u is not None and u.limited):
            continue
        keyed.append((not inside(u, g), first[name] if name in first else len(first), -_headroom(u), name))
    return [key[-1] for key in sorted(keyed)]


def _label(name: str, rows: Sequence[Usage], g: Guards) -> str:
    u = next((r for r in rows if r.name == name), None)
    if u is None or u.percent is None:
        return f"{name}（使用率が分からない）"
    return f"{name}（{u.percent:g}%{'' if inside(u, g) else '・閾値の外側'}）"


class Pool(NamedTuple):
    """選べる起動先。`candidates` は起動できる登録済みアカウント、`fallbacks` は従量の接続（起動できる順）、
    `explicit` は明示の名前（環境変数 → 指定の順。空文字は無いものとして読む）。"""

    candidates: Sequence[str]
    fallbacks: Sequence[str] = ()
    explicit: Sequence[str] = ()


def _start(rows: Sequence[Usage], pool: Pool, g: Guards) -> Pick:
    order = ranked(None, rows, pool.candidates, g, pool.explicit)
    if not order:
        return Pick("keep", None, "起動できる登録済みアカウントが無い。既定のログインで起動する")
    return Pick("switch", order[0], f"{_label(order[0], rows, g)} で起動する", tuple(order))


def _running(current: Optional[str], rows: Sequence[Usage], pool: Pool, g: Guards) -> Pick:
    now = next((u for u in rows if u.name == current), None)
    order = ranked(current, rows, pool.candidates, g, pool.explicit)
    if current is None or (not metered(current) and inside(now, g)):
        who = DEFAULT_LOGIN if current is None else current
        return Pick("keep", current, f"{who} で続ける", tuple(n for n in (current, *order) if n is not None))
    if not metered(current):
        order = [n for n in order if inside(next((u for u in rows if u.name == n), None), g)]
    if not order:
        return Pick("keep", current, f"{_label(current, rows, g)} より長く続けられる候補が無い", (current,))
    return Pick("switch", order[0], f"{_label(current, rows, g)} から {_label(order[0], rows, g)} へ替える", tuple(order))


def _after(scene: str, current: Optional[str], rows: Sequence[Usage], pool: Pool, g: Guards) -> Pick:
    """`limit`・`auth` の判断（モジュールの説明の表）。"""
    who = DEFAULT_LOGIN if current is None else current
    now = next((u for u in rows if u.name == current), None)
    usable = current is not None and not metered(current) and current in pool.candidates
    if scene == "limit" and usable and not (now is not None and now.limited):
        return Pick("switch", current, f"{current} は API の結果で上限でない。同じアカウントで続ける", (current,))
    tail = [f for f in pool.fallbacks if f != current]
    order = ranked(current, rows, pool.candidates, g, pool.explicit)
    what = f"{who} が上限に当たった" if scene == "limit" else f"{who} の認証に失敗した"
    if order:
        return Pick("switch", order[0], f"{what}。{_label(order[0], rows, g)} で再開する", tuple(order + tail))
    if tail:
        return Pick("switch", tail[0], f"{what}。登録済みの替え先が無いので従量の接続 {tail[0]} で再開する", tuple(tail))
    if scene == "limit":
        return Pick("wait_reset", current, f"{what}。替える先が無い。リセットの後に同じアカウントで再開する")
    return Pick("stalled", current, f"{what}。替える先が無い")


def choose(scene: str, current: Optional[str], rows: Sequence[Usage], pool: Pool, g: Guards) -> Pick:
    """場面 `scene` での起動先（モジュールの説明の表）。`pool` は選べる起動先（候補・従量の接続・明示の名前）。"""
    if scene not in SCENES:
        raise ValueError(f"場面 {scene} は無い")
    if scene == "start":
        return _start(rows, pool, g)
    if scene == "running":
        return _running(current, rows, pool, g)
    return _after(scene, current, rows, pool, g)
