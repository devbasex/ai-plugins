"""報告の保存先の用意・追記・読み取りのサブコマンド（`ndf worker prepare`・`append`・`collect`。#3877）。"""

from __future__ import annotations

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
