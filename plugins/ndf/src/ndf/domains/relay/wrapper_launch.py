"""ラッパーの起動先を試す手順（#3604 #3864。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `LaunchSteps` が、起動の要求（`Choice`）の名前を順に用意して関所で起動し、飛ばした名前を 1 行で
知らせる。用意に落ちた名前と、関所の子が claude になれなかった名前は飛ばして次の候補へ進み、尽きたら起動の理由ごとの
`fallback` に従う。起動の直前に停止要求を読み直し、子の終わりを確かめてから書き戻す。状態（今のセグメント・短いセグメントの
続き・設定）と記録の手は `Wrapper` が持つ。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Union

from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_dir, accounts, launch_inputs, launch_target, stop_signal, switch_decide
from ndf.domains.relay.launch_target import RUN_AGAIN, SETTLE_LINE, SKIP_LINE, UNAVAILABLE, Choice, Target, why_of

# `wrapper` の `Settings`・`Launch`・`Section`（import の輪を作らないよう型の名前だけで受ける）
Settings = Launch = Section = Any

STOP_REASON = "停止シグナルファイルがある"
# 子の claude を起動できないときの停止理由の文（#3842）。`_ended` が `ndf-relay:` を前に付けて端末へ出す
LAUNCH_FAILED = "子の claude を起動できない（{why}）"
CHILD_ALIVE = "子（pid {pid}）を強制終了しても {seconds:g} 秒のうちに終わらない。次の子を起動せずに止める"
AGAIN_RESTART = "、tmux の外で ndf relay restart {name} を打つ"
SPUN = "spun"  # `_launch_target` の返り: 試した起動先がどれも再起動ループの検出に当たった


class Ended(NamedTuple):
    """ラッパーの終わり。`cause` は停止理由の種類（済んだときは空）、`code` は終了コード（None なら種類の終了コード）。"""

    cause: str
    reason: str
    code: Optional[int] = None


class Launched(NamedTuple):
    """起動した子と、その起動先。"""

    target: Target
    child: Any


class Halt(Exception):
    """候補を試す途中で、次の候補へ進まずに止まる（起動の直前の停止要求・起動先によらない起動の失敗）。"""

    def __init__(self, ended: Ended):
        super().__init__(ended.reason)
        self.ended = ended


class LaunchSteps:
    """`Wrapper` の起動先を試す手順。属性と記録の手（`_log`・`_say`・`_env_for`・`_inputs`・`_length`）は `Wrapper` が持つ。"""

    s: Settings
    dir: Path
    store: Path
    spawn: Callable[..., Any]
    clock: Callable[[], float]
    wall: Any
    section: Optional[Section]
    streak: switch_decide.Streak
    spin_held: bool
    last_skip: str
    _log: Callable[[Mapping[str, Any]], None]
    _say: Callable[[str], None]
    _env_for: Callable[[Optional[str], str], dict[str, str]]
    _inputs: Callable[[float, bool], launch_inputs.Inputs]
    _length: Callable[[Section, float], float]

    def _launch_target(self, req: Launch, argv: list[str], launch: str) -> Union[Launched, str, None]:
        """`req.choice.names` を順に試して子を起動する（子が止まっている前提）。

        候補に無い・上限・再起動ループに当たる名前は用意せず 1 行で飛ばす。用意に落ちた名前と、関所の子が claude になれなかった
        名前も 1 行で飛ばして次へ進む。`fixed` の要求は除外を当てずに用意を試す（落ちれば既定のログインへ落とさない）。
        起動の直前に停止要求を見たとき・起動先によらない起動の失敗は `Halt` で抜ける。
        """
        inputs = self._inputs(self.wall.now(), True)
        skipped: list[str] = []
        spun = False
        for name in dict.fromkeys(req.choice.names):
            code = None if req.choice.fixed else inputs.excluded(name)
            spin = code is None and self._spins(req.kind, name)
            spun = spun or spin
            got = None if code is not None or spin else self._try(name, req.reason, argv, launch)
            if got is not None:
                return Launched(dataclasses.replace(got.target, skipped=tuple(skipped)), got.child)
            if code is not None:
                self._log({"kind": "account_dir", "account": name, "reason": code})
                self.last_skip = why_of(code, name)
            self._say(SKIP_LINE.format(name=name, why=switch_decide.SPIN_REASON if spin else self.last_skip))
            skipped.append(name)
        if req.choice.fixed and req.choice.names:
            return SPUN if spun else None
        return self._fallback(req, argv, launch, tuple(skipped), spun)

    def _fallback(self, req: Launch, argv: list[str], launch: str, skipped: tuple[str, ...], spun: bool) -> Union[Launched, str, None]:
        """名前が尽きた後の起動先。`fallback` を試し、落ちれば `limit`・`guard`・`auth` は None（既定のログインへ落とさない）、ほかは既定のログイン。

        飛ばした名前のうち 1 つ以上が再起動ループの検出で飛び、ほかに起動できる起動先が無ければ `SPUN`。"""
        fb = req.choice.fallback
        if fb is not None:
            if self._spins(req.kind, fb):
                return SPUN
            got = self._try(fb, req.reason, argv, launch)
            if got is not None:
                return Launched(dataclasses.replace(got.target, skipped=skipped), got.child)
            if req.reason in ("limit", "guard", "auth"):  # 同じ会話を別の認証で続けない
                return SPUN if spun else None
        if self._spins(req.kind, None):
            return SPUN
        target = Target(None, None, "", req.reason, skipped, None, self._env_for(None, launch), argv, None)
        return Launched(target, self._spawn_target(target))

    def _try(self, name: str, reason: str, argv: list[str], launch: str) -> Optional[Launched]:
        """1 つの名前を `account_choice.target_env` で用意して起動する。用意できない・関所の子が claude になれなければ、
        理由の文を `last_skip` に置いて None（届けは解除済み）。"""
        got = account_choice.target_env(self._env_for(name, launch), name, self.store, self.s.state_dir, self.s.cwd, argv)
        if isinstance(got, str):
            self._log({"kind": "account_dir", "account": name, "reason": got})
            self.last_skip = why_of(got, name)
            return None
        p = got.prepared
        if p is None:
            target = Target(name, None, "", reason, (), None, got.env, got.argv, got.resolved)
        else:
            links = p.links
            row = {"kind": "account_dir", "account": name, "reason": p.sync, "local_only": links.local_only, "skipped": links.skipped}
            self._log({**row, "added": links.added})
            target = Target(name, p.config_dir, p.generation, reason, (), p.lease, got.env, got.argv, got.resolved)
        try:
            return Launched(target, self._spawn_target(target))
        except launch_target.GateFailed as exc:
            self._log({"kind": "account_dir", "account": name, "reason": exc.code})
            self.last_skip = why_of(exc.code, name)
            return None

    def _spawn_target(self, target: Target) -> Any:
        """起動の直前に停止要求を読み直し、子を起動する。関所の失敗は `GateFailed`（呼び手が次の候補へ進む）。

        停止要求があれば用意した届けを解除して `Halt`。関所の子の終わりを確かめられない・起動先によらない起動の失敗も `Halt`。
        """
        if stop_signal.has_stop(self.dir):
            if target.account is not None and target.lease is not None:
                account_dir.release(self.store, target.account, target.lease)
            stop_signal.drop_stop(self.dir)
            raise Halt(Ended("", STOP_REASON))
        try:
            return launch_target.spawn(self.spawn, target, self.s.cwd, self.store)
        except launch_target.GateFailed:
            raise
        except launch_target.ChildLeft as exc:
            raise Halt(Ended("refused", CHILD_ALIVE.format(pid=exc.pid, seconds=limits.seconds("relay.child-exit")))) from exc
        except OSError as exc:
            raise Halt(Ended("precondition", LAUNCH_FAILED.format(why=exc))) from exc

    def _not_launched(self, got: Union[str, Ended, None], prev: Optional[Section]) -> Ended:
        """子を起動しなかった終わり。None は起動先が尽きた、`SPUN` は再起動ループの検出、`Ended` はそのまま。"""
        if isinstance(got, Ended):
            return got
        if got is None:
            again = AGAIN_RESTART.format(name=self.s.name) if self.s.environ.get("NDF_RELAY_SPAWNED") else RUN_AGAIN
            name = prev.account if prev is not None and prev.account is not None else "（既定のログイン）"
            return Ended("account_unavailable", UNAVAILABLE.format(name=name, why=self.last_skip, again=again))
        return Ended("spin", f"試した起動先がどれも再起動ループの検出に当たる。{switch_decide.SPIN_REASON}")

    def _spins(self, kind: Optional[str], name: Optional[str]) -> bool:
        """起動先 `name` で次のセグメントを起動すると再起動ループの検出に当たるか（最初のセグメントは当たらない）。"""
        prev = self.section
        if prev is None or kind is None:
            return False
        if self.spin_held:
            return True
        here, there = switch_decide.target_key(prev.account), switch_decide.target_key(name)
        length = self._length(prev, self.clock() if prev.ended_at is None else prev.ended_at)
        nxt = switch_decide.advance(self.streak, length, kind, here, there, self.s.spin)
        return switch_decide.hit(nxt, self.s.spin)

    def _initial(self, now: float) -> Choice:
        """最初のセグメントの要求。環境変数 `NDF_CLAUDE_ACCOUNT` の名前 → 指定の順（登録を列挙しない）。

        環境変数の名前が事前の確かめを通っても、用意や関所で落ちたときのために指定を続けて試す。環境変数の名前が使えるときは、
        壊れた指定で止めずに環境変数の名前だけで試す。
        """
        env = self.s.environ.get("NDF_CLAUDE_ACCOUNT")
        env = env if env and accounts.NAME_RE.match(env) else None
        try:
            pin = accounts.pinned(self.s.state_dir)
        except accounts.AccountError:
            if env is None or self._inputs(now, True).excluded(env) is not None:
                raise
            pin = None
        return Choice(tuple(dict.fromkeys(n for n in (env, pin) if n is not None)))

    def _settle(self, sec: Section) -> None:
        """終わった子のアカウントが登録済みなら 1 回だけ書き戻す（`account_dir.settle`）。書き戻せなければ 1 行を出して続ける。

        子の終わりを確かめられなければ書き戻さず、使用の届けも残す（子が同じ設定ディレクトリを使い続けうるので、
        再登録・削除を通さない。届けは子が終われば残骸として消される）。
        """
        t = sec.target
        if sec.settled or t is None or t.account is None or t.lease is None:
            return
        if sec.child.isalive():
            self._log({"kind": "account_dir", "account": t.account, "reason": "child_alive"})
            return
        sec.settled = True
        why = account_dir.settle(self.store, t.account, self.s.environ, t.generation, t.lease)
        self._log({"kind": "account_dir", "account": t.account, "reason": why})
        if why is not None:
            self._say(SETTLE_LINE.format(name=t.account, why=why_of(why, t.account)))
