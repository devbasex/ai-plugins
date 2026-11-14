"""マージの後の後片付け: マージ済みの PR のブランチの worktree とローカルブランチを外し、メインディレクトリを取り込む。

PR 1 件ごとに、メインディレクトリの排他（`mainlock`）を取ってから次を行う（並列のレーンの間で 1 本ずつ）。

1. PR を GitHub から読む（`adapters.github.GitHub.pulls`）。マージされていなければ何も消さない
2. PR の head がベースブランチか本番チャネルなら何も消さない（`merged_decide.long_lived_keep`。develop から main への
   リリースの PR）。スプリントブランチを残すかを決める（`merged_decide.sprint_keep`。開始時の記録 `flow_record` があればそれ、無ければ今の宣言）
3. ブランチを checkout した worktree を外す。追跡しているファイルに未コミットの変更があれば外さない。外す前に公開を閉じて
   テスト環境を破棄する（できなければ外さない）。locked の worktree は何も動かさずに残す。追跡していないファイル
   （無視したものを含む。`git worktree remove` は無視したファイルを黙って消す）があれば、先に退避（`trash.evacuate`）
   してから `--force` を付けずに外す。退避できないファイルがあったとき・git が断ったときは外さずに理由を返す
   （git が断ったときは退避したファイルを worktree へ戻す。`trash.restore`）
   （`--force` は戻せない削除で人の承認が要るため、この経路では使わない。C4）
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

from ndf.adapters.git import ORIGIN, Git, GitError
from ndf.adapters.github import Pull
from ndf.domains.worktree import declaration, mainlock, merged_decide, outcomes, repo, testenv, testenv_sweep, trash

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
    flow = decl.branch_flow
    out: dict[str, Any] = dict(flow) if flow is not None else {}
    out["production_branch"] = decl.production_branch
    return out


def _contained(main: Git, branch: str, flow: Optional[Mapping[str, Any]]) -> Optional[bool]:
    production = flow.get("production_branch") if flow is not None else None
    if not production:
        return None
    try:
        return main.is_ancestor(f"refs/heads/{branch}", f"refs/remotes/{ORIGIN}/{production}")
    except GitError:  # noqa: NDF401 外部コマンド: 参照が無い（取得していない本番チャネル）なら確かめられないとして残す側へ倒す
        return None


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
    closed = testenv_sweep.close_for_removal(item.path)
    if closed is not None:
        rec.add("worktree", str(item.path), outcomes.KEPT, closed)
        return False
    why = ""
    got: Optional[trash.Evacuated] = None
    if item.path.is_dir() and trash.leftovers(item.path):
        try:
            got = trash.evacuate(main, item.path, str(item.branch), item.head, merge)
        except trash.TrashError as exc:
            rec.add("worktree", str(item.path), outcomes.KEPT, f"退避できないため外さない: {exc}")
            return False
        why = f"退避先 {got.dest}" if got.dest is not None else "退避するものは無かった"
        if got.discarded:
            why += f"（捨てた生成物: {', '.join(got.discarded)}）"
    done = main.attempt("worktree", "remove", str(item.path))
    if done.returncode != 0:
        refused = f"git worktree remove が断った: {done.stderr.strip()}"
        back = _restore_note(item.path, got)
        rec.add("worktree", str(item.path), outcomes.KEPT, "。".join(filter(None, [refused, back])))
        return False
    rec.add("worktree", str(item.path), outcomes.REMOVED, why)
    return True


def _restore_note(worktree: Path, got: Optional[trash.Evacuated]) -> str:
    """外せなかった worktree へ退避したファイルを戻し、その結果の説明（退避していなければ空）。"""
    if got is None or got.dest is None:
        return ""
    failed = trash.restore(worktree, got)
    return f"退避したファイルを戻せなかった: {failed}" if failed is not None else "退避したファイルを worktree へ戻した"


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


def _one(main: Git, main_dir: Path, pull: Pull, flow: Optional[Mapping[str, Any]], base: Optional[str], rec: Record) -> None:
    branch = pull.head_ref
    production = flow.get("production_branch") if flow is not None else None
    keep = merged_decide.long_lived_keep(branch, base, production if isinstance(production, str) else None)
    if keep is None:
        keep = merged_decide.sprint_keep(branch, flow, _contained(main, branch, flow), ORIGIN)
    if keep is not None:
        rec.add("branch", branch, outcomes.KEPT, keep)
        return
    _clean_branch(main, main_dir, branch, rec, pull.merge_sha)


def sync_main(main: Git, base: Optional[str], rec: Record) -> None:
    """メインディレクトリがベースブランチにいれば fast-forward で取り込む。失敗は `rec.stopped` に書く。"""
    skip = merged_decide.pull_skip(repo.current_branch(main), base)
    if skip is not None:
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, skip)
        return
    fetched = main.fetch_branch(ORIGIN, base)
    if fetched.returncode != 0:
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, f"git fetch が失敗した（取り込まない）: {fetched.stderr.strip()}")
        return
    done = main.attempt("merge", "--ff-only", "-q", f"{ORIGIN}/{base}")
    if done.returncode == 0:
        rec.add("main_dir", str(main.cwd), outcomes.PULLED)
        return
    dirty = repo.dirty_paths(main)
    if dirty:
        # 利用者の手元の変更には触れない（stash も checkout もしない）。取り込みだけを見送る
        rec.add("main_dir", str(main.cwd), outcomes.KEPT, f"未コミットの変更（{len(dirty)} 件）が取り込みを塞いだため取り込まない")
        return
    rec.stopped = f"メインディレクトリの git merge --ff-only {ORIGIN}/{base} が失敗: {done.stderr.strip()}"
    rec.add("main_dir", str(main.cwd), outcomes.STOPPED, rec.stopped)


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
    base = repo.base_branch(main, decl)
    # 先に取り込む: `git branch -d` はメインディレクトリの HEAD に含まれたブランチだけを消す
    with mainlock.held(main):
        sync_main(main, base, rec)
    for n in numbers:
        pull = pulls.get(n)
        if pull is None or not pull.merged:
            rec.add("pr", str(n), outcomes.KEPT, "PR が見つからない" if pull is None else "PR がマージされていない")
            continue
        with mainlock.held(main):
            _one(main, main_dir, pull, flow, base, rec)
    main.run("worktree", "prune")
    # worktree の消えた割り当てのテスト環境を回収する（排他の外で行う。並列のレーンの後片付けを待たせない）
    t = testenv.target(main_dir)
    if t is not None:
        rec.items.extend({**i, "swept": True} for i in testenv.reclaim(t))
    return rec
