"""pace.py: 進め方の宣言（`.ndf/pace.json`）の読み取りとパスの照合（#1078）。

check-trigger.py・mvv-gate.py・supervise.py が同じ規則で読む。標準ライブラリだけで書く。

    {"version": 1,
     "fast": {"enabled": true, "modes": [...], "verify": "<導入の確認のコマンド>"},
     "auto": {"enabled": true, "modes": [...], "verify": "<導入の確認のコマンド>"},
     "areas": [{"name": "...", "common": true, "paths": ["<glob>", ...]}, ...],
     "boundary_paths": ["<glob>", ...],
     "triggers": {"score": 15, "common_weight": 2, "lines": 5000, "escapes": 2, "hours": 24},
     "loop": {"enabled": false, "budget": {"laps": null, "usd": null, "hours": null}, "same_failure": 2, "max_issues": 3}}

`business_hours`（`{"timezone": "Asia/Tokyo", "days": ["mon", ...], "start": "09:00", "end": "18:00"}`。#2913）は
`read_hours` だけが読み、不正なら警告を返して宣言なしとして扱う。
`loop` 節は `read_loop` だけが読む（`read_pace` は検証しない。#2089）。`loop.budget` の各単位は、書かない・`null` なら無制限である。

glob の `**` は区切りをまたぎ、`*` と `?` はまたがない。`**/` は 0 階層でもよい。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import zoneinfo
from dataclasses import dataclass
from pathlib import Path

DECL_NAME = "pace.json"
DEFAULT_TRIGGERS = {"score": 15, "common_weight": 2, "lines": 5000, "escapes": 2, "hours": 24}
DEFAULT_MODES = ("light", "standard", "legacy-refactor")
EXCLUDED_MODES = ("operation", "documentation")  # fast と auto に入れられないモード（書いても無視する）


class PaceError(Exception):
    """宣言が無い・読めない・形が誤っている。"""


class PaceMissing(PaceError):
    """宣言が無い。"""


def glob_re(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path: str, patterns) -> bool:
    return any(glob_re(p).match(path) for p in patterns)


def _read_pace_decl(root) -> tuple[Path, object]:
    """宣言のパスと未検証の JSON の値を返す。無い・読めなければ PaceError。形の検証は呼び手が行う。"""
    path = Path(root) / ".ndf" / DECL_NAME
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PaceMissing(f"進め方の宣言が無い: {path}")
    except (OSError, ValueError) as e:
        raise PaceError(f"進め方の宣言を読めない: {path}: {e}")


def read_pace(root) -> dict:
    """宣言を読み、既定を埋めて返す。無い・読めない・形が誤っていれば PaceError。"""
    path, d = _read_pace_decl(root)
    if not isinstance(d, dict):
        raise PaceError(f"進め方の宣言はオブジェクトで書く: {path}")
    if d.get("version") != 1 or isinstance(d.get("version"), bool):
        raise PaceError(f"進め方の宣言の version は 1 で書く: {path}")
    areas = _default(d, "areas", [])
    if not isinstance(areas, list) or not all(
        isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] and _globs(a.get("paths")) and a["paths"] for a in areas
    ):
        raise PaceError(f"進め方の宣言の areas は name と paths（glob の文字列の配列）を持つ: {path}")
    boundary_paths = _default(d, "boundary_paths", [])
    if not _globs(boundary_paths):
        raise PaceError(f"進め方の宣言の boundary_paths は glob の文字列の配列で書く: {path}")
    given = _default(d, "triggers", {})
    if not isinstance(given, dict):
        raise PaceError(f"進め方の宣言の triggers はオブジェクトで書く: {path}")
    triggers = {**DEFAULT_TRIGGERS, **given}
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in triggers.values()):
        raise PaceError(f"進め方の宣言の triggers は 0 以上の数で書く: {path}")
    fast = _pace_section(d, "fast", path)
    auto = _pace_section(d, "auto", path)
    return {**d, "fast": fast, "auto": auto, "areas": areas, "triggers": triggers, "boundary_paths": list(boundary_paths)}


LOOP_DEFAULTS = {
    "enabled": False,
    "budget": {"laps": None, "usd": None, "hours": None},  # None は無制限。書いた単位だけ効く（#2505）
    "same_failure": 2,
    "max_issues": 3,
}  # #2089 の決定 5・8


def read_loop(root) -> dict:
    """開発ループの節（`loop`。#2089 の決定 7）を読み、既定を埋めて返す。`read_pace` とは別に読み、節の誤りは
    ループだけを断る（`fast` / `auto` は今どおり動く）。宣言が無い・読めない・形が誤っていれば PaceError。"""
    path, d = _read_pace_decl(root)
    sec = _default(d if isinstance(d, dict) else {}, "loop", {})
    if not isinstance(sec, dict):
        raise PaceError(f"進め方の宣言の loop はオブジェクトで書く: {path}")
    budget = _default(sec, "budget", {})
    if not isinstance(budget, dict):
        raise PaceError(f"進め方の宣言の loop.budget はオブジェクトで書く: {path}")
    budget = {**LOOP_DEFAULTS["budget"], **budget}
    if set(budget) != set(LOOP_DEFAULTS["budget"]) or not all(v is None or _is_positive_number(v) for v in budget.values()):
        raise PaceError(f"進め方の宣言の loop.budget は laps・usd・hours を正の数か null で書く: {path}")
    out = {"enabled": sec.get("enabled") is True, "budget": budget}
    for key in ("same_failure", "max_issues"):
        v = _default(sec, key, LOOP_DEFAULTS[key])
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise PaceError(f"進め方の宣言の loop.{key} は 1 以上の整数で書く: {path}")
        out[key] = v
    return out


PARALLEL_DEFAULTS = {"plan": 8, "lane": 3}  # 答え待ちの依頼の閾値の既定（#2707 の利用者の指示の値）


def read_parallel(root) -> dict:
    """並列の計画の節（`parallel.answer_limit`）を読み、`{plan, lane, warnings}` を返す。例外を上げない。

    閾値の誤りで計画を止めないため（#2707 の決定 6）、宣言が無ければ既定で動き、読めない宣言と正の整数でない値は既定へ戻して
    `warnings` に残す。"""
    out, warnings = dict(PARALLEL_DEFAULTS), []
    try:
        path, d = _read_pace_decl(root)
    except PaceMissing:
        return {**out, "warnings": warnings}
    except PaceError as e:
        return {**out, "warnings": [f"{e}。答え待ちの閾値は既定を使う"]}
    sec = _default(d if isinstance(d, dict) else {}, "parallel", {})
    limit = _default(sec, "answer_limit", {}) if isinstance(sec, dict) else None
    if not isinstance(limit, dict):
        return {**out, "warnings": [f"進め方の宣言の parallel.answer_limit はオブジェクトで書く: {path}。既定を使う"]}
    for key, default in PARALLEL_DEFAULTS.items():
        v = limit.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            warnings.append(f"進め方の宣言の parallel.answer_limit.{key} は正の整数で書く（{v!r}）: {path}。既定の {default} を使う")
        else:
            out[key] = v
    return {**out, "warnings": warnings}


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # datetime.weekday() の順
HOURS_RULE = "business-hours"


@dataclass(frozen=True)
class HoursDecl:
    """検査を通った業務時間の宣言（`business_hours`。#2913）。"""

    timezone: str
    days: frozenset
    start: _dt.time
    end: _dt.time


def _hhmm(v) -> _dt.time | None:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", v) if isinstance(v, str) else None
    return _dt.time(int(m.group(1)), int(m.group(2))) if m else None


def parse_hours(sec) -> tuple[HoursDecl | None, str | None]:
    """`business_hours` の値を検査する。(宣言, 誤りの理由)。一部の項目だけを既定へ戻さず、誤りがあれば宣言全体を無いものとする。"""
    if not isinstance(sec, dict):
        return None, "business_hours はオブジェクトで書く"
    tz = sec.get("timezone")
    if not isinstance(tz, str) or not tz:
        return None, "business_hours.timezone が無い。IANA の名前（例: Asia/Tokyo）で書く"
    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None, f"business_hours.timezone を読めない（{tz!r}）。IANA の名前（例: Asia/Tokyo）で書く"
    days = sec.get("days")
    if not isinstance(days, list) or not days or any(d not in DAYS for d in days) or len(set(days)) != len(days):
        return None, f"business_hours.days は mon〜sun の重ならない 1 個以上の配列で書く（{days!r}）"
    start, end = _hhmm(sec.get("start")), _hhmm(sec.get("end"))
    if start is None or end is None:
        return None, f"business_hours.start / end は HH:MM（00:00〜23:59）で書く（{sec.get('start')!r} / {sec.get('end')!r}）"
    if start >= end:
        return None, "business_hours.start は end より前にする（日をまたぐ宣言は受けない）"
    return HoursDecl(tz, frozenset(DAYS.index(d) for d in days), start, end), None


def read_hours(root) -> tuple[HoursDecl | None, list[str]]:
    """業務時間の宣言を読む。(宣言, 警告)。宣言が無い・読めない・不正なら宣言は None（I3）。例外を上げない。"""
    try:
        path, d = _read_pace_decl(root)
    except PaceError:
        return None, []  # 宣言のファイルの誤りは read_pace / read_parallel が知らせる
    sec = d.get("business_hours") if isinstance(d, dict) else None
    if sec is None:
        return None, []
    decl, why = parse_hours(sec)
    return decl, ([f"進め方の宣言の {why}: {path}。業務時間の宣言なしとして扱う"] if why else [])


def hours_items(root) -> list[dict]:
    """`loop start`・`parallel.py check`・`start` の警告の行。宣言が不正なときだけ 1 件。"""
    return [{"level": "warning", "rule": HOURS_RULE, "at": "business_hours", "message": w} for w in read_hours(root)[1]]


def hours_state(decl: HoursDecl | None, now: _dt.datetime) -> dict:
    """`now`（時刻帯つき）が業務時間内か。宣言の時刻帯だけで決め、地方時を読まない（I4）。宣言が無ければ業務時間内（I3）。
    `next_start` は業務時間外のときの次の業務時間の始まり（UTC の ISO 8601）。"""
    if decl is None:
        return {"declared": False, "inside": True, "timezone": None, "next_start": None}
    zone = zoneinfo.ZoneInfo(decl.timezone)
    local = now.astimezone(zone)
    inside = local.weekday() in decl.days and decl.start <= local.time().replace(tzinfo=None) < decl.end
    nxt = None
    if not inside:
        for d in range(8):
            day = local.date() + _dt.timedelta(days=d)
            at = _dt.datetime.combine(day, decl.start, tzinfo=zone)
            if day.weekday() in decl.days and at > local:
                nxt = at.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                break
    return {"declared": True, "inside": inside, "timezone": decl.timezone, "next_start": nxt}


def hours_now(root, now: _dt.datetime) -> dict:
    """宣言を読んで `now` を判定する（ループの 1 回の境と `parallel.py wait` が呼ぶ）。"""
    return hours_state(read_hours(root)[0], now)


def over_budget(used: float, limit: float | None) -> bool:
    """予算の 1 単位に当たったか。`limit` が None（無制限）なら当たらない。ループの 1 回の境の判定と再開の判定が同じこれを読む。"""
    return limit is not None and used >= limit


def _is_positive_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _pace_section(d: dict, name: str, path: Path) -> dict:
    """進め方の節（fast / auto）を読み、既定を埋める。節が無ければ不許可（enabled が偽）。"""
    sec = _default(d, name, {})
    if not isinstance(sec, dict):
        raise PaceError(f"進め方の宣言の {name} はオブジェクトで書く: {path}")
    modes = _default(sec, "modes", list(DEFAULT_MODES))
    if not _globs(modes):
        raise PaceError(f"進め方の宣言の {name}.modes は文字列の配列で書く: {path}")
    modes = modes or list(DEFAULT_MODES)  # 空の配列は既定の 3 つ（references/pace.md の表）
    return {
        "enabled": sec.get("enabled") is True,
        "verify": str(sec.get("verify") or ""),
        "modes": [m for m in modes if m not in EXCLUDED_MODES],
    }


def _default(d: dict, key: str, default):
    """キーが無いか null のときだけ既定を使う（偽になる誤った値を既定へ置き換えて検証を通さない）。"""
    value = d.get(key)
    return default if value is None else value


def _globs(value) -> bool:
    """空でない文字列だけの配列か（文字列 1 つは 1 文字ずつのパターンに化けるので受けない）。"""
    return isinstance(value, list) and all(isinstance(p, str) and p for p in value)
