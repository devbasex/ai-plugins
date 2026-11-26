"""報告の保存先の用意・追記・読み取りのサブコマンド（`ndf worker prepare`・`append`・`collect`。#3877）。"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import causes, worker_paths
from ndf.core import durable_locks, worker, worker_append


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = worker.main(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_second_prepare_clears_the_first_done_and_result(tmp_path, capsys):
    d = tmp_path / "w"
    _, first = _run(capsys, "prepare", "--dir", str(d), "--name", "n")
    paths = first["metrics"]["paths"]
    for key, text in (("report", "1 回目の報告"), ("done", ""), ("result", "{}")):
        Path(paths[key]).write_text(text, encoding="utf-8")
    code, second = _run(capsys, "prepare", "--dir", str(d), "--name", "n")
    assert code == 0 and second["status"] == "ok"
    assert second["metrics"]["paths"] == paths
    assert not (d / "n.report.md.done").exists()
    assert not (d / "n.result.json").exists()
    assert (d / "n.report.md").read_text(encoding="utf-8") == ""


def _invalid(code: int, out: dict) -> bool:
    return out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid")


def test_prepare_returns_three_absolute_paths_in_the_same_directory(tmp_path, capsys):
    code, out = _run(capsys, "prepare", "--dir", str(tmp_path), "--name", "w-1")
    paths = {k: Path(v) for k, v in out["metrics"]["paths"].items()}
    assert code == 0 and out["status"] == "ok" and set(paths) == {"report", "done", "result"}
    assert all(p.is_absolute() and p.parent == tmp_path for p in paths.values())
    assert paths["report"].stat().st_size == 0
    assert not paths["done"].exists() and not paths["result"].exists()
    assert [(i["kind"], i["action"]) for i in out["items"]] == [("report", "created"), ("done", "absent"), ("result", "absent")]


def test_prepare_creates_the_directory_from_a_relative_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, out = _run(capsys, "prepare", "--dir", "a/b", "--name", "n")
    assert code == 0 and out["metrics"]["paths"]["report"] == str(tmp_path / "a" / "b" / "n.report.md")


@pytest.mark.parametrize("name", ["../x", ""])
def test_prepare_refuses_a_bad_name_before_making_the_directory(tmp_path, capsys, name):
    d = tmp_path / "w"
    code, out = _run(capsys, "prepare", "--dir", str(d), "--name", name)
    assert _invalid(code, out) and "paths" not in out.get("metrics", {})
    assert not d.exists()


def test_prepare_refuses_a_file_in_place_of_the_directory(tmp_path, capsys):
    (tmp_path / "w").write_text("x", encoding="utf-8")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(tmp_path / "w"), "--name", "n"))


def _previous(d: Path) -> None:
    d.mkdir()
    (d / "n.report.md").write_text("前の報告", encoding="utf-8")


@pytest.mark.parametrize("entry", ["n.report.md", "n.report.md.done", "n.result.json"])
@pytest.mark.parametrize("link", [os.symlink, os.link])
def test_prepare_leaves_a_link_and_its_target_alone(tmp_path, capsys, entry, link):
    d = tmp_path / "w"
    _previous(d)
    outside = tmp_path / "outside.txt"
    outside.write_text("利用者のファイル", encoding="utf-8")
    (d / entry).unlink(missing_ok=True)
    link(outside, d / entry)
    code, out = _run(capsys, "prepare", "--dir", str(d), "--name", "n")
    assert _invalid(code, out) and entry in out["summary"]
    assert outside.read_text(encoding="utf-8") == "利用者のファイル"
    assert (d / entry).exists()
    if entry != "n.report.md":
        assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告"


def _fail_on(monkeypatch, fn: str, name: str) -> None:
    real = getattr(worker_paths, fn)

    def failing(wd, entry, *rest, **kw):
        if entry == name:
            raise PermissionError(13, "Permission denied", entry)
        return real(wd, entry, *rest, **kw)

    monkeypatch.setattr(worker_paths, fn, failing)


def test_prepare_keeps_the_previous_done_with_its_report_when_the_done_cannot_go(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    _fail_on(monkeypatch, "_retire", "n.report.md.done")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告" and (d / "n.report.md.done").exists()


def test_prepare_restores_the_done_when_the_result_cannot_go(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    (d / "n.result.json").write_text("{}", encoding="utf-8")
    _fail_on(monkeypatch, "_retire", "n.result.json")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告" and (d / "n.report.md.done").exists()
    assert sorted(p.name for p in d.iterdir()) == ["n.report.md", "n.report.md.done", "n.result.json"]


def test_prepare_restores_the_done_and_result_when_the_report_cannot_be_published(tmp_path, monkeypatch, capsys):
    # sticky bit の所有者の制約などで、一時ファイルを作れても最後の置き換えが落ちる。退けた完了マーカーと結果ファイルを戻す
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    (d / "n.result.json").write_text("{}", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "replace", refuse)
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告"
    assert (d / "n.report.md.done").exists() and (d / "n.result.json").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in d.iterdir()) == ["n.report.md", "n.report.md.done", "n.result.json"]


def test_prepare_keeps_the_previous_report_done_and_result_when_the_report_cannot_be_staged(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    (d / "n.result.json").write_text("{}", encoding="utf-8")
    _fail_on(monkeypatch, "staged_write", "n.report.md")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告"
    assert (d / "n.report.md.done").exists() and (d / "n.result.json").read_text(encoding="utf-8") == "{}"


def _prepared(tmp_path, capsys) -> Path:
    _, out = _run(capsys, "prepare", "--dir", str(tmp_path / "w"), "--name", "n")
    return Path(out["metrics"]["paths"]["report"])


def _append(capsys, monkeypatch, report: Path, body: str, *flags: str) -> tuple[int, dict]:
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    return _run(capsys, "append", "--report", str(report), *flags)


def test_append_adds_the_body_and_makes_an_empty_done(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    code, out = _append(capsys, monkeypatch, report, "## 作業の報告\n終えた\n", "--done")
    assert code == 0 and out["status"] == "ok"
    assert report.read_text(encoding="utf-8") == "## 作業の報告\n終えた\n"
    assert Path(f"{report}.done").stat().st_size == 0


def test_append_keeps_both_bodies_in_order_without_a_done(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "1 つ目\n")
    _append(capsys, monkeypatch, report, "2 つ目\n")
    assert report.read_text(encoding="utf-8") == "1 つ目\n2 つ目\n"
    assert not Path(f"{report}.done").exists()


@pytest.mark.parametrize("entry", ["n.report.md", "n.report.md.done"])
@pytest.mark.parametrize("link", [os.symlink, os.link])
def test_append_leaves_a_link_target_alone(tmp_path, capsys, monkeypatch, entry, link):
    report = _prepared(tmp_path, capsys)
    outside = tmp_path / "outside.txt"
    outside.write_text("利用者のファイル", encoding="utf-8")
    (report.parent / entry).unlink(missing_ok=True)
    link(outside, report.parent / entry)
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n", "--done"))
    assert outside.read_text(encoding="utf-8") == "利用者のファイル"


def test_append_makes_no_done_when_the_report_cannot_be_written(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _fail_on(monkeypatch, "write_file", "n.report.md")
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n", "--done"))
    assert not Path(f"{report}.done").exists()


def test_append_reads_again_when_another_append_published_before_the_lock(tmp_path, capsys, monkeypatch):
    # PR 4325 の r1-8: ロックを取るまでに別の追記が公開したら、その後ろへ足す（報告はロックを取ってから読む）
    report = _prepared(tmp_path, capsys)
    real, published = worker_append._try_lock, []

    def lock_after_another(fd):
        if not published:
            published.append(True)
            with worker_paths.open_workdir(report.parent) as wd:
                worker_paths.write_file(wd, report.name, "先の追記\n")
        return real(fd)

    monkeypatch.setattr(worker_append, "_try_lock", lock_after_another)
    code, _ = _append(capsys, monkeypatch, report, "後の追記\n", "--done")
    assert code == 0 and report.read_text(encoding="utf-8") == "先の追記\n後の追記\n"


def test_append_stops_without_writing_while_another_append_holds_the_report(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    monkeypatch.setattr(worker_append, "_try_lock", lambda fd: False)
    monkeypatch.setattr(worker_append.durable_locks, "lock_wait_seconds", lambda: 0.0)
    code, out = _append(capsys, monkeypatch, report, "本文\n", "--done")
    assert out["cause"]["kind"] == "store_in_use" and code == causes.exit_code("store_in_use")
    assert report.read_text(encoding="utf-8") == "" and not Path(f"{report}.done").exists()


def _lock_file() -> Path:
    return identity.load().state_dir / worker_append.LOCK_FILE


def test_append_locks_a_private_file_outside_the_report_directory(tmp_path, capsys, monkeypatch):
    # PR 4325 の r2-2・r3-2: 報告の保存先を書き込める利用者もロックを開けず消せない。ロックの inode は公開をまたいで同じ
    report = _prepared(tmp_path, capsys)
    report.parent.chmod(0o777)
    _append(capsys, monkeypatch, report, "1 つ目\n")
    first = _lock_file().stat()
    code, _ = _append(capsys, monkeypatch, report, "2 つ目\n", "--done")
    assert code == 0 and report.read_text(encoding="utf-8") == "1 つ目\n2 つ目\n"
    assert first.st_mode & 0o077 == 0 and first.st_nlink == 1 and _lock_file().stat().st_ino == first.st_ino
    assert not list(report.parent.glob("*lock*")) and not list(_lock_file().parent.glob(".ndf-*"))


# 現状固定（4367 の I-001: 追記のロックを待つループを `durable_locks` の 1 つの関数へまとめる前）。実物の `flock` で待たせる


@contextlib.contextmanager
def _another_append():
    """別に開いたロックのファイルで、追記のロックを排他で持つ（報告を 1 度書いた後に呼ぶ）。"""
    with open(_lock_file(), "r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield held


@pytest.mark.parametrize("limit", [0.0, 0.1])
def test_append_waits_to_the_limit_and_names_the_report(tmp_path, capsys, monkeypatch, clock, limit):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "1 つ目\n")
    monkeypatch.setattr(durable_locks, "lock_wait_seconds", lambda: limit)
    paths = worker_paths.report_paths(report.parent, "n", resolve=False)
    with _another_append(), worker_paths.open_workdir(report.parent) as wd:
        with pytest.raises(durable_locks.StoreInUse) as err:
            worker_append.append(wd, paths, "2 つ目\n", done=True)
        code, out = _append(capsys, monkeypatch, report, "2 つ目\n", "--done")
    assert str(err.value) == f"報告のロックを {limit:g} 秒待っても取れない: {report}" and err.value.paths == (report,)
    assert out["cause"]["kind"] == "store_in_use" and code == causes.exit_code("store_in_use")
    assert set(clock.sleeps) == ({durable_locks.LOCK_POLL_SECONDS} if limit else set())
    assert report.read_text(encoding="utf-8") == "1 つ目\n" and not Path(f"{report}.done").exists()
    # 取れなかった追記はロックを残さず、放された後の追記は通る
    assert _append(capsys, monkeypatch, report, "3 つ目\n")[0] == 0
    assert report.read_text(encoding="utf-8") == "1 つ目\n3 つ目\n"


def test_append_goes_on_after_another_append_publishes_and_releases_during_the_last_wait(tmp_path, capsys, monkeypatch, clock):
    # 上限を越える待ちの後にも 1 回試す。報告はロックを取ってから読むので、待つ間に公開された本文の後ろへ足す
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "")
    monkeypatch.setattr(durable_locks, "lock_wait_seconds", lambda: durable_locks.LOCK_POLL_SECONDS / 2)
    with _another_append() as held:

        def publish_and_release():
            with worker_paths.open_workdir(report.parent) as wd:
                worker_paths.write_file(wd, report.name, "先の追記\n")
            fcntl.flock(held, fcntl.LOCK_UN)

        clock.on_sleep = publish_and_release
        code, _ = _append(capsys, monkeypatch, report, "後の追記\n", "--done")
    assert code == 0 and report.read_text(encoding="utf-8") == "先の追記\n後の追記\n"
    assert clock.sleeps == [durable_locks.LOCK_POLL_SECONDS] and Path(f"{report}.done").exists()


def test_append_holds_the_lock_while_writing_and_releases_it_on_failure(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    tried = []

    def fail(wd, name, text):
        with pytest.raises(BlockingIOError), _another_append():
            pass
        tried.append(name)
        raise worker_paths.EntryError("書けない")

    monkeypatch.setattr(worker_paths, "write_file", fail)
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n"))
    assert tried == [report.name]
    with _another_append():
        pass


def test_append_does_not_record_the_owner_in_the_lock(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "本文\n", "--done")
    assert _lock_file().stat().st_size == 0


def test_append_refuses_a_lock_others_can_open_or_a_link(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    lock = _lock_file()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o644)
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n"))
    lock.unlink()
    lock.symlink_to(tmp_path / "outside")
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n"))
    assert report.read_text(encoding="utf-8") == "" and not (tmp_path / "outside").exists()


def test_append_refuses_a_lock_others_can_remove(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    state = _lock_file().parent
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o777)
    assert _invalid(*_append(capsys, monkeypatch, report, "本文\n"))
    assert report.read_text(encoding="utf-8") == ""


def test_an_already_published_lock_is_not_replaced(tmp_path):
    # PR 4325 の r3-1: 間に別の追記が公開した名前を上書きしない
    with worker_paths.open_workdir(tmp_path) as wd:
        os.close(worker_paths.open_lock(wd, "lock"))
        first = (tmp_path / "lock").stat().st_ino
        worker_paths._publish_lock(wd, "lock")
    assert (tmp_path / "lock").stat().st_ino == first


def test_append_refuses_a_report_that_was_not_prepared(tmp_path, capsys, monkeypatch):
    code, out = _append(capsys, monkeypatch, tmp_path / "n.report.md", "本文\n", "--done")
    assert out["cause"]["kind"] == "precondition" and code == causes.exit_code("precondition")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cmd", ["append", "collect"])
def test_report_path_must_end_with_the_report_suffix(tmp_path, capsys, monkeypatch, cmd):
    monkeypatch.setattr("sys.stdin", io.StringIO("本文\n"))
    assert _invalid(*_run(capsys, cmd, "--report", str(tmp_path / "n.txt")))


def test_collect_returns_the_last_report_section(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "長い出力\n## 作業の報告\n古い\n## 作業の報告\n- 結果: 完了\n", "--done")
    code, out = _run(capsys, "collect", "--report", str(report))
    assert code == 0 and out["status"] == "ok"
    assert out["metrics"]["report"] == "## 作業の報告\n- 結果: 完了\n"


def test_collect_waits_for_the_done(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "## 作業の報告\n途中\n")
    code, out = _run(capsys, "collect", "--report", str(report))
    assert out["cause"]["kind"] == "precondition" and code == causes.exit_code("precondition")


def test_collect_needs_the_report_heading(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "見出しの無い本文\n", "--done")
    _, out = _run(capsys, "collect", "--report", str(report))
    assert out["cause"]["kind"] == "precondition"


def test_collect_does_not_read_through_a_link(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "", "--done")
    outside = tmp_path / "outside.md"
    outside.write_text("## 作業の報告\n利用者の秘密\n", encoding="utf-8")
    report.unlink()
    report.symlink_to(outside)
    code, out = _run(capsys, "collect", "--report", str(report))
    assert _invalid(code, out) and "利用者の秘密" not in json.dumps(out, ensure_ascii=False)


def test_collect_needs_the_report_itself(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    _append(capsys, monkeypatch, report, "## 作業の報告\n終えた\n", "--done")
    report.unlink()
    _, out = _run(capsys, "collect", "--report", str(report))
    assert out["cause"]["kind"] == "precondition" and "作業の報告" not in out["summary"]


def _swap_after_resolving(monkeypatch, shared, moved, other):
    """`prepare` が `--dir` を解いた直後に、あった親 `shared` を `other` へのリンクへ差し替える。"""
    resolved = worker_paths.prepare_paths

    def swap(directory, name):
        out = resolved(directory, name)
        shared.rename(moved)
        shared.symlink_to(other)
        return out

    monkeypatch.setattr(worker_paths, "prepare_paths", swap)


def test_prepare_does_not_reset_another_directory_when_the_parent_is_swapped(tmp_path, capsys, monkeypatch):
    shared = tmp_path / "shared"
    (shared / "d").mkdir(parents=True)
    other = tmp_path / "other"
    (other / "d").mkdir(parents=True)
    (other / "d" / "n.result.json").write_text("{}", encoding="utf-8")
    _swap_after_resolving(monkeypatch, shared, tmp_path / "moved", other)
    code, out = _run(capsys, "prepare", "--dir", str(shared / "d"), "--name", "n")
    assert _invalid(code, out) and "paths" not in out.get("metrics", {})
    assert (other / "d" / "n.result.json").read_text(encoding="utf-8") == "{}"


def test_collect_skips_a_heading_that_only_starts_like_the_report_heading(tmp_path, capsys, monkeypatch):
    report = _prepared(tmp_path, capsys)
    body = "## 作業の報告\n- 作業: 調査\n- 結果: 完了\n## 作業の報告の補足\n補足\n"
    _append(capsys, monkeypatch, report, body, "--done")
    code, out = _run(capsys, "collect", "--report", str(report))
    assert code == 0 and out["metrics"]["report"] == body


def test_prepare_does_not_reset_own_directory_renamed_over_a_created_name(tmp_path, capsys, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    for name, text in (("n.report.md", "別の作業の報告"), ("n.report.md.done", ""), ("n.result.json", "{}")):
        (old / name).write_text(text, encoding="utf-8")
    real_mkdir = worker_paths.os.mkdir

    def swap(part, *args, dir_fd=None, **kw):  # 作った直後に、同じ親の中で既存のディレクトリを同じ名前へ改名する
        real_mkdir(part, *args, dir_fd=dir_fd, **kw)
        if part == "new":
            os.rmdir(part, dir_fd=dir_fd)
            old.rename(tmp_path / "new")

    monkeypatch.setattr(worker_paths.os, "mkdir", swap)
    code, out = _run(capsys, "prepare", "--dir", str(tmp_path / "new"), "--name", "n")
    assert _invalid(code, out) and "paths" not in out.get("metrics", {})
    moved = tmp_path / "new"
    assert (moved / "n.report.md").read_text(encoding="utf-8") == "別の作業の報告"
    assert (moved / "n.report.md.done").exists() and (moved / "n.result.json").exists()


def test_append_and_collect_refuse_a_prepared_parent_swapped_to_a_link(tmp_path, capsys, monkeypatch):
    shared = tmp_path.resolve() / "shared"
    report = _prepared(shared, capsys)
    other = tmp_path / "other"
    (other / report.parent.relative_to(shared)).mkdir(parents=True)
    copied = other / report.relative_to(shared)
    copied.write_text("## 作業の報告\n別の場所の報告\n", encoding="utf-8")
    (copied.parent / (copied.name + ".done")).write_text("", encoding="utf-8")
    shared.rename(tmp_path / "moved")
    shared.symlink_to(other)
    monkeypatch.setattr("sys.stdin", io.StringIO("## 作業の報告\n書き換え\n"))
    code, out = _run(capsys, "append", "--report", str(report), "--done")
    assert _invalid(code, out)
    code, out = _run(capsys, "collect", "--report", str(report))
    assert _invalid(code, out) and "別の場所の報告" not in json.dumps(out, ensure_ascii=False)
    assert copied.read_text(encoding="utf-8") == "## 作業の報告\n別の場所の報告\n"


def test_prepare_does_not_create_directories_under_a_parent_swapped_to_a_link(tmp_path, capsys, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _swap_after_resolving(monkeypatch, shared, tmp_path / "moved", other)
    code, out = _run(capsys, "prepare", "--dir", str(shared / "new" / "d"), "--name", "n")
    assert _invalid(code, out) and list(other.iterdir()) == []


@pytest.mark.parametrize("same", [True, False])
def test_append_checks_the_result_dir_handed_by_worker_run(tmp_path, capsys, monkeypatch, same):
    # PR 4307 の r8-5: worker run が開いた保存先と違うディレクトリ（同名への差し替え）へ報告と完了マーカーを書かない
    report = _prepared(tmp_path, capsys)
    st = (report.parent if same else tmp_path).stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{report.parent}")
    code, out = _append(capsys, monkeypatch, report, "## 作業の報告\n終えた\n", "--done")
    if same:
        assert code == 0 and out["status"] == "ok" and Path(f"{report}.done").exists()
    else:
        assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
        assert report.read_text(encoding="utf-8") == "" and not Path(f"{report}.done").exists()


@pytest.mark.parametrize("same", [True, False])
def test_draft_writes_the_pr_body_only_into_the_result_dir_worker_run_opened(tmp_path, capsys, monkeypatch, same):
    # PR 4307 の r8-4: PR 本文の下書きは、検証した保存先を開いた記述子から書く
    state = tmp_path / "state"
    state.mkdir()
    st = (state if same else tmp_path).stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{state}")
    monkeypatch.setattr("sys.stdin", io.StringIO("## 概要\n直した\n"))
    code, out = _run(capsys, "draft", "--state-dir", str(state))
    draft = worker.pr_body_draft(state)
    if same:
        assert code == 0 and draft.read_text(encoding="utf-8") == "## 概要\n直した\n"
    else:
        assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition") and not draft.exists()


def test_draft_refuses_a_draft_dir_that_is_a_link(tmp_path, capsys, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "far").mkdir()
    (state / "work").symlink_to(tmp_path / "far")
    monkeypatch.setattr("sys.stdin", io.StringIO("本文"))
    code, out = _run(capsys, "draft", "--state-dir", str(state))
    assert _invalid(code, out) and list((tmp_path / "far").iterdir()) == []


@pytest.mark.parametrize("same", [True, False])
def test_write_replaces_a_file_only_in_the_result_dir_worker_run_opened(tmp_path, capsys, monkeypatch, same):
    # #4590: Write・Edit の Tool が .git の下を拒むので、承認資料の作業用のファイルは Bash からこのサブコマンドで書く
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("前の資料\n", encoding="utf-8")
    st = (state if same else tmp_path).stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{state}")
    monkeypatch.setenv(worker_append.RUN_ENV, RUN)
    monkeypatch.setattr("sys.stdin", io.StringIO("直した資料\n"))
    code, out = _run(capsys, "write", "--path", str(material))
    if same:
        assert code == 0 and material.read_text(encoding="utf-8") == "直した資料\n"
    else:
        assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
        assert material.read_text(encoding="utf-8") == "前の資料\n"


RUN = "0" * 32


def _hold(monkeypatch, state: Path) -> None:
    st = state.stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{state}")
    monkeypatch.setenv(worker_append.RUN_ENV, RUN)


def test_write_refuses_a_subdir_that_is_a_link(tmp_path, capsys, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "far").mkdir()
    (state / "sub").symlink_to(tmp_path / "far")
    _hold(monkeypatch, state)
    monkeypatch.setattr("sys.stdin", io.StringIO("本文"))
    code, out = _run(capsys, "write", "--path", str(state / "sub" / "material.md"))
    assert _invalid(code, out) and list((tmp_path / "far").iterdir()) == []


def test_write_refuses_outside_a_worker(tmp_path, capsys, monkeypatch):
    # PR 4591 の r1-1: 保存先の識別情報が無ければ書かない
    material = tmp_path / "material.md"
    material.write_text("前\n", encoding="utf-8")
    monkeypatch.delenv(identity.WORKER_RESULT_DIR_ENV, raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("後\n"))
    code, out = _run(capsys, "write", "--path", str(material))
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert material.read_text(encoding="utf-8") == "前\n"


@pytest.mark.parametrize("target", [".git/config", "state/../config", "state"])
def test_write_refuses_a_path_outside_the_result_dir(tmp_path, capsys, monkeypatch, target):
    # PR 4591 の r1-1: 保存先の外（.git の中のほかのファイルなど）は書かない
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / ".git").mkdir()
    for f in (tmp_path / ".git" / "config", tmp_path / "config"):
        f.write_text("前\n", encoding="utf-8")
    _hold(monkeypatch, state)
    monkeypatch.setattr("sys.stdin", io.StringIO("後\n"))
    code, out = _run(capsys, "write", "--path", str(tmp_path / target))
    assert _invalid(code, out)
    assert (tmp_path / ".git" / "config").read_text(encoding="utf-8") == "前\n"
    assert (tmp_path / "config").read_text(encoding="utf-8") == "前\n"


@pytest.mark.parametrize("swapped", [False, True])
def test_write_checks_the_result_dir_before_a_file_in_a_subdir(tmp_path, capsys, monkeypatch, swapped):
    # PR 4591 の r1-3: サブディレクトリの中のファイルも、保存先そのものの識別子を先に突き合わせてから書く
    state = tmp_path / "state"
    (state / "sub").mkdir(parents=True)
    _hold(monkeypatch, state)
    if swapped:
        state.rename(tmp_path / "old")
        (state / "sub").mkdir(parents=True)
    material = state / "sub" / "material.md"
    material.write_text("前\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("後\n"))
    code, out = _run(capsys, "write", "--path", str(material))
    if swapped:
        assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
        assert material.read_text(encoding="utf-8") == "前\n"
    else:
        assert code == 0 and material.read_text(encoding="utf-8") == "後\n"


def test_write_appends_only_the_added_text(tmp_path, capsys, monkeypatch):
    # PR 4591 の r1-5: 書き足すだけなら全文を渡さない
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("1 行目\n", encoding="utf-8")
    _hold(monkeypatch, state)
    monkeypatch.setattr("sys.stdin", io.StringIO("2 行目\n"))
    code, _ = _run(capsys, "write", "--path", str(material), "--append", "--op", "a1")
    assert code == 0 and material.read_text(encoding="utf-8") == "1 行目\n2 行目\n"


@pytest.mark.parametrize("flag", ["--append", "--edit"])
def test_write_needs_an_op_to_append_or_edit(tmp_path, capsys, monkeypatch, flag):
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("前\n", encoding="utf-8")
    _hold(monkeypatch, state)
    monkeypatch.setattr("sys.stdin", io.StringIO('[{"old": "前", "new": "後"}]'))
    code, out = _run(capsys, "write", "--path", str(material), flag)
    assert _invalid(code, out) and material.read_text(encoding="utf-8") == "前\n"


@pytest.mark.parametrize(("flag", "body"), [("--append", "2 行目\n"), ("--edit", '[{"old": "1 行目", "new": "一行目"}]')])
def test_write_does_not_reapply_the_same_op(tmp_path, capsys, monkeypatch, flag, body):
    # PR 4591 の r2-2: 応答を受け取れずに同じ操作を打ち直しても、結果は 1 回打ったときと変わらない
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("1 行目\n", encoding="utf-8")
    _hold(monkeypatch, state)
    outs = []
    for _ in range(2):
        monkeypatch.setattr("sys.stdin", io.StringIO(body))
        outs.append(_run(capsys, "write", "--path", str(material), flag, "--op", "o1"))
    once = material.read_text(encoding="utf-8")
    assert [code for code, _ in outs] == [0, 0] and "済んでいた" in outs[1][1]["summary"]
    assert once == ("1 行目\n2 行目\n" if flag == "--append" else "一行目\n")


def test_write_refuses_the_same_op_with_another_body(tmp_path, capsys, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("1 行目\n", encoding="utf-8")
    _hold(monkeypatch, state)
    for body in ("2 行目\n", "3 行目\n"):
        monkeypatch.setattr("sys.stdin", io.StringIO(body))
        code, out = _run(capsys, "write", "--path", str(material), "--append", "--op", "o1")
    assert _invalid(code, out) and material.read_text(encoding="utf-8") == "1 行目\n2 行目\n"


@contextlib.contextmanager
def _another_write(path: Path):
    """別の書き込みが `path` の書き先ごとのロックを排他で持つ。"""
    to = worker_append.write_target(str(path))
    key = worker_append._op_digest(f"{to.expected[0]}:{to.expected[1]}:{'/'.join(to.parts)}")
    with worker_paths.open_or_create(identity.load().state_dir / worker_append.AREA_DIR / "locks") as locks:
        fd = worker_paths.open_lock(locks, f"{key}.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def _state_with(tmp_path, monkeypatch, *names: str) -> list[Path]:
    # 状態の保存先（XDG_STATE_HOME の tmp_path / "state"）と別のディレクトリにする
    state = tmp_path / "result"
    state.mkdir()
    for name in names:
        (state / name).write_text("1 行目\n", encoding="utf-8")
    _hold(monkeypatch, state)
    return [state / name for name in names]


def _write(capsys, monkeypatch, path: Path, body: str, *flags: str) -> tuple[int, dict]:
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    return _run(capsys, "write", "--path", str(path), *flags)


def test_write_reads_and_publishes_under_the_lock_of_the_target(tmp_path, capsys, monkeypatch, clock):
    # PR 4591 の r2-1・r3-5: 読み取りから公開までを書き先ごとのロックで直列にする。別の書き先の書き込みは待たない
    material, other = _state_with(tmp_path, monkeypatch, "material.md", "other.md")
    monkeypatch.setattr(durable_locks, "lock_wait_seconds", lambda: 0.0)
    with _another_write(material):
        code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
        assert out["cause"]["kind"] == "store_in_use" and code == causes.exit_code("store_in_use")
        code, _ = _write(capsys, monkeypatch, other, "2 行目\n", "--append", "--op", "a1")
        assert code == 0
    assert material.read_text(encoding="utf-8") == "1 行目\n"
    assert other.read_text(encoding="utf-8") == "1 行目\n2 行目\n"


def _runs() -> Path:
    return identity.load().state_dir / worker_append.AREA_DIR / "runs"


def test_write_keeps_the_records_out_of_the_result_dir(tmp_path, capsys, monkeypatch):
    # PR 4591 の r3-3: 記録は利用者ごとの状態の保存先に置き、共有の保存先のほかの利用者が消せない
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert sorted(p.name for p in material.parent.iterdir()) == ["material.md"]
    assert list((_runs() / RUN).rglob(worker_append.OPS_FILE))


def test_run_records_removes_only_its_own_records(tmp_path, capsys, monkeypatch):
    # PR 4591 の r4-2・r5-2・r5-4: 記録は起動ごとの保存先に置き、終えた起動は自分の保存先だけを消す。
    # 同じ保存先を使う別の起動の記録は残り、新しい起動は前の起動の記録を読まない
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    with worker_append.run_records():
        run = os.environ[worker_append.RUN_ENV]
        _write(capsys, monkeypatch, material, "3 行目\n", "--append", "--op", "a1")
        assert (_runs() / run).exists()
    assert not (_runs() / run).exists() and (_runs() / RUN).exists()
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert code == 0 and "済んでいた" in out["summary"]
    assert material.read_text(encoding="utf-8") == "1 行目\n2 行目\n3 行目\n"


def test_write_needs_the_run_of_worker_run(tmp_path, capsys, monkeypatch):
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    monkeypatch.delenv(worker_append.RUN_ENV)
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert material.read_text(encoding="utf-8") == "1 行目\n"


def _published_then_lost(monkeypatch):
    """本文の公開の直後に止まった（応答を受け取れなかった）書き込みを真似る。元の `write_file` を返す。"""
    real = worker_paths.write_file

    def lose_reply(wd, name, text, **kw):
        real(wd, name, text, **kw)
        if name == "material.md":
            raise OSError(5, "Input/output error")

    monkeypatch.setattr(worker_paths, "write_file", lose_reply)
    return real


def _fail_body(monkeypatch):
    """本文の公開の前に止まった書き込みを真似る。元の `write_file` を返す。"""
    real = worker_paths.write_file

    def fail_body(wd, name, text, **kw):
        if name == "material.md":
            raise OSError(28, "No space left on device")
        return real(wd, name, text, **kw)

    monkeypatch.setattr(worker_paths, "write_file", fail_body)
    return real


def test_write_does_not_redo_an_op_a_later_write_undid(tmp_path, capsys, monkeypatch):
    # PR 4591 の r4-4・r5-5: 公開の直後に止まった操作も、後の書き込みで本文が元に戻った後の打ち直しで組み直さない
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    real = _published_then_lost(monkeypatch)
    _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    monkeypatch.setattr(worker_paths, "write_file", real)
    _write(capsys, monkeypatch, material, "1 行目\n")
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert code == 0 and "済んでいた" in out["summary"] and material.read_text(encoding="utf-8") == "1 行目\n"


def test_write_creates_a_file_an_empty_append_stopped_before(tmp_path, capsys, monkeypatch):
    # PR 4591 の r5-6: 無いファイルへの空の追記が公開の前に止まったら、打ち直すと空のファイルを作る
    state = tmp_path / "result"
    state.mkdir()
    _hold(monkeypatch, state)
    material = state / "material.md"
    real = _fail_body(monkeypatch)
    _write(capsys, monkeypatch, material, "", "--append", "--op", "a1")
    monkeypatch.setattr(worker_paths, "write_file", real)
    code, out = _write(capsys, monkeypatch, material, "", "--append", "--op", "a1")
    assert code == 0 and "済んでいた" not in out["summary"] and material.read_text(encoding="utf-8") == ""


def test_write_redoes_an_op_stopped_before_others_wrote(tmp_path, capsys, monkeypatch):
    # PR 4591 の r4-3: 公開の前に止まった操作は、後に別の追記が済んでも、打ち直すと今の本文へ 1 回だけ足す
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    real = _fail_body(monkeypatch)
    _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    monkeypatch.setattr(worker_paths, "write_file", real)
    assert _write(capsys, monkeypatch, material, "3 行目\n", "--append", "--op", "a2")[0] == 0
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert code == 0 and material.read_text(encoding="utf-8") == "1 行目\n3 行目\n2 行目\n"


def test_write_stops_when_it_cannot_tell_whether_an_op_was_published(tmp_path, capsys, monkeypatch):
    # PR 4591 の r5-7: 公開の直後に止まった操作の後に、ndf worker write の外でファイルが変わったら、済んだと返さずに止める
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    real = _published_then_lost(monkeypatch)
    _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    monkeypatch.setattr(worker_paths, "write_file", real)
    material.write_text("手で直した\n", encoding="utf-8")
    assert _write(capsys, monkeypatch, material, "3 行目\n", "--append", "--op", "a2")[0] == 0
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert material.read_text(encoding="utf-8") == "手で直した\n3 行目\n"


def test_write_redoes_an_op_stopped_before_the_body_was_published(tmp_path, capsys, monkeypatch):
    # PR 4591 の r3-1: 記録を本文より先に書く。本文の公開の前に止まった操作は、打ち直すと 1 回だけ書く
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    real = worker_paths.write_file

    def fail_body(wd, name, text, **kw):
        if name == "material.md":
            raise OSError(28, "No space left on device")
        return real(wd, name, text, **kw)

    monkeypatch.setattr(worker_paths, "write_file", fail_body)
    code, out = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
    assert _invalid(code, out) and material.read_text(encoding="utf-8") == "1 行目\n"
    monkeypatch.setattr(worker_paths, "write_file", real)
    for _ in range(2):
        code, _ = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a1")
        assert code == 0
    assert material.read_text(encoding="utf-8") == "1 行目\n2 行目\n"


def test_write_does_not_reapply_an_op_with_a_line_separator(tmp_path, capsys, monkeypatch):
    # PR 4591 の r3-4: 識別子に Unicode の行区切りがあっても、打ち直しで重ねない
    (material,) = _state_with(tmp_path, monkeypatch, "material.md")
    for _ in range(2):
        code, _ = _write(capsys, monkeypatch, material, "2 行目\n", "--append", "--op", "a\u2028b")
        assert code == 0
    assert material.read_text(encoding="utf-8") == "1 行目\n2 行目\n"


@pytest.mark.parametrize(
    ("edits", "after"),
    [
        ([{"old": "古い", "new": "新しい"}], "新しい文\n同じ\n同じ\n"),
        ([{"old": "同じ", "new": "x"}], None),
        ([{"old": "無い", "new": "x"}], None),
        ({"old": "古い", "new": "x"}, None),
    ],
)
def test_write_edits_one_place_at_a_time(tmp_path, capsys, monkeypatch, edits, after):
    # PR 4591 の r1-5: 一部の直しは差分の JSON だけを渡す。1 か所に決まらない old と形の違う入力は書かない
    state = tmp_path / "state"
    state.mkdir()
    material = state / "material.md"
    material.write_text("古い文\n同じ\n同じ\n", encoding="utf-8")
    _hold(monkeypatch, state)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(edits, ensure_ascii=False)))
    code, out = _run(capsys, "write", "--path", str(material), "--edit", "--op", "e1")
    if after is None:
        assert _invalid(code, out) and material.read_text(encoding="utf-8") == "古い文\n同じ\n同じ\n"
    else:
        assert code == 0 and material.read_text(encoding="utf-8") == after
