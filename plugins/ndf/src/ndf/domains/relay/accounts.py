"""子の claude を起動するアカウントの指定と、使用率のキャッシュの読み取り（#2464・#3336・#3337）。

指定は `<状態の保存先>/relay/accounts/pinned.json` に 1 つだけ持つ（`ndf relay account use <名前>`）。選ぶ順は
環境変数 `NDF_CLAUDE_ACCOUNT` → 指定 → 無し（claude の既定）で、中継の起動と supervise の子の `claude -p` は
どちらも `chosen` で選ぶ（`session_env.child_env` が子へ渡す）。指定のファイルが壊れていれば `AccountError` を
送出し、呼び出し元が理由を出して止まる（読めない指定を黙って既定へ倒さない）。

使用率は、アカウントごと・登録の世代ごとのキャッシュ（`usage/<名前>/<世代>.json`）を NDF の status line が書き、
中継は読むだけである（#3864 の決定 4）。認証の失敗で外した間は `held.json`、上限の行の観測は `limited.json` が持ち、
どちらも名前の下に世代ごとの項目を持つ（書き手は自分の世代の項目だけを書く）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, tzinfo
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from ndf.contracts import facts, limits
from ndf.domains.relay import account_decide, account_registry, signals

NAME_RE = signals.NAME_RE
WINDOWS = account_registry.WINDOWS
SCOPED = "seven_day_scoped"  # モデル別の週の窓。窓の名前は `seven_day_scoped/<識別>`
FULL = 100.0
WINDOW_WORDS = (("five_hour", re.compile(r"\bsession\b", re.I)), ("seven_day", re.compile(r"\bweek(?:ly)?\b", re.I)))
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


def write_usage(path: Path, windows: Mapping[str, tuple[float, float]], now: float) -> None:
    """status line と同じ形・同じ合わせ方で 1 つの記録を書く。錠が取れなければその回は書かない。

    錠は status line と同じ `<ファイル>.lock` の `flock`（待たない）。オーナーが生きている間はほかの書き手が奪えない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with signals.held(path.with_name(path.name + ".lock"), 0.0) as got:
        if not got:
            return
        prev = _read_windows(path)
        merged = dict(prev)
        for k, w in windows.items():
            got_w = _merge(prev.get(k), w, now)
            if got_w is not None:
                merged[k] = got_w
        if merged != prev:
            signals.replace_json(path, {"windows": _nested(merged), "recorded_at": now})


def usages(
    state_dir: Path, store: Optional[Path] = None, now: Optional[float] = None, generations: Optional[Mapping[str, str]] = None
) -> list[account_decide.Usage]:
    """登録済みアカウントの今の世代のキャッシュを名前の順に読み、上限の観測を重ねる。

    リセットの時刻を過ぎた窓は 0% として読み、上限は過ぎていない窓のどれかが 100 以上か、上限の観測が続くとき。
    読めない・窓の無い記録は返さない（使用率の分からないアカウント）。ただし上限の観測が続く名前は使用率の
    分からない上限の行（`percent` が None）で返す。`store` は登録簿の側の取得の記録を読む単位（U5b）で使う。
    `generations` を省くと旧い 1 つのキャッシュを読む（ラッパーの今の呼び手のため。`usage_path` の行）。
    """
    if generations is None or now is None:
        return _legacy_usages(state_dir)
    seen = {n: _read_windows(usage_path(state_dir, n, g)) for n, g in generations.items()}
    limited = limits_seen(state_dir, now, generations, seen)
    rows = []
    for name in sorted(generations):
        w = seen[name]
        if not w:
            if name in limited:
                rows.append(account_decide.Usage(name, None, True))  # type: ignore[arg-type]  # 型の変更は U6
            continue
        percent = max(p if r > now else 0.0 for p, r in w.values())
        full = any(p >= FULL for p, r in w.values() if r > now)
        rows.append(account_decide.Usage(name, percent, full or name in limited))
    return rows


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
    try:
        return account_decide.Usage(name=str(row["name"]), percent=float(row["percent"]), limited=bool(row["limited"]))
    except (KeyError, TypeError, ValueError) as exc:
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
    """`name` の `drop` の世代（操作した登録簿の前の世代）の外した間・上限の観測・キャッシュを消す。ほかの世代は残す。

    登録簿の排他の中で呼ぶ（`account_registry.register`・`unregister` の `settle`）。世代は登録ごとに新しいので、同じ状態の
    保存先を別の登録簿（`NDF_ACCOUNTS_DIR`）と共有していても、ほかの登録簿の同じ名前の記録は消さない。
    登録簿の側の取得の記録（`store` の `.usage-fetch/<名前>/`）を消すのは、取得の記録を足す単位（U5b）で入れる。
    錠を取れなければ `TimeoutError`、消せなければ `OSError`。
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
    for p in (cache, cache.with_name(cache.name + ".lock")):
        p.unlink(missing_ok=True)
    if cache.parent.is_dir() and not any(cache.parent.iterdir()):  # ほかの世代のファイルが残れば名前のディレクトリも残す
        cache.parent.rmdir()
