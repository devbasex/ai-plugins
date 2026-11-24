"""ラッパーの起動先を試す手順（#3604 #3864。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `LaunchSteps` が、起動の要求（`Choice`）の名前を順に用意して関所で起動し、飛ばした名前を 1 行で
知らせる。用意に落ちた名前と、関所の子が claude になれなかった名前は飛ばして次の候補へ進み、尽きたら起動の理由ごとの
`fallback` に従う。起動の直前に停止要求を読み直し、子の終わりを確かめてから書き戻す。状態（今のセグメント・短いセグメントの
続き・設定）と記録の手は `Wrapper` が持つ。
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Union

from ndf import identity
from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.contracts import limits
from ndf.domains.hooks import registration, tables
from ndf.domains.relay import (
    account_choice,
    account_dir,
    accounts,
    key_helper,
    launch_inputs,
    launch_target,
    signals,
    stop_signal,
    switch_decide,
)
from ndf.domains.relay import account_registry as reg
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


class RewakePlan(NamedTuple):
    """起動ごとの起床の hook の登録の判定の前提（#4068 の詳細設計の「ラッパー」の 1）。

    `ready` は利用者の `--settings` が無く、起床の時点が届く起動か。`settings` は一覧が空でないときの登録の JSON、
    `floor_exe` は `_start` の下限の確かめが解いた実パス（同じ実行ファイルならバージョンを記録から読む）。
    """

    ready: bool = False
    versions: tuple[str, ...] = ()
    settings: Optional[str] = None
    floor_exe: Optional[str] = None


# 起床の hook の登録を渡した子にだけ渡す環境変数
REWAKE_ENV = identity.env_name("relay-rewake")


def recorded_version(exe: Optional[str], env: Mapping[str, str], cwd: Path) -> Optional[str]:
    """`--version` の記録に在れば読んだバージョン。記録に無ければ None（打たない）。"""
    found = cli_floor.version_record_key(exe)
    return cli_floor.version_at("claude", exe, env, cwd) if found is not None and found[1] else None


class Gate(NamedTuple):
    """候補 1 つの起床の hook の登録の判定に使う値（実行ファイルとそのバージョン）。"""

    name: Optional[str]
    section: int
    env: Mapping[str, str]
    decl: Any
    exe: Optional[str]
    same: bool
    version: Optional[str]


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
    _env_for: Callable[[Optional[str], str, str], dict[str, str]]
    _inputs: Callable[[float, bool], launch_inputs.Inputs]
    _length: Callable[[Section, float], float]
    rewake: RewakePlan = RewakePlan()
    rewake_used: dict[str, Any] = {}

    def _rewake_plan(self, env: Mapping[str, str]) -> RewakePlan:
        """起動ごとに、起床の hook の登録を渡してよい前提をそろえる（`--version` は打たない）。"""
        user = account_choice.has_settings((*self.s.args, *self.s.first_args))
        why = registration.rewake_unreachable()
        if why is not None:
            self._log({"kind": "rewake_off", "reason": why})
        if user or why is not None:
            return RewakePlan()
        versions = limits.texts("cache.rewake", *limits.TEXT_KEYS)
        root = getattr(self, "launched_root", None) or tables.PLUGIN_ROOT
        settings = json.dumps(registration.rewake_settings(root)) if versions else None
        return RewakePlan(True, versions, settings, cli_floor.resolve(CLAUDE, env, self.s.cwd))

    def _gate_before(self, name: Optional[str], req: Launch, env: Mapping[str, str], decl: Any) -> Gate:
        """候補の実行ファイルを解き、下限の確かめと同じ実行ファイルなら記録からバージョンを読む。前提が無ければ何も解かない。"""
        if not self.rewake.ready:
            return Gate(name, req.number, env, decl, None, False, None)
        venv = account_choice.version_env(env, decl)
        exe = cli_floor.resolve(CLAUDE, venv, self.s.cwd)
        same = exe is not None and exe == self.rewake.floor_exe
        return Gate(name, req.number, env, decl, exe, same, recorded_version(exe, venv, self.s.cwd) if same else None)

    def _gated(self, name: str, req: Launch, env: Mapping[str, str]) -> Union[tuple[Gate, Any], str]:
        """候補の判定の値と、キーの子なら払い出しの記録の helper。用意できなければ理由の符号。

        従量の接続の設定は候補ごとに 1 回だけ読み、用意とバージョンの判定に同じ値を渡す。記録の無いキーの子は起動しない
        （払い出しを数えられない）。
        """
        decl = account_choice.metered_of(self.store, env) if self.rewake.ready and name == reg.METERED else None
        if isinstance(decl, str):
            return decl
        helper = None
        if account_choice.key_child(env, name, self.store, decl):
            try:
                helper = key_helper.create(self.dir)
            except OSError:  # noqa: NDF401 ファイル: 払い出しの記録を作れないキーの子は起動しない
                return "key_record"
        return self._gate_before(name, req, env, decl), helper

    def _listed(self, g: Gate) -> bool:
        """下限の確かめと同じ実行ファイルで、その記録のバージョンが一覧と完全に一致するか。"""
        return g.same and self.rewake.settings is not None and g.version in self.rewake.versions

    def _gate_after(self, g: Gate, key: bool) -> Optional[str]:
        """用意の後にバージョンを読む候補（別の実行ファイルを指す従量の接続）のバージョン。キーでない子は `version_probe` の行を書く。"""
        venv = account_choice.version_env(g.env, g.decl)
        found = cli_floor.version_record_key(g.exe)
        cached = found is not None and found[1]
        if not key and found is not None:
            row = {"kind": "version_probe", "pid": os.getpid(), "section": g.section, "account": g.name, "claude_path": g.exe}
            ran = bool(self.rewake.versions) and not cached
            self._log({**row, "mtime_ns": found[0][1], "size": found[0][2], "cached": cached, "ran": ran})
        if self.rewake.versions and not key:
            return cli_floor.version_at("claude", g.exe, venv, self.s.cwd)
        return recorded_version(g.exe, venv, self.s.cwd) if cached else None

    def _gate_built(self, g: Gate, built: list[str], key: bool) -> tuple[list[str], bool, Optional[str]]:
        """用意した起動の引数に、用意の後にバージョンを読む候補だけ登録を足す。（起動の引数, 登録したか, バージョン）。"""
        if not self.rewake.ready or g.exe is None or g.same or g.decl is None:
            return built, self._listed(g), g.version
        version = self._gate_after(g, key)
        if self.rewake.settings is None or version not in self.rewake.versions:
            return built, False, version
        return (account_choice.merged_settings if key else account_choice.with_settings)(built, self.rewake.settings), True, version

    def _rewake_child(self, launch: str, env: Mapping[str, str], on: bool, version: Optional[str]) -> dict[str, str]:
        """起動の直前に今の起動の記録を書き直し、子の環境の `NDF_RELAY_REWAKE` を登録の有無にそろえる。"""
        signals.set_launch(self.dir, launch, rewake=on, claude_version=version)
        self.rewake_used = {"rewake": on, "claude_version": version, "rewake_ready": self.rewake.ready}
        child = {k: v for k, v in env.items() if k != REWAKE_ENV}
        return {**child, REWAKE_ENV: "1"} if on else child

    def _metered(self) -> Optional[tuple[dict[str, str], bool]]:
        """従量の接続の設定（変数の組と、保存した設定か）。無い・読めない・検証に外れれば None（詳細設計書の「従量の接続の設定の検証」）。"""
        decl = account_choice.metered_decl(self.store, self.s.environ)
        return decl if isinstance(decl, tuple) else None

    def _fallbacks(self, now: float) -> list[str]:
        """起動できる従量の接続の名前（`metered` の順。`tried` を除く）。判定は `LaunchCache` に残し、スタンプが同じなら打ち直さない。"""
        return self._inputs(now, True).fallbacks()

    def _launch_target(self, req: Launch, argv: list[str], launch: str) -> Union[Launched, str, None]:
        """`req.choice.names` を順に試して子を起動する（子が止まっている前提）。`argv` は最初のプロンプトを除いた起動引数で、
        プロンプト（`req.prompt`）は起動先ごとに足す（キーの子は起動引数に載せず、最初の払い出しの後に子の端末へ送る）。

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
            got = None if code is not None or spin else self._try(name, req, argv, launch)
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

        飛ばした名前のうち 1 つ以上が再起動ループの検出で飛び、ほかに起動できる起動先が無ければ `SPUN`。
        `Choice.last` の要求は何も起動しない（認証に失敗した今のアカウント・既定のログインへ落とさない）。"""
        if req.choice.last:
            return SPUN if spun else None
        fb = req.choice.fallback
        if fb is not None:
            if self._spins(req.kind, fb):
                return SPUN
            got = self._try(fb, req, argv, launch)
            if got is not None:
                return Launched(dataclasses.replace(got.target, skipped=skipped), got.child)
            if req.reason in ("limit", "guard", "auth"):  # 同じ会話を別の認証で続けない
                return SPUN if spun else None
        if self._spins(req.kind, None):
            return SPUN
        env = self._env_for(None, launch, req.session_id)
        gate = self._gate_before(None, req, env, None)
        on = self._listed(gate)
        argv = account_choice.with_settings(argv, self.rewake.settings) if on else argv
        full = [*argv, req.prompt] if req.prompt else argv
        target = Target(None, None, "", req.reason, skipped, None, self._rewake_child(launch, env, on, gate.version), full, None)
        return Launched(target, self._spawn_target(target))

    def _skip(self, name: str, code: str) -> None:
        self._log({"kind": "account_dir", "account": name, "reason": code})
        self.last_skip = why_of(code, name)

    def _try(self, name: str, req: Launch, argv: list[str], launch: str) -> Optional[Launched]:
        """1 つの名前を `account_choice.target_env` で用意して起動する。用意できない・関所の子が claude になれなければ、
        理由の文を `last_skip` に置いて None（届けは解除済み）。キーの子は払い出しの記録を作り、記録先と ID を helper に渡す。"""
        env = self._env_for(name, launch, req.session_id)
        found = self._gated(name, req, env)
        if isinstance(found, str):
            self._skip(name, found)
            return None
        gate, helper = found
        use = account_choice.with_settings(argv, self.rewake.settings) if self._listed(gate) else argv
        if helper is not None:
            got = account_choice.key_target(env, self.store, self.s.state_dir, self.s.cwd, use, (helper.path, helper.helper_id))
        else:
            got = account_choice.target_env(env, name, self.store, self.s.state_dir, self.s.cwd, use)
        if isinstance(got, str):
            self._skip(name, got)
            return None
        built, on, version = self._gate_built(gate, got.argv, helper is not None)
        child = self._rewake_child(launch, got.env, on, version)
        reason, held = req.reason, req.prompt if helper is not None and req.prompt else None
        final = built if held is not None or not req.prompt else [*built, req.prompt]
        p = got.prepared
        if p is None:
            gen = got.generation
            floor = account_choice.claude_version(got.env, self.s.cwd) if helper is not None else None  # `cli_floor` の記録から読む
            target = Target(
                name, None, gen, reason, (), None, child, final, got.resolved, helper, got.settings_file, got.pin_file, held, floor
            )
        else:
            links = p.links
            row = {"kind": "account_dir", "account": name, "reason": p.sync, "local_only": links.local_only, "skipped": links.skipped}
            self._log({**row, "added": links.added})
            target = Target(name, p.config_dir, p.generation, reason, (), p.lease, child, final, got.resolved)
        try:
            return Launched(target, self._spawn_target(target))
        except launch_target.GateFailed as exc:
            target.drop_files()  # 起動しなかった子の一時ファイルと固定のファイルを残さない（起動した子は `_settle` が消す）
            self._skip(name, exc.code)
            return None
        except BaseException:
            target.drop_files()
            raise

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
        except launch_target.NotFound as exc:  # 起動先の環境の PATH に claude が無い: 名前のある起動先なら次の候補へ進む
            if target.account is None:
                raise Halt(Ended("precondition", LAUNCH_FAILED.format(why=exc))) from exc
            raise launch_target.GateFailed("exe_missing") from exc
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
        if t is not None and (t.settings_file or t.pin_file) and not sec.child.isalive():
            t.drop_files()  # 子が終わるまでは残す（Claude Code は動いている間も --settings のファイルを読み直す）
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
