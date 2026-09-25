"""状態ファイルと結果ファイルの置き場所を決める。

作業ディレクトリの解決・状態ファイルの探索と読み込み・結果ファイルの名前付けを
持つ。外部コマンドの実行（`sh`）も、置き場所を確かめる手として同居する。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any, Optional

import statefile

from . import die


def work_dir(state: dict[str, Any]) -> str:
    """状態から実装用 worktree の場所を返す。"""
    return str(state["worktrees"]["work"])


def default_worktree_base() -> pathlib.Path:
    """作業ディレクトリの親。解決順は cross-review と揃える。

    1. 環境変数 `NDF_WORKTREE_BASE`（明示指定）
    2. `<システム tmpdir>/ndf-worktrees`（非永続領域。コンテナ再作成で自動消滅）
    """
    import tempfile
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def repo_slug(repo: str) -> str:
    return repo.replace("/", "--")


def tmp_dir_for(work: pathlib.Path) -> pathlib.Path:
    """一時ディレクトリ。解決順は cross-review と同じ規約に揃える。

    1. 環境変数 `CROSS_REFACTORING_TMP_DIR`（明示指定）
    2. `<work>/.cross_refactoring/`
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    return pathlib.Path(env).resolve() if env else work / ".cross_refactoring"


def state_path(tmp_dir: pathlib.Path, state_id: int) -> pathlib.Path:
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


def load_state(state_id: int) -> tuple[pathlib.Path, dict[str, Any]]:
    path = _find_state(state_id)
    return path, statefile.load(path)


def sh(cmd: list[str], cwd: Optional[str] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        die(f"コマンドが失敗しました ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()


def result_path(state: dict[str, Any], runtime: str, stem: str) -> pathlib.Path:
    """CLI が結果を書き出すパス。

    agy だけは現在地を作業領域にしないため、起動時に一時ディレクトリを作業領域へ
    追加している（`--add-dir`）。したがって置き場所は全ランタイムで共通でよい。
    """
    return pathlib.Path(state["tmp_dir"]) / f"{stem}-result.json"


def stem_for(runtime: str, phase: str, state_id: int) -> str:
    """一時ファイル名の骨格。監視スクリプトの `--stem-template` と揃える。

    **手順の名前をそのまま入れる**（#933 の実装計画 I3）。ラウンドが無くなり、
    どの手順も 1 回の実行で 1 度だけ起動する（修正は同じ名前で何度も起動し、
    取り込み済みの判定は試行の番号と結果の中身の組で行う）。

    **最終ゲートの修正だけ実行の番号を持たない。** 名前は今（v10.17.x）と同じで、
    取り込み側（`merge-final-fix`）もこの名前で探す。
    """
    if phase == "final-fix":
        return f"{runtime}-final-fix"
    return f"{runtime}-{phase}-rf{state_id}"


def git_out(work: str, args: list[str], strip: bool = True) -> Optional[str]:
    """`git` を実行して標準出力を返す。失敗したら `None`。

    **固定幅で読む出力には `strip=False` を渡す。** `git status --porcelain` の
    状態コードは未 stage の変更で ` M` と先頭が空白になるため、`strip()` すると
    1 行目だけ 1 文字ずれ、切り出したパスの先頭が欠ける。欠けたパスは
    `git add` で `pathspec ... did not match any files` になり、同期が止まる。
    """
    r = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")
