"""PR に既にあるコメントのパース（#3941。純粋な関数）。

- 既存の指摘: インラインのレビューコメント・レビュー本文・PR コメントの 3 種（人と bot と前のラウンドの返信を含む）を、
  相手役へ信頼しない参考資料として渡す（今の差分で確かめた問題は既存のコメントにかかわらず報告させる）。cross-review 自身が書いた記録のコメントとスレッドの最初のコメント（どちらも
  `report_decide.MARKER` で始まる）は除く（自分の指摘はジャーナルが持つ）
- 引き継ぐ指摘: 前の実行が開いたまま残した自分のスレッド（最初のコメントが `MARKER` で始まり、その書き手が対象リポジトリに
  今の実行の認証の login と同じで、resolve されていない）を、
  別の保存先で始めた実行が未解決の指摘として引き継ぐ。状態ファイルの `threads` が既に持つスレッドは引き継がない
- 記録のコメントから戻す指摘: 有効な位置が無くスレッドにならなかった未解決の指摘は、最新の記録のコメント（書き手が
  信頼できるもの）に埋めた一覧（`report_decide.open_items`）から戻す（`recorded`）
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from typing import NamedTuple, Optional

from ndf.domains.crossreview import report_decide

# スレッドの最初のコメント（`threads.opening`）の見出しと本文
_OPENING = re.compile(
    r"\*\*cross-review の指摘 (?P<id>\S+)\*\*（(?P<by>[^・）]*)・ラウンド \d+(?:・(?P<severity>[a-z]+))?）\n\n(?P<text>.*)", re.S
)


class Remark(NamedTuple):
    """PR にあるコメント 1 件。`kind` は `review_comment`（インライン）・`review`（レビュー本文）・`comment`（PR コメント）。
    `at` は書いた時刻（ISO 8601。並べ替えにだけ使う）。"""

    kind: str
    author: str
    body: str
    path: Optional[str] = None
    line: Optional[int] = None
    at: str = ""
    # 書き手が信頼できるか。読む口は cross-review の記録のコメント（`MARKER` で始まる PR コメント）だけ確かめる
    trusted: bool = False
    # 最終更新の時刻（ISO 8601。PR コメントだけ。記録のコメントは打ち直しで書き換わるので、最新の記録はこれで選ぶ。PR 3944 の r2-5）
    updated: str = ""


class Thread(NamedTuple):
    """PR のレビューのスレッド 1 つ（最初のコメントの id と本文、resolve 済みか、付いた場所）。`trusted` は最初のコメントの
    書き手が信頼できる（今の実行の認証の login と同じ）か。読む口は `MARKER` で始まるスレッドだけ確かめる。"""

    comment: int
    body: str
    resolved: bool
    path: Optional[str] = None
    line: Optional[int] = None
    trusted: bool = False


class Carried(NamedTuple):
    """前の実行から引き継ぐ未解決の指摘 1 件。`comment` は開いたままのスレッドの最初のコメントの id（記録のコメントから
    戻したスレッドの無い指摘は None）。"""

    id: str
    comment: Optional[int]
    text: str
    by: Optional[str]
    severity: Optional[str]
    path: Optional[str]
    line: Optional[int]
    section: Optional[str] = None


# 記録のコメントから戻した指摘の id の頭（`p-<元の id>`。新しい実行の `r<ラウンド>-<n>` と重ねない）
RECORDED_PREFIX = "p-"


def _ours(body: str) -> bool:
    return body.lstrip().startswith(report_decide.MARKER)


def others(remarks: Iterable[Remark]) -> list[Remark]:
    """相手役へ渡すコメント（本文があり、cross-review 自身の記録・スレッドの最初のコメントでないもの）。"""
    return [r for r in remarks if r.body.strip() and not _ours(r.body)]


def _one_line(text: str, chars: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= chars else flat[:chars] + "…"


def _by_file(r: Remark) -> bool:
    """差分のファイルで絞るコメント（パスの付いたインラインのコメント）か。"""
    return r.kind == "review_comment" and r.path is not None


def needs_files(remarks: Iterable[Remark]) -> bool:
    """`relevant` に差分のファイルの一覧が要るか（パスの付いたインラインのコメントがあるか。無ければ一覧を取らない。PR 3944 の r3-5）。"""
    return any(_by_file(r) for r in remarks)


def relevant(remarks: Iterable[Remark], files: Collection[str]) -> list[Remark]:
    """今のラウンドに関わるコメント。インラインのコメントは今の差分のファイルに付いたものだけ（ほかはそのまま）。"""
    return [r for r in remarks if not _by_file(r) or r.path in files]


def remark_lines(remarks: Sequence[Remark], chars: int, total: Optional[int] = None) -> list[str]:
    """相手役へ渡す行（書いた順に 1 件 1 行。本文は `chars` 字で切る）。`total` を渡すと、行の字数の和がそれを超えない
    ように新しいものから残し、省いた件数を先頭の行で知らせる（プロンプトの長さを履歴の量によらず抑える）。"""
    lines = []
    for r in sorted(remarks, key=lambda r: r.at):
        where = f"・{r.path}{':' + str(r.line) if r.line else ''}" if r.path else ""
        lines.append(f"- {r.author or '不明'}（{r.kind}{where}）: {_one_line(r.body, chars)}")
    if total is None:
        return lines
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > total:
            break
        kept.append(line)
        used += len(line) + 1
    dropped = len(lines) - len(kept)
    head = [f"- （古いコメント {dropped} 件は字数の上限 {total} 字を超えるので省いた）"] if dropped else []
    return head + kept[::-1]


def carried(threads: Iterable[Thread], tracked: Collection[int]) -> list[Carried]:
    """引き継ぐ指摘。自分のスレッド（`MARKER` で始まり、書き手が信頼できる）で resolve されておらず、`tracked`（状態ファイルが
    持つスレッドの最初のコメント）に無いもの。書き手を確かめないと、PR にコメントできる人が同じ書式で書いた本文が修正担当へ渡る。"""
    out = []
    for t in threads:
        if t.resolved or t.comment in tracked or not t.trusted or not _ours(t.body):
            continue
        found = _OPENING.search(t.body)
        if found is None or not found.group("text").strip():
            continue
        out.append(
            Carried(f"t{t.comment}", t.comment, found.group("text").strip(), found.group("by"), found.group("severity"), t.path, t.line)
        )
    return out


def _opt_text(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def recorded(remarks: Iterable[Remark]) -> list[Carried]:
    """最新の記録のコメント（`MARKER` で始まる PR コメントのうち、書き手が信頼でき、最終更新の時刻が最も新しいもの）に残った
    未解決の指摘のうち、スレッドの無いもの（スレッドのある指摘は `carried` が引き継ぐ）。同じ保存先の打ち直しは記録の
    コメントを書き換えるので、作った時刻でなく最終更新の時刻で選ぶ（PR 3944 の r2-5）。"""
    mine = sorted((r for r in remarks if r.kind == "comment" and r.trusted and _ours(r.body)), key=lambda r: r.updated or r.at)
    if not mine:
        return []
    out = []
    for item in report_decide.open_items(mine[-1].body):
        fid, text = _opt_text(item.get("id")), _opt_text(item.get("text"))
        if fid is None or text is None or type(item.get("thread")) is int:
            continue
        line = item.get("line")
        out.append(
            Carried(
                fid if fid.startswith(RECORDED_PREFIX) else RECORDED_PREFIX + fid,
                None,
                text,
                _opt_text(item.get("by")),
                _opt_text(item.get("severity")),
                _opt_text(item.get("path")),
                line if type(line) is int and line > 0 else None,
                _opt_text(item.get("section")),
            )
        )
    return out
