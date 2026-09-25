"""コマンドを隠した PATH を作る補助 `path_with` のテスト（#555）。

読めないディレクトリが PATH にあると、補助が例外で落ちてテスト一式が赤くなっていた。
環境に依存しないよう、読めないディレクトリは一時ディレクトリの権限で作る。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from workflow_helpers import path_with

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root は権限の検査を受けないため、読めないディレクトリを作れない",
)


def command(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    return target


@pytest.fixture()
def restore_modes():
    """権限を落としたディレクトリを後片付けの前に戻す。戻さないと tmp_path を消せない。"""
    changed: list[Path] = []
    yield changed
    for directory in changed:
        directory.chmod(0o755)


def lock(directory: Path, mode: int, changed: list[Path]) -> None:
    directory.chmod(mode)
    changed.append(directory)


def test_an_unreadable_directory_does_not_break_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_modes: list[Path],
) -> None:
    """権限 000 のディレクトリと、祖先を辿れないディレクトリ（`/root/.local/bin` の形）。"""
    tools = tmp_path / "tools"
    command(tools, "jq")
    command(tools, "keep")
    closed = tmp_path / "closed"
    closed.mkdir()
    lock(closed, 0o000, restore_modes)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    lock(home, 0o000, restore_modes)
    monkeypatch.setenv("PATH", os.pathsep.join([str(closed), str(home / ".local" / "bin"), str(tools)]))

    path = path_with(tmp_path / "bin", without=("jq",))

    assert shutil.which("jq", path=path) is None
    assert shutil.which("keep", path=path) is not None


def test_every_copy_of_the_command_is_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ名前が複数の場所にあっても、どれも見えなくする。同居する他のコマンドは残す。"""
    first, second = tmp_path / "first", tmp_path / "second"
    command(first, "jq")
    command(second, "jq")
    command(second, "keep")
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second), str(first)]))

    path = path_with(tmp_path / "bin", without=("jq",))

    assert shutil.which("jq", path=path) is None
    assert shutil.which("keep", path=path) is not None


def test_calling_again_with_the_same_directory_does_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ `bin_dir` で隠す対象を変えて呼び直しても、前の複製に引きずられない。"""
    tools = tmp_path / "tools"
    command(tools, "jq")
    command(tools, "awk")
    monkeypatch.setenv("PATH", str(tools))

    first = path_with(tmp_path / "bin", without=("jq",))
    second = path_with(tmp_path / "bin", without=("awk",))

    assert shutil.which("jq", path=first) is None
    assert shutil.which("awk", path=first) is not None
    assert shutil.which("awk", path=second) is None
    assert shutil.which("jq", path=second) is not None


def test_a_relative_path_entry_keeps_working_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATH の相対パスから写したリンクも、複製の場所から辿れる。"""
    command(tmp_path / "tools", "jq")
    command(tmp_path / "tools", "keep")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "tools")

    path = path_with(tmp_path / "bin", without=("jq",))

    assert shutil.which("jq", path=path) is None
    assert shutil.which("keep", path=path) is not None


def test_a_command_that_cannot_be_hidden_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_modes: list[Path],
) -> None:
    """一覧できないが実行はできるディレクトリに対象があれば、隠せないので落とす。"""
    sealed = tmp_path / "sealed"
    command(sealed, "jq")
    lock(sealed, 0o111, restore_modes)
    monkeypatch.setenv("PATH", str(sealed))

    with pytest.raises(PermissionError):
        path_with(tmp_path / "bin", without=("jq",))
