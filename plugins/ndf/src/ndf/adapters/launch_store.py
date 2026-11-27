"""モデルと effort の実験の記録の保存先（#2944）。ファイルの名前・月・置き換えの書き込みと、使われなくなったリポジトリの片付け。

記録は保存先の下のリポジトリごとのディレクトリ（`<repo>`）に置く。割り付けと自分のリポジトリの月の掃除は `model_choice` が行い、
ここは名前の規則と、保存先全体を月に 1 回片付ける処理（`sweep`）を持つ。片付けは、ほかのリポジトリのロックを待たない。

リポジトリのディレクトリのロックを取る起動は、その間、保存先の共有ロック（`launch-store.lock`。`using`）を持つ。役目を終えた
ディレクトリの回収は、このロックを書き手として待たずに取れたときだけ行う。回収はロックのファイルも消すので、消したパスに起動が新しい
ロックを作って取ると、回収とその起動が同時に進む。回収の対象の外にある共通のロックで、回収と起動を重ねない。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from filelock import FileLock, ReadWriteLock, Timeout

from ndf.contracts import facts

# 表 ndf-files の行（保存先は使用量ログと同じ）
LEDGER = "launch-experiments"
LEDGER_LOCK = "launch-experiments-lock"
WEIGHTS = "launch-weights"
WEIGHTS_LOCK = "launch-weights-lock"
PRUNED = "launch-pruned"
PRUNED_LOCK = "launch-pruned-lock"
SWEPT = "launch-swept"
SWEPT_LOCK = "launch-swept-lock"
STORE_LOCK = "launch-store-lock"
# 月ごとのファイルの名前の、月の位置と、リポジトリごとのディレクトリの名前の位置
MONTH = "<YYYY-MM>"
REPO = "<repo>"
# リポジトリのディレクトリの名前（model_choice.repo_store）。ほかの名前のものとシンボリックリンクは片付けない
_REPO_DIR = re.compile(r"[0-9a-f]{16}")
_MONTH_FORM = re.compile(r"\d{4}-\d{2}")
# 役目を終えたディレクトリから消すファイル（NDF が作る名前だけ）
_RETIRE_FILES = (WEIGHTS, PRUNED, LEDGER_LOCK, WEIGHTS_LOCK, PRUNED_LOCK)
# write_replacing の一時ファイルの名前の、uuid4 の 16 進の部分
_TMP_ID = re.compile(r"[0-9a-f]{32}")

log = logging.getLogger(__name__)


def month(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y-%m}"


def month_start(name: str, ahead: int = 0) -> datetime:
    year, mon = divmod(int(name[:4]) * 12 + int(name[5:]) - 1 + ahead, 12)
    return datetime(year, mon + 1, 1, tzinfo=UTC)


def store_file(root: Path, file_id: str) -> Path:
    """ディレクトリ `root`（リポジトリのディレクトリか保存先）の中のファイル。"""
    return root / Path(facts.value("ndf-files", file_id, "path")).name


def write_replacing(path: Path, text: str) -> None:
    """一時ファイルに書いてから置き換える。一時ファイルは作るときから所有者だけが読み書きできる（0600）。

    出現率のファイルはリポジトリの絶対パスを持つので、umask で広がった権限のファイルに置き換えない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def done_in(done: Path, name: str) -> bool:
    """処理を終えた月を書くファイル `done`（`launch-pruned` か `launch-swept`）が、月 `name` を持つか。"""
    return done.is_file() and done.read_text(encoding="utf-8").strip() == name


def locked_now(lock: FileLock) -> bool:
    """錠を待たずに取る。ほかの起動が持っていれば偽。"""
    try:
        lock.acquire()
    except Timeout:
        log.info("ほかの起動が %s を持っているので、待たずに先へ進む", lock.lock_file)
        return False
    return True


def month_files(root: Path) -> dict[str, Path]:
    """リポジトリのディレクトリ `root` にある月のファイル（月 → パス）。"""
    head, tail = Path(facts.value("ndf-files", LEDGER, "path")).name.split(MONTH)
    found: dict[str, Path] = {}
    for path in root.glob(f"{head}*{tail}"):
        name = path.name[len(head) : len(path.name) - len(tail)]
        if _MONTH_FORM.fullmatch(name) and not path.is_symlink():
            found[name] = path
    return found


def drop_months(root: Path, edge: datetime) -> dict[str, Path]:
    """`root` の月のファイルのうち `edge` より前に終わった月を消し、残った月を返す。"""
    found = month_files(root)
    for name in [m for m in found if month_start(m, 1) <= edge]:
        found.pop(name).unlink(missing_ok=True)
    return found


def past(entry: Mapping[str, Any], now: datetime) -> bool:
    """出現率の項目が、計算した日時から項目の `window_days` を過ぎたか。"""
    return datetime.fromisoformat(entry["at"]) + timedelta(days=entry["allocation"]["window_days"]) < now


def _weights(path: Path, now: datetime) -> Optional[tuple[Optional[dict[str, Any]], list[Any]]]:
    """出現率のファイルの中身と、期限の前の項目。ファイルが無ければ (None, [])、壊れていれば None。"""
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data, [e for e in data["entries"] if not past(e, now)]
    except (ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 壊れた出現率のファイルは出現率の計算が作り直す
        return None


def prune_weights(root: Path, now: datetime) -> Optional[int]:
    """期限を過ぎた出現率の項目を消し、残りの数を返す（ファイルが無ければ 0、壊れていれば None）。出現率のロックの中で呼ぶ。"""
    path = store_file(root, WEIGHTS)
    got = _weights(path, now)
    if got is None:
        return None
    data, kept = got
    if data is not None and len(kept) != len(data["entries"]):
        write_replacing(path, json.dumps({**data, "entries": kept}, ensure_ascii=False))
    return len(kept)


def _store_lock(base: Path, *, shared: bool = True) -> ReadWriteLock:
    """保存先 `base` の共有ロック。起動は読み手（同じプロセスの入れ子を許す同じインスタンス）、回収は書き手（別のインスタンス）で取る。"""
    base.mkdir(parents=True, exist_ok=True)
    return ReadWriteLock(str(store_file(base, STORE_LOCK)), is_singleton=shared)


@contextmanager
def _as_store_error(path: Path) -> Iterator[None]:
    """共有ロックの SQLite の失敗（壊れたデータベース・読み書きの誤り）を `OSError` に変える。呼び手は記録の失敗として起動を続ける。"""
    try:
        yield
    except sqlite3.Error as exc:
        raise OSError(f"保存先の共有ロック {path} を使えない: {exc}") from exc


@contextmanager
def using(base: Path, timeout: float) -> Iterator[None]:
    """保存先 `base` の共有ロックを読み手で持つ。リポジトリのディレクトリのロックは、この中で取る。回収が終わるのを最長 `timeout` 秒待つ。"""
    path = store_file(base, STORE_LOCK)
    with _as_store_error(path):
        lock = _store_lock(base)
        lock.acquire_read(timeout, blocking=timeout > 0)
    try:
        yield
    finally:
        with _as_store_error(path):
            lock.release()


def sweep(base: Path, now: datetime, own: Path, edge: datetime) -> None:
    """保存先 `base` の自分（`own`）以外のリポジトリを片付け、役目を終えたものを回収する。錠を待たずに取れた起動が、月に 1 回
    （`launch-swept`）行う。

    `edge` はどの設定でも使わない古さの境。1 つのリポジトリで失敗しても警告を出して残りを続け、今月を書く。失敗した分は翌月に
    やり直す。起動のたびに保存先全体を読み直さないためである。保存先の一覧を読めないなど全体が中断すれば今月を書かず、次の起動がやり直す。
    """
    done, name = store_file(base, SWEPT), month(now)
    if done_in(done, name):
        return
    lock = FileLock(str(store_file(base, SWEPT_LOCK)), timeout=0)
    if not locked_now(lock):
        return
    try:
        if done_in(done, name):
            return
        with os.scandir(base) as found:
            dirs = [Path(e.path) for e in found if _REPO_DIR.fullmatch(e.name) and e.is_dir(follow_symlinks=False)]
        idle = [d for d in dirs if d != own and _tidied(d, now, edge)]
        if idle:
            _reclaim(base, idle, now)
        write_replacing(done, name)
    finally:
        lock.release()


def _pruned_month(root: Path) -> Optional[str]:
    """リポジトリの月の掃除が `launch-pruned` に書いた月。無いか月の形でなければ None。"""
    path = store_file(root, PRUNED)
    text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    return text if _MONTH_FORM.fullmatch(text) else None


def _idle(root: Path, now: datetime) -> bool:
    """回収してよいか。共有ロックを書き手で持つ間に、最新の状態を読んで確かめる。

    `launch-pruned` に記録された最後の掃除の月が先月より前で、ディレクトリの中が NDF の作るファイル（`_ndf_files`）だけで、
    期限の前の出現率の項目が無いもの。
    """
    last = _pruned_month(root)
    if last is None or last >= month(month_start(month(now), -1)) or _ndf_files(root) is None:
        return False
    got = _weights(store_file(root, WEIGHTS), now)
    return got is not None and not got[1]


def _tidied(root: Path, now: datetime, edge: datetime) -> bool:
    """ほかのリポジトリ `root` を片付け、回収の候補かを返す。失敗は警告を出して偽（翌月にやり直す）。

    今月に自分の月の掃除を終えたリポジトリは飛ばす（その掃除が `edge` より新しい境で月を消し、出現率の項目も消したため）。月ごとの
    実験記録は、ファイル名の月で削除を判断し、もう追記されない古さなのでロックを取らずに消す。出現率ファイルはロックを取らずに読み、
    期限を過ぎた項目があるときだけ、出現率のロックを待たずに取ってから読み直して消す。ロックが使用中なら、項目は消さずに飛ばす。
    候補かどうかは、ここで読んだ結果（最後の掃除の月・残った月・期限の前の項目の数）で選び、回収（`_reclaim`）が確かめ直す。
    """
    try:
        last = _pruned_month(root)
        if last == month(now):
            return False
        left = drop_months(root, edge)
        got = _weights(store_file(root, WEIGHTS), now)
        if got is not None and got[0] is not None and len(got[1]) != len(got[0]["entries"]):
            _prune_weights_now(root, now)
    except OSError as exc:
        log.warning("使われなくなったリポジトリの実験の記録を片付けられない（翌月にやり直す）: %s: %s", root, exc)
        return False
    return last is not None and last < month(month_start(month(now), -1)) and not left and got is not None and not got[1]


def _prune_weights_now(root: Path, now: datetime) -> None:
    lock = FileLock(str(store_file(root, WEIGHTS_LOCK)), timeout=0)
    with using(root.parent, 0):  # 回収するのは片付けをしているこの起動だけなので、待たずに取れる
        if not locked_now(lock):
            return  # 使っているリポジトリは、自分の月の掃除で項目を消す
        try:
            prune_weights(root, now)
        finally:
            lock.release()


def _reclaim(base: Path, roots: list[Path], now: datetime) -> None:
    """保存先の共有ロックを書き手として待たずに取り、候補 `roots` を 1 つずつ確かめ直して回収する。取れなければ翌月に回す。

    書き手を持つ間は、どの起動もリポジトリのディレクトリのロックを持たず、待ってもいないので、ロックのファイルも消せる。
    1 つの候補で失敗しても警告を出して次の候補へ進む。
    """
    path = store_file(base, STORE_LOCK)
    try:
        with _as_store_error(path):
            lock = _store_lock(base, shared=False)
            lock.acquire_write(timeout=0, blocking=False)
    except Timeout:
        log.info("起動が実験の記録を使っているので、役目を終えたリポジトリの回収を翌月に回す")
        return
    except OSError as exc:
        log.warning("保存先の共有ロックを取れないので、役目を終えたリポジトリの回収を翌月に回す: %s", exc)
        return
    for root in roots:
        _reclaim_repo(root, now)
    try:
        with _as_store_error(path):
            lock.release()
            lock.close()
    except OSError as exc:
        log.warning("保存先の共有ロックを返せない（プロセスの終了で外れる）: %s", exc)


def _reclaim_repo(root: Path, now: datetime) -> None:
    try:
        if _idle(root, now):
            _drop_dir(root)
    except OSError as exc:
        log.warning("役目を終えたリポジトリの実験の記録を回収できない（翌月にやり直す）: %s: %s", root, exc)


def _regular_own(path: Path) -> bool:
    """`path` が、この利用者の持つ通常のファイルか（シンボリックリンク・ディレクトリ・ほかの利用者のファイルでない）。"""
    st = os.lstat(path)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()


def _ndf_files(root: Path) -> Optional[list[Path]]:
    """ディレクトリ `root` の中身が、NDF の作るファイルだけなら、消す順（`launch-pruned` を最後）に並べて返す。そうでなければ None。

    NDF の作るファイルは、`_RETIRE_FILES` の名前と、`write_replacing` の書きかけの一時ファイルで、この利用者の持つ通常のファイルに
    限る。ほかの名前のもの・シンボリックリンク・ほかの利用者のファイルが 1 つでもあれば、利用者が置いたものとして何も消さない。
    """
    names = {store_file(root, n).name for n in _RETIRE_FILES}
    found: list[Path] = []
    with os.scandir(root) as entries:
        for e in entries:
            known = e.name in names or any(_tmp_of(e.name, n) for n in names)
            if not known or not _regular_own(Path(e.path)):
                log.info("NDF の作るファイルでないものがあるので、回収しない: %s", e.path)
                return None
            found.append(Path(e.path))
    marker = store_file(root, PRUNED)
    return sorted(found, key=lambda p: p == marker)


def _tmp_of(name: str, target: str) -> bool:
    """`name` が `write_replacing` が `target` を書くときの一時ファイルの名前か。"""
    head, tail = f".{target}.", ".tmp"
    return name.startswith(head) and name.endswith(tail) and _TMP_ID.fullmatch(name[len(head) : -len(tail)]) is not None


def _drop_dir(root: Path) -> None:
    """NDF の作るファイルを消してから、ディレクトリを消す。掃除の記録（`launch-pruned`）は最後に消す。

    途中で失敗しても `launch-pruned` が残るので、翌月の回収がまた候補として扱う。ディレクトリを消せない（親に書けない）ときは、
    何も消さずに止める。
    """
    if not os.access(root.parent, os.W_OK | os.X_OK):
        raise PermissionError(f"{root.parent} に書けないので {root} を消せない")
    files = _ndf_files(root)
    if files is None:
        raise FileExistsError(f"{root} に NDF の作るファイルでないものが現れた")
    for path in files:
        path.unlink()
    root.rmdir()
