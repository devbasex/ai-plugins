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
    return _parsed_weights(path.read_bytes(), now)


def _parsed_weights(raw: bytes, now: datetime) -> Optional[tuple[Optional[dict[str, Any]], list[Any]]]:
    """出現率のファイルの中身 `raw` と、期限の前の項目。NDF の書く形でなければ None。"""
    try:
        data = json.loads(raw.decode("utf-8"))
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
def _as_store_error(path: Path, *, acquiring: bool = False) -> Iterator[None]:
    """共有ロックの SQLite の失敗（壊れたデータベース・読み書きの誤り）を `OSError` に変える。呼び手は記録の失敗として起動を続ける。

    取るとき（`acquiring`）は、filelock がロックに使えないと拒むデータベース（WAL モード・大きすぎるスキーマ）の `ValueError` も変える。
    """
    refused: tuple[type[Exception], ...] = (sqlite3.Error, ValueError) if acquiring else (sqlite3.Error,)
    try:
        yield
    except refused as exc:
        raise OSError(f"保存先の共有ロック {path} を使えない: {exc}") from exc


@contextmanager
def using(base: Path, timeout: float) -> Iterator[None]:
    """保存先 `base` の共有ロックを読み手で持つ。リポジトリのディレクトリのロックは、この中で取る。回収が終わるのを最長 `timeout` 秒待つ。"""
    path = store_file(base, STORE_LOCK)
    with _as_store_error(path, acquiring=True):
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
    return _month_in(path.read_bytes(), path) if path.is_file() else None


def _month_in(raw: bytes, path: Path) -> Optional[str]:
    """掃除の記録 `path` の中身 `raw` の月。月の形でない（UTF-8 として読めないものを含む）なら None。"""
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:  # noqa: NDF401 ファイル: NDF が書いた記録でないものは、掃除の記録が無いものとして扱う
        log.warning("掃除の記録を UTF-8 として読めないので、このリポジトリは記録が無いものとして扱う: %s", path)
        return None
    return text if _MONTH_FORM.fullmatch(text) else None


def _idle(root: Path, fd: int, files: Mapping[str, os.stat_result], now: datetime) -> bool:
    """回収してよいか。共有ロックを書き手で持つ間に、開いたディレクトリ `fd`（`root`）の最新の状態を読んで確かめる。

    `files` は `_ndf_files` が確かめた NDF のファイル（名前 → 状態）。`launch-pruned` に記録された最後の掃除の月が先月より前で、
    出現率のファイルが無いか、NDF の書く形で期限の前の項目を持たないもの。
    """
    pruned, weights = (store_file(root, n) for n in (PRUNED, WEIGHTS))
    last = _month_in(_read_at(fd, pruned.name), pruned) if pruned.name in files else None
    if last is None or last >= month(month_start(month(now), -1)):
        return False
    if weights.name not in files:
        return True
    got = _parsed_weights(_read_at(fd, weights.name), now)
    return got is not None and _ndf_weights(got[0]) and not got[1]


def _ndf_weights(data: Any) -> bool:
    """`data` が NDF の書く出現率のファイルの形か。キーは `entries` だけで、その値は項目の配列。項目は日時の文字列 `at` と、
    整数の `window_days` を持つ割り付けの値 `allocation` を持つ。形が違えば利用者が置いたものとして回収しない。
    """
    if not isinstance(data, dict) or set(data) != {"entries"} or not isinstance(data["entries"], list):
        return False
    return all(
        isinstance(e, dict)
        and isinstance(e.get("at"), str)
        and isinstance(e.get("allocation"), dict)
        and type(e["allocation"].get("window_days")) is int
        for e in data["entries"]
    )


def _read_at(fd: int, name: str) -> bytes:
    """開いたディレクトリ `fd` の中のファイル `name` を、シンボリックリンクをたどらずに読む。"""
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as f:
        return f.read()


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
    1 つの候補で失敗しても警告を出して次の候補へ進む。途中で例外が出ても、書き手を返してからデータベースを閉じる。
    """
    path = store_file(base, STORE_LOCK)
    try:
        with _as_store_error(path, acquiring=True):
            lock = _store_lock(base, shared=False)
            lock.acquire_write(timeout=0, blocking=False)
    except Timeout:
        log.info("起動が実験の記録を使っているので、役目を終えたリポジトリの回収を翌月に回す")
        return
    except OSError as exc:
        log.warning("保存先の共有ロックを取れないので、役目を終えたリポジトリの回収を翌月に回す: %s", exc)
        return
    try:
        base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for root in roots:
                _reclaim_repo(base_fd, root, now)
        finally:
            os.close(base_fd)
    finally:
        _let_go(lock, path)


def _let_go(lock: ReadWriteLock, path: Path) -> None:
    """書き手を返し、返せなくてもデータベースを閉じる（閉じれば書き手も外れる）。どちらの失敗も警告にとどめる。"""
    for step in (lock.release, lock.close):
        try:
            with _as_store_error(path):
                step()
        except OSError as exc:
            log.warning("保存先の共有ロックを返せない（プロセスの終了で外れる）: %s", exc)


def _reclaim_repo(base_fd: int, root: Path, now: datetime) -> None:
    """候補 `root` を、シンボリックリンクをたどらずに開き、そのディレクトリの中だけを確かめて消す。"""
    try:
        fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
    except OSError as exc:
        log.warning("役目を終えたリポジトリの実験の記録を回収できない（翌月にやり直す）: %s: %s", root, exc)
        return
    try:
        files = _ndf_files(root, fd)
        if files is not None and _idle(root, fd, files, now):
            _drop_dir(base_fd, root, fd, files)
    except OSError as exc:
        log.warning("役目を終えたリポジトリの実験の記録を回収できない（翌月にやり直す）: %s: %s", root, exc)
    finally:
        os.close(fd)


def _ndf_files(root: Path, fd: int) -> Optional[dict[str, os.stat_result]]:
    """開いたディレクトリ `fd`（`root`）の中身が、NDF が作ったと確かめられるファイルだけなら、名前と状態を返す。そうでなければ None。

    NDF の作るファイルは、`_RETIRE_FILES` の名前と、`write_replacing` の書きかけの一時ファイルで、この利用者の持つ通常のファイルに
    限る。さらに中身や作り方で確かめる。ロックのファイルは filelock が何も書かないので空、一時ファイルは所有者だけが読み書きできる形
    （0600）、掃除の記録と出現率のファイルは `_idle` が NDF の書く形かを読んで確かめる。ほかの名前のもの・シンボリックリンク・
    ほかの利用者のファイル・形の合わないファイルが 1 つでもあれば、利用者が置いたものとして何も消さない。
    """
    names = {store_file(root, n).name for n in _RETIRE_FILES}
    locks = {store_file(root, n).name for n in (LEDGER_LOCK, WEIGHTS_LOCK, PRUNED_LOCK)}
    found: dict[str, os.stat_result] = {}
    for name in os.listdir(fd):
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        tmp = any(_tmp_of(name, n) for n in names)
        made = stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and (name in names or tmp)
        if not made or (name in locks and st.st_size) or (tmp and stat.S_IMODE(st.st_mode) != 0o600):
            log.info("NDF が作ったと確かめられないものがあるので、回収しない: %s", root / name)
            return None
        found[name] = st
    return found


def _tmp_of(name: str, target: str) -> bool:
    """`name` が `write_replacing` が `target` を書くときの一時ファイルの名前か。"""
    head, tail = f".{target}.", ".tmp"
    return name.startswith(head) and name.endswith(tail) and _TMP_ID.fullmatch(name[len(head) : -len(tail)]) is not None


def _drop_dir(base_fd: int, root: Path, fd: int, files: Mapping[str, os.stat_result]) -> None:
    """開いたディレクトリ `fd` から `_ndf_files` が確かめたファイル `files` を消してから、ディレクトリを消す。

    掃除の記録（`launch-pruned`）は最後に消す。ほかのファイルの削除で失敗すれば `launch-pruned` が残り、翌月の回収がやり直す。
    `launch-pruned` を消した後にディレクトリを消せなければ、呼び手が警告を出し、翌月の回収の候補にはならない。
    ディレクトリを消せない（親に書けない）ときは、何も消さずに止める。保存先の `root` の名前が開いたディレクトリと別のものに
    置き換わっていれば、ディレクトリは消さない。
    """
    if not os.access(".", os.W_OK | os.X_OK, dir_fd=base_fd):
        raise PermissionError(f"{root.parent} に書けないので {root} を消せない")
    marker = store_file(root, PRUNED).name
    for name in sorted(files, key=lambda n: n == marker):
        os.unlink(name, dir_fd=fd)
    if not os.path.samestat(os.fstat(fd), os.stat(root.name, dir_fd=base_fd, follow_symlinks=False)):
        raise FileExistsError(f"{root} が回収の途中で別のものに置き換わった")
    os.rmdir(root.name, dir_fd=base_fd)
