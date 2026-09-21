"""リポジトリ全体のテストに共通する前提を、1 か所で用意する。

3 つのことを行う。

1. `pytest_plugins` の宣言をここへ置く。pytest は最上位以外の `conftest.py` での宣言を
   受け付けないため、起点をリポジトリの根に置くと `playwright-kit-ops` の収集が中断する
2. 束ごとに要る外部コマンドを確かめ、無ければ収集の時点で失敗させる。読み飛ばしを既定に
   すると、テストの 9 割以上が消えても終了コード 0 で終わる
3. テストの実行中だけ git の全体設定と system の設定を空へ向ける。身元を用意していない
   テストは、実行した人の設定に関わらずその場で落ちる
4. テストの実行中だけ実行の要約の置き場所（`NDF_METRICS_DIR`）を一時ディレクトリへ向ける。
   状態を保存するテストが、実行した人の状態ディレクトリへ要約を書かない（#662 の AC72）
5. テストの実行中だけ監視の上限を指す環境変数（接頭辞 `MONITOR_`）を外す。上限を延ばした
   シェルから起動しても、既定値を前提にするテストが同じ結果になる（#678）

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

# `plugins/<family>/dev.agy/skills/` は配布 Skill への symlink である。実体は
# `plugins/<family>/skills/` にあり、そちらを収集する。両方をたどると同じテストが 2 度
# 数えられ、件数が実際の 2 倍近くになる（agy を配布先へ加えた時点で 1569 件が 2974 件へ
# 増えた）。**生成物は収集の対象から外す。**
collect_ignore_glob = ["plugins/*/dev.agy"]

# 束ごとに要る外部コマンド。**束によって前提が違う**ため、一覧は 1 か所に置きつつ
# 収集した束にだけ適用する。全体へ課すと、必要のないコマンドを求めることになる。
REQUIRED_COMMANDS: dict[str, tuple[str, ...]] = {
    "scripts/tests": ("bash", "jq", "git"),
    "plugins/ndf/skills/worktree/tests": ("bash", "jq", "git"),
    "plugins/ndf/skills/development-workflow/tests": ("bash", "jq", "git"),
    # `test_prepare_worktrees.py` が `prepare-worktrees.sh` を bash で起動し、
    # そのスクリプトが `jq` を要求する。残りのテストは `git` だけを使う。
    "plugins/ndf/skills/cross-refactoring/tests": ("bash", "jq", "git"),
    # `launch-reviewer.sh` と `rotate-pr.sh` を bash で起動する束がある。
    # どちらも内部で `jq` を呼ぶ。
    "plugins/ndf/skills/cross-review/tests": ("bash", "jq", "git"),
    # `assert-characterization.sh` を bash で起動し、検査の本体が `jq` と `python3` を呼ぶ。
    "tests/runtime-smoke": ("bash", "jq", "python3"),
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


# 監視の上限を指す環境変数の接頭辞（#678）。担当ごとの指定・共通の指定のどちらもこの
# 接頭辞を持つため、接頭辞だけで一致させる。名前を並べると、上限の種類が増えるたびに
# ここへ足し忘れる。
MONITOR_ENV_PREFIX = "MONITOR_"

# `pytest_configure` で外した値の控え。実行が終わったときに戻す。
_saved_monitor_env: dict[str, str] = {}


def _strip_monitor_env() -> dict[str, str]:
    """接頭辞の環境変数を外し、外した値を返す。"""
    return {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(MONITOR_ENV_PREFIX)}


def pytest_configure(config) -> None:
    """テストの実行中だけ、監視の上限を指す環境変数を外す（#678）。

    無進捗の許容と打ち切りの上限は環境変数で延ばせる。運用で延ばしたシェルから起動すると、
    表の既定値を前提にするテストが既定値ではなくその値を読み、変更の中身と関係なく落ちる。
    収束ループの初期化は着手前のテストの通過を条件にするため、そこで止まる。

    **収集より前に外す。** テストの本体を読み込む時点で上限を決めてしまう実装があり、
    セッションの前提（fixture）では間に合わない。子プロセスは環境変数を受け継ぐため、
    テストが起動する別プロセスにも同じ切り離しが効く。**個別に設定するテストは打ち消さない。**
    `monkeypatch` も、別プロセスへ渡す上書きも、この後に効く。
    """
    _saved_monitor_env.update(_strip_monitor_env())


def pytest_unconfigure(config) -> None:
    """実行が終わったら、外した環境変数を戻す。"""
    os.environ.update(_saved_monitor_env)
    _saved_monitor_env.clear()


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


@pytest.fixture(scope="session", autouse=True)
def _isolated_metrics_dir() -> object:
    """テストの実行中だけ、実行の要約の置き場所を一時ディレクトリへ向ける（#662）。

    状態を保存する副コマンドは、保存のたびに要約を書く。向けないと、テストを回すたびに
    実行した人の `~/.local/state/ndf/metrics` へ偽の実行が積もり、集計を汚す。
    **`NDF_METRICS=0` にはしない。** 書き出しの経路そのものをテストで通すためである。
    子プロセスは環境変数を受け継ぐため、同じ向け先へ書く。
    """
    with tempfile.TemporaryDirectory(prefix="ndf-tests-metrics-") as tmp:
        saved = {k: os.environ.get(k) for k in ("NDF_METRICS_DIR", "NDF_METRICS")}
        os.environ["NDF_METRICS_DIR"] = tmp
        os.environ.pop("NDF_METRICS", None)
        try:
            yield Path(tmp)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
