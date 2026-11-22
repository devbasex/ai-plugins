"""子の claude を起動するアカウントの指定と、使用率のキャッシュの読み取り（#2464・#3336・#3337）。

指定は `<状態の保存先>/relay/accounts/pinned.json` に 1 つだけ持つ（`ndf relay account use <名前>`）。選ぶ順は
環境変数 `NDF_CLAUDE_ACCOUNT` → 指定 → 無し（claude の既定）で、中継の起動と supervise の子の `claude -p` は
どちらも `chosen` で選ぶ（`session_env.child_env` が子へ渡す）。指定のファイルが壊れていれば `AccountError` を
送出し、呼び出し元が理由を出して止まる（読めない指定を黙って既定へ倒さない）。

使用率は、アカウントごと・登録の世代ごとのキャッシュ（`usage/<名前>/<世代>.json`）を NDF の status line が書き、
中継は読むだけである（#3864 の決定 4）。認証の失敗で外した間は `held.json`、上限の行の観測は `limited.json` が持ち、
どちらも名前の下に世代ごとの項目を持つ（書き手は自分の世代の項目だけを書く）。

使用率の API の取得の記録（`<アカウントの登録簿>/.usage-fetch/<名前>/<世代>.json`）は登録簿の側に置き、状態の保存先の
違うラッパーも同じ記録で取り直しを判定して同じ窓を読む（I16）。取得の重複は名前ごとの取得の排他（`fetch_lock`）で防ぐ。
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, tzinfo
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo

from ndf.adapters import claude_usage
from ndf.contracts import facts, limits
from ndf.domains.relay import account_decide, account_registry, signals

NAME_RE = signals.NAME_RE
WINDOWS = account_registry.WINDOWS
SCOPED = "seven_day_scoped"  # モデル別の週の窓。窓の名前は `seven_day_scoped/<識別>`
FULL = 100.0
WINDOW_WORDS = (("five_hour", re.compile(r"\bsession\b", re.I)), ("seven_day", re.compile(r"\bweek(?:ly)?\b", re.I)))
FETCH_LOCKS = ".fetch-locks"  # 取得の排他の錠の保存先（登録簿の下。アカウントのディレクトリの外）
RESETS_RE = re.compile(r"\bresets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b(?:\s*\(([^)]+)\))?", re.I)


class AccountError(ValueError):
    """アカウントの名前の形が違う・指定のファイルが壊れている。"""


def accounts_dir(state_dir: Path) -> Path:
    return state_dir / facts.value("locations", "relay-accounts", "path")


def pin_path(state_dir: Path) -> Path:
    return accounts_dir(state_dir) / "pinned.json"


def usage_dir(state_dir: Path) -> Path:
    """使用率のキャッシュの保存先（ファイルの表の `relay-usage` の `path` の先頭）。"""
    return accounts_dir(state_dir) / PurePosixPath(facts.value("ndf-files", "relay-usage", "path")).parts[0]


def usage_path(state_dir: Path, name: Optional[str] = None, generation: str = "") -> Path:
    """アカウント 1 つ・登録の世代 1 つのキャッシュ（`usage/<名前>/<世代>.json`。世代が空文字なら `_.json`）。

    `name` を省くと旧い 1 つのキャッシュ（`usage.json`）を返す。ラッパーの今の呼び手（`_limit_pick`）と
    そのテストのためだけに残し、ラッパーが `usages` の新しい形へ移る単位（U9）で消す。
    """
    if name is None:
        return accounts_dir(state_dir) / "usage.json"
    if not account_registry.valid_name(name):  # 登録名の検査（`..`・`/` を通さない）
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    return usage_dir(state_dir) / name / f"{_gen(generation)}.json"


def held_path(state_dir: Path) -> Path:
    return accounts_dir(state_dir) / facts.value("ndf-files", "relay-auth-held", "path")


def limits_path(state_dir: Path) -> Path:
    return accounts_dir(state_dir) / facts.value("ndf-files", "relay-limit-seen", "path")


def pinned(state_dir: Path) -> Optional[str]:
    """指定のアカウントの名前。指定が無ければ None、壊れていれば `AccountError`。"""
    path = pin_path(state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountError(f"アカウントの指定を読めない（{path}）: {exc}。ndf relay account clear で消してから指定し直す") from exc
    name = data.get("account") if isinstance(data, dict) else None
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise AccountError(f"アカウントの指定の形が違う（{path}）。ndf relay account clear で消してから指定し直す")
    return name


def use(state_dir: Path, name: str) -> None:
    """指定を書く。"""
    if not NAME_RE.match(name):
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    signals.replace_json(pin_path(state_dir), {"account": name})


def clear(state_dir: Path) -> bool:
    """指定を消す。消したら真。"""
    path = pin_path(state_dir)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def chosen(environ: Mapping[str, str], state_dir: Path) -> Optional[str]:
    """子を起動するアカウント。環境変数が先で、無ければ指定。"""
    name = environ.get("NDF_CLAUDE_ACCOUNT")
    if name and NAME_RE.match(name):
        return name
    return pinned(state_dir)


# ---------------------------------------------------------------- 使用率のキャッシュ（#3864 の決定 4）

Windows = dict[str, tuple[float, float]]  # 窓の名前 → (使用率, resets_at の Unix 秒)


def _gen(generation: str) -> str:
    """世代をファイルの名前と記録のキーにする（空文字は `_`）。`GEN_RE` の形でなければ `AccountError`（1 つのパスの要素に限る）。"""
    if not generation:
        return "_"
    if not account_registry.GEN_RE.fullmatch(generation):
        raise AccountError("登録の世代の形が違う")
    return generation


def _number(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _window(v: Any) -> Optional[tuple[float, float]]:
    if not isinstance(v, dict):
        return None
    percent, resets = _number(v.get("percent")), _number(v.get("resets_at"))
    return None if percent is None or resets is None else (percent, resets)


def _flat(windows: Any) -> Windows:
    """記録の `windows` を窓の名前ごとの対にする。形の違う窓は捨てる。"""
    if not isinstance(windows, dict):
        return {}
    got = {k: w for k in WINDOWS if (w := _window(windows.get(k))) is not None}
    scoped = windows.get(SCOPED)
    if isinstance(scoped, dict):
        got.update({f"{SCOPED}/{k}": w for k, v in scoped.items() if (w := _window(v)) is not None})
    return got


def _nested(windows: Windows) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, (percent, resets) in windows.items():
        row = {"percent": percent, "resets_at": resets}
        if k.startswith(f"{SCOPED}/"):
            out.setdefault(SCOPED, {})[k.split("/", 1)[1]] = row
        else:
            out[k] = row
    return out


def _read_json(path: Path) -> dict[str, Any]:
    """JSON の object。無い・読めない・形が違えば空（I7）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_windows(path: Path) -> Windows:
    return _flat(_read_json(path).get("windows"))


def _merge(prev: Optional[tuple[float, float]], new: tuple[float, float], now: float) -> Optional[tuple[float, float]]:
    """書き込みの規則の窓ごとの比べ。失効した入力・古い入力では前の値を残し、同じ窓の使用率は減らさない。"""
    if new[1] <= now or (prev is not None and new[1] < prev[1]):
        return prev
    if prev is None or new[1] > prev[1]:
        return new
    return (max(prev[0], new[0]), prev[1])


def _merged(prev: Windows, windows: Mapping[str, tuple[float, float]], now: float) -> Windows:
    """前の窓へ新しい窓を窓ごとに合わせる（書き込みの規則）。入力に無い窓は前の値を残す。"""
    merged = dict(prev)
    for k, w in windows.items():
        got = _merge(prev.get(k), w, now)
        if got is not None:
            merged[k] = got
    return merged


def write_usage(path: Path, windows: Mapping[str, tuple[float, float]], now: float) -> None:
    """status line と同じ形・同じ合わせ方で 1 つの記録を書く。錠が取れなければその回は書かない。

    錠は status line と同じ `<ファイル>.lock` のファイルの `flock`（待たない）。オーナーが生きている間はほかの書き手が奪えない。
    錠の場所がファイルでなければ（ディレクトリなど）消さずにその回は書かない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(path)
    if lock.is_dir():  # status line と同じく、錠の場所を塞ぐものを消さずにその回は書かない
        return
    with signals.held(lock, 0.0) as got:
        if not got:
            return
        prev = _read_windows(path)
        merged = _merged(prev, windows, now)
        if merged != prev:
            signals.replace_json(path, {"windows": _nested(merged), "recorded_at": now})


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def usages(
    state_dir: Path, store: Optional[Path] = None, now: Optional[float] = None, generations: Optional[Mapping[str, str]] = None
) -> list[account_decide.Usage]:
    """登録済みアカウントの今の世代のキャッシュを名前の順に読み、上限の観測を重ねる。

    リセットの時刻を過ぎた窓は 0% として読み、上限は過ぎていない窓のどれかが 100 以上か、上限の観測が続くとき。
    読めない・窓の無い記録は返さない（使用率の分からないアカウント）。ただし上限の観測が続く名前は使用率の
    分からない上限の行（`percent` が None）で返す。`store` があれば登録簿の側の取得の記録（`fetch_path`）の窓を
    `read_windows` で合わせて読み、その `spend_limit_reached` が真の名前を上限にする。`generations` を省くと旧い 1 つのキャッシュを読む（ラッパーの今の呼び手のため。`usage_path` の行）。
    """
    if generations is None or now is None:
        return _legacy_usages(state_dir)
    seen, spent = {}, set()
    for n, g in generations.items():
        cache = usage_path(state_dir, n, g)
        if store is None:
            seen[n] = _read_windows(cache)
            continue
        fetched = _read_json(
            fetch_path(store, n, g)
        )  # 1 度だけ読み、窓と上限の到達（spend_limit_reached）を同じ内容から取る（間の置き換えを混ぜない）
        seen[n] = _read_merged(cache, fetched)
        if fetched.get("spend_limit_reached") is True:
            spent.add(n)
    limited = set(limits_seen(state_dir, now, generations, seen)) | spent
    rows = []
    for name in sorted(generations):
        w = seen[name]
        if not w:
            if name in limited:
                rows.append(account_decide.Usage(name, None, None, None, None, True))
            continue
        rows.append(_windowed(name, w, now, name in limited))
    return rows


def _windowed(name: str, w: Windows, now: float, seen_limit: bool) -> account_decide.Usage:
    """窓ごとの値の行。過ぎた窓は 0% で、7 日の窓が過ぎていればそのリセットの時刻は分からない（None）。

    7 日の使用率は `seven_day` と、モデル別のスロットのうち過ぎていないものの最大の、大きいほう。
    """

    def live(k: str) -> Optional[float]:
        return None if k not in w else (w[k][0] if w[k][1] > now else 0.0)

    scoped = [p for k, (p, r) in w.items() if k.startswith(f"{SCOPED}/") and r > now]
    weekly = [v for v in (live("seven_day"), *scoped) if v is not None]
    seven = max(weekly) if weekly else None
    resets = w["seven_day"][1] if "seven_day" in w and w["seven_day"][1] > now else None
    percent = max(p if r > now else 0.0 for p, r in w.values())
    full = any(p >= FULL for p, r in w.values() if r > now)
    return account_decide.Usage(name, percent, live("five_hour"), seven, resets, full or seen_limit)


def _legacy_usages(state_dir: Path) -> list[account_decide.Usage]:
    path = usage_path(state_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountError(f"使用率のキャッシュを読めない（{path}）: {exc}") from exc
    rows = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise AccountError(f"使用率のキャッシュの形が違う（{path}）: accounts の配列が無い")
    return [_usage(row, path) for row in rows]


def _usage(row: Any, path: Path) -> account_decide.Usage:
    """旧い 1 つのキャッシュの行。窓ごとの値（`five`・`seven`）を持たない従来の形の行は、窓の最大の `percent` を
    5 時間と 7 日の両方の値として読む（従来の `percent` と閾値の比べ方を保つ）。"""
    try:
        opt = {k: None if row.get(k) is None else float(row[k]) for k in ("five", "seven", "seven_resets_at")}
        percent = float(row["percent"])
        if "five" not in row and "seven" not in row:
            opt["five"] = opt["seven"] = percent
        return account_decide.Usage(name=str(row["name"]), percent=percent, limited=bool(row["limited"]), **opt)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AccountError(f"使用率のキャッシュの行の形が違う（{path}）: {row!r}") from exc


def write_usages(state_dir: Path, rows: list[account_decide.Usage]) -> None:
    """旧い 1 つのキャッシュを書く（ラッパーのテストだけが使う。`usage_path` の行と同じ時期に消す）。"""
    signals.replace_json(usage_path(state_dir), {"accounts": [r._asdict() for r in rows]})


# ---------------------------------------------------------------- 外した間と上限の観測（世代ごとの項目）


def _entries(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """`{"<名前>": {"<世代>": {...}}}` の形の記録。壊れていれば空、形の違う項目は捨てる。"""
    return {
        n: {g: e for g, e in v.items() if isinstance(e, dict) and _number(e.get("until")) is not None}
        for n, v in _read_json(path).items()
        if isinstance(v, dict)
    }


def _current(path: Path, generations: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """今の世代の項目だけ（名前 → 項目）。"""
    data = _entries(path)
    return {n: data[n][_gen(g)] for n, g in generations.items() if n in data and _gen(g) in data[n]}


def _rewrite(path: Path, edit: Any) -> None:
    """錠の中で読み直し、`edit(data)` で変えて置き換える。錠を取れなければ `TimeoutError`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with signals.held(path.with_name(path.name + ".lock"), limits.seconds("relay.signal-lock")) as got:
        if not got:
            raise TimeoutError(f"記録の錠を取れない: {path}")
        data = _entries(path)
        edit(data)
        signals.replace_json(path, {n: v for n, v in data.items() if v})


def _put(path: Path, name: str, generation: str, entry: dict[str, Any], now: float) -> None:
    def edit(data: dict[str, dict[str, dict[str, Any]]]) -> None:
        for v in data.values():
            for g in [g for g, e in v.items() if e["until"] <= now]:
                del v[g]
        data.setdefault(name, {})[_gen(generation)] = entry

    _rewrite(path, edit)


def held(state_dir: Path, now: float, generations: Mapping[str, str]) -> dict[str, float]:
    """外した間の名前と終わりの時刻（終わりが `now` より後で、世代が今の登録と同じもの）。"""
    return {n: float(e["until"]) for n, e in _current(held_path(state_dir), generations).items() if e["until"] > now}


def hold(state_dir: Path, name: str, generation: str, until: float, now: float) -> None:
    """`name` の `generation` の項目だけを `until` まで外す。終わった項目は消し、ほかの世代の項目は変えない。"""
    _put(held_path(state_dir), name, generation, {"until": until}, now)


def unhold(state_dir: Path, name: str) -> None:
    """`name` の外した間を世代を問わず消す（`account use <名前>` の後）。"""
    path = held_path(state_dir)
    if name in _entries(path):
        _rewrite(path, lambda data: data.pop(name, None))


def see_limit(state_dir: Path, name: str, generation: str, seen_at: float, limit: tuple[float, Optional[str], Optional[float]]) -> None:
    """上限の行を初めて見た観測を書く（自分の世代の項目だけ）。`limit` は `limit_until` の返り（until・窓の名前・その resets_at）。"""
    until, window, window_resets_at = limit
    entry = {"seen_at": seen_at, "until": until, "window": window, "window_resets_at": window_resets_at}
    _put(limits_path(state_dir), name, generation, entry, seen_at)


def limits_seen(
    state_dir: Path, now: float, generations: Mapping[str, str], windows: Mapping[str, Mapping[str, tuple[float, float]]]
) -> dict[str, tuple[float, float]]:
    """解除に当たらない上限の観測（名前 → (見た時刻, until)）。

    解くのは `now ≥ until` か、観測の窓がキャッシュで `window_resets_at` と `until` のどちらよりも後の 100 未満の窓に
    入れ替わったときだけ（`until` 以下の窓は上限より前の入力が遅れて届いたものとみなし、解かない）。
    """
    out = {}
    for n, e in _current(limits_path(state_dir), generations).items():
        if now >= e["until"]:
            continue
        w, at = e.get("window"), _number(e.get("window_resets_at"))
        cur = windows[n].get(w) if n in windows and isinstance(w, str) and at is not None else None
        seen = _number(e.get("seen_at"))
        if seen is None or (cur is not None and at is not None and cur[1] > max(at, e["until"]) and cur[0] < FULL):
            continue
        out[n] = (seen, float(e["until"]))
    return out


def limit_until(
    line: str, windows: Mapping[str, tuple[float, float]], seen_at: float, tz: tzinfo
) -> tuple[float, Optional[str], Optional[float]]:
    """上限の行とキャッシュから (until, 窓の名前, 見た時刻のその窓の resets_at) を決める（「上限の観測」の順）。時計を読まない。"""
    word = next((k for k, pat in WINDOW_WORDS if pat.search(line)), None)
    at = _line_reset(line, seen_at, tz)
    if at is not None:
        until, zone = at
        name = word or next((k for k, (_, r) in windows.items() if _hm(r, zone) == _hm(until, zone)), None)
        return until, name, (windows[name][1] if name in windows else None)
    if word is not None and word in windows and windows[word][1] > seen_at:
        return windows[word][1], word, windows[word][1]
    full = [(r, k) for k, (p, r) in windows.items() if r > seen_at and p >= FULL]
    if full:
        r, k = max(full)
        return r, k, r
    five = windows.get("five_hour")
    if five is not None and five[1] > seen_at:
        return five[1], None, None
    return seen_at + limits.seconds("relay.limit-unknown"), None, None


def _hm(at: float, zone: tzinfo) -> tuple[int, int]:
    t = datetime.fromtimestamp(at, zone)
    return t.hour, t.minute


def _line_reset(line: str, seen_at: float, tz: tzinfo) -> Optional[tuple[float, tzinfo]]:
    """行の `resets 3pm (Asia/Tokyo)` の時刻のうち、`seen_at` より後で最も近いもの（と読んだ時間帯）。読めなければ None。"""
    m = RESETS_RE.search(line)
    if m is None:
        return None
    minute = int(m.group(2)) if m.group(2) is not None else 0
    if not 1 <= int(m.group(1)) <= 12 or minute > 59:
        return None
    zone = tz
    if m.group(4):
        try:
            zone = ZoneInfo(m.group(4).strip())
        except (ValueError, KeyError):
            zone = tz
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    t = datetime.fromtimestamp(seen_at, zone).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t.timestamp() <= seen_at:
        t += timedelta(days=1)
    return t.timestamp(), zone


def forget(state_dir: Path, store: Path, name: str, drop: str) -> None:
    """`name` の `drop` の世代（操作した登録簿の前の世代）の外した間・上限の観測・キャッシュ・取得の記録を消す。ほかの世代は残す。

    取得の排他（`fetch_lock`）と登録簿の排他の中で呼ぶ（`account_registry.register`・`unregister` の `settle`）。世代は登録
    ごとに新しいので、同じ状態の保存先を別の登録簿（`NDF_ACCOUNTS_DIR`）と共有していても、ほかの登録簿の同じ名前の記録は
    消さない。取得の排他の錠のファイル（`fetch_lock_path`）は消さない。錠を取れなければ `TimeoutError`、消せなければ `OSError`。
    """
    key = _gen(drop)

    def edit(data: dict[str, dict[str, dict[str, Any]]]) -> None:
        if name in data:
            data[name].pop(key, None)

    for path in (held_path(state_dir), limits_path(state_dir)):
        entries = _entries(path)
        if name in entries and key in entries[name]:
            _rewrite(path, edit)
    cache = usage_path(state_dir, name, drop)
    _unlink_all(cache, _lock_path(cache))
    _unlink_all(fetch_path(store, name, drop))


def _unlink_all(*paths: Path) -> None:
    """同じディレクトリのファイルを消し、ほかのファイルが残らなければディレクトリも消す（ほかの世代が残れば残す）。"""
    for p in paths:
        p.unlink(missing_ok=True)
    parent = paths[0].parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


# ---------------------------------------------------------------- 使用率の API の取得の記録と取得の排他（決定 4・I16）


def fetch_path(store: Path, name: str, generation: str) -> Path:
    """取得の記録（ファイルの表の `relay-usage-fetch`。`<登録簿>/.usage-fetch/<名前>/<世代>.json`。世代が空文字なら `_.json`）。"""
    if not account_registry.valid_name(name):
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    top = PurePosixPath(facts.value("ndf-files", "relay-usage-fetch", "path")).parts[0]
    return store / top / name / f"{_gen(generation)}.json"


def fetch_lock_path(store: Path, name: str) -> Path:
    """取得の排他の錠（`<登録簿>/.fetch-locks/<名前>.lock`）。状態の保存先と世代によらず、登録簿と名前で 1 つ。"""
    if not account_registry.valid_name(name):
        raise AccountError(f"アカウントの名前の形が違う: {name}")
    return store / FETCH_LOCKS / f"{name}.lock"


@contextmanager
def fetch_lock(store: Path, name: str, wait: float) -> Iterator[bool]:
    """取得の排他を `wait` 秒まで取りにいく（取り方はこれだけ。`flock` で、オーナーが生きている間は奪えず、死ねば OS が解く）。"""
    path = fetch_lock_path(store, name)
    os.makedirs(path.parent, mode=0o700, exist_ok=True)
    with signals.held(path, wait) as got:
        yield got


def fetch_wait() -> float:
    """登録・削除のコマンドが取得の排他を待つ上限（1 回の取得が排他を持つ最長）。"""
    http_s, refresh_s = limits.seconds("relay.usage-http"), limits.seconds("relay.oauth-refresh")
    return http_s * 2 + refresh_s + limits.seconds("claude-accounts.lock") * 4


def read_windows(usage: Path, fetch: Path) -> Windows:
    """status line のキャッシュと取得の記録の窓を読み取り用に合わせる（新しい `resets_at`、同じなら大きいほう。失効した窓も残す）。"""
    return _read_merged(usage, _read_json(fetch))


def _read_merged(usage: Path, fetched: Mapping[str, Any]) -> Windows:
    """`read_windows` の本体。取得の記録は読んだ後の内容で受け取る。"""
    return _merged(_read_windows(usage), _flat(fetched.get("windows")), -math.inf)


def write_fetch(path: Path, got: claude_usage.Usage, now: float) -> None:
    """成功した取得の窓を取得の記録へ窓ごとに合わせて書く（取得の排他の中で呼ぶ。書き手は同時に 1 つ）。

    `error` を null にし、`spend_limit_reached` は応答が真偽を持つときだけ置き換える。`at` は前の値を残す。
    """
    prev = _read_json(path)
    windows = {k: w for k, w in (("five_hour", got.five_hour), ("seven_day", got.seven_day)) if w is not None}
    if got.scoped is not None:  # 崩れていた（None）ならモデル別の窓は前の値を残す
        windows.update({f"{SCOPED}/{ident}": (p, r) for ident, p, r in got.scoped})
    spend = got.spend_limit_reached if got.spend_limit_reached is not None else prev.get("spend_limit_reached")
    row = {
        "at": _number(prev.get("at")),
        "error": None,
        "spend_limit_reached": spend if isinstance(spend, bool) else None,
        "windows": _nested(_merged(_flat(prev.get("windows")), windows, now)),
    }
    os.makedirs(path.parent, mode=0o700, exist_ok=True)
    signals.replace_json(path, row)


def due(usage: Path, fetch: Path, now: float, interval: float, until: Optional[float], tried: Optional[float]) -> bool:
    """取り直すか（I16）。最後に試した時刻から `interval` を過ぎたか、その後に過ぎたリセット（窓・上限の観測の `until`）があれば真。

    最後に試した時刻は取得の記録の `at` と `tried`（このラッパーが最後にキューへ入れた時刻）の大きいほう。
    """
    tried_at = [t for t in (_number(_read_json(fetch).get("at")), tried) if t is not None]
    if not tried_at:
        return True
    last = max(tried_at)
    if now - last >= interval:
        return True
    resets = [r for _, r in read_windows(usage, fetch).values()]
    if until is not None:
        resets.append(until)
    return any(last < t <= now for t in resets)


class FetchGate:
    """ラッパー 1 つに 1 つの、停止と GET の開始と更新の予約の排他。`close` の後は GET も更新も始めない。"""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._closed = False
        self._gets = 0
        self._refreshes = 0

    def _enter(self, attr: str) -> bool:
        with self._cv:
            if self._closed:
                return False
            setattr(self, attr, getattr(self, attr) + 1)
            return True

    def _leave(self, attr: str) -> None:
        with self._cv:
            setattr(self, attr, getattr(self, attr) - 1)
            self._cv.notify_all()

    def start_get(self) -> bool:
        return self._enter("_gets")

    def end_get(self) -> None:
        self._leave("_gets")

    def begin_refresh(self) -> bool:
        return self._enter("_refreshes")

    def end_refresh(self) -> None:
        self._leave("_refreshes")

    def close(self) -> None:
        """閉じたことを記録し、送信中の GET と更新の予約がどちらも 0 になるまで待つ（どちらの往復も期限を持つ）。"""
        with self._cv:
            self._closed = True
            self._cv.wait_for(lambda: self._gets == 0 and self._refreshes == 0)
