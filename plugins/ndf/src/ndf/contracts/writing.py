"""文書の書き方・直し方の基準（`contracts/data/writing-criteria.md`）の本文を読む（#4285 の決定 2・6）。

AI への指示に入れる本文の出所はここだけで、cross-review・worker・読み手役の指示が使う。
"""

from __future__ import annotations

from ndf import contracts

CRITERIA = "data/writing-criteria.md"
# 読み手の決め方のセクションの見出し
READERS_HEADING = "## 1. 読み手を想定する"
# 通じない文の 4 つの型と 2 つの区分のセクションの見出し
TYPES_HEADING = "## 3. 読み手に通じない文の 4 つの型"
_SECTION = "## "


class CriteriaBroken(contracts.ContractError):
    """基準の本文が読めない・見出しが無い。"""


@contracts.cached_reader
def criteria() -> str:
    """基準の全文。読めなければ `CriteriaBroken`。"""
    with contracts.raised_as(CriteriaBroken):
        return contracts.load_text(CRITERIA)


def readers_section() -> str:
    """読み手の決め方のセクション。見出しが無ければ `CriteriaBroken`。"""
    return _section(READERS_HEADING)


def types_section() -> str:
    """通じない文の 4 つの型と 2 つの区分のセクション。見出しが無ければ `CriteriaBroken`。"""
    return _section(TYPES_HEADING)


def _section(heading: str) -> str:
    """見出し `heading` から次の `## ` の見出しの前まで。見出しが無ければ `CriteriaBroken`。"""
    lines = criteria().splitlines()
    if heading not in lines:
        raise CriteriaBroken(f"文書の基準 {CRITERIA} に見出し「{heading}」が無い")
    start = lines.index(heading)
    rest = lines[start + 1 :]
    end = next((i for i, line in enumerate(rest) if line.startswith(_SECTION)), len(rest))
    return "\n".join([heading, *rest[:end]]).strip()
