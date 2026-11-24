"""変更の読み取り（`drive.py` から分けた。#4076）。`base...HEAD` で変わったファイルと、Markdown の本文・セクション。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.adapters import git
from ndf.domains.crossreview import scope_decide, settings


class Changed(NamedTuple):
    """`base...HEAD` で変わったファイルと、そのうち Markdown の中身（パスから本文）。1 ラウンドに 1 回だけ読む。"""

    files: tuple[str, ...]
    docs: dict[str, str]


def _regular_blobs(g: git.Git, paths: Sequence[str]) -> dict[str, str]:
    """HEAD のツリーで通常のファイル（シンボリックリンク・サブモジュールでない）の `paths` の、パスから blob の ID。
    `git ls-tree` を 1 回だけ打つ。"""
    if not paths:
        return {}
    out: dict[str, str] = {}
    for entry in g.run("ls-tree", "--full-tree", "-z", "HEAD", "--", *paths).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind == "blob" and mode in settings.REGULAR_MODES:
            out[path] = oid
    return out


def _local_text(root: Path, path: str, oid: str) -> Optional[str]:
    """HEAD の通常のファイル `path`（blob `oid`）の本文を、作業場所のファイルから git を起動せずに読む（リンクをたどらず、
    そのままか改行を LF にそろえると同じ blob のときだけ。`core.autocrlf`・`eol=crlf` で取り出したファイルも読める。
    PR 3944 の r7-4）。読めなければ None。"""
    target = root / path
    if target.is_symlink() or target.parent.resolve() != (root.resolve() / path).parent:
        return None
    try:
        raw = target.read_bytes()
    except OSError:  # noqa: NDF401 ファイル: 作業場所に無い・読めなければ呼び手がコミットから読む
        return None
    for candidate in (raw, raw.replace(b"\r\n", b"\n")):
        if settings.blob_id(candidate, len(oid)) == oid:
            return candidate.decode("utf-8", errors="replace")
    return None


# `git grep -z` の 1 行（`HEAD:<パス>\0<行>`）と、すべての行を出す引数（利用者の設定の行番号・列・色・textconv を打ち消す）
_GREP_ROW = re.compile(r"HEAD:([^\0]*)\0([^\n]*)\n")
_GREP_ALL = (
    "--literal-pathspecs",
    "grep",
    "-a",
    "-z",
    "--full-name",
    "--no-color",
    "--no-line-number",
    "--no-column",
    "--no-textconv",
    "-e",
    "",
)


def _committed_texts(g: git.Git, blobs: Mapping[str, str]) -> dict[str, str]:
    """HEAD のコミットの `blobs`（パスから blob の ID）の本文（行の並び。改行は LF）。何件あっても git を 1 回だけ起動する
    （PR 3944 の r7-4）。"""
    texts: dict[str, list[str]] = {p: [] for p in blobs}
    done = g.attempt(*_GREP_ALL, "HEAD", "--", *blobs)
    # 1 は一致する行が無い（空のファイルだけ）
    if done.returncode not in (0, 1):
        raise git.GitError(f"git grep が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")
    for row in _GREP_ROW.finditer(done.stdout):
        if row.group(1) in texts:
            texts[row.group(1)].append(row.group(2))
    # 手元に無い blob を、git grep は失敗にせずに飛ばす（遅延取得を止めたとき）。空のファイルなら行が無くてよい
    unread = [p for p, oid in blobs.items() if not texts[p] and oid != settings.blob_id(b"", len(oid))]
    if unread:
        raise git.GitError(f"HEAD の本文を読めない: {', '.join(unread)}: {done.stderr.strip()}")
    return {p: "".join(f"{line}\n" for line in lines) for p, lines in texts.items()}


def _docs(g: git.Git, files: Sequence[str], root: Path) -> dict[str, str]:
    """`files` のうち Markdown の本文。本文はレビューするコミット（HEAD）の通常のファイルの blob だけから読む
    （シンボリックリンクは差分の上のリンクとして扱い、リンク先を読まない。PR 3944 の r6-1）。作業場所のファイルから読めない
    本文はまとめて 1 回で読む（PR 3944 の r7-4）。"""
    blobs = _regular_blobs(g, [p for p in files if p.endswith(".md")])
    docs: dict[str, Optional[str]] = {path: _local_text(root, path, oid) for path, oid in blobs.items()}
    missing = {path: blobs[path] for path, text in docs.items() if text is None}
    if missing:
        docs |= _committed_texts(g, missing)
    return {path: text for path, text in docs.items() if text is not None}


def changed(g: git.Git, base: str, root: Path) -> Changed:
    """変わったファイルと、そのうち Markdown の本文。"""
    files = tuple(g.changed_files(base, "HEAD"))
    return Changed(files, _docs(g, files, root))


def sections(docs: dict[str, str]) -> list[str]:
    return [s for path, text in docs.items() for s in scope_decide.sections_of(path, text)]


def changed_sections(g: git.Git, base: str, root: Path) -> list[str]:
    """`base...HEAD` で変わった Markdown のセクション（`<パス>#<見出し>`）。改名を調べない（調べると部分クローンで前の本文が
    要る。改名前のパスは HEAD に無く、セクションは同じになる）。"""
    return sections(_docs(g, g.changed_files(base, "HEAD", "--no-renames"), root))
