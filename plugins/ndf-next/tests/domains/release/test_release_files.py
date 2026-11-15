"""マーケットプレイスと版の書き換え・宣言・タグと GitHub Release・worktree の後片付け・副命令。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHubError
from ndf.domains.release import bump_decide, cleanup, tagging, version_store
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import FakeHub, init_repo, not_found, pull, write_json

KIT = version_store.Unit("kit", "tools/kit")


class _Store:
    """kit が 1.2.3 の置き場（タグを作る単位は `tagging.bumped` の差し替えで決める）。"""

    kind = "plugin"

    def units(self) -> list[version_store.Unit]:
        return [KIT, version_store.Unit("mcp-x", "plugins/mcp/x")]

    def read(self, repo_git: object, ref: str, unit: version_store.Unit) -> str | None:
        return "1.2.3" if unit == KIT else None


STORE = _Store()


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
    monkeypatch.setattr(tagging, "bumped", lambda store, root, revision: [KIT])
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/git/ref/tags/kit--v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/kit--v1.2.3")] = not_found()
    made = tagging.publish(root, hub, "d" * 40, STORE)
    assert [m.item() for m in made] == [
        {"kind": "tag", "name": "kit--v1.2.3", "result": "created"},
        {"kind": "release", "name": "kit--v1.2.3", "result": "created"},
    ]
    release = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/releases"))
    assert release["body"] == "- 直した（#5）" and release["name"] == "kit v1.2.3"


# --- 現状固定（I-011）: タグと GitHub Release の有無の読み（GET の 404 だけを「無い」とする）---


def _publish_hub(root: Path, monkeypatch: pytest.MonkeyPatch, tag: object, release: object) -> FakeHub:
    monkeypatch.setattr(tagging, "bumped", lambda store, root, revision: [KIT])
    hub = FakeHub()
    for path, got in (("/repos/o/r/git/ref/tags/kit--v1.2.3", tag), ("/repos/o/r/releases/tags/kit--v1.2.3", release)):
        if got is not None:
            hub.responses[("GET", path)] = got
    return hub


def _posts(hub: FakeHub) -> list[str]:
    return [path for m, path, _p in hub.requests if m == "POST"]


def test_characterize_publish_keeps_what_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET が通れば（応答が空でも）あるとして作らない。タグだけあれば GitHub Release だけを作る。"""
    root = _repo(tmp_path)
    hub = _publish_hub(root, monkeypatch, None, None)
    assert [m.item() for m in tagging.publish(root, hub, "d" * 40, STORE)] == [
        {"kind": "tag", "name": "kit--v1.2.3", "result": "exists"},
        {"kind": "release", "name": "kit--v1.2.3", "result": "exists"},
    ]
    assert _posts(hub) == []
    hub = _publish_hub(root, monkeypatch, {"ref": "refs/tags/kit--v1.2.3"}, not_found())
    assert [m.result for m in tagging.publish(root, hub, "d" * 40, STORE)] == ["exists", "created"]
    assert _posts(hub) == ["/repos/o/r/releases"]
    release = next(p for m, path, p in hub.requests if m == "POST")
    assert release == {"tag_name": "kit--v1.2.3", "name": "kit v1.2.3", "body": "kit v1.2.3", "make_latest": "true"}


def test_characterize_publish_creates_the_tag_at_the_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    hub = _publish_hub(root, monkeypatch, not_found(), {})
    assert [m.result for m in tagging.publish(root, hub, "d" * 40, STORE)] == ["created", "exists"]
    assert [(path, p) for m, path, p in hub.requests if m == "POST"] == [
        ("/repos/o/r/git/refs", {"ref": "refs/tags/kit--v1.2.3", "sha": "d" * 40})
    ]


@pytest.mark.parametrize("status", [0, 403, 500])
@pytest.mark.parametrize("which", ["tag", "release"])
def test_characterize_publish_raises_errors_other_than_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, which: str
) -> None:
    """404 でない GitHub の誤りは呼び出し元へ上げ、そこから先を作らない。"""
    root = _repo(tmp_path)
    error = GitHubError("断られた", status)
    hub = _publish_hub(root, monkeypatch, error if which == "tag" else None, error if which == "release" else None)
    with pytest.raises(GitHubError) as caught:
        tagging.publish(root, hub, "d" * 40, STORE)
    assert caught.value is error
    assert _posts(hub) == []


def _commit(root: Path, message: str, name: str) -> str:
    (root / name).write_text(name, encoding="utf-8")
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", message)
    return git_sandbox.sh(root, "rev-parse", "HEAD").strip()


def test_overtaken_is_true_only_when_a_queued_pr_entered_before_the_bump_merge(tmp_path: Path) -> None:
    """決定 20: バージョン上げのマージコミットの第 1 親が第 2 親の祖先でなければ追い越された。今の行のリリースブランチは見ない。"""
    root = init_repo(tmp_path / "repo")
    base = _commit(root, "init", "a")
    git_sandbox.sh(root, "switch", "-q", "-c", "release/v1.3.0")
    _commit(root, bump_decide.message_of("kit", "1.3.0", base), "b")
    git_sandbox.sh(root, "switch", "-q", "develop")
    git_sandbox.sh(root, "merge", "-q", "--no-ff", "release/v1.3.0", "-m", "in order")
    in_order = git_sandbox.sh(root, "rev-parse", "HEAD").strip()
    assert not tagging.overtaken(git_mod.Git(root), in_order) and not tagging.overtaken(git_mod.Git(root), base)
    _commit(root, "queued pr", "c")
    git_sandbox.sh(root, "switch", "-q", "-c", "release/v1.3.1", in_order)
    _commit(root, bump_decide.message_of("kit", "1.3.1", in_order), "d")
    git_sandbox.sh(root, "switch", "-q", "develop")
    git_sandbox.sh(root, "merge", "-q", "--no-ff", "release/v1.3.1", "-m", "overtaken")
    overtaken = git_sandbox.sh(root, "rev-parse", "HEAD").strip()
    assert tagging.overtaken(git_mod.Git(root), overtaken)
    assert tagging.bumped(STORE, root, overtaken) == []  # type: ignore[arg-type]
    git_sandbox.sh(root, "switch", "-q", "-c", "release/v2.0.0", in_order)
    _commit(root, "Release: kit v2.0.0", "e")
    git_sandbox.sh(root, "switch", "-q", "develop")
    git_sandbox.sh(root, "merge", "-q", "--no-ff", "release/v2.0.0", "-m", "release branch")
    assert not tagging.overtaken(git_mod.Git(root), git_sandbox.sh(root, "rev-parse", "HEAD").strip())


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

    production = opt("--production")
    pr = req("--pr", int)
    target = [pr, production, opt("--plugin")]
    budget = req("--budget", float)
    answer = [
        opt("--route", "gate", ("gate", "github-merge")),
        opt("--answer", choices=("approved", "rejected")),
        opt("--presented"),
        opt("--by", choices=("user", "mvv")),
        production,
    ]
    branches = ("--branch", True, None, None, None, "_AppendAction")
    expected = {
        "assemble": common + target,
        "materials": common + target + [opt("--out")],
        "present": common + [pr, opt("--material")],
        "answer": common + [pr, *answer],
        "approval-check": common + [pr, production],
        "merge": common + [pr, production, budget],
        "confirm": common + [pr, production, budget],
        "guard": common + [branches, production],
        "bump": common + [production, budget],
        "pr-check": common + [pr],
        "verify-ref": common,
        "record": common + [pr, production],
        "verification-record": common + [pr, req("--record")],
        "records": common + [pr],
        "publish": common + [req("--revision")],
        "sync-base": common + [req("--main-dir"), req("--base")],
        "config-snapshot": common + [req("--out")],
        "config-check": common + [req("--before")],
        "cleanup": common + [("--branch", False, None, None, None, "_AppendAction"), (*opt("--pr")[:4], int, "_StoreAction")],
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
    assert vars(args) == {"command": "cleanup", "root": ".", "repo": None, "branch": ["a", "b"], "pr": None}
    args = parser.parse_args(["merge", "--pr", "3", "--production", "main", "--budget", "1.5"])
    assert (args.pr, args.budget, args.production) == (3, 1.5, "main")


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
    """ハンドラーの表（HANDLERS）は引数の表（ARGS）のハンドラーを同じ順に持つ。"""
    assert list(release_cli.HANDLERS.values()) == list(release_cli.ARGS)
