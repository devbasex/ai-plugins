"""変更の読み取り（`drive.py` から分けた。#4076）。

`base...HEAD` で変わったファイルと、Markdown の本文・セクション、差分とそのファイルごとの行を読む。差分の行の番号は git の
数え方（`\\n` だけで区切る）なので、Markdown の解析の行の番号（`\\r` でも区切る）とは `_markdown_lines` で対応させる。
"""

from __future__ import annotations

import ast
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from difflib import SequenceMatcher
from itertools import groupby
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


# `git grep` ですべての行を `<コミット>:<パス>\0<行>` の形で出す引数（利用者の設定の行番号・列・色・textconv を打ち消す）
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


def _committed_texts(g: git.Git, commit: str, paths: Sequence[str]) -> dict[str, str]:
    """`commit` の `paths` の本文（改行は変えず、末尾に無ければ足す。通常のファイルでなければ空）。
    何件あっても git を 1 回だけ起動する（PR 3944 の r7-4）。"""
    texts: dict[str, list[str]] = {p: [] for p in paths}
    # 1 は一致する行が無い（空のファイルだけ）
    out = g.run_bytes(*_GREP_ALL, commit, "--", *paths, codes=(0, 1)).decode("utf-8", errors="replace")
    for row in re.finditer(rf"{re.escape(commit)}:([^\0]*)\0([^\n]*\n)", out):
        if row.group(1) in texts:
            texts[row.group(1)].append(row.group(2))
    return {p: "".join(lines) for p, lines in texts.items()}


def changed(g: git.Git, base: str, root: Path) -> Changed:
    """変わったファイルと、そのうち Markdown の本文。本文はレビューするコミット（HEAD）の通常のファイルの blob だけから読む
    （シンボリックリンクは差分の上のリンクとして扱い、リンク先を読まない。PR 3944 の r6-1）。作業場所のファイルから読めない
    本文はまとめて 1 回で読む（PR 3944 の r7-4）。"""
    files = tuple(g.changed_files(base, "HEAD"))
    blobs = _regular_blobs(g, [p for p in files if p.endswith(".md")])
    docs: dict[str, Optional[str]] = {path: _local_text(root, path, oid) for path, oid in blobs.items()}
    missing = [path for path, text in docs.items() if text is None]
    if missing:
        docs |= _committed_texts(g, "HEAD", missing)
    return Changed(files, {path: text for path, text in docs.items() if text is not None})


def sections(docs: dict[str, str]) -> list[str]:
    return [s for path, text in docs.items() for s in scope_decide.sections_of(path, text)]


def changed_sections(g: git.Git, base: str, root: Path) -> list[str]:
    """`base...HEAD` で変わった Markdown のセクション（`<パス>#<見出し>`）。"""
    return sections(changed(g, base, root).docs)


# --- 差分 -----------------------------------------------------------------------------------

# 差分の形（接頭辞・パス・hunk の前後の行数とつなぎ方・改名の検出・サブモジュール・本文の変換）を利用者の設定によらず同じに
# する引数。改名の検出の上限は git の既定と同じ 1000（超えると、書き換えて改名したファイルは足したファイルになる）
_DIFF = (
    "-c",
    "core.quotePath=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.suppressBlankEmpty=false",
    "diff",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--unified=3",
    "--inter-hunk-context=0",
    "--find-renames",
    "-l1000",
    "--submodule=short",
)
# hunk の見出し（行数の省略は 1 行）
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# パスの行の始まりと、その行が示す側（ベースか）・パスの接頭辞
_NAMES = {"--- ": (True, "a/"), "+++ ": (False, "b/"), "rename from ": (True, ""), "rename to ": (False, "")}
# Markdown の解析が 1 行とみなす本文と改行
_MARKDOWN_ROW = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")
# 置き換えた行の照合（`_matched`）で、繰り返す行を照合する 1 区間の行の組の数の上限
_MAX_PAIRS = 10_000


class Line(NamedTuple):
    """差分の本文の 1 行。`base`・`head` はそれぞれの側の行の番号（1 始まり。足した行は `base`、消した行は `head` が None）。"""

    base: Optional[int]
    head: Optional[int]
    text: str


class Hunk(NamedTuple):
    """差分の 1 つの hunk。`head` は head の最初の行の番号（head の側が 0 行なら、その後ろの行の番号）。"""

    head: int
    lines: tuple[Line, ...]


class DiffFile(NamedTuple):
    """差分の 1 つのファイル。`path` は head のパス、`base_path` はベースのパス（改名なら改名前。足したファイルなら None）。
    行の番号は git の数え方。行の本文は CRLF の `\\r` を残す。"""

    path: str
    base_path: Optional[str]
    hunks: tuple[Hunk, ...]

    @property
    def added(self) -> dict[int, str]:
        """足した行の、head の行の番号から本文。"""
        return {x.head: x.text for hunk in self.hunks for x in hunk.lines if x.base is None and x.head is not None}

    @property
    def removed(self) -> list[str]:
        """消した行の本文。"""
        return [x.text for hunk in self.hunks for x in hunk.lines if x.head is None]


class Target(NamedTuple):
    """根拠の行を確かめる対象（コミット `merge_base` から `head` まで）。`docs` は head の Markdown の本文（読んでいない文書は
    含まない）。`full_diff` は全差分（`review_diff`）のファイルで、無ければ None。改行を変えずに読む（`read_text` は `\\r` を
    改行に変え、hunk の行数が合わなくなる）。"""

    merge_base: str
    head: str
    docs: Mapping[str, str]
    full_diff: Optional[Path]


def review_diff(g: git.Git, base: str, head: str) -> str:
    """`base...head` の差分。利用者の設定によらず同じ形で、行とその番号はコミットの blob のとおり（UTF-8 でない字は置き換える）。"""
    return g.run_bytes(*_DIFF, f"{base}...{head}").decode("utf-8", errors="replace")


def _path(value: str, prefix: str) -> Optional[str]:
    """差分の見出しの行のパス（`/dev/null` は None）。git は、空白を含むパスの後ろにタブを足し、制御文字・`"`・`\\` を含む
    パスを `"` で囲んで C のエスケープで書く（Python の文字列リテラルとして読める）。"""
    value = value.removesuffix("\t")
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        value = str(ast.literal_eval(value))
    return value.removeprefix(prefix)


def _read_name(line: str, names: dict[bool, Optional[str]]) -> None:
    """パスの行なら、`names`（ベースの側か → パス）に入れる。"""
    for start, (of_base, prefix) in _NAMES.items():
        if line.startswith(start):
            names[of_base] = _path(line.removeprefix(start), prefix)


def _hunk(header: re.Match[str], rest: Iterator[str]) -> Hunk:
    """見出し `header` の hunk を、示された行数だけ `rest` から読む（見出しに見える本文の行を読み違えない）。"""
    base_left, head_left = (int(n) if n is not None else 1 for n in (header[2], header[4]))
    # 0 行の側の番号は、その前の行を指す
    base = int(header[1]) + (base_left == 0)
    head = first = int(header[3]) + (head_left == 0)
    lines: list[Line] = []
    while base_left or head_left:
        line = next(rest)
        if line.startswith("\\"):
            # `\ No newline at end of file` は行でない
            continue
        in_base, in_head = line[0] != "+", line[0] != "-"
        lines.append(Line(base if in_base else None, head if in_head else None, line[1:]))
        base, base_left = base + in_base, base_left - in_base
        head, head_left = head + in_head, head_left - in_head
    return Hunk(first, tuple(lines))


def _diff_files(text: str) -> list[DiffFile]:
    """`review_diff` の差分を、ファイルごとに分ける。消したファイルと、パスの行の無いファイル（バイナリ・実行権限だけの変更・
    空のファイル）は返さない。"""
    files: list[tuple[dict[bool, Optional[str]], list[Hunk]]] = []
    rest = iter(text.split("\n"))
    for line in rest:
        header = _HUNK.match(line)
        if line.startswith("diff --git "):
            files.append(({}, []))
        elif header is not None:
            files[-1][1].append(_hunk(header, rest))
        elif files:
            _read_name(line, files[-1][0])
    return [DiffFile(path, names.get(True), tuple(hunks)) for names, hunks in files if (path := names.get(False)) is not None]


def _base_lines(hunks: Sequence[Hunk], head_line: int) -> Optional[int]:
    """head の行の番号 `head_line` に当たる、ベースの行の番号（足した行なら None）。"""
    offset = 0
    for hunk in hunks:
        if head_line < hunk.head:
            break
        for line in hunk.lines:
            if line.head == head_line:
                return line.base
        offset += sum((x.head is None) - (x.base is None) for x in hunk.lines)
    return head_line + offset


def _markdown_lines(text: str) -> list[tuple[int, str]]:
    """本文の行を Markdown の解析と同じ数え方（`\\n`・`\\r\\n`・`\\r` で区切る）で並べ、git の行の番号（`\\n` だけで区切る）と
    本文を返す。並びの n 番目が Markdown の n 行目で、`\\r` だけの改行を含む文書では git の番号と違う。"""
    out: list[tuple[int, str]] = []
    no = 1
    for row in _MARKDOWN_ROW.finditer(text):
        if row[0]:
            out.append((no, row[1]))
            no += row[2].endswith("\n")
    return out


def _rows(lines: Iterable[Line], of_base: bool) -> list[tuple[tuple[int, int], str]]:
    """差分の行のうちベース（`of_base`）か head の側にあるものを Markdown の行に分け、（git の行の番号, その行の中で何番目か。
    0 始まり）と本文を返す。"""
    out: list[tuple[tuple[int, int], str]] = []
    for line in lines:
        no = line.base if of_base else line.head
        if no is not None:
            out += [((no, nth), text) for nth, (_, text) in enumerate(_markdown_lines(line.text + "\n"))]
    return out


def _anchors(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """どちらにも 1 回だけ現れる同じ行のうち、順序を保って最も多く対応させた、`a` と `b` の位置の組。"""
    in_a, in_b = Counter(a), Counter(b)
    at = {t: i for i, t in enumerate(a) if in_a[t] == 1 and in_b[t] == 1}
    # 最長増加部分列。ends[k] は長さ k + 1 の列の末尾の位置の最小値、tails[k] はその末尾の組の番号
    ends: list[int] = []
    tails: list[int] = []
    pairs: list[tuple[int, int]] = []
    before: list[Optional[int]] = []
    for j, text in enumerate(b):
        if (i := at.get(text)) is None:
            continue
        k = bisect_left(ends, i)
        before.append(tails[k - 1] if k else None)
        ends[k : k + 1], tails[k : k + 1] = [i], [len(pairs)]
        pairs.append((i, j))
    out: list[tuple[int, int]] = []
    n = tails[-1] if tails else None
    while n is not None:
        out.append(pairs[n])
        n = before[n]
    return out[::-1]


def _matched(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """`a` と `b` の、順序を保って対応する同じ行の位置の組。1 回だけ現れる行で区切り、区切りの間は前後の同じ行を先に対応させ、
    残りは行の組の数が `_MAX_PAIRS` 以下のときだけ照合する（同じ行の繰り返しで所要時間が行数の 2 乗に増えない）。"""
    out: list[tuple[int, int]] = []
    i = j = 0
    for end_i, end_j in (*_anchors(a, b), (len(a), len(b))):
        stop_i, stop_j = end_i, end_j
        while i < stop_i and j < stop_j and a[i] == b[j]:
            out.append((i, j))
            i, j = i + 1, j + 1
        while i < stop_i and j < stop_j and a[stop_i - 1] == b[stop_j - 1]:
            stop_i, stop_j = stop_i - 1, stop_j - 1
            out.append((stop_i, stop_j))
        if 0 < (stop_i - i) * (stop_j - j) <= _MAX_PAIRS:
            match = SequenceMatcher(None, a[i:stop_i], b[j:stop_j], autojunk=False)
            out += [(i + x + n, j + y + n) for x, y, size in match.get_matching_blocks() for n in range(size)]
        if end_i < len(a):
            out.append((end_i, end_j))
        i, j = end_i + 1, end_j + 1
    return out


def _same_rows(hunks: Sequence[Hunk]) -> dict[tuple[int, int], tuple[int, int]]:
    """置き換えた git の行の中で変わっていない Markdown の行の、head の位置からベースの位置（位置は `_rows`）。git は `\\r` だけで
    区切った文書の 1 か所の変更を、つながった行全部の置き換えとして出す。"""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    for hunk in hunks:
        for replaced, run in groupby(hunk.lines, key=lambda x: x.base is None or x.head is None):
            if not replaced:
                continue
            lines = list(run)
            base, head = _rows(lines, True), _rows(lines, False)
            out |= {head[j][0]: base[i][0] for i, j in _matched([t for _, t in base], [t for _, t in head])}
    return out
