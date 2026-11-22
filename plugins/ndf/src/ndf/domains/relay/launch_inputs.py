"""ラッパーの判断の入力のキャッシュ: 使用率のキャッシュと、名前ごとの起動できるかのキャッシュ（#3604 #3864 の決定 3・5）。

使用率のキャッシュ（`UsageCache`）と起動できるかのキャッシュ（`LaunchCache`）を別々に持ち、別々に失効させる。
どちらも源のファイルの `stat`（`lstat` を含む）のスタンプで比べ、スタンプが同じなら源を開かない。
ラッパー（`wrapper.py`）から行数の上限のため分けた（判断の入力を読む責務）。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Optional, Sequence

from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.domains.relay import account_choice, account_decide, account_dir, accounts, session_env
from ndf.domains.relay import account_registry as reg

LSTAT = " (lstat)"  # `account_dir.ready` のスタンプのキーの末尾（その名前は lstat で比べる）


class UsageCache(NamedTuple):
    """使用率と上限の観測を重ねた行。`stamp` は読んだ源のスタンプ、`valid_until` は窓のリセット・外した間・上限の観測の終わりの最小。

    `limited_until` は上限の観測が続く名前とその `until`（取得の `due` に渡す）。`spent` は使用率の API が上限の到達を
    返した名前（行と同じ取得の記録から取る）。`generations` は読んだ登録の世代（名前 → 世代。観測と組で使う）。
    """

    stamp: tuple
    valid_until: float
    rows: list[account_decide.Usage]
    limited_until: Mapping[str, float]
    spent: frozenset[str]
    generations: Mapping[str, str]


class LaunchCache(NamedTuple):
    """1 つの名前の判定のキャッシュ。`ready` が返した `Probe` のスタンプと理由（None は起動できる）。"""

    stamps: dict[str, Optional[tuple]]
    result: Optional[str]


@dataclass
class Inputs:
    """判断の入力。`names` は再登録が要らず外した間でない登録済みアカウント（起動できるかを確かめる前の候補）。"""

    usage: Optional[UsageCache] = None
    launch: dict[str, LaunchCache] = field(default_factory=dict)
    names: tuple[str, ...] = ()
    # 従量の接続の替え先の判定（名前 → キャッシュ。`account_choice.fallbacks` の `Probe`）と、この続きで認証に失敗した接続先
    fallback: dict[str, LaunchCache] = field(default_factory=dict)
    tried: frozenset[str] = frozenset()

    def candidates(self) -> list[str]:
        """起動できる候補（`names` のうちキャッシュの理由が None のもの）。"""
        return [n for n in self.names if n in self.launch and self.launch[n].result is None]

    def fallbacks(self) -> list[str]:
        """起動できる従量の接続（キャッシュの理由が None で `tried` に無いもの。`metered` の順）。"""
        return [n for n, c in self.fallback.items() if c.result is None and n not in self.tried]

    def excluded(self, name: str) -> Optional[str]:
        """試す前に外す理由の符号（候補に無ければキャッシュの理由か `unusable`、上限なら `limited`）。外さなければ None。

        従量の接続は、設定が無いか検証に外れれば `metered_unavailable`、`tried` にあれば `tried`（上限の観測は当てない）。
        API キー（`key:<名前>`）は、復帰先に無い（登録簿に無い・使い方が `off`）なら `key_missing`、ほかは従量の接続と同じ。
        """
        if name == reg.METERED or name.startswith(account_decide.KEY_PREFIX):
            cached = self.fallback.get(name)
            if cached is None:
                return "metered_unavailable" if name == reg.METERED else "key_missing"
            return cached.result or ("tried" if name in self.tried else None)
        if name not in self.candidates():
            cached = self.launch.get(name)
            return cached.result if cached is not None and cached.result else "unusable"
        rows = self.usage.rows if self.usage is not None else []
        return "limited" if any(u.name == name and u.limited for u in rows) else None

    def pin_first(self, pin: str, g: account_decide.Guards) -> bool:
        """使える指定（候補にあり上限でない）が、閾値の内側（組 0〜2）か、指定を除いた `order` に組 0〜2 の候補が無いか（決定 7）。"""
        if self.excluded(pin) is not None:
            return False
        rows = self.usage.rows if self.usage is not None else []
        by = {u.name: u for u in rows}
        inside = (0, 1, 2)
        rest = account_decide.order(pin, rows, self.candidates(), g)
        return account_decide.group(by.get(pin), g) in inside or all(account_decide.group(by.get(n), g) not in inside for n in rest)


def _stat(key: str) -> Optional[tuple]:
    lstat = key.endswith(LSTAT)
    return account_dir.stat_stamp(key[: -len(LSTAT)] if lstat else key, lstat)  # 作る側と同じ形で比べる


def stamp(path: Path) -> Optional[tuple]:
    """1 つのファイルのスタンプ（無ければ None）。"""
    return _stat(str(path))


def unchanged(stamps: Mapping[str, Optional[tuple]]) -> bool:
    """キャッシュのスタンプがどれも今の `stat` と同じか（源のファイルを開かない）。"""
    return all(_stat(k) == v for k, v in stamps.items())


def _usage_sources(state_dir: Path, store: Path, generations: Mapping[str, str]) -> list[Path]:
    files = [accounts.held_path(state_dir), accounts.limits_path(state_dir)]
    for n, g in sorted(generations.items()):
        files += [accounts.usage_path(state_dir, n, g), accounts.fetch_path(store, n, g)]
    return files


def _usage(state_dir: Path, store: Path, now: float, generations: Mapping[str, str], stamp: tuple) -> UsageCache:
    """使用率を読み直す。`valid_until` は過ぎていない窓のリセット・外した間・上限の観測の終わりの最小。"""
    windows = {
        n: accounts.read_windows(accounts.usage_path(state_dir, n, g), accounts.fetch_path(store, n, g)) for n, g in generations.items()
    }
    ends = [r for w in windows.values() for _, r in w.values() if r > now]
    ends += list(accounts.held(state_dir, now, generations).values())
    seen = {n: until for n, (_, until) in accounts.limits_seen(state_dir, now, generations, windows).items()}
    ends += list(seen.values())
    rows, spent = accounts.usage_rows(state_dir, store, now, generations)
    return UsageCache(stamp, min(ends, default=math.inf), rows, seen, spent, dict(generations))


class Reader:
    """1 本のラッパーの判断の入力のキャッシュ。`environ` は親の環境、`cwd`・`argv` は子の作業ディレクトリと起動引数。"""

    def __init__(self, store: Path, state_dir: Path, environ: Mapping[str, str], cwd: Path, argv: Sequence[str]):
        self.store, self.state_dir, self.environ, self.cwd, self.argv = store, state_dir, environ, cwd, list(argv)
        self.inputs = Inputs()
        self.tried: set[str] = set()  # この続きで認証に失敗した接続先（ラッパーの `Wrapper.tried` と同じ集合）
        self._accs: Optional[tuple[tuple, dict[str, reg.Account]]] = None  # 登録簿のスタンプと読んだ登録
        self._held: Optional[tuple[tuple, dict[str, float]]] = None  # 外した間の記録のスタンプと世代と、終わりの時刻
        # 従量の接続の設定のスタンプ（保存した設定の `metered.json`。環境変数の設定は起動の間に変わらないので空）と、そのときの判定と、
        # 判定のときに `claude` を解決した `PATH` と実パスの組（未検出は None。関所のバージョンに依る候補が無く解決しなければ空）と、
        # そのときの子の基本の `PATH`（登録簿のキーの子の `PATH`。従量の接続の設定の `PATH` と同じ値に変わっても判定し直す）
        self._fallback: Optional[
            tuple[dict[str, Optional[tuple]], dict[str, LaunchCache], dict[Optional[str], Optional[str]], Optional[str]]
        ] = None
        # 子の `PATH` をそろえる起動先の NDF の根（ラッパーの `launched_root`。替え先の判定を実際の子と同じ `PATH` で打つ）
        self.launched_root: Optional[Path] = None

    def child_environ(self) -> Mapping[str, str]:
        """替え先の判定に使う親の環境。起動先の根が分かれば、実際の子と同じく `PATH` を `with_launched_ndf` でそろえる。"""
        return self.environ if self.launched_root is None else session_env.with_launched_ndf(self.environ, self.launched_root)

    def _registered(self) -> dict[str, reg.Account]:
        """登録済みアカウント。登録簿のディレクトリ・各登録のディレクトリと `account.json` のスタンプが同じなら読み直さない。"""
        stamp = self._registry_stamp()
        if self._accs is None or self._accs[0] != stamp:
            self._accs = (stamp, account_choice.registered(self.store))
        return self._accs[1]

    def _registry_stamp(self) -> tuple:
        base = _stat(str(self.store))
        if base is None or not os.path.isdir(self.store):
            return (base,)
        with os.scandir(self.store) as it:
            entries = sorted(e.name for e in it if reg.valid_name(e.name))
        dirs = tuple((e, _stat(str(self.store / e)), _stat(str(self.store / e / reg.ACCOUNT_FILE))) for e in entries)
        return (base, dirs)

    def _held_until(self, generations: Mapping[str, str], now: float) -> dict[str, float]:
        """外した間（名前 → 終わり）。記録のスタンプと世代が同じなら読み直さず、終わりの過ぎた名前だけを落とす。"""
        key = (_stat(str(accounts.held_path(self.state_dir))), tuple(sorted(generations.items())))
        if self._held is None or self._held[0] != key:
            self._held = (key, accounts.held(self.state_dir, -math.inf, generations))
        return {n: until for n, until in self._held[1].items() if until > now}

    def generations(self) -> dict[str, str]:
        """登録済みアカウントの名前と今の登録の世代（登録簿のスタンプが同じなら読み直さない）。"""
        return {n: a.generation for n, a in self._registered().items()}

    def recheck(self, name: str) -> Optional[str]:
        """1 つの名前へ `ready` を打ち直してキャッシュを置き換え、理由の符号（起動できれば None）を返す（子を止める直前の確かめ）。"""
        acc = self._registered().get(name)
        if acc is None:
            return "unusable"
        got = self._probe(acc, None)
        self.inputs.launch[name] = got
        return got.result

    def recheck_fallback(self, name: str) -> Optional[str]:
        """従量の接続か API キーの替え先の判定を、設定・登録簿のスタンプが変わっていれば打ち直してキャッシュを置き換え、外す理由の符号
        （起動できれば None）を返す（子を止める直前の確かめ）。"""
        self.inputs.fallback = self._fallbacks(self.inputs.fallback)
        return self.inputs.excluded(name)

    def read(self, now: float, need_candidates: bool) -> Inputs:
        """使用率のキャッシュはスタンプが同じで `now < valid_until` なら使い、違えば使用率だけを読み直す（`ready` を打たない）。

        登録と外した間はスタンプが変わったときだけ読み直し、同じ見回りの登録を候補の判定にも使う。起動できるかのキャッシュは、
        `need_candidates` が真のときだけ名前ごとにスタンプを比べ、変わった名前にだけ `ready` を打ち直す。
        """
        accs = self._registered()
        generations = {n: a.generation for n, a in accs.items()}
        stamp = tuple((str(p), _stat(str(p))) for p in _usage_sources(self.state_dir, self.store, generations))
        old = self.inputs.usage
        if old is None or old.stamp != stamp or now >= old.valid_until:
            self.inputs.usage = _usage(self.state_dir, self.store, now, generations, stamp)
        if need_candidates:
            held = self._held_until(generations, now)
            usable = {n: a for n, a in accs.items() if not a.needs_relogin and n not in held}
            self.inputs.names = tuple(usable)
            self.inputs.launch = {n: self._probe(a, self.inputs.launch.get(n)) for n, a in usable.items()}
            self.inputs.fallback = self._fallbacks(self.inputs.fallback)
            self.inputs.tried = frozenset(self.tried)
        return self.inputs

    def _fallbacks(self, old: dict[str, LaunchCache]) -> dict[str, LaunchCache]:
        """従量の接続と API キーの判定。設定・登録簿・記録のスタンプが同じなら打ち直さない（`tried` は判定の後に `Inputs` が当てる）。

        設定が無い・壊れている（判定が空）ときも結果を残し、保存した設定のスタンプが変わったときだけ読み直す。保存した設定と
        候補の依存先を 1 つの表にまとめて持ち、同じファイル（`metered.json` など）は見回りごとに 1 回だけ `stat` する。
        子の環境（`child_environ`）は判定し直すときか `claude` を解決するときだけ作る。
        `metered.json` のスタンプは設定を読む前に 1 回だけ取り、候補の判定へも渡す。表では読む前のスタンプを残す（読んだ後に別の
        プロセスが更新したら、次の見回りでスタンプが違って読み直す。読んだ後のスタンプで上書きすると古い設定の判定が残る）。
        """
        cached = self._fallback
        envs: list[Mapping[str, str]] = []  # 子の環境（`PATH` の各要素のリンクをたどるので、要るときに 1 回だけ作る）
        resolved: dict[Optional[str], Optional[str]] = {}  # この判定で解決した `PATH` → `claude` の実パス（`PATH` ごとに 1 回だけ）

        def environ_of() -> Mapping[str, str]:
            if not envs:
                envs.append(self.child_environ())
            return envs[0]

        def exe_for(env: Mapping[str, str]) -> Optional[str]:
            key = env.get("PATH")
            if key not in resolved:
                resolved[key] = cli_floor.resolve(CLAUDE, {} if key is None else {"PATH": key}, self.cwd)
            return resolved[key]

        # 関所の働くバージョンかは `claude` の指す実行ファイルで決まる。シンボリックリンクの付け替えや `PATH` の変化で別の実行ファイルを
        # 指したら（未検出からの変化も含め）、旧ファイルのスタンプが同じでも判定し直す。前の判定が解いた `PATH` ごとに解き直し、子の
        # `PATH` が変われば（起動先の NDF の根の変化）判定し直す。関所に依る候補が無ければ `PATH` を探さない
        if cached is not None and cached[1] is old and unchanged(cached[0]) and self._same_exes(cached[2], cached[3], environ_of):
            return old
        read = account_choice.read_before(self.store)._replace(exe_for=exe_for)
        path, book = str(reg.metered_path(self.store)), str(reg.book_path(self.store))
        # 環境変数の設定なら保存した設定に依らない。登録簿は API キーの替え先の有無を決めるので、判定が空でも依存先に入れる
        base = {k: read.stamps[k] for k in ((book,) if reg.declared_env(self.environ) is not None else (path, book))}
        decl = account_choice.metered_decl(self.store, self.environ)
        metered = decl if isinstance(decl, tuple) else None
        probes = account_choice.fallbacks(read, environ_of(), metered, self.cwd, self.argv, frozenset())
        got = {p.name: LaunchCache(p.stamps, p.reason) for p in probes}
        for p in probes:
            base.update({k: v for k, v in p.stamps.items() if k not in base})
        self._fallback = (base, got, resolved, environ_of().get("PATH") if resolved else None)
        return got

    def _same_exes(
        self, exes: dict[Optional[str], Optional[str]], base: Optional[str], environ_of: Callable[[], Mapping[str, str]]
    ) -> bool:
        """前の判定が解いた `PATH` ごとの `claude` の実パスが今も同じで、子の基本の `PATH` が前と同じか（解いていなければ真）。"""
        if not exes:
            return True
        same = all(cli_floor.resolve(CLAUDE, {} if p is None else {"PATH": p}, self.cwd) == e for p, e in exes.items())
        return same and environ_of().get("PATH") == base

    def _probe(self, acc: reg.Account, old: Optional[LaunchCache]) -> LaunchCache:
        if old is not None and unchanged(old.stamps):
            return old
        child = session_env.child_env(self.environ, self.state_dir, None, decided=True)
        launch = account_choice.launch_of(child, self.store, self.state_dir, acc, self.cwd, self.argv)
        got = account_dir.ready(self.store, acc.name, self.environ, launch)
        return LaunchCache(got.stamps, got.reason)
