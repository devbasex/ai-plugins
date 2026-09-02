"""版数の書式が 1 か所にあることと、その書式の振る舞いを固定する。

書式を 2 か所に持つと、片方だけを直したときに一方の検査だけが新しい書式を読める状態に
なる。ここでは、共有の定義が拾う値と、2 つの検査が自分で書式を持っていないことの両方を
確かめる。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import validate_manifests_helpers as helpers
from doc_staleness_helpers import CHECKER, REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts/lib"))
from version_pattern import VERSION_IN_DESCRIPTION, VERSION_VALUE  # noqa: E402

VALIDATE = REPO_ROOT / "scripts/validate-runtime-plugins.sh"
SHARED = REPO_ROOT / "scripts/lib/version_pattern.py"

# 版数の数字 3 つを直接書いた正規表現。どちらの検査にも残っていないことを確かめる。
INLINE_PATTERN = r"\d+\.\d+\.\d+"


def test_the_shared_definition_exists() -> None:
    assert SHARED.is_file()


@pytest.mark.parametrize("value", ["9.7.0", "9.7.0-dev.1", "9.7.0-rc.2", "10.0.11"])
def test_the_shared_format_reads_a_version(value: str) -> None:
    assert VERSION_VALUE.fullmatch(value)


@pytest.mark.parametrize("value", ["1.0", "9.7", "v9.7.0", "9.7.0.1"])
def test_the_shared_format_rejects_a_value_that_is_not_a_version(value: str) -> None:
    assert VERSION_VALUE.fullmatch(value) is None


@pytest.mark.parametrize("value", ["9.7.0", "9.7.0-dev.1"])
def test_the_shared_format_reads_the_version_in_a_description(value: str) -> None:
    found = VERSION_IN_DESCRIPTION.search(f"NDF plugin (v{value}): 33 focused skills.")
    assert found and found.group(1) == value


@pytest.mark.parametrize("path", [CHECKER, VALIDATE])
def test_neither_checker_writes_the_version_format_itself(path: Path) -> None:
    """書式を書き写した行が残っていないことを、ファイルを読んで確かめる。"""
    assert INLINE_PATTERN not in path.read_text(encoding="utf-8")


def test_the_manifest_checker_stops_when_the_shared_definition_is_missing(tmp_path: Path) -> None:
    """読み込めないときに素通りしない。書式が無いまま続けると突き合わせが全て素通りする。"""
    root = helpers.build_tree(tmp_path)
    (root / "scripts/lib/version_pattern.py").unlink()
    result = subprocess.run(
        [sys.executable, "-", str(root), helpers.FAMILY],
        input=helpers.extract_checker(),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "version_pattern" in result.stdout + result.stderr


def test_the_document_checker_stops_when_the_shared_definition_is_missing(tmp_path: Path) -> None:
    """説明文書の検査も、自分の隣に定義が無ければ止まる。"""
    copied = tmp_path / "check-doc-staleness.py"
    copied.write_text(CHECKER.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(copied), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "version_pattern" in result.stdout + result.stderr


def test_the_manifest_checker_reads_a_prerelease_version(tmp_path: Path) -> None:
    """共有の定義を経由しても、接尾辞付きの版数がこれまでどおり通る。"""
    root = helpers.build_tree(tmp_path, version="9.8.0-dev.3")
    result = subprocess.run(
        [sys.executable, "-", str(root), helpers.FAMILY],
        input=helpers.extract_checker(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_shared_definition_is_copied_from_the_real_file(tmp_path: Path) -> None:
    """木へ置くのは実物の複製である。テスト用に書式を書き写さない。"""
    root = helpers.build_tree(tmp_path)
    assert (root / "scripts/lib/version_pattern.py").read_text(encoding="utf-8") == SHARED.read_text(
        encoding="utf-8"
    )
    json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
