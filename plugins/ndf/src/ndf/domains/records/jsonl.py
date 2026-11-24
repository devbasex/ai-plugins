"""記録の JSONL を読む・1 行足す（使用量ログ・MVV の記録・文脈の切り替えの判定の記録が使う）。

記録は追記だけで持つ。どの行も読み書きの核（`ndf.contracts.internal`）を種類つきで通す。読むときは、書きかけ・辞書でない・形の
違う行を、行番号と理由つきで除いて止まらない（呼び手が結果に出す）。新しいバージョンの行と知らない種類は止める。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

from filelock import FileLock

from ndf.contracts import internal, limits


def now() -> str:
    """記録の行の時刻（UTC・秒まで・ISO 8601。行の `at`）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Broken(NamedTuple):
    """読めずに除いた行: 行番号（1 から）・理由・それより前にある読めた行の数・辞書として読めた中身（読めなければ None）。"""

    line: int
    reason: str
    before: int
    raw: Optional[dict[str, Any]]


def read_rows(path: Path, *, kind: str) -> tuple[list[dict[str, Any]], list[Broken]]:
    """種類 `kind` の行（今のバージョンの形）と、読めずに除いた行。ファイルが無ければ空。新しいバージョンの行があれば `InternalError`。"""
    if not path.is_file():
        return [], []
    return _read_text(path.read_text(encoding="utf-8"), kind, 1)


def read_checked(path: Path, *, kind: str, wanted: Callable[[Mapping[str, Any]], bool]) -> tuple[list[dict[str, Any]], list[Broken]]:
    """`wanted` が真の行と、ファイル全体の除いた行（錠は呼び手が持つ）。形は、前の呼び出しより後に足された行だけ確かめる。

    確かめ終えた位置は `<ファイル>.checked` に置く。位置より前の中身か種類の形が変われば、初めから確かめ直す。新しいバージョンの行が
    あれば位置を進めずに `InternalError`。位置を書けなくても読んだ結果を返す（次の呼び出しが同じ所から確かめ直す）。
    """
    if not path.is_file():
        return [], []
    data, mark, shape = path.read_bytes(), Path(f"{path}.checked"), internal.fingerprint(kind)
    size, broken = _load_mark(mark, data, shape)
    rows = _known_rows(data[:size], kind, wanted, {b.line for b in broken})
    added, bad = _read_text(data[size:].decode("utf-8"), kind, data.count(b"\n", 0, size) + 1)
    # 書きかけの最後の行は次の追記で変わるので、改行で終わる所までを確かめ終えた位置にする
    end = data.rfind(b"\n") + 1
    if end > size:
        whole = data.count(b"\n")
        _save_mark(mark, data[:end], shape, [*broken, *(b for b in bad if b.line <= whole)])
    return rows + [r for r in added if wanted(r)], broken + bad


def _digest(data: bytes, shape: str, broken: list[Broken]) -> str:
    """確かめ終えた中身・種類の形・位置・除いた行をまとめて見分ける値（どれかが変われば別の値になる）。"""
    head = json.dumps([shape, len(data), [[b.line, b.reason] for b in broken]])
    return hashlib.sha256(head.encode("utf-8") + data).hexdigest()


def _save_mark(mark: Path, data: bytes, shape: str, broken: list[Broken]) -> None:
    """確かめ終えた位置を書く。新しく作った一時ファイルを付け替えるので、`mark` がシンボリックリンクでもリンク先を変えない。"""
    body = {"size": len(data), "digest": _digest(data, shape, broken), "broken": [[b.line, b.reason] for b in broken]}
    try:
        fd, name = tempfile.mkstemp(prefix=f".{mark.name}.", suffix=".tmp", dir=mark.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(body, ensure_ascii=False))
            os.replace(name, mark)
        except OSError:
            os.unlink(name)
            raise
    except OSError as exc:  # 位置は作り直せるので、書けなくても記録の読み書きを止めない
        print(f"記録を確かめ終えた位置を書けない: {exc}", file=sys.stderr)


def _load_mark(mark: Path, data: bytes, shape: str) -> tuple[int, list[Broken]]:
    """確かめ終えた位置と、そこまでの除いた行。無い・読めない・今の中身と合わないなら初めから（0）。"""
    try:
        held = json.loads(mark.read_text(encoding="utf-8"))
        size, digest = int(held["size"]), held["digest"]
        broken = [Broken(int(line), str(reason), 0, None) for line, reason in held["broken"]]
    except (OSError, ValueError, TypeError, KeyError):  # 位置は作り直せるので、読めなければ初めから確かめる
        return 0, []
    return (size, broken) if digest == _digest(data[:size], shape, broken) else (0, [])


def _known_rows(data: bytes, kind: str, wanted: Callable[[Mapping[str, Any]], bool], skip: set[int]) -> list[dict[str, Any]]:
    """形を確かめ終えた行のうち `wanted` が真のもの（今のバージョンの形へ移すだけで、形は確かめない）。"""
    rows = []
    for number, line in enumerate(data.decode("utf-8").split("\n"), 1):
        if line.strip() and number not in skip:
            raw = json.loads(line)
            if wanted(raw):
                rows.append(internal.migrate(kind, raw))
    return rows


def _read_text(text: str, kind: str, first: int) -> tuple[list[dict[str, Any]], list[Broken]]:
    rows: list[dict[str, Any]] = []
    broken: list[Broken] = []
    for number, line in enumerate(text.split("\n"), first):
        if not line.strip():
            continue
        raw: Any = None
        try:
            raw = _object(line)
            rows.append(internal.read_record(kind, raw))
        except internal.InternalError as exc:
            if exc.kind != "invalid":
                raise
            broken.append(Broken(number, str(exc), len(rows), raw))
    return rows, broken


def _object(line: str) -> dict[str, Any]:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise internal.InternalError("invalid", f"JSON として読めない（{exc.msg}）") from exc
    if not isinstance(raw, dict):
        raise internal.InternalError("invalid", "辞書でない")
    return raw


def broken_items(path: Path, broken: list[Broken]) -> list[dict[str, Any]]:
    """除いた行を結果 JSON の `items` に出す警告（除いた行が無ければ空）。"""
    return [{"kind": "broken_rows", "path": str(path), "lines": [b.line for b in broken]}] if broken else []


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
    """種類 `kind` の 1 行を核で確かめてから足し、書いた行を返す（錠は呼び手が持つ）。形が違えば書かずに `InternalError`。"""
    out = internal.write_record(kind, row)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return out


def append_locked(path: Path, row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """錠を取って 1 行足し、書いた行を返す。"""
    with lock(path):
        return append(path, row, kind=kind)
