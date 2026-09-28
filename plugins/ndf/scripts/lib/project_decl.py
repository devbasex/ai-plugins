"""プロジェクトの宣言（`.ndf/project.json`）の読み取り（#1333）。Skill とスクリプトが宣言を読む唯一の入口。

宣言はメインディレクトリの `.ndf/` にあり（`project-decl.py write` の置き場）、無ければ `root` の `.ndf/` を読む。
読めない・無いときは `{}` を返し、例外を上げない。形の検証は `project-decl.py check` が持つ。標準ライブラリだけで書く。
"""

from __future__ import annotations

from pathlib import Path

import repo

DECL = Path(".ndf") / "project.json"


def read_project_decl_checked(root) -> tuple[dict, str | None]:
    """宣言の中身と、読めなかった理由。無ければ `({}, None)`、壊れていれば `({}, 理由)`（理由にファイルを書く）。"""
    main = repo.main_dir(root) if root else None
    for base in dict.fromkeys(p for p in (main, Path(root) if root else None) if p):
        f = Path(base) / DECL
        if f.is_file():
            return repo.read_json_object(f, DECL)
    return {}, None


def read_project_decl(root) -> dict:
    """宣言の中身。読めなければ `{}`。"""
    return read_project_decl_checked(root)[0]


def policy(root, name: str) -> bool:
    """`ndf_policies.<name>` が `true` か。キーが無い・不明・読めないときは掛けない（`False`。決定 8）。"""
    v = read_project_decl(root).get("ndf_policies")
    return isinstance(v, dict) and v.get(name) is True
