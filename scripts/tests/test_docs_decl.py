"""宣言の漏れの検査（#2925 の決定 2）。テストのソースが文書のファイルのパスを書いているのに、ai-plugins の宣言
（`.ndf/project.json` の `docs.tested`）でそのテストとの対応を持たないものを落とす。

文書だけの Pull Request では、宣言の対応のテストだけが走る。対応から漏れたテストは、文書を変えても走らず、
ベースブランチへの push の全体テストで初めて見つかる。ここでは、テストのソースに追跡している文書のパス
（`docs/a.md` の形か、`"docs" / "a.md"` の形）が現れるかを見る。根の直下のファイル（`README.md` など）は、
一時のリポジトリに作る同じ名前のファイルと見分けられないため見ない。パスを組み立てて読むテストはここで見つからない。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
from ndf_wrappers import require  # noqa: E402

require("pathmatch")
import diff_kinds  # noqa: E402
from pathmatch import path_matches  # noqa: E402


def tracked() -> list[str]:
    return subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()


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
        source = (REPO / test).read_text(encoding="utf-8", errors="replace")
        out += [f"{test}: {doc}" for doc in docs if mentions(source, doc) and not covered(decl, doc, test)]
    return out


def test_every_test_that_names_a_doc_is_declared():
    missing = leaks(diff_kinds.load_docs_decl(REPO), tracked())
    assert not missing, "宣言 docs.tested に無い文書とテストの組:\n" + "\n".join(missing)


def test_the_check_finds_a_dropped_test(tmp_path):
    decl = diff_kinds.load_docs_decl(REPO)
    row = next(r for r in decl.tested if "plugins/ndf/scripts/tests/test_glossary.py" in r.tests)
    dropped = diff_kinds.DocsDecl(decl.files, decl.behavior, tuple(r for r in decl.tested if r is not row))
    assert any(m.startswith("plugins/ndf/scripts/tests/test_glossary.py: ") for m in leaks(dropped, tracked()))
