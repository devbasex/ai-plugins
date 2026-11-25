"""MVV 判定の失敗の原因（`ndf records mvv gate` の `failure_text`・`record_failure`。#3959）。

利用者へ返す文は定型文だけにし、例外の中身は手元の診断の記録（0600 のファイル）へだけ書くことを確かめる。
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import git_sandbox
import pytest

from ndf.adapters import git
from ndf.domains.records import mvv, mvv_gate
from ndf.domains.records import mvv_material_decide as mm

from ._mvv_gate_records import HOST
from ._mvv_gate_records import failures as _failures


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """診断の記録の保存先（`NDF_MVV_STATE_DIR`）と、記録の `repo` を解く Git のリポジトリ。"""
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    root = tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(root))
    return root


# --- #3959 Task 9: 失敗の原因を定型文と手元の診断の記録に分ける ---


def test_failure_text_keeps_the_exception_only_in_the_local_record(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_TEST_TOKEN", "zz-fake-secret-77")
    exc = git.GitError(f"fetch https://x-access-token:zz-fake-secret-77@{HOST}/team/repo.git に失敗した")
    got = mvv_gate.failure_text("base_unreadable", exc, {"gate": "design", "pr": 5, "root": repo})
    (row,) = _failures()
    assert got == f"{mm.STOP_REASONS['base_unreadable']}（診断の記録 {row['id']}）"
    assert HOST not in got and "team/repo" not in got and "zz-fake" not in got
    assert (row["kind"], row["gate"], row["pr"], row["head"], row["repo"]) == ("base_unreadable", "design", 5, None, mvv.repo_key(repo))
    assert HOST in row["cause"] and "zz-fake-secret-77" not in row["cause"]
    # 同じ失敗でも呼ぶたびに新しい行（別の識別子）を書き、空の原因も空のまま残す
    first = mvv_gate.failure_text("llm_no_answer", "", {"gate": "release"})
    second = mvv_gate.failure_text("llm_no_answer", "", {"gate": "release"})
    rows = _failures()[1:]
    assert first != second and [r["cause"] for r in rows] == ["", ""] and len({r["id"] for r in rows}) == 2


def test_failure_text_does_not_raise_when_the_record_cannot_be_written(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("ファイルなのでディレクトリにできない")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(blocked / "state"))
    got = mvv_gate.failure_text("diff_unreadable", OSError(f"/home/someone/{HOST}"), {})
    assert got == f"{mm.STOP_REASONS['diff_unreadable']}（診断の記録に書けない）"


def test_every_stop_reason_is_a_fixed_sentence_without_values_from_the_pr() -> None:
    assert set(mm.STOP_REASONS) == {
        "no_gate", "no_pr", "mvv_unapproved", "base_unreadable", "diff_unreadable", "pr_moved", "pr_reread_failed",
        "incomplete_patches", "llm_no_answer", "records_unreadable", "records_unwritable", "note_unwritten",
        "cite_precondition", "cite_lookup_failed",
    }  # fmt: skip
    assert all(text and "{" not in text for text in mm.STOP_REASONS.values())


# --- #3959: 診断の記録は 0600 に限ったファイル記述子へだけ追記する（record_failure） ---


class _Fd:
    """`os.open`・`os.fstat`・`os.fchmod`・`os.write` を、診断の記録のパスとその記述子だけ差し替える（錠のファイルは本物へ通す）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail_open: Any = None, st: Any = None, fchmod_error: Any = None) -> None:
        self.path = str(mvv.record_path("mvv-gate-failures"))
        self.opened: list[tuple[int, int]] = []
        self.written: list[int] = []
        real_open, real_fstat, real_fchmod, real_write = os.open, os.fstat, os.fchmod, os.write

        def fake_open(path: Any, flags: int, mode: int = 0o777, **kw: Any) -> int:
            if str(path) != self.path:
                return real_open(path, flags, mode, **kw)
            if fail_open is not None:
                raise fail_open
            fd = real_open(path, flags, mode, **kw)
            self.opened.append((fd, flags))
            return fd

        def fake_fstat(fd: int) -> Any:
            got = real_fstat(fd)
            if st is None or fd not in self.fds():
                return got
            return SimpleNamespace(st_mode=st.get("st_mode", got.st_mode), st_uid=st.get("st_uid", got.st_uid))

        def fake_fchmod(fd: int, mode: int) -> None:
            if fchmod_error is not None and fd in self.fds():
                raise fchmod_error
            real_fchmod(fd, mode)

        def fake_write(fd: int, data: bytes) -> int:
            if fd in self.fds():
                self.written.append(fd)
            return real_write(fd, data)

        for name, fn in (("open", fake_open), ("fstat", fake_fstat), ("fchmod", fake_fchmod), ("write", fake_write)):
            monkeypatch.setattr(os, name, fn)

    def fds(self) -> set[int]:
        return {fd for fd, _ in self.opened}


def _unwritable(got: str) -> bool:
    return got == f"{mm.STOP_REASONS['base_unreadable']}（診断の記録に書けない）"


@pytest.fixture
def umask_022() -> Any:
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_record_failure_creates_the_directory_0700_and_the_file_0600(repo: Path, umask_022: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fd = _Fd(monkeypatch)
    got = mvv_gate.failure_text("base_unreadable", f"fetch {HOST}", {"gate": "design"})
    path = mvv.record_path("mvv-gate-failures")
    assert not _unwritable(got) and HOST in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700 and stat.S_IMODE(path.stat().st_mode) == 0o600
    # 権限を確かめた記述子へ書く（パスを開き直さない）。開くときは FIFO で止まらず、リンクをたどらない
    ((opened, flags),) = fd.opened
    assert fd.written and set(fd.written) == {opened} and flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW


def test_record_failure_narrows_an_existing_wider_file(repo: Path, umask_022: None) -> None:
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True)
    path.write_text("")
    path.chmod(0o644)
    mvv_gate.failure_text("base_unreadable", HOST, {})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and HOST in path.read_text(encoding="utf-8")


def _existing(mode: int = 0o644) -> Path:
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("前の行\n")
    path.chmod(mode)
    return path


@pytest.mark.parametrize(
    "stubbed",
    [
        {"fchmod_error": PermissionError(1, "許されない")},
        {"st": {"st_uid": os.geteuid() + 1}},
        {"st": {"st_mode": stat.S_IFIFO | 0o600}},
        # 本物の FIFO は作らない。読み手の無い FIFO を O_NONBLOCK で開いたときの ENXIO をスタブで返す（待たずに失敗する）
        {"fail_open": OSError(errno.ENXIO, "No such device or address")},
    ],
)
def test_record_failure_writes_nothing_when_the_file_cannot_be_kept_private(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stubbed: dict[str, Any]
) -> None:
    path = _existing()
    _Fd(monkeypatch, **stubbed)
    got = mvv_gate.failure_text("base_unreadable", HOST, {})
    assert _unwritable(got) and path.read_text(encoding="utf-8") == "前の行\n"


def test_record_failure_does_not_follow_a_symlink(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "other.txt"
    target.write_text("別のファイル\n")
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    assert _unwritable(mvv_gate.failure_text("base_unreadable", HOST, {}))
    assert target.read_text(encoding="utf-8") == "別のファイル\n"
