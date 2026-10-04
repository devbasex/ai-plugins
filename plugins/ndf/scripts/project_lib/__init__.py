"""プロジェクトの宣言の解析（project-decl.py）の中身（#1333）。設計は issues/issue-1333-design.md にある。

`model` だけが pydantic を使う（`check`・`write`・`schema` が `deps.require("schema")` の後に読む）。
ほかのモジュールは標準ライブラリと `git`・`gh` だけで動き、`measure` は uv の環境の外の `python3` でも通る。
"""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import project_decl  # noqa: E402
import repo  # noqa: E402

ANALYZER = 2  # 解析器の版。測る項目・入力のパスの表を変えたら上げる（上がると check が古いと判定する）
ITEM_KEYS = (
    "languages",
    "test",
    "test_duration",
    "ci",
    "services",
    "delivery",
    "issues",
    "checks",
    "ndf_policies",
    "instructions",
)
BRANCHES = "branches"  # P6。宣言では worktree.json の base_branch・production_branch へ書く
DECL_FILE = project_decl.DECL
WORKTREE_FILE = repo.WORKTREE_DECL
