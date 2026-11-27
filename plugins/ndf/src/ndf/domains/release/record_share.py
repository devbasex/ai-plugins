"""`record --version` の共通の前処理の保存先（共通の git ディレクトリの下の `ndf/release-record/<本番の PR>.json`）の
ファイル管理（#4580）。保存先の解決・読み書き・消去と、残った保存先と書きかけの一時ファイルの掃除を受け持つ。

届け先ごとに並べて打った呼び出しが前処理を 1 回だけ作って共有する手順は `record_unversioned` のモジュールの説明のとおり。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import string
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf import identity
from ndf.contracts import limits
from ndf.domains.release import record_decide
from ndf.domains.release.delivery_confirm import DeliveriesAt

if TYPE_CHECKING:
    from ndf.domains.release.record_unversioned import Hands

# 共通の前処理の保存先のロックを待つ上限
SHARE_LOCK = "release-record.share-lock"
# 保存先のディレクトリ（共通の git ディレクトリの下の `<NDF の名前>/release-record`）
_SHARE_DIR = "release-record"
# 保存先の JSON の `kind`。NDF が書いた保存先だけを読み、掃除する
_SHARE_KIND = "ndf-release-record.prefix/v1"
# 書きかけの一時ファイルの接尾辞。書き終えてから保存先へ差し替えるので、中断で残るのはこの名前のファイルだけである
_SHARE_TMP = ".ndf-tmp"
# 一時ファイルを作るディレクトリ（保存先のディレクトリの下）。NDF だけが作る場所で、掃除はここだけを見る
_SHARE_TMP_DIR = "tmp"
# 一時ファイルの名前の乱数の長さ（`secrets.token_hex` のバイト数。名前には 16 進でその 2 倍の文字が入る）
_SHARE_TMP_TOKEN = 8
# 保存先の名前の形（`{number}` は本番の PR の番号）
_SHARE_NAME = "{number}.json"
# NDF の一時ファイルの名前の形（`<本番の PR>.json.<16 進 16 文字>.ndf-tmp`）。作る側（`_tmp_name`）も掃除の一致の判定
# （`_SHARE_TMP_NAME`）もこの 1 つの形から組み立て、片側だけ形が変わって掃除が一時ファイルを見落とすことを防ぐ
_SHARE_TMP_FORMAT = _SHARE_NAME + ".{token}" + _SHARE_TMP
# 形の中の値ごとの一致を見る正規表現（名前付きの組で、掃除は `number` から同じ PR のロックを取る）
_SHARE_TMP_FIELDS = {"number": r"\d+", "token": rf"[0-9a-f]{{{2 * _SHARE_TMP_TOKEN}}}"}
# 掃除はこの名前だけを消す
_SHARE_TMP_NAME = re.compile(
    "".join(
        re.escape(literal) + (f"(?P<{field}>{_SHARE_TMP_FIELDS[field]})" if field is not None else "")
        for literal, field, _, _ in string.Formatter().parse(_SHARE_TMP_FORMAT)
    )
)


class Prefix(NamedTuple):
    """届け先によらず決まる前処理の結果。`deliveries` は push の `after` の時点の届け先の設定（worktree に設定が無ければ None）。"""

    commit: str
    push: Optional[record_decide.Push]
    deliveries: Optional[DeliveriesAt]
    pr_paths: frozenset[str]
    push_paths: frozenset[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": _SHARE_KIND,
            "commit": self.commit,
            "push": self.push._asdict() if self.push is not None else None,
            "at": self.deliveries.as_json() if self.deliveries is not None else None,
            "pr_paths": sorted(self.pr_paths),
            "push_paths": sorted(self.push_paths),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Prefix:
        push, at = raw["push"], raw["at"]
        return cls(
            raw["commit"],
            record_decide.Push(push["before"], push["after"]) if push is not None else None,
            DeliveriesAt.from_json(at) if at is not None else None,
            frozenset(raw["pr_paths"]),
            frozenset(raw["push_paths"]),
        )


def share_dir(hands: Hands) -> Path:
    """共有の保存先のディレクトリ（共通の git ディレクトリの下）。呼び手が解決済みなら git を起動せず、無ければ 1 回起動する。"""
    common = hands.common if hands.common is not None else hands.repo_git.common_dir()
    return common / identity.load().name / _SHARE_DIR


def _share_path(share: Path, number: int) -> Path:
    return share / _SHARE_NAME.format(number=int(number))


def _read_regular(path: Path) -> Optional[str]:
    """`path` が通常のファイルならその中身、そうでなければ None。

    リンクをたどらず、FIFO などで読み取りが止まらないように開いてから、通常のファイルかを確かめて読む。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:  # noqa: NDF401 ファイル: 無い・リンク・開けないファイルは None で返し、NDF が書いたものとして扱わない
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as raw:
            return raw.read().decode("utf-8")
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めないファイルは None で返し、NDF が書いたものとして扱わない
        return None
    finally:
        os.close(fd)


def _kind_of(text: str) -> Optional[Mapping[str, Any]]:
    """`text` が `kind` の `_SHARE_KIND` な JSON のオブジェクトならその中身、そうでなければ None。"""
    try:
        raw = json.loads(text)
    except ValueError:  # noqa: NDF401 ファイル: 壊れた中身は None で返し、NDF が書いたものとして扱わない
        return None
    return raw if isinstance(raw, dict) and raw.get("kind") == _SHARE_KIND else None


def _ndf_share(path: Path) -> Optional[Mapping[str, Any]]:
    """NDF が書いた保存先の中身（名前が `<本番の PR>.json` で、`kind` が `_SHARE_KIND`）。それ以外・無い・壊れていれば None。"""
    if not path.stem.isdigit():
        return None
    text = _read_regular(path)
    return None if text is None else _kind_of(text)


def _read_share(path: Path, commit: str, push: Optional[record_decide.Push] = None) -> Optional[Prefix]:
    """保存先の前処理（NDF が書き、反映したコミットが今と同じで、`push` を渡せばその push と同じときだけ）。当たらなければ None。

    同じ値の信用する記録の push（`push`）は保存先より優先する。保存先が別の push を指していれば使わずに作り直す。"""
    raw = _ndf_share(path)
    if raw is None:
        return None
    try:
        got = Prefix.from_json(raw)
    except (KeyError, TypeError, ValueError):  # noqa: NDF401 ファイル: 形の違う保存先は None で返して作り直す
        return None
    if got.commit != commit or (push is not None and got.push != push):
        return None
    return got


def _share_lock(path: Path, timeout: Optional[float] = None) -> FileLock:
    """保存先 `path` のロック（`path` に `.lock` を足した名前）。`timeout` を渡さなければ `SHARE_LOCK` の上限まで待つ。"""
    return FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK) if timeout is None else timeout)


def shared_prefix(
    share: Path, number: int, commit: str, compute: Callable[[], Prefix], push: Optional[record_decide.Push] = None
) -> Prefix:
    """届け先ごとに並べて打った呼び出しで共有する前処理（`share` は `share_dir`）。保存先のロックを取り、反映したコミットが
    同じもの（`push` を渡せばその push のもの）があれば読み、無いか違えば `compute` で作って書く。

    NDF が書いたものでないファイルが同じ名前で置かれているか、一時ファイルを作れない（`tmp/` が塞がれている・書き込めない）
    ときは、ロックを外してから `compute` で作ってその呼び出しだけで使う。共有できない前処理で、並べて打った呼び出しを
    待たせないためである（Value 5・6）。一時ファイルは `compute` の前に作り、作れるかを前処理の前に確かめる。"""
    path = _share_path(share, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _share_lock(path):
        got = _read_share(path, commit, push)
        if got is not None:
            return got
        opened = _open_tmp(share, number) if not path.exists() or _ndf_share(path) is not None else None
        if opened is not None:
            return _publish(path, opened, compute)
    # 同じ名前のファイルがあるか、一時ファイルを作れないので共有できない。その呼び出しだけで使う
    return compute()


def _tmp_name(number: int) -> str:
    """本番の PR `number` の一時ファイルの名前（`_SHARE_TMP_FORMAT` の形で、`_SHARE_TMP_NAME` に当たる）。"""
    return _SHARE_TMP_FORMAT.format(number=int(number), token=secrets.token_hex(_SHARE_TMP_TOKEN))


def _open_tmp(share: Path, number: int) -> Optional[tuple[int, Path]]:
    """本番の PR `number` の一時ファイルを、NDF だけが作るディレクトリ（`share` の下の `tmp/`。無ければ作る）に新しく作り、
    書き込み用に開く。ディレクトリがリンクや通常のファイルで塞がれている・書き込めないなど、作れなければ None。"""
    tmp_dir = share / _SHARE_TMP_DIR
    try:
        tmp_dir.mkdir(exist_ok=True)
        if not stat.S_ISDIR(tmp_dir.lstat().st_mode):
            return None
        tmp = tmp_dir / _tmp_name(number)
        return os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), tmp
    except OSError:  # noqa: NDF401 ファイル: 一時ファイルを作れなければ None で返し、共有せずに続ける
        return None


def _publish(path: Path, opened: tuple[int, Path], compute: Callable[[], Prefix]) -> Prefix:
    """`compute` で作った前処理を、開いた一時ファイル `opened` に書き終えてから `path` へ差し替え、前処理を返す。
    中断しても `path` が書きかけにならない。

    書き込みが途中で止まると一部だけ書かれた一時ファイルが残ることがあるので、掃除は中身を読まず、`tmp/` にあることと
    名前（`_SHARE_TMP_NAME`）で判断する。`compute` が失敗したときは一時ファイルを消す。"""
    fd, tmp = opened
    try:
        with os.fdopen(fd, "wb") as out:
            got = compute()
            out.write(json.dumps(got.as_json(), ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return got


def drop_share(share: Path, number: int) -> None:
    """本番の PR の保存先を消す（push を読み、`complete` が真になった呼び出し）。NDF が書いたものでなければ残す。"""
    path = _share_path(share, number)
    with FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK)):
        if _ndf_share(path) is not None:
            path.unlink(missing_ok=True)


def _drop_stale(path: Path, older_than: float, now: Callable[[], float]) -> None:
    """NDF が書き、ロックされておらず、作ってから `older_than` 秒より長く経った保存先 `path` を消す（ほかのファイルは残す）。"""
    if _ndf_share(path) is None:
        return
    try:
        with FileLock(str(path) + ".lock", timeout=0):
            # ロックを取る前の確かめの後に置き換わっていないかを、ロックの中で確かめ直す
            if _ndf_share(path) is not None and now() - path.stat().st_mtime > older_than:
                path.unlink(missing_ok=True)
    except (OSError, Timeout):  # noqa: NDF401 ファイル: 使っている保存先と消えた保存先は残す
        return


def _drop_stale_tmp(share: Path, path: Path, number: int, older_than: float, now: Callable[[], float]) -> None:
    """NDF だけが作るディレクトリに中断で残った本番の PR `number` の一時ファイル `path` を、作ってから `older_than` 秒より
    長く経っていれば消す。

    書く側は一時ファイルを前処理の前に作り、前処理の間も保存先のロックを持つ。前処理が `older_than` より長くかかっても
    消さないよう、同じ PR のロックをすぐ取れたときだけ、ロックの中で経った時間を確かめ直して消す。中身は読まない
    （途中まで書かれたものも消す）。リンクをたどらず、通常のファイルだけを消す。"""
    try:
        if not _stale_regular(path, older_than, now):
            return
        with FileLock(str(_share_path(share, number)) + ".lock", timeout=0):
            if _stale_regular(path, older_than, now):
                path.unlink(missing_ok=True)
    except (OSError, Timeout):  # noqa: NDF401 ファイル: 書いている途中の一時ファイルと消えた一時ファイルは残す
        return


def _stale_regular(path: Path, older_than: float, now: Callable[[], float]) -> bool:
    """`path` がリンクでない通常のファイルで、作ってから `older_than` 秒より長く経っていれば真。"""
    got = path.lstat()
    return stat.S_ISREG(got.st_mode) and now() - got.st_mtime > older_than


def sweep_shares(share: Path, older_than: float, now: Callable[[], float] = time.time) -> None:
    """`complete` にならずに残った保存先と、中断で残った書きかけの一時ファイルのうち、`older_than` 秒より長く経ったものを
    消す（保存先はロックされていないものだけ）。"""
    for path in share.glob("*.json"):
        _drop_stale(path, older_than, now)
    tmp_dir = share / _SHARE_TMP_DIR
    try:
        if not stat.S_ISDIR(tmp_dir.lstat().st_mode):
            return
        names = list(tmp_dir.iterdir())
    except OSError:  # noqa: NDF401 ファイル: 無い・列挙できない一時ファイルのディレクトリは掃除せず、記録へ進む
        return
    for path in names:
        matched = _SHARE_TMP_NAME.fullmatch(path.name)
        if matched is not None:
            _drop_stale_tmp(share, path, int(matched["number"]), older_than, now)
