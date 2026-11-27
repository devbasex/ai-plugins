"""レビューした HEAD と、実際に読んだ PR の確認（`drive.py` から分けた。#4369）。

`--reviewed-pr-out` を受けた呼び手は、ここが返した PR だけを「レビューが読んだ PR」として扱う。確かめられない回は None を返す。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional, Protocol

from ndf.adapters import git
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import store, threads
from ndf.domains.crossreview.settings import DriveStopped, RunConfig


class Run(Protocol):
    """確認に使う、駆動の 1 回の起動の値。`remote` は取得と送信に使うリモート名、`synced_head` は始めの同期で取得した先頭、
    `sent` は PR に届いた最後のコミット、`pushed` はこの起動で送った（送り直した分を含む）コミット。"""

    cfg: RunConfig
    g: git.Git
    data: dict[str, Any]
    warnings: list[str]
    read_pr: Optional[threads.ReadPr]
    remote: str
    synced_head: str
    sent: str
    pushed: set[str]


def read_by_all_this_run(data: Mapping[str, Any], rounds: int) -> Optional[str]:
    """今回の起動の `rounds` 件のラウンドのうち、相手役の全員が読めた最後のラウンドの head（無ければ None）。
    今回の起動で回答を得たラウンドが無ければ（全員が無回答など）、前の起動のラウンドの head を出さない（PR 3888 の r2-1）。
    一部の相手役が読めなかったラウンドの head は、次の差分レビューの起点にすると読んでいない差分を飛ばすので出さない（PR 3944 の r2-3）。"""
    this_run = store.list_of(data, store.ROUNDS)[-rounds:] if rounds > 0 else []
    for r in reversed(this_run):
        if isinstance(r, dict) and r.get("verdict") != fin.INCOMPLETE_REVIEWERS:
            head = r.get("head")
            return head if isinstance(head, str) and head else None
    return None


def seen(run: Run, pr: Optional[threads.ReadPr]) -> Optional[threads.ReadPr]:
    """読んだ PR の head ブランチがレビューするブランチで、head が同期で取得した先頭か送り直した後の先頭なら、その PR。
    `--base` がコミットの起動は同期の前に PR を読むので、送り直す前の head も受ける。"""
    if pr is None or pr.branch != run.cfg.branch or pr.head not in (run.synced_head, run.sent):
        return None
    return pr


def why_unread(run: Run, pr: Optional[threads.ReadPr]) -> Optional[str]:
    """読んだ PR の確認を求める起動で、読んだ `pr` を使えない理由（確認を求めないか、使えれば None）。呼び手は相手役を起動する
    前に止める（記録を出せないレビューに token と時間を使わない）。"""
    if not run.cfg.check_pr or run.read_pr is not None:
        return None
    if pr is None:
        return "PR のスレッドと head を読めない"
    return (
        f"PR {pr.ident} の head（{pr.branch} の {pr.head[:9]}）が、レビューするブランチ {run.cfg.branch} の"
        f"取得した先頭 {run.synced_head[:9]} と違う"
    )


def confirmed(run: Run, head: Optional[str]) -> Optional[str]:
    """レビューした `head` が、始めに読んだ PR の head か、この起動がその PR のブランチへ送った修正のコミットなら、その PR。
    読めなかった PR と、PR に無い手元のコミットを読んだ回は None（読んだ PR の記録を出さない）。読んだ PR の確認を求めない
    起動も None（push の送り先を調べる git を起動しない）。"""
    pr = run.read_pr
    if not run.cfg.check_pr or pr is None or head is None:
        return None
    if head == pr.head:
        return pr.ident
    return pr.ident if head in run.pushed and _pushes_to(run, pr.head_repo) else None


def _pushes_to(run: Run, head_repo: str) -> bool:
    """push の送り先がすべて PR の head のリポジトリか。`--repo` は GitHub の参照先を変えるだけで、push は作業場所のリモートへ
    送るので、ブランチ名と SHA が同じでも別のリポジトリのことがある。違うか確かめられなければ注意に載せる。"""
    try:
        targets = run.g.push_repos(run.remote)
    except git.GitError:
        targets = []
    if targets and all(t is not None and t.lower() == head_repo.lower() for t in targets):
        return True
    run.warnings.append(
        f"リモート {run.remote} の push 先が PR の head のリポジトリ {head_repo or '（不明）'} と一致すると確かめられないので、"
        "読んだ PR を記録しない"
    )
    return False


def fetch_repo(g: git.Git, remote: str) -> Optional[str]:
    """リモートの取得元の `<所有者>/<リポジトリ>`。GitHub の URL でないか読めなければ None。"""
    try:
        return g.remote_repo(remote)
    except git.GitError:  # noqa: NDF401 外部コマンド: 取得元を読めなければ、呼び手が一致を確かめられないとして止める
        return None


def check_base_source(cfg: RunConfig, reader_for: Optional[Callable[[int], threads.Reader]], g: git.Git, remote: str, branch: str) -> None:
    """PR の今のマージ先 `branch` を取得するリモートが、PR のベースリポジトリと一致すると確かめる。確かめられなければ、担当を
    起動する前に止める（`--reviewed-base-out` の起動。#3702）。

    マージ先の名前は GitHub の PR から読むが、ブランチは作業場所のリモートから取得する。`--repo` やフォークで両者が別の
    リポジトリだと、同名の別のブランチとの差分をレビューした結果を、承認へ渡す比較元として書いてしまう。
    """
    pr = store.state_pr(cfg.state_dir, cfg.pr).current_pr
    try:
        # 読む口が無い起動は、呼び手がマージ先を読む時点で止めている
        reader = reader_for(pr) if reader_for is not None else None
        target, known = (reader.repo(), reader.fetched()) if reader is not None else ("", None)
    except Exception as exc:
        raise DriveStopped("precondition", f"PR #{pr} のベースリポジトリを読めないので、比較元を決められない: {exc}") from exc
    # 対象を作業場所の同じリモートの URL から決めていれば、その値が取得元である（`git remote get-url` を打ち直さない）
    fetched = known if known is not None else fetch_repo(g, remote)
    if not target or fetched is None or fetched.lower() != target.lower():
        raise DriveStopped(
            "precondition",
            f"リモート {remote} の取得元（{fetched or '不明'}）が PR #{pr} のベースリポジトリ {target or '（不明）'} と"
            f"一致すると確かめられないので、マージ先 {branch} を比較元にできない",
        )
