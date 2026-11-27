"""殻: cross-refactoring の作業場所の準備と片付け（#3106・#750）。

作業場所の根は `ndf.core.converge.location` の保存先の `rf<PR>`。中に次を置く。

| パス | 中身 |
| --- | --- |
| `work/` | 書き込み用の作業ディレクトリ（コードを書く工程の担当が動く。PR の head から detached で作る） |
| `read/<ランタイム>/` | 読み取り用ディレクトリ（提案・計画の担当が動く。工程の前に `work` の HEAD へ合わせる） |
| `logs/` | 担当の出力と、回収した結果ファイル。手元の最終ゲートのログ（`local-gate-*.log`）も置く（0700） |
| `state.json` | 状態ファイル（`store`） |
| `verify-<ランダム>/` | 手元の最終ゲートの検証用の作業場所（`local_gate.verify_tree`。打ち終えたら取り除く） |

保存先は `ndf.core.converge.location` が決める。環境変数 `NDF_WORKTREE_BASE` で保存先を複数のリポジトリが共有するときだけ、
`<保存先>/repo-<リポジトリの鍵>/rf<PR>` に置く（#4566。鍵は git の共通ディレクトリの実パスの SHA-256 の先頭 16 文字）。
前の版が保存先の直下に作った `rf<PR>/` は、持ち主が自分と確かめられれば移さずに使い続ける。

同じ PR の `run` と `release` は `<親>/locks/rf<PR>.lock` の排他ロックを待たずに取る（`hold`）。ロックファイルは
`rf<PR>/` の外に置き、消さない。

- 作る前に、オーナーのいない NDF の lock を片付ける（`worktree.sweep_orphans`）
- 作った直後に、ホストと時刻つきの lock を掛ける（`worktree.lock_review_worktree`）
- 再開のときは作り直さずに登録を確かめる。ディレクトリが無ければ作り直し、在って登録が消えていれば
  `WorktreeUnregistered` で止める（作業を失わない）
- 担当の結果ファイルの保存先（`.cross_refactoring/`）を git の除外へ足し、差分に入れない
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock

from ndf import slot_pool
from ndf.adapters import git
from ndf.contracts import limits
from ndf.core.converge import location, worktree
from ndf.domains.crossrefactor import launch_decide, store

# 手元の最終ゲートのログの名前の頭と、残す秒の方針（#4566）
GATE_LOG_PREFIX = "local-gate-"
GATE_LOG_KEEP = "cross-refactoring.gate-log.keep"
LOCKS_DIR = "locks"
# 状態ファイルのうち、期限切れの掃除だけが残す鍵（`gate_logs` と前の版の場所の `repo`）
LOG_ONLY_KEYS = frozenset({"gate_logs", "repo"})


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


def _shared_base() -> Optional[Path]:
    declared = os.environ.get(location.BASE_ENV, "")
    return Path(declared) if declared else None


def repo_key(common: Path) -> str:
    return hashlib.sha256(str(common).encode("utf-8")).hexdigest()[:16]


def _dir_of(common: Path) -> Path:
    shared = _shared_base()
    if shared is None:
        return location.default_base(common.parent)
    return shared / f"repo-{repo_key(common)}"


def repo_dir(repo: git.Git) -> Path:
    """このリポジトリの PR の作業場所を置く親ディレクトリ。`NDF_WORKTREE_BASE` があれば `<保存先>/repo-<鍵>`。"""
    return _dir_of(repo.common_dir())


def _owned_legacy(legacy: Path, common: Path) -> bool:
    """前の版の `rf<PR>/` の持ち主が `common` のリポジトリか（`work` の共通ディレクトリか状態ファイルの `repo`）。"""
    work = legacy / "work"
    if (work / ".git").exists():
        try:
            if git.Git(work).common_dir() == common:
                return True
        except git.GitError:  # noqa: NDF401 ファイル: 登録の消えた work は状態ファイルの記録で確かめる
            pass
    try:
        held = store.load(store.path_of(legacy))
    except store.StateBroken:
        return False
    return held.get("repo") == str(common)


def root_of(repo: git.Git, pr: int) -> Path:
    """PR の作業場所の根（ふだんは `repo_dir(repo)/rf<PR>`）。

    `NDF_WORKTREE_BASE` があり、新しい場所が無く、保存先の直下に前の版の `rf<PR>/` があって持ち主が自分と確かめられれば、
    その場所を返す（状態ファイルへ `repo` を書く）。確かめられない場所は使わない。
    """
    common = repo.common_dir()
    fresh = _dir_of(common) / f"rf{pr}"
    shared = _shared_base()
    legacy = shared / f"rf{pr}" if shared is not None else None
    if legacy is None or fresh.exists() or not legacy.is_dir() or not _owned_legacy(legacy, common):
        return fresh
    path = store.path_of(legacy)
    held = store.load(path)
    if held.get("repo") != str(common):
        store.save(path, {**held, "repo": str(common)})
    return legacy


def legacy_base() -> Optional[Path]:
    """前の版の場所を探す保存先（`NDF_WORKTREE_BASE` が無ければ None）。"""
    return _shared_base()


def hold(parent: Path, pr: int) -> Optional[FileLock]:
    """`<parent>/locks/rf<PR>.lock` の排他ロックを待たずに取る。取れなければ None。ロックファイルは消さない。"""
    locks = parent / LOCKS_DIR
    locks.mkdir(parents=True, exist_ok=True)
    return slot_pool.try_lock(locks / f"rf{pr}.lock")


class LogRefused(Exception):
    """手元の最終ゲートのログを書けない（保存先がリンク・持ち主が違う・権限を直せない）。"""


def private_dir(logs: Path) -> None:
    """`logs/` を 0700 で用意する。リンク・持ち主が違う・権限を直せなければ `LogRefused`。"""
    if logs.is_symlink():
        raise LogRefused(f"ログの保存先がシンボリックリンクなので書かない: {logs}")
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = logs.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LogRefused(f"ログの保存先の持ち主が実行ユーザーでないか、ディレクトリでない: {logs}")
    try:
        os.chmod(logs, 0o700)
    except OSError as exc:
        raise LogRefused(f"ログの保存先の権限を 0700 に直せない: {logs}: {exc}") from exc


def write_private_log(logs: Path, name: str, text: str, state: dict[str, Any], save: Callable[[], None]) -> Path:
    """実行ユーザーだけが読めるログ `logs/<name>` を書く。ファイルを作る前に所有を状態の `gate_logs` へ記録する。

    前からあるファイルは開かずに置き換える（リンク先の中身と権限を変えない）。失敗すれば一時ファイルを消し、記録は残す。
    """
    private_dir(logs)
    tmp = f"{GATE_LOG_PREFIX}{uuid.uuid4().hex}.tmp"
    entry: dict[str, Any] = {"name": name, "tmp": tmp}
    state["gate_logs"] = [*state.get("gate_logs", []), entry]
    save()
    path = logs / tmp
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
            info = os.fstat(fd)
        finally:
            os.close(fd)
        entry.update(st_dev=info.st_dev, st_ino=info.st_ino)
        save()
        os.replace(path, logs / name)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return logs / name


def _same(path: Path, entry: Mapping[str, Any], *, tmp: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    if "st_dev" in entry and "st_ino" in entry:
        return (info.st_dev, info.st_ino) == (entry["st_dev"], entry["st_ino"])
    return tmp


def _sweep_one(root: Path, keep: float, now: float) -> int:
    """`root`（`rf<PR>/`）の、記録のある期限切れのログを消し、記録を外す。消した件数を返す。"""
    path = store.path_of(root)
    logs = root / "logs"
    if root.is_symlink() or logs.is_symlink() or not path.is_file():
        return 0
    try:
        state = store.load(path)
    except store.StateBroken:
        return 0
    entries = state.get("gate_logs", [])
    if not isinstance(entries, list):
        return 0
    removed = 0
    left: list[Any] = []
    for entry in entries:
        alive = False
        for key, is_tmp in (("name", False), ("tmp", True)):
            target = logs / str(entry.get(key, ""))
            if not entry.get(key) or "/" in str(entry[key]) or not _same(target, entry, tmp=is_tmp):
                continue
            if now - target.lstat().st_mtime > keep:
                try:
                    target.unlink()
                    removed += 1
                    continue
                except OSError:  # noqa: NDF401 ファイル: 消せないファイルは残して続ける
                    pass
            alive = True
        if alive:
            left.append(entry)
    if left != entries:
        state["gate_logs"] = left
    if not left and set(state) <= LOG_ONLY_KEYS:
        path.unlink(missing_ok=True)
        for d in (logs, root):
            try:
                d.rmdir()
            except OSError:  # noqa: NDF401 ファイル: 空でないディレクトリは残す
                pass
    elif left != entries:
        store.save(path, state)
    return removed


def sweep_gate_logs(parent: Path, legacy: Optional[Path], repo_id: str, own: Path, keep: float, now: float) -> int:
    """同じリポジトリの PR の、記録のある期限切れの手元の最終ゲートのログを消し、件数を返す。`own` のロックを持って呼ぶ。

    ほかの PR の `rf<PR>/` は、その PR のロックを待たずに取れたときだけ扱う。前の版の場所（`legacy` の直下）は、状態の
    `repo` が `repo_id` と一致するものだけを見る。
    """
    roots = [p for p in sorted(parent.glob("rf*")) if p.is_dir()] if parent.is_dir() else []
    if legacy is not None and legacy.is_dir():
        for p in sorted(legacy.glob("rf*")):
            try:
                if p.is_dir() and not p.is_symlink() and store.load(store.path_of(p)).get("repo") == repo_id:
                    roots.append(p)
            except store.StateBroken:  # noqa: NDF401 ファイル: 読めない状態ファイルの場所には触れない
                continue
    total = 0
    for root in dict.fromkeys(roots):
        if root == own:
            total += _sweep_one(root, keep, now)
            continue
        number = root.name.removeprefix("rf")
        held = hold(root.parent, int(number)) if number.isdigit() else None
        if held is None:
            continue
        try:
            total += _sweep_one(root, keep, now)
        finally:
            held.release()
    return total


def sweep(repo: git.Git, own: Path, now: float) -> int:
    """`run` と `release` が自分のロックを取った後に呼ぶ期限切れの掃除。消せなくても止めない。"""
    try:
        return sweep_gate_logs(own.parent, legacy_base(), str(repo.common_dir()), own, limits.seconds(GATE_LOG_KEEP), now)
    except OSError as exc:
        print(f"⚠ 手元の最終ゲートのログを片付けられなかった（結果は変えない）: {exc}", file=sys.stderr)
        return 0


def layout(root: Path, runtimes: Sequence[str]) -> Workspace:
    return Workspace(root, root / "work", root / "read", root / "logs", tuple(runtimes))


def _exclude_results(g: git.Git) -> None:
    """結果ファイルの保存先を git の除外（`info/exclude`）へ足す。足してあれば何もしない。"""
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


def prepare(repo: git.Git, pr: int, head: str, runtimes: Sequence[str]) -> Workspace:
    """取得済みの PR の先頭のコミット `head` から作業場所を用意する（再開なら登録を確かめて使う）。`repo` はリポジトリのどこかの
    作業場所の git。"""
    worktree.sweep_orphans(repo)
    ws = layout(root_of(repo, pr), runtimes)
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
