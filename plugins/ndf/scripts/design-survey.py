#!/usr/bin/env python3
"""design-survey.py: 設計文書の自前で作る決定に、外部の調査で比べた候補があるかを確かめる（#2519）。LLM を呼ばず、読むだけ。

    python3 design-survey.py check (--issue N... | --file PATH...) [--root DIR]

- `--issue`: HEAD の `issues/` から、名前の番号の並びにその番号を含む `issue-<番号>-design*.md` と
  `issue-<番号>-requirements.md` を読む。読んだ文書の名前のほかの番号もたどり、新しい番号が出なくなるまで読み足す
- `--file`: 渡したパスを worktree から読む（`light` の受け入れ条件のファイル・`operation` の実行の記録）

課題の番号を 1 つでも共有する文書どうしを 1 つのまとまりにし、まとまりごとに判定する（コードブロックの中は読まない）。

- `unknown_method`: 決定の `作り方:` の値に「自前で作る」「ライブラリやサービスを使う」「標準に倣う」以外の語がある
- `no_candidates`: 作り方に「自前で作る」を含む決定の `比べた候補:` に番号が無い（まとまりの外部の調査を `省く:` で
  省いたときは除く）
- `unknown_candidate`: `比べた候補:` の番号が、まとまりの外部の調査の表の 1 列目（`STD<数>` / `PRD<数>` / `LIB<数>`）に無い

終了コードは 0 = 指摘なし（作り方の行が 0 件の文書を含む）/ 1 = 指摘あり / 2 = 文書か git を読めない /
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
from design_docs import find_closed, name_numbers, read_files, require_repo  # noqa: E402
from step_result import (  # noqa: E402
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_VIOLATION,
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


class Doc:
    """1 つの文書から、外部の調査の節（省いたか・候補の番号）と決定の 2 行を読んだもの。"""

    def __init__(self, path: str, text: str):
        self.path = path
        self.nums = name_numbers(path, requirements=True) or set()
        lines = text.splitlines()
        fenced = md.fenced_lines(text)
        prose = [(i, ln) for i, ln in enumerate(lines) if not fenced[i]]
        sections = md.md_sections(text)
        surveys = [s for s in sections if s.heading.title.startswith(SURVEY_TITLE)]
        self.skipped = any(s.start <= i < s.end and SKIP_LINE.match(ln) for s in surveys for i, ln in prose)
        self.candidates: set[str] = set()
        for t in md.tables(text):
            if any(s.start <= t.start < s.end for s in surveys):
                for row in t.rows:
                    cell = row[0].strip().strip("`*_ ") if row else ""
                    if CANDIDATE_ID.fullmatch(cell):
                        self.candidates.add(cell)
        self.decisions = []
        for s in sections:
            m = DECISION_TITLE.match(s.heading.title)
            if not m:
                continue
            body = [(i, ln) for i, ln in prose if s.start <= i < s.end]
            method = next(((i, ln, mm.group("v")) for i, ln in body if (mm := METHOD_LINE.match(ln))), None)
            cands = next(((i, ln, mm.group("v")) for i, ln in body if (mm := CANDIDATES_LINE.match(ln))), None)
            self.decisions.append((m.group("n"), s.heading.line, method, cands))


def groups(docs: list[Doc]) -> list[list[Doc]]:
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


def judge(group: list[Doc]) -> tuple[list[dict], dict]:
    skipped = any(d.skipped for d in group)
    known = set().union(*(d.candidates for d in group))
    items: list[dict] = []
    counts = {"decisions": 0, "with_method": 0, "self_built": 0}
    for d in group:
        for n, head, method, cands in d.decisions:
            counts["decisions"] += 1
            ids = CANDIDATE_ID.findall(cands[2]) if cands else []
            unknown = [i for i in ids if i not in known]
            if unknown:
                items.append({"name": f"{d.path}:{cands[0] + 1} 決定 {n}", "rule": "unknown_candidate",
                              "text": cands[1].strip()})  # fmt: skip
            if method is None:
                continue
            counts["with_method"] += 1
            line, text, value = method
            terms = _terms(value)
            if not terms or any(t not in METHODS for t in terms):
                items.append({"name": f"{d.path}:{line + 1} 決定 {n}", "rule": "unknown_method", "text": text.strip()})
            if SELF_BUILT in terms:
                counts["self_built"] += 1
                if not ids and not skipped:
                    items.append({"name": f"{d.path}:{line + 1} 決定 {n}", "rule": "no_candidates", "text": text.strip()})
    return items, {**counts, "skipped": int(skipped)}


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
    for g in groups(docs):
        got, counts = judge(g)
        items += got
        for k, v in counts.items():
            metrics[k] += v
    names = ", ".join(d.path for d in docs)
    if items:
        summary = f"文書 {len(docs)} 件（{names}）。決定の作り方と比べた候補に {len(items)} 件の指摘がある"
        emit(result(TOOL, "stopped", summary, items, metrics), EXIT_VIOLATION)
    summary = f"文書 {len(docs)} 件（{names}）。自前で作る決定 {metrics['self_built']} 件に指摘なし"
    emit(result(TOOL, "ok", summary, items, metrics), EXIT_OK)


def build_parser():
    common = common_parser()
    ap = argparse.ArgumentParser(prog="design-survey.py", description=__doc__.splitlines()[0], parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", parents=[common], help="自前で作る決定に比べた候補があるか")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", nargs="+", type=int, help="課題の番号（issues/ の文書を HEAD から読む）")
    g.add_argument("--file", nargs="+", help="読む文書のパス（worktree から読む）")
    p.set_defaults(func=cmd_check)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
