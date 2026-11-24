"""MVV の記録を読み書きする口（N8b・#2603・#2614・#2777・#2970・#2853）。

記録は状態の保存先の mvv-state（表 locations）の下の JSONL で、追記だけで持つ。記録の `repo` はメインディレクトリの
パスにする（worktree から書いても同じキー）。改訂の兆候は、ベースブランチの設定を読んで数える。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import git as git_mod
from ndf.adapters import markdown, publish, run_log
from ndf.adapters.github import GitHub, GitHubError, Pull
from ndf.contracts import internal, worker_paths
from ndf.core.converge import oscillation
from ndf.domains.records import files, jsonl
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.domains.records.mvv_decide import MvvError
from ndf.domains.settings import renames
from ndf.progress.gate import records
from ndf.progress.gate.checks import Changed, MvvFacts

# LLM の判定の関数: MVV 判定の入力を受け、判定の語と費用・秒の実測値を返す
Judge = Callable[[Mapping[str, str]], tuple[str, Mapping[str, Any]]]


# 共通原則の本文（コントラクトの保存先からの相対）
PRINCIPLES = "data/ndf-common-principles.md"
# 本文と共通原則のスナップショットのディレクトリ（`records.mvv_state_dir()` の下）
SNAPSHOT_DIR = "snapshots"
# ファイル名に入れる sha256 の先頭の文字数
_NAME_SHA = 12
# 設計文書の決定の見出し（`### 決定 4: 題`）のレベルと題の始まり
_DECISION_LEVEL = 3
_DECISION = re.compile(r"決定 [0-9]+\b")
# 承認資料の「目指す姿の根拠」のセクションの題と、セクションにする見出しの深さの上限（決定の見出しが 3 の深さなので 2 まで）
BASIS_TITLE = "目指す姿の根拠"
_BASIS_LEVEL = 2
# 設計文書ごとのまとまりの見出し（深さ 1 の `# <設計文書の H1>（#<課題番号>）`）
_BLOCK = re.compile(r".*[（(]#[0-9]+[）)]$")


class Snapshot(NamedTuple):
    """コミット `commit` の MVV を読んだ結果。

    承認済みのときだけ `version`・`sha256`・`path`（本文のスナップショット）・`items`・`principles_*` を持つ。`blobs` は
    読んだ設定と本文のパス → Git の blob の id（設定が無ければ空）。
    """

    state: str
    version: Optional[int]
    sha256: Optional[str]
    path: Optional[Path]
    items: tuple[str, ...]
    blobs: Mapping[str, str]
    commit: str
    principles_sha256: str
    principles_path: Optional[Path]

    @property
    def identity(self) -> str:
        """この MVV を見分ける値（`mvv_decide.identity_of`）。"""
        return md.identity_of(self.state, self.version, self.sha256, self.principles_sha256)


def record_path(file_id: str) -> Path:
    """記録のファイル（表 ndf-files の mvv-validation・mvv-signals）。"""
    return records.mvv_record_path(file_id)


@cache
def repo_key(root: Path) -> str:
    """記録の `repo`（メインディレクトリの絶対パス。worktree から呼んでも同じ）。git の外なら `MvvError`。

    git は 1 回だけ起動する（プロセスの中で覚える）。
    """
    try:
        entries = git_mod.Git(root).worktrees()
    except git_mod.GitError as exc:
        raise MvvError(f"メインディレクトリを解けない: {exc}") from exc
    if not entries:
        raise MvvError(f"メインディレクトリを解けない: {root}")
    return str(entries[0].path)


def decl_path() -> str:
    """MVV の設定のパス（根からの相対）。"""
    return files.relpath("mvv")


def decl_at(root: Path, ref: str) -> dict[str, Any]:
    """参照（`<リモート名>/<開発の起点>` など）にある MVV の設定を読む。読めなければ `MvvError`。"""
    return decl_from(_blob(git_mod.Git(root), f"{ref}:{decl_path()}", f"{ref} の MVV の設定"), ref, root)


def decl_from(raw: bytes, ref: str, root: Path) -> dict[str, Any]:
    """参照 `ref` から読んだ MVV の設定のバイト列を解析して確かめる。壊れている・旧いキーが残るなら `MvvError`。"""
    try:
        decl = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MvvError(f"{ref} の MVV の設定を読めない: {exc}") from exc
    if not isinstance(decl, dict):
        raise MvvError(f"{ref} の MVV の設定が object でない")
    _refuse_legacy(decl, root)
    return decl


def _refuse_legacy(raw: Mapping[str, Any], root: Path) -> None:
    """旧いキーが残る MVV の設定なら `MvvError`（既定へ倒さない）。"""
    legacy = renames.legacy_detail("mvv", raw, root)
    if legacy is not None:
        raise MvvError(legacy)


def principles() -> bytes:
    """共通原則の本文（元のバイト列）。"""
    return (contracts.contracts_dir() / PRINCIPLES).read_bytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_id(data: bytes) -> str:
    """Git の blob の id（Git を起動せずに元のバイト列から求める）。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _commit_of(g: git_mod.Git, ref: str) -> str:
    try:
        return g.rev_parse(ref)
    except git_mod.GitError as exc:
        raise MvvError(f"MVV を読む参照 {ref} をコミットへ解決できない: {exc}") from exc


def _decl_blob(g: git_mod.Git, commit: str) -> Optional[str]:
    """コミットのツリーにある設定の blob の名前。エントリが無いと確かめられたときだけ None（読めなければ `MvvError`）。"""
    where = f"{commit} の {decl_path()}"
    try:
        listed = g.run("ls-tree", "-z", "--full-tree", commit, "--", decl_path())
    except git_mod.GitError as exc:
        raise MvvError(f"{where} があるかを確かめられない: {exc}") from exc
    if not listed:
        return None
    mode, kind, oid = listed.partition("\t")[0].split()
    if not stat.S_ISREG(int(mode, 8)):
        raise MvvError(f"{where} が通常のファイルでない: {mode} {kind}")
    return oid


def _blob(g: git_mod.Git, name: str, where: str) -> bytes:
    """blob `name` の元のバイト列。読めなければ `MvvError`（`where` は誤りに出すファイル）。"""
    try:
        return g.run_bytes("cat-file", "blob", name)
    except git_mod.GitError as exc:
        raise MvvError(f"{where}を読めない: {exc}") from exc


def _blobs(g: git_mod.Git, *names: str) -> list[Optional[bytes]]:
    """blob の元のバイト列（無い・blob でないものは None）。git が失敗すれば `MvvError`。"""
    try:
        return g.blobs(*names)
    except git_mod.GitError as exc:
        raise MvvError(f"MVV の設定と本文を読めない: {exc}") from exc


def _is_private(path: Path) -> bool:
    """`path` が、この利用者が所有し、ほかに権限を与えていない通常のファイルか（無ければ偽。リンクは辿らない）。"""
    if not os.path.lexists(path):
        return False
    st = path.lstat()
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) & 0o077 == 0


def _snapshot_dir(outside: Optional[Path]) -> Path:
    """スナップショットのディレクトリ（リンクをたどった実体のパス。無ければ作る）。

    担当はこのパスを後で開くので、確かめた後に中身を差し替えられる場所（`outside` の中・ほかの利用者が根までのどこかの
    項目を差し替えられる場所）なら `MvvError`。
    """
    where = Path(os.path.realpath(records.mvv_state_dir() / SNAPSHOT_DIR))
    if outside is not None and where.is_relative_to(os.path.realpath(outside)):
        raise MvvError(f"MVV のスナップショットの保存先 {where} がレビューする worktree {outside} の中にある。worktree の外にする")
    try:
        with worker_paths.open_or_create(where) as wd:
            worker_paths.check_unshared(wd)
            return wd.path
    except worker_paths.EntryError as exc:
        raise MvvError(f"MVV のスナップショットの保存先 {where} を使えない（ほかの利用者が中身を差し替えられる）: {exc}") from exc


def _write_verified(path: Path, data: bytes, sha256: str) -> Path:
    """`path` を、所有者だけが読める（0600）中身 `data`（sha256 は `sha256`）のファイルにする。

    中身と権限が同じなら書かない。違えば一時ファイルへ書いて置き換える（本文は非公開のリポジトリのものを含む）。
    """
    if _is_private(path) and _sha256(path.read_bytes()) == sha256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=f".{path.name}.") as tmp:
        new = Path(tmp) / path.name
        with open(os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
            out.write(data)
        os.replace(new, path)
    return path


def mismatch_detail(blobs: Mapping[str, str]) -> str:
    """本文が承認の後に変わったことを伝える文。`blobs` は `Snapshot.blobs`（最後が本文）。"""
    return f"ベースの MVV の本文が承認の記録と合わない: {list(blobs)[-1]}"


def items_in(body: str, shared: str) -> tuple[str, ...]:
    """MVV の本文と共通原則に実在する項目（`mvv_decide.items_of`）。コードブロックの中の例は数えない。"""
    return md.items_of(_without_code(body), _without_code(shared))


def _without_code(text: str) -> str:
    """Markdown の本文の、コードブロックの行を空にする。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    inside = markdown.code_lines(text)
    return "\n".join("" if no < len(inside) and inside[no] else line for no, line in enumerate(lines))


def current(root: Path, ref: str, allow_mismatch: bool = False, outside: Optional[Path] = None) -> Snapshot:
    """参照 `ref`（取得済みの参照かコミット）の MVV を読み、承認済みなら本文と共通原則のスナップショットを書いて返す。

    `ref` が完全な SHA なら解決し直さない。`outside` の中にはスナップショットを書かない（レビューする worktree を渡す）。
    設定がツリーに無いと確かめられたときだけ `absent`。参照・設定・本文を読めないときと、本文が承認の記録と合わない
    とき（`allow_mismatch` でない）は `MvvError`、スナップショットを書けないときは `OSError`。
    """
    snap, body, shared = _read(git_mod.Git(root), root, ref, allow_mismatch)
    if snap.state != md.State.APPROVED or snap.sha256 is None:
        return snap
    found = items_in(_text(body, list(snap.blobs)[-1]), shared.decode("utf-8"))
    where = _snapshot_dir(outside)
    body_copy = _write_verified(where / f"v{snap.version}-{snap.sha256[:_NAME_SHA]}.md", body, snap.sha256)
    shared_copy = _write_verified(where / f"principles-{snap.principles_sha256[:_NAME_SHA]}.md", shared, snap.principles_sha256)
    return snap._replace(path=body_copy, items=found, principles_path=shared_copy)


def of_base(root: Path, branch: str, commit: str, outside: Path) -> Snapshot:
    """取得済みのベースブランチ `branch` の先頭 `commit` の MVV（`current`）。読めない・スナップショットを書けないときは、どれも
    ブランチ名を添えた `MvvError`（MVV を担当へ渡す処理が、担当を起動する前に止まるために使う）。"""
    try:
        return current(root, commit, outside=outside)
    except (MvvError, identity.IdentityError, OSError) as exc:
        raise MvvError(f"{branch} の MVV を読めない: {exc}") from exc


def identity_at(root: Path, ref: str) -> str:
    """参照 `ref`（取得済みの参照かコミット）の MVV を見分ける値（`Snapshot.identity`）。スナップショットを書かない。

    `current` と同じ読み方で求める。参照・設定・本文を読めないときと、本文が承認の記録と合わないときは `MvvError`。
    ネットワークに出ない（部分クローンで設定や本文が手元に無ければ、取得せずに `MvvError`）。
    """
    return _read(git_mod.Git.offline(root), root, ref, False)[0].identity


def _read(g: git_mod.Git, root: Path, ref: str, allow_mismatch: bool) -> tuple[Snapshot, bytes, bytes]:
    """参照 `ref` の MVV を `g` で読んだ結果（スナップショットのパスと項目は空）と、承認済みのときの本文と共通原則（ほかは空）。"""
    commit = ref if git_mod.FULL_SHA.match(ref) else _commit_of(g, ref)
    held = _decl_blob(g, commit)
    if held is None:
        return _without_approval(md.State.ABSENT, {}, commit), b"", b""
    usual = md.body_path({})
    # 本文は既定のパスにあると見込んで、設定と同じ 1 回の起動で読む
    raw, guess = _blobs(g, held, f"{commit}:{usual}")
    if raw is None:
        raise MvvError(f"{commit} の MVV の設定（{decl_path()}）を読めない: {held}")
    decl = decl_from(raw, commit, root)
    if md.status(decl, None) == md.State.UNAPPROVED:
        return _without_approval(md.State.UNAPPROVED, {decl_path(): _blob_id(raw)}, commit), b"", b""
    path = md.body_path(decl)
    body = guess if path == usual and guess is not None else _blob(g, f"{commit}:{path}", f"{commit} の MVV の本文（{path}）")
    blobs = {decl_path(): _blob_id(raw), path: _blob_id(body)}
    body = md.approved_form(decl, body)
    sha = _sha256(body)
    state = md.status(decl, sha)
    if state == md.State.MISMATCH and not allow_mismatch:
        raise MvvError(mismatch_detail(blobs))
    if state != md.State.APPROVED:
        return _without_approval(state, blobs, commit), b"", b""
    version = md.current_version(decl)
    if version is None:
        raise MvvError(f"{commit} の MVV の設定の最後のバージョンに番号が無い: {decl_path()}")
    shared = principles()
    return Snapshot(state, version, sha, None, (), blobs, commit, _sha256(shared), None), body, shared


def _without_approval(state: str, blobs: Mapping[str, str], commit: str) -> Snapshot:
    """承認済みでない状態 `state` の `Snapshot`（承認済みのときだけ持つ値は空）。"""
    return Snapshot(state, None, None, None, (), blobs, commit, "", None)


def _text(body: bytes, path: str) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MvvError(f"MVV の本文 {path} を UTF-8 で読めない: {exc}") from exc


def fenced_lines(text: str) -> frozenset[int]:
    """Markdown の本文 `text` の、コードブロック（囲みの記号の行とインデントも含む）の行の番号（1 始まり）。"""
    return frozenset(no for no, inside in enumerate(markdown.code_lines(text), 1) if inside)


def decision_lines(text: str) -> frozenset[int]:
    """Markdown の本文 `text` の、決定の見出し（`### 決定 <数>`）の次の行から次の見出しの前までの行の番号（1 始まり）。"""
    heads = markdown.atx_headings(text)
    ends = [h.line for h in heads[1:]] + [len(markdown.code_lines(text))]
    got: set[int] = set()
    for head, end in zip(heads, ends, strict=False):
        if head.level == _DECISION_LEVEL and _DECISION.match(head.title):
            got.update(range(head.line + 2, end + 1))
    return frozenset(got)


class Cited(NamedTuple):
    """`cite` の結果。`errors` は根拠の行の誤り（`citation_errors` の形）、`checked` は確かめた根拠の行の数（誤りが 0 件でも数える）。"""

    errors: list[dict[str, Any]]
    checked: int

    def metrics(self, snap: Snapshot) -> dict[str, Any]:
        """結果 JSON の `metrics`: 確かめた MVV のバージョンとベースのコミット、確かめた根拠の行の数。"""
        return {"mvv_version": snap.version, "commit": snap.commit, "checked": self.checked}


def _material_lines(text: str) -> list[str]:
    """本文の行（Markdown の解析と同じく `\\r\\n`・`\\r`・`\\n` で区切る）。並びの n 番目が n 行目。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def cite(snap: Snapshot, files: Sequence[Path], require_basis: bool = False) -> Cited:
    """承認資料 `files` の全体の根拠の行を、承認済みの MVV `snap` で確かめる（cross-review の `_citations` と同じ関数・同じ規則。#3959）。

    先に `files` のすべてを UTF-8 で読み（読めなければ `OSError`・`UnicodeDecodeError`）、MVV が承認済みでなければ確かめずに
    `Cited([], 0)`。
    """
    texts = [(str(path), path.read_bytes().decode("utf-8")) for path in files]
    if snap.state != md.State.APPROVED or snap.version is None:
        return Cited([], 0)
    errors: list[dict[str, Any]] = []
    checked = 0
    for path, text in texts:
        code, decisions = fenced_lines(text), decision_lines(text)
        lines = [(path, no, line, no in decisions) for no, line in enumerate(_material_lines(text), 1) if no not in code]
        checked += sum(1 for _, _, line, decision in lines if md.parse_citation(line, decision) is not None)
        found = md.citation_errors(lines, {snap.version: snap.items}) + _Material(path, text).errors(require_basis)
        errors += sorted(found, key=lambda e: int(e["line"]))
    return Cited(errors, checked)


class _Material:
    """承認資料 1 つの見出しのコントラクト（「目指す姿の根拠」のセクション・決定の見出し・設計文書ごとのまとまり）の確かめ。行の番号は 1 始まり。"""

    def __init__(self, path: str, text: str) -> None:
        self.path, self.rows = path, _material_lines(text)
        self.code, self.decisions = fenced_lines(text), decision_lines(text)
        self.heads = markdown.atx_headings(text)
        self.total = len(markdown.code_lines(text))

    def _end(self, at: int, closes: Callable[[markdown.Heading], bool]) -> int:
        """`at` 番目の見出しの範囲の最後の行（`closes` に当たる次の見出しの前の行。無ければ文書の最後の行）。"""
        return next((h.line for h in self.heads[at + 1 :] if closes(h)), self.total)

    def _basis(self, no: int) -> bool:
        """`no` 行目が、コードブロックの外の根拠の行の形（`parse_citation` が決定の根拠の行として候補を返す行）か。"""
        return no not in self.code and md.parse_citation(self.rows[no - 1], True) is not None

    def _error(self, no: int) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": no,
            "cited": self.rows[no - 1].strip(),
            "kind": md.CITATION_HEADING,
            "version": None,
            "unknown": [],
        }

    def _sections(self) -> list[tuple[int, int]]:
        """「目指す姿の根拠」のセクション（見出しの行・最後の行）。同じ深さか上位の次の見出しで閉じる。"""
        return [
            (h.line + 1, self._end(i, lambda g, h=h: g.level <= h.level))
            for i, h in enumerate(self.heads)
            if h.level <= _BASIS_LEVEL and h.title.strip() == BASIS_TITLE
        ]

    def _decisions(self, start: int, end: int) -> list[tuple[int, int]]:
        """行 `start`〜`end` の決定の見出し（見出しの行・その下の最後の行）。"""
        return [
            (h.line + 1, self._end(i, lambda g: True))
            for i, h in enumerate(self.heads)
            if start <= h.line + 1 <= end and h.level == _DECISION_LEVEL and _DECISION.match(h.title)
        ]

    def _blocks(self) -> list[tuple[int, int]]:
        """設計文書ごとのまとまり（見出しの行・最後の行）。まとまりの見出しが無ければ文書の全体を 1 つにする。"""
        at = [i for i, h in enumerate(self.heads) if h.level == 1 and _BLOCK.match(h.title.strip())]
        if not at:
            return [(1, self.total)]
        return [(self.heads[i].line + 1, self._end(i, lambda g: g.level == 1 and _BLOCK.match(g.title.strip()) is not None)) for i in at]

    def errors(self, require_basis: bool) -> list[dict[str, Any]]:
        """見出しのコントラクトの誤り。`require_basis`（設計承認ゲート）なら、セクションの無いまとまりと根拠の行の無い決定も誤りにする。"""
        out: list[dict[str, Any]] = []
        sections = self._sections()
        for start, end in sections:
            decisions = self._decisions(start, end)
            out += [] if decisions else [self._error(start)]
            out += [self._error(no) for no in range(start + 1, end + 1) if no not in self.decisions and self._basis(no)]
            if require_basis:
                out += [self._error(head) for head, last in decisions if not any(self._basis(no) for no in range(head + 1, last + 1))]
        if require_basis:
            out += [self._error(first) for first, last in self._blocks() if not any(first <= s <= last for s, _ in sections)]
        return out


def signals(root: Path, base_branch: str, *, warnings: list[dict[str, Any]]) -> md.Signals:
    """ベースブランチの設定の現行のバージョンで、改訂の兆候を数える（本番のブランチの古いバージョンで数えない）。

    記録に読めない行があれば `warnings` へ足す。
    """
    decl = decl_at(root, md.signals_ref(base_branch, git_mod.Git(root).remote()))
    rows, _ = jsonl.read_rows_warned(record_path("mvv-signals"), kind="mvv-signals", warnings=warnings)
    return md.count_signals(rows, decl, repo_key(root))


def worktree_version(root: Path) -> Optional[int]:
    """worktree の MVV の設定の現行のバージョン。設定を読めなければ `MvvError`。"""
    try:
        raw = json.loads((root / decl_path()).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MvvError(f"worktree の MVV の設定を読めない: {exc}") from exc
    if not isinstance(raw, dict):
        raise MvvError("worktree の MVV の設定が object でない")
    _refuse_legacy(raw, root)
    return md.current_version(raw)


def record_answer(root: Path, answer: Mapping[str, Any], version: Optional[int]) -> list[dict[str, Any]]:
    """承認ゲートの回答（`verdict`・`approved`・`state`・`gate`・`pr`）を改訂の兆候の記録へ写し、書いた行を返す。

    「判定できない」なら人の回答によらず `unknown` の行を書き、覆し（と「判定できない」を人が通したもの）なら
    その種類の行を書く。どちらでもなければ書かない。`version` が無ければ worktree の設定の現行のバージョンで埋める
    （バージョンの無い行は兆候に数えられないため）。
    """
    kinds = md.signal_kinds(answer.get("verdict"), bool(answer.get("approved")))
    if not kinds:
        return []
    at, repo = jsonl.now(), repo_key(root)
    got = version if version is not None else worktree_version(root)
    pair = {k: answer[k] for k in ("state", "gate", "pr") if k in answer}
    rows = [{"at": at, "kind": kind, "mvv_version": got, "repo": repo, **pair} for kind in kinds]
    path = record_path("mvv-signals")
    return [jsonl.append_locked(path, row, kind="mvv-signals") for row in rows]


# 診断の記録を開くフラグ。リンクをたどらず、読み手の無い FIFO を置かれても待たない（ENXIO で失敗する）
_PRIVATE_APPEND = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK


def _append_private(path: Path, line: bytes) -> None:
    """`path` を所有者だけが読めるファイルに限ってから 1 行足す。限れなければ書かずに `OSError`。

    権限を確かめた記述子へそのまま書く。パスで権限を変えてから開き直すと、その間にファイル名を差し替えられうるためである。
    """
    fd = os.open(path, _PRIVATE_APPEND, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            raise OSError(f"診断の記録が自分の通常のファイルでない: {path}")
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        view = memoryview(line)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def record_failure(row: Mapping[str, Any]) -> str:
    """失敗の診断の記録（表 ndf-files の mvv-gate-failures）へ 1 行足し、行の識別子を返す。書けなければ例外をそのまま投げる。

    識別子は乱数で、PR の値も例外の文も含まない（PR のコメントと承認資料はこの識別子で記録を指す。#3959）。行の `cause` は
    社内のホスト名や手元のパスを含むので、判定を打った OS の利用者だけが読めるファイル（0600）にだけ書く。親のディレクトリは
    無ければ 0700 で作り、既にあるディレクトリの権限は変えない。
    """
    ident = secrets.token_hex(8)
    out = internal.write_record("mvv-gate-failure", {**row, "id": ident})
    path = record_path("mvv-gate-failures")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with jsonl.lock(path):
        _append_private(path, (json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
    return ident


def record_key(text: str) -> str:
    """cross-review の指摘の本文 `text` を見分ける値。起動をまたいで同じ指摘に同じ値になる。

    秘密を伏せてから、空白と大文字小文字の違いを除いて切り詰めた本文の SHA-256 にする。順を逆にすると、小文字にした秘密や切り詰めで
    分かれた秘密が伏せられずに残る。
    """
    return _sha256(oscillation.finding_key(str(run_log.redact(text))).encode("utf-8"))


class ReviewWrite(NamedTuple):
    """`record_review` の結果。`written` は足した行の数、`missing` は記録に無いままの必須の行（`redline`・`deferred`）、
    `cause` は最初の失敗の原因（`failure_cause` の形。失敗が無ければ None）。"""

    written: int
    missing: tuple[Mapping[str, Any], ...]
    cause: Optional[str]


# 記録の書き込みで止める失敗（どれも外へ出さず `ReviewWrite` にする）
_WRITE_ERRORS = (OSError, UnicodeDecodeError, internal.InternalError, identity.IdentityError, MvvError)


def failure_cause(step: str, exc: BaseException) -> str:
    """記録を書けなかった原因（`<操作>: <例外の型>: <伏せた文>`）。秘密と認証情報の付いた URL の全体を伏せ、保存先のパスは残す。"""
    return f"{step}: {type(exc).__name__}: {publish.scrub(str(exc), False).text}"


def _review_rows_of(repo: str, pr: int, head: str, snapshot: Snapshot, found: Sequence[md.Judged], clean: Any) -> list[dict[str, Any]]:
    shared = {"at": jsonl.now(), "repo": repo, "pr": pr, "mvv_version": snapshot.version, "sha256": snapshot.sha256}
    texts = [t for f in found for t in f.free_texts] if clean is not None else []
    shown = dict(zip(texts, clean(texts), strict=True)) if clean is not None and texts else {}
    keyed = [(record_key(str(f.row["text"])), f) for f in found]
    reviews = [{**md.review_entry(f, shown if clean is not None else None), "key": key} for key, f in keyed]
    # 必須の行を先に書く（後の通常の行の失敗で、必須の行が欠けない）
    ordered = sorted(md.one_per_key(reviews), key=lambda e: e["outcome"] not in md.REVIEW_REQUIRED)
    return [{**shared, "kind": md.MVV_REVIEW, "head": head, **e} for e in ordered]


def unwritable(snapshot: Snapshot, rows: Sequence[Mapping[str, Any]], cause: str) -> ReviewWrite:
    """記録を書く前に止まった（呼び手が記録の `repo` を解けない）結果。今の MVV で判定した必須の行をすべて欠けとする。"""
    found = md.judged_findings(rows, snapshot.identity, holds=True)
    keyed = [{"key": record_key(str(f.row["text"])), "outcome": f.outcome} for f in found]
    return ReviewWrite(0, tuple(e for e in md.one_per_key(keyed) if e["outcome"] in md.REVIEW_REQUIRED), cause)


def record_review(
    repo: str,
    pr: int,
    head: str,
    snapshot: Snapshot,
    rows: Sequence[Mapping[str, Any]],
    clean: Optional[Callable[[Sequence[str]], Sequence[str]]],
) -> ReviewWrite:
    """cross-review の指摘 `rows` のうち、MVV `snapshot` で判定したものを MVV の記録へ、見送ったものを改訂の兆候へ書く。

    `repo` は記録の `repo`（`repo_key` の値）。呼び手が解決済みのメインディレクトリから渡し、ここでは git を起動しない。
    自由記述（見送りの理由・必ず人の承認が要る操作の指摘の本文・根拠の記述の誤りが引いた項目）は `clean` に 1 回で通して書く。
    `clean` が None（伏せる所を確かめられなかった）なら、自由記述を書かずに `withheld` を置く。前の起動と同じ結果は書かない。
    書けなくても例外を出さず、記録に無いままの必須の行と原因を返す（#3959。打ち直すと欠けた行だけを書く）。
    """
    found = md.judged_findings(rows, snapshot.identity, holds=True)
    if not found:
        return ReviewWrite(0, (), None)
    entries = _review_rows_of(repo, pr, head, snapshot, found, clean)
    # 通常の指摘を直した記録は、同じキーの保留の記録があるときだけ書く（直した通常の指摘をすべて記録に積まない）
    resolving = frozenset(record_key(str(f.row["text"])) for f in found if f.plain and f.outcome == md.REVIEW_FIXED)
    wrote, left, cause = _append_reviews(entries, resolving)
    missing = tuple(e for e in left if e["outcome"] in md.REVIEW_REQUIRED)
    if cause is not None:
        return ReviewWrite(wrote, missing, cause)
    shared = {k: entries[0][k] for k in ("at", "repo", "pr", "mvv_version", "sha256")}
    disputed = {record_key(str(f.row["text"])): str(f.row["id"]) for f in found if f.outcome == md.REVIEW_DEFERRED and not f.plain}
    more, _, cause = _append_disputed([{**shared, "kind": md.REVIEW_DISPUTED, "key": k, "id": fid} for k, fid in disputed.items()])
    return ReviewWrite(wrote + more, missing, cause)


def _of_pr(entry: Mapping[str, Any]) -> Callable[[Mapping[str, Any]], bool]:
    """`entry` と同じ種類・リポジトリ・PR の記録か。"""
    return lambda row: all(row.get(k) == entry[k] for k in ("kind", "repo", "pr"))


def _append_missing(
    kind: str, entries: list[dict[str, Any]], missing: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
) -> tuple[int, list[dict[str, Any]], Optional[str]]:
    """記録 `kind` をロックし、`entries` と同じ種類・リポジトリ・PR の記録から `missing` が選んだ記録だけを足す。

    足す行を確定してから 1 行ずつ追記する。返すのは足した件数・記録に無いままの行・失敗の原因（無ければ None）。追記の前に
    失敗したら、どの行が記録にあるか分からないので `entries` のすべてを記録に無い行とする。
    """
    step, new, done = "保存先の決定", list(entries), 0
    where = "" if kind == "mvv-validation" else f"{kind}.jsonl の"
    try:
        path = record_path(kind)
        step = "ロックの取得"
        with jsonl.lock(path):
            step = "既存の行の読み取り"
            held, _ = jsonl.read_rows(path, kind=kind, wanted=_of_pr(entries[0]))
            new = missing(held)
            step = "追記"
            for e in new:
                jsonl.append(path, e, kind=kind)
                done += 1
    except _WRITE_ERRORS as exc:
        return done, new[done:], failure_cause(where + step, exc)
    return done, [], None


def _append_reviews(
    entries: list[dict[str, Any]], resolving: frozenset[str] = frozenset()
) -> tuple[int, list[dict[str, Any]], Optional[str]]:
    """指摘の記録のうち、同じキーの最後の記録（時刻の順。同じ時刻なら追記の順）と違うものを足す。キーが `resolving` の記録は、
    同じキーの記録が既にあるときだけ足す。"""

    def missing(held: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last = {r["key"]: r for r in sorted(held, key=lambda r: internal.epoch(r["at"]))}
        wanted = [e for e in entries if e["key"] not in resolving or e["key"] in last]
        return [e for e in wanted if not md.review_recorded(last.get(e["key"]), e)]

    return _append_missing("mvv-validation", entries, missing)


def _append_disputed(entries: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], Optional[str]]:
    """見送りの兆候のうち、同じ MVV で同じキーの兆候がまだ無いものを足す。"""
    if not entries:
        return 0, [], None

    def missing(held: list[dict[str, Any]]) -> list[dict[str, Any]]:
        known = {(r.get("key"), r["mvv_version"], r.get("sha256")) for r in held}
        return [e for e in entries if (e["key"], e["mvv_version"], e["sha256"]) not in known]

    return _append_missing("mvv-signals", entries, missing)


def review_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`validation_rows` の行のうち、cross-review の指摘の記録（種類 `mvv-review`）だけ（#3959）。"""
    return [dict(r) for r in rows if r.get("kind") == md.MVV_REVIEW]


def validation_rows(root: Path) -> tuple[list[dict[str, Any]], list[jsonl.Broken]]:
    """記録 `mvv-validation.jsonl` のすべての種類の行と、読めずに除いた行（判定 1 回につき 1 回だけ読む。#3959）。

    記録がまだ作られていない（保存先のパスに何も無い）ときだけ空を返す。パスに通常のファイルでないものがあれば `OSError`。
    読めなければ `OSError`・`UnicodeDecodeError`・`internal.InternalError`（新しい形式のバージョンの行）をそのまま投げる。
    `root` は判定するリポジトリ（記録の保存先はリポジトリによらない）。
    """
    del root
    path = record_path("mvv-validation")
    if not os.path.lexists(path):
        return [], []
    if not path.is_file():
        raise OSError(f"MVV の記録の保存先がファイルでない: {path}")
    return jsonl.read_rows(path, kind="mvv-validation")


def mvv_validate_sprint(
    root: Path,
    inputs: Mapping[str, str],
    judge: Judge,
    reuse: bool = True,
    rows: Optional[Sequence[Mapping[str, Any]]] = None,
    broken: Sequence[jsonl.Broken] = (),
) -> tuple[dict[str, Any], Optional[str]]:
    """スプリント MVV を MVV 判定し、記録に足した行と、足せなかったときの原因（足せたら None）を返す。

    `reuse` なら、同じ MVV 判定の入力の最新の判定が「従う」の記録を再利用して LLM を呼ばない。再利用の候補は読み済みの行 `rows`
    （同じ読み取りで除いた行 `broken` も渡す）から選び、`rows` が None のときだけ記録を読む。
    同じ入力の判定かもしれない読めない行が後にある判定は再利用しない。`reuse` が偽なら記録を読まない。
    `project-mvv vet` に当たる呼び手は `reuse=False` で毎回 LLM を呼ぶ。追記の `OSError` は外へ出さず、原因として返す。
    """
    key = md.mvv_validation_key(inputs)
    path = record_path("mvv-validation")
    repo = repo_key(root)
    origin = None
    if reuse:
        if rows is None:
            rows, broken = jsonl.read_rows(path, kind="mvv-validation")
        origin = md.reusable(list(rows), key, [(b.before, b.raw) for b in broken])
    if origin is not None:
        row = md.reuse_row(origin, jsonl.now(), repo)
    else:
        verdict, cost = judge(inputs)
        row = md.judged_row(key, verdict, jsonl.now(), repo, cost)
    try:
        return jsonl.append_locked(path, row, kind="mvv-validation"), None
    except OSError as exc:
        return row, str(exc)


# 比較の API が `files` を返す上限の件数（GitHub REST API の「Working with large comparisons」）
COMPARE_FILES_CAP = 300
# 比較の API を読む権限が無いときの HTTP の状態。非公開のリポジトリで権限が無いと 404、明示の拒否は 403（手元の git で読む）
COMPARE_DENIED = (403, 404)
# 手元の git で比較を読み直すときの差分の形（手元の Git の設定で出力の形が変わらないよう固定する。#3959）
LOCAL_DIFF = (
    "--merge-base", "--patch", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
    "--submodule=short", "--src-prefix=a/", "--dst-prefix=b/", "-M",
)  # fmt: skip


def compare_files(hub: GitHub, g: git_mod.Git, remote: str, base: str, head: str) -> list[dict[str, Any]]:
    """コミット `base` と `head`（どちらも SHA）の 3 点の比較で変えたファイル（GitHub の files の行の形）。

    比較の REST を 1 回打つ。ページを指定すると `files` は 1 ページ目に比較の全体の分（`COMPARE_FILES_CAP` 件まで）が載り、
    コミットの一覧だけがページで区切られるので、`page=1&per_page=1` で使わないコミットの一覧を 1 件に抑える。
    `files` が `COMPARE_FILES_CAP` 件に届いたときと、比較の API の認証情報に権限が無い（`COMPARE_DENIED`。非公開のリポジトリで
    Contents の読み取りを持たない API の認証情報。`compare_denied`）ときは、head を取得して同じ組を手元の git（`--merge-base`）で読み直す。
    基点を SHA で固定するので、読む間にマージ先が進んでも差分は変わらない。
    比較を読めなければ `GitHubError`、手元で読めなければ `GitError`・`mvv_material_decide.DiffError`。
    """
    try:
        got = hub.rest("GET", f"/repos/{hub.repo}/compare/{base}...{head}", params={"page": 1, "per_page": 1})
    except GitHubError as exc:
        if not compare_denied(exc):
            raise
        return _local_files(g, remote, base, head)
    files = got.get("files") if isinstance(got, dict) else None
    if not isinstance(files, list):
        raise GitHubError(f"比較 {base}...{head} の応答に files が無い")
    if len(files) < COMPARE_FILES_CAP:
        return files
    return _local_files(g, remote, base, head)


def compare_denied(exc: GitHubError) -> bool:
    """比較の API の失敗が、権限が無いことによる拒否か（手元の git で読み直してよいか）。

    状態が `COMPARE_DENIED` でも、GitHub のアダプターがレート制限と識別した失敗（`quota`・`secondary`・`retry_after` のどれかを
    持つ）は拒否でない。手元の git へ切り替えると、上限に当たるたびに `git fetch` と `git diff` が増えるので、そのまま投げる。
    """
    limited = exc.quota is not None or exc.secondary or exc.retry_after is not None
    return exc.status in COMPARE_DENIED and not limited


def _local_files(g: git_mod.Git, remote: str, base: str, head: str) -> list[dict[str, Any]]:
    """head を取得し、`base` と `head` の組を手元の git で読む（`compare_files` の手元の経路）。"""
    done = g.fetch(remote, head)
    if done.returncode != 0:
        raise git_mod.GitError(f"{remote} から {head} を取得できない: {done.stderr.strip()}")
    return mm.diff_rows(g.diff_between(base, head, *LOCAL_DIFF))


def changed_of(rows: list[dict[str, Any]]) -> list[Changed]:
    """GitHub の files の行 → 機械の検査の変えたファイル（名前を変えた前のパスを持つ）。"""
    return [Changed(str(f["filename"]), f["previous_filename"] if "previous_filename" in f else None,
                    f["status"] == "renamed") for f in rows]  # fmt: skip


def pr_facts(hub: GitHub, number: int, base: MvvFacts, rows: list[dict[str, Any]], pull: Optional[Pull] = None) -> MvvFacts:
    """PR の本文と変えたファイルを読み、機械の検査の事実を埋める（検査 6 の `body`・`claimed`）。

    `rows` は読み済みの変えたファイル（`compare_files` の結果）、`pull` は読み済みの PR（無ければここで読む）。"""
    body = (pull if pull is not None else hub.pull(number)).body
    return mm.facts_from_pr(base, body, changed_of(rows))
