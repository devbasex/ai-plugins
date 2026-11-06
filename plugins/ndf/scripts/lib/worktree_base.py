"""作業用 worktree の親ディレクトリの解決（cross-review と cross-refactoring の共通層）。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile

ENV = "NDF_WORKTREE_BASE"
WORKTREES = ".worktrees"  # git の無視へ登録する語の元。`worktree-common.sh` の WT_WORKTREE_DIR と同じ
PLACE = pathlib.PurePosixPath(WORKTREES) / ".ndf"  # `.` で始まる語はブランチ名に使えず、開発の worktree と衝突しない


def _git_stdout(root: str | os.PathLike, *args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def repo_main_dir(root: str | os.PathLike) -> pathlib.Path | None:
    """`root`（worktree・サブディレクトリも可）のメインディレクトリ。git のリポジトリへ解けなければ None。

    解き方は `worktree-common.sh` の `wt_main_dir` と同じ。"""
    if _git_stdout(root, "rev-parse", "--show-superproject-working-tree"):
        top = _git_stdout(root, "rev-parse", "--show-toplevel")
        return pathlib.Path(top) if top else None
    common = _git_stdout(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return pathlib.Path(common).parent if common else None


def _declared_parent(root: str | os.PathLike) -> pathlib.Path | None:
    """コンテナの宣言があるリポジトリの置き場。宣言が無いか、git のリポジトリへ解けなければ None。"""
    import container_reach  # container_reach がこのモジュールを読むため、要るときに読む

    main = repo_main_dir(root)
    return main / PLACE if main is not None and container_reach.container_suites(main) else None


def worktree_parent(root: str | os.PathLike) -> pathlib.Path:
    """worktree の親ディレクトリ。`root` はリポジトリのルート（worktree・サブディレクトリも可）で、省けない。

    1. 環境変数 `NDF_WORKTREE_BASE`（明示指定）。相対パスのまま状態ファイルへ残すと後続の
       パスの比較が壊れるため、絶対パスへ解決して返す
    2. コンテナの宣言があれば `<メインディレクトリ>/.worktrees/.ndf`。ホストの Docker を共有する
       環境で一時ディレクトリはデーモンから見えず、テスト環境が worktree を見られないため
    3. `<システム tmpdir>/ndf-worktrees`（非永続領域。コンテナ再作成で自動消滅）

    かつての /work/worktrees ($HOME/work/worktrees) は共有の永続 volume 上にあり、
    別リポジトリの pr<N> と衝突する・明示削除が必要・volume を消費する問題が
    あったため廃止した。
    """
    env = os.environ.get(ENV)
    if env:
        return pathlib.Path(env).resolve()
    return _declared_parent(root) or pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def add_exclude(repo_dir: str | os.PathLike, entry: str) -> bool:
    """共通の git ディレクトリの `info/exclude` へ `entry` を登録する。追跡される `.gitignore` は変えない。"""
    common = _git_stdout(repo_dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return False
    exclude = pathlib.Path(common) / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if entry not in text.splitlines():
            with exclude.open("a", encoding="utf-8") as f:
                f.write(("" if not text or text.endswith("\n") else "\n") + entry + "\n")
    except OSError:
        return False
    return True


def ensure_ignored(root: str | os.PathLike, worktree: str | os.PathLike) -> None:
    """`worktree` が宣言の置き場の下なら、`git worktree add` の前に `.worktrees/` を git の無視へ登録する。

    未登録のまま作るとメインディレクトリの `git add -A` が worktree を拾う。登録できなければ `OSError`。
    置き場を読むだけの片付けの経路では呼ばない。"""
    parent = _declared_parent(root)
    if parent is None or not pathlib.Path(worktree).resolve().is_relative_to(parent):
        return
    main = parent.parent.parent
    if _git_stdout(main, "check-ignore", "-q", f"{WORKTREES}/") is None and not add_exclude(main, f"{WORKTREES}/"):
        raise OSError(f"{WORKTREES}/ を git の無視へ登録できない（{main}）")
