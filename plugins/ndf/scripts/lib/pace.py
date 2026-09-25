"""pace.py: 進め方の宣言（`.ndf/pace.json`）の読み取りとパスの照合（#1078）。

check-trigger.py・mvv-gate.py・supervise.py が同じ規則で読む。標準ライブラリだけで書く。

    {"version": 1,
     "fast": {"enabled": true, "modes": [...], "verify": "<導入の確認のコマンド>"},
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
EXCLUDED_MODES = ("operation", "documentation")  # fast に入れられないモード（書いても無視する）


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
    areas = d.get("areas") or []
    if not isinstance(areas, list) or not all(
            isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]
            and isinstance(a.get("paths"), list) and a["paths"] for a in areas):
        raise PaceError(f"進め方の宣言の areas は name と paths を持つ: {path}")
    triggers = {**DEFAULT_TRIGGERS, **(d.get("triggers") or {})}
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in triggers.values()):
        raise PaceError(f"進め方の宣言の triggers は 0 以上の数で書く: {path}")
    fast = d.get("fast") or {}
    if not isinstance(fast, dict):
        raise PaceError(f"進め方の宣言の fast はオブジェクトで書く: {path}")
    modes = fast.get("modes") or list(DEFAULT_MODES)
    fast = {"enabled": fast.get("enabled") is True, "verify": str(fast.get("verify") or ""),
            "modes": [m for m in modes if m not in EXCLUDED_MODES]}
    return {**d, "fast": fast, "areas": areas, "triggers": triggers,
            "boundary_paths": list(d.get("boundary_paths") or [])}
