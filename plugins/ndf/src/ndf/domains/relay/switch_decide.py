"""セグメントを切り替えるかの判断（#2962・#2438・#2595・#3243・#3337）。

中継のラッパーが見回るたびに、今のセグメントの状態（シグナルファイルの中身・子の生き死に・静かな秒・文脈の量・
上限と認証の失敗・再起動ループの検出）を値で渡し、次の手を受け取る。起動の回数は入力に持たず、回数では止めない（#3864 の I15）。判断は時計と端末を読まない純粋な関数で、同じ状態に
同じ手を返す（端末と時計は呼ぶ側が読んで値で渡す）。擬似端末を立てずに単体のテストで確かめられる。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple, Optional

from ndf import identity
from ndf.domains.relay.account_decide import Pick

# 文脈の量が上限を超えたときに中継が子へ送る 1 行（#2595。hook の外でも判定の機会を作る）
HANDOVER_PROMPT = (
    "文脈の量が運用上限を超えたので、ラッパーが子を止めて起動し直した。オーガナイザーは引継ぎ文書の「現在地」に打ち直す ndf lanes wait の"
    f"行を残す。引継ぎ文書をメインディレクトリの {identity.FIXED_WORDS['ndf_settings_dir']}/handoff の下に絶対パスで更新し、"
    "ndf records --cwd <メインディレクトリの絶対パス> handoff check <文書の絶対パス> で確かめ、"
    "保存と検査が通ったときだけ次のセグメントのブロック（ndf-next）を出してセグメントを終える。"
    "通らなければブロックを出さずに理由を伝える"
)
# 上限で替えた先のアカウントで再開するときに送る 1 行（#3337）
RESUME_PROMPT = "利用上限でアカウントを替えた。中断したところから続ける"
# 見回りで閾値を越えて替えた先のアカウントで再開するときに送る 1 行（#3864 の決定 11）
GUARD_RESUME_PROMPT = "利用上限の手前でアカウントを替えた。中断したところから続ける"
# 見回りで従量の接続から登録済みアカウントへ戻るときに送る 1 行（#3864 の決定 8）
GUARD_BACK_PROMPT = "利用上限が解けたアカウントへ戻した。中断したところから続ける"
# 認証の失敗で替えた先のアカウントで再開するときに送る 1 行（#3864 の決定 6）
AUTH_RESUME_PROMPT = "認証の失敗でアカウントを替えた。中断したところから続ける"
# 見回りで替えた先の最初の入力に足す 1 行（子を止めると監視も終わるが、起動し直した会話はそれを知らない。#4049 の決定 9）
MONITOR_RESUME = (
    "バックグラウンドの監視（ndf lanes wait・ndf plan wait・ndf ci watch・Monitor）は、アカウントの切り替えで終了した。"
    "動かしていた監視は、直前に受け取ったカーソル（ndf lanes wait の --after など）から始め直してから待つ"
)
# 止めるものも待つものも無い見回りの理由
ACTIVE = "子が動いている"
# 見回りの替えで、止めてよい時点を待つ理由（I13）
GUARD_POINT = f"{ACTIVE}。アカウントを切り替える条件を満たしたので、子を止めてよい時点を待つ"
# バックグラウンドの作業と監視を待つ理由（#4049 の決定 4・5）。開始の記録は「少なくとも 1 件」だけを示す
_HOLDING = "待つ間は子を止めず、引継ぎ・起動し直し・利用上限に達する前のアカウントの切り替えをしない"
BACKGROUND_WAIT = f"バックグラウンドの作業 {{count}} 件が終わるのを待つ（ほかに監視 {{monitors}} 件。{{at}} の記録）。{_HOLDING}"
BACKGROUND_LEAST = f"バックグラウンドの作業 1 件以上が終わるのを待つ（{{at}} に作業の開始を記録）。{_HOLDING}"
MONITOR_WAIT = (
    "監視 {monitors} 件が終わるのを待つ（{at} の記録）。子の出力が止まっていても起動し直さない"
    "（起動し直すと監視が失われ、会話は入力を待つだけになる）"
)
BACKGROUND_UNKNOWN = (
    "バックグラウンドの作業が残っているかを確かめられないので、引継ぎと、利用上限に達する前のアカウントの切り替えをしない"
    "（子の出力と端末の入力が止まったときの起動し直しは行う）"
)
# 質問を転送する理由。質問の ID を入れて、直前と同じ理由を省く記録でも別の質問の行を残す（#3814）
FORWARD = "AskUserQuestion の質問 {id} を人かオーガナイザーへ転送する（hook は待たない）"
# 質問の ID が分からないときの理由（今の入力の JSON の文）
FORWARD_UNKNOWN = "AskUserQuestion の質問を人かオーガナイザーへ回す（hook は待たない）"
# 転送した質問の回答を待つ理由（#3814。子を止めず、子の端末へ書かない）
QUESTION_PENDING = "回答待ちの質問がある。子を止めず、子の端末へ書かない"
# 再起動ループの検出に当たったときの理由（#3864 の決定 13）
SPIN_REASON = "起動から間もないセグメントが続いた（再起動ループ）。次のセグメントを起動しない"
# API キーの子で止める必要が立ち、止められる時点を待つ理由と、止めた後に替え先が無い理由（#3864 の決定 12）
END_WAIT = "API キーの設定が変わった。止められる時点を待つ"
REVOKED = "API キーの子を止めた。替える先が無い"
# 既定のログイン（アカウントの指定が無い）の起動先の値。登録の名前に使えない括弧を含むので重ならない
DEFAULT_TARGET = "(default)"


class Background(NamedTuple):
    """今の起動のバックグラウンドの作業の記録（`signals.read_busy`）。`count` は作業、`monitors` は監視の件数。

    `source` は書いた事象（`start` なら `count` は「少なくとも 1 件」）、`at` は書いた時刻。
    """

    count: int
    source: str
    at: str
    monitors: int


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
    - `auth_pick`: 認証の失敗を確かめたときの替え先の判断（`account_decide.after_auth`。確かめていなければ None。#3864 の決定 6）
    - `spin`・`spin_seen`・`spin_held`: 今のセグメントを終えて数える手で次を始めると再起動ループの検出に当たるか・
      続きの中で起動した起動先・検出に当たった後の保持（#3864 の決定 13）
    - `guard_pick`・`safe`: 見回りの判断（`account_decide.guard` の `switch`）と、止めてよい時点か（#3864 の決定 11・I13）
    - `must_end`・`end_ready`・`drift_pick`: API キーの子で止める必要が立ったか・この見回りで旧子の終わりを確かめたか・
      止めた後に取り直した替え先の判断（`end_ready` が偽の間は None。#3864 の決定 12）
    - `metered`: 今の起動先が従量の接続か（見回りで戻るときの 1 行を選ぶ。#3864 の決定 8）
    - `background`: 今の起動のバックグラウンドの作業の記録。None は不明か、読んでいない（`needs_background`。#4049）
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
    auth_pick: Optional[Pick] = None
    question_forwarded: bool = False
    question_id: Optional[str] = None
    spin: bool = False
    spin_seen: frozenset[str] = frozenset()
    spin_held: bool = False
    guard_pick: Optional[Pick] = None
    safe: bool = False
    must_end: bool = False
    end_ready: bool = False
    drift_pick: Optional[Pick] = None
    metered: bool = False
    background: Optional[Background] = None


class Step(NamedTuple):
    """次の手。`kind` は `wait`・`active`・`forward`・`switch`・`account`・`wait_reset`・`wait_background`・`stalled`・`handover`・
    `restart`・`finish`・`spin`・`revoked`。

    `prompt` は子へ送る 1 行、`account` は次に使うアカウント（替えないときは None）。`busy` は子を止める直前に記録を読み直す条件
    （`zero` は作業 0 件、`none_running` は作業も監視も 0 件か不明のときだけ止める。None は読み直さない。#4049）。
    """

    kind: str
    reason: str
    prompt: str = ""
    account: Optional[str] = None
    busy: Optional[str] = None


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
        return Step("spin", f"{step.reason}。{SPIN_REASON}", busy=step.busy)
    return step


def needs_background(state: SectionState) -> bool:
    """この見回りで選ばれる判定が、バックグラウンドの作業の件数を要るか（要る見回りだけ記録を読む。#4049）。"""
    step = decide(replace(state, background=None))
    return step.kind == "wait_background" or step.busy is not None


def decide(state: SectionState) -> Step:
    """次の手を決める。上から順に最初に当たった条件で決まる。API キーの子の停止（`_revoked`）がどれより先。"""
    revoked = _revoked(state)
    if revoked is not None:
        return revoked
    if state.question:
        if state.question_forwarded:
            return Step("wait", QUESTION_PENDING)
        return Step("forward", FORWARD_UNKNOWN if state.question_id is None else FORWARD.format(id=state.question_id))
    if state.spin_held and not state.child_alive:
        # 保持の間に子が終われば、上限の表示が残っていても待たずに spin で終わる（替え先の現れで再起動しない）
        return _ended(state)
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
    guarded = _guard(state)
    if guarded is not None and guarded.kind != "active":
        return guarded
    if not state.child_alive:
        return _ended(state)
    step = _alive(state, quiet)
    # 止めてよい時点を待つ間も、文脈の上限の引継ぎと出力の止まりの再起動は先に打つ（tool_use のままハングした子を残さない）
    return guarded if guarded is not None and step.kind == "active" else step


def _revoked(state: SectionState) -> Optional[Step]:
    """API キーの子の停止（決定 12）。止めるまでは `wait`、止めた後は替え先で `account`・再起動ループなら `spin`・無ければ `revoked`。"""
    if not state.must_end:
        return None
    if not state.end_ready:
        return Step("wait", END_WAIT)
    pick = state.drift_pick
    if pick is not None and pick.kind == "switch":
        return _spin_or(state, Step("account", pick.reason, prompt=AUTH_RESUME_PROMPT, account=pick.account))
    return Step("revoked", pick.reason if pick is not None else REVOKED)


def _guard(state: SectionState) -> Optional[Step]:
    """見回りの替え。閾値を越えて替え先があれば、止めてよい時点で `account`、それまでは `active`（決定 11・I13）。

    `active` は `decide` が子の生き死にと `_alive` の判断の後に回す（引継ぎ・再起動が当たればそちらを打つ）。
    作業が残るか確かめられなければ替えずに待つ。確かめられない子が止まっていれば `_alive` の起動し直しに任せる（#4049 の決定 8）。
    """
    pick = state.guard_pick
    if pick is None or pick.kind != "switch":
        return None
    if not state.safe:
        return Step("active", GUARD_POINT)
    if state.background is None and _stalled(state):
        return None
    if state.background is None or state.background.count:
        return _wait_background(state.background)
    prompt = GUARD_BACK_PROMPT if state.metered else GUARD_RESUME_PROMPT
    return _spin_or(state, Step("account", pick.reason, prompt=f"{prompt}。{MONITOR_RESUME}", account=pick.account, busy="zero"))


def _wait_background(found: Optional[Background]) -> Step:
    """作業の終わりを待つ手（None は確かめられない）。"""
    if found is None:
        return Step("wait_background", BACKGROUND_UNKNOWN)
    reason = BACKGROUND_LEAST if found.source == "start" else BACKGROUND_WAIT
    return Step("wait_background", reason.format(**found._asdict()))


def _limit_or_auth(state: SectionState) -> Optional[Step]:
    pick = state.limit_pick
    if pick is not None and pick.kind == "switch":
        # 回数では止めない。同じ起動先の間を短く回り続ける替えは再起動ループの検出が止める（#3864 の決定 13）
        return _spin_or(state, Step("account", pick.reason, prompt=RESUME_PROMPT, account=pick.account))
    if pick is not None:
        return Step("wait_reset", pick.reason)
    auth = state.auth_pick
    if auth is not None and auth.kind == "switch":
        return _spin_or(state, Step("account", auth.reason, prompt=AUTH_RESUME_PROMPT, account=auth.account))
    if auth is not None and state.child_alive:
        return Step("stalled", f"{auth.reason}。子が入力を待っている。running として扱わない")
    return None


def _ended(state: SectionState) -> Step:
    # 検出に当たった後の保持の間に子が終わったら、終了コードによらず spin で終わる（`end` の行に spin を残す）
    if state.spin_held:
        return Step("spin", f"子が終了コード {state.child_exit} で終わった。{SPIN_REASON}")
    if state.child_exit == 0:
        return Step("finish", "子が次のセグメントのブロックを書かずに終わった")
    return _restart_or_stop(state, f"子が終了コード {state.child_exit} で終わった")


def _over(state: SectionState) -> bool:
    """文脈の量が上限以上か。"""
    return state.context_limit is not None and state.context_tokens is not None and state.context_tokens >= state.context_limit


def _stalled(state: SectionState) -> bool:
    """子の出力と端末の入力が、止まりとみなす秒だけ無いか。"""
    return state.stall_seconds > 0 and state.idle_seconds >= state.stall_seconds


def _alive(state: SectionState, quiet: bool) -> Step:
    """動いている子の手。作業が残る子は止めない。止まりでは監視だけの子も止めない（#4049 の決定 4・9）。"""
    ask, stalled, found = _over(state) and quiet and not state.handover_asked, _stalled(state), state.background
    if found is not None and found.count and (ask or stalled):
        return _wait_background(found)
    if ask and found is not None:
        reason = f"文脈の量 {state.context_tokens} が上限 {state.context_limit} を超えた"
        return _spin_or(state, Step("handover", reason, prompt=HANDOVER_PROMPT, busy="zero"))
    if stalled and found is not None and found.monitors:
        return Step("wait_background", MONITOR_WAIT.format(**found._asdict()))
    if stalled:
        return _restart_or_stop(state, f"子の出力が {state.idle_seconds:g} 秒止まっている", busy="none_running")
    return _wait_background(None) if ask else Step("active", ACTIVE)


def _restart_or_stop(state: SectionState, why: str, busy: Optional[str] = None) -> Step:
    return _spin_or(state, Step("restart", why, busy=busy))
