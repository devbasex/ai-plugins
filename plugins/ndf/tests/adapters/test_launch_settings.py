"""#1959: 起動の設定（`.ndf/launch.json`）の読み取り・工程の指定・MCP の定義ファイル・承認の対象。"""

from __future__ import annotations

import json
import os
import shlex
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
    assert "permissions/steps/impl/mcp/0" in _stopped(ls.approval_target, ls.load(root))


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


# ---------------------------------------------------------------- prepared（full の起動 1 回の指定）


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """`.git` を持つリポジトリの根。リポジトリのキーは git を打たずに根のパスにする。"""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(ls.launch_approval, "repo_key", lambda r: str(r))
    return root


def _approve(root: Path) -> str:
    sha = ls.approval_target(ls.load(root)).sha256
    ls.launch_approval.append(str(root), sha, ls.launch_approval.Action.APPROVE, approver="人")
    return sha


def _prepare_stops(name: str, cwd: Path) -> str:
    def enter() -> None:
        with ls.prepared(name, cwd):
            raise AssertionError("止まらずに起動の指定を返した")

    return _stopped(enter)


def test_an_empty_spec_launches_without_looking_for_an_approval_or_running_git(tmp_path: Path, monkeypatch) -> None:
    """起動の費用: 指定の無いリポジトリの準備は、承認を調べず git を起動しない。照らし直しも置かない。"""

    def no_git(root):
        raise AssertionError("git を打った")

    monkeypatch.setattr(ls.launch_approval, "repo_key", no_git)
    with ls.prepared("impl", tmp_path) as grant:
        assert grant.sha256 is None and grant.verifier is None and grant.mcp_files == () and not grant.inherit
    _write(tmp_path, {"version": 1, "permissions": {"steps": {"design": {"load": ["LSP"]}}}})
    with ls.prepared("impl", tmp_path) as grant:  # ほかの工程だけの指定は、この工程の起動に承認を求めない
        assert grant.sha256 is None


def test_the_root_is_the_nearest_directory_with_git_seen_from_the_work_place(repo: Path) -> None:
    (repo / "sub").mkdir()
    assert ls.launch_root(repo / "sub") == repo
    (repo / "wt").mkdir()
    (repo / "wt" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # リンクされた worktree は .git がファイル
    assert ls.launch_root(repo / "wt") == repo / "wt"


def test_an_unapproved_spec_stops_with_the_listing_and_how_to_approve(repo: Path) -> None:
    """条件 11: このマシンで承認していない指定は起動せず、承認の対象と `/ndf:launch-config`・端末のコマンドを示す。コピーを作らない。"""
    _write(repo, {"version": 1, "permissions": {"steps": {"impl": {"mcp": [".ndf/mcp/a.json"]}}}})
    _write(repo, {"mcpServers": {"search": SERVER}}, ".ndf/mcp/a.json")
    sha = ls.approval_target(ls.load(repo)).sha256
    text = _prepare_stops("impl", repo)
    assert "launch-config" in text and f"--sha256 {sha}" in text and ".ndf/mcp/a.json" in text and HIDDEN not in text
    assert not ls.copies_dir().exists() or not any(ls.copies_dir().iterdir())


def test_the_approval_command_quotes_paths_for_the_shell(tmp_path: Path, monkeypatch) -> None:
    """案内のコマンドを端末に貼っても、空白で引数が分かれず、特殊文字を解釈しない。"""
    root = tmp_path / "my repo;$(touch x)"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(ls.launch_approval, "repo_key", lambda r: str(r))
    _write(root, {"version": 1, "permissions": {"steps": {"impl": {"load": ["LSP"]}}}})
    text = _prepare_stops("impl", root)
    command = text.split("`")[-2]
    words = shlex.split(command)
    assert words[:3] == ["cd", str(root), "&&"] and words[4:6] == ["launch-config", "approve"]


def test_an_approved_spec_gives_private_copies_of_what_was_checked_and_removes_them(repo: Path) -> None:
    """条件 5・11・I8: 承認した定義は、確かめた中身のコピー（0700 のディレクトリの 0600 のファイル）を渡し、抜けると消える。"""
    _write(
        repo, {"version": 1, "permissions": {"steps": {"impl": {"mcp": [".ndf/mcp/a.json"], "load": ["LSP"], "allow": ["mcp__search__q"]}}}}
    )
    _write(repo, {"mcpServers": {"search": SERVER}}, ".ndf/mcp/a.json")
    sha = _approve(repo)
    (repo / "sub").mkdir()
    with ls.prepared("impl", repo / "sub") as grant:  # 作業場所の親の根の設定を読む
        (copy,) = grant.mcp_files
        assert grant.sha256 == sha and grant.load == ("LSP",) and grant.allow == ("mcp__search__q",) and not grant.inherit
        assert copy != repo / ".ndf/mcp/a.json" and json.loads(copy.read_text(encoding="utf-8")) == {"mcpServers": {"search": SERVER}}
        assert stat_mode(copy) == 0o600 and stat_mode(copy.parent) == 0o700
        assert grant.verifier is not None and grant.verify() is None
    assert not copy.exists() and not copy.parent.exists()


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_copies_are_removed_when_the_launch_fails(repo: Path) -> None:
    _write(repo, {"version": 1, "permissions": {"steps": {"*": {"mcp": [".ndf/mcp/a.json"]}}}})
    _write(repo, {"mcpServers": {}}, ".ndf/mcp/a.json")
    _approve(repo)
    with pytest.raises(RuntimeError):
        with ls.prepared("impl", repo) as grant:
            raise RuntimeError("起動の失敗")
    assert not grant.mcp_files[0].parent.exists()


def test_copies_left_by_a_process_that_is_gone_are_removed(repo: Path) -> None:
    gone = ls.copies_dir() / "4194399-left"
    gone.mkdir(parents=True)
    mine = ls.copies_dir() / f"{os.getpid()}-running"
    mine.mkdir()
    _write(repo, {"version": 1, "permissions": {"steps": {"*": {"mcp": [".ndf/mcp/a.json"]}}}})
    _write(repo, {"mcpServers": {}}, ".ndf/mcp/a.json")
    _approve(repo)
    with ls.prepared("impl", repo):
        assert not gone.exists() and mine.exists()


@pytest.mark.parametrize("change", ["settings", "definition", "revoke"])
def test_the_check_before_each_child_sees_a_change_after_the_approval(repo: Path, change: str) -> None:
    """条件 11・I5: 確かめた後に設定・定義ファイルを書き換えるか取り消すと、照らし直しが理由を返す（次の子は起動しない）。"""
    _write(repo, {"version": 1, "permissions": {"steps": {"impl": {"mcp": [".ndf/mcp/a.json"]}}}})
    _write(repo, {"mcpServers": {"search": SERVER}}, ".ndf/mcp/a.json")
    sha = _approve(repo)
    with ls.prepared("impl", repo) as grant:
        if change == "settings":
            _write(repo, {"version": 1, "permissions": {"steps": {"impl": {"mcp": [".ndf/mcp/a.json"], "load": ["LSP"]}}}})
        elif change == "definition":
            _write(repo, {"mcpServers": {"search": {**SERVER, "args": ["other"]}}}, ".ndf/mcp/a.json")
        else:
            ls.launch_approval.append(str(repo), sha, ls.launch_approval.Action.REVOKE)
        reason = grant.verify()
        assert reason is not None and "launch-config" in reason and HIDDEN not in reason
        assert json.loads(grant.mcp_files[0].read_text(encoding="utf-8")) == {"mcpServers": {"search": SERVER}}


def test_inherit_reads_the_environment_mcp_without_copies(repo: Path) -> None:
    _write(repo, {"version": 1, "permissions": {"steps": {"design": {"mcp": "inherit"}}}})
    _approve(repo)
    with ls.prepared("design", repo) as grant:
        assert grant.inherit and grant.mcp_files == () and grant.sha256 is not None
    _write(repo, {"mcpServers": {"search": SERVER}}, ".mcp.json")  # inherit の承認は .mcp.json の書き換えで外れる
    assert "launch-config" in _prepare_stops("design", repo)


def test_a_definition_of_another_step_that_cannot_be_read_stops_every_approved_launch(repo: Path) -> None:
    """受け入れる限界: 承認は全工程の定義ファイルをまとめて照らすので、ほかの工程の定義が読めなくても止まる。文はそのファイルを示す。
    指定の無い工程は承認を調べないので起動する。"""
    _write(repo, {"version": 1, "permissions": {"steps": {"impl": {"load": ["LSP"]}, "design": {"mcp": [".ndf/mcp/gone.json"]}}}})
    text = _prepare_stops("impl", repo)
    assert ".ndf/mcp/gone.json" in text and "すべての工程" in text
    with ls.prepared("release", repo) as grant:
        assert grant.sha256 is None
    _write(repo, {"version": 1, "permissions": {"steps": {"design": {"mcp": ["../outside.json"]}}}})
    with ls.prepared("release", repo) as grant:  # ほかの工程のパスが根の外を指しても、指定の無い工程は止めない
        assert grant.sha256 is None
    assert "permissions/steps/design/mcp/0" in _prepare_stops("design", repo)


def test_a_broken_settings_file_stops_the_launch(repo: Path) -> None:
    """条件 5: 指定が壊れていれば、黙って指定なしで起動せずに止まる。"""
    _write(repo, "{壊れた")
    assert str(repo / ".ndf" / "launch.json") in _prepare_stops("impl", repo)
