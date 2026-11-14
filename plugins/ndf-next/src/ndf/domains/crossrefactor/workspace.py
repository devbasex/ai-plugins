"""殻: cross-refactoring の作業場所の準備と片付け（#3106・#750）。

作業場所の根は `ndf.core.converge.place` の置き場の `rf<PR>`。中に次を置く。

| パス | 中身 |
| --- | --- |
| `work/` | 書き込み用の作業ディレクトリ（コードを書く工程の担当が動く。PR の head から detached で作る） |
| `read/<ランタイム>/` | 読み取り用ディレクトリ（提案・計画の担当が動く。工程の前に `work` の HEAD へ合わせる） |
| `logs/` | 担当の出力と、回収した結果ファイル |
| `state.json` | 状態ファイル（`store`） |

- 作る前に、持ち主のいない NDF の lock を片付ける（`worktree.sweep_orphans`）
- 作った直後に、ホストと時刻つきの lock を掛ける（`worktree.lock_review_worktree`）
- 再開のときは作り直さずに登録を確かめる。ディレクトリが無ければ作り直し、在って登録が消えていれば
  `WorktreeUnregistered` で止める（作業を失わない）
- 担当の結果ファイルの置き場（`.cross_refactoring/`）を git の除外へ足し、差分に入れない
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ndf.adapters import git
from ndf.core.converge import place, worktree
from ndf.domains.crossrefactor import launch_decide


@dataclass(frozen=True)
class Workspace:
    root: Path
    work: Path
    read_root: Path
    logs: Path
    runtimes: tuple[str, ...]

    def reader(self, runtime: str) -> Path:
        return self.read_root / runtime

    def worktrees(self) -> tuple[Path, ...]:
        return (self.work, *(self.reader(r) for r in self.runtimes))


def root_of(repo: git.Git, pr: int) -> Path:
    """PR の作業場所の根（<置き場>/rf<PR>）。"""
    return place.work_root(repo, f"rf{pr}")


def layout(root: Path, runtimes: Sequence[str]) -> Workspace:
    return Workspace(root, root / "work", root / "read", root / "logs", tuple(runtimes))


def _exclude_results(g: git.Git) -> None:
    """結果ファイルの置き場を git の除外（`info/exclude`）へ足す。足してあれば何もしない。"""
    path = Path(g.run("rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if launch_decide.IGNORE_ENTRY not in text.splitlines():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + launch_decide.IGNORE_ENTRY + "\n", encoding="utf-8")


def _add(repo: git.Git, path: Path, commit: str, pr: int) -> None:
    """作業場所を `commit` から detached で作り、lock を掛ける。在れば登録を確かめて作り直さない。"""
    if worktree.resumable(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    repo.run("worktree", "add", "--detach", str(path), commit)
    worktree.lock_review_worktree(repo, path, launch_decide.SKILL, pr)


def prepare(repo: git.Git, pr: int, branch: str, runtimes: Sequence[str]) -> Workspace:
    """作業場所を用意する（再開なら登録を確かめて使う）。`repo` はリポジトリのどこかの作業場所の git。"""
    worktree.sweep_orphans(repo)
    ws = layout(root_of(repo, pr), runtimes)
    head = worktree.fetch_head(repo, branch)
    _add(repo, ws.work, head, pr)
    work_head = git.Git(ws.work).rev_parse("HEAD")
    for runtime in ws.runtimes:
        _add(repo, ws.reader(runtime), work_head, pr)
    _exclude_results(git.Git(ws.work))
    ws.logs.mkdir(parents=True, exist_ok=True)
    return ws


def refresh_readers(ws: Workspace) -> str:
    """読み取り用ディレクトリを書き込み用の作業ディレクトリの HEAD へ合わせ、その HEAD を返す。"""
    head = git.Git(ws.work).rev_parse("HEAD")
    for runtime in ws.runtimes:  # noqa: NDF201 上限: 担当の数（ランタイムの表の既定の席）
        worktree.detach_clean(git.Git(ws.reader(runtime)), head)
    return head


def release(repo: git.Git, ws: Workspace) -> list[str]:
    """作業場所を片付ける。外せなかった worktree の理由を返す（利用者の lock・残った変更）。"""
    kept: list[str] = []
    for path in ws.worktrees():
        if not path.exists():
            continue
        done = worktree.release(repo, path)
        if not done.removed:
            kept.append(f"{path}: {done.reason}")
    return kept
