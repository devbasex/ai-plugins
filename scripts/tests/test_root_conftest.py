"""リポジトリの根の設定が持つ前提を固定する（#232 / #233 / #235）。

3 つのことを確かめる。

1. 起点をリポジトリの根に置いても収集が中断しない（`pytest_plugins` の宣言の位置）
2. 前提の外部コマンドが無いとき、読み飛ばさずに 0 以外の終了コードで終わる
3. テストの実行中は git の全体設定と system の設定を読まない

前提の不足は、`PATH` を絞った子プロセスとして pytest を起動して確かめる。実行環境の
`PATH` は書き換えない。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_CONFTEST = REPO_ROOT / "conftest.py"

# 前提を確かめる対象として、外部コマンドを呼ばずに済む小さな束を使う。
BUNDLE = "plugins/ndf/skills/development-workflow/tests"


def _read_root_conftest() -> str:
    return ROOT_CONFTEST.read_text(encoding="utf-8")


def _run_pytest(target: str, *, path: str | None = None, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("NDF_TESTS_ALLOW_MISSING_COMMANDS", None)
    if path is not None:
        env["PATH"] = path
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _path_without(tmp_path: Path, *names: str) -> str:
    """指定したコマンドだけを外した `PATH` を、`tmp_path` の symlink で組み立てる。

    置き場所を `tmp_path` にすると、テストが終わったときに pytest が片付ける。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    keep = ["python3", sys.executable.rsplit("/", 1)[-1], "bash", "jq", "git", "sh", "env", "uv"]
    for name in keep:
        if name in names:
            continue
        found = shutil.which(name)
        if found:
            link = bin_dir / name
            if not link.exists():
                link.symlink_to(found)
    return str(bin_dir)


def test_the_root_conftest_declares_the_plugin() -> None:
    """`pytest_plugins` は最上位の conftest.py でしか宣言できない。"""
    assert 'pytest_plugins = ["pytester"]' in _read_root_conftest()


def test_the_skill_conftest_no_longer_declares_the_plugin() -> None:
    body = (
        REPO_ROOT / "plugins/playwright-kit/skills/playwright-kit-ops/tests/conftest.py"
    ).read_text(encoding="utf-8")
    assert 'pytest_plugins = ["pytester"]' not in body


def test_the_skill_config_loads_the_plugin_for_its_own_root() -> None:
    """その skill のディレクトリを起点にした実行でも `pytester` が要る。"""
    body = (
        REPO_ROOT / "plugins/playwright-kit/skills/playwright-kit-ops/pyproject.toml"
    ).read_text(encoding="utf-8")
    assert "-p pytester" in body


def test_no_bundle_skips_itself_when_a_command_is_missing() -> None:
    """読み飛ばしの指定が、束の側に残っていないこと。"""
    for name in (
        "plugins/ndf/skills/worktree/tests/conftest.py",
        "plugins/ndf/skills/development-workflow/tests/conftest.py",
    ):
        assert "collect_ignore_glob" not in (REPO_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq を外した PATH を組み立てられない")
def test_a_missing_command_fails_the_collection(tmp_path: Path) -> None:
    """前提のコマンドが無ければ、読み飛ばさずに 0 以外の終了コードで終わる。"""
    result = _run_pytest(BUNDLE, path=_path_without(tmp_path, "jq"))

    assert result.returncode != 0
    assert "jq" in result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq を外した PATH を組み立てられない")
def test_the_failure_names_the_bundle_and_the_command(tmp_path: Path) -> None:
    result = _run_pytest(BUNDLE, path=_path_without(tmp_path, "jq"))
    out = result.stdout + result.stderr

    assert BUNDLE in out
    assert "jq" in out


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq を外した PATH を組み立てられない")
def test_the_opt_in_skips_instead_of_failing(tmp_path: Path) -> None:
    """指定したときだけ、これまでどおり読み飛ばす。"""
    result = _run_pytest(
        BUNDLE,
        path=_path_without(tmp_path, "jq"),
        env_extra={"NDF_TESTS_ALLOW_MISSING_COMMANDS": "1"},
    )

    assert result.returncode == 0
    assert "skipped" in result.stdout


def test_a_bundle_outside_the_table_is_not_checked(tmp_path: Path) -> None:
    """一覧に無い束だけを収集したときは、前提を確かめない。"""
    result = _run_pytest("plugins/ndf/skills/cross-review/tests", path=_path_without(tmp_path, "jq"))

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_git_identity_is_isolated_during_the_run() -> None:
    """テストの実行中は、実行した人の全体設定を読まない。"""
    assert os.environ.get("GIT_CONFIG_GLOBAL")
    out = subprocess.run(
        ["git", "config", "--global", "--get", "user.email"],
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == ""
