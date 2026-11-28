"""2 ラウンド目以降の線引きと起票の判断（#3780。純粋な関数）。

指摘を、修正へ進めるものと直さずに残すもの（指摘の行の `parked`）に分ける。行へ書くのは `journal.FindingJournal.sort` である。
残した指摘のうち起票するもの、起票の題と本文、同じ指摘を扱う Issue の見分け、起票の記録の形もここが持つ。起票は `leftover` が行う。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from enum import StrEnum
from typing import Any, NamedTuple, Optional

from markdown_it import MarkdownIt

from ndf import identity as ndf_identity
from ndf.domains.crossreview import criteria_decide
from ndf.domains.records import mvv_decide

# 直さずに残す指摘の行の欄。修正担当へ渡さず、次のラウンドを始める理由にも数えない
PARKED = "parked"
# 残すと決めたときに照らした出し直しに、行があったかの記録（結果の `decisions` の理由に使う）。行の `line` は初出時の位置が残る
# ことがあるので、理由の元にしない
PARKED_ON_LINE = "parked_on_line"
# 状態ファイルのキー。前の実行の記録のコメントから引き継いだ、直さずに残した指摘の起票の URL の並び。記録の本文は伏せてあり、
# 伏せる値は実行する環境で変わるので、本文でなく Issue の目印で指摘と照らす
CARRIED_URLS = "issue_carried_urls"
# 指摘の行番号が、その位置の基準のコミットの PR の差分に載らないことの記録。行番号は起票の場所に使うので行に残し、投稿の位置
# （`location`）だけをファイルにする
OFF_DIFF = "off_diff"
# 起票の題にする本文の先頭の字数
TITLE_CHARS = 80
_ENTRY = ndf_identity.FIXED_WORDS["entrypoint"]
# 起票の記録のコメントの先頭の目印と、記録の中身を埋める最後の行の前
RECORD_MARK = f"<!-- {_ENTRY}:cross-review-leftover-record -->"
_ENTRIES_MARK = f"<!-- {_ENTRY}:cross-review-leftover-entries "
# 起票の記録に書く識別値（`identity`）と時刻（GitHub の ISO 8601）の形
_IDENT = re.compile(r"[0-9a-f]{12}")
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
# 記録のコメントに載せ、読み戻す起票の URL の形（認証情報・クエリ・断片を持たない）
_ISSUE_URL = re.compile(r"https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/[1-9][0-9]*")
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
    # 別の保存先から引き継いだ、まだ指す Issue を確かめていない URL
    PRIOR = "prior_issue"
    # 起票しない行を引き継いだ URL と照らし、その指摘の Issue が無かったときの、指摘と URL の組の値
    CHECKED = "issue_checked"


class Listed(NamedTuple):
    """検索か一覧で読んだ Issue（`is_pull` は Pull Request。`author_id` は作者のアカウント ID で、無ければ 0）。"""

    url: str
    body: str
    author: str
    association: str
    created_at: str
    is_pull: bool
    author_id: int = 0


def issue_url(value: object, repo: Optional[str] = None) -> Optional[str]:
    """`value` が正規の形の Issue の URL ならその値、違えば None。`repo`（`<所有者>/<リポジトリ>`）を渡せば、そのリポジトリの
    Issue だけを通す。記録のコメントの一覧は伏せ字を当てずに URL を載せるので、書く側と読む側の両方がこれを通す。"""
    found = _ISSUE_URL.fullmatch(value) if isinstance(value, str) else None
    if found is None or (repo and found.group("repo").casefold() != repo.casefold()):
        return None
    return found.group(0)


def files_issue(row: Mapping[str, Any]) -> bool:
    """重い指摘（critical・major）が起票してよい種類を持つか。"""
    if not criteria_decide.blocking(criteria_decide.severity_of(row.get("severity"))):
        return False
    return bool(finding_kinds(row) & set(criteria_decide.filed_kinds()))


def identity(origin_pr: int, text: str) -> str:
    """指摘を見分ける値（元の PR の番号と本文の全体の SHA-256 の先頭 12 字）。"""
    return hashlib.sha256(f"{origin_pr}\n{normalized(text)}".encode()).hexdigest()[:12]


def issue_mark(ident: str) -> str:
    """起票する Issue の本文の最後に置く目印の行。"""
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


def issue_parts(text: str, impact: str, place: str, pr_urls: Sequence[str]) -> tuple[str, str]:
    """起票の題と、目印（`issue_mark`）を除く本文。`text`・`impact`・`place` は全文に伏せ字を当てた後の値で、ここでは切り出すだけで伏せない
    （伏せる前に切り出すと、途中で切れた秘密が秘密の形に当たらず公開される）。"""
    title = " ".join(text.split())[:TITLE_CHARS]
    parts = [_fenced(text.strip())]
    parts += [f"## {head}\n\n{_fenced(value.strip())}" for head, value in (("起きること", impact), ("場所", place)) if value.strip()]
    parts.append("## Pull Request\n\n" + "\n".join(f"- {url}" for url in pr_urls))
    return title, "\n\n".join(parts) + "\n"


def trusted(author_id: int, me: int, account_ids: Collection[int]) -> bool:
    """起票の記録と Issue の作者が、実行しているアカウントか、前に起票に使ったアカウントか。login は手放すと別の
    アカウントが取得できるので、アカウント ID で照らす（ID が無い 0 は当たらない）。ほかの作者は、呼び手が対象リポジトリの
    権限で確かめる（`GitHub.trusts`）。"""
    return author_id > 0 and (author_id == me or author_id in account_ids)


def prose(text: str) -> str:
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
    if listed.is_pull or _final_line(listed.body) != issue_mark(ident):
        return False
    return re.search(rf"/pull/{int(origin_pr)}(?!\d)", prose(listed.body)) is not None


def _has_mark(body: str, ident: str, prose_of: Callable[[str], str]) -> bool:
    """引用とコードブロックの外に、目印だけの行があるか。目印の文字列を含まない本文は解析しない。"""
    mark = issue_mark(ident)
    return mark in body and any(line.strip() == mark for line in _lines_of(prose_of(body)))


def names_finding(listed: Listed, ident: str, text: str, prose_of: Callable[[str], str] = prose) -> bool:
    """起票の記録か引き継ぎの URL の Issue が、この指摘のものか。起票の後に本文へ書き足されることがあるので、目印の位置は問わない。
    引用かコードブロックに転載した目印は数えない。

    目印は、今の識別値 `ident` か、本文の PR の URL の番号（起票時の元の PR）と指摘の本文 `text` の識別値を受ける。PR を
    巻き直して別の保存先から始めると元の番号が変わり、`ident` は巻き直し前に起票した Issue の目印と違うためである。
    目印の照合と PR の番号の抽出は、本文の解析（`prose_of`）の結果を共有する。呼び手は本文ごとの結果を使い回す。"""
    if listed.is_pull:
        return False
    if _has_mark(listed.body, ident, prose_of):
        return True
    prs = dict.fromkeys(int(n) for n in re.findall(r"/pull/(\d+)", prose_of(listed.body)))
    return any(_has_mark(listed.body, identity(pr, text), prose_of) for pr in prs)


def _issue_number(url: str, issues_url: str) -> Optional[int]:
    """`issues_url`（このリポジトリの Issue の URL の番号より前）で始まる Issue の URL の番号。違えば None。"""
    head, tail = url[: len(issues_url)], url[len(issues_url) :]
    return int(tail) if head.lower() == issues_url.lower() and tail.isdecimal() else None


def record_body(entries: Mapping[str, Mapping[str, Any]], issues_url: str) -> str:
    """起票の記録のコメントの本文（識別値ごとの試みた時刻 `at` と Issue の番号 `number`）。

    伏せ字には通さない。通すと、手元のアカウント名が `number` や番号と同じ数字のときにキーや値が置き換わり、読み戻せない。
    代わりに、固定の文とキーに、形を確かめた識別値・時刻・番号だけを足して組む（形の違う値は書かない）。URL は番号にする。"""
    stored: dict[str, dict[str, Any]] = {}
    for ident, entry in entries.items():
        if not _IDENT.fullmatch(ident):
            continue
        stored[ident] = {}
        if "at" in entry and _AT.fullmatch(entry["at"]):
            stored[ident]["at"] = entry["at"]
        number = _issue_number(entry["url"], issues_url) if "url" in entry else None
        if number is not None:
            stored[ident]["number"] = number
    note = "cross-review が起票を試みた指摘の記録（同じ指摘を 2 件起票しないために読む。書き換えない）。"
    return f"{RECORD_MARK}\n{note}\n\n{_ENTRIES_MARK}{json.dumps(stored, sort_keys=True)} -->"


def _entry(stored: Mapping[str, Any], issues_url: str) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if isinstance(stored.get("at"), str):
        entry["at"] = stored["at"]
    number = stored.get("number")
    if type(number) is int and number > 0:
        entry["url"] = f"{issues_url}{number}"
    return entry


def record_entries(body: str, issues_url: str) -> dict[str, dict[str, Any]]:
    """起票の記録の本文から、識別値ごとの `at` と `url`（番号から組む。どちらも無いことがある）を読む。読めなければ空。"""
    last = _final_line(body)
    if not (last.startswith(_ENTRIES_MARK) and last.endswith(" -->")):
        return {}
    try:
        raw = json.loads(last[len(_ENTRIES_MARK) : -len(" -->")])
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {ident: _entry(stored, issues_url) for ident, stored in raw.items() if isinstance(stored, dict)}


# --- 結果への載せ方（#3780） -------------------------------------------------------------


def _left(row: Mapping[str, Any]) -> bool:
    """直さずに残して未解決の指摘か。"""
    return bool(row.get(PARKED)) and row.get("outcome") == mvv_decide.REVIEW_UNRESOLVED


def result_fields(row: Mapping[str, Any], comment_url: Optional[str]) -> dict[str, str]:
    """結果の `items` に足す残し方（`disposition` と `url`。残していない指摘は空）。起票できていなければ記録のコメント
    `comment_url` を指し、それも書けていなければ `url` を足さない。"""
    if not _left(row):
        return {}
    if url := row.get(Filed.URL):
        return {"disposition": "issue", "url": url}
    return {"disposition": "comment", **({"url": comment_url} if comment_url else {})}


def summary_line(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """要約に足す残した指摘の数（無ければ None）。"""
    left = [r for r in rows if _left(r)]
    if not left:
        return None
    return f"残した指摘 {len(left)}（起票 {sum(1 for r in left if r.get(Filed.URL))}）"


def decisions(rows: Sequence[Mapping[str, Any]], comment_url: Optional[str]) -> list[dict[str, str]]:
    """プロジェクト MVV に反する残した指摘を、承認する人が確かめる判断にする。"""
    out = []
    for row in rows:
        item = row.get(mvv_decide.REVIEW_ITEM)
        if not _left(row) or not item:
            continue
        where = result_fields(row, comment_url)
        on_line = row.get(PARKED_ON_LINE)
        if on_line is None:
            # 照らした位置の記録が無い行は、行とファイルのどちらで確かめたかを言わない
            why = "2 ラウンド目以降の線引きで残した major（照らした位置の記録が無い）"
        elif on_line:
            why = "2 ラウンド目以降の、変えていない行への major"
        else:
            # 照らした出し直しに行が無いので、指したファイルが変わっていないことだけを確かめた（`proceeds`）
            why = "2 ラウンド目以降の、行を特定していない major（指したファイルは変えていない）"
        out.append(
            {
                "decision": f"指摘 {row.get('id')} は {item} に反するが、直さずに残した",
                "basis": f"{why}（{where['url'] if 'url' in where else '記録のコメントを書けていない'}）",
            }
        )
    return out
