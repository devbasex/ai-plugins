"""リリースの承認の証跡を組み、読み、判定する（純粋な関数）。

承認の SSOT は、リリースの PR へのコメント（証跡）だけである。手元の記録を持たないので、回答が記録へ届く前に
止まって同じ承認ゲートで止まり続けること（リリース 8 の 2026-11-14 の事例）が起きない。

証跡の 1 件は、マーカー `TRAIL_MARK` と、回答の行・承認したコミットの行（40 桁の SHA）を持つコメントである。
承認資料の提示（マーカー `REQUEST_MARK`）は証跡に数えず、提示した HEAD と資料の SHA-256 として読む（`parse_request`）。
判定は write 以上の権限を持つ書き手の証跡だけを読み、PR の今の HEAD に当てた最も新しい回答で決める。

数える回答は担い手で分ける（#4446）。利用者の回答は、今の HEAD への最新の提示より後に、その提示と同じ資料の SHA-256
（行 `回答した資料:`）を持つものだけを数える。資料を差し替えて提示し直すと、前の資料への回答では進まない。MVV 判定の回答は
人に資料を示さずに通す経路なので head だけで数えるが、同じ HEAD にその回答より後の提示があれば数えない（人に問うと決め直した）。
資料の SHA-256 を持たない旧いバージョンの利用者の回答は、同じ HEAD に資料の SHA-256 を持つ提示が無い間だけ数える（旧いバージョンで済んだ
承認を取り直させない）。

| 数える証跡 | 判定 |
| --- | --- |
| 今の HEAD への最新の回答が承認 | `approved` |
| 今の HEAD への最新の回答が変更要求 | `rejected` |
| 今の HEAD への回答が無く、ほかの証跡がある（回答の後に HEAD か資料が変わった） | `stale` |
| 1 件も無い | `missing` |
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from ndf import identity

_NS = identity.FIXED_WORDS["workflow_namespace"]
# 証跡のマーカーと、承認資料の提示のマーカー（提示は証跡に数えない）
TRAIL_MARK = f"<!-- {_NS}:release-approval -->"
REQUEST_MARK = f"<!-- {_NS}:release-approval-request -->"
# 承認の判定を PR の HEAD へ付ける commit status の名前
STATUS_CONTEXT = f"{_NS}/release-approval"
# 回答の語（事実の表 answers の語と同じ綴り）
APPROVED, REJECTED = "approved", "rejected"
ANSWERS = (APPROVED, REJECTED)
# 判定
STALE, MISSING = "stale", "missing"
# 証跡の行の見出し
ANSWER_LINE = "回答:"
COMMIT_LINE = "承認したコミット:"
HEAD_LINE = "提示したコミット:"
MATERIAL_LINE = "提示した資料:"
ANSWERED_LINE = "回答した資料:"
BY_LINE = "担い手:"
# 担い手（`trail.By` と同じ値）。資料のハッシュを持つのは利用者の回答だけ
USER, MVV = "user", "mvv"
_SHA = re.compile(r"\b([0-9a-f]{40})\b")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Trail(NamedTuple):
    """読んだ証跡 1 件。`order` はコメントの並び（古い順の番号）。`material` は利用者が回答した資料の SHA-256（無ければ None）。"""

    order: int
    author: str
    answer: str
    sha: str
    by: str | None = None
    material: str | None = None


class Presentation(NamedTuple):
    """読んだ提示 1 件（`order` はコメントの並び、`head` は提示したコミット、`material` は資料の SHA-256）。"""

    order: int
    head: str
    material: str | None


class Judgment(NamedTuple):
    """判定と、人へ見せる 1 行。`trail` は判定に使った証跡（無ければ None）。"""

    state: str
    detail: str
    trail: Trail | None = None

    @property
    def approved(self) -> bool:
        return self.state == APPROVED


def render_trail(answer: str, sha: str, by: str, material_sha256: str | None = None) -> str:
    """証跡のコメントの本文。担い手が利用者のときだけ、回答した資料の SHA-256 の行を足す（#4446）。"""
    if answer not in ANSWERS:
        raise ValueError(f"回答は {', '.join(ANSWERS)} のどれか（渡された値: {answer!r}）")
    if not _SHA.fullmatch(sha):
        raise ValueError(f"承認したコミットは 40 桁の SHA（渡された値: {sha!r}）")
    answered = f"{ANSWERED_LINE} `{material_sha256}`\n" if by == USER and material_sha256 else ""
    return f"{TRAIL_MARK}\n## リリースの承認\n\n{ANSWER_LINE} {answer}\n{COMMIT_LINE} `{sha}`\n{BY_LINE} {by}\n{answered}"


def render_request(material: str, head: str, material_sha256: str) -> str:
    """承認資料の提示のコメントの本文（証跡に数えない）。提示したコミットと資料の SHA-256 の行を持つ（#4446）。"""
    return f"{REQUEST_MARK}\n{HEAD_LINE} `{head}`\n{MATERIAL_LINE} `{material_sha256}`\n\n{material.rstrip()}\n"


def _line_value(body: str, label: str) -> str | None:
    """見出し `label` で始まる行の値（最初の 1 行）。無ければ None。"""
    for line in body.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip().strip("`").strip()
    return None


def material_head(material: str) -> str | None:
    """承認資料が対象にしたコミット（行 `HEAD_LINE` の値の先頭の 40 桁の SHA）。無い・形が違えば None。

    SHA の後ろの注記（`（版上げの PR の HEAD）` など）は読まない。
    """
    got = _line_value(material, HEAD_LINE)
    found = _SHA.match(got.lstrip("`")) if got is not None else None
    return found.group(1) if found else None


def _sha256_of(body: str, label: str) -> str | None:
    got = _line_value(body, label)
    return got if got is not None and _SHA256.fullmatch(got) else None


def parse(order: int, author: str, body: str) -> Trail | None:
    """コメント 1 件を証跡として読む。証跡でない・形が違えば None（提示のコメントも None）。"""
    if TRAIL_MARK not in body or REQUEST_MARK in body:
        return None
    answer, commit = _line_value(body, ANSWER_LINE), _line_value(body, COMMIT_LINE)
    if answer not in ANSWERS or commit is None or not _SHA.fullmatch(commit):
        return None
    return Trail(order, author, answer, commit, _line_value(body, BY_LINE), _sha256_of(body, ANSWERED_LINE))


def parse_request(order: int, author: str, body: str) -> Presentation | None:
    """コメント 1 件を提示として読む（提示したコミットと資料の SHA-256）。提示でない・形が違えば None。"""
    if REQUEST_MARK not in body:
        return None
    head = material_head(body)
    return Presentation(order, head, _sha256_of(body, MATERIAL_LINE)) if head is not None else None


def trails(comments: Iterable[tuple[str, str, bool]]) -> list[Trail]:
    """(書き手, 本文, 信用するか) の並び（古い順）から、信用する書き手の証跡だけを読む。"""
    out = []
    for i, (author, body, trusted) in enumerate(comments):
        got = parse(i, author, body) if trusted else None
        if got is not None:
            out.append(got)
    return out


def presentations(comments: Iterable[tuple[str, str, bool]]) -> list[Presentation]:
    """(書き手, 本文, 信用するか) の並び（古い順）から、信用する書き手の提示だけを読む。"""
    out = []
    for i, (author, body, trusted) in enumerate(comments):
        got = parse_request(i, author, body) if trusted else None
        if got is not None:
            out.append(got)
    return out


def latest_presentation(presented: Sequence[Presentation], head: str) -> Presentation | None:
    """今の HEAD `head` への最新の提示。無ければ None。"""
    on_head = [p for p in presented if p.head == head]
    return max(on_head, key=lambda p: p.order) if on_head else None


def _counted(t: Trail, latest: Presentation | None) -> bool:
    """今の HEAD への回答 `t` を判定に数えるか（モジュールの説明の「数える回答」）。変更要求は担い手と資料を問わず数える
    （承認の後の変更要求を捨てて進めない）。"""
    if t.answer == REJECTED:
        return True
    if t.by == MVV:
        return latest is None or latest.order < t.order
    if latest is None or latest.material is None:
        return t.material is None  # 資料の SHA-256 の行を持たない旧いバージョンの提示と回答。人の回答はバージョンアップをまたいで数える
    return t.material == latest.material and t.order > latest.order


def judge(found: Sequence[Trail], presented: Sequence[Presentation], head: str) -> Judgment:
    """今の HEAD `head`（40 桁）への最新の、数える回答で判定する（担い手で分ける。#4446）。"""
    latest = latest_presentation(presented, head)
    on_head = [t for t in found if t.sha == head and _counted(t, latest)]
    if on_head:
        last = max(on_head, key=lambda t: t.order)
        verb = "承認した" if last.answer == APPROVED else "変更を要求した"
        return Judgment(last.answer, f"{last.author} が {head[:12]} を{verb}", last)
    if found:
        last = max(found, key=lambda t: t.order)
        return Judgment(
            STALE,
            f"回答は {last.sha[:12]} か前の資料へのもので、今の HEAD {head[:12]} と最新の資料への回答が無い（承認し直しが要る）",
            last,
        )
    return Judgment(MISSING, f"{head[:12]} への承認の証跡が無い")
