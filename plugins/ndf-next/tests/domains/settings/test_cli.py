"""サブコマンド `ndf settings migrate`: 差分を示す実行と承認した差分を書く実行（#3613。I10・受け入れ条件 12）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import settings_legacy as legacy
from ndf import contracts
from ndf.contracts import exit_codes, result
from ndf.domains.settings import cli, renames


def _repo(tmp_path: Path, indent: int = 2) -> Path:
    (tmp_path / ".ndf").mkdir(parents=True)
    (tmp_path / ".ndf" / "mvv.json").write_text(json.dumps(legacy.mvv(2), indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    return tmp_path


def _bytes(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((root / ".ndf").glob("*.json"))}


def _read(root: Path, name: str) -> dict[str, Any]:
    return json.loads((root / ".ndf" / name).read_text(encoding="utf-8"))


def test_plan_shows_the_diff_without_writing(tmp_path: Path):
    root = _repo(tmp_path)
    before = _bytes(root)
    got = cli.plan(root)
    assert result.errors(got) == []
    assert got["status"] == "gate" and [i["file"] for i in got["items"]] == ["mvv"]
    digest = got["metrics"]["digest"]
    assert len(digest) == 64 and got["routes"]["approved"]["argv"][-2:] == ["--apply", digest]
    assert _bytes(root) == before
    assert cli.plan(root)["metrics"]["digest"] == digest


def test_apply_with_the_shown_digest_writes_the_new_keys_once(tmp_path: Path):
    root = _repo(tmp_path)
    got = cli.apply(root, cli.plan(root)["metrics"]["digest"])
    assert got["status"] == "ok" and result.errors(got) == []
    mvv = _read(root, "mvv.json")
    assert mvv["versions"][0]["validation"] == {"result": "pass", "checked_at": "2026-01-01T00:00:00+00:00", "accepted_unknown": False}
    assert mvv["settings"] == {"revise_after": {"overrides": 3}}
    assert contracts.schema_errors(mvv, contracts.schema_path("ndf-settings/mvv")) == []
    assert (root / ".ndf" / "mvv.json").read_text(encoding="utf-8").endswith("}\n")
    again = cli.plan(root)
    assert again["status"] == "ok" and again["items"] == []


def test_apply_refuses_when_the_settings_changed_after_the_diff(tmp_path: Path):
    root = _repo(tmp_path)
    digest = cli.plan(root)["metrics"]["digest"]
    data = _read(root, "mvv.json")
    key = legacy.old_validation()[0]
    data["versions"][0][key][legacy.last(next(r.old for r in renames.load() if r.id == "mvv-validation-checked-at"))] = (
        "2027-01-01T00:00:00+00:00"
    )
    (root / ".ndf" / "mvv.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = _bytes(root)
    got = cli.apply(root, digest)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused"
    assert _bytes(root) == before


def test_apply_refuses_an_empty_diff_and_schema_errors(tmp_path: Path):
    (tmp_path / ".ndf").mkdir()
    assert cli.plan(tmp_path)["status"] == "ok"
    assert cli.apply(tmp_path, "0" * 64)["cause"]["kind"] == "refused"
    root = _repo(tmp_path / "r")
    data = _read(root, "mvv.json")
    data["body"] = 7
    (root / ".ndf" / "mvv.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    before = _bytes(root)
    got = cli.apply(root, cli.plan(root)["metrics"]["digest"])
    assert got["cause"]["kind"] == "refused" and "スキーマ" in got["summary"]
    assert _bytes(root) == before


def test_plan_reports_reindent_and_unreadable_settings(tmp_path: Path):
    items = {i["file"]: i for i in cli.plan(_repo(tmp_path / "a", indent=4))["items"]}
    assert items["mvv"]["reindent"] is True
    root = _repo(tmp_path / "b")
    assert {i["file"]: i for i in cli.plan(root)["items"]}["mvv"]["reindent"] is False
    (root / ".ndf" / "mvv.json").write_text("{broken", encoding="utf-8")
    got = cli.plan(root)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid" and got["items"] == []


def test_main_checks_the_arguments(tmp_path: Path, capsys):
    root = _repo(tmp_path)
    assert cli.main(["migrate", "--root", str(root)]) == exit_codes.first_code(result.GATE)
    assert cli.main(["migrate", "--root", str(tmp_path / "none")]) == 0
    assert cli.main(["migrate", "--root", str(root), "--apply", "a" * 63]) == 2
    assert cli.main(["migrate", "--root", str(root), "--apply", "A" * 64]) == 2
    with pytest.raises(SystemExit):
        cli.main(["migrate"])
