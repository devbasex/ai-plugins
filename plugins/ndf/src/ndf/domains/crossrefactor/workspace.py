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
`<保存先>/repo-<リポジトリのキー>/rf<PR>` に置く（#4566。キーは git の共通ディレクトリの実パスの SHA-256 の先頭 16 文字）。
前のバージョンが保存先の直下に作った `rf<PR>/` は、オーナーが自分と確かめられれば移さずに使い続ける。

同じ PR の `run` と `release` は `<親>/locks/rf<PR>.lock` の排他ロックを待たずに取る（`hold_pr`）。ロックファイルは
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
# 状態ファイルのうち、期限切れの掃除だけが残すキー（`gate_logs` と、前のバージョンの場所の `repo`）
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
    """このリポジトリの PR の作業場所を置く親ディレクトリ。`NDF_WORKTREE_BASE` があれば `<保存先>/repo-<キー>`。"""
    return _dir_of(repo.common_dir())


def _work_common(work: Path) -> Optional[Path]:
    """`work` の git の共通ディレクトリ。登録が消えていれば None。"""
    if not (work / ".git").exists():
        return None
    try:
        return git.Git(work).common_dir()
    except git.GitError:  # noqa: NDF401 外部コマンド: 登録の消えた work は状態ファイルの記録で確かめる
        return None


def _recorded_repo(root: Path) -> Optional[str]:
    """`root` の状態ファイルの `repo`。無いか読めなければ None。"""
    try:
        held = store.load(store.path_of(root))
    except store.StateBroken:  # noqa: NDF401 ファイル: 読めない状態ファイルの場所はオーナーを確かめられない
        return None
    return str(held["repo"]) if "repo" in held else None


def _owned_legacy(legacy: Path, common: Path) -> bool:
    """前のバージョンの `rf<PR>/` のオーナーが `common` のリポジトリか（`work` の共通ディレクトリか状態ファイルの `repo`）。"""
    return _work_common(legacy / "work") == common or _recorded_repo(legacy) == str(common)


def root_of(repo: git.Git, pr: int) -> Path:
    """PR の作業場所の根（ふだんは `repo_dir(repo)/rf<PR>`）。

    `NDF_WORKTREE_BASE` があり、新しい場所が無く、保存先の直下に前のバージョンの `rf<PR>/` があってオーナーが自分と
    確かめられれば、その場所を返す（状態ファイルへ `repo` を書く）。確かめられない場所は使わない。
    """
    common = repo.common_dir()
    fresh = _dir_of(common) / f"rf{pr}"
    shared = _shared_base()
    legacy = shared / f"rf{pr}" if shared is not None else None
    if legacy is None or fresh.exists() or not legacy.is_dir() or not _owned_legacy(legacy, common):
        return fresh
    if _recorded_repo(legacy) != str(common):
        path = store.path_of(legacy)
        store.save(path, {**store.load(path), "repo": str(common)})
    return legacy


def hold_pr(parent: Path, pr: int) -> Optional[FileLock]:
    """`<parent>/locks/rf<PR>.lock` の排他ロックを待たずに取る。取れなければ None。ロックファイルは消さない。"""
    locks = parent / LOCKS_DIR
    locks.mkdir(parents=True, exist_ok=True)
    return slot_pool.try_lock(locks / f"rf{pr}.lock")


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
    # 担当の出力と手元の最終ゲートのログが入るので、作成と再開で 0700 にする（#4566）
    private_dir(ws.logs)
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


class LogRefused(Exception):
    """手元の最終ゲートのログを書けない（保存先がリンク・オーナーが違う・権限を直せない）。"""


def private_dir(logs: Path) -> None:
    """`logs/` を 0700 で用意する。リンク・オーナーが違う・権限を直せなければ `LogRefused`。"""
    if logs.is_symlink():
        raise LogRefused(f"ログの保存先がシンボリックリンクなので書かない: {logs}")
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = logs.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LogRefused(f"ログの保存先のオーナーが実行ユーザーでないか、ディレクトリでない: {logs}")
    try:
        os.chmod(logs, 0o700)
    except OSError as exc:
        raise LogRefused(f"ログの保存先の権限を 0700 に直せない: {logs}: {exc}") from exc


def gate_logs(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """状態の `gate_logs`（手元の最終ゲートのログの所有の記録）。無いか形が違えば空。"""
    held = state["gate_logs"] if "gate_logs" in state else []
    return [e for e in held if isinstance(e, dict)] if isinstance(held, list) else []


def write_private_log(logs: Path, name: str, text: str, state: dict[str, Any], save: Callable[[], None]) -> Path:
    """実行ユーザーだけが読めるログ `logs/<name>` を書く。ファイルを作る前に所有を状態の `gate_logs` へ記録する。

    前からあるファイルは開かずに置き換える（リンク先の中身と権限を変えない）。失敗すれば一時ファイルを消し、記録は残す。
    """
    private_dir(logs)
    tmp = f"{GATE_LOG_PREFIX}{uuid.uuid4().hex}.tmp"
    entry: dict[str, Any] = {"name": name, "tmp": tmp}
    state["gate_logs"] = [*gate_logs(state), entry]
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
    """`path` が記録したファイルと同じか。識別子が無い記録は、NDF がランダムに名付けた一時ファイルだけを同じとする。"""
    try:
        info = path.lstat()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 消えたファイルは記録と同じでない
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    if "st_dev" in entry and "st_ino" in entry:
        return (info.st_dev, info.st_ino) == (entry["st_dev"], entry["st_ino"])
    return tmp


def _expire(path: Path, keep: float, now: float) -> bool:
    """期限を過ぎていれば消して真。期限の内か消せなければ偽（残す）。"""
    if now - path.lstat().st_mtime <= keep:
        return False
    try:
        path.unlink()
    except OSError:  # noqa: NDF401 ファイル: 消せないファイルは残して続ける
        return False
    return True


def _sweep_entry(logs: Path, entry: Mapping[str, Any], keep: float, now: float) -> tuple[int, bool]:
    """記録 1 件の `name` と `tmp` の期限切れを消し、(消した件数, 記録したファイルが残るか) を返す。"""
    removed, alive = 0, False
    for key, is_tmp in (("name", False), ("tmp", True)):
        rel = str(entry[key]) if key in entry else ""
        target = logs / rel
        if not rel or "/" in rel or not _same(target, entry, tmp=is_tmp):
            continue
        if _expire(target, keep, now):
            removed += 1
        else:
            alive = True
    return removed, alive


def _drop_empty(path: Path, logs: Path, root: Path) -> None:
    """ログの記録だけの状態ファイルと、空になった `logs/`・`rf<PR>/` を消す。空でないディレクトリは残す。"""
    path.unlink(missing_ok=True)
    for d in (logs, root):
        try:
            d.rmdir()
        except OSError:  # noqa: NDF401 ファイル: 空でないディレクトリは残す
            return


def _sweep_one(root: Path, keep: float, now: float) -> int:
    """`root`（`rf<PR>/`）の、記録のある期限切れのログを消し、記録を外す。消した件数を返す。"""
    path, logs = store.path_of(root), root / "logs"
    if root.is_symlink() or logs.is_symlink() or not path.is_file():
        return 0
    try:
        state = store.load(path)
    except store.StateBroken:  # noqa: NDF401 ファイル: 読めない状態ファイルの場所には触れない
        return 0
    entries = gate_logs(state)
    removed, left = 0, []
    for entry in entries:
        gone, alive = _sweep_entry(logs, entry, keep, now)
        removed += gone
        if alive:
            left.append(entry)
    if not left and set(state) <= LOG_ONLY_KEYS:
        _drop_empty(path, logs, root)
    elif left != entries:
        store.save(path, {**state, "gate_logs": left})
    return removed


def _legacy_roots(legacy: Optional[Path], repo_id: str) -> list[Path]:
    """前のバージョンの場所（保存先の直下）の `rf<PR>/` のうち、状態の `repo` が自分のもの。"""
    if legacy is None or not legacy.is_dir():
        return []
    return [p for p in sorted(legacy.glob("rf*")) if p.is_dir() and not p.is_symlink() and _recorded_repo(p) == repo_id]


def _sweep_other(root: Path, keep: float, now: float) -> int:
    """ほかの PR の `rf<PR>/` は、その PR のロックを待たずに取れたときだけ扱う。"""
    number = root.name.removeprefix("rf")
    held = hold_pr(root.parent, int(number)) if number.isdigit() else None
    if held is None:
        return 0
    try:
        return _sweep_one(root, keep, now)
    finally:
        held.release()


def sweep_gate_logs(parent: Path, legacy: Optional[Path], repo_id: str, own: Path, keep: float, now: float) -> int:
    """同じリポジトリの PR の、記録のある期限切れの手元の最終ゲートのログを消し、件数を返す。`own` のロックを持って呼ぶ。

    ほかの PR の `rf<PR>/` は、その PR のロックを待たずに取れたときだけ扱う。前のバージョンの場所（`legacy` の直下）は、状態の
    `repo` が `repo_id` と一致するものだけを見る。
    """
    roots = [p for p in sorted(parent.glob("rf*")) if p.is_dir()] if parent.is_dir() else []
    total = 0
    for root in dict.fromkeys([*roots, *_legacy_roots(legacy, repo_id)]):
        total += _sweep_one(root, keep, now) if root == own else _sweep_other(root, keep, now)
    return total


def sweep(repo: git.Git, own: Path, now: float) -> int:
    """`run` と `release` が自分のロックを取った後に呼ぶ期限切れの掃除。消せなくても止めない。"""
    try:
        return sweep_gate_logs(own.parent, _shared_base(), str(repo.common_dir()), own, limits.seconds(GATE_LOG_KEEP), now)
    except OSError as exc:
        print(f"⚠ 手元の最終ゲートのログを片付けられなかった（結果は変えない）: {exc}", file=sys.stderr)
        return 0
