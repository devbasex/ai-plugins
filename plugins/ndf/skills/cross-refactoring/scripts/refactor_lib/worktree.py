"""コミットの取り消しと積み直し、worktree の未コミットの変更の掃除。"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Optional

import tool_paths

from . import ABORT, die, info
from .paths import git_out


def reset_hard(work: str, sha: Optional[str]) -> None:
    """着手前の HEAD へ戻す。半端な履歴を Pull Request に残さないための後始末。"""
    if sha:
        subprocess.run(["git", "reset", "--hard", sha], cwd=work, capture_output=True, text=True)


def revert_range(work: str, ordered: list[str]) -> Optional[str]:
    """並びの順に `git revert` する。戻せなかったコミットを返す（全部戻せたら `None`）。

    **ここで中断しない。** 戻せないときは `git revert --abort` だけを打ち、着手前へ戻すかは呼び出し側
    （`undo`）が決める。同じファイルを触った項目まで広げてやり直せるようにするためである。
    """
    for sha in ordered:
        r = subprocess.run(["git", "revert", "--no-edit", sha], cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work, capture_output=True, text=True)
            info(f"⚠ {sha[:12]} を戻せませんでした: {r.stderr.strip()[:200]}")
            return sha
    return None


def replay_commits(work: str, shas: list[str]) -> tuple[dict[str, str], Optional[str]]:
    """コミットを**古い順に**積み直し、`({元の SHA: 新しい SHA}, 積み直せなかったコミット)` を返す。

    競合したら `git cherry-pick --abort` だけを打って返す。**ここで中断しない。** 同じファイルを
    触った項目まで広げるかは呼び出し側（`undo`）が決める。
    """
    mapping: dict[str, str] = {}
    for sha in shas:
        r = subprocess.run(
            ["git", "cherry-pick", "--allow-empty", sha],
            cwd=work,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "cherry-pick", "--abort"], cwd=work, capture_output=True, text=True)
            info(f"⚠ {sha[:12]} を積み直せませんでした: {r.stderr.strip()[:200]}")
            return mapping, sha
        mapping[sha] = git_out(work, ["rev-parse", "HEAD"]) or sha
    return mapping, None


def _order_newest_first(work: str, shas: list[str]) -> list[str]:
    """コミットを **git の履歴順（新しい順）** に並べ替える。

    申告された順序を信じない。古いコミットから取り消すと、後続の取り消しが
    競合して進めなくなる。履歴に無いものは順序を決められないので末尾へ置く。
    """
    if len(shas) < 2:
        return list(shas)
    history = git_out(work, ["rev-list", "HEAD"])
    if history is None:
        return list(shas)
    rank = {sha: i for i, sha in enumerate(history.split())}  # 0 が最も新しい
    resolved = {s: (git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"]) or s) for s in shas}
    return sorted(shas, key=lambda s: rank.get(resolved[s], len(rank)))


def _worktree_changes(work: str) -> dict[str, str]:
    """作業ツリーの変更を `パス → 状態` で返す。同期の前後を比べるために使う。

    無視されているファイルは現れない（`--porcelain` の既定）。改名は移動先の
    パスだけを見る。
    """
    # `core.quotePath` の既定（true）では、非 ASCII を含むパスが `"` で囲まれ
    # `\343` の形へエスケープされる。そのまま `git add` へ渡すと見つからない。
    out = git_out(
        work,
        ["-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
        strip=False,
    )
    changes: dict[str, str] = {}
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:  # 改名。移動先だけを対象にする
            path = path.split(" -> ", 1)[1]
        changes[path.strip('"')] = line[:2]
    return changes


def _control_prefix(state: dict[str, Any], work: str) -> Optional[str]:
    """作業ディレクトリから見た制御用ディレクトリの相対パス。外にあれば `None`。

    状態ファイル・プロンプト・結果・ログの置き場所で、**同期コミットへ入れない**。
    `prepare-worktrees.sh` が無視の設定を置くが、置き場所を環境変数で移した場合や
    配置前に同期が走った場合に備えて、ここでも明示的に外す。
    """
    tmp_dir = str(state.get("tmp_dir") or "")
    if not tmp_dir:
        return None
    try:
        relative = pathlib.Path(tmp_dir).resolve().relative_to(pathlib.Path(work).resolve())
    except ValueError:
        return None
    return f"{relative}/"


def _split_dirty(state: dict[str, Any], work: str) -> tool_paths.Split:
    """作業ツリーの未コミット変更を、利用者の変更とツールのパスに分ける。制御用ディレクトリは除く。"""
    control = _control_prefix(state, work)
    paths = sorted(path for path in _worktree_changes(work) if not (control and path.startswith(control)))
    return tool_paths.split_changes(paths, tool_paths.load_or_die(work, ABORT))


def _dirty_paths(state: dict[str, Any], work: str) -> list[str]:
    """作業ツリーの未コミット変更のパス。制御用ディレクトリとツールのパスは除く。

    ツールのパスを除くので、同期コミット（このパスだけを `git add` する）にも入らない。
    """
    return _split_dirty(state, work).user


def _discard_worktree_changes(work: str) -> None:
    """作業ツリーと index の未コミット変更を捨てる。**着手前が綺麗なときだけ呼ぶ。**

    **index も戻す。** `git checkout -- .` は staged された差分を戻さないため、
    同期コマンドが `git add` してから失敗すると清浄性のチェックが通らないままになり、
    `pending_push` の再試行が永久に進まない。

    無視されたファイル（制御用ディレクトリを含む）は消さない（`git clean` に
    `-x` を付けない）。
    """
    for args in (["reset", "--hard", "HEAD"], ["clean", "-fd"]):
        subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)


def discard_impl_leftovers(state: dict[str, Any], work: str) -> None:
    """実装担当が残した未コミットの変更を捨てる。取り込みの前に呼ぶ。

    **公開は進行側が検証を通してから行う**ので、コミットされなかった変更は
    どの検証も受けていない。Pull Request へ出す道が無い以上、残す意味がない。

    残したまま進むと、push の直前の清浄性のチェックで中断する。実測では、修正
    手順でコミットを作れなかった実装担当が直しかけの差分を置いたまま終え、
    続く `merge-fix` が「修正 0 件」として先へ進むこともできなくなった。

    制御用ディレクトリ（状態・結果・ログ）は無視の設定で守られており、
    `git clean` に `-x` を付けないため消えない。
    """
    if not pathlib.Path(work).is_dir():
        return
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = "、".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    _discard_worktree_changes(work)
    info(f"🧹 コミットされなかった変更を捨てました（{shown}{more}）。検証を受けていないため公開しません")


def _require_clean_worktree(state: dict[str, Any], work: str) -> None:
    """同期の前に作業ツリーが綺麗であることを求める。汚れていたら中断する。

    汚れたまま同期すると、**同期が作った差分と元からあった差分を区別できない**。
    区別しようと状態コードを比べても足りず、次の 2 つを取りこぼす。

    - 元から ` M` のファイルを同期がさらに書き換えても、状態コードは ` M` のままで
      検知できない。その変更がコミットされず、**push がまた落ちる**
    - `git commit` は index の内容を全て含めるため、`git add` の対象を絞っても
      **先に staged だった変更が検証を受けないまま Pull Request へ入る**

    無視されたファイルはここに現れない。生成物やキャッシュを `.gitignore` へ
    入れてあれば止まらない。
    """
    dirty, tool = _split_dirty(state, work)
    if tool:
        info(tool_paths.describe(tool))
    if not dirty:
        return
    shown = ", ".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    die(
        f"生成物を同期する前に、作業ツリーへ未コミットの変更があります（{shown}{more}）。"
        "同期が作った差分と区別できず、検証を受けていない変更を公開しかねないため"
        "中断します。コミットするか `.gitignore` へ入れてから再実行してください"
    )
