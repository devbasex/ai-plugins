"""マーケットプレイスと版の書き換え・宣言・タグと GitHub Release・worktree の後片付け・副命令。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.adapters import git as git_mod
from ndf.domains.release import cleanup, tagging
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import FakeHub, not_found, pull, write_json


def _repo(root: Path) -> Path:
    write_json(
        root / ".claude-plugin" / "marketplace.json",
        {
            "plugins": [
                {"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"},
                {"name": "mcp-x", "source": "./plugins/mcp/x"},
            ]
        },
    )
    write_json(root / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    write_json(root / "tools" / "kit" / ".codex-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    write_json(root / "plugins" / "mcp" / "x" / ".claude-plugin" / "plugin.json", {"name": "mcp-x", "version": "0.1.0"})
    (root / "README.md").write_text("| kit | 1.2.3 |\n| other | 1.2.3 |\n", encoding="utf-8")
    return root


def test_issue_1756_plugins_come_from_the_marketplace(tmp_path: Path) -> None:
    """#1756: 名前と置き場はマーケットプレイスの登録から読む（`plugins/<名前>` の形を仮定しない）。"""
    root = _repo(tmp_path)
    found = plugins_mod.load(root)
    assert [(p.name, p.dir) for p in found] == [("kit", "tools/kit"), ("mcp-x", "plugins/mcp/x")]
    assert plugins_mod.version(root, found[1]) == "0.1.0"


def test_bump_writes_manifests_marketplace_and_declared_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    kit = plugins_mod.by_name(plugins_mod.load(root))["kit"]
    written = plugins_mod.bump(root, kit, "1.2.4", ["README.md"])
    assert written == [
        "tools/kit/.claude-plugin/plugin.json",
        "tools/kit/.codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "README.md",
    ]
    assert plugins_mod.version(root, kit) == "1.2.4"
    assert "Kit (v1.2.4)" in (root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    assert (root / "README.md").read_text(encoding="utf-8") == "| kit | 1.2.4 |\n| other | 1.2.3 |\n"


def test_versions_must_agree_between_manifests(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    write_json(root / "tools" / "kit" / ".codex-plugin" / "plugin.json", {"name": "kit", "version": "9.9.9"})
    with pytest.raises(plugins_mod.PluginsError, match="そろわない"):
        plugins_mod.version(root, plugins_mod.load(root)[0])


def test_declaration_defaults_and_steps(tmp_path: Path) -> None:
    assert decl_mod.load(tmp_path) == decl_mod.Declaration()
    write_json(
        decl_mod.path_of(tmp_path),
        {"version": 1, "bump_undistributed": True, "steps": [{"name": "記録", "stage": "production", "command": ["true", "{version}"]}]},
    )
    got = decl_mod.load(tmp_path)
    assert got.bump_undistributed and [s.name for s in got.for_stage("production")] == ["記録"] and got.for_stage("dev") == []
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "steps": [{"name": "x", "stage": "nowhere", "command": ["true"]}]})
    with pytest.raises(decl_mod.DeclarationError):
        decl_mod.load(tmp_path)


def test_declared_step_failure_is_reported(tmp_path: Path) -> None:
    with pytest.raises(decl_mod.StepFailed, match="終了コード 1"):
        decl_mod.run_step(tmp_path, decl_mod.Step("落ちる", "production", ("false",)), "1.0.0")


def test_publish_creates_missing_tag_and_release_for_bumped_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    (root / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 直した（#5）\n", encoding="utf-8")
    monkeypatch.setattr(git_mod.Git, "changed_files", lambda self, a, b: ["tools/kit/.claude-plugin/plugin.json"])
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/git/ref/tags/kit--v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/kit--v1.2.3")] = not_found()
    made = tagging.publish(root, hub, "d" * 40)
    assert [m.item() for m in made] == [
        {"kind": "tag", "name": "kit--v1.2.3", "result": "created"},
        {"kind": "release", "name": "kit--v1.2.3", "result": "created"},
    ]
    release = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/releases"))
    assert release["body"] == "- 直した（#5）" and release["name"] == "kit v1.2.3"


def _entry(path: str, branch: str | None) -> git_mod.WorktreeEntry:
    return git_mod.WorktreeEntry(Path(path), "e" * 40, branch)


def test_issue_3106_cleanup_uses_the_given_remover() -> None:
    """#3106 の残り: worktree を外す関数は呼び手が渡し（lock を外してから外す関数を渡せる）、外せないものは残して報告する。"""
    entries = [_entry("/m", "develop"), _entry("/w1", "release/v1.2.4"), _entry("/w2", "feat/x"), _entry("/w3", "release/v1.2.4")]
    seen: list[Path] = []

    def remove(path: Path) -> None:
        seen.append(path)
        if path == Path("/w3"):
            raise git_mod.GitError("locked")

    swept = cleanup.sweep(entries, ["release/v1.2.4"], remove)
    assert seen == [Path("/w1"), Path("/w3")]
    assert swept.removed == (Path("/w1"),) and swept.kept == ((Path("/w3"), "locked"),)


def test_release_steps_is_registered_and_stops_with_cause(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.COMMANDS[release_cli.TOOL] == release_cli.__name__
    hub = FakeHub(pulls={7: pull(base="develop")})
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    code = release_cli.main(["approval-check", "--pr", "7", "--production", "main"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "stopped" and out["cause"]["kind"] == "not_applicable" and code != 0


def _options(parser: argparse.ArgumentParser) -> dict[str, list[tuple]]:
    """副命令ごとの引数の (名前, 必須, 既定, 選択肢, 型, 種類)。"""
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {
        name: [
            (a.option_strings[0], a.required, a.default, tuple(a.choices) if a.choices else None, a.type, type(a).__name__)
            for a in sp._actions
            if a.option_strings and a.dest != "help"
        ]
        for name, sp in sub.choices.items()
    }


def test_release_steps_parser_current_behavior() -> None:
    """現状固定（_parser）: 副命令の並びと、副命令ごとの引数の必須・既定・選択肢・型。"""
    common = [("--root", False, ".", None, None, "_StoreAction"), ("--repo", False, None, None, None, "_StoreAction")]

    def req(name: str, type_: type | None = None, choices: tuple | None = None) -> tuple:
        return (name, True, None, choices, type_, "_StoreAction")

    def opt(name: str, default: str | None = None, choices: tuple | None = None) -> tuple:
        return (name, False, default, choices, None, "_StoreAction")

    target = [
        req("--base"),
        req("--production"),
        req("--channel", choices=("production", "dev")),
        opt("--level", "patch", ("major", "minor", "patch")),
        opt("--plugin"),
    ]
    pr = req("--pr", int)
    expected = {
        "assemble": common + target,
        "materials": common + target + [opt("--out")],
        "present": common + [pr, opt("--material")],
        "answer": common + [pr, req("--answer", choices=("approved", "rejected")), req("--presented"), req("--by")],
        "approval-check": common + [pr, req("--production")],
        "merge": common + [pr, req("--base"), req("--production"), req("--budget", float)],
        "publish": common + [req("--revision")],
        "sync-base": common + [req("--main-dir"), req("--base")],
        "config-snapshot": common + [req("--out")],
        "config-check": common + [req("--before")],
        "cleanup": common + [("--branch", True, None, None, None, "_AppendAction")],
    }
    got = _options(release_cli._parser())
    assert list(got) == list(expected) == list(release_cli.HANDLERS)
    assert got == expected


def test_release_steps_parser_parses_and_requires_a_command() -> None:
    """現状固定（_parser）: 副命令は必須で、引数は名前空間の属性になる。"""
    parser = release_cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args(["cleanup", "--branch", "a", "--branch", "b"])
    assert vars(args) == {"command": "cleanup", "root": ".", "repo": None, "branch": ["a", "b"]}
    args = parser.parse_args(["merge", "--pr", "3", "--base", "develop", "--production", "main", "--budget", "1.5"])
    assert (args.pr, args.budget, args.base, args.production) == (3, 1.5, "develop", "main")


def test_declaration_parse_reads_every_key(tmp_path: Path) -> None:
    """現状固定（_parse）: すべての鍵を読み、空の配列は既定でなく空のまま。生の宣言を `raw` に持つ。"""
    raw = {
        "version": 1,
        "steps": [{"name": "a", "stage": "verify", "command": ["echo", "{version}"], "writes": ["x"], "timeout_seconds": 5}],
        "undistributed": ["docs/"],
        "bump_undistributed": True,
        "version_files": ["README.md"],
        "changelog": "docs/CL.md",
    }
    write_json(decl_mod.path_of(tmp_path), raw)
    got = decl_mod.load(tmp_path)
    assert got == decl_mod.Declaration(
        (decl_mod.Step("a", "verify", ("echo", "{version}"), ("x",), 5.0),), ("docs/",), True, ("README.md",), "docs/CL.md"
    )
    assert got.raw == raw
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "undistributed": []})
    assert decl_mod.load(tmp_path) == decl_mod.Declaration(undistributed=())
    write_json(decl_mod.path_of(tmp_path), {"version": 1})
    assert decl_mod.load(tmp_path) == decl_mod.Declaration()


# NOTE: 現状固定。timeout_seconds に真偽の true を書くと 1 秒として通る（bool は int の一種）。直すなら別の変更で行う。
def test_declaration_parse_accepts_bool_timeout_as_number(tmp_path: Path) -> None:
    write_json(
        decl_mod.path_of(tmp_path), {"version": 1, "steps": [{"name": "a", "stage": "dev", "command": ["t"], "timeout_seconds": True}]}
    )
    assert decl_mod.load(tmp_path).steps == (decl_mod.Step("a", "dev", ("t",), (), 1.0),)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"version": 2}, "宣言の version が 1 でない: 2"),
        ({}, "宣言の version が 1 でない: None"),
        ({"version": "1"}, "宣言の version が 1 でない: '1'"),
        ({"version": 1, "steps": {}}, "steps は配列"),
        ({"version": 1, "bump_undistributed": "yes"}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "bump_undistributed": None}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "changelog": 3}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "undistributed": "x"}, "undistributed は文字列の配列"),
        ({"version": 1, "version_files": [1]}, "version_files は文字列の配列"),
        ({"version": 1, "steps": [1]}, "steps の行がオブジェクトでない: 1"),
        (
            {"version": 1, "steps": [{"name": "a", "stage": "x", "command": ["t"]}]},
            "steps の行の name か stage（production, dev, verify）が違う: {'name': 'a', 'stage': 'x', 'command': ['t']}",
        ),
        ({"version": 1, "steps": [{"name": "a", "stage": "dev", "command": []}]}, "steps の a の command は空でない文字列の配列"),
        (
            {"version": 1, "steps": [{"name": "a", "stage": "dev", "command": ["t"], "timeout_seconds": 0}]},
            "steps の a の timeout_seconds は正の数",
        ),
        ({"version": 1, "steps": [{"name": "a", "stage": "dev", "command": ["t"], "writes": "x"}]}, "writes は文字列の配列"),
    ],
)
def test_declaration_parse_errors(tmp_path: Path, raw: dict, message: str) -> None:
    """現状固定（_parse）: 形の違う宣言は、鍵ごとの文言の `DeclarationError`。"""
    write_json(decl_mod.path_of(tmp_path), raw)
    with pytest.raises(decl_mod.DeclarationError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value) == message


def test_declaration_load_errors_name_the_file(tmp_path: Path) -> None:
    """現状固定（load）: 読めない・オブジェクトでない宣言はファイルのパスを書いた `DeclarationError`。"""
    path = decl_mod.path_of(tmp_path)
    assert path == tmp_path / ".ndf" / "release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(decl_mod.DeclarationError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value) == f"{path} が JSON のオブジェクトでない"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(decl_mod.DeclarationError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value).startswith(f"{path} を読めない: ")


def test_release_steps_args_table_matches_handlers() -> None:
    """引数の表（ARGS）とハンドラーの表（HANDLERS）は同じ副命令を同じ順に持つ。"""
    assert list(release_cli.ARGS) == list(release_cli.HANDLERS)
