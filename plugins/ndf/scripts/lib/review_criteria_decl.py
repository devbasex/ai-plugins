"""review_criteria_decl.py: レビューの宣言（`.ndf/review.json`）のプロファイルの形の検査（#1910・#1942）。

`review_criteria.py` が読む。ここは形だけを見て、既定の基準・節の組み立て・プロファイルの選択は持たない。標準ライブラリだけで書く。
"""

from __future__ import annotations

import re
from typing import NamedTuple

FOCUS_CRITERION = 3
# 外せない基準（#1910 の決定 1）。プロファイルがあっても番号 2 のまま残り、プロファイルは 2 と 3 を使えない
FIXED_CRITERION = 2
INNER = "{inner}"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NUMBER_RE = re.compile(r"^[1-9][0-9]*$")
PROFILE_KEYS = ("criteria", "not_written", "waive_kinds", "reply", "categories", "replace_auto_categories")


class Profile(NamedTuple):
    """レビューのプロファイル（#1910）。書かなかった鍵は `None` で既定を使う。`none` と `unreadable` では鍵がすべて既定。"""

    status: str  # declared | none | unreadable
    criteria: dict | None = None  # 番号（int）→ 文
    not_written: tuple[str, ...] | None = None
    waive_kinds: dict | None = None  # 種類 → 名前
    reply: str | None = None
    categories: dict | None = None  # 名前 → (glob の組, 観点の文)
    replace_auto_categories: bool = False
    error: str | None = None
    name: str | None = None  # version 3 で選んだ名前（選ばなかったときは空文字）。version 1・2 は None
    paths: tuple[str, ...] | None = None  # version 3 の候補の glob。None はどの PR にも当たる

    def record(self) -> dict:
        """状態ファイル・振り分けの JSON へ写す形（宣言の `profile` と同じ形。書いた鍵だけ。`name` と `paths` は写さない）。"""
        c = self.categories
        out = {
            "criteria": None if self.criteria is None else {str(n): t for n, t in self.criteria.items()},
            "not_written": None if self.not_written is None else list(self.not_written),
            "waive_kinds": None if self.waive_kinds is None else dict(self.waive_kinds),
            "reply": self.reply,
            "categories": None if c is None else {n: {"paths": list(g), "instructions": i} for n, (g, i) in c.items()},
            "replace_auto_categories": True if self.replace_auto_categories else None,
        }
        return {k: v for k, v in out.items() if v is not None}


NO_PROFILE = Profile("none")


def _text(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _nonempty_globs(x) -> bool:
    """空でない glob の配列か（`categories` と `profiles` の `paths`。`.ndf/pace.json` の `areas` と同じ規則）。"""
    return isinstance(x, list) and bool(x) and all(_text(p) for p in x)


def _parse_criteria(v):
    if not isinstance(v, dict) or not v:
        return None, "criteria は番号の文字列 → 文のオブジェクトで、1 件以上書く"
    bad = [k for k in v if not (isinstance(k, str) and _NUMBER_RE.match(k)) or not _text(v[k])]
    if bad:
        return None, f"criteria の鍵は 1 以上の整数、値は空でない文で書く: {', '.join(map(str, bad))}"
    taken = sorted({int(k) for k in v} & {FIXED_CRITERION, FOCUS_CRITERION})
    if taken:
        return None, f"criteria は番号 {FIXED_CRITERION}（外せない基準）と {FOCUS_CRITERION}（重点）を使えない: {taken}"
    return {int(k): v[k].strip() for k in sorted(v, key=int)}, None


def _parse_not_written(v):
    if not isinstance(v, list) or not all(_text(x) for x in v):
        return None, "not_written は空でない文字列の配列で書く"
    return tuple(x.strip() for x in v), None


def _parse_waive_kinds(v):
    if not isinstance(v, dict) or not v or not all(isinstance(k, str) and _NAME_RE.match(k) and _text(n) for k, n in v.items()):
        return None, "waive_kinds は種類（英小文字・数字・_）→ 空でない名前のオブジェクトで、1 件以上書く"
    return {k: n.strip() for k, n in v.items()}, None


def _parse_reply(v):
    if not isinstance(v, str) or v.count(INNER) != 1:
        return None, f"reply は {INNER} をちょうど 1 回含む文字列で書く"
    return v, None


def _parse_categories(v):
    if not isinstance(v, dict):
        return None, "categories は名前 → {paths, instructions} のオブジェクトで書く"
    out = {}
    for name, c in v.items():
        paths = c.get("paths") if isinstance(c, dict) else None
        if not (isinstance(name, str) and _NAME_RE.match(name)) or not _nonempty_globs(paths) or not _text(c.get("instructions")):
            return None, f"categories の {name!r} は英小文字の名前で、paths（空でない glob の配列）と instructions（空でない文）を持つ"
        out[name] = (tuple(p.strip() for p in paths), c["instructions"].strip())
    return out, None


def _parse_replace(v):
    if not isinstance(v, bool):
        return None, "replace_auto_categories は真偽値で書く"
    return v, None


_PARSERS = dict(
    zip(PROFILE_KEYS, (_parse_criteria, _parse_not_written, _parse_waive_kinds, _parse_reply, _parse_categories, _parse_replace))
)


def _parse_profile(raw) -> Profile:
    """`profile` の値を検査する。どれか 1 つでも形が違えば丸ごと `unreadable`（I4）。"""
    if raw is None or raw == {}:
        return NO_PROFILE
    if not isinstance(raw, dict):
        return Profile("unreadable", error="profile はオブジェクトで書く")
    unknown = [k for k in raw if k not in _PARSERS]
    if unknown:
        return Profile("unreadable", error=f"profile に知らない鍵がある: {', '.join(map(str, unknown))}（{'/'.join(PROFILE_KEYS)}）")
    values = {}
    for key, value in raw.items():
        parsed, err = _PARSERS[key](value)
        if err:
            return Profile("unreadable", error=err)
        values[key] = parsed
    return Profile("declared", **values)


def _parse_profiles(raw) -> tuple[tuple[Profile, ...] | None, str | None]:
    """`version: 3` の `profiles` を検査する。1 つでも形が違えば (None, 理由)（決定 4）。"""
    if raw is not None and not isinstance(raw, list):
        return None, "profiles は配列で書く"
    out: list[Profile] = []
    for i, item in enumerate(raw or []):
        obj = isinstance(item, dict)
        name = item.get("name") if obj else None
        profile = _parse_profile({k: v for k, v in item.items() if k not in ("name", "paths")} if obj else None)
        problems = (
            (not obj or not (isinstance(name, str) and _NAME_RE.match(name)), "オブジェクトで書き、name は英小文字・数字・_ の名前にする"),
            (any(p.name == name for p in out), "name が重複している"),
            (obj and "paths" in item and not _nonempty_globs(item["paths"]), "paths は空でない glob の配列で書く"),
            (bool(out) and out[-1].paths is None, "paths を持たないプロファイルは高々 1 つで、末尾に置く"),
            (profile.status == "unreadable", profile.error),
        )
        err = next((msg for bad, msg in problems if bad), None)
        if err:
            return None, f"profiles[{i}]（{name if isinstance(name, str) else '?'}）: {err}"
        out.append(profile._replace(name=name, paths=tuple(x.strip() for x in item["paths"]) if "paths" in item else None))
    return tuple(out), None
