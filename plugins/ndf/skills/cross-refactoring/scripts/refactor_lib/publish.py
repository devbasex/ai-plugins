"""push の直前の生成物の同期と、head ブランチへの push。

**公開するのはオーケストレーターだけである。** 認証の退避の値はライブラリ（`scripts/lib/git-credential.sh`）が持つ。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import statefile

from . import die, info, timeline, worktree
from .paths import sh
from .plan import format_plan, normalize_plan_file, publish_plan_comment
from .process import run_with_timeout
from .vocabulary import (
    PLAN_COMMIT_MESSAGE,
    SYNC_AND_PLAN_COMMIT_MESSAGE,
    SYNC_COMMIT_MESSAGE,
)


def _run_sync_command(state: dict[str, Any], work: str, command: str) -> None:
    """同期コマンドを実行する。失敗したら差分を捨てて中断する。

    **黙って push しない。** 同期できない状態を公開すると、利用者のリポジトリの
    チェックを壊したまま進むことになる。
    """
    code, timed_out = run_with_timeout(
        command, work, timeline.state_test_timeout(state)
    )
    if not (timed_out or code != 0):
        return
    # **途中まで書き換えた差分を残さない。** 残すと次の実行は
    # `_require_clean_worktree` で必ず止まり、`pending_push` の再試行が
    # 永久に進まなくなる。着手前が綺麗だったことは確認済みなので、
    # ここにある変更は全て同期が作ったものだと分かる。
    worktree._discard_worktree_changes(work)
    die(
        f"生成物の同期に失敗しました（{command}）: "
        + ("打ち切りました" if timed_out else f"終了コード {code}")
        + "。同期が作った差分は破棄したので、原因を直せばそのまま再開できます"
    )


def _write_plan_file(state: dict[str, Any], work: str, rel: str) -> None:
    """改修計画を作業ディレクトリの中へ書き出す。

    内容は状態から決まるので、**状態が動いていなければ差分は出ない**。
    書き出しを毎回行っても、余計なコミットは積まれない。
    """
    path = pathlib.Path(work) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_plan(state), encoding="utf-8")


def _publish_commit_message(produced: list[str], plan_rel: str) -> str:
    """公開の直前に積むコミットのメッセージを、中身に合わせて選ぶ。"""
    has_plan = bool(plan_rel) and plan_rel in produced
    has_generated = any(p != plan_rel for p in produced)
    if has_plan and has_generated:
        return SYNC_AND_PLAN_COMMIT_MESSAGE
    if has_plan:
        return PLAN_COMMIT_MESSAGE
    return SYNC_COMMIT_MESSAGE


def _commit_sync_changes(
    work: str, command: str, produced: list[str], plan_rel: str = ""
) -> None:
    """同期が作った差分を進行側のコミットとして積む。差分が無ければ何もしない。

    このコミットはどの改善項目にも属さない。取り消しでは積み直されないが、
    次の push で作り直されるので失われても問題にならない。
    """
    if not produced:
        return
    # **後段で落ちたときも差分を残さない。** `git add` / `git commit` の失敗で
    # 作業ツリーを汚したまま中断すると、次の実行は `_require_clean_worktree` で
    # 必ず止まり、`pending_push` の再試行が永久に進まない。捨ててよい根拠は
    # 同期コマンド自身が失敗したときと同じで、着手前が綺麗だったことを
    # 確認済みだからである。
    try:
        sh(["git", "add", "--", *produced], cwd=work)
        sh(["git", "commit", "-m", _publish_commit_message(produced, plan_rel)],
            cwd=work)
    except SystemExit:
        worktree._discard_worktree_changes(work)
        raise
    if command:
        info(f"🔧 生成物を同期しました（{command} / {len(produced)} ファイル）")
    else:
        info(f"📝 改修計画を記録しました（{len(produced)} ファイル）")


def _sync_generated(state: dict[str, Any]) -> None:
    """push の直前に生成物を同期し、差分があれば進行側のコミットとして積む。

    同期を**実装担当の責務にすると範囲外の変更が生まれ**、範囲のチェックで全件失敗する
    （実測ではラウンドの採用 5 件が全て範囲外で落ちた）。かといって同期しないと、
    生成物の同期をチェックする pre-push を持つリポジトリでは push そのものが通らず、
    取り消しを Pull Request へ反映できない。そこで**進行側が push の直前に同期する**。

    このコミットはどの改善項目にも属さない。取り消しでは積み直されないが、
    次の push で作り直されるので失われても問題にならない。

    同期に失敗したら中断する。**黙って push しない。** 同期できない状態を公開すると、
    利用者のリポジトリのチェックを壊したまま進むことになる。
    """
    command = str(state.get("sync_command") or "").strip()
    # 状態ファイルの値も受け取った時点と同じ基準で通す。旧い状態ファイルや
    # 手で書き換えられた値でも、作業ディレクトリの外へは書き出さない。
    plan_rel = normalize_plan_file(state.get("plan_file"))
    if not command and not plan_rel:
        return
    work = state["worktrees"]["work"]
    # **同期の前に作業ツリーが綺麗であることを求める。** 汚れたまま同期すると、
    # 同期が作った差分と元からあった差分を区別できない。
    worktree._require_clean_worktree(state, work)
    # 改修計画も生成物と同じ経路に乗せる。**別のコミットに分けない。**
    # 分けると、進行側のコミットが公開のたびに 2 つずつ積まれる。
    if plan_rel:
        _write_plan_file(state, work, plan_rel)
    if command:
        _run_sync_command(state, work, command)
    _commit_sync_changes(work, command, worktree._dirty_paths(state, work), plan_rel)


# 退避に使う値は共通層が 1 か所で持つ（#524）。**複製は持たない。** 手順書と実装が
# 別々に同じ文字列を持つと、片方だけが更新される。
_CREDENTIAL_LIB = (
    pathlib.Path(__file__).resolve().parents[4] / "scripts" / "lib" / "git-credential.sh"
)


def gh_available() -> bool:
    """`gh` を使えるか。使えなければ退避しても通らない。"""
    return shutil.which("gh") is not None


def credential_fallback_args() -> list[str]:
    """共通層が定める退避のオプションを読む。読めなければ空を返す。"""
    if not _CREDENTIAL_LIB.is_file():
        return []
    out = subprocess.run(
        ["bash", "-c", f'. "{_CREDENTIAL_LIB}"; ndf_git_credential_fallback_args'],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.split("\n") if line]


def _push_with_credential_fallback(args: list[str], cwd: str) -> None:
    """`git` を実行し、失敗したときだけ退避して**1 度だけ**再試行する。

    **既定の経路は変えない。** helper が正しく動く環境では 1 度目で終わる。
    再試行を 1 度に限るのは、認証以外の理由（参照の競合・ネットワークの不通）で
    失敗したときに同じ失敗を繰り返さないためである。
    """
    try:
        sh(["git", *args], cwd=cwd)
        return
    except Exception:
        fallback = credential_fallback_args() if gh_available() else []
        if not fallback:
            raise
    info("↻ credential helper を退避して push をやり直します（gh の認証を使う）")
    sh(["git", *fallback, *args], cwd=cwd)


def push_head(state: dict[str, Any]) -> None:
    """head ブランチへ push する。**`--force` は使わない。**

    **公開するのは進行側だけである。** 実装担当に push させると、検証を通る前に
    変更が Pull Request へ現れ、取り消しの反映漏れがそのまま残る。
    """
    _sync_generated(state)
    _push_with_credential_fallback(
        ["push", "origin", f"HEAD:{state['head_branch']}"],
        state["worktrees"]["work"],
    )
    # **改修計画のコメントは push の後で更新する**（#436 決定 6）。差分に混ざらない
    # ので push とは独立だが、公開した内容と食い違わないよう後ろへ置く。投稿に
    # 失敗しても進行は止めない（`publish_plan_comment` が出力へ残す）。
    publish_plan_comment(state)


def push_with_retry_marker(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """保留のフラグを立ててから push し、成功したらフラグを消す。

    フラグを残さずに push すると、失敗したときに**取り消しがローカルだけに留まる**。
    処理済みガードで次回は素通りするため、Pull Request へ永久に反映されない。
    """
    entry["pending_push"] = True
    statefile.save(path, state)
    push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def flush_pending_push(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回やり残した push を、処理済みの判定より**先に**片づける。"""
    if not entry.get("pending_push"):
        return
    info("↻ 前回 push できなかった取り消しを反映します")
    push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)
