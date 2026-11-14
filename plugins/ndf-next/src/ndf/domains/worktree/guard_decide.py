"""メインディレクトリを編集しようとしたときの案内の判断（純粋な関数。ファイルも git も触らない）。

書き込み先のパスを受け、メインディレクトリの下で許可パスの外のものだけを案内の対象にする。worktree の中で打った
書き込み・メインディレクトリの外の書き込みは対象にしない。**案内は止めない。** 意図した操作ならそのまま続けられる。

パスは字面で畳む（`.` と `..`）。畳まずに前方一致で比べると、`<メイン>/a/../../外` が `<メイン>/` で始まって見える。
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Iterable, Optional

# 案内に並べるパスの上限（超えた分は件数へ丸める）
MAX_LISTED = 5
SUMMARY = "メインディレクトリを編集しています。開発は {wdir}/<ブランチ名> の worktree で行ってください"
CONTEXT = """この編集先は、リポジトリを clone したメインディレクトリです。

{paths}

開発の変更は worktree の中で行います。まだ用意していなければ `ndf worktree create <ブランチ名>` で
{wdir}/<ブランチ名> に作り、そこへ移ってから編集してください。

この編集を止めてはいません。意図した操作であればそのまま続けてください。"""
UNREAD = (
    "このコマンドは構文を読み切れないため、メインディレクトリへの書き込みかを判定していません（止めてはいません）。"
    "書き込むなら、開発の変更は {wdir}/<ブランチ名> の worktree の中で行ってください。"
)


def normalize(path: str, base: str) -> str:
    """`base` を起点に絶対パスへ直し、`.` と `..` を字面で畳む。"""
    return posixpath.normpath(posixpath.join(base, path))


def relative_to(path: str, root: str) -> Optional[str]:
    """`root` からの相対パス（`root` そのものは `.`）。`root` の外なら `None`。"""
    if path == root:
        return "."
    prefix = root.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def is_allowed(rel: str, entries: Iterable[str]) -> bool:
    """末尾が `/` の項目は前方一致（ディレクトリそのものも含む）、それ以外は完全一致とその配下。"""
    for entry in entries:
        if entry.endswith("/"):
            if rel == entry[:-1] or rel.startswith(entry):
                return True
        elif rel == entry or rel.startswith(entry + "/"):
            return True
    return False


@dataclass(frozen=True)
class Place:
    """判断に要る位置。パスは実体へ解決した絶対パス。"""

    main_dir: str
    in_worktree: bool
    allow_paths: tuple[str, ...]
    worktrees_rel: str


def flagged(targets: Iterable[str], base: str, where: Place) -> list[str]:
    """書き込み先のうち案内の対象（メインディレクトリからの相対パス、重なりを除いた順）。"""
    if where.in_worktree:
        return []
    out: list[str] = []
    for target in targets:
        rel = relative_to(normalize(target, base), where.main_dir)
        if rel is None or rel == "." or rel in out:
            continue
        if is_allowed(rel, (where.worktrees_rel + "/", *where.allow_paths)):
            continue
        out.append(rel)
    return out


@dataclass(frozen=True)
class Notice:
    summary: str
    context: str


def notice(paths: list[str], worktrees_rel: str, unread: bool = False) -> Optional[Notice]:
    """案内の文。構文を読み切れなければ、その旨を先に返す。対象が無ければ `None`。"""
    summary = SUMMARY.format(wdir=worktrees_rel)
    if unread:
        return Notice(summary, UNREAD.format(wdir=worktrees_rel))
    if paths:
        listed = [f"- {p}" for p in paths[:MAX_LISTED]]
        if len(paths) > MAX_LISTED:
            listed.append(f"- ほか {len(paths) - MAX_LISTED} 件")
        return Notice(summary, CONTEXT.format(paths="\n".join(listed), wdir=worktrees_rel))
    return None
