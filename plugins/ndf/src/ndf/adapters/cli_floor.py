"""外部 CLI（gh・claude・codex）のバージョンを下限の表（`contracts/domains/cli-floors.json`）と比べる（#3691 の決定 11。I12）。

- 起動の箇所は、既存の前提（学習の設定・重い起動のスロット）を通った後、起動の前に `require` を呼ぶ。古ければ `TooOld` が上がり、
  その箇所の今の「起動できない」の形で返す
- 比べるのは実際に起動する実行ファイルで、起動と同じ `PATH` と作業ディレクトリで解いた実パスである
- `--version` は実パス・`st_mtime_ns`・`st_size` の組ごとにプロセスの中で 1 回だけ打ち、結果を再利用する
- バージョンを読めない（出力の形が違う・起動できない・期限切れ）ときは標準エラーへ 1 行を出して起動を通す。
  CLI が無い・壊れているときの扱いは、今の起動の箇所が持つ
- 表に無い名前（kiro・agy）は何も打たない
- 行の `flag_floors`（引数の名前から下限）の引数を起動の引数 `args` が含めば、その下限と比べる。このときはバージョンを確かめられなくても
  起動を通さず `Unverified` を上げる（`--restricted` の読み取りの境界を頼れないバージョンで起動しない。#3955）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Mapping, Optional, Sequence

import semver

from ndf import process_group
from ndf.adapters.run_log import diag
from ndf.contracts import facts, limits

TABLE = "cli-floors"
# `--version` を待つ上限の方針（contracts/limits/）。過ぎたら読めないとして起動を通す
VERSION_POLICY = "cli-floor.version"
# (実パス, st_mtime_ns, st_size) → 読めたバージョン（読めなければ None）
_SEEN: dict[tuple[str, int, int], Optional[str]] = {}
_LOCK = threading.Lock()


class TooOld(Exception):
    """外部 CLI が下限より古い。`name`・`path`（実パス）・`found`（見つけたバージョン）・`floor`（下限）を持つ。
    `public` は実パスを除いた文面（PR のコメントなど公開される場所へ載せる。PR 4461 の r1-1）。"""

    def __init__(self, name: str, path: str, found: str, floor: str) -> None:
        tail = f"は下限 {floor} より古い。{floor} 以上へ上げる"
        self._fill(f"{name} {found}（{path}）{tail}", f"{name} {found} {tail}", name, path, found, floor)

    def _fill(self, message: str, public: str, name: str, path: str, found: str, floor: str) -> None:
        """例外の文面・公開用の文面と 4 属性をまとめて設定する（`TooOld` と派生の初期化が共有する）。"""
        Exception.__init__(self, message)
        self.public = public
        self.name, self.path, self.found, self.floor = name, path, found, floor


class Unverified(TooOld):
    """引数ごとの下限（`flag_floors`）を当てる起動で、外部 CLI のバージョンを確かめられない（見つからない・`--version` を読めない）。"""

    def __init__(self, name: str, path: Optional[str], floor: str) -> None:
        missing = "実行ファイルが見つからない"
        where = path if path is not None else missing
        tail = f"。下限 {floor} 以上かを確かめられないので起動しない"
        public = f"{name} のバージョンを確かめられない" + (f"（{missing}）" if path is None else "") + tail
        self._fill(f"{name} のバージョンを確かめられない（{where}）{tail}", public, name, where, "", floor)


def locate(argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """起動と同じ条件で `argv0` が指す実行ファイルの絶対パス（リンクはたどらない）。見つからなければ None。

    `PATH` は `env` の値（無ければ今のプロセスの値）。相対のパスと `PATH` の相対の要素は、起動と同じく `cwd` から解く。
    """
    base = str(cwd) if cwd is not None else os.getcwd()
    if os.sep in argv0:
        return shutil.which(os.path.join(base, argv0))
    raw = env["PATH"] if env is not None and "PATH" in env else os.environ.get("PATH", os.defpath)
    dirs = [d if os.path.isabs(d) else os.path.join(base, d) for d in raw.split(os.pathsep)]
    return shutil.which(argv0, path=os.pathsep.join(dirs))


def resolve(argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """`locate` の実パス（リンクをたどった先）。見つからなければ None。"""
    found = locate(argv0, env, cwd)
    return os.path.realpath(found) if found is not None else None


def _read_version(path: str, row: Mapping[str, object], env: Optional[Mapping[str, str]], cwd: Optional[Path]) -> Optional[str]:
    """`path` のバージョンを出す引数で 1 回打って読む。読めなければ None。期限を過ぎたら組ごと止める。"""
    try:
        proc = subprocess.Popen(
            [path, *row["version_args"]],  # type: ignore[misc]
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError:  # noqa: NDF401 外部コマンド: 起動できなければ読めないとして通し、起動の失敗は今の起動の箇所が扱う
        return None
    try:
        out, _ = proc.communicate(timeout=limits.seconds(VERSION_POLICY))
    except subprocess.TimeoutExpired:
        process_group.stop_group(proc)
        if proc.stdout is not None:
            proc.stdout.close()
        return None
    return _parse_version(out, str(row["version_pattern"]))


def _parse_version(out: str, pattern: str) -> Optional[str]:
    """`--version` の出力 `out` から `pattern` の最初のキャプチャを取り、semver として正しければ返す。違えば None。"""
    m = re.search(pattern, out)
    if m is None:
        return None
    found = m.group(1)
    return found if semver.Version.is_valid(found) else None


def _version(name: str, path: str, row: Mapping[str, object], env: Optional[Mapping[str, str]], cwd: Optional[Path]) -> Optional[str]:
    """実行ファイルのバージョン（同じ実パス・更新時刻・大きさなら再利用）。初めて読めなかったときに 1 行を出す。"""
    try:
        st = os.stat(path)
    except OSError:  # noqa: NDF401 外部コマンド: 消えた実行ファイルは比べずに通し、起動の失敗は今の起動の箇所が扱う
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _SEEN:
            return _SEEN[key]
        version = _read_version(path, row, env, cwd)
        _SEEN[key] = version
        if version is None:
            diag(f"{name} のバージョンを読めない（{path}）。下限 {row['min_version']} と比べずに起動する", kind="cli_floor", cli=name)
        return version


def refuse_old(name: str, argv0: str, error: type[Exception], env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> None:
    """`require` を打ち、古ければ `TooOld` の代わりに `error(<理由>)` を上げる（起動の箇所の「起動できない」の例外へ直す）。"""
    try:
        require(name, argv0, env, cwd)
    except TooOld as exc:
        raise error(str(exc)) from None


def _flag_floor(row: Mapping[str, object], args: Sequence[str]) -> Optional[str]:
    """起動の引数 `args` が含む、行の `flag_floors` の引数の下限のうち最も高いもの。含まなければ None。"""
    floors = row.get("flag_floors")
    found = [str(v) for k, v in floors.items() if k in args] if isinstance(floors, Mapping) else []
    return max(found, key=semver.Version.parse) if found else None


def require(name: str, argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None, args: Sequence[str] = ()) -> None:
    """`argv0` で起動する外部 CLI `name` が下限より古ければ `TooOld`。表に無い名前と、見つからない・読めない CLI は通す。
    起動の引数 `args` が行の `flag_floors` の引数を含めば、その下限と比べ、バージョンを確かめられなければ `Unverified`（#3955）。"""
    if name not in facts.ids(TABLE):
        return
    row = facts.row(TABLE, name)
    strict = _flag_floor(row, args)
    floor = strict if strict is not None else str(row["min_version"])
    path = resolve(argv0, env, cwd)
    found = _version(name, path, row, env, cwd) if path is not None else None
    if found is None:
        if strict is not None:
            raise Unverified(name, path, strict)
        return
    if semver.Version.parse(found) < semver.Version.parse(floor):
        raise TooOld(name, str(path), found, floor)


def version(name: str, argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """`argv0` で起動する外部 CLI `name` のバージョン。`require` と同じ記録（同じ実パス・更新時刻・大きさ）を使い、記録が無ければ
    `--version` を 1 回打って記録する（`require` より先に呼ばれても読めないとしない）。表に無い名前・見つからない・読めないなら None。
    """
    return version_at(name, resolve(argv0, env, cwd), env, cwd) if name in facts.ids(TABLE) else None


def record_key(path: Optional[str]) -> Optional[tuple[tuple[str, int, int], bool]]:
    """実行ファイルのバージョンの記録のキー（実パス・更新時刻・大きさ）と、そのキーが記録に在るか。`--version` を打たない。無ければ None（#4068）。"""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:  # noqa: NDF401 ファイル: 消えた実行ファイルには記録のキーが無い
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    with _LOCK:
        return key, key in _SEEN


def version_at(name: str, path: Optional[str], env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """解決済みの実パス `path` の外部 CLI `name` のバージョン（`version` と同じ記録を使う。`PATH` を探し直さない）。"""
    if name not in facts.ids(TABLE) or path is None:
        return None
    return _version(name, path, facts.row(TABLE, name), env, cwd)
