"""`ndf loop design-tests`: 設計に名前の出るテストが HEAD にあるかを判定する（#1241 の確認 (a)・#2118）。

    ndf loop design-tests (--issue <N>... | --design <パス>...)

- `--issue` は HEAD の `issues/issue-<N>-design*.md` を、`--design` は作業場所のファイルを読む
- すべてある（改名の後の名前で見つけたものを含む）なら ok。HEAD に無い名前があれば check_failed、確かめられない
  名前だけが残れば incomplete で止まる。設計文書が無ければ not_applicable
- 止まった理由は名前ごとに items（`result` が missing など）と要約に全文で載せ、切り詰めない（#2118）

拾う規則と止まりにしないもの（採らなかった案・改名の宣言）は `design_tests_decide` が持つ。LLM を呼ばず、読むだけ。
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters.git import GitError
from ndf.contracts import result
from ndf.domains.loop import design_tests_decide as dt

# 結果の tool は親の副命令 `ndf loop` の名前
TOOL = cli.tool_of(f"{__package__}.cli")
DESIGN_FILE = re.compile(r"^issues/issue-(\d+)-design[^/]*\.md$")
NOT_APPLICABLE = "not_applicable"
INVALID = "invalid"

Grep = Callable[[Sequence[str], Sequence[str]], Optional[set[str]]]


def _grep(g: git.Git) -> Grep:
    def run(names: Sequence[str], pathspec: Sequence[str]) -> Optional[set[str]]:
        """HEAD の `pathspec` に語として現れる `names`。git grep が失敗すれば None（確かめられない）。"""
        if not names:
            return set()
        args = ["grep", "-w", "-F", "-o", "-h", "-I"]
        for n in names:
            args += ["-e", n]
        done = g.attempt(*args, "HEAD", "--", *pathspec)
        if done.returncode not in (0, 1):
            return None
        return {ln.strip() for ln in done.stdout.splitlines() if ln.strip()}

    return run


def judge(shape: dt.Span, files: set[str], dirs: set[str], grep: Grep) -> str:
    """名前 1 つの判定（present / missing / unverified）。"""
    if shape.kind == dt.TEST_FILE_KIND:
        assert shape.path is not None
        here = (
            shape.path in files or shape.path in dirs or ("/" not in shape.path and any(f.rsplit("/", 1)[-1] == shape.path for f in files))
        )
        return dt.PRESENT if here else dt.MISSING
    if shape.path is not None and shape.path not in files:
        return dt.MISSING
    names = list(shape.names) if shape.path is not None else list(shape.names[:1])
    found = grep(names, [shape.path] if shape.path is not None else [".", ":(exclude)*.md"])
    if found is None:
        return dt.UNVERIFIED
    return dt.PRESENT if set(names) <= found else dt.MISSING


def judge_found(item: dt.Found, pairs: list[tuple[str, str]], files: set[str], dirs: set[str], grep: Grep) -> dict[str, Any]:
    """拾った名前 1 つの結果の行。HEAD に無ければ改名を当てた名前で探し直す（#2118）。"""
    row: dict[str, Any] = {"kind": item.shape.kind, "name": item.span, "doc": item.doc, "line": item.line, "section": item.section}
    got = judge(item.shape, files, dirs, grep)
    if got == dt.MISSING:
        for other in dt.variants(item.span, pairs):
            shape = dt.classify(other, item.section)
            if shape is not None and shape.kind != dt.SKIPPED and judge(shape, files, dirs, grep) == dt.PRESENT:
                row["renamed_to"] = other
                got = dt.RENAMED
                break
    row["result"] = got
    return row


def _designs(g: git.Git, root: Path, a: argparse.Namespace) -> list[tuple[str, str]]:
    if a.design:
        out = []
        for raw in a.design:
            path = Path(raw) if Path(raw).is_absolute() else root / raw
            out.append((raw, path.read_text(encoding="utf-8")))
        return out
    want = {int(n) for n in a.issue}
    rels = [
        r
        for r in g.run("ls-tree", "-r", "--name-only", "HEAD", "--", "issues").splitlines()
        if (m := DESIGN_FILE.match(r)) and int(m.group(1)) in want
    ]
    return [(rel, g.run("show", f"HEAD:{rel}")) for rel in rels]


def run(a: argparse.Namespace) -> dict[str, Any]:
    g = git.Git(a.cwd)
    try:
        root = Path(g.run("rev-parse", "--show-toplevel").strip())
        designs = _designs(g, root, a)
        files = set(g.run("ls-tree", "-r", "--name-only", "HEAD").splitlines())
    except (OSError, UnicodeDecodeError, GitError) as exc:
        return result.stopped(TOOL, INVALID, f"設計文書か git を読めない: {exc}")
    if not designs:
        return result.stopped(TOOL, NOT_APPLICABLE, "設計文書が無い。設計に名前の出るテストの確かめは該当なし")
    dirs = {str(p) for f in files for p in PurePosixPath(f).parents if str(p) != "."}
    pairs = dt.renames(text for _, text in designs)
    seen: set[str] = set()
    found: list[dt.Found] = []
    skipped = 0
    for doc, text in designs:
        got, sk = dt.extract(doc, text, seen)
        found += got
        skipped += sk
    rows = [judge_found(it, pairs, files, dirs, _grep(g)) for it in found]
    kind, summary = dt.outcome(r["result"] for r in rows)
    summary = f"設計文書 {len(designs)} 件（{', '.join(doc for doc, _ in designs)}）。{summary}"
    metrics = {
        "designs": len(designs),
        "names": len(rows),
        "skipped": skipped,
        **{k: sum(r["result"] == k for r in rows) for k in (dt.MISSING, dt.UNVERIFIED, dt.RENAMED)},
    }
    if kind == "ok":
        return result.result(TOOL, result.OK, summary, rows, metrics)
    left = [r for r in rows if r["result"] in (dt.MISSING, dt.UNVERIFIED)]
    detail = summary + ": " + "、".join(f"{r['name']}（{r['doc']} の {r['line']} 行・{r['result']}）" for r in left)
    extra: dict[str, Any] = {}
    if kind == "incomplete":
        extra["gaps"] = [
            {"kind": r["kind"], "name": r["name"], "reason": f"{r['doc']} の {r['line']} 行を確かめられなかった"} for r in left
        ]
    return result.stopped(TOOL, kind, detail, items=rows, metrics=metrics, **extra)


def add_parser(sub: Any) -> None:
    """`ndf loop` の副命令の並びへ `design-tests` を足す。"""
    p = sub.add_parser("design-tests", help="設計に名前の出るテストが HEAD にあるかを判定する")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--issue", type=int, nargs="+", help="HEAD の issues/issue-<N>-design*.md を読む")
    where.add_argument("--design", nargs="+", help="作業場所の設計文書のパス")
    p.set_defaults(handler=run)
