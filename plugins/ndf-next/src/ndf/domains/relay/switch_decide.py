"""区間を切り替えるかの判断（#2962・#2438・#2595・#3243・#3337）。

中継のラッパーが見回るたびに、今の区間の状態（シグナルファイルの中身・子の生き死に・起動し直した回数・静かな秒・
文脈の量・上限と認証の失敗）を値で渡し、次の手を受け取る。判断は時計と端末を読まない純粋な関数で、同じ状態に
同じ手を返す（端末と時計は呼ぶ側が読んで値で渡す）。擬似端末を立てずに単体のテストで確かめられる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional

from ndf.domains.relay.account_decide import Pick

# 文脈の量が上限を超えたときに中継が子へ送る 1 行（#2595。hook の外でも判定の機会を作る）
HANDOVER_PROMPT = "文脈の量が上限を超えた。引継ぎ文書を更新し、次の区間のブロック（ndf-next）を出して区間を終える"
# 上限で替えた先のアカウントで再開するときに送る 1 行（#3337）
RESUME_PROMPT = "利用上限でアカウントを替えた。中断したところから続ける"


@dataclass(frozen=True)
class SectionState:
    """見回り 1 回の区間の状態。

    - `question`: AskUserQuestion の hook が書いた問いがまだ回されていないか
    - `next_block`: Stop の hook が書いた次の区間のブロック（無ければ None）。`/goal` が未達でも同じ（#2438）
    - `next_age_seconds`: 次の区間の印を最初に書いてからの秒（`first_written_at` から。`/goal` の未達の応答が
      続いて画面が静かにならなくても、印から数えて静かな時間が過ぎれば切り替える。#2438）
    - `child_alive`・`child_exit`: 子のプロセスが生きているか・終わったときの終了コード
    - `starts_today`・`max_starts`: 今日起動し直した回数と上限
    - `idle_seconds`・`quiet_seconds`・`stall_seconds`: 子の出力が止まってからの秒・切り替えに要る静かな秒・止まりとみなす秒
    - `context_tokens`・`context_limit`・`handover_asked`: 文脈の量・切り替える量・この区間で既に引継ぎを頼んだか（#2595）
    - `limit_pick`: 利用上限に当たったときの替える先の判断（当たっていなければ None。#3337）
    - `auth_failed`: 子の画面に認証の失敗が出て、`/goal` が外れたか（#3243）
    """

    question: bool
    next_block: Optional[str]
    child_alive: bool
    child_exit: Optional[int]
    starts_today: int
    max_starts: int
    idle_seconds: float = 0.0
    next_age_seconds: float = 0.0
    quiet_seconds: float = 0.0
    stall_seconds: float = 0.0
    context_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    handover_asked: bool = False
    limit_pick: Optional[Pick] = None
    auth_failed: bool = False


class Step(NamedTuple):
    """次の手。`kind` は `wait`・`forward`・`switch`・`account`・`wait_reset`・`stalled`・`handover`・`restart`・`finish`・`stop`。

    `prompt` は子へ送る 1 行、`account` は次に使うアカウント（替えないときは None）。
    """

    kind: str
    reason: str
    prompt: str = ""
    account: Optional[str] = None


def decide(state: SectionState) -> Step:
    """次の手を決める。上から順に最初に当たった条件で決まる。"""
    if state.question:
        return Step("forward", "AskUserQuestion の問いを人かオーガナイザーへ回す（hook は待たない）")
    first = _limit_or_auth(state)
    if first is not None:
        return first
    quiet = state.idle_seconds >= state.quiet_seconds
    if state.next_block is not None:
        if state.child_alive and not quiet and state.next_age_seconds < state.quiet_seconds:
            return Step("wait", f"次の区間の印がある。静かな時間 {state.quiet_seconds:g} 秒を待つ")
        return Step("switch", "最後の応答が次の区間のブロックを持つ（/goal が未達でも切り替える）", prompt=state.next_block)
    if not state.child_alive:
        return _ended(state)
    return _alive(state, quiet)


def _limit_or_auth(state: SectionState) -> Optional[Step]:
    pick = state.limit_pick
    if pick is not None and pick.kind == "switch":
        # 替えて起こし直すのも起動の 1 回に数える（古い使用率で同じ先を選び続けても、日の上限で止まる）
        if state.starts_today >= state.max_starts:
            return Step("stop", f"{pick.reason}。今日起動し直した回数が上限（{state.max_starts} 回）に当たった")
        return Step("account", pick.reason, prompt=RESUME_PROMPT, account=pick.account)
    if pick is not None:
        return Step("wait_reset", pick.reason)
    if state.auth_failed and state.child_alive:
        return Step("stalled", "認証の失敗で /goal が外れ、子が入力を待っている。running として扱わない")
    return None


def _ended(state: SectionState) -> Step:
    if state.child_exit == 0:
        return Step("finish", "子が次の区間のブロックを書かずに終わった")
    return _restart_or_stop(state, f"子が終了コード {state.child_exit} で終わった")


def _alive(state: SectionState, quiet: bool) -> Step:
    over = state.context_limit is not None and state.context_tokens is not None and state.context_tokens >= state.context_limit
    if over and quiet and not state.handover_asked:
        return Step("handover", f"文脈の量 {state.context_tokens} が上限 {state.context_limit} を超えた", prompt=HANDOVER_PROMPT)
    if state.stall_seconds and state.idle_seconds >= state.stall_seconds:
        return _restart_or_stop(state, f"子の出力が {state.idle_seconds:g} 秒止まっている")
    return Step("wait", "子が動いている")


def _restart_or_stop(state: SectionState, why: str) -> Step:
    if state.starts_today >= state.max_starts:
        return Step("stop", f"{why}。今日起動し直した回数が上限（{state.max_starts} 回）に当たった")
    return Step("restart", why)
