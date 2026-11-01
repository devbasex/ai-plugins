"""プロジェクトの宣言（`.ndf/project.json`）の読み取り（#1333）。Skill とスクリプトが宣言を読む唯一の入口。

宣言は呼ばれた木（`root`）の `.ndf/` → メインディレクトリの順に読む（置き場は `decl.setting_path`。Pull Request が
変えた宣言をその worktree で使う。#2317・#1709）。ただし本番マージの判定に使う `delivery` はメインディレクトリ → 呼ばれた木の
順に読む（契約の `keys.delivery.place` が `main`。Pull Request の変更だけで承認ゲート 2 を緩められないようにする。#2363）。
`project-decl.py write` はメインディレクトリへ書く。
読めない・無いときは `{}` を返し、例外を上げない。形の検証は `project-decl.py check` が持つ。標準ライブラリだけで書く。
"""

from __future__ import annotations

from pathlib import Path

import decl

DECL = Path(".ndf") / "project.json"


def read_project_decl_checked(root) -> tuple[dict, str | None]:
    """宣言の中身と、読めなかった理由。無ければ `({}, None)`、壊れていれば `({}, 理由)`（理由にファイルを書く）。"""
    return decl.read_json_setting(root, DECL.name)


def read_project_decl(root) -> dict:
    """宣言の中身。読めなければ `{}`。"""
    return read_project_decl_checked(root)[0]


def policy(root, name: str) -> bool:
    """`ndf_policies.<name>` が `true` か。キーが無い・不明・読めないときは掛けない（`False`。決定 8）。"""
    v = read_project_decl(root).get("ndf_policies")
    return isinstance(v, dict) and v.get(name) is True
