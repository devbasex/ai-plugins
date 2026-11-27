"""変更の読み取りと、PR が足した MVV の根拠の記述の確かめ（#4076）。

`base...HEAD` で変わったファイルと、Markdown の本文・セクション、差分とそのファイルごとの行を読む。差分の行の番号は git の
数え方（`\\n` だけで区切る）なので、Markdown の解析の行の番号（`\\r` でも区切る）とは `_markdown_rows` で対応させる。

根拠の記述は、`- 根拠: Value 5（MVV バージョン 11）` のように、決定がどの MVV の項目に基づくかを書いた行である。`citations` が、
PR 全体の差分で本文に増えた根拠の記述を、ベースブランチの承認済みの MVV のバージョンと項目に突き合わせる。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import git
from ndf.domains.crossreview import criteria_decide, scope_decide, settings
from ndf.domains.records import mvv, mvv_decide


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
        if kind == "blob" and mode in git.REGULAR_MODES:
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
        if mvv.blob_id(candidate, len(oid)) == oid:
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


def _docs(g: git.Git, files: Sequence[str], root: Path) -> dict[str, str]:
    """`files` のうち Markdown の本文。本文はレビューするコミット（HEAD）の通常のファイルの blob だけから読む
    （シンボリックリンクは差分の上のリンクとして扱い、リンク先を読まない。PR 3944 の r6-1）。作業場所のファイルから読めない
    本文はまとめて 1 回で読む（PR 3944 の r7-4）。"""
    blobs = _regular_blobs(g, [p for p in files if p.endswith(".md")])
    docs: dict[str, Optional[str]] = {path: _local_text(root, path, oid) for path, oid in blobs.items()}
    missing = {path: blobs[path] for path, text in docs.items() if text is None}
    if missing:
        texts = _committed_texts(g, "HEAD", list(missing))
        # 手元に無い blob を、git grep は失敗にせずに飛ばす（遅延取得を止めたとき）。空のファイルなら行が無くてよい
        unread = [p for p, oid in missing.items() if not texts[p] and oid != mvv.blob_id(b"", len(oid))]
        if unread:
            raise git.GitError(f"HEAD の本文を読めない: {', '.join(unread)}")
        docs |= texts
    return {path: text for path, text in docs.items() if text is not None}


def changed(g: git.Git, base: str, root: Path) -> Changed:
    """変わったファイルと、そのうち Markdown の本文。"""
    files = tuple(g.changed_files(base, "HEAD"))
    return Changed(files, _docs(g, files, root))


def sections(docs: dict[str, str]) -> list[str]:
    return [s for path, text in docs.items() for s in scope_decide.sections_of(path, text)]


def changed_sections(g: git.Git, base: str, root: Path) -> list[str]:
    """`base...HEAD` で変わった Markdown のセクション（`<パス>#<見出し>`）。改名を調べない（調べると部分クローンで前の本文が
    要る。改名前のパスは HEAD に無く、セクションは同じになる）。HEAD に無いパスは、ツリーを読まなくて済むように除く。"""
    return sections(_docs(g, g.changed_files(base, "HEAD", NO_RENAMES, "--diff-filter=d"), root))


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
# 名前を変えたファイルを削除と追加の 2 つに分けて数える（`round_scope` の起点どうしの比較も同じ）
NO_RENAMES = "--no-renames"

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# パスの行の始まりと、その行が示す側（ベースか）・パスの接頭辞
_NAMES = {"--- ": (True, "a/"), "+++ ": (False, "b/"), "rename from ": (True, ""), "rename to ": (False, "")}
# Markdown の解析が 1 行とみなす本文と改行
_MARKDOWN_ROW = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")


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


def _markdown_rows(text: str) -> list[tuple[int, str]]:
    """本文の行を Markdown の解析と同じ数え方（`\\n`・`\\r\\n`・`\\r` で区切る）で並べ、git の行の番号（`\\n` だけで区切る）と
    本文を返す。並びの n 番目が Markdown の n 行目で、`\\r` だけの改行を含む文書では git の番号と違う。"""
    out: list[tuple[int, str]] = []
    no = 1
    for row in _MARKDOWN_ROW.finditer(text):
        if row[0]:
            out.append((no, row[1]))
            no += row[2].endswith("\n")
    return out


# --- 根拠の記述の確かめ -------------------------------------------------------------------------

# コードブロックの囲みの行（引用の `>` とリストの行頭の記号の後ろも）
_FENCE = re.compile(r"(?:[\s>]|[-*+](?=\s)|[0-9]+[.)](?=\s))*(?:`{3}|~{3})")


class CitationRun(Protocol):
    """確かめが読み書きする、1 回の起動の値（`drive._Run`）。`last_target` は最後のラウンドの確かめる対象、`checked_head` は
    最後に確かめ終えた head、`citations` は（ベースとの共通の祖先・head・MVV を見分ける値）から、見つけた誤り。"""

    g: git.Git
    warnings: list[str]
    snapshot: Optional[mvv.Snapshot]
    checked_head: Optional[str]
    last_target: Optional[Target]
    citations: dict[tuple[str, str, str], list[dict[str, Any]]]

    @property
    def cfg(self) -> settings.RunConfig: ...


class _Row(NamedTuple):
    """head の文書の根拠の記述 1 行。`line` は git の行の番号、`part` はその中の `\\r` で区切った何番目の行か、`decision` は
    決定の見出しの下にあるか。"""

    file: DiffFile
    line: int
    part: int
    text: str
    decision: bool


def _cites(text: str) -> bool:
    """差分の行に、根拠の記述の書き出し（`根拠:`）があるか。"""
    return any(mvv_decide.parse_citation(line, True) is not None for _, line in _markdown_rows(text))


def _fence_changed(file: DiffFile, added: Mapping[int, str]) -> bool:
    """コードブロックの囲みの行を足したか（`added` は足した行）消したか。"""
    return any(_FENCE.match(line) for text in (*added.values(), *file.removed) for _, line in _markdown_rows(text))


def _numbered(text: str) -> dict[int, list[int]]:
    """git の行の番号から、その中の行の Markdown の番号の並び。"""
    out: dict[int, list[int]] = {}
    for at, (no, _) in enumerate(_markdown_rows(text), 1):
        out.setdefault(no, []).append(at)
    return out


def _cited_rows(file: DiffFile, text: str) -> list[_Row]:
    """head の本文 `text` の、コードブロックの外にある根拠の記述。"""
    code, decisions = mvv.fenced_lines(text), mvv.decision_lines(text)
    rows: list[_Row] = []
    last_no = part = 0
    for at, (no, line) in enumerate(_markdown_rows(text), 1):
        # git の番号は減らないので、前の行と同じ番号なら同じ git の行の続き
        part = part + 1 if no == last_no else 0
        last_no = no
        decision = at in decisions
        if at not in code and mvv_decide.parse_citation(line, decision) is not None:
            rows.append(_Row(file, no, part, line, decision))
    return rows


def _came_out(g: git.Git, commit: str, rows: Sequence[_Row]) -> list[_Row]:
    """足していない行 `rows` のうち、ベース（`commit`）ではコードブロックの中にあったもの。ベースの本文は 1 回でまとめて読む。"""
    paths = {row.file.base_path: None for row in rows if row.file.base_path is not None}
    if not paths:
        return []
    texts = _committed_texts(g, commit, list(paths))
    held = {path: (_numbered(text), mvv.fenced_lines(text)) for path, text in texts.items()}
    out = []
    for row in rows:
        base_line = _base_lines(row.file.hunks, row.line)
        if row.file.base_path is None or base_line is None:
            continue
        numbers, code = held[row.file.base_path]
        if numbers[base_line][row.part] in code:
            out.append(row)
    return out


class _Candidate(NamedTuple):
    """根拠の記述が本文に増えうるファイル。`added` は足した行（ファイルごとに 1 回だけ求める。候補の行ごとに全 hunk を走査
    しない）、`fenced` はコードブロックの囲みの行を変えたか。"""

    file: DiffFile
    added: Mapping[int, str]
    fenced: bool


def _diff_text(g: git.Git, target: Target) -> str:
    """確かめる対象の差分（全差分のファイルがあればその中身、無ければ git から読む）。"""
    if target.full_diff is not None:
        return target.full_diff.read_bytes().decode("utf-8")
    return review_diff(g, target.merge_base, target.head)


def _candidates(files: Sequence[DiffFile]) -> list[_Candidate]:
    """`files` のうち、コードブロックの囲みの行を変えたか、根拠の記述の書き出しのある行を足したもの。"""
    found = [_Candidate(file, added, _fence_changed(file, added)) for file in files for added in (file.added,)]
    return [c for c in found if c.fenced or any(_cites(text) for text in c.added.values())]


def _new_rows(g: git.Git, target: Target) -> list[_Row]:
    """PR で本文に増えた根拠の記述: 足した行と、変わっていないがコードブロックの囲みを変えたので本文に出た行。"""
    candidates = _candidates(_diff_files(_diff_text(g, target)))
    missing = [c.file.path for c in candidates if c.file.path not in target.docs]
    texts = {**target.docs, **(_committed_texts(g, target.head, missing) if missing else {})}
    added: list[_Row] = []
    kept: list[_Row] = []
    for candidate in candidates:
        for row in _cited_rows(candidate.file, texts[candidate.file.path]):
            if row.line in candidate.added:
                added.append(row)
            elif candidate.fenced:
                kept.append(row)
    return sorted([*added, *_came_out(g, target.merge_base, kept)], key=lambda row: (row.file.path, row.line, row.part))


def _unchecked(run: CitationRun, exc: Exception) -> None:
    run.warnings.append(f"PR が足した根拠の記述が承認済みの MVV に合うかを確かめられない: {exc}")


def citations(run: CitationRun, target: Target) -> Optional[list[dict[str, Any]]]:
    """PR が足した根拠の記述の誤り（`mvv_decide.citation_errors` の形に、指摘の文 `text` を足したもの）。確かめられなければ
    注意に載せて None。承認済みの MVV が無ければ、git を起動せずに空。同じ対象と MVV の確かめは、1 回の起動で 1 回だけ打つ。"""
    snap = criteria_decide.approved(run.snapshot)
    if snap is None or snap.version is None:
        return []
    key = (target.merge_base, target.head, snap.identity)
    if key not in run.citations:
        try:
            rows = _new_rows(run.g, target)
        except (git.GitError, OSError, UnicodeDecodeError) as exc:
            return _unchecked(run, exc)
        lines = [(row.file.path, row.line, row.text, row.decision) for row in rows]
        errors = mvv_decide.citation_errors(lines, {snap.version: snap.items})
        run.citations[key] = [{**e, "text": criteria_decide.citation_text(e, snap.version)} for e in errors]
    run.checked_head = target.head
    return run.citations[key]


def _final_target(run: CitationRun, head: str) -> Target:
    """終わり方を決める head の確かめる対象。最後のラウンドと同じ head なら、そのラウンドの対象をそのまま使う。"""
    if run.last_target is not None and run.last_target.head == head:
        return run.last_target
    return Target(run.g.merge_base(run.cfg.base_commit, head), head, {}, None)


def final_citations(run: CitationRun, head: str) -> Optional[list[dict[str, Any]]]:
    """終わり方を決める直前の確かめ（`citations` と同じ値）。呼び手は承認済みの MVV があるときだけ呼ぶ。"""
    try:
        target = _final_target(run, head)
    except git.GitError as exc:
        return _unchecked(run, exc)
    return citations(run, target)
