"""`cross-review run` の出力ファイル（`--reviewed-out`・`--reviewed-pr-out`・`--reviewed-base-out`）の片付けと書き出し（#3702）。

始めに前の起動の出力を片付け、書けなかった起動の後に前の値を残さない。ただし自動で消すのは NDF が書いたと確かめられたファイル
だけである（PR 4638 の C4）。書いた記録は出力の隣の `.<名前>.ndf-output.json` に置く。状態の保存先には置かないので、状態の
保存先が違う起動（通常のレビューと差分のレビュー）も同じ出力の記録を読む（PR 4638 の r1-1）。記録には中身の hash と、
ファイルの同一性（デバイス・inode・大きさ・更新時刻）を残す。消した利用者が同じパスへ同じ中身のファイルを作り直しても、
NDF が書いたファイルとはみなさない（PR 4638 の r1-4）。消すときは記録も消す。

記録の無い既存の出力（記録を残さない前のバージョンが書いたものを含む）は、来歴を確かめられないので消さずに止まる
（PR 4638 の r3-2）。NDF の工程（実装のテンプレート・単位のレビュー）は起動の前に自分の出力を消すので、ここでは止まらない。

無い出力は一時のファイルへ書き、記録を残してから出力の名前へ置き換える（PR 4638 の r1-4）。置き換えは inode と更新時刻を
変えないので、記録はそのまま公開後の出力を指す。公開の前に落ちても、残るのは出力の無い記録だけで、次の起動はそれを消して続く。
呼び手が先に作った空の出力先は、その inode と権限を保つために直接書く（PR 4638 の r3-3）。書く前に、その inode と書く中身の
hash を「書きかけ」の記録として残す。書いた後に落ちても、次の起動はこの記録で今回の書き込みと確かめ、空へ戻して続く。
記録を残せなければ書く前の状態（無い・空）へ戻す。次の起動は記録の無い出力で止まらない（PR 4638 の r2-4）。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from ndf.domains.crossreview import store

# 出力の記録のファイル名（`.<出力の名前><RECORD_SUFFIX>`）
RECORD_SUFFIX = ".ndf-output.json"
# 空の出力先へ直接書く前に残す、書きかけの記録のキー
PENDING_KEY = "pending_write"


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


def _writing_record(path: Path, text: str) -> dict[str, Any]:
    """空の出力先へ直接書く前に残す「書きかけ」の記録（inode と、書く中身の hash）。"""
    st = os.stat(path)
    return {PENDING_KEY: {"dev": st.st_dev, "ino": st.st_ino, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}}


def _owned(path: Path) -> Optional[str]:
    """`path` を NDF が書いたと確かめられるか。書いたままなら `"written"`、空の出力先へ書いている途中で落ちたなら `"pending"`、
    確かめられなければ None。"""
    try:
        found = store.read_object(owner_record(path))
        if found == _file_identity(path):
            return "written"
        pending = found.get(PENDING_KEY)
        st = os.stat(path)
        same = pending == {"dev": st.st_dev, "ino": st.st_ino, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except (store.StateBroken, OSError):  # noqa: NDF401 ファイル: 記録か出力を読めなければ、NDF が書いたと確かめられないとして消さない
        return None
    return "pending" if same else None


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def clear_stale(path: Path) -> None:
    """前の起動の出力を片付け、出力と記録の保存先に書けるかを確かめる（親が無い・書けない・記録の保存先がファイルでない・
    残った記録を置き換えられなければ `OSError`）。

    - NDF が書いたままのファイルは、記録とともに消す
    - 空の出力先へ書いている途中で落ちたファイル（書きかけの記録と inode・中身が合うもの）は、空へ戻して残す
    - 空のファイル（呼び手が先に作った出力先など）は失う中身が無いので、消さずに残す。レビューの後にそのファイルへ書く。
      書いた後に記録を作るために読み直すので、読み書きの両方ができるかを確かめる（PR 4638 の r1-5）
    - ほかの既存のファイル（記録の無い・作り直された・書き換えられたもの）は、利用者のデータかもしれないので消さずに `Unowned`
    """
    record = owner_record(path)
    if record.exists() and not record.is_file():
        # レビューの後に記録を残せないと分かっても遅いので、相手役を起動する前に止める（PR 4638 の r2-4）
        raise OSError(f"出力の記録の保存先 {record} がファイルでない")
    if path.exists():
        if path.is_file() and path.stat().st_size == 0:
            path.open("r+", encoding="utf-8").close()
        else:
            owned = _owned(path)
            if owned is None:
                raise Unowned(str(path))
            if owned == "pending":
                path.open("w", encoding="utf-8").close()
            else:
                path.unlink()
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


def write_output(path: Path, text: str) -> None:
    """出力を書き、書いたファイルの記録を隣に残す（次の起動が消してよい出力かを確かめる）。記録は出力を公開する前に残し、
    途中で落ちても次の起動が来歴を確かめられるようにする（PR 4638 の r1-4）。

    - 無い出力は、一時のファイルへ書いて記録を残してから、出力の名前へ置き換える
    - 先に作られた空のファイルは、書きかけの記録を残してから、その inode と権限のまま書き、書いた後の記録へ書き直す

    記録を残せなければ、出力を書く前の状態（無い・空）へ戻して `OSError`。"""
    record = owner_record(path)
    if not path.exists():
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(name)
        try:
            with open(fd, "w", encoding="utf-8") as out:
                out.write(text)
            # `mkstemp` は所有者だけの権限で作るので、`write_text` と同じ umask に従う権限にそろえる
            tmp.chmod(0o666 & ~_umask())
            store.write_object(record, _file_identity(tmp))
            os.replace(tmp, path)
        except BaseException as exc:
            tmp.unlink(missing_ok=True)
            _forget_record(record, exc)
            raise
        return
    try:
        store.write_object(record, _writing_record(path, text))
        path.write_text(text, encoding="utf-8")
        store.write_object(record, _file_identity(path))
    except BaseException as exc:
        path.open("w", encoding="utf-8").close()
        _forget_record(record, exc)
        raise


def _forget_record(record: Path, cause: BaseException) -> None:
    """書けなかった出力の記録を消す。消せなければ、元の失敗 `cause` に書き添える（残った記録は次の起動の片付けが消す）。"""
    try:
        record.unlink(missing_ok=True)
    except OSError as exc:  # 元の失敗を返すのを優先し、消せなかったことは書き添える
        cause.add_note(f"出力の記録 {record} を消せない: {exc}")
