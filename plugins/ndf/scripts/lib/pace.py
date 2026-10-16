"""pace.py: 進め方の宣言（`.ndf/pace.json`）の読み取りとパスの照合（#1078）。

check-trigger.py・mvv-gate.py・supervise.py が同じ規則で読む。標準ライブラリだけで書く。

    {"version": 1,
     "fast": {"enabled": true, "modes": [...], "verify": "<導入の確認のコマンド>"},
     "auto": {"enabled": true, "modes": [...], "verify": "<導入の確認のコマンド>"},
     "areas": [{"name": "...", "common": true, "paths": ["<glob>", ...]}, ...],
     "boundary_paths": ["<glob>", ...],
     "triggers": {"score": 15, "common_weight": 2, "lines": 5000, "escapes": 2, "hours": 24}}

glob の `**` は区切りをまたぎ、`*` と `?` はまたがない。`**/` は 0 階層でもよい。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DECL_NAME = "pace.json"
DEFAULT_TRIGGERS = {"score": 15, "common_weight": 2, "lines": 5000, "escapes": 2, "hours": 24}
DEFAULT_MODES = ("light", "standard", "legacy-refactor")
EXCLUDED_MODES = ("operation", "documentation")  # fast と auto に入れられないモード（書いても無視する）


class PaceError(Exception):
    """宣言が無い・読めない・形が誤っている。"""


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


def read_pace(root) -> dict:
    """宣言を読み、既定を埋めて返す。無い・読めない・形が誤っていれば PaceError。"""
    path = Path(root) / ".ndf" / DECL_NAME
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PaceError(f"進め方の宣言が無い: {path}")
    except (OSError, ValueError) as e:
        raise PaceError(f"進め方の宣言を読めない: {path}: {e}")
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
