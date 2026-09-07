"""状態ファイルと結果ファイルの置き場所を決める。

作業ディレクトリの解決・状態ファイルの探索と読み込み・結果ファイルの名前付けを
持つ。外部コマンドの実行（`_sh`）も、置き場所を確かめる手として同居する。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any, Optional

import statefile

from . import die


def _default_worktree_base() -> pathlib.Path:
    """作業ディレクトリの親。解決順は cross-review と揃える。

    1. 環境変数 `NDF_WORKTREE_BASE`（明示指定）
    2. `<システム tmpdir>/ndf-worktrees`（非永続領域。コンテナ再作成で自動消滅）
    """
    import tempfile
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "--")


def _tmp_dir_for(work: pathlib.Path) -> pathlib.Path:
    """一時ディレクトリ。解決順は cross-review と同じ規約に揃える。

    1. 環境変数 `CROSS_REFACTORING_TMP_DIR`（明示指定）
    2. `<work>/.cross_refactoring/`
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    return pathlib.Path(env).resolve() if env else work / ".cross_refactoring"


def _state_path(tmp_dir: pathlib.Path, state_id: int) -> pathlib.Path:
    return tmp_dir / f"cross-refactoring-rf{state_id}-state.json"


def _find_state(state_id: int) -> pathlib.Path:
    """状態ファイルを探す。見つからなければ終了する。

    環境変数が設定されていればそこを、無ければ現在の作業ディレクトリからの
    相対で探す。呼び出し側の bash は `init` の出力を `export` してから使う。
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    candidates = []
    if env:
        candidates.append(pathlib.Path(env) / f"cross-refactoring-rf{state_id}-state.json")
    candidates.append(
        pathlib.Path.cwd() / ".cross_refactoring"
        / f"cross-refactoring-rf{state_id}-state.json"
    )
    for c in candidates:
        if c.exists():
            return c
    die(
        f"状態ファイルが見つかりません（rf{state_id}）。"
        "CROSS_REFACTORING_TMP_DIR を export してから実行してください"
    )
    raise SystemExit(1)  # die が抜けることはないが型のために置く


def _load(state_id: int) -> tuple[pathlib.Path, dict[str, Any]]:
    path = _find_state(state_id)
    return path, statefile.load(path)


def _sh(cmd: list[str], cwd: Optional[str] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        die(f"コマンドが失敗しました ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()


def _result_path(state: dict[str, Any], runtime: str, stem: str) -> pathlib.Path:
    """CLI が結果を書き出すパス。

    agy だけは現在地を作業領域にしないため、起動時に一時ディレクトリを作業領域へ
    追加している（`--add-dir`）。したがって置き場所は全ランタイムで共通でよい。
    """
    return pathlib.Path(state["tmp_dir"]) / f"{stem}-result.json"


def stem_for(runtime: str, phase: str, state_id: int, round_no: Optional[int] = None) -> str:
    """一時ファイル名の骨格。監視スクリプトの `--stem-template` と揃える。

    **提案にもラウンド番号を入れる。** CLI の起動時に同名の結果ファイルを消すため、
    番号が無いと 2 巡目の提案が始まった時点で 1 巡目の提案内容が失われる。
    統合後の採否は状態ファイルに残るが、**各ランタイムが何をどう提案したかは
    復元できなくなる**（実測）。
    """
    if phase == "propose":
        return f"{runtime}-propose-rf{state_id}-r{round_no}"
    # **最終ゲートの修正だけラウンド番号を持たない。** Step 7 は提案ラウンドの外に
    # あり、直すのは全体のテストの失敗である。番号を付けると、どの提案ラウンドの
    # 修正なのかと読める名前になる。
    if phase == "final-fix":
        return f"{runtime}-final-fix"
    return f"{runtime}-{phase}-r{round_no}"
