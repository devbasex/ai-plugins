"""`ndf loop design-tests`: 設計に名前の出るテストが HEAD にあるかを判定する（#1241 の確認 (a)・#2118）。

    ndf loop design-tests (--issue <N>... | --design <パス>...)

- `--issue` は HEAD の `issues/issue-<N>-design*.md` を、`--design` は作業場所のファイルを読む
- すべてある（改名の後の名前で見つけたものを含む）なら ok。HEAD に無い名前があれば check_failed、確かめられない
  名前だけが残れば incomplete で止まる。設計文書が無ければ not_applicable
- 停止理由は名前ごとに items（`result` が missing など）と要約に全文で載せ、切り詰めない（#2118）

拾う規則と止まりにしないもの（採らなかった案・改名の設定）は `design_tests_decide` が持つ。LLM を呼ばず、読むだけ。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters.git import GitError
from ndf.contracts import result
from ndf.core import issue_docs
from ndf.domains.loop import design_tests_decide as dt

# 結果の tool は親のサブコマンド `ndf loop` の名前
TOOL = cli.tool_of(f"{__package__}.cli")

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


# パスを持たない名前を探す範囲（リポジトリ全体から設計などの文書を除く）
WHOLE = (".", ":(exclude)*.md")


def _query(shape: dt.Span) -> tuple[list[str], tuple[str, ...]]:
    """関数の名前の判定で git grep へ渡す名前と範囲。"""
    if shape.path is not None:
        return list(shape.names), (shape.path,)
    return list(shape.names[:1]), WHOLE


def _batched(grep: Grep, shapes: Sequence[dt.Span], files: set[str]) -> Grep:
    """`shapes` の名前を範囲ごとに 1 回の git grep で探しておき、判定の問い合わせに回答する `Grep`。

    名前ごとに git grep を打つと、パスの無い名前が N 個ある設計でリポジトリ全体の検索と Git の起動が N 回になる
    （PR 3537 の指摘）。先に探していない名前（改名を当てた名前など）の問い合わせだけ `grep` を打つ。範囲の検索が
    失敗すれば、その範囲の問い合わせは None（確かめられない）。
    """
    wanted: dict[tuple[str, ...], set[str]] = {}
    for shape in shapes:
        if shape.kind == dt.Kind.FILE or (shape.path is not None and shape.path not in files):
            continue
        names, pathspec = _query(shape)
        wanted.setdefault(pathspec, set()).update(names)
    held = {spec: grep(sorted(names), spec) for spec, names in wanted.items()}

    def run(names: Sequence[str], pathspec: Sequence[str]) -> Optional[set[str]]:
        spec = tuple(pathspec)
        if spec in held and set(names) <= wanted[spec]:
            got = held[spec]
            return None if got is None else got & set(names)
        return grep(names, pathspec)

    return run


def judge(shape: dt.Span, files: set[str], dirs: set[str], grep: Grep) -> dt.Verdict:
    """名前 1 つの判定（present / missing / unverified）。"""
    if shape.kind == dt.Kind.FILE:
        assert shape.path is not None
        here = (
            shape.path in files or shape.path in dirs or ("/" not in shape.path and any(f.rsplit("/", 1)[-1] == shape.path for f in files))
        )
        return dt.Verdict.PRESENT if here else dt.Verdict.MISSING
    if shape.path is not None and shape.path not in files:
        return dt.Verdict.MISSING
    names, pathspec = _query(shape)
    found = grep(names, pathspec)
    if found is None:
        return dt.Verdict.UNVERIFIED
    return dt.Verdict.PRESENT if set(names) <= found else dt.Verdict.MISSING


def judge_found(item: dt.Found, pairs: list[tuple[str, str]], files: set[str], dirs: set[str], grep: Grep) -> dict[str, Any]:
    """拾った名前 1 つの結果の行。HEAD に無ければ改名を当てた名前で探し直す（#2118）。"""
    row: dict[str, Any] = {"kind": item.shape.kind.value, "name": item.span, "doc": item.doc, "line": item.line, "section": item.section}
    got = judge(item.shape, files, dirs, grep)
    if got == dt.Verdict.MISSING:
        for other in dt.variants(item.span, pairs):
            shape = dt.classify(other, item.section)
            if shape is not None and shape.kind != dt.Kind.SHAPE and judge(shape, files, dirs, grep) == dt.Verdict.PRESENT:
                row["renamed_to"] = other
                got = dt.Verdict.RENAMED
                break
    row["result"] = got.value
    return row


def _designs(g: git.Git, root: Path, a: argparse.Namespace) -> list[tuple[str, str]]:
    if a.design:
        out = []
        for raw in a.design:
            path = Path(raw) if Path(raw).is_absolute() else root / raw
            out.append((raw, path.read_text(encoding="utf-8")))
        return out
    want = {int(n) for n in a.issue}
    rels = [r for r in g.run("ls-tree", "-r", "--name-only", "HEAD", "--", "issues").splitlines() if want & issue_docs.design_issues(r)]
    return [(rel, g.run("show", f"HEAD:{rel}")) for rel in rels]


def run(a: argparse.Namespace) -> dict[str, Any]:
    g = git.Git(a.cwd)
    try:
        root = Path(g.run("rev-parse", "--show-toplevel").strip())
        designs = _designs(g, root, a)
        files = set(g.run("ls-tree", "-r", "--name-only", "HEAD").splitlines())
    except (OSError, UnicodeDecodeError, GitError) as exc:
        return result.stopped(TOOL, "invalid", f"設計文書か git を読めない: {exc}")
    if not designs:
        return result.stopped(TOOL, "not_applicable", "設計文書が無い。設計に名前の出るテストの確かめは該当なし")
    dirs = {str(p) for f in files for p in PurePosixPath(f).parents if str(p) != "."}
    pairs = dt.renames(text for _, text in designs)
    found, skipped = _collect(designs)
    grep = _batched(_grep(g), [it.shape for it in found], files)
    rows = [judge_found(it, pairs, files, dirs, grep) for it in found]
    return _report(rows, [doc for doc, _ in designs], skipped)


def _collect(designs: list[tuple[str, str]]) -> tuple[list[dt.Found], int]:
    """全設計文書から拾った名前と、飛ばした数。同じ名前は文書をまたいで 1 度だけ拾う。"""
    seen: set[str] = set()
    found: list[dt.Found] = []
    skipped = 0
    for doc, text in designs:
        got, sk = dt.extract(doc, text, seen)
        found += got
        skipped += sk
    return found, skipped


def _report(rows: list[dict[str, Any]], docs: list[str], skipped: int) -> dict[str, Any]:
    """名前ごとの判定の行から結果の JSON を組む。"""
    kind, summary = dt.outcome(r["result"] for r in rows)
    summary = f"設計文書 {len(docs)} 件（{', '.join(docs)}）。{summary}"
    metrics = {
        "designs": len(docs),
        "names": len(rows),
        "skipped": skipped,
        **{k.value: sum(r["result"] == k for r in rows) for k in (dt.Verdict.MISSING, dt.Verdict.UNVERIFIED, dt.Verdict.RENAMED)},
    }
    if kind == "ok":
        return result.result(TOOL, result.OK, summary, rows, metrics)
    left = [r for r in rows if r["result"] in (dt.Verdict.MISSING, dt.Verdict.UNVERIFIED)]
    detail = summary + ": " + "、".join(f"{r['name']}（{r['doc']} の {r['line']} 行・{r['result']}）" for r in left)
    extra: dict[str, Any] = {}
    if kind == "incomplete":
        extra["gaps"] = [
            {"kind": r["kind"], "name": r["name"], "reason": f"{r['doc']} の {r['line']} 行を確かめられなかった"} for r in left
        ]
    return result.stopped(TOOL, kind, detail, items=rows, metrics=metrics, **extra)


def add_parser(sub: Any) -> None:
    """`ndf loop` のサブコマンドの並びへ `design-tests` を足す。"""
    p = sub.add_parser("design-tests", help="設計に名前の出るテストが HEAD にあるかを判定する")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--issue", type=int, nargs="+", help="HEAD の issues/issue-<N>-design*.md を読む")
    where.add_argument("--design", nargs="+", help="作業場所の設計文書のパス")
    p.set_defaults(handler=run)
