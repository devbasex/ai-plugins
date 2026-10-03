"""状態ファイルと結果ファイルの置き場所を決める。

作業ディレクトリの解決・状態ファイルの探索と読み込み・結果ファイルの名前付けを
持つ。外部コマンドの実行（`sh`）も、置き場所を確かめる手として同居する。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any, Optional

import proc
import repo as repo_lib
import statefile
import worktree_base

from . import die


def work_dir(state: dict[str, Any]) -> str:
    """状態から実装用 worktree の場所を返す。"""
    return str(state["worktrees"]["work"])


# worktree の親ディレクトリ。解決の規則は共通層の `worktree_base.worktree_parent` だけが持つ。
default_worktree_base = worktree_base.worktree_parent


def tmp_dir_for(work: pathlib.Path) -> pathlib.Path:
    """一時ディレクトリ。解決順は cross-review と同じ規約に揃える。

    1. 環境変数 `CROSS_REFACTORING_TMP_DIR`（明示指定）
    2. `<work>/.cross_refactoring/`
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    return pathlib.Path(env).resolve() if env else work / ".cross_refactoring"


def state_path(tmp_dir: pathlib.Path, state_id: int) -> pathlib.Path:
    return tmp_dir / f"cross-refactoring-rf{state_id}-state.json"


def github_repo_from_origin() -> Optional[str]:
    """カレントの origin の URL から `owner/repo` を求める（GitHub の URL だけ）。求まらなければ `None`。

    **求めた名前はそのまま使わない。** `repos/{owner}/{repo}/pulls/{PR}` の応答が
    そのまま検証になるため、誤った名前は失敗として現れる（`_fetch_pr_context`）。
    URL の読み方はライブラリの `repo.owner_repo_from_url` が持つ。
    """
    url = proc.git_out(pathlib.Path.cwd(), "remote", "get-url", "origin") or ""
    return repo_lib.owner_repo_from_url(url) if "github.com" in url else None


def default_tmp_dir(state_id: int) -> Optional[pathlib.Path]:
    """既定の worktree の置き場の状態の置き場（`<既定の根>/<owner--repo>/rf<ID>/work/.cross_refactoring`）。

    origin の owner/repo が求まらなければ `None`。環境変数 `CROSS_REFACTORING_TMP_DIR` は見ない
    （駆動の `known_tmp` と `_find_state` が先に見る）。
    """
    repo = github_repo_from_origin()
    if not repo:
        return None
    return default_worktree_base() / repo_lib.slug(repo) / f"rf{state_id}" / "work" / ".cross_refactoring"


def _find_state(state_id: int) -> pathlib.Path:
    """状態ファイルを探す。見つからなければ終了する。

    環境変数 → 現在の作業ディレクトリの `.cross_refactoring/` → 既定の worktree の置き場の順に探す。
    駆動が終わった後の conductor は環境変数を持たないため、既定の置き場からも探す（#1692 の決定 5）。
    """
    env = os.environ.get("CROSS_REFACTORING_TMP_DIR")
    candidates = []
    if env:
        candidates.append(state_path(pathlib.Path(env), state_id))
    candidates.append(state_path(pathlib.Path.cwd() / ".cross_refactoring", state_id))
    fallback = default_tmp_dir(state_id)
    if fallback is not None:
        candidates.append(state_path(fallback, state_id))
    for c in candidates:
        if c.exists():
            return c
    die(f"状態ファイルが見つかりません（rf{state_id}）。CROSS_REFACTORING_TMP_DIR を export してから実行してください")
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

    起動はライブラリの `proc.run` が行う。名前と引数の形（語の並びと `strip`）は、ここを使う
    モジュールのために残す（`proc.git_out` は前後の空白を必ず落とす）。
    """
    r = proc.run(["git", *args], cwd=work, check=False)
    if r.returncode != 0:
        return None
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")


def resolve_commit(work: str, sha: str) -> Optional[str]:
    """SHA（短縮形を含む）をコミットの完全な SHA へ解決する。解決できなければ `None`。"""
    return git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])


def full_commit(work: str, sha: Any) -> Any:
    """SHA を完全な形へ解決する。解決できない SHA と、文字列でない・空の値はそのまま返す。"""
    return (resolve_commit(work, sha) or sha) if isinstance(sha, str) and sha else sha
