from __future__ import annotations

from pathlib import Path

import pytest

from ndf.contracts import worker_paths


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_report_paths_returns_four_files_under_directory():
    paths = worker_paths.report_paths(Path("/w"), "n")
    assert paths == worker_paths.ReportPaths(
        prompt=Path("/w/n.prompt.md"),
        report=Path("/w/n.report.md"),
        done=Path("/w/n.report.md.done"),
        result=Path("/w/n.result.json"),
    )


@pytest.mark.parametrize("name", ["a", "a" * 64])
def test_report_paths_accepts_names_of_1_and_64_chars(name):
    assert worker_paths.report_paths(Path("/w"), name).report == Path(f"/w/{name}.report.md")


def test_report_paths_absolutizes_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = worker_paths.report_paths(Path("d"), "n")
    assert all(p.is_absolute() and p.parent == tmp_path.resolve() / "d" for p in paths)


def test_report_paths_does_not_resolve_link_named_result(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    (d / "n.result.json").symlink_to(outside)
    assert worker_paths.report_paths(d, "n").result == d.resolve() / "n.result.json"


@pytest.mark.parametrize("name", ["", "../x", "a/b", "日本語", "a" * 65])
def test_report_paths_rejects_bad_names_without_touching_files(tmp_path, name):
    before = _tree(tmp_path)
    with pytest.raises(worker_paths.NameInvalid):
        worker_paths.report_paths(tmp_path, name)
    assert _tree(tmp_path) == before
