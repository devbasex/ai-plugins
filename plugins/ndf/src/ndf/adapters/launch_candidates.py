"""起動の承認の候補のディレクトリ（#1959）: 普通のファイルとしての読み取り、NDF が作ったものだけかの確かめ、退避と中断の後の片付け。

候補のディレクトリの中身だけを扱い、リクエスト・回答・承認の記録は読まない。どの候補を片付けるかは呼び手（`launch_approval`）が
履歴から決め、ここは決まった候補を、利用者の設定案と利用者の項目を失わない手順で退避し、消し、元の名前へ戻す。
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

# 候補のディレクトリの中身: 候補（反映先の写し）と、反映先・反映元のバージョン・作った時の候補のバージョンの記録
CANDIDATE_NAME = "candidate.json"
SOURCE_NAME = "source.json"
# 退避した候補の中の、消してよいバージョン（「承認しない」と回答したリクエストの候補のバージョン）の記録
DISCARD_NAME = "discard.json"
# 候補のディレクトリの作業中の名前の頭。作成途中（DRAFTING）、退避（REMOVING）、消す前の隔離（SETTLING）。
# 作成と掃除は候補の錠の中だけで行うので、錠の中で見つかる作成途中と隔離のディレクトリは中断の残りである
DRAFTING = ".draft-"
REMOVING = ".removing-"
SETTLING = ".settling-"
# 作成途中のディレクトリ `.draft-<名前>` の隣に置く記録 `.draft-<名前>.record` の拡張子。ディレクトリを作る前に名前の行を書き終え、
# 作った後に実体（`dev`・`ino`）の行を書き足す。掃除は、名前の形でなくこの記録で NDF が作ったと確かめたものだけを片付ける
DRAFT_RECORD = ".record"
# 作成途中の候補の名前の形。`new_candidate` が作る乱数（`uuid4().hex`）で、利用者が同じ名前のファイルやディレクトリを置くことはない
DRAFT_ID = re.compile(r"[0-9a-f]{32}")
# 作成途中のディレクトリの中で NDF が作るもの: 候補と反映元の記録、それらを書く途中の一時ファイル（`replace_private` の名前）
DRAFT_FILES = (CANDIDATE_NAME, SOURCE_NAME)
DRAFT_TEMP = re.compile(r"\.(" + "|".join(re.escape(n) for n in DRAFT_FILES) + r")\.[0-9a-f]{32}")


def replace_private(path: Path, data: bytes) -> None:
    """所有者だけが読めるファイルを、一時ファイルからの付け替えで書く。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_launch_record(path: Path, obj: Mapping[str, Any]) -> None:
    replace_private(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _untouched(made: Path, owned: _Owned, kept: Optional[str] = None) -> bool:
    """候補が、作った時（`written`）か `kept` のバージョンのままか。作った後に直した候補（Skill か利用者が設定案を書いた候補）は掃除で消さない。

    `written` は、所有を確かめた時に読んだ `source.json`（`owned`）から取り、開き直さない。候補も、所有を確かめた時と同じファイル
    （`owned.stamp`）のときだけ読んだ中身を使う。読めない候補（権限の無いファイル・ディレクトリ・FIFO など）と、所有を確かめた
    後に変わった候補は、直していないと確かめられないので偽を返す（掃除で消さず、一覧にも載せない）。
    """
    ok, now = stamped_version(made, owned.stamp)
    return ok and unedited(now, owned.record(SOURCE_NAME).get("written"), kept)


def stamped_version(made: Path, stamp: frozenset[tuple[str, int, int, int]]) -> tuple[bool, Optional[str]]:
    """候補 `candidate.json` を `_read_regular` で読んだバージョン。`stamp`（ディレクトリを調べた時の状態）と同じファイルでなければ偽。"""
    try:
        read = _read_regular(made / CANDIDATE_NAME)
    except FileNotFoundError:  # 無い候補は「無し」のバージョン。調べた時にあった候補が消えていれば、同じ状態ではない
        return all(entry[0] != CANDIDATE_NAME for entry in stamp), None
    except OSError:  # 読めない候補は、その候補だけを掃除から外す（呼び手が飛ばす）
        return False, None
    return (CANDIDATE_NAME, *read.ident[1:]) in stamp, hashlib.sha256(read.data).hexdigest()


def unedited(now: Optional[str], written: Any, kept: Optional[str]) -> bool:
    """読み終えた候補のバージョン `now` が、作った時（`written`）か `kept` のバージョンか（無い候補は真）。"""
    return now is None or now in (written, kept)


def regular_version(path: Path, strict: bool = True) -> tuple[bool, Optional[str]]:
    """普通のファイルだと確かめてから読んだ中身のバージョン（`_read_regular`）。無ければ `(True, None)`、FIFO・ディレクトリなど読めなければ `(False, None)`。

    掃除が過去の反映先や候補を読むときに使う。利用者が置き換えた項目 1 つで、掃除の全体が止まらないようにするためである。
    """
    try:
        return True, hashlib.sha256(_read_regular(path, strict).data).hexdigest()
    except FileNotFoundError:  # 無い反映先は「無し」のバージョンとして照らす
        return True, None
    except OSError:  # 読めない項目は、その候補だけを掃除から外す（呼び手が飛ばす）
        return False, None


@dataclass(frozen=True)
class Read:
    """読んだ中身と、読んだファイルの識別情報と状態（`_ident`。後で同じファイルのままかを照らす）。"""

    data: bytes
    ident: tuple[int, int, int, int]


def _ident(st: os.stat_result) -> tuple[int, int, int, int]:
    """ファイルの `(st_dev, st_ino, 大きさ, 更新時刻)`。同じ大きさと更新時刻の別のファイルへの置き換えも見分ける。"""
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _read_regular(path: Path, strict: bool = True) -> Read:
    """ファイルを開いた記述子で普通のファイルと確かめてから読む。FIFO は開く段階で待たない（`O_NONBLOCK`）。

    `strict` なら、親のディレクトリとファイルのどちらもリンクをたどらない（候補のディレクトリの中の項目）。偽なら、利用者の
    worktree の反映先のように、リンクをたどって開く。無ければ `FileNotFoundError`、普通のファイルでないか読む間に変わったら
    `OSError`。
    """
    nofollow = os.O_NOFOLLOW if strict else 0
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | nofollow | os.O_CLOEXEC)
    try:
        fd = os.open(path.name, os.O_RDONLY | nofollow | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=folder)
    finally:
        os.close(folder)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "普通のファイルでない", str(path))
        data = f.read()
        after = os.fstat(f.fileno())
    if _ident(before) != _ident(after):
        raise OSError(errno.EAGAIN, "読む間に変わった", str(path))
    return Read(data, _ident(after))


def _settleable() -> bool:
    """退避した候補を消してよいかを確かめられる環境か（`/proc/<pid>/fd` がある）。無ければ候補を退避せず、元のパスに残す。"""
    return Path("/proc/self/fd").is_dir()


def set_aside(made: Path, kept: Optional[str] = None) -> bool:
    """候補の錠の中で、直していない候補を、消す前の退避（`REMOVING` の名前）へ移し、移したかを返す。中身はその場では消さない。

    名前を変えた後は、元のパスへの保存はディレクトリが無いので失敗し、エディタの中の設定案は残る。名前を変える前に保存された
    中身と、変えた後も開いたままのファイルへ書かれた中身は退避の中に残り、次の掃除（`settle_aside`）が比べて元の名前へ戻す。
    消してよいかを確かめられない環境（`_settleable` が偽）では退避しない。退避しても次の掃除で戻すだけで、`draft` のたびに
    元のパスで開けるかどうかが変わるためである。その候補は `launch_approval.cleanup_targets` が理由と共に返す。NDF が作ったファイルのほかに
    項目がある候補も退避しない（次の掃除で消さずに戻すだけで、利用者の項目を退避の名前の下へ移すことになるため）。
    """
    owned = ownership(made) if _settleable() else None
    if owned is None or not _untouched(made, owned, kept):
        return False
    aside = made.with_name(REMOVING + made.name)
    try:
        os.rename(made, aside)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 退避の前に無くなった候補は、退避するものが無い
        return False
    if kept is not None:
        write_launch_record(aside / DISCARD_NAME, {"kept": kept})
    return True


def _open_inodes(wanted: set[tuple[int, int]]) -> Optional[set[tuple[int, int]]]:
    """`wanted` のうち、いまどれかのプロセスが開いているもの（`(st_dev, st_ino)` の組）。`/proc` の無い環境では確かめられないので None。

    退避した候補をまとめて 1 回だけ走査する。走査するのは `/proc/<pid>/fd/` の項目の `stat` だけで、子のプロセスは起動しない。
    """
    if not _settleable():
        return None
    if not wanted:
        return set()
    return {key for key in map(_fd_target, Path("/proc").glob("[0-9]*/fd/*")) if key in wanted}


def _fd_target(fd: Path) -> Optional[tuple[int, int]]:
    """`/proc` の fd が指すファイルの `(st_dev, st_ino)`。調べる間に閉じた fd と、読めないほかのユーザーの fd は None。"""
    try:
        opened = fd.stat()
    except OSError:  # noqa: NDF401 ファイル: 調べる間に閉じた fd と、読めないほかのユーザーの fd は、候補を指さないものとして飛ばす
        return None
    return opened.st_dev, opened.st_ino


# 候補のディレクトリの中で NDF が作るファイル。掃除が消すのはこれだけで、ほかの項目がある候補は消さずに元の名前へ戻す
OWN_FILES = frozenset({CANDIDATE_NAME, SOURCE_NAME, DISCARD_NAME})
# NDF が作る記録の欄。中身がこの欄だけを `write_launch_record` が書く形のまま持つ記録だけを、NDF が作ったままの記録とみなす
# （`candidate.json` は中身を比べるバージョンと照らす `_untouched` が見る）
RECORD_KEYS = {SOURCE_NAME: frozenset({"target", "base", "written"}), DISCARD_NAME: frozenset({"kept"})}


def raw_record(path: Path) -> Optional[Read]:
    """記録を、親のディレクトリと記録のどちらもリンクをたどらずに開き、開いた記述子で普通のファイルと確かめてから読む（`_read_regular`）。

    無い・読めない・普通のファイルでない・読む間に変わった記録は None。FIFO は開く段階で待たない。
    """
    try:
        return _read_regular(path)
    except OSError:  # noqa: NDF401 ファイル: 無い・読めない記録は「記録が無い」と同じに扱い、所有の確かめで外す
        return None


def _ndf_record(path: Path, read: Optional[Read] = None) -> bool:
    """`path` が、NDF が書いたままの記録（普通のファイルで、`RECORD_KEYS` の欄だけを `write_launch_record` の形で持つ）か。

    利用者が同じ名前の別のファイルやディレクトリへ置き換えていれば偽で、呼び手はそれを消さない。呼び手が読み終えた中身 `read` が
    あれば、読み直さずに使う。
    """
    if read is None:
        read = raw_record(path)
    if read is None:
        return False
    try:
        obj = json.loads(read.data)
    except ValueError:  # noqa: NDF401 ファイル: 壊れた記録は、NDF が書いたままと確かめられないので消さない
        return False
    return (
        isinstance(obj, dict) and obj.keys() == RECORD_KEYS[path.name] and json.dumps(obj, ensure_ascii=False).encode("utf-8") == read.data
    )


@dataclass(frozen=True)
class _Owned:
    """候補のディレクトリが NDF の作ったものだけだと確かめた結果。`keys` は開いたままかを調べる `(st_dev, st_ino)` の組、
    `stamp` は中の項目ごとの名前・inode・大きさ・更新時刻（後で同じ状態のままかを照らす）、`records` は確かめた時に読んだ記録の中身。"""

    keys: frozenset[tuple[int, int]]
    stamp: frozenset[tuple[str, int, int, int]]
    records: tuple[tuple[str, Read], ...] = ()

    def record(self, name: str) -> dict[str, Any]:
        """所有を確かめた時に読んだ記録 `name` の中身（無ければ空）。開き直さないので、確かめた後の置き換えに左右されない。"""
        return record_object(dict(self.records).get(name))


def _entries(folder: Path) -> Optional[tuple[os.stat_result, list[tuple[str, os.stat_result]]]]:
    """候補のディレクトリと中の項目の状態（`lstat`）。NDF が作る名前の普通のファイルのほかに項目があるか、状態を読めなければ None。"""
    try:
        top = os.lstat(folder)
        with os.scandir(folder) as it:
            entries = list(it)
        if any(e.name not in OWN_FILES or not e.is_file(follow_symlinks=False) for e in entries):
            return None
        return top, [(e.name, e.stat(follow_symlinks=False)) for e in entries]
    except OSError:  # noqa: NDF401 ファイル: 状態を読めない候補は、消してよいと確かめられないので消さない（呼び手が元の名前へ戻す）
        return None


def _stamp(stats: list[tuple[str, os.stat_result]]) -> frozenset[tuple[str, int, int, int]]:
    return frozenset((name, st.st_ino, st.st_size, st.st_mtime_ns) for name, st in stats)


def ownership(folder: Path, known: Optional[Mapping[str, Optional[Read]]] = None) -> Optional[_Owned]:
    """候補のディレクトリの中が NDF の作った普通のファイルだけなら、その確かめた結果（`_Owned`）。

    リンクをたどらずに調べる（`lstat`）。ほかの項目（利用者のファイル・シンボリックリンク・ディレクトリ）があるか、
    記録（`source.json`・`discard.json`）が NDF の書いたままでないか（`_ndf_record`）、調べる途中で状態を読めなければ None を返し、
    呼び手はその候補を消さない。`known` は呼び手が読み終えた記録（読めなければ None）で、読み直さずに使う。その記録が、ここで
    調べた同じ名前の項目と識別情報・状態まで同じでなければ None を返す（読んだ後に別のファイルへ置き換わった記録の中身を、
    新しいファイルの状態と組み合わせない）。
    """
    found = _entries(folder)
    if found is None:
        return None
    top, stats = found
    known = {} if known is None else known
    present = {name for name, _ in stats}
    if any(read is not None and name not in present for name, read in known.items()):
        return None
    read_records = []
    for name, st in stats:
        if name == CANDIDATE_NAME:
            continue
        read = known[name] if name in known else raw_record(folder / name)
        if read is None or read.ident != _ident(st) or not _ndf_record(folder / name, read):
            return None
        read_records.append((name, read))
    keys = frozenset({(top.st_dev, top.st_ino)} | {(st.st_dev, st.st_ino) for _, st in stats})
    return _Owned(keys, _stamp(stats), tuple(read_records))


def _original_name(aside: Path) -> str:
    """退避（`.removing-<名前>`）か、消す前の隔離（`.settling-<乱数>.<名前>`）の、候補の元の名前。"""
    if aside.name.startswith(SETTLING):
        return aside.name.rsplit(".", 1)[1]
    return aside.name[len(REMOVING) :]


def _put_back(aside: Path) -> Optional[Path]:
    """退避か隔離の候補を元の名前へ戻し、戻したパスを返す。戻せなければ None（その候補は残し、次の掃除でやり直す）。

    `discard.json` は NDF が書いたままのときだけ消す。同じ名前の利用者の項目（ファイル・ディレクトリ）には触れない。
    """
    discard = aside / DISCARD_NAME
    back = aside.with_name(_original_name(aside))
    try:
        if _ndf_record(discard):
            discard.unlink()
        os.rename(aside, back)
    except OSError:  # noqa: NDF401 ファイル: 戻せない候補は隔離の名前のまま残し、ほかの候補と呼び手の操作を続ける
        return None
    return back


def _remove_own(aside: Path) -> None:
    """NDF が作ったファイルだけを消してから、空になったディレクトリを消す。ほかの項目が増えていれば、ディレクトリを消せずに `OSError`。

    呼び手は、記録が NDF の書いたままであることを `ownership` で確かめてから呼ぶ。
    """
    for name in OWN_FILES:
        (aside / name).unlink(missing_ok=True)
    aside.rmdir()


def start_draft(home: Path, name: str) -> Path:
    """候補の錠の中で、作成途中のディレクトリ `.draft-<name>`（0700）を作り、ディレクトリを返す。`name` は乱数の名前（`DRAFT_ID`）。

    ディレクトリを作る前に、隣の記録 `.draft-<name>.record` を一時ファイルを使わずに新しく作り、名前の行を書き終える。作った後に、
    同じ記録へディレクトリの実体の行を書き足す。中に何かを書くのは、実体の行を書き終えた後（呼び手）だけである。
    記録のための一時ファイルは作らない。どの時点で落ちても、残るのは記録と作成途中のディレクトリだけで、掃除が記録の中身で見分ける（`reclaim_drafts`）。
    """
    if not DRAFT_ID.fullmatch(name):
        raise ValueError(f"作成途中の候補の名前が乱数の形でない: {name}")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    drafting = home / f"{DRAFTING}{name}"
    record = _draft_record(drafting)
    fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_draft_head(name))
        f.flush()  # 名前の行を、ディレクトリを作る前に書き終える
        try:
            drafting.mkdir(mode=0o700)
        except BaseException:
            record.unlink(missing_ok=True)
            raise
        st = drafting.lstat()
        f.write(json.dumps({"dev": st.st_dev, "ino": st.st_ino}) + "\n")
    return drafting


def finish_draft(drafting: Path, made: Path) -> None:
    """作成途中のディレクトリを候補の名前へ付け替え、作成途中の記録を消す。"""
    os.rename(drafting, made)
    _draft_record(drafting).unlink(missing_ok=True)


def _draft_record(drafting: Path) -> Path:
    return drafting.with_name(drafting.name + DRAFT_RECORD)


def _draft_head(name: str) -> str:
    """作成途中の記録の 1 行目（ディレクトリを作る前に書き終える名前の行）。"""
    return json.dumps({"id": name}, ensure_ascii=False) + "\n"


# 作成途中の記録の 2 行目（実体の行）の形。数字を 0 にそろえて、書きかけの行がこの形の頭かを比べる
DRAFT_BODY_FORM = '{"dev": 0, "ino": 0}'


def _draft_state(text: str, name: str) -> tuple[str, Optional[dict[str, int]]]:
    """作成途中の記録の中身から、作成がどこまで進んだか。

    `torn`（空か、名前の行の途中。ディレクトリを作る前に落ちた）、`pending`（名前の行の後で、実体の行を書き終える前。ディレクトリは
    作ったか、作る前で、作っていれば空のまま）、`made`（実体の行まで書いた。実体を返す）、`foreign`（NDF の記録の形でない）のどれか。
    """
    head = _draft_head(name)
    if head.startswith(text) and text != head:
        return "torn", None
    if not text.startswith(head):
        return "foreign", None
    body = text[len(head) :]
    if not body.endswith("\n"):
        return ("pending", None) if "\n" not in body and DRAFT_BODY_FORM.startswith(re.sub(r"[0-9]+", "0", body)) else ("foreign", None)
    try:
        obj = json.loads(body)
    except ValueError:  # ファイル: 壊れた実体の行は、NDF の記録の形でない
        return "foreign", None
    if not isinstance(obj, dict) or set(obj) != {"dev", "ino"} or not all(type(v) is int for v in obj.values()):
        return "foreign", None
    return ("made", obj) if json.dumps(obj) + "\n" == body else ("foreign", None)


def reclaim_drafts(home: Path) -> None:
    """候補の錠の中で、中断した作成の残りを片付ける。NDF が作ったと記録で確かめられたものだけを消す。

    記録の名前は NDF が作る乱数（`DRAFT_ID`）なので、その形の名前で記録の形の中身を持つ記録は NDF が書いたものである。
    ディレクトリは、記録の実体（`dev`・`ino`）と合うときだけ NDF が作ったものとして中の NDF のファイルを消す。実体の行を書く前に
    落ちた場合は実体と照らせないが、NDF はその時点でディレクトリの中に何も書いていない。そのため、空のディレクトリだけを消し、
    中身のあるディレクトリは NDF が作ったものでないとして残す。記録の無い作成途中のディレクトリと、乱数の形でない名前のものは、
    空でも消さない（利用者が作ったものかもしれないため）。

    | 作成途中の記録の中身 | ディレクトリ `.draft-<名前>` | 消すもの |
    | --- | --- | --- |
    | 空か、名前の行の途中まで（`torn`） | — | 記録だけ（ディレクトリを作る前に落ちた） |
    | 名前の行の後、実体の行を書き終える前（`pending`） | 空 | ディレクトリと記録 |
    | 名前の行の後、実体の行を書き終える前（`pending`） | 中身があるか、無いか、ディレクトリでない | 記録だけ |
    | 実体の行まで（`made`）で、実体が合う | 中が NDF の書くもの（`DRAFT_FILES` と書く途中の一時ファイル）だけ | 中身、ディレクトリ、記録 |
    | 実体の行まで（`made`）で、実体が合う | ほかの項目がある | NDF の書くものだけ（ディレクトリと記録は残す） |
    | 実体の行まで（`made`）で、実体が違うか無い | — | 記録だけ（ディレクトリは NDF が作ったものでない） |
    | ほかの形（`foreign`）か、読めない | — | 何も消さない |
    """
    for record in home.glob(f"{DRAFTING}*{DRAFT_RECORD}"):
        drafting = record.with_name(record.name.removesuffix(DRAFT_RECORD))
        name = drafting.name.removeprefix(DRAFTING)
        if not DRAFT_ID.fullmatch(name):
            continue
        read = raw_record(record)
        try:
            text = read.data.decode("utf-8") if read is not None else None
        except UnicodeDecodeError:  # ファイル: 読めない記録は NDF の記録と確かめられないので消さない
            text = None
        if text is None:
            continue
        state, obj = _draft_state(text, name)
        if state == "foreign":
            continue
        if state == "pending" and _is_dir(drafting):
            with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 中身のあるディレクトリは NDF が作ったものでないので残す
                drafting.rmdir()
        if state == "made" and obj is not None and _same_dir(drafting, obj):
            _remove_draft_files(drafting)
            if _same_dir(drafting, obj):
                continue  # 利用者の項目が残っているか、消し切れなかった。ディレクトリと記録を残す
        record.unlink(missing_ok=True)


def _is_dir(where: Path) -> bool:
    """`where` がディレクトリか。リンクはたどらない。"""
    try:
        return stat.S_ISDIR(os.lstat(where).st_mode)
    except OSError:  # noqa: NDF401 ファイル: 無いディレクトリは、片付けるものが無い
        return False


def _same_dir(where: Path, obj: Mapping[str, Any]) -> bool:
    """`where` が記録の実体（`dev`・`ino`）と同じディレクトリか。リンクは同じと見なさない。"""
    try:
        st = os.lstat(where)
    except OSError:  # noqa: NDF401 ファイル: 無いディレクトリは、記録の実体と同じでない
        return False
    return stat.S_ISDIR(st.st_mode) and (st.st_dev, st.st_ino) == (obj["dev"], obj["ino"])


def _remove_draft_files(drafting: Path) -> None:
    """作成途中のディレクトリの中の、NDF が作る普通のファイルだけを消し、空になればディレクトリを消す。ほかの項目には触れない。"""
    with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 消せなかった残りは、ディレクトリごと次の掃除に任せる
        for entry in _draft_entries(drafting):
            if _is_draft_file(entry):
                os.unlink(entry.path)
        drafting.rmdir()


def _draft_entries(where: Path) -> list[os.DirEntry[str]]:
    """`where` の中の項目。読めなければ `OSError` を呼び手へ上げる。"""
    with os.scandir(where) as it:
        return list(it)


def _is_draft_file(entry: os.DirEntry[str]) -> bool:
    """NDF が作成途中のディレクトリに書く普通のファイル（`DRAFT_FILES` と書く途中の一時ファイル）か。リンクはたどらない。"""
    return bool(entry.name in DRAFT_FILES or DRAFT_TEMP.fullmatch(entry.name)) and entry.is_file(follow_symlinks=False)


def settle_aside(home: Path) -> set[str]:
    """候補の錠の中で、作業中のディレクトリを片付け、元の名前へ戻した候補の名前を返す。

    作成途中は中断の残りなので、記録と実体の合う NDF の作ったものだけを消す（`reclaim_drafts`）。前の掃除が退避した候補は、消してよいと確かめられたものだけを消し、ほかは元の名前へ戻す。
    戻した候補は、呼び手がこの掃除では退避し直さない（利用者が元のパスへ保存できるようにし、判定は次の掃除でやり直す）。

    消す前に、退避した候補を乱数を含む名前（`SETTLING`）へ移してから、開いたままのプロセスを `/proc` で 1 回だけ調べ、
    その後に中身を比べる。名前を変えた後は、退避前のパスでは候補を開けない。開いたままのプロセスがある候補、`/proc` が無く
    確かめられない環境の候補、NDF が作ったファイルのほかに項目がある候補、状態を読めない候補は消さない。消すのは、作った時の
    中身（NDF が作った写し）か反映した中身のままの候補の、NDF が作ったファイルだけである。1 つの候補で失敗しても、ほかの候補の
    片付けと呼び手の操作は続ける。
    """
    reclaim_drafts(home)
    settling = [_isolate(left) for left in sorted([*home.glob(REMOVING + "*"), *home.glob(SETTLING + "*")])]
    if not settling:
        return set()
    owned = {aside: ownership(aside) for aside in settling}
    held = _open_inodes(set().union(*(o.keys for o in owned.values() if o is not None)))
    restored = set()
    for aside in settling:
        own = owned[aside]
        removable = held is not None and own is not None and not own.keys & held
        if not (removable and _untouched(aside, own, own.record(DISCARD_NAME).get("kept")) and _removed(aside)):
            back = _put_back(aside)
            if back is not None:
                restored.add(back.name)
    return restored


def _removed(aside: Path) -> bool:
    """`_remove_own` で消せたか。消す途中で項目が増えたなどで消せなければ偽を返し、呼び手が残りを元の名前へ戻す。"""
    try:
        _remove_own(aside)
    except OSError:  # noqa: NDF401 ファイル: 消せなかった候補は、残った中身ごと元の名前へ戻す（呼び手が戻す）
        return False
    return True


def _isolate(aside: Path) -> Path:
    """退避した候補を、消す前の隔離の名前（`.settling-<乱数>.<元の名前>`）へ移す。"""
    isolated = aside.with_name(f"{SETTLING}{uuid.uuid4().hex}.{_original_name(aside)}")
    os.rename(aside, isolated)
    return isolated


def restore_aside(candidate: Path) -> None:
    """候補の錠の中で、退避か隔離の途中で止まった候補を元の名前へ戻す（同じ候補でリクエストを作り直したとき）。"""
    home, name = candidate.parents[1], candidate.parent.name
    if candidate.parent.exists():
        return
    for aside in [home / f"{REMOVING}{name}", *home.glob(f"{SETTLING}*.{name}")]:
        if aside.exists() and _put_back(aside) is not None:
            return


def record_object(raw: Optional[Read]) -> dict[str, Any]:
    """読んだ記録（`raw_record`）の JSON のオブジェクト。無い・壊れていれば空（記録が無いと同じ）。"""
    try:
        obj = json.loads(raw.data) if raw is not None else {}
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def same_stamp(made: Path, stamp: frozenset[tuple[str, int, int, int]]) -> bool:
    """候補のディレクトリの項目と各項目の inode・大きさ・更新時刻が、判定の時（`stamp`）と同じか。"""
    found = _entries(made)
    return found is not None and _stamp(found[1]) == stamp
