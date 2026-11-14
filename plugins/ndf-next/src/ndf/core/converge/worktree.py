"""レビュー worktree の登録の確かめ・lock・origin への同期・片付け（#2440・#3106・#3109）。

- 作った直後に `git worktree lock` を掛ける。lock は git の管理情報のファイルで、`/tmp` を共有しない別の環境の
  `git worktree prune` も lock した登録は消さない。lock を掛けられなければ worktree を外して止める
- lock の理由は作業の鍵（`ndf <tool> pr<N>`）に、掛けたホストと時刻（`host=<名前> at=<ISO 8601>`）を足したもの。
  `lock_of` は鍵を返し、`holder_of` がホストと時刻を返す
- 持ち主のいない lock（NDF の lock で、掛けたホストがこのホストで、作業場所のディレクトリが無いもの）は
  `sweep_orphans` が外して登録を片付ける。別のホストの lock は、そのホストの作業場所が見えないだけなので触らない
- 打つ前に、worktree の `.git` が指す管理情報（`HEAD`・`commondir`）があるかを確かめ、無ければ
  `WorktreeUnregistered`（worktree のパスと `.git` が指す先）で止める。「リポジトリを決められない」とは書かない
- 流し直しの初めに、worktree を origin の先頭へ合わせ、送っていない自分のコミットだけを載せ直す。衝突したら
  前の HEAD へ戻す（コミットを失わない）。commit していない変更・送っていないマージのコミット（載せ直すと
  マージで足した変更が消える）・origin の先頭が追跡するパスと重なる未追跡のファイル（ignored を含む。reset で
  上書きされる）があれば、何も変えずに止める
- 片付けは、lock の理由が NDF の作業のものだけを unlock してから強制で外す。利用者の lock は外さない。lock の無い
  worktree は NDF が作ったと確かめられないので強制せずに外し、変更が残っていれば外さずに理由を返す

関数は作業の名前（`tool`。`cross-review`・`cross-refactoring`）を受け、cross 系の作業が同じ部品を使う。
"""

from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Optional

from ndf import identity
from ndf.adapters import git

# lock の理由の前置き（NDF が掛けた lock だけを片付けで外す）
LOCK_PREFIX = f"{identity.FIXED_WORDS['entrypoint']} "
# lock の理由（例: `ndf cross-review pr3101`）
LOCK_REASON = LOCK_PREFIX + "{tool} pr{pr}"
# lock の理由の末尾の持ち主（掛けたホストと時刻）
_HOLDER = re.compile(r" host=(?P<host>\S+) at=(?P<at>\S+)$")
# worktree の `.git` ファイルの 1 行
_GITDIR = re.compile(r"^gitdir:\s*(?P<path>.+?)\s*$", re.MULTILINE)
# 管理情報にあるはずのファイル（消えた登録は `ndf/state` などの残りだけになる。#3109）
_ADMIN_FILES = ("HEAD", "commondir")


class WorktreeUnregistered(Exception):
    """worktree の管理情報が無い（別の環境の prune・片付けで消えた）。"""

    def __init__(self, worktree: Path, points_to: Optional[Path]):
        self.worktree = worktree
        self.points_to = points_to
        where = f"、.git が指す {points_to}" if points_to is not None else "、.git が無いか読めない"
        super().__init__(f"worktree の管理情報が無い（{worktree}{where}）")


class LockFailed(Exception):
    """lock を掛けられず、作ったばかりの worktree を外した（外せなければその理由も持つ）。"""


class SyncRefused(Exception):
    """origin へ合わせると worktree の変更・コミット・ファイルを失うので合わせない。"""


class Registration(NamedTuple):
    """worktree と、その管理情報のディレクトリ。"""

    worktree: Path
    gitdir: Path


class SyncResult(NamedTuple):
    """同期の前後の HEAD・載せ直したコミットの数・衝突したファイル（衝突したら `after` は `before`）。"""

    before: str
    after: str
    replayed: int
    conflicts: tuple[str, ...]


class Release(NamedTuple):
    """片付けの結果。外さなかったときは理由を持つ。"""

    removed: bool
    reason: Optional[str]


def conflict_detail(synced: SyncResult) -> str:
    """同期で衝突したときの説明（前の HEAD へ戻したことと衝突したファイル）。"""
    return f"自分の未送信のコミットが origin の先頭と衝突したので {synced.before} へ戻した: {', '.join(synced.conflicts)}"


class Holder(NamedTuple):
    """lock の理由を分けたもの。`key` は作業の鍵、`host`・`at` は掛けたホストと時刻（持ち主の無い旧い形では None）。"""

    key: str
    host: Optional[str]
    at: Optional[str]


def lock_reason(tool: str, pr: int) -> str:
    """作業の鍵（lock の理由から持ち主を除いたもの）。"""
    return LOCK_REASON.format(tool=tool, pr=pr)


def held_reason(tool: str, pr: int, host: Optional[str] = None, at: Optional[str] = None) -> str:
    """lock に書く理由。鍵の後ろに、掛けたホスト（既定はこのホスト）と時刻（既定は今の UTC）を足す。"""
    who = host if host is not None else socket.gethostname()
    when = at if at is not None else datetime.now(UTC).isoformat(timespec="seconds")
    return f"{lock_reason(tool, pr)} host={who} at={when}"


def parse_holder(text: str) -> Holder:
    """lock の理由を鍵と持ち主へ分ける。持ち主の無い理由は、全体を鍵にしてホストと時刻を None にする。"""
    m = _HOLDER.search(text)
    if m is None:
        return Holder(text, None, None)
    return Holder(text[: m.start()], m.group("host"), m.group("at"))


def _gitdir_of(worktree: Path, dotgit: Path) -> Optional[Path]:
    try:
        text = dotgit.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: .git が無い・読めないのは登録が無い印
        return None
    m = _GITDIR.search(text)
    if m is None:
        return None
    path = Path(m.group("path"))
    return path if path.is_absolute() else (worktree / path).resolve()


def registration(worktree: Path) -> Registration:
    """worktree の管理情報を確かめる。無ければ `WorktreeUnregistered`。主の worktree（`.git` がディレクトリ）はそのまま。"""
    dotgit = worktree / ".git"
    if dotgit.is_dir():
        return Registration(worktree, dotgit)
    gitdir = _gitdir_of(worktree, dotgit)
    if gitdir is None or not all((gitdir / name).is_file() for name in _ADMIN_FILES):
        raise WorktreeUnregistered(worktree, gitdir)
    return Registration(worktree, gitdir)


def lock_review_worktree(repo: git.Git, worktree: Path, tool: str, pr: int) -> str:
    """作ったばかりの worktree に lock を掛け、理由を返す。掛けられなければ worktree を外して `LockFailed`。"""
    reason = lock_reason(tool, pr)
    try:
        repo.run("worktree", "lock", "--reason", held_reason(tool, pr), str(worktree))
    except git.GitError as exc:
        try:
            repo.run("worktree", "remove", "--force", str(worktree))
        except git.GitError as again:
            raise LockFailed(f"lock を掛けられず（{exc}）、worktree も外せない（{again}）: {worktree}") from None
        raise LockFailed(f"lock を掛けられないので worktree を外した（lock の無いまま作業を始めない）: {exc}") from None
    return reason


def _blocks(porcelain: str) -> list[dict[str, str]]:
    """`git worktree list --porcelain` を 1 件 1 辞書（鍵は行の先頭の語、値は残り）にする。"""
    out: list[dict[str, str]] = []
    for block in porcelain.strip().split("\n\n"):
        entry: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            entry[key] = value
        if entry:
            out.append(entry)
    return out


def _raw_lock(repo: git.Git, worktree: Path) -> Optional[str]:
    """worktree の lock の理由をそのまま。lock されていなければ None、理由の無い lock は空の文字列。"""
    target = worktree.resolve()
    for entry in _blocks(repo.run("worktree", "list", "--porcelain")):
        if "worktree" in entry and Path(entry["worktree"]).resolve() == target:
            return entry["locked"] if "locked" in entry else None
    return None


def lock_of(repo: git.Git, worktree: Path) -> Optional[str]:
    """worktree の lock の鍵（理由から持ち主を除いたもの）。lock されていなければ None、理由の無い lock は空の文字列。"""
    raw = _raw_lock(repo, worktree)
    return parse_holder(raw).key if raw is not None else None


def holder_of(repo: git.Git, worktree: Path) -> Optional[Holder]:
    """worktree の lock の鍵と持ち主。lock されていなければ None。"""
    raw = _raw_lock(repo, worktree)
    return parse_holder(raw) if raw is not None else None


def orphans(porcelain: str, host: str) -> list[Path]:
    """`git worktree list --porcelain` から、持ち主のいない NDF の lock の worktree（このホストが掛け、ディレクトリが無い）。"""
    found: list[Path] = []
    for entry in _blocks(porcelain):
        if "worktree" not in entry or "locked" not in entry:
            continue
        held = parse_holder(entry["locked"])
        if held.key.startswith(LOCK_PREFIX) and held.host == host and not Path(entry["worktree"]).exists():
            found.append(Path(entry["worktree"]))
    return found


def sweep_orphans(repo: git.Git, host: Optional[str] = None) -> list[Path]:
    """持ち主のいない NDF の lock を外して登録を片付け、片付けた worktree を返す。別のホスト・利用者の lock は触らない。"""
    found = orphans(repo.run("worktree", "list", "--porcelain"), host if host is not None else socket.gethostname())
    for path in found:
        repo.run("worktree", "unlock", str(path))
        repo.run("worktree", "remove", "--force", str(path))
    return found


def resumable(worktree: Path) -> bool:
    """再開の前の確かめ。作業場所のディレクトリが無ければ偽（作り直す）。在って登録が消えていれば `WorktreeUnregistered`。"""
    if not worktree.exists():
        return False
    registration(worktree)
    return True


def release(repo: git.Git, worktree: Path) -> Release:
    """NDF の lock なら外してから worktree を外す。利用者の lock（理由が NDF の前置きでない）は外さずに理由を返す。"""
    reason = lock_of(repo, worktree)
    if reason is None:
        # NDF が作ったと確かめられない。強制せずに外し、利用者の変更が残れば git が拒むのでそのまま返す
        try:
            repo.run("worktree", "remove", str(worktree))
        except git.GitError as exc:
            return Release(False, f"NDF の lock が無く、変更が残るので強制では外さない: {exc}")
        return Release(True, None)
    if not reason.startswith(LOCK_PREFIX):
        return Release(False, f"利用者の lock なので外さない（理由: {reason or '無し'}）")
    repo.run("worktree", "unlock", str(worktree))
    repo.run("worktree", "remove", "--force", str(worktree))
    return Release(True, None)


def detach_clean(g: git.Git, commit: str) -> None:
    """作業場所 `g` を `commit` へ切り離して合わせ、追跡されていないファイルを消す。"""
    g.run("checkout", "-q", "--detach", "--force", commit)
    g.run("clean", "-q", "-fd")


def conflicted_files(g: git.Git) -> tuple[str, ...]:
    """衝突して統合できていないファイル（`-z` で区切るので空白・改行を含むパスも割らない）。"""
    return tuple(p for p in g.run("diff", "--name-only", "-z", "--diff-filter=U").split("\0") if p)


def fetch_head(g: git.Git, head_branch: str) -> str:
    """origin の PR のブランチを取り、その先頭のコミットを返す。"""
    g.run("fetch", "origin", f"+refs/heads/{head_branch}:refs/remotes/origin/{head_branch}")
    return g.rev_parse(f"refs/remotes/origin/{head_branch}")


def own_commits(g: git.Git, remote: str, head: str) -> list[str]:
    """`head` にあって `remote` に無い自分のコミット（古い順。同じ変更を持つコミットとマージを除く）。"""
    return g.run("rev-list", "--reverse", "--no-merges", "--cherry-pick", "--right-only", f"{remote}...{head}").split()


def _untracked_in_the_way(g: git.Git, remote: str) -> list[str]:
    """未追跡のファイル（ignored を含む）のうち、`remote` が追跡するパスと重なる（reset で上書き・削除される）もの。"""
    untracked = [p for p in g.run("ls-files", "--others", "-z").split("\0") if p]
    if not untracked:
        return []
    files = {p for p in g.run("ls-tree", "-r", "--name-only", "-z", remote).split("\0") if p}
    dirs = {d for f in files for d in _parents(f)}
    return [p for p in untracked if _clashes(p, files, dirs)]


def _parents(path: str) -> list[str]:
    """`path` の親のディレクトリ（根に近い順。`a/b/c` なら `a` と `a/b`）。"""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _clashes(path: str, files: set[str], dirs: set[str]) -> bool:
    """`path` が追跡されるファイルかディレクトリと同じか、追跡されるファイルを親に持つか。"""
    return path in files or path in dirs or any(p in files for p in _parents(path))


def _refuse_lossy_sync(g: git.Git, remote: str, before: str) -> None:
    """合わせると失うもの（送っていないマージのコミット・上書きされる未追跡のファイル）があれば `SyncRefused`。"""
    merges = g.run("rev-list", "--merges", f"{remote}..{before}").split()
    if merges:
        raise SyncRefused(
            f"送っていないマージのコミットがあり、載せ直すとマージで足した変更が消える。送ってから流し直す: {', '.join(merges)}"
        )
    clashing = _untracked_in_the_way(g, remote)
    if clashing:
        raise SyncRefused(
            f"未追跡のファイルが origin の先頭の追跡するパスと重なり、合わせると上書きされる。移してから流し直す: {', '.join(clashing)}"
        )


def sync_worktree(g: git.Git, head_branch: str) -> SyncResult:
    """worktree を origin の先頭へ合わせ、送っていない自分のコミットだけを載せ直す（#2440）。失うものがあれば何も変えずに `SyncRefused`。"""
    if g.run("status", "--porcelain", "--untracked-files=no").strip():
        raise SyncRefused("worktree に commit していない変更がある。commit してから流し直す")
    before = g.rev_parse("HEAD")
    remote = fetch_head(g, head_branch)
    _refuse_lossy_sync(g, remote, before)
    own = own_commits(g, remote, before)
    g.run("reset", "--hard", remote)
    if not own:
        return SyncResult(before, remote, 0, ())
    picked = g.runner(["cherry-pick", *own], g.cwd)
    if picked.returncode == 0:
        return SyncResult(before, g.rev_parse("HEAD"), len(own), ())
    conflicts = conflicted_files(g)
    g.run("cherry-pick", "--abort")
    g.run("reset", "--hard", before)
    if not conflicts:
        # 空になったコミットなど、衝突のファイル無しで載せ直せなかった。origin へ合っていないので成功とは返さない
        raise SyncRefused(f"自分の未送信のコミットを origin の先頭へ載せ直せないので {before} へ戻した: {picked.stderr.strip()}")
    return SyncResult(before, before, 0, conflicts)
