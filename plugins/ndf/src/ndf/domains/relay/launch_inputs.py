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
from typing import Mapping, NamedTuple, Optional, Sequence

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
    path = key[: -len(LSTAT)] if lstat else key
    try:
        st = os.lstat(path) if lstat else os.stat(path)
    except OSError:  # noqa: NDF401 ファイル: 無い源は None のスタンプで持つ（後から作られると変わる）
        return None
    return (st.st_mtime_ns, st.st_ino, st.st_size)


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
        """従量の接続の判定。記録のスタンプが同じなら打ち直さない（`tried` は判定の後に `Inputs` が当てる）。"""
        if old and all(unchanged(c.stamps) for c in old.values()):
            return old
        decl = account_choice.metered_decl(self.store, self.environ)
        metered = decl if isinstance(decl, tuple) else None
        probes = account_choice.fallbacks(self.store, self.environ, metered, self.cwd, self.argv, frozenset())
        return {p.name: LaunchCache(p.stamps, p.reason) for p in probes}

    def _probe(self, acc: reg.Account, old: Optional[LaunchCache]) -> LaunchCache:
        if old is not None and unchanged(old.stamps):
            return old
        child = session_env.child_env(self.environ, self.state_dir, None, decided=True)
        launch = account_choice.launch_of(child, self.store, self.state_dir, acc, self.cwd, self.argv)
        got = account_dir.ready(self.store, acc.name, self.environ, launch)
        return LaunchCache(got.stamps, got.reason)
