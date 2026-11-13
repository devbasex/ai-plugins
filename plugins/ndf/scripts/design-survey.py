#!/usr/bin/env python3
"""design-survey.py: 設計文書の検査。自前で作る決定に外部の調査で比べた候補があるかと、人へ残す判断がレッドラインを名指すかを確かめる。LLM を呼ばず、読むだけ。

    python3 design-survey.py check (--issue N... | --file PATH...) [--require-detail] [--root DIR]

- `--issue`: HEAD の `issues/` から、名前の番号の並びにその番号を含む `issue-<番号>-design*.md` と
  `issue-<番号>-requirements.md` を読む。読んだ文書の名前のほかの番号もたどり、新しい番号が出なくなるまで読み足す
- `--file`: 渡したパスを worktree から読む（`light` の受け入れ条件のファイル・`operation` の実行の記録）

課題の番号を 1 つでも共有する文書どうしを 1 つのまとまりにし、まとまりごとに判定する（コードブロックの中は読まない）。

- `unknown_method`: 決定の `作り方:` の値に「自前で作る」「ライブラリやサービスを使う」「標準に倣う」以外の語がある
- `no_candidates`: 作り方に「自前で作る」を含む決定の `比べた候補:` に番号が無い（まとまりの外部の調査を `省く:` で
  省いたときは除く）
- `unknown_candidate`: `比べた候補:` の番号が、まとまりの外部の調査の表の 1 列目（`STD<数>` / `PRD<数>` / `LIB<数>`）に無い
- `unnamed_redline`: 文書ごとに判定する。見出しが「決定の記録」「未確認のまま残ること」「人へ残す判断」で始まる節
  （下位の節を含む）で、判断を人へ委ねる語（`DELEGATE`）を含みレッドラインの番号（`C<数>` / `P<数>` / `R<数>`）を
  含まない行。「人へ残す判断」の表で、番号のセルに番号が無い行と「なぜ当たるか」のセルが空の行。
  インラインコードの中の語は数えない

詳細設計の文書（`issue-<番号>-design-detail.md`。#3144）があるまとまりには、`lib/design_detail.py` の 4 規則
（`detail_section`・`detail_failure`・`detail_reuse`・`detail_file`）を当てる。`--require-detail` を渡すと、詳細設計の
文書の無いまとまりを `detail_missing` で指摘する（詳細設計を通る `standard`・`legacy-refactor` の設計のプランが渡す）。

終了コードは 0 = 指摘なし（作り方の行が 0 件の文書を含む）/ 1 = 指摘あり / 2 = 文書か git・契約・雛形を読めない /
3 = `--issue` で文書が 1 つも見つからない。結果は lib/step_result.py の形の 1 行の JSON。
手順は design の references/external-survey.md にある。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md")
import md  # noqa: E402
import doc_kinds  # noqa: E402
from design_detail import DetailTemplateError, check_detail, is_detail  # noqa: E402
from design_docs import find_closed, name_numbers, read_files, require_repo  # noqa: E402
from design_results import IMPLEMENT, DesignCellError, parse_results  # noqa: E402
from step_result import (  # noqa: E402
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_UNREADABLE,
    EXIT_VIOLATION,
    StepError,
    common_parser,
    emit,
    git_root,
    main_with,
    result,
)

TOOL = "design-survey"
SELF_BUILT = "自前で作る"
METHODS = (SELF_BUILT, "ライブラリやサービスを使う", "標準に倣う")
SURVEY_TITLE = "外部の調査"
DECISION_TITLE = re.compile(r"^決定\s*(?P<n>\d+)")
LEAD = r"^\s*(?:[-*+]\s+)?"
METHOD_LINE = re.compile(LEAD + r"作り方\s*[:：]\s*(?P<v>.*)$")
CANDIDATES_LINE = re.compile(LEAD + r"比べた候補\s*[:：]\s*(?P<v>.*)$")
SKIP_LINE = re.compile(LEAD + r"省く\s*[:：]")
CANDIDATE_ID = re.compile(r"(?<![A-Za-z0-9])(?:STD|PRD|LIB)\d+(?![0-9])")
SPLIT = re.compile(r"\s*[・/]\s*")
REDLINE_TITLES = ("決定の記録", "未確認のまま残ること", "人へ残す判断")
LEFT_TITLE = "人へ残す判断"
# 判断の主語を人に置いて決めさせる言い回し（「利用者が決めた」のような済んだ決定の記録は含めない）。
# mvv-gate-replay.py が判定の理由から集合を選ぶときも同じ一覧を使う。
# 用語「人へ残す判断」と重なる言い回しは入れない
DELEGATE = re.compile(
    r"利用者が(?:決める|選ぶ|確かめる|判断する)"
    r"|承認ゲート(?:\s*\d+\s*)?で(?:人|利用者)が(?:決め|選|確かめ)"
    r"|(?:人|利用者)(?:へ|に)委ね(?:る|た|られ)"
)
REDLINE_ID = re.compile(r"(?<![A-Za-z0-9])[CPR][0-9]+(?![0-9])")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")


def _first_match(body: list[tuple[int, str]], pattern: re.Pattern[str]) -> tuple[int, str, str] | None:
    """本文の行のうち pattern に最初に合う行の（行番号, 行, 値）。無ければ None。"""
    return next(((i, ln, m.group("v")) for i, ln in body if (m := pattern.match(ln))), None)


def _read_survey(text: str, sections: list, prose: list[tuple[int, str]]) -> tuple[bool, set[str]]:
    """外部の調査の節から、`省く:` の行があるかと、表の 1 列目の候補の番号を読む。"""
    surveys = [s for s in sections if s.heading.title.startswith(SURVEY_TITLE)]
    skipped = any(s.start <= i < s.end and SKIP_LINE.match(ln) for s in surveys for i, ln in prose)
    candidates: set[str] = set()
    for t in md.tables(text):
        if any(s.start <= t.start < s.end for s in surveys):
            for row in t.rows:
                cell = row[0].strip().strip("`*_ ") if row else ""
                if CANDIDATE_ID.fullmatch(cell):
                    candidates.add(cell)
    return skipped, candidates


def _read_decisions(sections: list, prose: list[tuple[int, str]]) -> list:
    """決定の節ごとに（番号, `作り方:` の行, `比べた候補:` の行）を読む。"""
    decisions = []
    for s in sections:
        m = DECISION_TITLE.match(s.heading.title)
        if not m:
            continue
        body = [(i, ln) for i, ln in prose if s.start <= i < s.end]
        decisions.append((m.group("n"), _first_match(body, METHOD_LINE), _first_match(body, CANDIDATES_LINE)))
    return decisions


def _left_table_rows(text: str, sections: list) -> list[tuple[int, str, str, str]]:
    """「人へ残す判断」の表の行ごとに（行番号, 行, 番号のセル, なぜ当たるかのセル）を読む。"""
    lefts = [s for s in sections if s.heading.title.startswith(LEFT_TITLE)]
    lines = text.splitlines()
    out = []
    for t in md.tables(text):
        if not any(s.start <= t.start < s.end for s in lefts):
            continue
        head = [h.strip() for h in t.header]
        num_col = next((k for k, h in enumerate(head) if "レッドライン" in h), 1)
        why_col = next((k for k, h in enumerate(head) if "なぜ" in h), 2)
        for k, row in enumerate(t.rows):
            line = t.start + 2 + k

            def cell(c: int, row: list = row) -> str:
                return row[c].strip() if c < len(row) else ""

            out.append((line, lines[line] if line < len(lines) else "", cell(num_col), cell(why_col)))
    return out


def unnamed_redlines(path: str, text: str) -> list[dict]:
    """人へ委ねる判断がレッドラインの番号を名指していない行を指摘する（規則 unnamed_redline）。"""
    sections = [s for s in md.md_sections(text) if s.heading.title.startswith(REDLINE_TITLES)]
    if not sections:
        return []
    lines = text.splitlines()
    fenced = md.fenced_lines(text)
    flagged: dict[int, str] = {}
    for i, ln in enumerate(lines):
        if fenced[i] or not any(s.start <= i < s.end for s in sections):
            continue
        if DELEGATE.search(INLINE_CODE.sub("", ln)) and not REDLINE_ID.search(ln):
            flagged[i] = ln
    for i, ln, num, why in _left_table_rows(text, sections):
        if not REDLINE_ID.search(num) or not why.strip("`*_ "):
            flagged[i] = ln
    return [{"name": f"{path}:{i + 1}", "rule": "unnamed_redline", "text": ln.strip()} for i, ln in sorted(flagged.items())]


class Doc:
    """1 つの文書から、外部の調査の節（省いたか・候補の番号）と決定の 2 行と、名指しの無い委ねる行を読んだもの。"""

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.nums = name_numbers(path, requirements=True) or set()
        lines = text.splitlines()
        fenced = md.fenced_lines(text)
        prose = [(i, ln) for i, ln in enumerate(lines) if not fenced[i]]
        sections = md.md_sections(text)
        self.skipped, self.candidates = _read_survey(text, sections, prose)
        self.decisions = _read_decisions(sections, prose)
        self.unnamed = unnamed_redlines(path, text)


def survey_groups(docs: list[Doc]) -> list[list[Doc]]:
    """課題の番号を 1 つでも共有する文書どうしを、つながりをたどって 1 つのまとまりにする。番号の無い文書は 1 つで 1 まとまり。"""
    out: list[tuple[set[str], list[Doc]]] = []
    for d in docs:
        joined = [g for g in out if d.nums and g[0] & d.nums]
        nums = set(d.nums).union(*(g[0] for g in joined))
        members = [x for g in joined for x in g[1]] + [d]
        out = [g for g in out if g not in joined] + [(nums, members)]
    return [members for _, members in out]


def _terms(value: str) -> list[str]:
    return [t.strip("`*_ 。") for t in SPLIT.split(value.strip()) if t.strip("`*_ 。")]


def judge_decision(path: str, decision, known: set, skipped: bool) -> tuple[list[dict], dict]:
    """決定 1 件の指摘と件数を返す。候補の未知を作り方の有無より先に見る。"""
    n, method, cands = decision
    items: list[dict] = []
    counts = {"decisions": 1, "with_method": 0, "self_built": 0}
    ids = CANDIDATE_ID.findall(cands[2]) if cands else []
    if any(i not in known for i in ids):
        items.append({"name": f"{path}:{cands[0] + 1} 決定 {n}", "rule": "unknown_candidate", "text": cands[1].strip()})
    if method is None:
        return items, counts
    counts["with_method"] = 1
    line, text, value = method
    terms = _terms(value)
    if not terms or any(t not in METHODS for t in terms):
        items.append({"name": f"{path}:{line + 1} 決定 {n}", "rule": "unknown_method", "text": text.strip()})
    if SELF_BUILT in terms:
        counts["self_built"] = 1
        if not ids and not skipped:
            items.append({"name": f"{path}:{line + 1} 決定 {n}", "rule": "no_candidates", "text": text.strip()})
    return items, counts


def judge_group(group: list[Doc]) -> tuple[list[dict], dict]:
    skipped = any(d.skipped for d in group)
    known = set().union(*(d.candidates for d in group))
    items: list[dict] = []
    counts = {"decisions": 0, "with_method": 0, "self_built": 0}
    for d in group:
        for decision in d.decisions:
            got, c = judge_decision(d.path, decision, known, skipped)
            items += got
            for k, v in c.items():
                counts[k] += v
    return items, {**counts, "skipped": int(skipped)}


RESULTS_TITLE = "設計の結果"
LEGACY_TITLES = ("現状の構造", "変更後の構造")


def _result_files(head: Doc | None) -> tuple[set[str] | None, str]:
    """頭の設計文書の「設計の結果」から、扱いが「実装する」の行の触るファイルを集める。

    `legacy-refactor` の書式（「設計の結果」が無く「現状の構造」と「変更後の構造」がある）なら `(None, "")`。
    読めなければ `(None, 理由)`。"""
    if head is None:
        return None, "頭の設計文書が読んだ文書に無い"
    try:
        rows = parse_results(head.text, head.path)
    except DesignCellError as exc:
        return None, f"設計の結果の触るファイルのセルを読めない: {str(exc)[:300]}"
    if rows is not None:
        return {f for r in rows if r.kind == IMPLEMENT for f in r.files}, ""
    has = {t: md.section_named(head.text, t) is not None for t in (RESULTS_TITLE, *LEGACY_TITLES)}
    if not has[RESULTS_TITLE] and all(has[t] for t in LEGACY_TITLES):
        return None, ""
    return None, f"頭の設計文書 {head.path} の設計の結果の表を読めない"


def _head_of(detail: Doc, group: list[Doc]) -> Doc | None:
    """詳細設計の文書と同じ番号を持つ、種類が `design` の設計文書。"""
    return next((d for d in group if d.nums == detail.nums and doc_kinds.kind_of(d.path, placed=False) == "design"), None)


def detail_items(group: list[Doc], require: bool) -> list[dict]:
    """まとまりの詳細設計の文書ごとに `check_detail` を当て、`require` なら文書の欠けを指摘する。"""
    try:
        details = [d for d in group if is_detail(d.path)]
        if not details:
            if not require:
                return []
            nums = sorted(set().union(*(d.nums for d in group)), key=int)
            name = " ".join(f"#{n}" for n in nums) or group[0].path
            return [{"name": name, "rule": "detail_missing", "text": "詳細設計の文書（issue-<番号>-design-detail.md）が無い"}]
        items: list[dict] = []
        for d in details:
            files, why = _result_files(_head_of(d, group))
            items += check_detail(d.path, d.text, files, why)
        return items
    except (doc_kinds.DocKindsError, DetailTemplateError) as exc:
        raise StepError(str(exc), EXIT_UNREADABLE) from None


def cmd_check(a):
    root = git_root(a.root)
    if a.issue:
        require_repo(root)
        texts = find_closed(root, [str(n) for n in a.issue])
        if not texts:
            nums = ", ".join(f"#{n}" for n in a.issue)
            emit(result(TOOL, "stopped", f"設計文書が無い（{nums}）", metrics={"docs": 0}), EXIT_PRECONDITION)
    else:
        texts = read_files(root, a.file)
    docs = [Doc(p, t) for p, t in texts]
    items: list[dict] = []
    metrics = {"docs": len(docs), "decisions": 0, "with_method": 0, "self_built": 0, "skipped": 0}
    for g in survey_groups(docs):
        got, counts = judge_group(g)
        items += got
        for k, v in counts.items():
            metrics[k] += v
    unnamed = [it for d in docs for it in d.unnamed]
    items += unnamed
    metrics["unnamed_redline"] = len(unnamed)
    detail = [it for g in survey_groups(docs) for it in detail_items(g, a.require_detail)]
    items += detail
    metrics["detail_docs"] = sum(1 for d in docs if is_detail(d.path))
    for rule in ("detail_section", "detail_failure", "detail_reuse", "detail_file", "detail_missing"):
        metrics[rule] = sum(1 for it in detail if it["rule"] == rule)
    names = ", ".join(d.path for d in docs)
    if items:
        summary = f"文書 {len(docs)} 件（{names}）。決定の作り方と比べた候補・人へ残す判断のレッドライン・詳細設計に {len(items)} 件の指摘がある"
        emit(result(TOOL, "stopped", summary, items, metrics), EXIT_VIOLATION)
    summary = f"文書 {len(docs)} 件（{names}）。自前で作る決定 {metrics['self_built']} 件・人へ残す判断・詳細設計 {metrics['detail_docs']} 件に指摘なし"
    emit(result(TOOL, "ok", summary, items, metrics), EXIT_OK)


def build_parser():
    common = common_parser()
    ap = argparse.ArgumentParser(prog="design-survey.py", description=__doc__.splitlines()[0], parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", parents=[common], help="自前で作る決定に比べた候補があるか・人へ残す判断がレッドラインを名指すか")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", nargs="+", type=int, help="課題の番号（issues/ の文書を HEAD から読む）")
    g.add_argument("--file", nargs="+", help="読む文書のパス（worktree から読む）")
    p.add_argument("--require-detail", action="store_true", help="詳細設計の文書の無いまとまりを detail_missing で指摘する")
    p.set_defaults(func=cmd_check)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
