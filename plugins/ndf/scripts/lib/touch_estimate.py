"""触る場所の見込みと、見込みが重ならない課題を先に選ぶ並べ替え（#2997）。

ファイルも外部コマンドも読まず、渡した文字列と追跡しているファイルの一覧（`git ls-files`）から値を返すだけにする。

    files = ["src/app/models/user.rb", "docs/a.md"]
    est = estimate([(DESIGN, lambda: from_design(text, 12)), (BODY, lambda: from_body(body, files))])
    est.to_dict()   # {"paths": ["src/app/models/user.rb"], "source": "課題の本文", "note": ""}
    got = spread([1, 2, 3], set(), {1: e1, 2: e2, 3: e3}, slots=2, width=1)
    got.take, got.overlap, got.reorders

- 出所は 設計の結果 → 課題グループの表 → 課題の本文 の順に見て、見込みが 1 件以上出た最初の 1 つだけを使う（決定 1）
- 本文からはファイルだけを取る（追跡しているファイルに一致・glob が当たる・末尾のパスの要素が 1 つのファイルだけに一致）
- 重なりの判定は実装のキューと同じ `admission.overlap_of` と共有の一覧を使う（I10）
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable, Mapping, Sequence

import md
from supervise_lib import admission

DESIGN = "設計の結果"
GROUP = "課題グループの表"
BODY = "課題の本文"
NONE = "見込みなし"
SOURCES = (DESIGN, GROUP, BODY, NONE)

YES, NO, UNKNOWN = "あり", "なし", "不明"

GROUP_HEADING = "並列の組（見込み）"
BODY_HEADINGS = ("目的", "対象範囲", "事象", "原因の見当")
BODY_STOP = "含まない"  # 対象範囲の「含まない」より後は読まない
_GLOB = re.compile(r"[*?\[]")
_ISSUE = re.compile(r"#(\d+)")


@dataclass(frozen=True)
class Estimate:
    paths: list[str] = field(default_factory=list)
    source: str = NONE
    note: str = ""

    def to_dict(self) -> dict:
        return {"paths": list(self.paths), "source": self.source, "note": self.note}


@dataclass
class Spread:
    take: list = field(default_factory=list)  # 取った課題（取った順）
    overlap: dict = field(default_factory=dict)  # 課題 → {"overlap": あり/なし/不明, "overlap_with": [課題]}
    reorders: list[dict] = field(default_factory=list)  # [{"later", "earlier", "with"}]


def _clean(token: str) -> str:
    t = token.strip()
    return t[2:] if t.startswith("./") else t


def _match_file(token: str, files: Sequence[str]) -> list[str]:
    """本文の語に当たるファイル: 完全に一致 → glob が当たる → 末尾のパスの要素が 1 つのファイルだけに一致。ディレクトリは取らない。"""
    t = _clean(token)
    if not t or " " in t or t.endswith("/"):
        return []
    if t in files:
        return [t]
    if _GLOB.search(t):
        return fnmatch.filter(files, t)
    tail = PurePosixPath(t).parts
    hits = [f for f in files if PurePosixPath(f).parts[-len(tail) :] == tail]
    return hits if len(hits) == 1 else []


def _match_place(token: str, files: Sequence[str]) -> list[str]:
    """課題グループの表の語: ファイルかディレクトリ（追跡しているファイルを含むもの）か glob。当たらない語（Skill の名前など）は捨てる。"""
    t = _clean(token).rstrip("/")
    if not t or " " in t:
        return []
    if _GLOB.search(t):
        return fnmatch.filter(files, t)
    return [t] if any(admission.contains(t, f) for f in files) else []


def _uniq(paths: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def from_design(text: str, issue: int) -> list[str]:
    """設計文書の「設計の結果」の表の、課題が「実装する」の行の触るファイル。表が無い・読めなければ空。"""
    import design_results

    try:
        rows = design_results.parse_results(text or "") or []
    except Exception:  # noqa: BLE001 — 書式の外れた表は見込みの出所にしない（次の出所へ進む）
        return []
    return _uniq(f for r in rows if r.issue == issue and r.kind == design_results.IMPLEMENT for f in r.files)


def _group_rows(description: str) -> list[tuple[list[int], str]]:
    """`### 並列の組（見込み）` の表の行（課題の番号, 「触る場所の見込み」のセル）。表が無ければ空。"""
    text = description or ""
    sec = md.section_named(text, GROUP_HEADING)
    if sec is None:
        return []
    out = []
    for table in md.tables(text):
        head = [h.strip() for h in table.header]
        if not sec.start <= table.start < sec.end or "課題" not in head or "触る場所の見込み" not in head:
            continue
        ci, pi = head.index("課題"), head.index("触る場所の見込み")
        out += [([int(n) for n in _ISSUE.findall(c[ci])], c[pi]) for c in table.rows if len(c) > max(ci, pi)]
    return out


def group_issues(description: str) -> list[int]:
    """課題グループの表に載る課題の番号（載った順）。"""
    return _uniq(n for nums, _ in _group_rows(description) for n in nums)


def from_group_table(description: str, issue: int, files: Sequence[str]) -> list[str]:
    """マイルストーンの説明の `### 並列の組（見込み）` の表で、課題の行の「触る場所の見込み」のバッククォートの語。"""
    out: list[str] = []
    for nums, cell in _group_rows(description):
        if issue in nums:
            for token in re.findall(r"`([^`]+)`", cell):
                out += _match_place(token, files)
    return _uniq(out)


def _body_lines(text: str) -> list[tuple[int, int]] | None:
    """本文のうち読む行の範囲 [(始め, 終わり)]。見出しが 1 つも無ければ None（全体を読む）。"""
    lines = text.splitlines()
    spans = []
    for s in md.md_sections(text):
        if s.heading.title not in BODY_HEADINGS:
            continue
        end = s.end
        if s.heading.title == "対象範囲":
            end = next((i for i in range(s.start, s.end) if i < len(lines) and BODY_STOP in lines[i]), s.end)
        spans.append((s.start, end))
    return spans or None


def from_body(body: str, files: Sequence[str]) -> list[str]:
    """課題の本文の見出し「目的」「対象範囲」（含まないより前）「事象」「原因の見当」のバッククォートの語に当たるファイル。"""
    text = body or ""
    spans = _body_lines(text)
    out: list[str] = []
    for span in md.code_spans(text):
        if spans is None or any(a <= span.line < b for a, b in spans):
            out += _match_file(span.content, files)
    return _uniq(out)


def estimate(sources: Iterable[tuple[str, Callable[[], list[str]]]], note: str = "") -> Estimate:
    """出所を順に見て、見込みが 1 件以上出た最初の出所の見込み。どれも出なければ見込みなし（note は理由）。

    出所は (出所の名前, 見込みを返す関数) で渡す。先の出所で出れば後の関数を呼ばない（本文の取得を省く）。"""
    for source, get in sources:
        paths = _uniq(get() or [])
        if paths:
            return Estimate(paths, source, "")
    return Estimate([], NONE, note)


def _relation(mine: Estimate, others: Sequence[tuple[object, Estimate]], shared: Sequence[Mapping]) -> tuple[str, list]:
    """既に選んだ課題との重なり。どれかと重なれば あり、重ならずに見込みなしの相手がいれば 不明、ほかは なし。"""
    if not others:
        return NO, []
    if not mine.paths:
        return UNKNOWN, [n for n, _ in others]
    hit = [n for n, e in others if e.paths and admission.overlap_of(mine.paths, e.paths, shared)]
    if hit:
        return YES, hit
    unknown = [n for n, e in others if not e.paths]
    return (UNKNOWN, unknown) if unknown else (NO, [])


def spread(
    order: Sequence,
    fixed: Iterable,
    estimates: Mapping[object, Estimate],
    slots: int,
    width: int = 0,
    shared: Iterable[Mapping] = (),
) -> Spread:
    """枠の順 `order` から `slots` 件を取る。`fixed`（先頭に選ぶ課題・品質の枠）は重なりを見ずに先に取る（I3）。

    残りは先頭から「残りの枠の数＋width」件の窓の中で、既に取った課題と重ならない課題を先に取り（I4）、枠が残れば
    取らなかった候補を枠の順に戻して埋める（I5）。width が 0 なら取る組は枠の順の先頭 `slots` 件と同じ（I6）。"""
    shared, fixed = list(shared), set(fixed)
    blank = Estimate()
    est = {n: estimates.get(n, blank) for n in order}
    take = [n for n in order if n in fixed][: max(slots, 0)]
    rest = [n for n in order if n not in fixed]
    k = max(slots - len(take), 0)
    window = rest[: k + max(width, 0)]
    picked: list = []
    for n in window:
        if len(picked) >= k:
            break
        rel, _ = _relation(est[n], [(m, est[m]) for m in take + picked], shared)
        if rel != YES:
            picked.append(n)
    for n in rest:
        if len(picked) >= k:
            break
        if n not in picked:
            picked.append(n)
    take += picked
    out = Spread(take=take)
    for i, n in enumerate(take):
        rel, with_ = _relation(est[n], [(m, est[m]) for m in take[:i]], shared)
        out.overlap[n] = {"overlap": rel, "overlap_with": with_}
    chosen = set(take)
    for j, n in enumerate(window):
        if n in chosen:
            continue
        for later in window[j + 1 :]:
            if later in chosen and later in picked:
                _, with_ = _relation(est[n], [(m, est[m]) for m in take if m != n], shared)
                out.reorders.append({"later": n, "earlier": later, "with": with_})
    return out
