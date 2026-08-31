"""入口のスクリプトを検証する（受け入れ条件 1〜3）。

GitHub へは通信しない。`gh` を PATH から外すか、記録するだけの偽物へ差し替える。
"""
from __future__ import annotations

import json
import os
import shutil
import stat

from projects_helpers import run_sync, write_declaration

VALID = json.dumps({"version": 1, "owner": "devbasex", "number": 1})


def fake_gh(tmp_path, log, exit_code: int = 0, stderr: str = "") -> dict:
    """`gh` の代わりに引数を記録するだけの実行ファイルを PATH の先頭へ置く。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        + (f'printf "%s" {stderr!r} >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}


def test_no_declaration_is_silent(repo, tmp_path) -> None:
    """宣言が無いリポジトリでは何も出力せず 0 で終わる。"""
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert got.stdout == "" and got.stderr == ""
    assert not log.exists(), "宣言が無いのに gh を呼んでいる"


def without_gh(tmp_path) -> dict:
    """`gh` だけを PATH から外す。実行に要る他のコマンドは残す。

    PATH を空にすると `bash` 自体が見つからず、検証したい経路へ届かない。
    """
    bindir = tmp_path / "nogh"
    bindir.mkdir(exist_ok=True)
    for name in ("bash", "git", "jq", "grep", "dirname", "printf"):
        found = shutil.which(name)
        if found:
            link = bindir / name
            if not link.exists():
                link.symlink_to(found)
    return {"PATH": str(bindir)}


def test_missing_gh_is_silent(repo, tmp_path) -> None:
    """`gh` が無い環境でも 0 で終わる。進行管理を開発の前提条件にしない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=without_gh(tmp_path))
    assert got.returncode == 0, got.stderr
    assert got.stdout == ""


def test_gh_failure_is_silent(repo, tmp_path) -> None:
    """権限不足などで `gh` が落ちても 0 で終わる。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    env = fake_gh(tmp_path, log, exit_code=1, stderr="missing scope 'project'")
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=env)
    assert got.returncode == 0


def test_unknown_key_is_an_error(repo, tmp_path) -> None:
    """知らないキーは呼び出し側の誤りである。黙って進まない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "nope", "x", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2
    assert "nope" in got.stderr


def test_unknown_stage_is_an_error(repo, tmp_path) -> None:
    """工程名は工程表の行と一致する。綴りの誤りを盤面へ書き込まない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", "レビューする", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2
    assert "レビューする" in got.stderr


def test_unknown_mode_is_an_error(repo, tmp_path) -> None:
    write_declaration(repo, VALID)
    got = run_sync("186", "mode", "medium", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2


def test_missing_arguments_is_an_error(repo, tmp_path) -> None:
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2


def test_known_stage_reaches_gh(repo, tmp_path) -> None:
    """宣言があり `gh` があるときは、盤面の更新を試みる。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert log.exists(), "gh が呼ばれていない"
    assert "devbasex" in log.read_text(encoding="utf-8")


def test_text_key_reaches_gh(repo, tmp_path) -> None:
    """文字列のフィールドは値を検査しない。任意のパスが入る。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "plan", "issues/issue-186.md", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert log.exists()
