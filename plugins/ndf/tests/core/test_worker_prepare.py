"""報告の保存先の用意・追記・読み取りのサブコマンド（`ndf worker prepare`・`append`・`collect`。#3877）。"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from ndf.contracts import causes, worker_paths
from ndf.core import worker


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

    def failing(wd, entry, *rest):
        if entry == name:
            raise PermissionError(13, "Permission denied", entry)
        return real(wd, entry, *rest)

    monkeypatch.setattr(worker_paths, fn, failing)


def test_prepare_keeps_the_previous_done_with_its_report_when_the_done_cannot_go(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    _fail_on(monkeypatch, "remove_file", "n.report.md.done")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告" and (d / "n.report.md.done").exists()


def test_prepare_removes_the_done_before_failing_on_the_result(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    (d / "n.result.json").write_text("{}", encoding="utf-8")
    _fail_on(monkeypatch, "remove_file", "n.result.json")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告" and not (d / "n.report.md.done").exists()


def test_prepare_keeps_the_previous_report_when_it_cannot_be_emptied(tmp_path, monkeypatch, capsys):
    d = tmp_path / "w"
    _previous(d)
    _fail_on(monkeypatch, "write_file", "n.report.md")
    assert _invalid(*_run(capsys, "prepare", "--dir", str(d), "--name", "n"))
    assert (d / "n.report.md").read_text(encoding="utf-8") == "前の報告"


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


def test_append_refuses_a_report_that_was_not_prepared(tmp_path, capsys, monkeypatch):
    code, out = _append(capsys, monkeypatch, tmp_path / "n.report.md", "本文\n", "--done")
    assert out["cause"]["kind"] == "precondition" and code == causes.exit_code("precondition")
    assert not (tmp_path / "n.report.md.done").exists()


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
