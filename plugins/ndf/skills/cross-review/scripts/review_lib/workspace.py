"""レビューの worktree の作成と、PR の head への同期（#1142 の C2）。

`_sync_worktree` の 1 つの手順（head の解決・取得・除外・差分の確認・巻き戻し・掃除）を関数に分けて持つ。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
from typing import NamedTuple

import review_lib  # noqa: E402
import gh_call  # noqa: E402
from review_lib import github  # noqa: E402


def _default_worktree_base() -> pathlib.Path:
    """worktree の親ディレクトリを解決する。

    優先順位:
      1. 環境変数 NDF_WORKTREE_BASE (明示オーバーライド)
      2. <システム tmpdir>/ndf-worktrees (非永続領域。コンテナ再作成で自動消滅)

    かつての /work/worktrees ($HOME/work/worktrees) は共有の永続 volume 上にあり、
    別リポジトリの pr<N> と衝突する・明示削除が必要・volume を消費する問題が
    あったため廃止した。
    """
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        # 相対パスのまま state.json に保存されると後続のパス比較が壊れるため、
        # 常に絶対パスへ解決して返す。
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def _is_registered_worktree(path: str) -> bool:
    """path が現リポジトリに登録済みの worktree かどうか。

    パスが存在しても別リポジトリの残骸や git 管理外ディレクトリの可能性があり、
    流用すると git 操作が壊れるため、流用前に必ずこれで検証する。
    """
    out = review_lib._sh(["git", "worktree", "list", "--porcelain"], check=False)
    target = str(pathlib.Path(path).resolve())
    return any(line == f"worktree {target}" for line in out.splitlines())


def _create_worktree(worktree: str, pr: int, head_branch: str) -> None:
    """origin/<head> から detached worktree を作成する (フォーク PR はフォールバック)。"""
    pathlib.Path(worktree).parent.mkdir(parents=True, exist_ok=True)
    # worktree を /tmp 等の非永続領域に置くと、実体だけ消えて親リポジトリの
    # 登録 (prunable) が残ることがある。その状態で `git worktree add` すると
    # 「パス登録済み」として失敗するため、追加前に prune で掃除しておく。
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True, text=True,
    )
    # フォーク PR の場合 origin に head_branch がないことがある。
    # fetch 失敗時は gh pr checkout --detach でフォールバックする。
    fetch_result = subprocess.run(
        ["git", "fetch", "origin", head_branch],
        capture_output=True, text=True,
    )
    if fetch_result.returncode == 0:
        # head branch が既に別の worktree で checkout されている場合を避けるため
        # detached で展開する。cross-review はファイル参照しかしないので問題ない。
        review_lib._sh(["git", "worktree", "add", "--detach", worktree, f"origin/{head_branch}"])
        review_lib.info(f"✅ worktree 作成 (detached @ origin/{head_branch}): {worktree}")
    else:
        review_lib.info(f"⚠ git fetch origin {head_branch} 失敗 (フォーク PR の可能性) — gh pr checkout でフォールバック")
        review_lib._sh(["git", "worktree", "add", "--detach", worktree, "HEAD"])
        # worktree 内で gh pr checkout を実行して正しいコミットに切り替え
        checkout_result = gh_call.gh(["pr", "checkout", str(pr), "--detach"], cwd=worktree)
        if checkout_result.returncode != 0:
            # HEAD (親コミット) 指向のまま残すと、次回実行時に
            # _is_registered_worktree() を通過して不正流用されるため、
            # die() の前に作成済み worktree をロールバックする。
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                capture_output=True, text=True,
            )
            review_lib.die(f"gh pr checkout --detach #{pr} 失敗: {checkout_result.stderr.strip()}")
        review_lib.info(f"✅ worktree 作成 (gh pr checkout --detach #{pr}): {worktree}")


class HeadRef(NamedTuple):
    """レビュー対象の Pull Request の head。

    比較の基準は `oid`（`gh pr view --json headRefOid` が返すコミット）で、
    `origin/<branch>` ではない。フォークの Pull Request では head branch が base の
    リポジトリに無いため、`origin/<branch>` は解決できない。基準を ref で持つと、
    未 push のコミットの検出も一致判定も、フォークのときだけ行えなくなる。
    """

    branch: str
    oid: str
    is_fork: bool


def _resolve_head_ref(pr: int, code: int = 8, repo: str | None = None) -> HeadRef:
    """その時点の head を GitHub から取り直す。

    状態ファイルの `head_branch` は `init` が書いた後に更新されない。`squash` の
    巻き直しは `<branch>-r<HHMMSS>` という新しいブランチを作るため、そのまま使うと
    **巻き直しの後に巻き直し前のブランチへ戻すことになる**。取れないときは
    状態ファイルの古い値へ落とさずに止める。落ちた先が誤っていては意味が無い。

    照会は REST の 1 回で、ブランチ名・commit・フォークの別が同じ応答から取れる。
    """
    meta = github._fetch_pr_metadata(pr, repo)
    if meta is None:
        review_lib.die(f"PR #{pr} の head を取得できない", code=code)
        raise SystemExit(code)  # die は戻らないが、型のために置く
    if not meta.head_branch or not meta.head_sha:
        review_lib.die(f"PR #{pr} の head が空である", code=code)
    return HeadRef(branch=meta.head_branch, oid=meta.head_sha, is_fork=meta.is_fork)


def _fetch_head(worktree: str, pr: int, head: HeadRef) -> bool:
    """基準のコミットを手元へ取り込み、手元にあるかを返す。

    取り込みの宛先だけが Pull Request の種別で変わる。フォークは base のリポジトリ側の
    `refs/pull/<番号>/head` から引く。取り込みの後に確かめるのは、`gh pr view` と
    `git fetch` の間に head が動いていると、取り込んだ内容に基準が含まれないためである。
    """
    target = f"refs/pull/{pr}/head" if head.is_fork else head.branch
    subprocess.run(
        ["git", "fetch", "origin", target],
        capture_output=True, text=True, cwd=worktree,
    )
    have = subprocess.run(
        ["git", "cat-file", "-e", f"{head.oid}^{{commit}}"],
        capture_output=True, text=True, cwd=worktree,
    )
    return have.returncode == 0


def _sync_exclusions(worktree: str) -> list[str]:
    """掃除（`git clean`）から外すパスを返す。

    状態ファイルと結果ファイルは tmp ディレクトリにある。`.cross_review/` が
    `.gitignore` に載っているのはこのリポジトリの都合で、レビュー対象のリポジトリで
    載っている保証は無い。載っていなければ、ラウンドごとの掃除がそれらを消す。
    消えると次の読み込みで止まり、振動検知は前のラウンドの payload を失う。
    """
    names = [".cross_review"]
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        try:
            rel = pathlib.Path(env).resolve().relative_to(pathlib.Path(worktree).resolve())
        except ValueError:
            rel = None
        if rel is not None and str(rel) not in ("", ".") and str(rel) not in names:
            names.append(str(rel))
    return names


def _worktree_changes(
    worktree: str,
    exclusions: list[str],
    code: int = 8,
) -> tuple[list[str], list[str]]:
    """作業ツリーの変更を、追跡対象と追跡対象外に分けて返す。

    `git status --porcelain` は先頭 2 文字が状態、3 文字目が空白、4 文字目からがパスである。
    **行全体を strip しない。** 先頭が空白の状態（` M path` など）でパスが 1 文字ずれる。
    """
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=worktree,
    )
    if r.returncode != 0:
        review_lib.die(f"作業ツリーの状態を読み取れない: {r.stderr.strip()[:200]}", code=code)
    tracked: list[str] = []
    untracked: list[str] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if status == "??":
            if any(path == e or path.startswith(f"{e}/") for e in exclusions):
                continue
            untracked.append(path)
        else:
            tracked.append(path)
    return tracked, untracked


def _is_synced(
    worktree: str,
    pr: int,
    head: HeadRef,
    exclusions: list[str],
    code: int,
) -> bool:
    """基準と突き合わせ、書き換えが要らないかを返す。失われるものがあるときは止める。

    **`strict=True` の経路からだけ呼ぶ。** 見つかる変更は、同じループの修正の工程が
    今まさに残したものである。捨てると修正そのものが失われ、しかも失われたことが
    誰にも見えない。
    """
    tracked, untracked = _worktree_changes(worktree, exclusions, code)
    if tracked:
        review_lib.die(
            "作業ツリーに未 push の変更が残っています: "
            f"{' '.join(tracked[:10])}。"
            " 修正を push してから次のラウンドを開始してください",
            code=code,
        )
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{head.oid}..HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    if ahead.returncode != 0:
        review_lib.die(
            f"基準 {head.oid[:7]} からの差を数えられない: {ahead.stderr.strip()[:200]}",
            code=code,
        )
    try:
        extra = int(ahead.stdout.strip() or "0")
    except ValueError:
        # 数えられない値を 0 と読むと、未 push のコミットを見落として捨てることになる。
        review_lib.die(f"基準 {head.oid[:7]} からの差を読み取れない: {ahead.stdout.strip()[:80]}", code=code)
    if extra > 0:
        review_lib.die(
            f"作業ツリーに PR #{pr} の head へ含まれないコミットが {extra} 件あります。"
            " push してから次のラウンドを開始してください",
            code=code,
        )
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    if current.returncode != 0 or current.stdout.strip() != head.oid or untracked:
        return False
    review_lib.info(f"✔ worktree は PR #{pr} の head と同期済み: {head.oid[:7]}")
    return True


def _reset_worktree_head(
    worktree: str, pr: int, target: str | None, code: int,
) -> None:
    """基準へ巻き戻す。基準が無ければ PR の checkout へフォールバックする。"""
    if target is not None:
        reset = subprocess.run(
            ["git", "reset", "--hard", target],
            capture_output=True, text=True, cwd=worktree,
        )
        if reset.returncode != 0:
            review_lib.die(f"worktree を {target} へ同期できない: {reset.stderr.strip()}", code=code)
    else:
        checkout = gh_call.gh(["pr", "checkout", str(pr), "--detach"], cwd=worktree)
        if checkout.returncode != 0:
            review_lib.die(f"gh pr checkout --detach #{pr} 失敗: {checkout.stderr.strip()}", code=code)


def _clean_untracked_files(worktree: str, exclusions: list[str], code: int) -> None:
    """除外パスを残して追跡対象外のファイルを掃除する。"""
    clean = subprocess.run(
        ["git", "clean", "-fd", *[a for e in exclusions for a in ("-e", e)]],
        capture_output=True, text=True, cwd=worktree,
    )
    if clean.returncode != 0:
        # 消せないまま進むと、残骸を抱えた作業ツリーで fix 担当が `git add -A` を
        # 使い、Pull Request へ混ざる。差分そのものは合っていても止める。
        review_lib.die(f"追跡対象外のファイルを消せない: {clean.stderr.strip()}", code=code)


def _resolve_sync_target(
    worktree: str, pr: int, head: str | HeadRef,
) -> tuple[bool, str, str]:
    """同期する基準を取り込み、手元の有無・対象・表示名を返す。"""
    if isinstance(head, HeadRef):
        return _fetch_head(worktree, pr, head), head.oid, head.branch

    # 旧来の呼び出し（ブランチ名だけを渡す経路）。基準は `origin/<branch>` になる。
    fetch = subprocess.run(
        ["git", "fetch", "origin", head],
        capture_output=True, text=True,
    )
    return fetch.returncode == 0, f"origin/{head}", head


def _has_unpushed_commits(worktree: str, target: str) -> bool:
    """作業ツリーの HEAD が基準より先へ進んでいて、基準がその祖先であるかを返す。

    修正待ちから再開したとき、修正の工程が作ったコミットはまだ push されていない。
    これを巻き戻すと修正が捨てられ、`merge-fix` が止まる。数えられないときや
    分かれているとき（force push の後など）は False を返し、これまでどおり巻き戻す。
    """
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{target}..HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    try:
        extra = int(ahead.stdout.strip() or "0") if ahead.returncode == 0 else 0
    except ValueError:
        extra = 0
    if extra <= 0:
        return False
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", target, "HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    return ancestor.returncode == 0


def _sync_worktree(
    worktree: str,
    pr: int,
    head: str | HeadRef,
    *,
    strict: bool = False,
) -> None:
    """既存 worktree を PR の head へ同期する。

    worktree は使い捨ての領域だが、実際には前回の実行のものがそのまま残る。
    同期せずに流用すると、**レビュー担当は古い差分を読む**。指摘は現在の PR に
    存在しない行に対して出るか、直したはずの箇所へ再び出る。どちらも投稿されて
    しまうため、読む側からは見分けが付かない。

    前回の実行が残した追跡対象外のファイルも消す。残したまま fix 担当が
    `git add -A` を使うと、レビューと無関係なファイルが Pull Request へ混ざる。
    tmp ディレクトリは `-e` で除外するため、state.json と result.json は残る。

    `strict` は、失われるものの扱いと止めるときの終了コードを切り替える。

    | 観点 | `strict=False`（init / 再開） | `strict=True`（ラウンドの開始） |
    | --- | --- | --- |
    | head と一致していて変更が無い | 巻き戻して掃除する | 何もしない |
    | 基準の先へ進んだ未 push のコミット | 巻き戻さずに残す | 止める |
    | 追跡対象の変更 / 基準から分かれたコミット | 捨てる | 止める |
    | 基準を手元に持てない | `gh pr checkout --detach` へ落とす | 止める |
    | 止めるときの終了コード | 1 | 8 |

    `init` の側を強くしないのは、そこで見つかる残骸が**前回の実行のもの**だからである。
    ラウンドの開始時に見つかる変更は、**同じループの修正の工程が今まさに残したもの**で
    あり、意味が違う。
    """
    code = 8 if strict else 1
    exclusions = _sync_exclusions(worktree)
    have_base, target, label = _resolve_sync_target(worktree, pr, head)

    if have_base:
        if strict and isinstance(head, HeadRef) and _is_synced(
                worktree, pr, head, exclusions, code):
            return
        if not strict and _has_unpushed_commits(worktree, target):
            review_lib.info(
                f"↷ 作業ツリーに PR #{pr} の head より先の未 push のコミットがあるため巻き戻さない"
            )
            return
    elif strict:
        # HEAD を動かす前に、何が失われるかを数える材料が無い（基準が手元に無いのだから、
        # 未 push のコミットを数えられない）。判定できない状態でフォールバックしない。
        review_lib.die(
            f"PR #{pr} の基準のコミット {target[:7]} を取り込めない。"
            " ネットワークか権限を確認してください",
            code=code,
        )
    else:
        # フォーク PR は origin に head branch が無い。作成時と同じ経路で合わせる。
        review_lib.info(f"⚠ git fetch origin {label} 失敗 (フォーク PR の可能性) — gh pr checkout でフォールバック")
    _reset_worktree_head(worktree, pr, target if have_base else None, code)
    _clean_untracked_files(worktree, exclusions, code)
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    sha = rev.stdout.strip() if rev.returncode == 0 else "?"
    review_lib.info(f"↻ 既存 worktree を PR #{pr} の head へ同期: {sha}")
