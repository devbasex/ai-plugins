"""`cross-review run` の出力ファイル（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`）の片付けと書き出し（#3702）。

始めに前の起動の出力を片付け、書けなかった起動の後に前の値を残さない。ただし自動で消すのは NDF が書いたと確かめられたファイル
だけである（PR 4638 の C4）。書いた記録は出力の隣の `.<名前>.ndf-output.json` に置く。状態の保存先には置かないので、状態の
保存先が違う起動（通常のレビューと差分のレビュー）も同じ出力の記録を読む（PR 4638 の r1-1）。記録には中身の hash と、
ファイルの同一性（デバイス・inode・大きさ・更新時刻）を残す。消した利用者が同じパスへ同じ中身のファイルを作り直しても、
NDF が書いたファイルとはみなさない（PR 4638 の r1-4）。

記録の無い既存の出力（記録を残さない前のバージョンが書いたものを含む）は、来歴を確かめられないので消さずに止まる
（PR 4638 の r3-2）。NDF の工程（実装のテンプレート・単位のレビュー）は起動の前に自分の出力を消すので、ここでは止まらない。

記録には種別（`ndf_record`）を書き、片付けで消すのはこの種別の記録（と空の記録）と、その記録が指す一時のファイルだけである
（PR 4638 の r2-5）。

起動は始めに記録のファイルに錠（`filelock`）を取り、終わるまで持つ（`claim`）。同じ出力先を使う起動が重なれば、後の起動は
片付けの前に止まり、実行中の起動の一時のファイルや出力を消さない（PR 4638 の r3-4）。記録は置き換えでなく、錠を持った
ファイルへその場で書く。置き換えると錠が新しいファイルへ移らないためで、記録のための一時のファイルも作らない（PR 4638 の r3-5）。
出力や一時のファイルを指す記録は、前の記録の後ろへ 1 行として足し、読むときは終端の改行まで書けた最後の行を使う。書き込みの
途中で終了しても前の記録が残り、書き切れなかった行は読み飛ばす（PR 4638 の r5-2）。何も指さない記録だけは切り詰めてから書き、記録を小さく保つ（PR 4638 の r4-5）。
記録には書く中身を残すので、所有者のほかに読み書きの権限がある既存の記録は使わずに止まる（PR 4638 の r4-4）。
片付けや書き込みの後始末に失敗した起動は、記録が指す出力と一時のファイルを次の起動が片付けられるよう、記録を消さない
（PR 4638 の r4-6・r4-7）。

無い出力は一時のファイルへ書き、記録を残してから出力の名前へ置き換える（PR 4638 の r1-4）。置き換えは inode と更新時刻を
変えないので、記録はそのまま公開後の出力を指す。一時のファイルの名前と書く中身は作る前に記録へ書き、書いた後に同一性を書く。
公開の前に強制終了されても、次の起動は記録から一時のファイルを見つけ、同一性（同一性を書く前なら、中身が書く中身の先頭の
部分であること）を確かめて消す（PR 4638 の r2-7）。一時のファイルは `write_text` と同じ作成時の権限（0666 から umask と親の
デフォルト ACL が引かれる）で作り、後から権限を広げない（PR 4638 の r2-6）。

呼び手が先に作った空の出力先は、その inode と権限を保つために直接書く（PR 4638 の r3-3）。書く前に、その inode と書く中身を
「書きかけ」の記録として残す。書いている途中や書いた後に終了しても、次の起動は inode が合い、中身が書く中身の先頭の部分である
ことで今回の書き込みと確かめ、空へ戻して続く（PR 4638 の r2-8）。
記録を残せなければ書く前の状態（無い・空）へ戻す。次の起動は記録の無い出力で止まらない（PR 4638 の r2-4）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from filelock import FileLock, Timeout

# 出力の記録のファイル名（`.<出力の名前><RECORD_SUFFIX>`）
RECORD_SUFFIX = ".ndf-output.json"
# 記録の種別（NDF が書いた記録だけを片付ける。PR 4638 の r2-5）
RECORD_KIND_KEY = "ndf_record"
RECORD_KIND = "cross-review-output"
# 記録の中身のキー: 書いた出力の同一性・空の出力先へ書いている途中の inode と中身・公開の前の一時のファイル
WRITTEN_KEY = "written"
WRITING_KEY = "writing"
STAGING_KEY = "staging"
# 何も指さない記録の行（途中で切れた書き込みも、指す物の無い空の記録と同じに読む。PR 4638 の r4-5）
_BARE = json.dumps({RECORD_KIND_KEY: RECORD_KIND}, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"


class Unowned(Exception):
    """出力先に、NDF が書いたと確かめられない中身のあるファイルがある（消さずに止まる）。"""


class Busy(Exception):
    """同じ出力先を、実行中の別の起動が使っている（片付けずに止まる。PR 4638 の r3-4）。"""


def owner_record(path: Path) -> Path:
    return path.with_name(f".{path.name}{RECORD_SUFFIX}")


def _file_identity(path: Path) -> dict[str, Any]:
    st = os.stat(path)
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": st.st_size,
        "dev": st.st_dev,
        "ino": st.st_ino,
        "mtime_ns": st.st_mtime_ns,
    }


def _record(**parts: Any) -> dict[str, Any]:
    return {RECORD_KIND_KEY: RECORD_KIND, **parts}


def _writing_record(path: Path, text: str) -> dict[str, Any]:
    """空の出力先へ直接書く前に残す「書きかけ」の記録（inode と書く中身）。中身は出力と同じ値で、記録は 0600 なので読める人は増えない。"""
    st = os.stat(path)
    return _record(**{WRITING_KEY: {"dev": st.st_dev, "ino": st.st_ino, "text": text}})


def _prefix_of(text: Any, path: Path) -> bool:
    """`path` の中身が `text` の先頭の部分か（途中で止まった書き込みは、書く中身の先頭の部分だけを残す。PR 4638 の r2-8）。"""
    return isinstance(text, str) and text.encode("utf-8").startswith(path.read_bytes())


def _owned(path: Path, found: dict[str, Any]) -> Optional[str]:
    """`path` を NDF が書いたと記録 `found` で確かめられるか。書いたままなら `"written"`、空の出力先へ書いている途中か書いた後に
    落ちたなら `"writing"`、確かめられなければ None。"""
    try:
        if found.get(WRITTEN_KEY) == _file_identity(path):
            return "written"
        writing = found.get(WRITING_KEY)
        if not isinstance(writing, dict):
            return None
        st = os.stat(path)
        same = (writing.get("dev"), writing.get("ino")) == (st.st_dev, st.st_ino)
        return "writing" if same and _prefix_of(writing.get("text"), path) else None
    except OSError:  # noqa: NDF401 ファイル: 出力を読めなければ、NDF が書いたと確かめられないとして消さない
        return None


def _collect_staging(path: Path, found: dict[str, Any]) -> None:
    """公開の前に落ちた起動が残した一時のファイルを、記録の名前と同一性で確かめて消す（PR 4638 の r2-7）。確かめられなければ
    `OSError`（利用者のファイルかもしれないので消さない）。錠を持っているので、実行中の起動の一時のファイルではない（PR 4638 の r3-4）。"""
    staging = found.get(STAGING_KEY)
    if not isinstance(staging, dict):
        return
    name = staging.get("tmp")
    if not (isinstance(name, str) and "/" not in name and name.startswith(f".{path.name}.") and name.endswith(".tmp")):
        raise OSError(f"出力の記録 {owner_record(path)} の一時のファイルの名前が正しくない")
    tmp = path.parent / name
    if not os.path.lexists(tmp):
        return
    st = os.lstat(tmp)
    if "ino" in staging:
        ours = (staging.get("dev"), staging.get("ino")) == (st.st_dev, st.st_ino)
    else:
        # 名前を記録してから作り、同一性を書く前に落ちた。中身は書く中身の先頭の部分である
        ours = stat.S_ISREG(st.st_mode) and _prefix_of(staging.get("text"), tmp)
    if not (ours and stat.S_ISREG(st.st_mode)):
        raise OSError(f"一時のファイル {tmp} が、NDF の作ったものと確かめられない")
    tmp.unlink()


class Claim:
    """1 つの出力先の錠と記録。記録のファイルに錠（`filelock`）を持ったまま、記録はこのファイルへその場で書く。"""

    def __init__(self, path: Path, fd: int, lock: FileLock, created: bool) -> None:
        self.path = path
        self.record = owner_record(path)
        self._fd = fd
        self._lock = lock
        # 消してよい記録か（この起動が作った・NDF の種別の記録だった）。利用者が先に作った空のファイルは残す
        self._ours = created
        self._written = False
        # 記録が、片付けの済んでいない出力か一時のファイルを指しているか。指していれば消さない（PR 4638 の r4-6・r4-7）
        self._pointing = False

    def _read(self) -> Optional[dict[str, Any]]:
        """記録の中身（終端の改行まで書けた最後の行）。空か、何も指さない記録を書く途中で切れたなら `{}`（NDF は作った直後に
        種別を書くので、空の記録は消さずに使うだけにする）、NDF の種別の記録でなければ None。"""
        raw = os.pread(self._fd, os.fstat(self._fd).st_size, 0)
        if b"\n" in raw:
            # 最後の改行の後ろは、終端まで書けなかった行なので使わない。末尾の改行だけが欠けた行も JSON としては読めるので、
            # 読めるかでなく終端の改行で書き切ったかを見る（PR 4638 の r5-2）
            lines = raw.split(b"\n")[:-1]
        else:
            # 改行の無い 1 つの JSON（前の書き方の記録）
            lines = [raw]
        found: Optional[dict[str, Any]] = None
        for line in lines:
            if not line:
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 改行の後ろへ足す途中で切れた行は読み飛ばす（PR 4638 の r4-5）
                continue
            if not (isinstance(value, dict) and value.get(RECORD_KIND_KEY) == RECORD_KIND):
                return None
            found = value
        if found is None and _BARE.startswith(raw):
            return {}
        return found

    def _put(self, value: dict[str, Any]) -> None:
        """記録を書く。出力か一時のファイルを指す記録は前の記録の後ろへ足し、途中で終了しても前の記録を残す。何も指さない記録は
        切り詰めてから書く（途中で切れても、指す物の無い空の記録と同じに読む）。書き切れなければ `OSError`（PR 4638 の r4-5）。"""
        line = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        if value.keys() <= {RECORD_KIND_KEY}:
            os.ftruncate(self._fd, 0)
            offset = 0
        else:
            offset = os.fstat(self._fd).st_size
            # 前の行が書き切れずに終わっていても、新しい行を別の行にする
            line = b"\n" + line
        if os.pwrite(self._fd, line, offset) != len(line):
            raise OSError(f"出力の記録 {self.record} を書き切れない")

    def clear(self) -> None:
        """前の起動の出力を片付ける（`claim` の説明のとおり）。"""
        found = self._read()
        if found is None:
            if self.path.exists() and not (self.path.is_file() and self.path.stat().st_size == 0):
                raise Unowned(str(self.path))
            # 記録の名前のファイルを NDF が書いたと確かめられない。利用者のファイルかもしれないので消さずに止まる（PR 4638 の r2-5）
            raise OSError(f"出力の記録の保存先 {self.record} に、NDF が書いたと確かめられないファイルがある")
        if found:
            self._ours = True
            # 片付けを終えるまでは記録を消さない。途中で止まっても、次の起動が同じ記録で確かめて片付ける（PR 4638 の r4-6・r4-7）
            self._pointing = bool(found.keys() - {RECORD_KIND_KEY})
            _collect_staging(self.path, found)
        if self.path.exists():
            _clear_output(self.path, found)
        if self._pointing:
            self._put(_record())
            self._pointing = False
        if not self.path.exists():
            # 出力の名前にファイルを作れるか（公開は同じディレクトリの一時のファイルからの置き換え）
            self.path.open("x", encoding="utf-8").close()
            self.path.unlink()

    def write(self, text: str) -> None:
        """出力を書き、書いたファイルの記録を残す。記録は出力を公開する前に残し、途中で落ちても次の起動が来歴を確かめられる
        ようにする（PR 4638 の r1-4）。記録を残せなければ、出力を書く前の状態（無い・空）へ戻して `OSError`。"""
        if self.path.exists():
            self._write_into(text)
        else:
            self._publish(text)
        self._written = True

    def _write_into(self, text: str) -> None:
        """先に作られた空のファイルへ、書きかけの記録を残してから、その inode と権限のまま書く。"""
        self._pointing = True
        try:
            self._put(_writing_record(self.path, text))
            self.path.write_text(text, encoding="utf-8")
            self._put(_record(**{WRITTEN_KEY: _file_identity(self.path)}))
        except BaseException as exc:
            try:
                self.path.open("w", encoding="utf-8").close()
            except OSError as undo:
                exc.add_note(f"出力 {self.path} を空へ戻せない（次の起動が記録で確かめて片付ける）: {undo}")
            else:
                self._reset(exc)
            raise

    def _publish(self, text: str) -> None:
        """無い出力を、一時のファイルの名前と中身を記録してから作り、同一性を記録してから出力の名前へ置き換える。"""
        staging: dict[str, Any] = {"tmp": f".{self.path.name}.{secrets.token_hex(8)}.tmp", "text": text}
        tmp = self.path.parent / staging["tmp"]
        made = False
        self._pointing = True
        try:
            self._put(_record(**{STAGING_KEY: staging}))
            # `write_text` と同じ作成時の権限で作る。umask と親のデフォルト ACL が引かれたまま公開する（PR 4638 の r2-6）
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            made = True
            with open(fd, "w", encoding="utf-8") as out:
                out.write(text)
            st = os.stat(tmp)
            staging.update(dev=st.st_dev, ino=st.st_ino)
            self._put(_record(**{STAGING_KEY: staging, WRITTEN_KEY: _file_identity(tmp)}))
            os.replace(tmp, self.path)
        except BaseException as exc:
            try:
                if made:
                    tmp.unlink(missing_ok=True)
            except OSError as undo:
                exc.add_note(f"一時のファイル {tmp} を消せない（次の起動が記録で確かめて片付ける）: {undo}")
            else:
                self._reset(exc)
            raise

    def _reset(self, cause: BaseException) -> None:
        """書けなかった出力の記録を、何も指さない記録へ戻す。戻せなければ、元の失敗 `cause` に書き添える（残った記録は次の起動の
        片付けが消す）。"""
        try:
            self._put(_record())
            self._pointing = False
        except OSError as exc:  # 元の失敗を返すのを優先し、戻せなかったことは書き添える
            cause.add_note(f"出力の記録 {self.record} を戻せない: {exc}")

    def release(self) -> None:
        """錠を外す。出力を書かなかった起動は、消してよい記録を消す（書いた起動の記録と、片付けの済んでいない物を指す記録は
        次の起動が読む）。"""
        try:
            if not self._written and self._ours and not self._pointing:
                self.record.unlink(missing_ok=True)
        finally:
            os.close(self._fd)
            self._lock.release()


def _lock(path: Path) -> Claim:
    """記録のファイルに錠を取り（無ければ 0600 で作る）、読み書きのために開く。別の起動が持っていれば `Busy`。"""
    record = owner_record(path)
    if os.path.lexists(record) and not record.is_file():
        # レビューの後に記録を残せないと分かっても遅いので、相手役を起動する前に止める（PR 4638 の r2-4）
        raise OSError(f"出力の記録の保存先 {record} がファイルでない")
    created = not os.path.lexists(record)
    # 記録は錠のファイルでもあるので、外すときに消さない（消すのは出力を書かなかった起動が自分で行う）
    lock = FileLock(str(record), mode=0o600, blocking=False, fallback_to_soft=False, preserve_lock_file=True)
    try:
        lock.acquire()
    except Timeout:
        raise Busy(str(path)) from None
    try:
        # 記録を書けるか（sticky bit のある共有ディレクトリで他の利用者が持つ記録など）もここで分かる（PR 4638 の r1-6）
        fd = os.open(record, os.O_RDWR | os.O_NOFOLLOW)
    except BaseException:
        lock.release()
        raise
    held = Claim(path, fd, lock, created)
    try:
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
    except BaseException:
        held.release()
        raise
    if mode & 0o077:
        # 記録には書く中身（ブランチ名・コミットの SHA）を残すので、他の利用者が読める既存の記録は使わない。利用者の
        # ファイルかもしれないので、権限を変えず、消さずに止まる（PR 4638 の r4-4）
        held._ours = False
        held.release()
        raise OSError(f"出力の記録 {record} に所有者のほかの読み書きの権限がある（{mode:o}）。権限を 600 にしてから打ち直す")
    if created:
        try:
            # 強制終了で残っても次の起動が NDF の記録と分かるよう、作った直後に種別を書く
            held._put(_record())
        except BaseException:
            held.release()
            raise
    return held


@contextmanager
def claim(path: Path) -> Iterator[Claim]:
    """出力先の錠を取り、前の起動の出力を片付けて、起動の終わりまで持つ（親が無い・書けない・記録の保存先がファイルでない・
    NDF が書いたと確かめられない記録がある・一時のファイルを確かめられなければ `OSError`、実行中の別の起動が使っていれば
    `Busy`、確かめられない出力があれば `Unowned`）。

    - NDF が書いたままのファイルは消す
    - 空の出力先へ書いている途中か書いた後に落ちたファイル（書きかけの記録と inode・中身が合うもの）は、空へ戻して残す
    - 空のファイル（呼び手が先に作った出力先など）は失う中身が無いので、消さずに残す。レビューの後にそのファイルへ書く。
      書いた後に記録を作るために読み直すので、読み書きの両方ができるかを確かめる（PR 4638 の r1-5）
    - ほかの既存のファイル（記録の無い・作り直された・書き換えられたもの）は、利用者のデータかもしれないので消さずに `Unowned`
    - 公開の前に落ちた起動の一時のファイルは、記録で確かめて消す（PR 4638 の r2-7）
    """
    held = _lock(path)
    try:
        held.clear()
        yield held
    finally:
        held.release()


def _clear_output(path: Path, found: dict[str, Any]) -> None:
    """既存の出力を、空なら読み書きできるかを確かめて残し、NDF が書いたと確かめられれば片付け、確かめられなければ `Unowned`。"""
    if path.is_file() and path.stat().st_size == 0:
        path.open("r+", encoding="utf-8").close()
        return
    owned = _owned(path, found) if found else None
    if owned is None:
        raise Unowned(str(path))
    if owned == "writing":
        path.open("w", encoding="utf-8").close()
    else:
        path.unlink()


def clear_stale(path: Path) -> None:
    """前の起動の出力を片付けて錠を外す（`claim` の 1 回分）。"""
    with claim(path):
        pass


def write_output(path: Path, text: str) -> None:
    """前の起動の出力を片付けてから出力を書き、錠を外す（`claim` の 1 回分）。"""
    with claim(path) as held:
        held.write(text)
