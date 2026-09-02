"""リポジトリ全体のテストに共通する前提を、1 か所で用意する。

3 つのことを行う。

1. `pytest_plugins` の宣言をここへ置く。pytest は最上位以外の `conftest.py` での宣言を
   受け付けないため、起点をリポジトリの根に置くと `playwright-kit-ops` の収集が中断する
2. 束ごとに要る外部コマンドを確かめ、無ければ収集の時点で失敗させる。読み飛ばしを既定に
   すると、テストの 9 割以上が消えても終了コード 0 で終わる
3. テストの実行中だけ git の全体設定と system の設定を空へ向ける。身元を用意していない
   テストは、実行した人の設定に関わらずその場で落ちる

`playwright-kit-ops` のディレクトリを起点にした実行では、このファイルは読まれない。
`pytester` はそのディレクトリの `pyproject.toml` の `addopts` が読み込む。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# `pytester` は playwright-kit の pytest plugin の自己テストが使う。pytest はこの宣言を
# 最上位の conftest.py でしか受け付けない。
pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent

# 束ごとに要る外部コマンド。**束によって前提が違う**ため、一覧は 1 か所に置きつつ
# 収集した束にだけ適用する。全体へ課すと、必要のないコマンドを求めることになる。
REQUIRED_COMMANDS: dict[str, tuple[str, ...]] = {
    "scripts/tests": ("bash", "jq", "git"),
    "plugins/ndf/skills/worktree/tests": ("bash", "jq", "git"),
    "plugins/ndf/skills/development-workflow/tests": ("bash", "jq", "git"),
    "plugins/ndf/skills/cross-refactoring/tests": ("git",),
}

# 読み飛ばしを選ぶ指定。**既定は失敗**である。読み飛ばしたい実行環境のために残すが、
# 選ぶのは実行する側とする。
ALLOW_MISSING_ENV = "NDF_TESTS_ALLOW_MISSING_COMMANDS"


def _allow_missing() -> bool:
    return os.environ.get(ALLOW_MISSING_ENV, "") not in ("", "0")


def _bundle_of(path: Path) -> str | None:
    """その項目が属する束を返す。一覧に無ければ `None`。"""
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None
    for bundle in REQUIRED_COMMANDS:
        if rel == bundle or rel.startswith(bundle + "/"):
            return bundle
    return None


def _missing(bundles: set[str]) -> dict[str, list[str]]:
    """収集した束ごとに、不足している外部コマンドを返す。"""
    found: dict[str, list[str]] = {}
    for bundle in sorted(bundles):
        lacking = [c for c in REQUIRED_COMMANDS[bundle] if shutil.which(c) is None]
        if lacking:
            found[bundle] = lacking
    return found


def pytest_collection_modifyitems(config, items) -> None:
    bundles = {b for item in items if (b := _bundle_of(Path(str(item.fspath)))) is not None}
    missing = _missing(bundles)
    if not missing:
        return
    lines = [f"{bundle}: {' '.join(commands)}" for bundle, commands in missing.items()]
    if _allow_missing():
        mark = pytest.mark.skip(reason=f"外部コマンドが無い（{ALLOW_MISSING_ENV} の指定により読み飛ばす）")
        for item in items:
            if _bundle_of(Path(str(item.fspath))) in missing:
                item.add_marker(mark)
        return
    raise pytest.UsageError(
        "テストの実行に要る外部コマンドが見つかりません:\n  "
        + "\n  ".join(lines)
        + f"\n読み飛ばして実行する場合は {ALLOW_MISSING_ENV}=1 を指定してください。"
    )


@pytest.fixture(scope="session", autouse=True)
def _isolated_git_identity() -> object:
    """テストの実行中だけ、git の全体設定と system の設定を空へ向ける。

    身元を用意し直しても、実行した人の全体設定が残っていれば、次に同じ依存が入ったときに
    気づけない。空へ向けておけば、用意していないテストはその場で落ちる。**実行した人の
    設定は書き換えない。** 向け先は一時ディレクトリの空のファイルで、実行が終われば消える。
    """
    with tempfile.TemporaryDirectory(prefix="ndf-tests-git-") as tmp:
        empty = Path(tmp) / "gitconfig"
        empty.write_text("", encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = str(empty)
        os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
        try:
            yield empty
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
