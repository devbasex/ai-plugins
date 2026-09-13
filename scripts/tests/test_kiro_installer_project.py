"""Kiro のインストーラが `--project` の誤った値を案内つきで止めることを固定する（#415）。

変更前は `cd` の裸のエラーと終了コード 1 で終わり、何を直せばよいかが読み取れなかった。
ここで見るのは、**誤りの種類ごとに文言が分かれること**・**存在しないパスにだけ
`mkdir -p` の案内が付くこと**・**案内のパスが bash の語 1 つとして読み戻せること**・
**正しい値では振る舞いが変わらないこと**の 4 つである。

終了コードはどちらの誤りも 2 であるため、文言まで見ないと 2 つの誤りを区別できない。
正しい値の確認は `--dry-run` で行い、利用者の環境へ書き込まない。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "plugins" / "ndf" / "dev.kiro" / "install.sh"


def run(*args: str, home: Path) -> subprocess.CompletedProcess:
    # `--scope global` は HOME の下を導入先にする。誤って書き込んでも利用者の HOME に
    # 届かないよう、一時ディレクトリを HOME として渡す。
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def hint_words(stderr: str) -> list[str]:
    """`HINT: mkdir -p ` に続く部分を bash で読み戻し、語の並びを返す。

    `shlex.split` は `$'...'` の形を解さないため使わない（タブを含むパスで `$` が残る）。
    """
    prefix = "HINT: mkdir -p "
    lines = [line for line in stderr.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, stderr
    quoted = lines[0][len(prefix):]
    out = subprocess.run(
        ["bash", "-c", 'eval "set -- $1"; printf "%s\\0" "$@"', "_", quoted],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("\0")[:-1]


def assert_no_bare_cd_error(proc: subprocess.CompletedProcess) -> None:
    assert "cd:" not in proc.stdout + proc.stderr


@pytest.mark.parametrize("name", ["missing", "my project", "tab\tx"])
def test_missing_path_stops_with_error_and_hint(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    proc = run("--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    lines = proc.stderr.splitlines()
    assert lines[0] == f"ERROR: --project points at a path that does not exist: {target}"
    assert lines[1].startswith("HINT: mkdir -p ")
    assert len(lines) == 2
    # 案内のパスは、空白やタブを含んでも語 1 つとして渡したパスへ戻る
    assert hint_words(proc.stderr) == [str(target)]
    assert not target.exists()
    assert_no_bare_cd_error(proc)


def test_file_path_stops_without_hint(tmp_path: Path) -> None:
    target = tmp_path / "afile"
    target.write_text("", encoding="utf-8")
    proc = run("--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    assert proc.stderr.splitlines() == [
        f"ERROR: --project points at a path that is not a directory: {target}"
    ]
    assert "HINT:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_global_scope_with_missing_path_stops_the_same_way(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    proc = run("--scope", "global", "--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    lines = proc.stderr.splitlines()
    assert lines[0] == f"ERROR: --project points at a path that does not exist: {target}"
    assert hint_words(proc.stderr) == [str(target)]
    assert len(lines) == 2
    assert "WARN:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_existing_directory_is_unchanged(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    proc = run("--project", str(project), "--dry-run", "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "ERROR:" not in proc.stderr
    assert_no_bare_cd_error(proc)
    # --dry-run は導入先へ書き込まない
    assert list(project.iterdir()) == []
