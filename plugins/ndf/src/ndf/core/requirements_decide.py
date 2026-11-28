"""要求仕様書の型と、読んだバイト列の解析、受け入れ条件のハッシュ値の計算（外部の I/O を持たない。#4735 の決定 2・決定 10）。

条件のハッシュ値は条件の文の SHA-256 の先頭 `DIGEST_CHARS` 桁で、チェックボックスの記号と先頭の番号は文に数えない。
条件の文は字下げした継続の行（`操作:`・`結果:` など）を含み、各行の前後の空白と空行は数えない。ただしコードブロックの中は、
コード本体の相対的な字下げと空行を数える。HTML コメントは数えない。
並べ替えとチェックの付け外しでは変わらず、文を直すと変わる。条件一覧のハッシュ値は、条件のハッシュ値を番号の順に
つないだもののハッシュ値である。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import NamedTuple, Optional

from ndf.core import issue_docs
from ndf.core.plan import inputs_decide

# 条件と条件一覧のハッシュ値・実行を指す値・確認対象を指す値の桁数。ほかのモジュールはここを読む
DIGEST_CHARS = 12
# 行頭のチェックボックスの記号（`- [ ] `）。条件の本文の `[x]`・`[ ]` は記号でない
_CHECKBOX = re.compile(r"^\s*-\s+\[[ xX]\](?=\s|$)")
# 条件の先頭の番号（`1. `）。後ろに空白があるものだけ（`11.0` は文の一部）
_NUMBER = re.compile(r"^\d+\.(?:\s+|$)")
# 要求仕様書を持たないモード
_WITHOUT_REQUIREMENTS = "legacy-refactor"


class Criterion(NamedTuple):
    """受け入れ条件 1 つとそのハッシュ値。`number` は上から 1 始まり、`text` は記号と番号を除いた文。"""

    number: int
    text: str
    digest: str


class Doc(NamedTuple):
    """読んだ要求仕様書 1 本。`source` は読み先、`commit` は読んだコミット（作業場所なら None）。"""

    path: str
    source: str
    commit: Optional[str]
    issues: tuple[int, ...]
    body: str
    criteria: tuple[Criterion, ...]
    criteria_digest: str


class Lookup(NamedTuple):
    """番号ごとの要求仕様書と、見つからない番号。"""

    docs: dict[int, Doc]
    missing: tuple[int, ...]


class RequirementsError(Exception):
    """要求仕様書を読めない理由。`cause`・`detail`・`gaps` は結果 JSON の同じ名前の欄へ渡す。"""

    def __init__(self, cause: str, detail: str, gaps: Sequence[dict[str, str]] = ()) -> None:
        super().__init__(detail)
        self.cause = cause
        self.detail = detail
        self.gaps = list(gaps)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def _sentence(text: str) -> str:
    """チェックボックスの記号より後ろの文を、条件の文にする。

    HTML コメントを除き、各行の前後の空白と空行を落とし、先頭の番号を除く。コードブロックの中の行は、
    リストに属する字下げだけを除いて残す。
    """
    first, *rest = text.split("\n")
    # 先頭の行は記号を除いてあるので、同じ字下げになるリスト項目の行を代わりに置いて、続きの行のコードブロックを読む
    code = inputs_decide.code_lines(["- x", *rest])
    marker = "- x "
    bare = inputs_decide.uncommented([marker + first, *rest])
    head = (bare[0] or "")[len(marker) :].strip()
    lines = [head] if head else []
    for i, (line, cut) in enumerate(zip(rest, bare[1:]), 1):
        if cut is None:
            continue
        if i in code and cut == line:
            lines.append(code[i])
        elif cut.strip():
            lines.append(cut.strip())
    return _NUMBER.sub("", "\n".join(lines).rstrip("\n"), count=1)


def criterion_digest(block: str) -> str:
    """条件のハッシュ値。`block` は要求仕様書に書かれたままの、`- [ ] `・`- [x] `・`- [X] ` から始まる条件の行（続きの行を含めてよい）。

    除く記号は行頭の 1 つだけ。記号より後ろの文（`Criterion.text`）は受け取らない（文の先頭の `- [ ]` と区別できない）。
    行頭に記号が無ければ `ValueError`。
    """
    found = _CHECKBOX.match(block)
    if not found:
        raise ValueError(f"チェックボックスの行でない: {block!r}")
    return _digest(_sentence(block[found.end() :]))


def criteria_of(body: str) -> tuple[Criterion, ...]:
    """`## 受け入れ条件` のチェックボックスを上から番号を振って読む。同じ文の条件が 2 つあれば `RequirementsError`。"""
    sentences = [_sentence(c) for c in inputs_decide.criteria_blocks(body)]
    found = tuple(Criterion(n, text, _digest(text)) for n, text in enumerate(sentences, 1))
    numbers: dict[str, list[int]] = {}
    for c in found:
        numbers.setdefault(c.digest, []).append(c.number)
    repeated = ["・".join(str(n) for n in same) for same in numbers.values() if len(same) > 1]
    if repeated:
        raise RequirementsError("check_failed", f"受け入れ条件の文が重なる（条件の番号 {'、'.join(repeated)}）")
    return found


def criteria_list_digest(criteria: Sequence[Criterion]) -> str:
    """条件一覧のハッシュ値。条件を足す・消す・並べ替える・直すと変わる。"""
    return _digest("\n".join(c.digest for c in criteria))


def parse_doc(path: str, raw: bytes, *, source: str, commit: Optional[str], repo: str) -> Doc:
    """バイト列を UTF-8 で読み、「元の Issue」の行と受け入れ条件を確かめて `Doc` にする。

    ファイル名の番号すべての「元の Issue」の行が要る。欠け・UTF-8 でない中身は `RequirementsError("check_failed")`。
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequirementsError("check_failed", f"{path} を UTF-8 で読めない: {exc}") from None
    issues = tuple(sorted(issue_docs.requirements_issues(path)))
    named = issue_docs.origin_issues(text, repo)
    lacking = [n for n in issues if n not in named]
    if lacking:
        reason = f"{path} の最初の ## より前に「元の Issue: https://github.com/{repo}/issues/<番号>」の行が無い"
        gaps = [{"kind": "origin_issue", "name": f"#{n}", "reason": reason} for n in lacking]
        raise RequirementsError("check_failed", f"{reason}（#{'・#'.join(str(n) for n in lacking)}）", gaps)
    criteria = criteria_of(text)
    return Doc(path, source, commit, issues, text, criteria, criteria_list_digest(criteria))


def needs_requirements(mode: str) -> bool:
    """そのモードが要求仕様書を持つか。偽は `legacy-refactor` だけで、空の文字列は真。"""
    return mode != _WITHOUT_REQUIREMENTS
