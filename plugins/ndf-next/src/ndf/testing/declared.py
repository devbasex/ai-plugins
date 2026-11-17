"""テストの設定（`.ndf/project.json` の `test.suites`）と設定の保存先を読む。

設定とテストの設定は、呼ばれた位置から根へ向かって最初に見つかる `.ndf/` から読む
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
        candidate = base / identity.FIXED_WORDS["ndf_settings_dir"] / name
        if candidate.is_file():
            return candidate
    return None


class NdfSettingsError(Exception):
    """設定のファイルを読めない（JSON として読めないか、ファイルを開けない）。`path` と元の例外 `cause` を持つ。"""

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path
        self.cause = cause


def read_ndf_settings(cwd: Path) -> tuple[Path, object] | None:
    """`cwd` から根へ探した設定（`.ndf/project.json`）のパスと JSON の値。見つからなければ None。

    見つかったが読めなければ `NdfSettingsError`。値の形は確かめない（呼び手ごとに扱いが違う）。
    """
    path = find_setting(cwd, PROJECT_FILE)
    if path is None:
        return None
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NdfSettingsError(path, exc) from exc


def test_section(cwd: Path) -> dict[str, object]:
    """設定（`.ndf/project.json`）の `test` のセクション。設定が無い・読めない・形が違えば空の辞書。"""
    try:
        found = read_ndf_settings(cwd)
    except NdfSettingsError:  # 読めない設定はセクションが無いとして扱う
        return {}
    data = found[1] if found is not None else None
    test = data.get("test") if isinstance(data, dict) else None
    return test if isinstance(test, dict) else {}


def load_suites(cwd: Path) -> list[dict[str, object]]:
    """設定（`.ndf/project.json` の `test.suites`）のうちテストの suite。読めなければ空の一覧。"""
    suites = test_section(cwd).get("suites")
    if not isinstance(suites, list):
        return []
    return [s for s in suites if isinstance(s, dict) and weight.is_test_suite(s)]
