"""テストの宣言（`.ndf/project.json` の `test.suites`）と設定の置き場を読む。

設定とテストの宣言は、呼ばれた位置から根へ向かって最初に見つかる `.ndf/` から読む
（worktree はメインディレクトリの下に置くので、worktree に無ければメインディレクトリの値が効く）。
標準ライブラリと `ndf.identity`・`ndf.testing.weight` だけで書く（hook の guard も読む）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndf import identity
from ndf.testing import weight

PROJECT_FILE = "project.json"


def find_setting(cwd: Path, name: str) -> Path | None:
    """`cwd` から根へ向かって `.ndf/<name>` を探す。"""
    for base in (cwd.resolve(), *cwd.resolve().parents):
        candidate = base / identity.FIXED_WORDS["declaration_dir"] / name
        if candidate.is_file():
            return candidate
    return None


def test_section(cwd: Path) -> dict[str, object]:
    """宣言（`.ndf/project.json`）の `test` の節。宣言が無い・読めない・形が違えば空の辞書。"""
    path = find_setting(cwd, PROJECT_FILE)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない宣言は節が無いとして扱う
        return {}
    test = data.get("test") if isinstance(data, dict) else None
    return test if isinstance(test, dict) else {}


def load_suites(cwd: Path) -> list[dict[str, object]]:
    """宣言（`.ndf/project.json` の `test.suites`）のうちテストの suite。読めなければ空の一覧。"""
    suites = test_section(cwd).get("suites")
    if not isinstance(suites, list):
        return []
    return [s for s in suites if isinstance(s, dict) and weight.is_test_suite(s)]
