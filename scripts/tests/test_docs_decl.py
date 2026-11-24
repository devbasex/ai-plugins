"""宣言の漏れの検査（#2925 の決定 2）。テストのソースが文書のファイルのパスを書いているのに、ai-plugins の宣言
（`.ndf/project.json` の `docs.tested`）でそのテストとの対応を持たないものを落とす。

文書だけの Pull Request では、宣言の対応のテストだけが走る。対応から漏れたテストは、文書を変えても走らず、
ベースブランチへの push の全体テストで初めて見つかる。ここでは、テストのソースに追跡している文書のパス
（`docs/a.md` の形か、`"docs" / "a.md"` の形）が現れるかを見る。根の直下のファイル（`README.md` など）は、
一時のリポジトリに作る同じ名前のファイルと見分けられないため見ない。パスを組み立てて読むテストはここで見つからない。
注記（`#` のコメントと docstring）に書いたパスは出典の参照で、テストが文書を読む依存ではないので数えない。
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
from ndf_wrappers import require  # noqa: E402

require("pathmatch")
import diff_kinds  # noqa: E402
from pathmatch import path_matches  # noqa: E402


def tracked() -> list[str]:
    return subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()


def code_of(source: str) -> str:
    """注記（`#` のコメントと docstring）を除いたソース。字句か構文で読めなければ元のまま返す。"""
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError):  # 読めないソースは注記を外さずに全文で見る
        return source
    lines = source.splitlines(keepends=True)
    drop = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                drop.update(range(body[0].lineno, body[0].end_lineno + 1))
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            line = lines[row - 1]
            lines[row - 1] = line[:col] + ("\n" if line.endswith("\n") else "")
    return "".join("" if i + 1 in drop else line for i, line in enumerate(lines))


def mentions(source: str, doc: str) -> bool:
    joined = " / ".join(f'"{part}"' for part in doc.split("/"))
    return doc in source or joined in source


def covered(decl: diff_kinds.DocsDecl, doc: str, test: str) -> bool:
    return any(
        path_matches(doc, row.paths) and any(test == t or test.startswith(t.rstrip("/") + "/") for t in row.tests) for row in decl.tested
    )


def leaks(decl: diff_kinds.DocsDecl, files: list[str]) -> list[str]:
    docs = [f for f in files if "/" in f and path_matches(f, decl.files)]
    tests = [f for f in files if Path(f).name.startswith("test_") and f.endswith(".py")]
    out = []
    for test in tests:
        source = code_of((REPO / test).read_text(encoding="utf-8", errors="replace"))
        out += [f"{test}: {doc}" for doc in docs if mentions(source, doc) and not covered(decl, doc, test)]
    return out


def test_every_test_that_names_a_doc_is_declared():
    missing = leaks(diff_kinds.load_docs_decl(REPO), tracked())
    assert not missing, "宣言 docs.tested に無い文書とテストの組:\n" + "\n".join(missing)


def test_the_check_finds_a_dropped_test(tmp_path):
    decl = diff_kinds.load_docs_decl(REPO)
    row = next(r for r in decl.tested if "scripts/tests/test_notion_writing_backticks.py" in r.tests)
    dropped = diff_kinds.DocsDecl(decl.files, decl.behavior, tuple(r for r in decl.tested if r is not row))
    assert any(m.startswith("scripts/tests/test_notion_writing_backticks.py: ") for m in leaks(dropped, tracked()))


def test_a_doc_named_only_in_notes_is_not_a_dependency():
    source = '"""出典は docs/specifications/a.md の決定 1。"""\n# docs/specifications/a.md\nx = 1\n'
    assert not mentions(code_of(source), "docs/specifications/a.md")
    assert mentions(code_of(source + 'p = "docs/specifications/a.md"\n'), "docs/specifications/a.md")
