"""記録の JSONL を読む・1 行足す（使用量ログ・MVV の記録・文脈の切り替えの判定の記録が使う）。

記録は追記だけで持つ。どの行も読み書きの核（`ndf.contracts.internal`）を種類つきで通す。読むときは、書きかけ・辞書でない・形の
違う行を、行番号と理由つきで除いて止まらない（呼び手が結果に出す）。新しいバージョンの行と知らない種類は止める。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

from filelock import FileLock

from ndf.contracts import internal, limits


# `append` が書く行の始まり。`internal.write_record` が `version` を先頭に置き、`json.dumps` がその順で書く
ROW_START = b'{"version": '


def now() -> str:
    """記録の行の時刻（UTC・秒まで・ISO 8601。行の `at`）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_exact() -> str:
    """順序を同じ秒の中でも見分ける時刻（UTC・マイクロ秒まで・ISO 8601）。記録の行の `at` にも使える形である。"""
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Broken(NamedTuple):
    """読めずに除いた行: 行番号（1 から）・理由・それより前にある読めた行の数・辞書として読めた中身（読めなければ None）。"""

    line: int
    reason: str
    before: int
    raw: Optional[dict[str, Any]]


def read_rows(
    path: Path, *, kind: str, wanted: Callable[[Mapping[str, Any]], bool] = lambda row: True
) -> tuple[list[dict[str, Any]], list[Broken]]:
    """種類 `kind` の `wanted` が真の行（今のバージョンの形）と、読めずに除いた行。ファイルが無ければ空。新しいバージョンの行があれば `InternalError`。

    `wanted` が偽の行はバージョンだけを見て、移さず、形も確かめない（錠の中で一部の行だけを使う読み手が、全部の行の移行と検証を払わない）。
    """
    if not path.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    broken: list[Broken] = []
    for number, chunk in enumerate(path.read_bytes().split(b"\n"), 1):
        if not chunk.strip():
            continue
        raws, cut = _raw(chunk)
        if cut is not None:  # 途中で切れた書き込みの断片。断片だけを除き、前後につながった行は読む
            broken.append(Broken(number, cut, len(rows), None))
        for raw in raws:
            try:
                if wanted(raw):
                    rows.append(internal.read_record(kind, raw))
                else:
                    internal.readable_version(kind, raw)
            except internal.InternalError as exc:
                if exc.kind != "invalid":
                    raise
                broken.append(Broken(number, str(exc), len(rows), raw))
    return rows, broken


def read_rows_after(
    path: Path, start: int, *, kind: str, wanted: Callable[[Mapping[str, Any]], bool] = lambda row: True
) -> Optional[tuple[list[dict[str, Any]], int, int]]:
    """バイト位置 `start` より後に足された、改行で終わる行のうち `wanted` が真の行と、読めなかった行の数と、読み終えた位置。

    錠を取らない（追記の途中の、改行で終わらない行は読まない）。ファイルが無いか `start` より短ければ（置き換えられた）None。
    """
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            if f.tell() < start:
                return None
            f.seek(start)
            data = f.read()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 記録がまだ無ければ足された行も無い（置き換えられたときと同じく全体を読む）
        return None
    end = data.rfind(b"\n") + 1
    rows: list[dict[str, Any]] = []
    broken = 0
    for chunk in data[:end].split(b"\n"):
        if not chunk.strip():
            continue
        raws, cut = _raw(chunk)
        broken += cut is not None
        for raw in raws:
            try:
                if wanted(raw):
                    rows.append(internal.read_record(kind, raw))
            except internal.InternalError as exc:
                if exc.kind != "invalid":
                    raise
                broken += 1
    return rows, broken, start + end


def _raw(chunk: bytes) -> tuple[list[dict[str, Any]], Optional[str]]:
    """1 行のバイト列を辞書の並びにし、読めなかった断片の理由を添えて返す。

    行全体が 1 つの辞書として読めれば（[辞書], None）。読めなければ、`append` の書いた行の始まり（`ROW_START`）を
    境界の候補として前から順に探し、境界から行末までが 1 つの辞書として読める最初の境界で分ける。境界より前も同じ手順で
    読む。前の書き込みが途中で終わると、改行の無い断片の後ろに次の書き手の行がつながる。前の書き込みが閉じ括弧まで
    書けて末尾の改行だけが欠けると、完全な辞書が 2 つつながる（`append` は末尾の改行を確かめるが、確かめてから書くまでに
    別の書き手の書き込みが終わることがある）。どちらの場合も、境界の前後で完全に読めた辞書はすべて返す。
    行の始まりだけで分けるので、断片の中の入れ子の辞書を行として拾わない。返すのは（読めた辞書の並び, 断片の理由）で、
    断片が無ければ理由は None、どこからも読めなければ（[], 理由）。行全体が読めた行では、境界を探さず読み直さない。
    """
    whole = _parsed(chunk)
    if isinstance(whole, dict):
        return [whole], None
    start = chunk.find(ROW_START, 1)
    while start != -1:
        tail = _parsed(chunk[start:])
        if isinstance(tail, dict):
            head, cut = _raw(chunk[:start])
            return [*head, tail], cut
        start = chunk.find(ROW_START, start + 1)
    return [], whole


def _parsed(chunk: bytes) -> dict[str, Any] | str:
    """バイト列を UTF-8 の 1 つの辞書として読む。読めなければ、その理由の文字列。"""
    try:
        return internal.parse_line(chunk.decode("utf-8"))
    except UnicodeDecodeError:
        return "UTF-8 として読めない"
    except internal.InternalError as exc:
        return str(exc)


def broken_items(path: Path, broken: list[Broken]) -> list[dict[str, Any]]:
    """除いた行を結果 JSON の `items` に出す警告（除いた行が無ければ空）。"""
    return [{"kind": "broken_rows", "path": str(path), "lines": [b.line for b in broken]}] if broken else []


def read_rows_warned(
    path: Path, *, kind: str, warnings: list[dict[str, Any]], wanted: Callable[[Mapping[str, Any]], bool] = lambda row: True
) -> tuple[list[dict[str, Any]], list[Broken]]:
    """`read_rows` で読み、除いた行があれば `broken_items` の警告を `warnings` へ足す。"""
    rows, broken = read_rows(path, kind=kind, wanted=wanted)
    warnings += broken_items(path, broken)
    return rows, broken


def parse_lines(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """JSONL の行を読む: 辞書の行と、読めなかった行（壊れた JSON・辞書でない）の数。空の行は数えない。"""
    rows: list[dict[str, Any]] = []
    broken = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            broken += 1
    return rows, broken


def lock(path: Path) -> FileLock:
    """記録のファイルの錠（`<ファイル>.lock`）。待ちの上限は `locks.append`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock", timeout=limits.seconds("locks.append"))


def append(path: Path, row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """種類 `kind` の 1 行を核で確かめてから足し、書いた行を返す。形が違えば書かずに `InternalError`。錠は取らない。

    錠が要るかは呼び手が決める（既存の行を読んでから書く呼び手は `lock` の中で打つか `append_locked` を使う）。行全体を
    追記の形（`O_APPEND`）の 1 回の書き込みで足すので、錠を取らない書き手どうしでも行は混ざらない。前の書き込みが
    途中で終わってファイルが改行で終わっていなければ、改行を先に足して断片を 1 行に閉じ込める。確かめてから書くまでに
    別の書き手の書き込みが途中で終われば、この行は断片の後ろにつながる。その場合も読み手（`read_rows`・`read_rows_after`）が
    行の始まり（`ROW_START`）で分けて読み直し、断片だけを読めない行として除く。別の書き手の行が末尾の改行だけを欠いて
    つながった場合は、両方の行を読む。一部しか書けなければ `OSError`。
    """
    out = internal.write_record(kind, row)
    data = (json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data
        written = os.write(fd, data)
    finally:
        os.close(fd)
    if written != len(data):
        raise OSError(f"{path} へ行の一部しか書けなかった（{written}/{len(data)} バイト）")
    return out


def append_locked(path: Path, row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """錠を取って 1 行足し、書いた行を返す。"""
    with lock(path):
        return append(path, row, kind=kind)
