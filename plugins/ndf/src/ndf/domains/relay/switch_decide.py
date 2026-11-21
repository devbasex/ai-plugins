"""セグメントを切り替えるかの判断（#2962・#2438・#2595・#3243・#3337）。

中継のラッパーが見回るたびに、今のセグメントの状態（シグナルファイルの中身・子の生き死に・静かな秒・文脈の量・
上限と認証の失敗・再起動ループの検出）を値で渡し、次の手を受け取る。起動の回数は入力に持たず、回数では止めない（#3864 の I15）。判断は時計と端末を読まない純粋な関数で、同じ状態に
同じ手を返す（端末と時計は呼ぶ側が読んで値で渡す）。擬似端末を立てずに単体のテストで確かめられる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional

from ndf.domains.relay.account_decide import Pick

# 文脈の量が上限を超えたときに中継が子へ送る 1 行（#2595。hook の外でも判定の機会を作る）
HANDOVER_PROMPT = "文脈の量が上限を超えた。引継ぎ文書を更新し、次のセグメントのブロック（ndf-next）を出してセグメントを終える"
# 上限で替えた先のアカウントで再開するときに送る 1 行（#3337）
RESUME_PROMPT = "利用上限でアカウントを替えた。中断したところから続ける"
# 質問を転送する理由。質問の ID を入れて、直前と同じ理由を省く記録でも別の質問の行を残す（#3814）
FORWARD = "AskUserQuestion の質問 {id} を人かオーガナイザーへ転送する（hook は待たない）"
# 質問の ID が分からないときの理由（今の入力の JSON の文）
FORWARD_UNKNOWN = "AskUserQuestion の質問を人かオーガナイザーへ回す（hook は待たない）"
# 転送した質問の回答を待つ理由（#3814。子を止めず、子の端末へ書かない）
QUESTION_PENDING = "回答待ちの質問がある。子を止めず、子の端末へ書かない"
# 再起動ループの検出に当たったときの理由（#3864 の決定 13）
SPIN_REASON = "起動から間もないセグメントが続いた（再起動ループ）。次のセグメントを起動しない"
# 既定のログイン（アカウントの指定が無い）の起動先の値。登録の名前に使えない括弧を含むので重ならない
DEFAULT_TARGET = "(default)"


@dataclass(frozen=True)
class SectionState:
    """見回り 1 回のセグメントの状態。

    - `question`: AskUserQuestion の hook が書いた質問が回答待ちか（質問が閉じると hook が消す。#3814）
    - `question_forwarded`・`question_id`: 今の質問を転送したか・今の質問の ID（#3814。無い入力の JSON も読める）
    - `next_block`: Stop の hook が書いた次のセグメントのブロック（無ければ None）。`/goal` が未達でも同じ（#2438）
    - `next_age_seconds`: 次のセグメントのマーカーを最初に書いてからの秒（`first_written_at` から。`/goal` の未達の応答が
      続いて画面が静かにならなくても、マーカーから数えて静かな時間が過ぎれば切り替える。#2438）
    - `child_alive`・`child_exit`: 子のプロセスが生きているか・終わったときの終了コード
    - `idle_seconds`・`quiet_seconds`・`stall_seconds`: 子の出力が止まってからの秒・切り替えに要る静かな秒・止まりとみなす秒
    - `context_tokens`・`context_limit`・`handover_asked`: 文脈の量・切り替える量・このセグメントで既に引継ぎを頼んだか（#2595）
    - `limit_pick`: 利用上限に当たったときの替える先の判断（当たっていなければ None。#3337）
    - `auth_failed`: 子の画面に認証の失敗が出て、`/goal` が外れたか（#3243）
    - `spin`・`spin_seen`・`spin_held`: 今のセグメントを終えて数える手で次を始めると再起動ループの検出に当たるか・
      続きの中で起動した起動先・検出に当たった後の保持（#3864 の決定 13）
    """

    question: bool
    next_block: Optional[str]
    child_alive: bool
    child_exit: Optional[int]
    idle_seconds: float = 0.0
    next_age_seconds: float = 0.0
    quiet_seconds: float = 0.0
    stall_seconds: float = 0.0
    context_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    handover_asked: bool = False
    limit_pick: Optional[Pick] = None
    auth_failed: bool = False
    question_forwarded: bool = False
    question_id: Optional[str] = None
    spin: bool = False
    spin_seen: frozenset[str] = frozenset()
    spin_held: bool = False


class Step(NamedTuple):
    """次の手。`kind` は `wait`・`forward`・`switch`・`account`・`wait_reset`・`stalled`・`handover`・`restart`・`finish`・`spin`。

    `prompt` は子へ送る 1 行、`account` は次に使うアカウント（替えないときは None）。
    """

    kind: str
    reason: str
    prompt: str = ""
    account: Optional[str] = None


class Spin(NamedTuple):
    """再起動ループの検出の閾値（`relay.spin`）。`seconds` 未満のセグメントが `count` 続いたら当たる。秒が 0 なら検出しない。"""

    count: int
    seconds: float


class Streak(NamedTuple):
    """短いセグメントの続き。`count` は短いセグメントの数、`seen` は続きの中で起動した起動先。"""

    count: int
    seen: frozenset[str]


def target_key(account: Optional[str]) -> str:
    """起動先を見分ける値。アカウントの名前・`metered`・`key:<名前>` はそのまま、既定のログイン（None）は `DEFAULT_TARGET`。"""
    return DEFAULT_TARGET if account is None else account


def advance(streak: Streak, seconds: float, kind: str, current: str, target: str, limit: Spin) -> Streak:
    """終わったセグメント（長さ `seconds`・起動先 `current`）と次を起動する手（`kind`・起動先 `target`）で続きを進める。

    短くなければ続きを今と次の起動先だけにする。短く、アカウントを替える手で初めての起動先なら数えずに起動先を足す。
    それ以外の短いセグメントは数を 1 足し、次の起動先も足す（#3864 の決定 13）。
    """
    if limit.seconds <= 0 or seconds >= limit.seconds:
        return Streak(0, frozenset({current, target}))
    if kind == "account" and target not in streak.seen:
        return Streak(streak.count, streak.seen | {target})
    return Streak(streak.count + 1, streak.seen | {target})


def hit(streak: Streak, limit: Spin) -> bool:
    """続きが再起動ループの検出に当たるか。"""
    return limit.seconds > 0 and streak.count >= limit.count


def _spun(state: SectionState, kind: str, account: Optional[str]) -> bool:
    """次のセグメントを起動する手（`kind`。`account` は替え先）が検出に当たるか。保持の間は常に当たる。"""
    if state.spin_held:
        return True
    if not state.spin:
        return False
    return not (kind == "account" and target_key(account) not in state.spin_seen)


def _spin_or(state: SectionState, step: Step) -> Step:
    """検出に当たれば手を `spin` に替える（理由は元の理由に `SPIN_REASON` を足す）。"""
    if _spun(state, step.kind, step.account):
        return Step("spin", f"{step.reason}。{SPIN_REASON}")
    return step


def decide(state: SectionState) -> Step:
    """次の手を決める。上から順に最初に当たった条件で決まる。"""
    if state.question:
        if state.question_forwarded:
            return Step("wait", QUESTION_PENDING)
        return Step("forward", FORWARD_UNKNOWN if state.question_id is None else FORWARD.format(id=state.question_id))
    first = _limit_or_auth(state)
    if first is not None:
        return first
    quiet = state.idle_seconds >= state.quiet_seconds
    if state.next_block is not None:
        if state.child_alive and not quiet and state.next_age_seconds < state.quiet_seconds:
            return Step("wait", f"次のセグメントのマーカーがある。静かな時間 {state.quiet_seconds:g} 秒を待つ")
        return _spin_or(
            state, Step("switch", "最後の応答が次のセグメントのブロックを持つ（/goal が未達でも切り替える）", prompt=state.next_block)
        )
    if not state.child_alive:
        return _ended(state)
    return _alive(state, quiet)


def _limit_or_auth(state: SectionState) -> Optional[Step]:
    pick = state.limit_pick
    if pick is not None and pick.kind == "switch":
        # 回数では止めない。同じ起動先の間を短く回り続ける替えは再起動ループの検出が止める（#3864 の決定 13）
        return _spin_or(state, Step("account", pick.reason, prompt=RESUME_PROMPT, account=pick.account))
    if pick is not None:
        return Step("wait_reset", pick.reason)
    if state.auth_failed and state.child_alive:
        return Step("stalled", "認証の失敗で /goal が外れ、子が入力を待っている。running として扱わない")
    return None


def _ended(state: SectionState) -> Step:
    # 検出に当たった後の保持の間に子が終わったら、終了コードによらず spin で終わる（`end` の行に spin を残す）
    if state.spin_held:
        return Step("spin", f"子が終了コード {state.child_exit} で終わった。{SPIN_REASON}")
    if state.child_exit == 0:
        return Step("finish", "子が次のセグメントのブロックを書かずに終わった")
    return _restart_or_stop(state, f"子が終了コード {state.child_exit} で終わった")


def _alive(state: SectionState, quiet: bool) -> Step:
    over = state.context_limit is not None and state.context_tokens is not None and state.context_tokens >= state.context_limit
    if over and quiet and not state.handover_asked:
        return _spin_or(
            state, Step("handover", f"文脈の量 {state.context_tokens} が上限 {state.context_limit} を超えた", prompt=HANDOVER_PROMPT)
        )
    if state.stall_seconds and state.idle_seconds >= state.stall_seconds:
        return _restart_or_stop(state, f"子の出力が {state.idle_seconds:g} 秒止まっている")
    return Step("wait", "子が動いている")


def _restart_or_stop(state: SectionState, why: str) -> Step:
    return _spin_or(state, Step("restart", why))
