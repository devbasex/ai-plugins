"""マージの後の後片付け: マージ済みの PR のブランチの worktree とローカルブランチを外し、メインディレクトリを取り込む。

PR 1 件ごとに、メインディレクトリの排他（`mainlock`）を取ってから次を行う（並列のレーンの間で 1 本ずつ）。

1. PR を GitHub から読む（`adapters.github.GitHub.pulls`）。マージされていなければ何も消さない
2. PR の head がベースブランチか本番ブランチなら何も消さない（`merged_decide.long_lived_keep`。develop から main への
   リリースの PR）。スプリントブランチを残すかを決める（`merged_decide.sprint_keep`）

ベースブランチ・本番ブランチ・取り込み方は、スプリントの開始時のスナップショット（`declaration.write_start_snapshot` が
書いたファイル）があればそれ、無ければ今のブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）から読む。
3. ブランチを checkout した worktree を外す。追跡しているファイルに未コミットの変更があれば外さない。外す前に公開を閉じて
   テスト環境を破棄する（できなければ外さない）。locked の worktree は何も動かさずに残す。追跡していないファイル
   （無視したものを含む。`git worktree remove` は無視したファイルを黙って消す）があれば、先に退避（`trash.evacuate`）
   してから `--force` を付けずに外す。退避できないファイルがあったとき・git が断ったときは外さずに理由を返す
   （git が断ったときは退避したファイルを worktree へ戻す。`trash.restore`）
   （`--force` は戻せない削除で人の承認が要るため、この経路では使わない。C4）
4. ローカルブランチを `git branch -d` で消す（`-D` は使わない。git がマージ済みと認めなければ残して理由を返す）

片付けの前に、メインディレクトリがベースブランチにいれば、`git fetch` で `refs/remotes/<リモート名>/<ベースブランチ>` を
更新してから `git merge --ff-only <リモート名>/<ベースブランチ>` で取り込む（`git branch -d` は HEAD に含まれたブランチだけを消す）。
最後に `git worktree prune` を打ち、worktree の消えた割り当てのテスト環境を回収する（`testenv.reclaim`）。
`git pull` は使わない（`FETCH_HEAD` をほかのセッションの fetch と取り合い、取り込みが止まる。#3317）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

from ndf.adapters.git import Git
from ndf.adapters.github import Pull
from ndf.core.branch_strategy import Strategy
from ndf.domains.worktree import declaration, mainlock, merged_decide, outcomes, removal, repo, testenv, trash

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


def _remove_worktree(main: Git, item: repo.Worktree, rec: Record, merge: str = "") -> bool:
    """worktree を外す。外せたかを返す。`merge` は PR のマージコミット（退避の台帳へ書く）。"""
    if item.path.is_dir():
        dirty = repo.dirty_paths(Git(item.path))
        if dirty:
            names = ", ".join(line[3:] for line in dirty[:LISTED]) + ("…" if len(dirty) > LISTED else "")
            rec.add("worktree", str(item.path), outcomes.KEPT, f"追跡しているファイルに未コミットの変更が {len(dirty)} 件ある（{names}）")
            return False
    if item.locked:  # 公開を閉じる・テスト環境を破棄するより先に見る（locked は何も動かさずに残す）
        rec.add("worktree", str(item.path), outcomes.KEPT, "locked の worktree（git worktree unlock の後に片付ける）")
        return False

    def remove() -> Optional[str]:
        done = main.attempt("worktree", "remove", str(item.path))
        return None if done.returncode == 0 else f"git worktree remove が断った: {done.stderr.strip()}"

    got = removal.remove_evacuating(main, item, merge, remove)
    if got.closed is not None:
        rec.add("worktree", str(item.path), outcomes.KEPT, got.closed)
        return False
    if got.trash_error is not None:
        rec.add("worktree", str(item.path), outcomes.KEPT, f"退避できないため外さない: {got.trash_error}")
        return False
    if got.refused is not None:
        rec.add("worktree", str(item.path), outcomes.KEPT, "。".join(filter(None, [got.refused, got.back])))
        return False
    rec.add("worktree", str(item.path), outcomes.REMOVED, _evacuated_note(got.got))
    return True


def _evacuated_note(got: Optional[trash.Evacuated]) -> str:
    """退避の結果の説明（退避していなければ空。捨てた生成物も添える）。"""
    if got is None:
        return ""
    why = f"退避先 {got.dest}" if got.dest is not None else "退避するものは無かった"
    if got.discarded:
        why += f"（捨てた生成物: {', '.join(got.discarded)}）"
    return why


def _delete_branch(main: Git, branch: str, rec: Record) -> None:
    if not main.has_ref(f"refs/heads/{branch}"):
        rec.add("branch", branch, outcomes.ABSENT)
        return
    sha = main.rev_parse(f"refs/heads/{branch}")
    refused = repo.delete_merged_branch(main, branch)
    if refused is None:
        rec.add("branch", branch, outcomes.DELETED, sha=sha, restore=f"git branch {branch} {sha}")
        return
    why = f"git がマージ済みと認めない（{repo.MANUAL_FORCE}）: {refused}"
    rec.add("branch", branch, outcomes.KEPT, why, sha=sha)


def _clean_branch(main: Git, main_dir: Path, branch: str, rec: Record, merge: str = "") -> None:
    free = True
    for item in main.worktrees():
        if item.branch != branch:
            continue
        if item.path == main_dir:
            rec.add("worktree", str(item.path), outcomes.KEPT, "メインディレクトリがこのブランチを checkout している")
            free = False
            continue
        free = _remove_worktree(main, item, rec, merge) and free
    if free:
        _delete_branch(main, branch, rec)


def _keep_reason(main: Git, branch: str, strategy: Strategy, remote: str) -> Optional[str]:
    """ブランチを残す理由（消してよければ `None`）。"""
    keep = merged_decide.long_lived_keep(branch, strategy.base, strategy.production)
    if keep is None:
        contained = repo.contained_in_production(main, branch, strategy.production, remote)
        keep = merged_decide.sprint_keep(branch, strategy, contained, remote)
    return keep


def _one(main: Git, main_dir: Path, pull: Pull, keep: Optional[str], rec: Record) -> None:
    if keep is not None:
        rec.add("branch", pull.head_ref, outcomes.KEPT, keep)
        return
    _clean_branch(main, main_dir, pull.head_ref, rec, pull.merge_sha)


def sync_main(main: Git, base: str, remote: str, rec: Record) -> None:
    """メインディレクトリがベースブランチにいれば fast-forward で取り込む。失敗は `rec.stopped` に書く。"""
    skip = merged_decide.pull_skip(repo.current_branch(main), base)
    if skip is not None:
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, skip)
        return
    fetched = main.fetch_branch(remote, base)
    if fetched.returncode != 0:
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, f"git fetch が失敗した（取り込まない）: {fetched.stderr.strip()}")
        return
    done = main.merge_ff(f"{remote}/{base}")
    if done.returncode == 0:
        rec.add("main_dir", str(main.cwd), outcomes.PULLED)
        return
    dirty = repo.dirty_paths(main)
    if dirty:
        # 利用者の手元の変更には触れない（stash も checkout もしない）。取り込みだけを見送る
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, f"未コミットの変更（{len(dirty)} 件）が取り込みを塞いだため取り込まない")
        return
    rec.stopped = f"メインディレクトリの git merge --ff-only {remote}/{base} が失敗: {done.stderr.strip()}"
    rec.add("main_dir", str(main.cwd), outcomes.STOPPED, rec.stopped)


def strategy_of(main_dir: Path, snapshot: Optional[Path]) -> Strategy:
    """開始時のスナップショット（渡したとき）か今の宣言のセクション。読めなければ `DeclarationError`。"""
    return declaration.read_snapshot(snapshot) if snapshot is not None else declaration.strategy(main_dir)


def after_merge(main_dir: Path, numbers: list[int], hub: PullsReader, snapshot: Optional[Path] = None) -> Record:
    """マージの後の後片付けを行い、記録を返す。"""
    main = Git(main_dir)
    declaration.load(main_dir)  # 旧いキーの残る worktree の宣言では片付けない
    strategy = strategy_of(main_dir, snapshot)
    pulls = hub.pulls(numbers)
    rec = Record()
    base = strategy.base
    remote = main.remote()  # エントリポイントで 1 回だけ求める（PR ごとに `git worktree list` を打たない）
    # 先に取り込む: `git branch -d` はメインディレクトリの HEAD に含まれたブランチだけを消す
    with mainlock.held(main):
        sync_main(main, base, remote, rec)
    for n in numbers:
        pull = pulls.get(n)
        if pull is None or not pull.merged:
            rec.add("pr", str(n), outcomes.KEPT, "PR が見つからない" if pull is None else "PR がマージされていない")
            continue
        with mainlock.held(main):
            _one(main, main_dir, pull, _keep_reason(main, pull.head_ref, strategy, remote), rec)
    main.run("worktree", "prune")
    # worktree の消えた割り当てのテスト環境を回収する（排他の外で行う。並列のレーンの後片付けを待たせない）
    t = testenv.target(main_dir)
    if t is not None:
        rec.items.extend({**i, "swept": True} for i in testenv.reclaim(t))
    return rec
