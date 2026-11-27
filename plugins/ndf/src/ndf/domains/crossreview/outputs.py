"""`cross-review run` の出力ファイル（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`）の片付けと書き出し（#3702）。

始めに前の起動の出力を片付け、書けなかった起動の後に前の値を残さない。ただし自動で消すのは NDF が書いたと確かめられたファイル
だけである（PR 4638 の C4）。書いた記録は出力の隣の `.<名前>.ndf-output.json` に置く。状態の保存先には置かないので、状態の
保存先が違う起動（通常のレビューと差分のレビュー）も同じ出力の記録を読む（PR 4638 の r1-1）。記録には中身の hash と、
ファイルの同一性（デバイス・inode・大きさ・更新時刻）を残す。消した利用者が同じパスへ同じ中身のファイルを作り直しても、
NDF が書いたファイルとはみなさない（PR 4638 の r1-4）。消すときは記録も消す。

記録の無い既存の出力（記録を残さない前のバージョンが書いたものを含む）は、来歴を確かめられないので消さずに止まる
（PR 4638 の r3-2）。NDF の工程（実装のテンプレート・単位のレビュー）は起動の前に自分の出力を消すので、ここでは止まらない。

記録には種別（`ndf_record`）を書き、片付けで消すのはこの種別の記録と、その記録が指す一時のファイルだけである（PR 4638 の r2-5）。

無い出力は一時のファイルへ書き、記録を残してから出力の名前へ置き換える（PR 4638 の r1-4）。置き換えは inode と更新時刻を
変えないので、記録はそのまま公開後の出力を指す。一時のファイルの名前は、作る前に記録へ書き、作った後にその inode も書く。
公開の前に強制終了されても、次の起動は記録から一時のファイルを見つけ、同一性を確かめて消す（PR 4638 の r2-7）。一時のファイルは
`write_text` と同じ作成時の権限（0666 から umask と親のデフォルト ACL が引かれる）で作り、後から権限を広げない（PR 4638 の r2-6）。

呼び手が先に作った空の出力先は、その inode と権限を保つために直接書く（PR 4638 の r3-3）。書く前に、その inode と書く中身を
「書きかけ」の記録として残す。書いている途中や書いた後に終了しても、次の起動は inode が合い、中身が書く中身の先頭の部分である
ことで今回の書き込みと確かめ、空へ戻して続く（PR 4638 の r2-8）。
記録を残せなければ書く前の状態（無い・空）へ戻す。次の起動は記録の無い出力で止まらない（PR 4638 の r2-4）。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional

from ndf.domains.crossreview import store

# 出力の記録のファイル名（`.<出力の名前><RECORD_SUFFIX>`）
RECORD_SUFFIX = ".ndf-output.json"
# 記録の種別（NDF が書いた記録だけを片付ける。PR 4638 の r2-5）
RECORD_KIND_KEY = "ndf_record"
RECORD_KIND = "cross-review-output"
# 記録の中身のキー: 書いた出力の同一性・空の出力先へ書いている途中の inode と中身・公開の前の一時のファイル
WRITTEN_KEY = "written"
WRITING_KEY = "writing"
STAGING_KEY = "staging"


class Unowned(Exception):
    """出力先に、NDF が書いたと確かめられない中身のあるファイルがある（消さずに止まる）。"""


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
    """空の出力先へ直接書く前に残す「書きかけ」の記録（inode と書く中身）。中身は出力と同じ値なので、記録に写しても読める人は増えない。"""
    st = os.stat(path)
    return _record(**{WRITING_KEY: {"dev": st.st_dev, "ino": st.st_ino, "text": text}})


def _load_record(record: Path) -> Optional[dict[str, Any]]:
    """NDF が書いた記録の中身。無い・読めない・種別が違えば None。"""
    try:
        found = store.read_object(record)
    except store.StateBroken:  # noqa: NDF401 ファイル: 読めない記録は、NDF が書いたと確かめられないとして消さない
        return None
    return found if found.get(RECORD_KIND_KEY) == RECORD_KIND else None


def _owned(path: Path, found: dict[str, Any]) -> Optional[str]:
    """`path` を NDF が書いたと記録 `found` で確かめられるか。書いたままなら `"written"`、空の出力先へ書いている途中か書いた後に
    落ちたなら `"writing"`、確かめられなければ None。"""
    try:
        if found.get(WRITTEN_KEY) == _file_identity(path):
            return "written"
        writing = found.get(WRITING_KEY)
        if not isinstance(writing, dict) or not isinstance(writing.get("text"), str):
            return None
        st = os.stat(path)
        same = (writing.get("dev"), writing.get("ino")) == (st.st_dev, st.st_ino)
        # 途中で止まった書き込みは、書く中身の先頭の部分だけを残す（PR 4638 の r2-8）
        return "writing" if same and writing["text"].encode("utf-8").startswith(path.read_bytes()) else None
    except OSError:  # noqa: NDF401 ファイル: 出力を読めなければ、NDF が書いたと確かめられないとして消さない
        return None


def _collect_staging(path: Path, found: dict[str, Any]) -> None:
    """公開の前に落ちた起動が残した一時のファイルを、記録の名前と同一性で確かめて消す（PR 4638 の r2-7）。確かめられなければ
    `OSError`（利用者のファイルかもしれないので消さない）。"""
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
        # 名前を記録してから作り、inode を書く前に落ちた。作ったばかりで何も書いていないので空である
        ours = st.st_size == 0
    if not (ours and stat.S_ISREG(st.st_mode)):
        raise OSError(f"一時のファイル {tmp} が、NDF の作ったものと確かめられない")
    tmp.unlink()


def clear_stale(path: Path) -> None:
    """前の起動の出力を片付け、出力と記録の保存先に書けるかを確かめる（親が無い・書けない・記録の保存先がファイルでない・
    NDF が書いたと確かめられない記録がある・残った記録や一時のファイルを消せなければ `OSError`）。

    - NDF が書いたままのファイルは、記録とともに消す
    - 空の出力先へ書いている途中か書いた後に落ちたファイル（書きかけの記録と inode・中身が合うもの）は、空へ戻して残す
    - 空のファイル（呼び手が先に作った出力先など）は失う中身が無いので、消さずに残す。レビューの後にそのファイルへ書く。
      書いた後に記録を作るために読み直すので、読み書きの両方ができるかを確かめる（PR 4638 の r1-5）
    - ほかの既存のファイル（記録の無い・作り直された・書き換えられたもの）は、利用者のデータかもしれないので消さずに `Unowned`
    - 公開の前に落ちた起動の一時のファイルは、記録で確かめて消す（PR 4638 の r2-7）
    """
    record = owner_record(path)
    if os.path.lexists(record) and not record.is_file():
        # レビューの後に記録を残せないと分かっても遅いので、相手役を起動する前に止める（PR 4638 の r2-4）
        raise OSError(f"出力の記録の保存先 {record} がファイルでない")
    found = _load_record(record) if record.exists() else None
    if found is not None:
        _collect_staging(path, found)
    if path.exists():
        _clear_output(path, found)
    if record.exists() and found is None:
        # 記録の名前のファイルを NDF が書いたと確かめられない。利用者のファイルかもしれないので消さずに止まる（PR 4638 の r2-5）
        raise OSError(f"出力の記録の保存先 {record} に、NDF が書いたと確かめられないファイルがある")
    if not path.exists():
        path.open("x", encoding="utf-8").close()
        path.unlink()
    # 残った記録（出力の無い記録・空の出力先の書きかけの記録）は、どの出力も指さない。消して、後で記録を置き換えられるかを
    # 確かめる（sticky bit のある共有ディレクトリでは、他の利用者の記録を消せず、置き換えもできない。PR 4638 の r1-6）
    record.unlink(missing_ok=True)
    # 記録は親のディレクトリに一時のファイルを作ってから置き換えて書く（`store.write_object`）。同じ作り方で確かめる（PR 4638 の r3-5）
    fd, probe = tempfile.mkstemp(dir=path.parent, prefix=f".{record.name}.", suffix=".tmp")
    os.close(fd)
    os.unlink(probe)


def _clear_output(path: Path, found: Optional[dict[str, Any]]) -> None:
    """既存の出力を、空なら読み書きできるかを確かめて残し、NDF が書いたと確かめられれば片付け、確かめられなければ `Unowned`。"""
    if path.is_file() and path.stat().st_size == 0:
        path.open("r+", encoding="utf-8").close()
        return
    owned = None if found is None else _owned(path, found)
    if owned is None:
        raise Unowned(str(path))
    if owned == "writing":
        path.open("w", encoding="utf-8").close()
    else:
        path.unlink()


def write_output(path: Path, text: str) -> None:
    """出力を書き、書いたファイルの記録を隣に残す（次の起動が消してよい出力かを確かめる）。記録は出力を公開する前に残し、
    途中で落ちても次の起動が来歴を確かめられるようにする（PR 4638 の r1-4）。

    - 無い出力は、一時のファイルの名前を記録してから作り、その inode を記録し、中身を書いて同一性を記録してから、出力の名前へ置き換える
    - 先に作られた空のファイルは、書きかけの記録を残してから、その inode と権限のまま書き、書いた後の記録へ書き直す

    記録を残せなければ、出力を書く前の状態（無い・空）へ戻して `OSError`。"""
    record = owner_record(path)
    if not path.exists():
        _publish(path, record, text)
        return
    try:
        store.write_object(record, _writing_record(path, text))
        path.write_text(text, encoding="utf-8")
        store.write_object(record, _record(**{WRITTEN_KEY: _file_identity(path)}))
    except BaseException as exc:
        path.open("w", encoding="utf-8").close()
        _forget_record(record, exc)
        raise


def _publish(path: Path, record: Path, text: str) -> None:
    """無い出力を一時のファイルから公開する。"""
    staging: dict[str, Any] = {"tmp": f".{path.name}.{secrets.token_hex(8)}.tmp"}
    tmp = path.parent / staging["tmp"]
    made = False
    try:
        store.write_object(record, _record(**{STAGING_KEY: staging}))
        # `write_text` と同じ作成時の権限で作る。umask と親のデフォルト ACL が引かれたまま公開する（PR 4638 の r2-6）
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        made = True
        with open(fd, "w", encoding="utf-8") as out:
            st = os.fstat(out.fileno())
            staging.update(dev=st.st_dev, ino=st.st_ino)
            store.write_object(record, _record(**{STAGING_KEY: staging}))
            out.write(text)
        store.write_object(record, _record(**{STAGING_KEY: staging, WRITTEN_KEY: _file_identity(tmp)}))
        os.replace(tmp, path)
    except BaseException as exc:
        if made:
            tmp.unlink(missing_ok=True)
        _forget_record(record, exc)
        raise


def _forget_record(record: Path, cause: BaseException) -> None:
    """書けなかった出力の記録を消す。消せなければ、元の失敗 `cause` に書き添える（残った記録は次の起動の片付けが消す）。"""
    try:
        record.unlink(missing_ok=True)
    except OSError as exc:  # 元の失敗を返すのを優先し、消せなかったことは書き添える
        cause.add_note(f"出力の記録 {record} を消せない: {exc}")
