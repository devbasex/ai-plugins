"""リリースの承認の証跡を組み、読み、判定する（純粋な関数）。

承認の正本は、リリースの PR へのコメント（証跡）だけである。手元の記録を持たないので、答えが記録へ届く前に
止まって同じ承認ゲートで止まり続けること（リリース 8 の 2026-11-14 の事例）が起きない。

証跡の 1 件は、印 `TRAIL_MARK` と、答えの行・承認したコミットの行（40 桁の SHA）を持つコメントである。
承認資料の提示（印 `REQUEST_MARK`）は証跡に数えない。判定は write 以上の権限を持つ書き手の証跡だけを読み、
PR の今の先端に当てた最も新しい答えで決める。

| 信用する証跡 | 判定 |
| --- | --- |
| 今の先端への最新の答えが承認 | `approved` |
| 今の先端への最新の答えが差し戻し | `rejected` |
| 今の先端への答えが無く、ほかのコミットへの答えがある（答えの後に先端が動いた） | `stale` |
| 1 件も無い | `missing` |
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from ndf import identity

_NS = identity.FIXED_WORDS["workflow_namespace"]
# 証跡の印と、承認資料の提示の印（提示は証跡に数えない）
TRAIL_MARK = f"<!-- {_NS}:release-approval -->"
REQUEST_MARK = f"<!-- {_NS}:release-approval-request -->"
# 承認の判定を PR の先端へ付ける commit status の名前
STATUS_CONTEXT = f"{_NS}/release-approval"
# 答えの語（事実の表 answers の語と同じ綴り）
APPROVED, REJECTED = "approved", "rejected"
ANSWERS = (APPROVED, REJECTED)
# 判定
STALE, MISSING = "stale", "missing"
# 証跡の行の見出し
ANSWER_LINE = "答え:"
COMMIT_LINE = "承認したコミット:"
HEAD_LINE = "提示したコミット:"
_SHA = re.compile(r"\b([0-9a-f]{40})\b")


class Trail(NamedTuple):
    """読んだ証跡 1 件。`order` はコメントの並び（古い順の番号）。"""

    order: int
    author: str
    answer: str
    sha: str


class Judgment(NamedTuple):
    """判定と、人へ見せる 1 行。`trail` は判定に使った証跡（無ければ None）。"""

    state: str
    detail: str
    trail: Trail | None = None

    @property
    def approved(self) -> bool:
        return self.state == APPROVED


def render_trail(answer: str, sha: str, by: str) -> str:
    """証跡のコメントの本文。"""
    if answer not in ANSWERS:
        raise ValueError(f"答えは {', '.join(ANSWERS)} のどれか（渡された値: {answer!r}）")
    if not _SHA.fullmatch(sha):
        raise ValueError(f"承認したコミットは 40 桁の SHA（渡された値: {sha!r}）")
    return f"{TRAIL_MARK}\n## リリースの承認\n\n{ANSWER_LINE} {answer}\n{COMMIT_LINE} `{sha}`\n担い手: {by}\n"


def render_request(material: str, head: str) -> str:
    """承認資料の提示のコメントの本文（証跡に数えない）。"""
    return f"{REQUEST_MARK}\n{HEAD_LINE} `{head}`\n\n{material.rstrip()}\n"


def _line_value(body: str, label: str) -> str | None:
    """見出し `label` で始まる行の値（最初の 1 行）。無ければ None。"""
    for line in body.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip().strip("`").strip()
    return None


def parse(order: int, author: str, body: str) -> Trail | None:
    """コメント 1 件を証跡として読む。証跡でない・形が違えば None（提示のコメントも None）。"""
    if TRAIL_MARK not in body or REQUEST_MARK in body:
        return None
    answer, commit = _line_value(body, ANSWER_LINE), _line_value(body, COMMIT_LINE)
    if answer not in ANSWERS or commit is None or not _SHA.fullmatch(commit):
        return None
    return Trail(order, author, answer, commit)


def trails(comments: Iterable[tuple[str, str, bool]]) -> list[Trail]:
    """(書き手, 本文, 信用するか) の並び（古い順）から、信用する書き手の証跡だけを読む。"""
    out = []
    for i, (author, body, trusted) in enumerate(comments):
        got = parse(i, author, body) if trusted else None
        if got is not None:
            out.append(got)
    return out


def judge(found: Sequence[Trail], head: str) -> Judgment:
    """今の先端 `head`（40 桁）への最新の答えで判定する。"""
    on_head = [t for t in found if t.sha == head]
    if on_head:
        last = max(on_head, key=lambda t: t.order)
        verb = "承認した" if last.answer == APPROVED else "差し戻した"
        return Judgment(last.answer, f"{last.author} が {head[:12]} を{verb}", last)
    if found:
        last = max(found, key=lambda t: t.order)
        return Judgment(STALE, f"答えは {last.sha[:12]} へのもので、今の先端 {head[:12]} への答えが無い（承認し直しが要る）", last)
    return Judgment(MISSING, f"{head[:12]} への承認の証跡が無い")
