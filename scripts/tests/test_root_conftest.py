"""リポジトリの根の設定が持つ前提を固定する（#232 / #233 / #235）。

4 つのことを確かめる。

1. 起点をリポジトリの根に置いても収集が中断しない（`pytest_plugins` の宣言の位置）
2. 前提の外部コマンドが無いとき、読み飛ばさずに 0 以外の終了コードで終わる
3. テストの実行中は git の全体設定と system の設定を読まない
4. テストの実行中は監視の上限を指す環境変数を読まない（#678）

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

# 一覧に無い束。**シェルスクリプトを起動しない束であること**が条件である。
# `_path_without` が組み立てる `PATH` には `dirname` すら無いため、スクリプトを
# 起動する束をここへ置くと、前提のチェックではなくスクリプトの側で落ちる。
OUTSIDE_BUNDLE = "plugins/playwright-kit/skills/playwright-kit-ops/tests"


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


def test_the_outside_bundle_is_really_outside_the_table() -> None:
    """`OUTSIDE_BUNDLE` が一覧へ載ったら、次のチェックは何も確かめていない。

    載せた側はチェックが素通りしたことに気づけないため、ここで落とす。
    """
    body = _read_root_conftest()
    assert f'"{OUTSIDE_BUNDLE}"' not in body
    assert f'"{BUNDLE}"' in body


def test_a_bundle_outside_the_table_is_not_checked(tmp_path: Path) -> None:
    """一覧に無い束だけを収集したときは、前提を確かめない。"""
    result = _run_pytest(OUTSIDE_BUNDLE, path=_path_without(tmp_path, "jq"))

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


def test_metrics_dir_points_to_a_temporary_directory_during_tests() -> None:
    """状態を保存するテストが、実行した人の状態ディレクトリへ要約を書かない（#662 の AC72）。"""
    import tempfile

    metrics = os.environ.get("NDF_METRICS_DIR", "")
    assert metrics, "NDF_METRICS_DIR が設定されていない"
    assert Path(metrics).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert "NDF_METRICS" not in os.environ


# ---------- 監視の上限を指す環境変数の切り離し（#678） ----------


def _root_conftest_module():
    """根の設定を別名で読み込む。記録の辞書を汚さずに、外す側と戻す側を直接呼ぶ。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ndf_root_conftest", ROOT_CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_monitor_variable_survives_into_a_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """外す側を呼んだ後は、監視の上限を指す環境変数が 1 つも残らない。

    **確かめる前に自分で 1 つ差し込む。** 周りのシェルが上限を持たないと、外す仕組みを
    壊しても素通りする。差し込んでおけば、起動したシェルが何を持っていても同じことを
    確かめられる。記録を汚さないよう、根の設定は別名で読み込む。
    """
    mod = _root_conftest_module()
    monkeypatch.setenv("MONITOR_STALL_AGY", "1800")

    try:
        mod.pytest_configure(None)

        remaining = [k for k in os.environ if k.startswith("MONITOR_")]
        assert remaining == [], remaining
    finally:
        mod.pytest_unconfigure(None)


def test_a_test_can_still_set_its_own_monitor_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """個別に設定した値は打ち消されない。切り離しは実行の前に 1 度だけ効く。"""
    monkeypatch.setenv("MONITOR_STALL_AGY", "600")
    assert os.environ["MONITOR_STALL_AGY"] == "600"


def test_the_child_process_does_not_inherit_a_monitor_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外した後に起動した子プロセスは、監視の上限を指す環境変数を受け継がない。

    起動する側で外し直さなくてよいことを確かめる。**確かめる前に自分で 1 つ差し込む。**
    周りのシェルが上限を持たないと、外す仕組みを壊しても素通りする。記録を汚さないよう、
    根の設定は別名で読み込む。
    """
    mod = _root_conftest_module()
    monkeypatch.setenv("MONITOR_STALL_AGY", "1800")

    try:
        mod.pytest_configure(None)

        out = subprocess.run(
            [sys.executable, "-c",
             "import os; print([k for k in os.environ if k.startswith('MONITOR_')])"],
            capture_output=True, text=True,
        )
        assert out.stdout.strip() == "[]", out.stdout
    finally:
        mod.pytest_unconfigure(None)


def test_the_values_are_put_back_after_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """外した値は、実行が終わったときに戻る。"""
    mod = _root_conftest_module()
    monkeypatch.setenv("MONITOR_STALL_AGY", "1800")

    mod.pytest_configure(None)

    assert "MONITOR_STALL_AGY" not in os.environ
    assert mod._saved_monitor_env == {"MONITOR_STALL_AGY": "1800"}

    mod.pytest_unconfigure(None)

    assert os.environ["MONITOR_STALL_AGY"] == "1800"
    assert mod._saved_monitor_env == {}


def test_the_isolation_holds_from_a_bundle_directory() -> None:
    """束のディレクトリを起点にしても切り離しが効く。

    テストの基準のディレクトリ（rootdir）が起点で止まると、根の設定が読み込まれず、
    上限を延ばしたシェルから起動したときだけ落ちる。根の設定ファイル（`pytest.ini`）が
    基準をリポジトリの根へ固定していることを、実際の起動で確かめる。
    **監視の環境変数は明示的に足す。** 実行中は根の設定が外した後のため、渡す環境へ
    足さないと再現しない。
    """
    bundle = REPO_ROOT / "plugins/ndf/skills/cross-review/tests"
    env = dict(os.environ)
    env.pop("NDF_TESTS_ALLOW_MISSING_COMMANDS", None)
    env.update({"MONITOR_STALL_AGY": "1800", "MONITOR_TIMEOUT": "1800"})

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_monitor_stall_default.py", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=str(bundle),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_prefix_is_declared_once() -> None:
    """接頭辞は根の設定だけが持つ。テストの側へ書き戻すと、同じ除去がまた散る。"""
    body = _read_root_conftest()

    assert 'MONITOR_ENV_PREFIX = "MONITOR_"' in body
    assert "def pytest_unconfigure" in body


# ジョブの分割（#882）。**分けても項目が欠けず、重ならず、ファイルが割れないこと**を、
# 実際の収集で確かめる。分割が項目を落としても各ジョブと集約のジョブは成功し得るため、
# 継続的統合の結果だけでは退行に気づけない。
SHARD_TARGET = "scripts/tests"
SHARD_TOTAL = 2


def _collected_ids(shard: tuple[int, int] | None) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("SHARD_TOTAL", "SHARD_INDEX")}
    if shard is not None:
        env["SHARD_INDEX"], env["SHARD_TOTAL"] = str(shard[0]), str(shard[1])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SHARD_TARGET, "--collect-only", "-q",
         "--no-header", "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [line for line in proc.stdout.splitlines() if "::" in line]


def test_the_shards_cover_every_item_exactly_once_without_splitting_a_file() -> None:
    full = _collected_ids(None)
    shards = [_collected_ids((i, SHARD_TOTAL)) for i in range(SHARD_TOTAL)]

    assert all(shards), "どれかの shard が空で、分割を確かめられない"
    merged = [nodeid for ids in shards for nodeid in ids]
    assert len(merged) == len(set(merged)), "同じ項目が 2 つの shard に入った"
    assert sorted(merged) == sorted(full), "shard の和が未分割の収集と一致しない"

    files = [{nodeid.split("::", 1)[0] for nodeid in ids} for ids in shards]
    assert not set.intersection(*files), "同じファイルが複数の shard に割れた"
