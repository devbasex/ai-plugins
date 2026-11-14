"""リポジトリ全体のテストに共通する前提を、1 か所で用意する。

3 つのことを行う。

1. `pytest_plugins` の宣言をここへ置く。pytest は最上位以外の `conftest.py` での宣言を
   受け付けないため、起点をリポジトリの根に置くと `playwright-kit-ops` の収集が中断する
2. 束ごとに要る外部コマンドを確かめ、無ければ収集の時点で失敗させる。読み飛ばしを既定に
   すると、テストの 9 割以上が消えても終了コード 0 で終わる
3. テストの実行中だけ git の全体設定と system の設定を空へ向ける。身元を用意していない
   テストは、実行した人の設定に関わらずその場で落ちる
4. テストの実行中だけ実行の要約の置き場所（`NDF_METRICS_DIR`）を一時ディレクトリへ向ける。
   状態を保存するテストが、実行した人の状態ディレクトリへ要約を書かない（#662 の AC72）。
   使用量の帳簿・プランの状態の実体・耐久の記録の置き場所は `plugins/ndf/conftest.py` が
   テストごとに向ける（プラグインのキャッシュでも効くよう、プラグインの根に置く。#1953）
5. テストの実行中だけ監視の上限を指す環境変数（接頭辞 `MONITOR_`）を外す。上限を延ばした
   シェルから起動しても、既定値を前提にするテストが同じ結果になる（#678）
6. `SHARD_TOTAL` と `SHARD_INDEX` が与えられたら、収集した項目をファイル単位で分け、
   自分の分だけを残す。継続的統合がジョブを分けて回すために使う（#882）
7. pytest が SIGTERM で止められたとき・終わったときに、テストが起こした子孫のプロセスを止める
   （`scripts/pytest_child_cleanup.py`。#2291）
8. worker を起こす前に、プラグインの依存の環境（`~/.cache/ndf/venv/<版>`）を 1 度だけ作る。
   版を上げた直後に並列の worker が同じ環境へ同時に入れて壊すのを防ぐ

どの束のディレクトリを起点にしても読まれるよう、テストの基準のディレクトリ（rootdir）は
根の設定ファイル（`pytest.ini`）がリポジトリの根へ固定する。

`playwright-kit-ops` のディレクトリを起点にした実行では、このファイルは読まれない。
`pytester` はそのディレクトリの `pyproject.toml` の `addopts` が読み込む。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import pytest

# `pytester` は playwright-kit の pytest plugin の自己テストが使う。pytest はこの宣言を
# 最上位の conftest.py でしか受け付けない。
# `pytest_child_cleanup` は、テストが起こした子孫のプロセスを、pytest が SIGTERM で止められたとき・
# 終わったときに止める（#2291）。親と xdist の各 worker で働く。
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest_plugins = ["pytester", "pytest_child_cleanup"]

# `plugins/<family>/dev.agy/skills/` は配布 Skill への symlink である。実体は
# `plugins/<family>/skills/` にあり、そちらを収集する。両方をたどると同じテストが 2 度
# 数えられ、件数が実際の 2 倍近くになる（agy を配布先へ加えた時点で 1569 件が 2974 件へ
# 増えた）。**生成物は収集の対象から外す。**
#
# `plugins/ndf-next/` は自分の設定（plugins/ndf-next/pyproject.toml の importlib 読み込み）と
# 自分の workflow（ndf-next.yml）で確かめる（scripts/ci-heavy-skip.py の `SELF_TESTED`）。
# 根から収集すると、`tests/__init__.py` を持つ束どうし（playwright-kit-ops）の conftest が
# 同じ `tests.conftest` になって収集が止まる。入れ替え（#1142 の S2）で `SELF_TESTED` と一緒に消す。
collect_ignore_glob = ["plugins/*/dev.agy", "plugins/ndf-next"]

# 束ごとに要る外部コマンド（前提のコマンドの一覧）。**束によって前提が違う**ため、一覧は
# 1 か所に置きつつ収集した束にだけ適用する。全体へ課すと、必要のないコマンドを求めることになる。
# キーはリポジトリの根からの相対パスで、ファイルの中のクラスまで絞るときは `::` でつなぐ。
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
    # `assert-characterization.sh` を bash で起動し、チェックの本体が `jq` と `python3` を呼ぶ。
    "tests/runtime-smoke": ("bash", "jq", "python3"),
    # スライドの生成スクリプト（pptxgenjs）を node で実行し、依存が無ければ `npm ci` で入れる（#2065）
    "plugins/ndf/skills/release/tests/test_slide_template.py": ("node", "npm"),
    "tests/test_presentation_decks.py": ("node", "npm"),
    # 名前つきのラッパーを使い捨ての tmux サーバで動かす結合テスト（#2346）。同じファイルの
    # ほかのテストは tmux を使わないため、クラスまで絞る。
    "plugins/ndf/scripts/tests/test_relay_hub.py::TestWithTmux": ("tmux",),
}

# 読み飛ばしを選ぶ指定。**既定は失敗**である。読み飛ばしたい実行環境のために残すが、
# 選ぶのは実行する側とする。
ALLOW_MISSING_ENV = "NDF_TESTS_ALLOW_MISSING_COMMANDS"


def _allow_missing() -> bool:
    return os.environ.get(ALLOW_MISSING_ENV, "") not in ("", "0")


def _item_id(item) -> str | None:
    """項目の識別子（根からの相対パス、続けてファイルの中の位置を `::` でつないだもの）を返す。

    根の外のファイルは `None`。
    """
    path = Path(str(item.fspath)).resolve()
    if not path.is_relative_to(ROOT):
        return None
    _, sep, inner = item.nodeid.partition("::")
    return path.relative_to(ROOT).as_posix() + sep + inner


def _bundles_of(item_id: str | None) -> list[str]:
    """識別子が当たる一覧のキーを返す。

    当たるのは、識別子がキーと一致するか、キーの直後が `/` か `::` のときだけである
    （`::TestWithTmux` は `::TestWithTmuxOld` に当たらない）。
    """
    if item_id is None:
        return []
    return [k for k in REQUIRED_COMMANDS if item_id == k or item_id.startswith((k + "/", k + "::"))]


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

# `pytest_configure` で外した値の記録。実行が終わったときに戻す。
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
    if not hasattr(config, "workerinput"):
        _warm_plugin_venv()


def _warm_plugin_venv() -> None:
    """プラグインの依存の環境（`~/.cache/ndf/venv/<版>`）を、worker を起こす前に 1 度だけ作っておく。

    `uv run --script` のスクリプト（`state.py` など）は `deps.require` でこの環境へ起動し直す。版を上げた
    直後は環境が無く、並列の worker が同じ場所へ同時に入れて壊れる（greenlet の `*.data/headers` を
    動かせず `uv run` が 2 で終わる。ndf v10.27.13 の CI）。全グループを先に入れておけば、後の
    `uv run --extra` は入れ直さない。uv が無い・入れられないときは何もしない（テストの側で従来どおり作る）。
    """
    spec = importlib.util.spec_from_file_location("_ndf_deps_for_warmup", ROOT / "plugins" / "ndf" / "scripts" / "lib" / "deps.py")
    if spec is None or spec.loader is None:
        return
    deps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deps)
    uv = deps.find_uv()
    if not uv:
        return
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=deps.venv_dir())
    subprocess.run(
        [uv, "sync", "--quiet", "--frozen", "--inexact", "--project", str(deps.PLUGIN_ROOT), "--all-extras"],
        env=env,
        capture_output=True,
        check=False,
    )


def pytest_unconfigure(config) -> None:
    """実行が終わったら、外した環境変数を戻す。"""
    os.environ.update(_saved_monitor_env)
    _saved_monitor_env.clear()


def pytest_sessionfinish(session, exitstatus) -> None:
    """テストのセッションの終わりに、寿命の窓を過ぎた `ndf-tests-*`（止められたセッションの残り）を片付ける（#2229）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "plugins" / "ndf" / "scripts" / "lib"))
    import run_state

    run_state.sweep_states(None, kinds=("test-tmp",), tmp_only=True)


# ジョブの分割（#882）。**分ける単位はファイルで、割り当てはパスの剰余だけで決める。**
# 同じファイルのテストは同じジョブへ入るため、ファイル単位の前提（モジュールの読み込み・
# 一時ディレクトリ）が割れない。偏りが出たら実測時間での割り当てへ進む。
SHARD_TOTAL_ENV = "SHARD_TOTAL"
SHARD_INDEX_ENV = "SHARD_INDEX"


def _shard() -> tuple[int, int] | None:
    """`(index, total)` を返す。指定が無ければ `None`（分けない）。"""
    total = os.environ.get(SHARD_TOTAL_ENV, "")
    if total == "":
        return None
    index = os.environ.get(SHARD_INDEX_ENV, "")
    if not (total.isdigit() and index.isdigit() and int(index) < int(total)):
        raise pytest.UsageError(
            f"{SHARD_TOTAL_ENV}={total!r} / {SHARD_INDEX_ENV}={index!r}: "
            f"0 <= {SHARD_INDEX_ENV} < {SHARD_TOTAL_ENV} の整数を指定してください"
        )
    return int(index), int(total)


def _select_shard(config, items) -> None:
    shard = _shard()
    if shard is None:
        return
    index, total = shard
    kept, dropped = [], []
    for item in items:
        path = Path(str(item.fspath)).resolve()
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
        (kept if zlib.crc32(rel.encode()) % total == index else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_collection_modifyitems(config, items) -> None:
    _select_shard(config, items)
    bundles = {b for item in items for b in _bundles_of(_item_id(item))}
    missing = _missing(bundles)
    if not missing:
        return
    lines = [f"{bundle}: {' '.join(commands)}" for bundle, commands in missing.items()]
    if _allow_missing():
        mark = pytest.mark.skip(reason=f"外部コマンドが無い（{ALLOW_MISSING_ENV} の指定により読み飛ばす）")
        for item in items:
            if any(b in missing for b in _bundles_of(_item_id(item))):
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
def _isolated_context_dir() -> object:
    """テストの実行中だけ、再起動の方針の記録と受け渡しの置き場所（#2262）を一時ディレクトリへ向ける。

    statusline と hook は窓・適用値・判定の記録を書く。向けないと、実行した人の `~/.local/state/ndf/context` へ
    偽の記録が積もる。子プロセスは環境変数を受け継ぐため、同じ向け先へ書く。
    """
    with tempfile.TemporaryDirectory(prefix="ndf-tests-context-") as tmp:
        saved = os.environ.get("NDF_CONTEXT_DIR")
        os.environ["NDF_CONTEXT_DIR"] = tmp
        try:
            yield Path(tmp)
        finally:
            if saved is None:
                os.environ.pop("NDF_CONTEXT_DIR", None)
            else:
                os.environ["NDF_CONTEXT_DIR"] = saved


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
