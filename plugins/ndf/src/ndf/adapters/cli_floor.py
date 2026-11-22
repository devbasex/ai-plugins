"""外部 CLI（gh・claude・codex）のバージョンを下限の表（`contracts/domains/cli-floors.json`）と比べる（#3691 の決定 11。I12）。

- 起動の箇所は、既存の前提（学習の設定・重い起動のスロット）を通った後、起動の前に `require` を呼ぶ。古ければ `TooOld` が上がり、
  その箇所の今の「起動できない」の形で返す
- 比べるのは実際に起動する実行ファイルで、起動と同じ `PATH` と作業ディレクトリで解いた実パスである
- `--version` は実パス・`st_mtime_ns`・`st_size` の組ごとにプロセスの中で 1 回だけ打ち、結果を再利用する
- バージョンを読めない（出力の形が違う・起動できない・期限切れ）ときは標準エラーへ 1 行を出して起動を通す。
  CLI が無い・壊れているときの扱いは、今の起動の箇所が持つ
- 表に無い名前（kiro・agy）は何も打たない
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Mapping, Optional

import semver

from ndf import process_group
from ndf.adapters.run_log import diag
from ndf.contracts import facts

TABLE = "cli-floors"
# `--version` を待つ上限（秒）。過ぎたら読めないとして起動を通す
VERSION_SECONDS = 5.0
# (実パス, st_mtime_ns, st_size) → 読めたバージョン（読めなければ None）
_SEEN: dict[tuple[str, int, int], Optional[str]] = {}
_LOCK = threading.Lock()


class TooOld(Exception):
    """外部 CLI が下限より古い。`name`・`path`（実パス）・`found`（見つけたバージョン）・`floor`（下限）を持つ。"""

    def __init__(self, name: str, path: str, found: str, floor: str) -> None:
        super().__init__(f"{name} {found}（{path}）は下限 {floor} より古い。{floor} 以上へ上げる")
        self.name, self.path, self.found, self.floor = name, path, found, floor


def resolve(argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """起動と同じ条件で `argv0` が指す実行ファイルの実パス。見つからなければ None。

    `PATH` は `env` の値（無ければ今のプロセスの値）。相対のパスと `PATH` の相対の要素は、起動と同じく `cwd` から解く。
    """
    base = str(cwd) if cwd is not None else os.getcwd()
    if os.sep in argv0:
        found = shutil.which(os.path.join(base, argv0))
    else:
        raw = env["PATH"] if env is not None and "PATH" in env else os.environ.get("PATH", os.defpath)
        dirs = [d if os.path.isabs(d) else os.path.join(base, d) for d in raw.split(os.pathsep)]
        found = shutil.which(argv0, path=os.pathsep.join(dirs))
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
        out, _ = proc.communicate(timeout=VERSION_SECONDS)
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


def require(name: str, argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> None:
    """`argv0` で起動する外部 CLI `name` が下限より古ければ `TooOld`。表に無い名前と、見つからない・読めない CLI は通す。"""
    if name not in facts.ids(TABLE):
        return
    path = resolve(argv0, env, cwd)
    if path is None:
        return
    row = facts.row(TABLE, name)
    found = _version(name, path, row, env, cwd)
    floor = str(row["min_version"])
    if found is not None and semver.Version.parse(found) < semver.Version.parse(floor):
        raise TooOld(name, path, found, floor)


def seen(argv0: str, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> Optional[str]:
    """`require` が記録た `argv0` のバージョン（同じ実パス・更新時刻・大きさのとき）。記録が無い・読めなかったなら None。

    `--version` を打たない（起動の前に `require` が打った 1 回の結果だけを読む）。
    """
    path = resolve(argv0, env, cwd)
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:  # noqa: NDF401 外部コマンド: 消えた実行ファイルのバージョンは分からない
        return None
    with _LOCK:
        return _SEEN.get((path, st.st_mtime_ns, st.st_size))
