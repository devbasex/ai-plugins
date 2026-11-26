"""#1959: 起動の設定（`.ndf/launch.json`）の読み取り・工程の指定・MCP の定義ファイル・承認の対象。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters import launch_settings as ls
from ndf.contracts import internal

# 作り物の値（定義の中身が文に出ないことを確かめる）
HIDDEN = "hidden-value-1959"


def _write(root: Path, data: object, name: str = ".ndf/launch.json", indent: int | None = None) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=indent), encoding="utf-8")
    return path


def _settings(root: Path, steps: dict, **more) -> ls.Settings:
    _write(root, {"version": 1, "permissions": {"steps": steps, **more}})
    return ls.load(root)


def _stopped(fn, *args) -> str:
    with pytest.raises(internal.InternalError) as got:
        fn(*args)
    assert got.value.kind == "precondition"
    return str(got.value)


SERVER = {"command": f"/opt/bin/search-mcp --token {HIDDEN}", "args": [HIDDEN], "env": {"KEY": HIDDEN}}
REMOTE = {"type": "http", "url": f"https://{HIDDEN}.example.invalid/mcp", "headers": {"Authorization": HIDDEN}}


# ---------------------------------------------------------------- load・step_spec


def test_a_step_replaces_the_keys_of_the_star_spec_one_by_one(tmp_path: Path) -> None:
    s = _settings(
        tmp_path, {"*": {"load": ["LSP"], "allow": ["Bash(git log:*)"]}, "impl": {"allow": ["mcp__search__query"], "mcp": "inherit"}}
    )
    assert ls.step_spec(s, "impl") == ls.StepSpec(("LSP",), ("mcp__search__query",), "inherit")
    assert ls.step_spec(s, "design") == ls.StepSpec(("LSP",), ("Bash(git log:*)",), None)


@pytest.mark.parametrize("data", [None, {"version": 1}, {"version": 1, "permissions": {}}])
def test_no_file_or_no_permissions_gives_an_empty_spec(tmp_path: Path, data) -> None:
    if data is not None:
        _write(tmp_path, data)
    spec = ls.step_spec(ls.load(tmp_path), "impl")
    assert spec.empty and spec == ls.StepSpec()


@pytest.mark.parametrize("word", ["Bash(git log:*)", "mcp__search__query", "Read,Grep", "Web Fetch"])
def test_load_takes_only_plain_tool_names_and_the_stop_names_the_place(tmp_path: Path, word: str) -> None:
    _write(tmp_path, {"version": 1, "permissions": {"steps": {"impl": {"load": ["LSP", word]}}}})
    text = _stopped(ls.load, tmp_path)
    assert "permissions/steps/impl/load/1" in text and word not in text


@pytest.mark.parametrize("data", ["{壊れた", json.dumps({"version": 2}), json.dumps({"version": 1, "permissions": {"other": 1}})])
def test_a_broken_file_stops_instead_of_launching_without_the_spec(tmp_path: Path, data: str) -> None:
    _write(tmp_path, data)
    assert str(tmp_path / ".ndf" / "launch.json") in _stopped(ls.load, tmp_path)


def test_an_allow_specifier_shaped_like_a_secret_stops_without_showing_it(tmp_path: Path) -> None:
    value = "curl -H token=" + HIDDEN
    _write(tmp_path, {"version": 1, "permissions": {"steps": {"impl": {"allow": [f"Bash({value})"]}}}})
    text = _stopped(ls.load, tmp_path)
    assert "permissions/steps/impl/allow/0" in text and HIDDEN not in text


def test_an_environment_reference_in_an_assignment_is_not_a_secret(tmp_path: Path) -> None:
    s = _settings(tmp_path, {"impl": {"allow": ["Bash(curl -H token=$API_TOKEN:*)"]}})
    assert ls.step_spec(s, "impl").allow == ("Bash(curl -H token=$API_TOKEN:*)",)


@pytest.mark.parametrize("value", ["token=${PREFIX}" + HIDDEN, "token=$API_TOKEN:" + HIDDEN])
def test_plain_text_joined_to_an_environment_reference_is_a_secret(tmp_path: Path, value: str) -> None:
    """参照に平文をつないだ値は、平文が一覧とプロセスの一覧に出るので秘密の形として止める。"""
    _write(tmp_path, {"version": 1, "permissions": {"steps": {"impl": {"allow": [f"Bash(curl -H {value})"]}}}})
    text = _stopped(ls.load, tmp_path)
    assert "permissions/steps/impl/allow/0" in text and HIDDEN not in text


def test_duplicate_keys_stop_with_the_place_for_the_settings_and_a_definition(tmp_path: Path) -> None:
    """同じキーが 2 つある起動の設定と MCP の定義ファイルは、どちらもファイルを示す precondition で止まる。"""
    _write(tmp_path, '{"version": 1, "version": 1}')
    assert str(tmp_path / ".ndf" / "launch.json") in _stopped(ls.load, tmp_path)
    s = _settings(tmp_path, {})
    _write(tmp_path, '{"mcpServers": {"x": {"command": "a"}, "x": {"command": "' + HIDDEN + '"}}}', ".ndf/mcp/a.json")
    text = _stopped(ls.check_definitions, s, [".ndf/mcp/a.json"])
    assert ".ndf/mcp/a.json" in text and HIDDEN not in text


@pytest.mark.parametrize("rel", ["/etc/mcp.json", "../outside.json", "link/mcp.json"])
def test_an_mcp_path_outside_the_root_stops(tmp_path: Path, rel: str) -> None:
    root, outside = tmp_path / "repo", tmp_path / "elsewhere"
    outside.mkdir()
    root.mkdir()
    (root / "link").symlink_to(outside)
    _write(root, {"version": 1, "permissions": {"steps": {"impl": {"mcp": [rel]}}}})
    assert "permissions/steps/impl/mcp/0" in _stopped(ls.load, root)


def test_a_reflection_left_halfway_stops_instead_of_reading_no_spec(tmp_path: Path) -> None:
    _write(tmp_path, {"version": 1}, ".ndf/launch.json.reflecting")
    assert "approve" in _stopped(ls.load, tmp_path)


def test_a_source_file_is_read_in_place_of_the_settings(tmp_path: Path) -> None:
    source = _write(tmp_path, {"version": 1, "permissions": {"steps": {"*": {"load": ["LSP"]}}}}, "candidate/launch.json")
    assert ls.step_spec(ls.load(tmp_path, source), "x").load == ("LSP",)


# ---------------------------------------------------------------- check_definitions


def test_definitions_with_a_command_or_a_url_are_returned_as_read(tmp_path: Path) -> None:
    s = _settings(tmp_path, {})
    _write(tmp_path, {"mcpServers": {"search": SERVER, "remote": REMOTE}}, ".ndf/mcp/a.json")
    _write(tmp_path, {"mcpServers": {}}, ".ndf/mcp/empty.json")
    got = ls.check_definitions(s, [".ndf/mcp/a.json", ".ndf/mcp/empty.json"])
    assert got == {".ndf/mcp/a.json": {"mcpServers": {"search": SERVER, "remote": REMOTE}}, ".ndf/mcp/empty.json": {"mcpServers": {}}}


@pytest.mark.parametrize(
    "data",
    [
        None,
        "{壊れた",
        [HIDDEN],
        {"mcpServers": [HIDDEN]},
        {"mcpServers": {"x": {"args": [HIDDEN]}}},
        {"mcpServers": {"x": {"command": [HIDDEN]}}},
    ],
)
def test_a_broken_definition_stops_without_its_values(tmp_path: Path, data) -> None:
    s = _settings(tmp_path, {})
    if data is not None:
        _write(tmp_path, data if isinstance(data, str) else json.dumps(data), ".ndf/mcp/a.json")
    text = _stopped(ls.check_definitions, s, [".ndf/mcp/a.json"])
    assert ".ndf/mcp/a.json" in text and HIDDEN not in text


# ---------------------------------------------------------------- approval_target


def test_the_target_ignores_indentation_and_key_order(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    perm = {"steps": {"impl": {"mcp": [".ndf/mcp/a.json"], "load": ["LSP"]}}, "unsandboxed": ["codex"]}
    _write(a, {"version": 1, "permissions": perm})
    _write(
        b,
        {"permissions": {"unsandboxed": ["codex"], "steps": {"impl": {"load": ["LSP"], "mcp": [".ndf/mcp/a.json"]}}}, "version": 1},
        indent=4,
    )
    _write(a, {"mcpServers": {"search": SERVER}}, ".ndf/mcp/a.json")
    _write(b, {"mcpServers": {"search": dict(reversed(SERVER.items()))}}, ".ndf/mcp/a.json", indent=2)
    assert ls.approval_target(ls.load(a)).sha256 == ls.approval_target(ls.load(b)).sha256


def test_changing_a_definition_changes_the_target_and_the_listing_hides_its_values(tmp_path: Path) -> None:
    s = _settings(tmp_path, {"impl": {"mcp": [".ndf/mcp/a.json"], "allow": ["mcp__search__query"]}})
    _write(tmp_path, {"mcpServers": {"search": SERVER, "remote": REMOTE}}, ".ndf/mcp/a.json")
    before = ls.approval_target(s)
    _write(tmp_path, {"mcpServers": {"search": {**SERVER, "args": ["other"]}, "remote": REMOTE}}, ".ndf/mcp/a.json")
    after = ls.approval_target(s)

    assert before.sha256 != after.sha256 and before.listing["sha256"] == before.sha256
    assert HIDDEN not in json.dumps(before.listing)
    (entry,) = before.listing["files"]
    assert entry["path"] == ".ndf/mcp/a.json" and len(entry["sha256"]) == 64
    assert entry["servers"] == [{"name": "search", "command": "search-mcp"}, {"name": "remote", "command": "url"}]
    assert before.listing["steps"] == {"impl": {"allow": ["mcp__search__query"], "mcp": [".ndf/mcp/a.json"]}}


def test_inherit_puts_the_repository_mcp_file_into_the_target(tmp_path: Path) -> None:
    s = _settings(tmp_path, {"design": {"mcp": "inherit"}})
    missing = ls.approval_target(s)
    assert missing.listing["files"] == [{"path": ".mcp.json", "sha256": None, "servers": []}]
    _write(tmp_path, {"mcpServers": {"search": SERVER}}, ".mcp.json")
    assert ls.approval_target(s).sha256 != missing.sha256


def test_inherit_refuses_a_repository_mcp_file_linked_outside_the_root(tmp_path: Path) -> None:
    """`inherit` が読む `.mcp.json` も、リンクの先が根の外なら読まずに止める（外の定義のサーバ名を一覧に出さない）。"""
    root, outside = tmp_path / "repo", tmp_path / "home.mcp.json"
    root.mkdir()
    outside.write_text(json.dumps({"mcpServers": {HIDDEN: SERVER}}), encoding="utf-8")
    (root / ".mcp.json").symlink_to(outside)
    s = _settings(root, {"design": {"mcp": "inherit"}})
    text = _stopped(ls.approval_target, s)
    assert ".mcp.json" in text and HIDDEN not in text


def test_a_definition_only_another_step_points_to_still_stops_the_target(tmp_path: Path) -> None:
    s = _settings(tmp_path, {"impl": {"mcp": [".ndf/mcp/gone.json"]}})
    assert ".ndf/mcp/gone.json" in _stopped(ls.approval_target, s)
