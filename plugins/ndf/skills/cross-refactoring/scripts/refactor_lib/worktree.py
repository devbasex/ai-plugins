"""コミットの取り消しと積み直し、worktree の未コミットの変更の掃除。"""
from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Optional

from . import die, info
from .paths import git_out


def revert_item_commits(
    state: dict[str, Any], item: dict[str, Any], dry_run: bool = False
) -> int:
    """改善項目のコミットを取り消し、取り消した件数を返す。

    **新しいコミットから順に戻す。** 逆順にすると後続の取り消しが競合する。
    取り消しに失敗したら中断する。半端な状態を Pull Request に残さない。

    適用の検証に失敗したときと、レビューが収束しなかったときの両方から呼ぶ。
    前者で呼ばないと、実装担当が既に push した差分が Pull Request に残り、
    以後のレビュー対象にも混入する。
    """
    # **取り消し済みなら何もしない。** push の失敗などで叩き直したときに、
    # 既に戻したコミットへもう一度 `git revert` を掛けると必ず失敗し、
    # そこから先へ進めなくなる。
    if item.get("reverted"):
        info(f"↩ {item['item_id']} は取り消し済みです")
        return 0

    work = state["worktrees"]["work"]
    shas = _order_newest_first(
        work, [s for s in (item.get("commits") or []) if isinstance(s, str) and s]
    )
    if dry_run:
        for sha in shas:
            info(f"（dry-run）git revert --no-edit {sha}")
        return len(shas)

    # 途中で失敗したら**着手前の HEAD まで戻す**。1 項目が複数のコミットを持つとき、
    # 先行して成功した取り消しだけが履歴に残ると、再実行で不整合になって進めなくなる。
    before = git_out(work, ["rev-parse", "HEAD"])
    revert_range(work, shas, before, prefix=f"{item['item_id']} の")
    item["reverted"] = True
    return len(shas)


def reset_hard(work: str, sha: Optional[str]) -> None:
    """着手前の HEAD へ戻す。半端な履歴を Pull Request に残さないための後始末。"""
    if sha:
        subprocess.run(["git", "reset", "--hard", sha], cwd=work,
                       capture_output=True, text=True)


def revert_range(
    work: str, ordered: list[str], before: Optional[str], prefix: str = ""
) -> None:
    """範囲を**新しい順に**全て取り消す。失敗したら着手前へ戻して中断する。

    範囲全体を新しい順にたどる取り消しは、履歴をそのまま逆再生するだけなので
    **競合しない**。競合するのは「一部のコミットだけを飛ばして戻す」ときである。
    """
    for sha in ordered:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work,
                           capture_output=True, text=True)
            reset_hard(work, before)
            die(
                f"{prefix}コミット {sha} を取り消せませんでした: {r.stderr.strip()[:400]}"
                f"（HEAD を {before} へ戻しました）"
            )


def replay_commits(work: str, shas: list[str]) -> Optional[dict[str, str]]:
    """残す項目のコミットを**古い順に**積み直し、`{元の SHA: 新しい SHA}` を返す。

    競合したら `None` を返す。**ここで中断しない。** どの項目を残せるか決められない
    だけなので、呼び出し側がラウンド全件の取り消しへ退避できる。
    """
    mapping: dict[str, str] = {}
    for sha in shas:
        r = subprocess.run(
            ["git", "cherry-pick", "--allow-empty", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "cherry-pick", "--abort"], cwd=work,
                           capture_output=True, text=True)
            info(f"⚠ {sha[:7]} を積み直せませんでした: {r.stderr.strip()[:200]}")
            return None
        mapping[sha] = git_out(work, ["rev-parse", "HEAD"]) or sha
    return mapping


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
    rank = {sha: i for i, sha in enumerate(history.split())}   # 0 が最も新しい
    resolved = {
        s: (git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"]) or s)
        for s in shas
    }
    return sorted(shas, key=lambda s: rank.get(resolved[s], len(rank)))


def _worktree_changes(work: str) -> dict[str, str]:
    """作業ツリーの変更を `パス → 状態` で返す。同期の前後を比べるために使う。

    無視されているファイルは現れない（`--porcelain` の既定）。改名は移動先の
    パスだけを見る。
    """
    # `core.quotePath` の既定（true）では、非 ASCII を含むパスが `"` で囲まれ
    # `\343` の形へエスケープされる。そのまま `git add` へ渡すと見つからない。
    out = git_out(
        work, ["-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
        strip=False,
    )
    changes: dict[str, str] = {}
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:            # 改名。移動先だけを対象にする
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
        relative = pathlib.Path(tmp_dir).resolve().relative_to(
            pathlib.Path(work).resolve()
        )
    except ValueError:
        return None
    return f"{relative}/"


def _dirty_paths(state: dict[str, Any], work: str) -> list[str]:
    """作業ツリーの未コミット変更のパス。制御用ディレクトリは除く。"""
    control = _control_prefix(state, work)
    return sorted(
        path for path in _worktree_changes(work)
        if not (control and path.startswith(control))
    )


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
    info(
        f"🧹 コミットされなかった変更を捨てました（{shown}{more}）。"
        "検証を受けていないため公開しません"
    )


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
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = ", ".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    die(
        f"生成物を同期する前に、作業ツリーへ未コミットの変更があります（{shown}{more}）。"
        "同期が作った差分と区別できず、検証を受けていない変更を公開しかねないため"
        "中断します。コミットするか `.gitignore` へ入れてから再実行してください"
    )
