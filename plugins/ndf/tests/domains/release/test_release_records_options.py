"""`release records` が渡した読み取りオプションで経路を選ぶ振る舞いを固定する（I-008 のリファクタリングの前）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.release import cli as release_cli
from ndf.domains.release import record

PR = 42


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """record の読み取り 3 つを差し替え、呼ばれた経路と引数を記録する。"""
    seen: list[tuple] = []
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: object())

    def read(hub, pr):
        seen.append(("read", pr))
        return record.Records(None, ())

    def read_release(hub, pr, select, bodies, issues):
        seen.append(("read_release", pr, select, bodies, issues))
        return {"release": None, "verifications": []}

    def pr_body_only(hub, pr):
        seen.append(("pr_body_only", pr))
        return {"body": ""}

    monkeypatch.setattr(record, "read", read)
    monkeypatch.setattr(record, "read_release", read_release)
    monkeypatch.setattr(record, "pr_body_only", pr_body_only)
    return seen


def _run(tmp_path: Path, *extra: str) -> dict:
    """結果のスキーマの確かめを避けるため、main を通さず cmd_records を直接呼ぶ（読み取りは差し替えてある）。"""
    args = release_cli._parser().parse_args(["records", "--root", str(tmp_path), "--pr", str(PR), *extra])
    return release_cli.cmd_records(args)


def test_no_read_option_reads_all_records(tmp_path: Path, calls: list[tuple]) -> None:
    out = _run(tmp_path)
    assert out["status"] == "ok"
    assert calls == [("read", PR)]
    assert out["outputs"] == {"release": None, "verifications": []}


@pytest.mark.parametrize(
    ("extra", "select", "bodies", "issues"),
    [
        (["--version", "1.2.3"], record.Selection("1.2.3", None, None), False, None),
        (["--release", "7"], record.Selection(None, 7, None), False, None),
        (["--unit", "ndf"], record.Selection(None, None, "ndf"), False, None),
        (["--bodies"], record.Selection(None, None, None), True, None),
        (["--issues", "3", "4"], record.Selection(None, None, None), False, [3, 4]),
        (["--version", "1.2.3", "--bodies"], record.Selection("1.2.3", None, None), True, None),
    ],
)
def test_any_read_option_reads_the_release(
    tmp_path: Path,
    calls: list[tuple],
    extra: list[str],
    select: record.Selection,
    bodies: bool,
    issues: list[int] | None,
) -> None:
    out = _run(tmp_path, *extra)
    assert out["status"] == "ok"
    assert calls == [("read_release", PR, select, bodies, issues)]
    assert "今回のリリース" in out["summary"]


def test_pr_body_only_alone_reads_only_the_body(tmp_path: Path, calls: list[tuple]) -> None:
    out = _run(tmp_path, "--pr-body-only")
    assert out["status"] == "ok"
    assert calls == [("pr_body_only", PR)]
    assert out["outputs"] == {"body": ""}


@pytest.mark.parametrize(
    "extra",
    [["--version", "1.2.3"], ["--release", "7"], ["--unit", "ndf"], ["--bodies"], ["--issues", "3"]],
)
def test_pr_body_only_with_any_read_option_is_refused(tmp_path: Path, calls: list[tuple], extra: list[str]) -> None:
    with pytest.raises(record.RecordError, match="--pr-body-only"):
        _run(tmp_path, "--pr-body-only", *extra)
    assert calls == []
