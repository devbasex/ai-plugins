"""2 ラウンド目以降の線引きと起票の判断（#3780。純粋な関数）。

指摘を、修正へ進めるものと直さずに残すもの（指摘の行の `parked`）に分ける。行へ書くのは `journal.FindingJournal.sort` である。
残した指摘のうち起票するもの、起票の題と本文、同じ指摘を扱う Issue の見分け、起票の記録の形もここが持つ。起票は `leftover` が行う。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import Any, NamedTuple, Optional

from markdown_it import MarkdownIt

from ndf import identity as ndf_identity
from ndf.domains.crossreview import criteria_decide
from ndf.domains.records import mvv_decide

# 直さずに残す指摘の行の欄。修正担当へ渡さず、次のラウンドを始める理由にも数えない
PARKED = "parked"
# 指摘の行番号が、その位置の基準のコミットの PR の差分に載らないことの記録。行番号は起票の場所に使うので行に残し、投稿の位置
# （`location`）だけをファイルにする
OFF_DIFF = "off_diff"
# 起票の題にする本文の先頭の字数
TITLE_CHARS = 80
_ENTRY = ndf_identity.FIXED_WORDS["entrypoint"]
# 起票の記録のコメントの先頭の目印と、記録の中身を埋める最後の行の前
RECORD_MARK = f"<!-- {_ENTRY}:cross-review-leftover-record -->"
_ENTRIES_MARK = f"<!-- {_ENTRY}:cross-review-leftover-entries "
# 本文の先頭の `<名前>:<行>`
_LOCATION = re.compile(r"^\s*`?([\w./-]+):(\d+)")


class Touched(NamedTuple):
    """線引きの起点の後に変わったファイルと、足した行・書き換えた行の番号（行を消しただけの箇所は前後の行）。`commentable` は
    今の PR の差分に載る head の側の行の番号（行コメントを付けられる行）。"""

    files: frozenset[str] = frozenset()
    lines: Mapping[str, frozenset[int]] = {}
    commentable: Mapping[str, frozenset[int]] = {}


def normalized(text: str) -> str:
    """空白をまとめ、大文字小文字をそろえた本文の全体。"""
    return " ".join(text.split()).casefold()


def finding_kinds(row: Mapping[str, Any]) -> frozenset[str]:
    """指摘の種類（行の `protect` と、判定役の確認の `tracks` の和）。"""
    check = row.get(criteria_decide.CHECK)
    found: list[str] = []
    for kinds in (row.get(criteria_decide.PROTECT), check.get("tracks") if isinstance(check, Mapping) else None):
        if isinstance(kinds, (list, tuple)):
            found += kinds
    return frozenset(found)


def plain_path(path: str) -> str:
    """git の一覧と同じ形のパス（前後の空白と先頭の `./` を除く）。"""
    return path.strip().removeprefix("./")


def _path(row: Mapping[str, Any]) -> str | None:
    """指摘の `path`（`plain_path` の形）。無ければ None。"""
    path = row.get("path")
    return plain_path(path) if isinstance(path, str) else None


def location(finding: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    """指摘を投稿する位置のファイルのパスと行（分からなければ None）。相手役が返した `path`・`line` を先に使う。`OFF_DIFF` を
    持つ指摘は行を返さない。"""
    off = bool(finding.get(OFF_DIFF))
    given = finding.get("path")
    if isinstance(given, str) and given.strip():
        line = finding.get("line")
        return given.strip(), line if not off and type(line) is int and line > 0 else None
    section = finding.get("section")
    path = section.split("#", 1)[0] if isinstance(section, str) and section else None
    if not path:
        return None, None
    text = finding.get("text")
    found = _LOCATION.match(text) if isinstance(text, str) else None
    if off or found is None:
        return path, None
    named = found.group(1)
    same = named == path or path.endswith("/" + named)
    return path, int(found.group(2)) if same else None


def on_touched_line(row: Mapping[str, Any], touched: Touched) -> bool:
    """指摘の位置が、変えた行を指すか。"""
    path = _path(row)
    return path in touched.lines and row.get("line") in touched.lines[path]


def on_commentable_line(row: Mapping[str, Any], touched: Touched) -> bool:
    """指摘の位置が、PR の差分に載る行（行コメントを付けられる行）を指すか。"""
    path = _path(row)
    return path in touched.commentable and row.get("line") in touched.commentable[path]


def proceeds(row: Mapping[str, Any], touched: Touched, *, returned: bool = False, unconfirmed: bool = False) -> bool:
    """指摘の行 `row` を直さずに残さないか（真なら `parked` を付けない）。`returned` は直した後にもう一度出た指摘、
    `unconfirmed` は判定役へ渡して有効な回答の無かった指摘。minor・nit も真だが、重要度で修正担当へは渡らない。

    必ず人の承認が要る操作の指摘を先頭で通すので、ほかの条件や前の `parked` によらず、人の判断を待つ。"""
    if row.get(mvv_decide.REVIEW_REDLINE) or row.get("by") == mvv_decide.BY_NDF or row.get("round") == 0 or returned:
        return True
    # 残すのは収束の妨げになる重さのうち最も軽いもの（major）だけ
    if criteria_decide.severity_of(row.get("severity")) != criteria_decide.at_least_blocking(None):
        return True
    path, line = _path(row), row.get("line")
    if on_touched_line(row, touched):
        return True
    if finding_kinds(row) & set(criteria_decide.protect_kinds()):
        return True
    if line is None and (path is None or path in touched.files):
        return True
    return unconfirmed


# --- 起票（#3780） -----------------------------------------------------------------------


class Filed(StrEnum):
    """起票の結果を書く指摘の行の欄。"""

    URL = "issue"
    ERROR = "issue_error"


class Listed(NamedTuple):
    """検索か一覧で読んだ Issue（`is_pull` は Pull Request。`author_id` は作者のアカウント ID で、無ければ 0）。"""

    url: str
    body: str
    author: str
    association: str
    created_at: str
    is_pull: bool
    author_id: int = 0


def files_issue(row: Mapping[str, Any]) -> bool:
    """重い指摘（critical・major）が起票してよい種類を持つか。"""
    if not criteria_decide.blocking(criteria_decide.severity_of(row.get("severity"))):
        return False
    return bool(finding_kinds(row) & set(criteria_decide.filed_kinds()))


def identity(origin_pr: int, text: str) -> str:
    """指摘を見分ける値（元の PR の番号と本文の全体の SHA-256 の先頭 12 字）。"""
    return hashlib.sha256(f"{origin_pr}\n{normalized(text)}".encode()).hexdigest()[:12]


def _issue_mark(ident: str) -> str:
    return f"<!-- {_ENTRY}:cross-review-leftover {ident} -->"


def _lines_of(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _fenced(part: str) -> str:
    """閉じていないコードブロックを閉じる（後ろの欄と PR の URL がコードの中に入らないようにする）。"""
    # 後ろに足した行まで囲みが続くかで見分ける
    end = len(_lines_of(part)) + 1
    for token in MarkdownIt().parse(f"{part}\n\nx"):
        if token.type == "fence" and token.level == 0 and token.map and token.map[1] > end:
            return f"{part}\n{token.markup}"
    return part


def issue_parts(text: str, impact: str, place: str, pr_urls: Sequence[str], ident: str) -> tuple[str, str]:
    """起票の題と本文。`text`・`impact`・`place` は全文に伏せ字を当てた後の値で、ここでは切り出すだけで伏せない
    （伏せる前に切り出すと、途中で切れた秘密が秘密の形に当たらず公開される）。"""
    title = " ".join(text.split())[:TITLE_CHARS]
    parts = [_fenced(text.strip())]
    parts += [f"## {head}\n\n{_fenced(value.strip())}" for head, value in (("起きること", impact), ("場所", place)) if value.strip()]
    parts.append("## Pull Request\n\n" + "\n".join(f"- {url}" for url in pr_urls))
    parts.append(_issue_mark(ident))
    return title, "\n\n".join(parts) + "\n"


def trusted(author_id: int, me: int, account_ids: Collection[int]) -> bool:
    """起票の記録と Issue の作者が、実行しているアカウントか、前に起票に使ったアカウントか。login は手放すと別の
    アカウントが取得できるので、アカウント ID で照らす（ID が無い 0 は当たらない）。ほかの作者は、呼び手が対象リポジトリの
    権限で確かめる（`GitHub.trusts`）。"""
    return author_id > 0 and (author_id == me or author_id in account_ids)


def _prose(text: str) -> str:
    """引用とコードブロックを除いた本文。"""
    lines = _lines_of(text)
    quoted: set[int] = set()
    for token in MarkdownIt().parse("\n".join(lines)):
        if token.type in ("blockquote_open", "fence", "code_block") and token.map:
            quoted.update(range(*token.map))
    return "\n".join(line for n, line in enumerate(lines) if n not in quoted)


def _final_line(body: str) -> str:
    """空でない最後の行。"""
    return next((line.strip() for line in reversed(_lines_of(body)) if line.strip()), "")


def matches_finding(listed: Listed, ident: str, origin_pr: int) -> bool:
    """検索か一覧で読んだ Issue がこの指摘を扱うものか。目印が最後の行で、引用とコードブロックの外に元の PR の URL がある。
    目印を含むだけの Issue は使わない。指摘の本文は比べない（伏せ字の結果は実行する環境で変わる。本文は目印の識別値が表す）。
    URL は伏せ字が変えない末尾の `/pull/<番号>` で見る。"""
    if listed.is_pull or _final_line(listed.body) != _issue_mark(ident):
        return False
    return re.search(rf"/pull/{int(origin_pr)}(?!\d)", _prose(listed.body)) is not None


def names_finding(listed: Listed, ident: str) -> bool:
    """起票の記録にある URL の Issue が、この指摘のものか。起票の後に本文へ書き足されることがあるので、目印の位置は問わない。"""
    return not listed.is_pull and _issue_mark(ident) in listed.body


def record_body(entries: Mapping[str, Mapping[str, Any]]) -> str:
    """起票の記録のコメントの本文（識別値ごとの試みた時刻 `at` と Issue の `url`）。"""
    payload = json.dumps({k: dict(v) for k, v in entries.items()}, sort_keys=True)
    note = "cross-review が起票を試みた指摘の記録（同じ指摘を 2 件起票しないために読む。書き換えない）。"
    return f"{RECORD_MARK}\n{note}\n\n{_ENTRIES_MARK}{payload} -->"


def record_entries(body: str) -> dict[str, dict[str, Any]]:
    """起票の記録の本文から、識別値ごとの `at` と `url`（どちらも無いことがある）を読む。読めなければ空。"""
    last = _final_line(body)
    if not (last.startswith(_ENTRIES_MARK) and last.endswith(" -->")):
        return {}
    try:
        raw = json.loads(last[len(_ENTRIES_MARK) : -len(" -->")])
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        ident: {k: v for k, v in entry.items() if k in ("at", "url") and isinstance(v, str)}
        for ident, entry in raw.items()
        if isinstance(entry, dict)
    }
