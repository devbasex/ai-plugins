"""MVV の記録を読み書きする口（N8b・#2603・#2614・#2777・#2970・#2853）。

記録は状態の保存先の mvv-state（表 locations）の下の JSONL で、追記だけで持つ。記録の `repo` はメインディレクトリの
パスにする（worktree から書いても同じキー）。改訂の兆候は、ベースブランチの設定を読んで数える。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, GitHubError, Pull
from ndf.contracts import facts, limits
from ndf.domains.records import files, jsonl
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.domains.records import usage_decide as ud
from ndf.domains.records.mvv_decide import MvvError
from ndf.domains.settings import renames
from ndf.progress.gate.checks import Changed, MvvFacts

# LLM の判定の関数: MVV 判定の入力を受け、判定の語と費用・秒の実測値を返す
Judge = Callable[[Mapping[str, str]], tuple[str, Mapping[str, Any]]]


# 共通原則の本文（コントラクトの保存先からの相対）
PRINCIPLES = "data/ndf-common-principles.md"
# 本文と共通原則のスナップショットのディレクトリ（`_state_dir()` の下）
SNAPSHOT_DIR = "snapshots"
# ファイル名に入れる sha256 の先頭の文字数
_NAME_SHA = 12


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


def _state_dir() -> Path:
    var = facts.value("locations", "mvv-state", "env")
    # 環境変数があれば identity を読まない。状態の保存先を解けなければ IdentityError のまま
    state = None if os.environ.get(var) else identity.load().state_dir
    return ud.state_location_dir(os.environ, var, state, facts.value("locations", "mvv-state", "path"))


def record_path(file_id: str) -> Path:
    """記録のファイル（表 ndf-files の mvv-validation・mvv-signals）。"""
    return _state_dir() / facts.value("ndf-files", file_id, "path")


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


def _is_private(path: Path) -> bool:
    """`path` が所有者のほかに権限を与えていない通常のファイルか（無ければ偽。リンクは辿らない）。"""
    if not os.path.lexists(path):
        return False
    mode = path.lstat().st_mode
    return stat.S_ISREG(mode) and stat.S_IMODE(mode) & 0o077 == 0


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


def current(root: Path, ref: str, allow_mismatch: bool = False) -> Snapshot:
    """参照 `ref`（取得済みの参照かコミット）の MVV を読み、承認済みなら本文と共通原則のスナップショットを書いて返す。

    設定がツリーに無いと確かめられたときだけ `absent`。参照・設定・本文を読めないときと、本文が承認の記録と合わない
    とき（`allow_mismatch` でない）は `MvvError`、スナップショットを書けないときは `OSError`。
    """
    g = git_mod.Git(root)
    commit = _commit_of(g, ref)
    held = _decl_blob(g, commit)
    if held is None:
        return Snapshot(md.State.ABSENT, None, None, None, (), {}, commit, "", None)
    raw = _blob(g, held, f"{commit} の MVV の設定（{decl_path()}）")
    decl = decl_from(raw, commit, root)
    if md.status(decl, None) == md.State.UNAPPROVED:
        return Snapshot(md.State.UNAPPROVED, None, None, None, (), {decl_path(): _blob_id(raw)}, commit, "", None)
    path = md.body_path(decl)
    body = _blob(g, f"{commit}:{path}", f"{commit} の MVV の本文（{path}）")
    blobs = {decl_path(): _blob_id(raw), path: _blob_id(body)}
    body = md.approved_form(decl, body)
    sha = _sha256(body)
    state = md.status(decl, sha)
    if state == md.State.MISMATCH and not allow_mismatch:
        raise MvvError(f"ベースの MVV の本文が承認の記録と合わない: {path}")
    if state != md.State.APPROVED:
        return Snapshot(state, None, None, None, (), blobs, commit, "", None)
    version = md.current_version(decl)
    if version is None:
        raise MvvError(f"{commit} の MVV の設定の最後のバージョンに番号が無い: {decl_path()}")
    shared = principles()
    items = md.items_of(_text(body, path), shared.decode("utf-8"))
    shared_sha, where = _sha256(shared), (_state_dir() / SNAPSHOT_DIR).absolute()
    body_copy = _write_verified(where / f"v{version}-{sha[:_NAME_SHA]}.md", body, sha)
    shared_copy = _write_verified(where / f"principles-{shared_sha[:_NAME_SHA]}.md", shared, shared_sha)
    return Snapshot(state, version, sha, body_copy, items, blobs, commit, shared_sha, shared_copy)


def _text(body: bytes, path: str) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MvvError(f"MVV の本文 {path} を UTF-8 で読めない: {exc}") from exc


def signals(root: Path, base_branch: str) -> md.Signals:
    """ベースブランチの設定の現行のバージョンで、改訂の兆候を数える（本番のブランチの古いバージョンで数えない）。"""
    decl = decl_at(root, md.signals_ref(base_branch, git_mod.Git(root).remote()))
    rows, _ = jsonl.read_rows(record_path("mvv-signals"))
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
    rows = [{"at": at, "kind": kind, "version": got, "repo": repo, **pair} for kind in kinds]
    path = record_path("mvv-signals")
    for row in rows:
        jsonl.append_locked(path, row)
    return rows


def mvv_validate_sprint(root: Path, inputs: Mapping[str, str], judge: Judge, reuse: bool = True) -> dict[str, Any]:
    """スプリント MVV を MVV 判定し、記録に足した行を返す。

    `reuse` なら、同じ MVV 判定の入力の最新の判定が「従う」の記録を再利用して LLM を呼ばない。記録が無い・壊れた行を
    含んでも止まらず LLM で判定する。`project-mvv vet` に当たる呼び手は `reuse=False` で毎回 LLM を呼ぶ。
    """
    key = md.mvv_validation_key(inputs)
    path = record_path("mvv-validation")
    repo = repo_key(root)
    rows, _ = jsonl.read_rows(path)
    origin = md.reusable(rows, key) if reuse else None
    if origin is not None:
        row = md.reuse_row(origin, jsonl.now(), repo)
    else:
        verdict, cost = judge(inputs)
        row = md.judged_row(key, verdict, jsonl.now(), repo, cost)
    jsonl.append_locked(path, row)
    return row


def pr_files(hub: GitHub, number: int) -> list[dict[str, Any]]:
    """PR の変えたファイル（GitHub の files の行。`github.list-pages` ページまで）。超えれば `GitHubError`。"""
    pages = int(limits.value("github.list-pages", "count"))
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        got = hub.rest("GET", f"/repos/{hub.repo}/pulls/{int(number)}/files", params={"per_page": PER_PAGE, "page": page})
        out += got
        if len(got) < PER_PAGE:
            return out
    raise GitHubError(f"PR #{number} の変更ファイルが {pages} ページを超えた（github.list-pages）")


def changed_of(rows: list[dict[str, Any]]) -> list[Changed]:
    """GitHub の files の行 → 機械の検査の変えたファイル（名前を変えた前のパスを持つ）。"""
    return [Changed(str(f["filename"]), f["previous_filename"] if "previous_filename" in f else None,
                    f["status"] == "renamed") for f in rows]  # fmt: skip


def pr_facts(
    hub: GitHub, number: int, base: MvvFacts, rows: Optional[list[dict[str, Any]]] = None, pull: Optional[Pull] = None
) -> MvvFacts:
    """PR の本文と変えたファイルを読み、機械の検査の事実を埋める（検査 6 の `body`・`claimed`）。

    `rows` は読み済みの変えたファイル（`pr_files` の結果）、`pull` は読み済みの PR。無ければここで読む。"""
    body = (pull if pull is not None else hub.pull(number)).body
    return mm.facts_from_pr(base, body, changed_of(rows if rows is not None else pr_files(hub, number)))
