"""ラッパーの子を止める手順と、キーの子の停止の共通の経路（#3604 #3864 の決定 12・I9。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `StopSteps` が持つ。

- 子を止める手順（`_stop_if_clear`・`_stop_child`・`_halt_broken` など）は、質問の hook と同じロックの中で子を止める（I4）
- キーの子（`Target.helper` のある子）は、見回りの `_loop` の (2) で `_drift` が起動の後の設定の変更を確かめ（登録簿のキーの
  削除・登録し直し・`off` は止めてよい時点を待ち、接続先・資格情報・helper の変更は待たない）、`_key_auth` が helper の拒み・記録の異常・失敗のマーク・
  401・設定の当たりを確かめると `Section.must_end`・`end_now` を立て、(3) で `_end_point` が真なら `_stop_revoked` が
  替え先の候補を読む前に旧子を止める。止めた見回りの `_state` で `_revoked_pick` だけが替え先を読む（別冊の「止める時点」）
- 替え先が無ければ `_on_revoked` が直し方の 1 行（認証の失敗）か設定の当たりの 1 行を出し、`account_unavailable` で終える。
  子を残したまま待つ経路は無い（`stalled` にしない）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_decide, accounts, key_helper, launch_inputs, observe, signals, switch_decide
from ndf.domains.relay.account_decide import Pick
from ndf.domains.relay.account_registry import METERED
from ndf.domains.relay.wrapper_auth import AUTH_FIX_METERED
from ndf.domains.relay.wrapper_busy import Busy
from ndf.domains.relay.wrapper_launch import CHILD_ALIVE, Ended

Section = Any  # `wrapper.Section`（import の輪を作らないよう型の名前だけで受ける）
HALT_BROKEN = "質問が閉じるか子の終わりを待ってから止める"
# 止めた子の起動の記録か質問を外せないとき（後継を起動しない）
LAUNCH_STUCK = "止めた子の起動の記録（{dir}/launch.json）か質問（{dir}/question.json）を外せない。次の子を起動せずに止める"
# 止める必要が立った見回りで 1 回出す（以後の入力は子へ送らず捨てる）
DROP_LINE = "ndf-relay: API キーの設定が変わったので入力を子へ送らない。子が止まるのを待つ（入力は替えた後に打ち直す）"
DRIFT_LINE = "ndf-relay: API キーの子の送信先・認証の設定（{source}）が起動のときと変わったので止めた。設定を戻すか、ndf relay で起動し直す"
REGISTRY_SOURCE = "API キーの登録簿"  # `_drift` が登録簿のキーの削除・登録し直し・off を当てたときの設定の当たりの出所
REKEYED_REASON = "{name} を登録し直した。新しいキーで再開する"
REVOKED_FIX_LINE = "ndf-relay: {names} の認証に失敗し、替える先が無い。{fixes}"
AUTH_FIX_KEY = "ndf relay account add-api-key {name} で登録し直す"
REVOKED_END = "{reason}。会話を送らずに止めた。直してから ndf relay で続ける"
# 最初の払い出しを見届けた後、保留した入力を送る前に子の画面が静かであるべき秒。Claude Code 2.1.330 は入力欄を描く前（起動から
# 約 0.2 秒）に helper を呼び、入力欄を描く前に打った入力は捨てる（2026-11-22 に実の子で確かめた。描いた後の約 0.5 秒なら届く）
TYPE_QUIET = 1.0


def revoked_fix(tried: set[str]) -> str:
    """この続きで認証に失敗した接続先を名前の順にすべて並べ、名前ごとの直し方を添えた 1 行。"""
    names = sorted(tried)
    fixes = "。".join(f"{n}: {AUTH_FIX_METERED if n == METERED else AUTH_FIX_KEY.format(name=n.removeprefix('key:'))}" for n in names)
    return REVOKED_FIX_LINE.format(names="・".join(names), fixes=fixes)


class StopSteps:
    """`Wrapper` の子を止める手順。属性と手（`_say`・`_log`・`_pump`・`_write_state`・`_confirm` など）は `Wrapper` が持つ。"""

    s: Any
    dir: Path
    store: Path
    clock: Callable[[], float]
    wall: Any
    tried: set[str]
    key_memo: Optional[tuple]
    _say: Callable[[str], None]
    _log: Callable[[Mapping[str, Any]], None]
    _pump: Callable[[float], None]
    _write_state: Callable[[str, str], None]
    _current: Callable[[], Section]
    _auth_seen: Callable[[Section, str], bool]
    _safe_point: Callable[[Section, float], bool]
    _recheck_choice: Callable[[Section], Optional[str]]
    _confirm: Callable[[Section, str, float, Callable[[launch_inputs.Inputs], Pick]], Pick]

    # ---------------------------------------------------------------- 子を止める

    def _stop_if_clear(self, sec: Section, allow: Optional[Callable[[Busy], bool]] = None) -> str:
        """質問の hook と同じロックを待たずに取り、質問のファイルが無ければロックを持ったまま子を止める（I4）。

        `allow` を渡すと、同じロックの中でバックグラウンドの作業の記録を読み直し、偽なら止めない（#4049。開始の記録も同じロックの
        中で書かれるので、読み直しの後に作業は始まらない）。
        `"stopped"` は子の終わりを確かめた、`"held"` はロックを取れない・質問のファイルが在る・`allow` が偽（子にも何もしない）、
        `"alive"` は強制終了しても子が終わらない。止めたら止めた子の起動を今の起動から外し、残った hook が書けないようにする。
        """
        with signals.held(self.dir / signals.STOP_LOCK, 0) as got:
            if not got or signals.question_present(self.dir):
                return "held"
            if allow is not None and not allow(signals.read_busy(self.dir, sec.launch, sec.session_id)):
                return "held"
            stopped = self._stop_child(sec)
            signals.set_launch(self.dir, None)
            return "stopped" if stopped else "alive"

    def _not_stopped(self, stopped: str, sec: Section) -> Optional[Ended]:
        """子を止められなかったときの終わり。確かめが外れた（`held`）なら何もせず次の見回りで決め直し、終わらない子なら止まる。

        `held` ならセグメントは続くので、終わりの時刻を取り消す（回答待ちの間もセグメントの長さに数え、次の判断で求め直す）。
        """
        if stopped == "held":
            sec.ended_at = None
            return None
        return self._child_alive(sec)

    @staticmethod
    def _child_alive(sec: Section) -> Ended:
        return Ended("refused", CHILD_ALIVE.format(pid=sec.child.pid, seconds=limits.seconds("relay.child-exit")))

    def _halt_broken(self, exc: Exception) -> Ended:
        """設定が壊れたときの終わり。回答待ちの質問を残したまま子を止めず、ロックの中の確かめが通るか子が終わるまで待つ（決定 5）。

        待つ間は判断をせず（切り替え・起動し直し・引継ぎ・転送をしない）、子の出力と利用者の入力だけをそのまま渡す。
        """
        sec = self._current()
        self._write_state("waiting", f"{exc}。{HALT_BROKEN}")
        while sec.child.isalive():
            stopped = self._stop_if_clear(sec)
            if stopped == "alive":
                return self._child_alive(sec)
            if stopped == "stopped":
                break
            self._pump(self.s.tick)
        return Ended("config_broken", str(exc))

    def _stop_child(self, sec: Section, deadline: Optional[float] = None) -> bool:
        """子を強制終了し、`deadline`（`clock`。既定は今から `relay.child-exit` の秒）までに子の終わりを確かめたら真。

        シグナルの間の待ちも終わりの確かめの待ちも、残り時間の中に収める（`_terminate_by`）。
        """
        child = sec.child
        if not child.isalive():
            return True
        deadline = self.clock() + limits.seconds("relay.child-exit") if deadline is None else deadline
        if self._terminate_by(child, deadline):
            return True
        while child.isalive():
            left = deadline - self.clock()
            if left <= 0:
                return False
            time.sleep(min(self.s.tick, left))
        return True

    def _terminate_by(self, child: Any, deadline: float) -> bool:
        """`PtyProcess.terminate(force=True)` を打つ。シグナル 4 つの後の待ち（`delayafterterminate`）を残り時間の 4 等分までに縮める。

        期限を過ぎていれば待たずに 4 つを送る（最後は SIGKILL）。待ちの属性の無い子（テストの子）はそのまま打つ。
        """
        delay = getattr(child, "delayafterterminate", None)
        if delay is None:
            return bool(child.terminate(force=True))
        child.delayafterterminate = min(delay, max(0.0, deadline - self.clock()) / 4)
        try:
            return bool(child.terminate(force=True))
        finally:
            child.delayafterterminate = delay

    # ---------------------------------------------------------------- キーの子の停止（決定 12）

    def _must_end(self, sec: Section, cause: str, now: bool, at: Optional[float] = None) -> None:
        """止める必要を立てる。`end_cause` は最初に書いた側のまま、`end_now` は一度立てば下ろさない。初めて立てた見回りで 1 行出す。

        `end_now` を初めて立てたとき、旧子の終わりの期限 `stop_by` を `at`（当たりを確かめた時刻。既定は今）+ `relay.child-exit` に
        決める（前の見回りの拒否のマークの待ちの期限を使い回さない。同じ見回りの拒否のマークの待ちはこの期限の中に入る）。
        """
        if not sec.must_end:
            sec.must_end, sec.must_end_at = True, self.clock()
            sec.held = None  # 保留した入力も送らずに捨てる（後継にも渡さない）
            self._say(DROP_LINE)
        sec.end_cause = sec.end_cause or cause
        if now and not sec.end_now:
            sec.stop_by = (self.clock() if at is None else at) + limits.seconds("relay.child-exit")
        sec.end_now = sec.end_now or now

    def _drift(self, sec: Section) -> None:
        """キーの子の起動の後の設定の変更を確かめる（詳細設計書の「起動の後の設定の変更」）。源のスタンプが変わった見回りだけ確かめ直す。

        登録簿のキーの削除・登録し直し・`off` は `must_end` だけを立て（止めてよい時点を待つ）、接続先・資格情報・helper の変更
        （`verify` の外れ）は `end_now` も立てる。候補・`choose` を読まず、`tried` に入れない（別冊の「設定の変更でキーの子を止める範囲」）。
        """
        t = sec.target
        if sec.end_now or (sec.drift_stamps is not None and launch_inputs.unchanged(sec.drift_stamps)):
            return
        started = account_choice.Started(t.generation, t.resolved.helper if t.resolved is not None else None, t.version)
        wait, now, sec.drift_stamps = account_choice.drift(str(t.account), t.env, t.argv, self.s.cwd, self.store, started)
        if wait is None and now is None:
            return
        self._log({"kind": "auth_drift", "account": t.account, "items": [c for c in (wait, now) if c is not None]})
        sec.rekeyed = sec.rekeyed or wait == "key_rekeyed"
        sec.end_source = sec.end_source or (key_helper.SETTINGS_FILE if now is not None else REGISTRY_SOURCE)
        self._must_end(sec, "drift", now=now is not None)

    def _key_auth(self, sec: Section, screen: str) -> None:
        """キーの子の helper の拒み・記録の異常・失敗のマークと 401 を認証の失敗、`gate` の行などを設定の当たりとして確かめ、フラグだけを立てる。

        候補・`choose` を読まず、`hold` もしない。`must_end` が既に `drift` で立っていれば、認証の失敗では `end_now` を
        立てない（設定の当たりは立てる）。401 では止める前に拒否のマークを記録へ足す（別冊の「同時の当たり」）。
        """
        rec = sec.target.helper
        stamp = (sec.launch, key_helper.stamp(self.dir, rec))
        seen = key_helper.Seen(False, None)
        if self.key_memo != stamp:  # スタンプが変わったときだけ読む（当たれば同じ見回りで止めるので、外れた読みだけを残す）
            seen = key_helper.watch(self.dir, rec)
            self.key_memo = stamp
        if seen.paid:
            sec.paid = True
        refused = self._auth_seen(sec, screen)
        at = self.clock()  # 確かめた時刻（この見回りで止めへ移るなら、旧子の終わりの期限の起点）
        if refused and not sec.denied:  # 書けた後は打たない。書けなければ次の見回りで期限の残りの中で打ち直す
            wait = self._deny_wait(sec, at)
            if wait is not None:
                sec.denied = key_helper.deny(rec, wait)
        if seen.source is not None:
            sec.end_source = sec.end_source or seen.source
            self._must_end(sec, "drift", now=True, at=at)
        if (seen.auth or refused) and not (sec.must_end and sec.end_cause == "drift"):
            self.tried.add(switch_decide.target_key(sec.account))
            self._must_end(sec, "auth", now=True, at=at)

    def _deny_wait(self, sec: Section, at: float) -> Optional[float]:
        """401 の拒否のマークの錠を待つ秒。期限を過ぎていれば None（打たない）。

        錠を待つのも、この見回りで止めへ移るときの期限の中（待った分だけ旧子の終わりを待つ時間が減る）。打ち直しは、止めへ移った
        後なら旧子の終わりの期限 `stop_by`、止める時点を待つ間なら `must_end_at` + `relay.end-wait` の残りに収める。
        """
        wait = min(limits.value(key_helper.POLICY, "lock"), limits.seconds("relay.child-exit"))
        if sec.end_now and sec.stop_by is not None:
            until: Optional[float] = sec.stop_by
        elif sec.must_end and sec.must_end_at is not None:
            until = sec.must_end_at + limits.seconds("relay.end-wait")
        else:
            return wait
        left = until - at
        return None if left <= 0 else min(wait, left)

    def _key_step(self) -> tuple[Optional[Ended], bool]:
        """`_loop` の (2)〜(3)。キーの子なら `_key_auth` を打ち、止める時点なら旧子を止める。返りは（終わり、止めたか）。"""
        sec = self._current()
        if sec.target is not None and sec.target.helper is not None:
            self._drift(sec)
            self._key_auth(sec, observe.plain(bytes(sec.screen)))
            self._first_payout(sec)
        if not (sec.must_end and self._end_point(sec)):
            return None, False
        return self._stop_revoked(sec), True

    @staticmethod
    def typed(prompt: str) -> bytes:
        """最初のプロンプトを子の端末へ打つバイト列（貼り付けの括りで 1 つの入力にし、改行で送る）。"""
        return b"\x1b[200~" + prompt.encode() + b"\x1b[201~\r"

    def _first_payout(self, sec: Section) -> None:
        """最初の払い出しを見届けたら保留した入力を順に送る。`relay.key-helper` の `first` を過ぎても見届けなければ、設定の当たり
        （出所は設定ファイル）として止める（別冊の「最初の払い出しまで送らない」）。"""
        if sec.held is None or sec.must_end:
            return
        if sec.paid:
            if self.clock() - sec.last_output < TYPE_QUIET:
                return  # 入力欄を描き終えるまで待つ
            held, sec.held = sec.held, None
            for data in held:
                sec.child.write(data)
            if held:
                sec.input_at = self.wall.now()
                sec.screen.clear()
        elif self.clock() - sec.started_at >= limits.value(key_helper.POLICY, "first"):
            sec.end_source = sec.end_source or key_helper.SETTINGS_FILE
            self._must_end(sec, "drift", now=True)

    def _forget_tried(self, sec: Section) -> None:
        """替えた子が応答を 1 つ返したら、この続きで認証に失敗した接続先の記録（`tried`）を空にする（決定 6）。"""
        if not self.tried or sec.replied or sec.must_end or sec.auth_at is not None:
            return  # 認証に失敗した子の応答では空にしない（その子の接続先を残したまま替え先を選ぶ）
        got = observe.turn_done(observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id), self.s.transcript_bytes)
        if got is not None and got[0] and got[1] is not None and got[1] > sec.wall_started:
            sec.replied = True
            self.tried.clear()

    def _end_point(self, sec: Section) -> bool:
        """キーの子を止める時点か。`end_now`・子の終わり・止めてよい時点・`relay.end-wait` の経過・静かな間の質問か API の失敗の記録。"""
        if sec.end_now or not sec.child.isalive():
            return True
        now = self.clock()
        idle = now - sec.last_output
        if self._safe_point(sec, idle) or now - sec.must_end_at >= limits.seconds("relay.end-wait"):
            return True
        if idle < self.s.quiet:
            return False
        transcript = observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)
        return signals.question_present(self.dir) or observe.api_error_since(transcript, self.s.transcript_bytes, sec.input_at)

    def _clear_launch(self) -> bool:
        """（ロックの中で）今の起動を外して質問を消し、どちらも確かめたら真。書けない・消せない・確かめが外れれば偽。"""
        try:
            signals.set_launch(self.dir, None)
            (self.dir / signals.QUESTION_FILE).unlink(missing_ok=True)
        except (OSError, signals.SignalError):  # noqa: NDF401 ファイル: 外せなければ呼び手がロックを待って打ち直す
            return False
        return signals.current_launch(self.dir) is None and not signals.question_present(self.dir)

    def _stop_revoked(self, sec: Section) -> Optional[Ended]:
        """キーの子の旧子を止める。質問が在っても見送らず、ロックを取れなくても止めるのを先にし、止めた後に外し直す。

        子が終わらなければ `CHILD_ALIVE`、起動の記録か質問を外せなければ `LAUNCH_STUCK` で終え、替え先を選ばない。
        """
        if sec.ended_at is None:
            sec.ended_at = self.clock()
        cleared = False
        try:
            with signals.held(self.dir / signals.STOP_LOCK, 0) as got:
                cleared = got and self._clear_launch()
                stopped = self._stop_child(sec, sec.stop_by)
        except OSError:  # ファイル: ロックの操作が落ちても止めるのを先送りしない
            stopped = self._stop_child(sec, sec.stop_by)
        if not stopped:
            return self._child_alive(sec)
        if not cleared:
            try:
                with signals.held(self.dir / signals.STOP_LOCK, limits.seconds("relay.child-exit")) as got:
                    cleared = got and self._clear_launch()
            except OSError:  # ファイル: 外せなければ後継を起動しない
                cleared = False
        return None if cleared else Ended("refused", LAUNCH_STUCK.format(dir=self.dir))

    def _revoked_pick(self, sec: Section, now: float) -> Optional[Pick]:
        """止めた旧子の替え先（起動の理由 `revoked`）。止める直前の確かめで外れたら作り直し、尽きた・読めなければ None。"""

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            fallbacks = inputs.fallbacks()
            pick = account_decide.choose("auth", sec.account, rows, inputs.candidates(), self.s.guards, fallbacks, self._explicit())
            if pick.kind != "switch" and sec.rekeyed and sec.account in fallbacks:  # 登録し直した名前は新しい世代の候補に残す
                return Pick("switch", sec.account, REKEYED_REASON.format(name=sec.account), (str(sec.account),))
            return pick

        try:
            while True:
                pick = self._confirm(sec, "revoked", now, judge)
                if pick.kind != "switch":
                    return None
                if self._recheck_choice(sec) is None:
                    return pick
        except (accounts.AccountError, OSError):  # noqa: NDF401 ファイル: 旧子は止めてあり、替え先無しとして revoked で終える
            return None

    def _on_revoked(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        """替え先の無いキーの子の停止。旧子は `_stop_revoked` が止めてある。直し方か設定の当たりの 1 行を出して終える。"""
        if sec.end_cause == "auth":
            self._say(revoked_fix(self.tried))
        else:
            self._say(DRIFT_LINE.format(source=sec.end_source or key_helper.SETTINGS_FILE))
        return Ended("account_unavailable", REVOKED_END.format(reason=step.reason))
