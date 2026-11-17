"""`ndf gws check`（gws の確認）と、records の gws の部品に足した 3 つ（#3547・#2067）。

偽の gws・npm を PATH（偽のコマンドの保存先だけ）に置き、受付（`ndf.cli.main`）から通して確かめる。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from ndf import cli
from ndf.contracts import limits
from ndf.domains.gws import report_decide as rd
from ndf.domains.gws import thumbnails
from ndf.domains.records import cli as records_cli
from ndf.domains.records import gws as records_gws

from . import Fakes, assert_routes, run_gws, status_rule


def test_gws_is_in_the_command_table() -> None:
    assert cli.COMMANDS["gws"] == "ndf.domains.gws.cli"


def test_authenticated_by_token_source(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("token_env_var", client=False)])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["status"]) == (0, "ok")
    assert got["metrics"] == {
        "state": "authenticated",
        "credential_source": "token_env_var",
        "auth_method": "none",
        "client_config_exists": False,
    }


def test_token_in_env_is_authenticated_even_when_source_is_none(fakes: Fakes, capsys, last_result, monkeypatch) -> None:
    fakes.add("gws", [status_rule("none")])
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_TOKEN", "t")
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["metrics"]["state"]) == (0, "authenticated")


def test_logged_in_with_client_secret_is_authenticated(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("client_secret.json", "oauth2")])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["status"]) == (0, "ok")
    assert got["metrics"] == {
        "state": "authenticated",
        "credential_source": "client_secret.json",
        "auth_method": "oauth2",
        "client_config_exists": True,
    }


@pytest.mark.parametrize(
    ("source", "client"),
    [("none", True), ("none", False), ("client_secret.json", True)],
    ids=["no-token", "no-client", "client-secret-only-2067"],
)
def test_unauthenticated_is_gate_11(fakes: Fakes, capsys, last_result, source: str, client: bool) -> None:
    fakes.add("gws", [status_rule(source, client=client)])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["status"], got["metrics"]["state"]) == (11, "gate", "unauthenticated")
    assert got["metrics"]["client_config_exists"] is client
    assert_routes(got)
    assert got["routes"] == {"retry": {"argv": ["ndf", "gws", "check"]}, "cancel": None}
    assert ("gws auth setup" in got["summary"]) is (not client)


def test_client_secret_only_matches_records_gws_check(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("client_secret.json")])
    gws_code, _ = run_gws(capsys, last_result, "check")
    records = records_cli._gws_check(argparse.Namespace(), Path("."))
    assert gws_code == 11 and records["status"] == "stopped" and records["cause"]["kind"] == "precondition"


def test_missing_without_install_writes_the_material_and_does_not_install(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("npm", [{"prefix": ["prefix", "-g"], "out": "/opt/npm-global\n"}])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["status"], got["metrics"]) == (10, "gate", {"state": "missing"})
    assert fakes.calls("npm") == [["prefix", "-g"]]
    assert_routes(got)
    assert got["routes"] == {"approved": {"argv": ["ndf", "gws", "check", "--install"]}, "rejected": None}
    text = Path(got["presentation_path"]).read_text(encoding="utf-8")
    for part in (
        "https://www.npmjs.com/package/@googleworkspace/cli",
        "npm install -g @googleworkspace/cli",
        "/opt/npm-global",
        "npm uninstall -g @googleworkspace/cli",
    ):
        assert part in text


def test_missing_twice_keeps_one_material(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("npm", [{"prefix": ["prefix", "-g"], "code": 1}])
    _, first = run_gws(capsys, last_result, "check")
    _, second = run_gws(capsys, last_result, "check")
    path = Path(second["presentation_path"])
    assert first["presentation_path"] == second["presentation_path"]
    assert [p.name for p in path.parent.iterdir()] == ["install.md"]
    assert "値を読めなかった" in path.read_text(encoding="utf-8")


def test_uninstallable_without_npm(fakes: Fakes, capsys, last_result) -> None:
    code, got = run_gws(capsys, last_result, "check", "--install")
    assert (code, got["status"], got["cause"]["kind"], got["metrics"]) == (3, "stopped", "precondition", {"state": "uninstallable"})


def test_install_then_rechecks(fakes: Fakes, capsys, last_result) -> None:
    gws = fakes.add("gws", [status_rule("token_cache")], staged=True)
    fakes.add("npm", [{"prefix": ["install"], "copy": [str(gws), str(fakes.bin / "gws")]}])
    code, got = run_gws(capsys, last_result, "check", "--install")
    assert (code, got["metrics"]["state"]) == (0, "authenticated")
    assert fakes.calls("npm") == [["install", "-g", "@googleworkspace/cli"]]
    assert fakes.calls("gws") == [["auth", "status"]]


def test_install_when_gws_present_does_nothing(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("none")])
    fakes.add("npm", [])
    code, _ = run_gws(capsys, last_result, "check", "--install")
    assert code == 11 and fakes.calls("npm") == []


def test_install_failure_stops_with_1_and_shows_the_tail(fakes: Fakes, capsys, last_result) -> None:
    out = "".join(f"line {n}\n" for n in range(30))
    fakes.add("npm", [{"prefix": ["install"], "code": 243, "out": out, "err": "EACCES\n"}])
    code, got = run_gws(capsys, last_result, "check", "--install")
    assert (code, got["cause"]["kind"]) == (1, "unknown")
    detail = got["items"][0]["detail"]
    assert got["items"][0]["result"] == "failed" and detail.endswith("EACCES") and len(detail.splitlines()) == rd.DETAIL_LINES
    assert "sudo" in got["summary"]


def test_install_over_the_limit_is_interrupted(fakes: Fakes, capsys, last_result, monkeypatch) -> None:
    monkeypatch.setattr(limits, "seconds", lambda name: 0.5)
    fakes.add("npm", [{"prefix": ["install"], "sleep": 10}])
    code, got = run_gws(capsys, last_result, "check", "--install")
    assert (code, got["cause"]["kind"]) == (1, "interrupted")


def test_installed_but_not_on_path_stops_with_1(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("npm", [{"prefix": ["install"]}, {"prefix": ["prefix", "-g"], "out": "/opt/npm-global\n"}])
    code, got = run_gws(capsys, last_result, "check", "--install")
    assert (code, got["cause"]["kind"]) == (1, "unknown")
    assert "/opt/npm-global/bin" in got["summary"]


@pytest.mark.parametrize(
    "rule",
    [
        {"prefix": ["auth", "status"], "code": 1, "err": "boom\n"},
        {"prefix": ["auth", "status"], "out": "not json", "err": "boom\n"},
        {"prefix": ["auth", "status"], "out": json.dumps({"credential_source": "none"}), "err": "boom\n"},
    ],
    ids=["exit-1", "not-json", "missing-key"],
)
def test_unreadable_status_stops_with_2_and_shows_stderr(fakes: Fakes, capsys, last_result, rule: dict) -> None:
    fakes.add("gws", [rule])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["status"], got["cause"]["kind"]) == (2, "stopped", "invalid")
    assert got["items"] == [{"name": "gws auth status の標準エラーの末尾", "result": "stderr", "detail": "boom"}]
    assert fakes.calls("gws") == [["auth", "status"]]


def test_status_over_the_limit_stops_with_2_and_uses_the_call_limit(fakes: Fakes, capsys, last_result, monkeypatch) -> None:
    asked: list[str] = []

    def short(name: str) -> float:
        asked.append(name)
        return 0.5

    monkeypatch.setattr(limits, "seconds", short)
    fakes.add("gws", [{"prefix": ["auth", "status"], "sleep": 10}])
    code, got = run_gws(capsys, last_result, "check")
    assert (code, got["cause"]["kind"], got["items"]) == (2, "invalid", [])
    assert "google-workspace.call" in asked and "gws.auth-status" not in asked


def test_does_not_copy_other_keys(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("token_cache", token="secret-value", token_cache_exists=True)])
    _, got = run_gws(capsys, last_result, "check")
    assert set(got["metrics"]) == {"state", *records_gws.STATUS_KEYS}
    assert "secret-value" not in json.dumps(got)


def test_unknown_argument_is_rejected(fakes: Fakes, capsys, last_result) -> None:
    fakes.add("gws", [status_rule("token_cache")])
    code, got = run_gws(capsys, last_result, "check", "--login")
    assert (code, got["cause"]["kind"]) == (2, "invalid") and fakes.calls("gws") == []
    code, _ = run_gws(capsys, last_result)
    assert code == 2


def test_records_auth_status_keeps_its_default_limit_and_carries_stderr(fakes: Fakes, monkeypatch) -> None:
    asked: list[str] = []
    real = limits.seconds
    monkeypatch.setattr(limits, "seconds", lambda name: asked.append(name) or real(name))
    fakes.add("gws", [{"prefix": ["auth", "status"], "code": 2, "err": "denied\n"}])
    with pytest.raises(records_gws.GwsError) as caught:
        records_gws.auth_status()
    assert asked == ["gws.auth-status"] and caught.value.stderr == "denied\n"
    assert records_gws.GwsError("x").stderr == ""


def test_tail_and_pick() -> None:
    lines = [f"l{n}" for n in range(rd.DETAIL_LINES + 1)]
    assert rd.tail("\n".join(lines)).splitlines() == lines[1:]
    assert rd.tail("\n".join(lines[: rd.DETAIL_LINES])).splitlines() == lines[: rd.DETAIL_LINES]
    assert rd.tail(None) == "" and rd.tail("") == ""
    assert records_gws.public_keys({"a": 1, "b": 2, "token": "t"}, ("a", "b")) == {"a": 1, "b": 2}


def _io_schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "io" / f"gws.{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_states_match_the_result_schema_enum() -> None:
    # 4 つの状態の値は結果のスキーマ gws.result の metrics.state の列挙と同じ（並びも含む）
    enum = _io_schema("result")["properties"]["metrics"]["properties"]["state"]["enum"]
    assert [rd.AUTHENTICATED, rd.MISSING, rd.UNAUTHENTICATED, rd.UNINSTALLABLE] == enum
    assert (rd.AUTHENTICATED, rd.MISSING, rd.UNAUTHENTICATED, rd.UNINSTALLABLE) == (
        "authenticated",
        "missing",
        "unauthenticated",
        "uninstallable",
    )


def test_sizes_match_the_schema_enums() -> None:
    # サムネイルの大きさは結果のスキーマ gws.result の metrics.size と、引数のスキーマ gws.args の --size の列挙と同じ
    result_enum = _io_schema("result")["properties"]["metrics"]["properties"]["size"]["enum"]
    args = next(v for v in _io_schema("args")["oneOf"] if v["properties"]["subcommand"]["const"] == "thumbnails")
    assert list(thumbnails.SIZES) == result_enum == args["properties"]["--size"]["enum"]
    assert thumbnails.DEFAULT_SIZE == args["properties"]["--size"]["default"]
