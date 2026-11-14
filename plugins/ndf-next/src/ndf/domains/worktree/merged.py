"""マージの後の後片付け: マージ済みの PR のブランチの worktree とローカルブランチを外し、メインディレクトリを取り込む。

PR 1 件ごとに、メインディレクトリの排他（`mainlock`）を取ってから次を行う（並列のレーンの間で 1 本ずつ）。

1. PR を GitHub から読む（`adapters.github.GitHub.pulls`）。マージされていなければ何も消さない
2. スプリントブランチを残すかを決める（`merged_decide.sprint_keep`。開始時の記録 `flow_record` があればそれ、無ければ今の宣言）
3. ブランチを checkout した worktree を外す。追跡しているファイルに未コミットの変更があれば外さない。外す前に公開を閉じて
   テスト環境を破棄する（できなければ外さない）。`git worktree remove` が
   追跡していないファイルで断ったら、それを退避（`trash.evacuate`）してから外す
4. ローカルブランチを `git branch -d` で消す（`-D` は使わない。git がマージ済みと認めなければ残して理由を返す）

片付けの前に、メインディレクトリがベースブランチにいれば、`git fetch` で `refs/remotes/origin/<ベースブランチ>` を
更新してから `git merge --ff-only origin/<ベースブランチ>` で取り込む（`git branch -d` は HEAD に含まれたブランチだけを消す）。
最後に `git worktree prune` を打ち、worktree の消えた割り当てのテスト環境を回収する（`testenv.reclaim`）。
`git pull` は使わない（`FETCH_HEAD` をほかのセッションの fetch と取り合い、取り込みが止まる。#3317）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import Pull
from ndf.domains.worktree import create, declaration, mainlock, merged_decide, repo, testenv, testenv_sweep, trash

# 未コミットの変更を理由に並べるパスの上限
LISTED = 5


class PullsReader(Protocol):
    """番号ごとの PR を読むもの（`adapters.github.GitHub` が満たす。テストは差し替える）。"""

    def pulls(self, numbers: Iterable[int]) -> dict[int, Optional[Pull]]: ...


@dataclass
class Record:
    """後片付けの記録。`items` は 1 件ごとの `{kind, name, result, reason?}`。"""

    items: list[dict[str, Any]] = field(default_factory=list)
    stopped: Optional[str] = None

    def add(self, kind: str, name: str, result: str, reason: str = "", **extra: Any) -> None:
        item: dict[str, Any] = {"kind": kind, "name": name, "result": result, **extra}
        if reason:
            item["reason"] = reason
        self.items.append(item)

    def count(self, kind: str, result: str) -> int:
        return sum(1 for i in self.items if i["kind"] == kind and i["result"] == result)


def declared_flow(decl: Optional[declaration.Declaration]) -> Optional[dict[str, Any]]:
    """今の宣言の取り込み方と本番チャネル（開始時の記録の無いスプリントが使う）。"""
    if decl is None:
        return None
    flow = decl.raw.get("branch_flow")
    out: dict[str, Any] = dict(flow) if isinstance(flow, dict) else {}
    out["production_branch"] = decl.production_branch
    return out


def _contained(main: Git, branch: str, flow: Optional[Mapping[str, Any]]) -> Optional[bool]:
    production = flow.get("production_branch") if flow is not None else None
    if not production:
        return None
    try:
        return main.is_ancestor(f"refs/heads/{branch}", f"refs/remotes/{create.REMOTE}/{production}")
    except GitError:  # noqa: NDF401 外部コマンド: 参照が無い（取得していない本番チャネル）なら確かめられないとして残す側へ倒す
        return None


def _close_testenv(path: Path) -> Optional[str]:
    """外す前に公開を閉じてテスト環境を破棄する（定義と閉じる手段は worktree にある）。できなければ理由を返す。"""
    t = testenv.target(path) if path.is_dir() else None
    if t is None:
        return None
    try:
        testenv_sweep.unexpose(t)
        testenv.down(t)
    except testenv.TestenvError as exc:
        return f"テスト環境を閉じられない: {exc}"
    return None


def _remove_worktree(main: Git, item: repo.Worktree, rec: Record) -> bool:
    """worktree を外す。外せたかを返す。"""
    if item.path.is_dir():
        dirty = repo.dirty_paths(Git(item.path))
        if dirty:
            names = ", ".join(line[3:] for line in dirty[:LISTED]) + ("…" if len(dirty) > LISTED else "")
            rec.add("worktree", str(item.path), "kept", f"追跡しているファイルに未コミットの変更が {len(dirty)} 件ある（{names}）")
            return False
    closed = _close_testenv(item.path)
    if closed is not None:
        rec.add("worktree", str(item.path), "kept", closed)
        return False
    first = main.runner(["worktree", "remove", str(item.path)], main.cwd)
    if first.returncode == 0:
        rec.add("worktree", str(item.path), "removed")
        return True
    got = trash.evacuate(main, item.path, str(item.branch), item.head)
    main.run("worktree", "remove", "--force", str(item.path))
    why = f"退避先 {got.dest}" if got.dest is not None else "退避するものは無かった"
    if got.discarded:
        why += f"（捨てた生成物: {', '.join(got.discarded)}）"
    rec.add("worktree", str(item.path), "removed", why)
    return True


def _delete_branch(main: Git, branch: str, rec: Record) -> None:
    if not repo.has_ref(main, f"refs/heads/{branch}"):
        rec.add("branch", branch, "absent")
        return
    sha = main.rev_parse(f"refs/heads/{branch}")
    done = main.runner(["branch", "-d", branch], main.cwd)
    if done.returncode == 0:
        rec.add("branch", branch, "deleted", sha=sha, restore=f"git branch {branch} {sha}")
        return
    why = f"git がマージ済みと認めない（消すなら人が確かめて -D で消す）: {done.stderr.strip()}"
    rec.add("branch", branch, "kept", why, sha=sha)


def _clean_branch(main: Git, main_dir: Path, branch: str, rec: Record) -> None:
    free = True
    for item in main.worktrees():
        if item.branch != branch:
            continue
        if item.path == main_dir:
            rec.add("worktree", str(item.path), "kept", "メインディレクトリがこのブランチを checkout している")
            free = False
            continue
        free = _remove_worktree(main, item, rec) and free
    if free:
        _delete_branch(main, branch, rec)


def _one(main: Git, main_dir: Path, pull: Optional[Pull], number: int, flow: Optional[Mapping[str, Any]], rec: Record) -> None:
    if pull is None:
        rec.add("pr", str(number), "kept", "PR が見つからない")
        return
    if not pull.merged:
        rec.add("pr", str(number), "kept", "PR がマージされていない")
        return
    branch = pull.head_ref
    keep = merged_decide.sprint_keep(branch, flow, _contained(main, branch, flow))
    if keep is not None:
        rec.add("branch", branch, "kept", keep)
        return
    _clean_branch(main, main_dir, branch, rec)


def sync_main(main: Git, base: Optional[str], rec: Record) -> None:
    """メインディレクトリがベースブランチにいれば fast-forward で取り込む。失敗は `rec.stopped` に書く。"""
    skip = merged_decide.pull_skip(repo.current_branch(main), base)
    if skip is not None:
        rec.add("main_dir", str(main.cwd), "kept", skip)
        return
    fetched = main.fetch(create.REMOTE, f"+refs/heads/{base}:refs/remotes/{create.REMOTE}/{base}")
    if fetched.returncode != 0:
        rec.add("main_dir", str(main.cwd), "kept", f"git fetch が失敗した（取り込まない）: {fetched.stderr.strip()}")
        return
    done = main.runner(["merge", "--ff-only", "-q", f"{create.REMOTE}/{base}"], main.cwd)
    if done.returncode == 0:
        rec.add("main_dir", str(main.cwd), "pulled")
        return
    dirty = repo.dirty_paths(main)
    if dirty:
        # 利用者の手元の変更には触れない（stash も checkout もしない）。取り込みだけを見送る
        rec.add("main_dir", str(main.cwd), "kept", f"未コミットの変更（{len(dirty)} 件）が取り込みを塞いだため取り込まない")
        return
    rec.stopped = f"メインディレクトリの git merge --ff-only {create.REMOTE}/{base} が失敗: {done.stderr.strip()}"
    rec.add("main_dir", str(main.cwd), "stopped", rec.stopped)


def read_flow_record(path: Optional[Path]) -> Optional[dict[str, Any]]:
    """開始時の記録（`branch_flow` の写しに `production_branch` を足した JSON のオブジェクト）。読めなければ `DeclarationError`。"""
    if path is None:
        return None
    return declaration.read_object(path)


def after_merge(main_dir: Path, numbers: list[int], hub: PullsReader, flow_record: Optional[Mapping[str, Any]] = None) -> Record:
    """マージの後の後片付けを行い、記録を返す。"""
    main = Git(main_dir)
    decl = declaration.load(main_dir)
    flow = flow_record if flow_record is not None else declared_flow(decl)
    pulls = hub.pulls(numbers)
    rec = Record()
    base = decl.base_branch if decl is not None and decl.base_branch else create.default_branch(main)
    # 先に取り込む: `git branch -d` はメインディレクトリの HEAD に含まれたブランチだけを消す
    with mainlock.held(main):
        sync_main(main, base, rec)
    for n in numbers:
        with mainlock.held(main):
            _one(main, main_dir, pulls.get(n), n, flow, rec)
    main.run("worktree", "prune")
    # worktree の消えた割り当てのテスト環境を回収する（排他の外で行う。並列のレーンの後片付けを待たせない）
    t = testenv.target(main_dir)
    if t is not None:
        rec.items.extend({**i, "swept": True} for i in testenv.reclaim(t))
    return rec
