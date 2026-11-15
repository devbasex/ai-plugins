"""設計に名前の出るテストを拾う判断（#1241 の確認 (a)・#2118。純粋な関数。git を呼ばない）。

設計文書の `## 決定の記録`・`## テスト設計`・`## 構成要素` にバッククォートで書かれた、テストファイルのパス・
`<パス>::<名前>`・`## テスト設計` の `test` で始まる識別子を拾う。囲みと引用の中は拾わない。

止まりにしないもの（#2118）:

- 「採らなかった案」の中の名前。採らなかった案は作らないので、HEAD に無くてよい。`採らなかった案` で始まる行から
  次の見出しまでを採らなかった案の範囲とみなす
- 設計が改名を定義した前の名前。設計文書のどこかに `` `前` → `後` `` の形で書いた改名を集め（`renames`）、HEAD に
  無い名前は改名を当てた名前（`variants`。ファイル名の `-` と `_` の違いも当てる）で探し直す。見つかれば renamed（`Verdict.RENAMED`）
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum
from pathlib import PurePosixPath
from typing import NamedTuple, Optional

from ndf.core.plan import inputs_decide as inputs

NAME_SECTIONS = ("決定の記録", "テスト設計", "構成要素")
BARE_SECTION = "テスト設計"
REJECTED = "採らなかった案"
CODE_SPAN = re.compile(r"`([^`\n]+)`")
RENAME = re.compile(r"`([^`\n]+)`\s*(?:→|->)\s*`([^`\n]+)`")
SHAPE_CHARS = ("<", "{", "*", "…")
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
TEST_FILE = re.compile(r"^(?:test_.+|.+_test\.[^.]+|.+\.test\.[^.]+|.+\.spec\.[^.]+|.+Tests?\.[^.]+)$")
BARE_TEST = re.compile(r"^(?:test|Test|TEST)(?:_\w*[A-Za-z0-9]|[A-Z0-9]\w*)$")
PATH_LIKE = re.compile(r"^[\w./@+-]+$")
ARGS = re.compile(r"\[.*\]$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+|\*\*")


class Verdict(str, Enum):
    """名前 1 つの判定。"""

    PRESENT = "present"
    MISSING = "missing"
    RENAMED = "renamed"
    UNVERIFIED = "unverified"


class Kind(str, Enum):
    """拾った字面の種類。SHAPE は形の説明（`<名前>` など）で、判定しない。"""

    FILE = "test_file"
    FUNCTION = "test_function"
    SHAPE = "skipped"


class Span(NamedTuple):
    """拾った名前 1 つの形。`path` はファイルのパス（パスの無い識別子なら None）、`names` は確かめる名前。"""

    kind: Kind
    path: Optional[str]
    names: tuple[str, ...]


class Found(NamedTuple):
    """設計文書から拾った名前 1 つ。"""

    span: str
    doc: str
    line: int
    section: str
    shape: Span


def looks_like_test_file(s: str) -> bool:
    """パスの区切りのどれかがテストの保存先か、ファイル名がテストの命名のものか。"""
    if not PATH_LIKE.match(s) or ("/" not in s and "." not in s):
        return False
    parts = PurePosixPath(s.strip("/")).parts
    return bool(parts) and (any(p in TEST_DIRS for p in parts) or bool(TEST_FILE.match(parts[-1])))


def _classify_shape(s: str) -> Optional[Span]:
    """形の説明（`<名前>` など）の字面。テストの名前の形なら kind が skipped、対象外なら None。"""
    probe = re.sub(r"[<>{}*…]+", "x", s)
    return Span(Kind.SHAPE, None, ()) if ("::" in s or looks_like_test_file(probe) or BARE_TEST.match(probe)) else None


def _classify_function(s: str) -> Optional[Span]:
    """`<パス>::<名前>` の字面。パスがテストのファイルで名前がそろえば関数、対象外なら None。"""
    path, *names = s.split("::")
    names = [ARGS.sub("", n).strip() for n in names]
    if looks_like_test_file(path) and names and all(names):
        return Span(Kind.FUNCTION, path.removeprefix("./"), tuple(names))
    return None


def classify(span: str, section: str) -> Optional[Span]:
    """字面 → 形。形の説明（`<名前>` など）なら kind が skipped、対象外なら None。"""
    s = span.strip()
    if any(c in s for c in SHAPE_CHARS):
        return _classify_shape(s)
    if "::" in s:
        return _classify_function(s)
    if looks_like_test_file(s):
        return Span(Kind.FILE, s.removeprefix("./").rstrip("/"), ())
    if section == BARE_SECTION and BARE_TEST.match(s):
        return Span(Kind.FUNCTION, None, (s,))
    return None


def _outside_fences(text: str) -> list[tuple[int, str]]:
    """囲みの外の行を `(行番号 1 始まり, 行)` で返す。囲みは字下げ 3 つまでを許す（`_FENCE`）。"""
    return [(i + 1, line) for i, line in inputs.outside_fences(text.splitlines(), _FENCE)]


def _next_section(heading: re.Match[str], section: Optional[str]) -> Optional[str]:
    """見出しの後のセクション。`##` の見出しなら拾うセクションかを決め直し、他の深さでは今のセクションのまま。"""
    if len(heading.group(1)) == 2:
        return heading.group(2) if heading.group(2) in NAME_SECTIONS else None
    return section


def _starts_rejected(line: str) -> bool:
    """採らなかった案の範囲を始める行か。"""
    return _LIST_MARK.sub("", line).lstrip().startswith(REJECTED)


def _is_skipped(line: str, section: Optional[str], rejected: bool) -> bool:
    """拾わない行か（拾うセクションの外・採らなかった案の中・引用）。"""
    return section is None or rejected or line.lstrip().startswith(">")


def target_lines(text: str) -> list[tuple[str, int, str]]:
    """拾うセクションの、囲み・引用・採らなかった案の外の行を `(セクション, 行番号 1 始まり, 行)` で返す。"""
    out: list[tuple[str, int, str]] = []
    section: Optional[str] = None
    rejected = False
    for lineno, line in _outside_fences(text):
        h = _HEADING.match(line)
        if h:
            rejected = False
            section = _next_section(h, section)
            continue
        rejected = rejected or _starts_rejected(line)
        if _is_skipped(line, section, rejected):
            continue
        assert section is not None
        out.append((section, lineno, line))
    return out


def extract(doc: str, text: str, seen: set[str]) -> tuple[list[Found], int]:
    """設計文書 1 つから名前を拾う。`seen` に入った字面は飛ばす（最初の 1 か所だけを出す）。形の説明の数も返す。"""
    items: list[Found] = []
    skipped = 0
    for section, lineno, line in target_lines(text):
        for span in CODE_SPAN.findall(line):
            if span in seen:
                continue
            shape = classify(span, section)
            if shape is None:
                continue
            seen.add(span)
            if shape.kind == Kind.SHAPE:
                skipped += 1
                continue
            items.append(Found(span, doc, lineno, section, shape))
    return items, skipped


def renames(texts: Iterable[str]) -> list[tuple[str, str]]:
    """設計文書が定義した改名（`` `前` → `後` ``）の組。囲みの中は読まない。"""
    out: list[tuple[str, str]] = []
    for text in texts:
        for _, line in _outside_fences(text):
            out += [(a.strip(), b.strip()) for a, b in RENAME.findall(line) if a.strip() and a.strip() != b.strip()]
    return list(dict.fromkeys(out))


def variants(span: str, pairs: Iterable[tuple[str, str]]) -> list[str]:
    """改名を当てた名前の候補。ファイル名の `-` と `_` の違いも当てる。元の名前と同じものは返さない。"""
    out: list[str] = []
    for old, new in pairs:
        for a, b in ((old, new), (old.replace("-", "_"), new.replace("-", "_"))):
            if a in span:
                out.append(span.replace(a, b))
    return [v for v in dict.fromkeys(out) if v != span]


def outcome(results: Iterable[str]) -> tuple[str, str]:
    """判定の並びから (停止理由の種類か ok, 要約)。無いものは check_failed、確かめられないものは incomplete。"""
    got = list(results)
    missing, unverified = got.count(Verdict.MISSING), got.count(Verdict.UNVERIFIED)
    if missing:
        return "check_failed", f"設計に名前の出るテストのうち {missing} 件が HEAD に無い"
    if unverified:
        return "incomplete", f"設計に名前の出るテストのうち {unverified} 件を確かめられなかった"
    renamed = got.count(Verdict.RENAMED)
    note = f"（うち改名の後の名前で見つけたもの {renamed} 件）" if renamed else ""
    return "ok", f"設計に名前の出るテスト {len(got)} 件がすべて HEAD にある{note}"
