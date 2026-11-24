"""承認済みの MVV のスナップショット（`mvv.current`。#4076・#4090・#4099）。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

import git_sandbox
import pytest
from git_sandbox import sh

from ndf import contracts
from ndf.adapters import git
from ndf.domains.records import mvv
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import preflight

# 作業場所に作る MVV の本文のパス（このリポジトリ自身の MVV の文書は読まない）
NDF_DIR = ".ndf"
BODY_AT = f"{NDF_DIR}/mvv.md"
BODY = """# 見本の MVV

## Mission

使命

## Vision

展望

## Value

### 利用者の価値

1. **一つ目**

    - 例
    - 2. 入れ子の番号

    3. 字下げした番号

2. **二つ目**

## 必ず人の承認が要る操作

| # | 操作 | 理由 |
| --- | --- | --- |
| P1 | 消す | 戻せない |

## 改訂

| A1 | 表の外 | x |

7. 改訂の手順
""".replace("\n", "\r\n")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decl(approved: str, **extra: Any) -> str:
    return json.dumps({"versions": [{"version": 10, "sha256": "x"}, {"version": 11, "sha256": _sha(approved)}], **extra})


def _blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    main = tmp_path / "main"
    sh(tmp_path, "init", "-q", "-b", "main", str(main))
    sh(main, "config", "core.autocrlf", "false")
    return main


def _commit(repo: Path, decl: Optional[str], body: Optional[str] = BODY, body_at: str = BODY_AT) -> str:
    for name, text in ((".ndf/mvv.json", decl), (body_at, body)):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            (repo / name).write_bytes(text.encode("utf-8"))
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "--allow-empty", "-m", "x")
    return sh(repo, "rev-parse", "HEAD", strip=True)


def _snapshots(repo: Path) -> list[Path]:
    return sorted((repo.parent / "mvv-state" / mvv.SNAPSHOT_DIR).glob("*"))


def _drop_object(repo: Path, rev: str) -> None:
    """手元のオブジェクトを 1 つ欠損させる。"""
    oid = sh(repo, "rev-parse", rev, strip=True)
    (repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()


def test_current_snapshots_the_approved_body_of_the_ref_not_of_the_worktree(repo: Path) -> None:
    decl = _decl(BODY)
    head = _commit(repo, decl)
    (repo / BODY_AT).write_text("作業場所で変えた本文", encoding="utf-8")
    got = mvv.current(repo, "main")
    principles = (contracts.contracts_dir() / mvv.PRINCIPLES).read_bytes()
    assert (got.state, got.version, got.sha256, got.commit) == ("approved", 11, _sha(BODY), head)
    assert got.path is not None and got.path.is_absolute() and got.path.read_bytes() == BODY.encode("utf-8")
    assert got.principles_path is not None and got.principles_path.read_bytes() == principles
    assert got.principles_sha256 == hashlib.sha256(principles).hexdigest()
    assert _snapshots(repo) == sorted([got.path, got.principles_path])
    assert got.path.name == f"v11-{_sha(BODY)[:12]}.md"
    assert got.principles_path.name == f"principles-{got.principles_sha256[:12]}.md"
    assert got.items[:5] == ("Mission", "Vision", "Value 1", "Value 2", "P1") and "C1" in got.items
    assert got.blobs == {".ndf/mvv.json": _blob_id(decl.encode("utf-8")), BODY_AT: _blob_id(BODY.encode("utf-8"))}
    assert got.blobs[BODY_AT] == sh(repo, "rev-parse", f"HEAD:{BODY_AT}", strip=True)


def test_current_reads_each_object_once(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _commit(repo, _decl(BODY))
    calls: list[str] = []
    for name in ("run", "run_bytes"):
        real = getattr(git.Git, name)
        monkeypatch.setattr(git.Git, name, lambda self, *a, _real=real: (calls.append(a[0]), _real(self, *a))[1])
    mvv.current(repo, "main")
    assert calls == ["rev-parse", "ls-tree", "cat-file", "cat-file"]


def test_current_keeps_a_matching_snapshot_and_replaces_a_different_one(repo: Path) -> None:
    """4090-10: 同じ名前でも中身を確かめ、同じなら書かず、違えば検証済みの中身で置き換える。"""
    _commit(repo, _decl(BODY))
    first = mvv.current(repo, "main")
    assert first.path is not None and first.principles_path is not None
    inode = first.path.stat().st_ino
    first.principles_path.write_text("別の中身", encoding="utf-8")
    again = mvv.current(repo, "main")
    assert again == first and first.path.stat().st_ino == inode
    assert hashlib.sha256(first.principles_path.read_bytes()).hexdigest() == first.principles_sha256
    assert _snapshots(repo) == sorted([first.path, first.principles_path])


def test_snapshots_are_readable_only_by_the_owner(repo: Path) -> None:
    """umask 022 でも 0600 で作り、前からあった緩い権限のスナップショットは使い回さずに作り直す。"""
    _commit(repo, _decl(BODY))
    before = os.umask(0o022)
    try:
        first = mvv.current(repo, "main")
        assert first.path is not None and [p.stat().st_mode & 0o777 for p in _snapshots(repo)] == [0o600, 0o600]
        first.path.chmod(0o644)
        assert mvv.current(repo, "main") == first and first.path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(before)


LF, CRLF = BODY.replace("\r\n", "\n"), BODY


@pytest.mark.parametrize("approved", [LF, CRLF])
@pytest.mark.parametrize("committed", [LF, CRLF])
@pytest.mark.parametrize("checked_out", [LF, CRLF])
def test_line_ending_conversion_does_not_split_preflight_and_current(repo: Path, approved: str, committed: str, checked_out: str) -> None:
    """`core.autocrlf`・`eol=crlf` で作業場所と blob の改行が違っても、どちらで承認した記録も両方が承認済みにする。"""
    _commit(repo, _decl(approved), committed)
    (repo / BODY_AT).write_bytes(checked_out.encode("utf-8"))
    got = mvv.current(repo, "main")
    assert (preflight.mvv_state(repo), got.state, got.sha256) == ("approved", "approved", _sha(approved))
    assert got.path is not None and got.path.read_bytes() == approved.encode("utf-8")
    assert got.blobs[BODY_AT] == _blob_id(committed.encode("utf-8"))


def test_a_record_of_the_lf_body_covers_mixed_line_endings(repo: Path) -> None:
    """LF にそろえた sha256 の記録は、LF と CRLF が混ざった作業場所と、Git が LF にそろえた blob の両方を承認済みにする。"""
    _commit(repo, _decl(LF), LF)
    (repo / BODY_AT).write_bytes(CRLF.replace("\r\n", "\n", 3).encode("utf-8"))
    assert (preflight.mvv_state(repo), mvv.current(repo, "main").state) == ("approved", "approved")


def test_a_change_other_than_line_endings_is_a_mismatch_for_both(repo: Path) -> None:
    _commit(repo, _decl(LF), CRLF.replace("使命", "別の使命"))
    assert preflight.mvv_state(repo) == "mismatch"
    assert mvv.current(repo, "main", allow_mismatch=True).state == "mismatch"


def test_failed_write_leaves_no_temporary_file(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _commit(repo, _decl(BODY))

    def fail(*args: Any) -> None:
        raise OSError("容量が無い")

    monkeypatch.setattr(mvv.os, "replace", fail)
    with pytest.raises(OSError, match="容量が無い"):
        mvv.current(repo, "main")
    assert _snapshots(repo) == []


def test_current_reads_the_body_at_the_declared_path(repo: Path) -> None:
    """4090-1: 最上位の `body` が既定外でも承認済みを返す（`versions[].body` は本文の場所ではない）。"""
    _commit(repo, _decl(BODY, body="docs/x.md"), body_at=".ndf/docs/x.md")
    got = mvv.current(repo, "main")
    assert got.state == "approved" and list(got.blobs) == [".ndf/mvv.json", ".ndf/docs/x.md"]


def test_current_without_mvv_settings_is_absent(repo: Path) -> None:
    head = _commit(repo, None)
    assert mvv.current(repo, "main") == mvv.Snapshot("absent", None, None, None, (), {}, head, "", None)
    assert _snapshots(repo) == []


@pytest.mark.parametrize("decl", [{"versions": []}, {"versions": [], "body": "../x.md"}])
def test_current_of_an_unapproved_mvv_reads_no_body_and_writes_no_snapshot(repo: Path, decl: dict[str, Any]) -> None:
    """承認の記録が無い設定は、本文が無くても `body` が不正でも `unapproved`（設定だけを先に置いたプロジェクトを止めない）。"""
    head = _commit(repo, json.dumps(decl), body=None)
    got = mvv.current(repo, "main")
    assert got == mvv.Snapshot("unapproved", None, None, None, (), got.blobs, head, "", None)
    assert list(got.blobs) == [".ndf/mvv.json"] and _snapshots(repo) == []


def test_mismatch_stops_unless_it_is_allowed(repo: Path) -> None:
    """4099-2: 承認の後に変わった本文は止める。`allow_mismatch` なら項目もスナップショットも無い `mismatch`。"""
    head = _commit(repo, _decl("承認した本文"))
    with pytest.raises(mvv.MvvError, match=r"ベースの MVV の本文が承認の記録と合わない: \.ndf/mvv\.md"):
        mvv.current(repo, "main")
    got = mvv.current(repo, "main", allow_mismatch=True)
    assert got == mvv.Snapshot("mismatch", None, None, None, (), got.blobs, head, "", None)
    assert _snapshots(repo) == []


def test_unresolved_ref_is_an_error_not_absent(repo: Path) -> None:
    _commit(repo, None)
    with pytest.raises(mvv.MvvError, match="origin/nope"):
        mvv.current(repo, "origin/nope")


@pytest.mark.parametrize("missing", ["HEAD:.ndf/mvv.json", f"HEAD:{BODY_AT}", "HEAD:.ndf", "HEAD^{tree}"])
def test_missing_objects_are_errors_not_absent(repo: Path, missing: str) -> None:
    """4099-2: 設定と本文の blob・ツリーの読み取りの失敗を `absent` に落とさない。"""
    _commit(repo, _decl(BODY))
    _drop_object(repo, missing)
    with pytest.raises(mvv.MvvError):
        mvv.current(repo, "main")


@pytest.mark.parametrize("kind", ["tree", "symlink"])
def test_settings_that_are_not_a_regular_file_are_errors(repo: Path, kind: str) -> None:
    path = repo / ".ndf" / "mvv.json"
    if kind == "tree":
        _commit(repo, None, body_at=".ndf/mvv.json/x")
    else:
        path.parent.mkdir()
        path.symlink_to("mvv.md")
        _commit(repo, None)
    with pytest.raises(mvv.MvvError, match="通常のファイルでない"):
        mvv.current(repo, "main")


@pytest.mark.parametrize(
    "decl",
    ["{", "[]", _decl(BODY, body="../x.md"), _decl(BODY, body="none.md"), json.dumps({"versions": [{"sha256": _sha(BODY)}]})],
)
def test_broken_settings_and_unreadable_bodies_are_errors(repo: Path, decl: str) -> None:
    _commit(repo, decl)
    with pytest.raises(mvv.MvvError):
        mvv.current(repo, "main")
    assert _snapshots(repo) == []


def test_undecodable_settings_and_bodies_are_errors(repo: Path) -> None:
    raw = b"\xff"
    _commit(repo, json.dumps({"versions": [{"version": 1, "sha256": hashlib.sha256(raw).hexdigest()}]}))
    (repo / BODY_AT).write_bytes(raw)
    _commit(repo, None, None)
    with pytest.raises(mvv.MvvError):
        mvv.current(repo, "main")
    (repo / ".ndf" / "mvv.json").write_bytes(raw)
    _commit(repo, None, None)
    with pytest.raises(mvv.MvvError):
        mvv.current(repo, "main")


@pytest.mark.parametrize(("decl", "want"), [({}, BODY_AT), ({"body": 1}, BODY_AT), ({"body": "docs/x.md"}, ".ndf/docs/x.md")])
def test_body_path_resolves_from_the_settings_directory(decl: dict, want: str) -> None:
    assert md.body_path(decl) == want


@pytest.mark.parametrize("body", ["", "/etc/x.md", "../x.md", "docs/../../x.md"])
def test_body_path_refuses_paths_outside_the_settings_directory(body: str) -> None:
    with pytest.raises(mvv.MvvError):
        md.body_path({"body": body})


def test_items_of_lists_numbers_in_order_without_duplicates() -> None:
    principles = "| C1 | 秘密 | x |\n| C2 | 認可 | x |\n| P1 | 重なり | x |\n"
    want = ("Mission", "Vision", "Value 1", "Value 2", "P1", "C1", "C2")
    assert md.items_of(BODY, principles) == want
    assert md.items_of(BODY.replace("\r\n", "\n"), principles) == want


def test_items_of_a_body_without_value_has_no_value_items() -> None:
    assert md.items_of("# x\n\n## Mission\n\n1. 番号\n", "") == ("Mission",)


def test_principles_are_the_bytes_of_the_contract() -> None:
    assert mvv.principles() == (contracts.contracts_dir() / mvv.PRINCIPLES).read_bytes()
