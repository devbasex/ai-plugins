"""マーケットプレイスとバージョンの書き換え・設定・タグと GitHub Release・worktree のクリーンアップ・サブコマンド。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.adapters import git as git_mod
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import cleanup, names_decide, tagging
from ndf.domains.release import cli as release_cli
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import plugins as plugins_mod
from ndf.domains.release import version_store as store_mod

from ._fakes import FakeHub, init_repo, not_found, pull, section, strategy, write_json


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
    """#1756: 名前と保存先はマーケットプレイスの登録から読む（`plugins/<名前>` の形を仮定しない）。"""
    root = _repo(tmp_path)
    found = plugins_mod.load(root)
    assert [(p.name, p.dir) for p in found] == [("kit", "tools/kit"), ("mcp-x", "plugins/mcp/x")]
    assert plugins_mod.version(root, found[1]) == "0.1.0"


def test_bump_writes_manifests_marketplace_and_declared_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    kit = plugins_mod.by_name(plugins_mod.load(root))["kit"]
    write_json(root / "package.json", {"name": "kit", "meta": {"version": "1.2.3"}})
    # 設定の release.version_store.files の行。pointer の無い行は名前を含む行、ある行は JSON のその位置を書き換える
    files = [{"path": "README.md"}, {"path": "package.json", "pointer": "/meta/version"}, {"path": "missing.md"}]
    written = plugins_mod.bump(root, kit, "1.2.4", files)
    assert written == [
        "tools/kit/.claude-plugin/plugin.json",
        "tools/kit/.codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "README.md",
        "package.json",
    ]
    assert plugins_mod.version(root, kit) == "1.2.4"
    assert "Kit (v1.2.4)" in (root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    assert (root / "README.md").read_text(encoding="utf-8") == "| kit | 1.2.4 |\n| other | 1.2.3 |\n"
    assert json.loads((root / "package.json").read_text(encoding="utf-8"))["meta"]["version"] == "1.2.4"


def test_versions_must_agree_between_manifests(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    write_json(root / "tools" / "kit" / ".codex-plugin" / "plugin.json", {"name": "kit", "version": "9.9.9"})
    with pytest.raises(plugins_mod.PluginsError, match="そろわない"):
        plugins_mod.version(root, plugins_mod.load(root)[0])


def test_ndf_settings_defaults_and_steps(tmp_path: Path) -> None:
    assert decl_mod.load(tmp_path) == decl_mod.NdfSettings()
    write_json(
        decl_mod.path_of(tmp_path),
        {"version": 1, "bump_undistributed": True, "steps": [{"name": "記録", "stage": "production", "command": ["true", "{version}"]}]},
    )
    got = decl_mod.load(tmp_path)
    assert got.bump_undistributed and [s.name for s in got.for_stage("production")] == ["記録"] and got.for_stage("dev") == []
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "steps": [{"name": "x", "stage": "nowhere", "command": ["true"]}]})
    with pytest.raises(decl_mod.NdfSettingsError):
        decl_mod.load(tmp_path)


def test_declared_step_failure_is_reported(tmp_path: Path) -> None:
    with pytest.raises(decl_mod.StepFailed, match="終了コード 1"):
        decl_mod.run_step(tmp_path, decl_mod.Step("落ちる", "production", ("false",)), "1.0.0")


def test_declared_step_runs_without_the_release_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """設定の手順へは、GitHub の認証の値と環境変数で渡す git の設定（送り先の認証のヘッダー）を渡さない。"""
    for name, value in (("GH_TOKEN", "t1"), ("GITHUB_TOKEN", "t2"), ("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_VALUE_0", "h")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NDF_KEEP", "kept")
    script = "env | grep -E '^(GH_TOKEN|GITHUB_TOKEN|GIT_CONFIG_|NDF_KEEP)' | cut -d= -f1 | sort > ENV.txt"
    decl_mod.run_step(tmp_path, decl_mod.Step("環境", "production", ("sh", "-c", script)), "1.0.0")
    assert (tmp_path / "ENV.txt").read_text(encoding="utf-8").split() == ["NDF_KEEP"]


def _bumped_repo(tmp_path: Path) -> tuple[Path, str]:
    """kit を 1.2.2 から 1.2.3 へ上げたコミットを HEAD に持つリポジトリと、その SHA。"""
    root = _repo(init_repo(tmp_path / "repo"))
    for rel in ("tools/kit/.claude-plugin/plugin.json", "tools/kit/.codex-plugin/plugin.json"):
        write_json(root / rel, {"name": "kit", "version": "1.2.2"})
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "init")
    for rel in ("tools/kit/.claude-plugin/plugin.json", "tools/kit/.codex-plugin/plugin.json"):
        write_json(root / rel, {"name": "kit", "version": "1.2.3"})
    (root / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 直した（#5）\n", encoding="utf-8")
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "Release: kit v1.2.3")
    return root, git_sandbox.sh(root, "rev-parse", "HEAD").strip()


def test_publish_creates_missing_tag_and_release_for_bumped_plugins(tmp_path: Path) -> None:
    root, head = _bumped_repo(tmp_path)
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/git/ref/tags/kit--v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/kit--v1.2.3")] = not_found()
    made = tagging.publish(root, hub, head, strategy("plugin"))
    assert [m.item() for m in made] == [
        {"kind": "tag", "name": "kit--v1.2.3", "result": "created"},
        {"kind": "release", "name": "kit--v1.2.3", "result": "created"},
    ]
    release = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/releases"))
    assert release["body"] == "- 直した（#5）" and release["name"] == "kit v1.2.3"


@pytest.mark.parametrize(
    ("ref", "objects", "want"),
    [
        ({"object": {"type": "commit", "sha": "HEAD"}}, {}, "exists"),
        ({"object": {"type": "tag", "sha": "t" * 40}}, {"t" * 40: "HEAD"}, "exists"),
        ({"object": {"type": "commit", "sha": "e" * 40}}, {}, "moved"),
        ({"object": {"type": "tag", "sha": "t" * 40}}, {"t" * 40: "e" * 40}, "moved"),
    ],
)
def test_issue_3615_publish_compares_where_an_existing_tag_points(tmp_path: Path, ref: dict, objects: dict, want: str) -> None:
    """I7: 同じ名前のタグがあれば、コミットまでたどった指す先（注釈付きタグはタグオブジェクトの先）を比べ、違えば止まる。"""
    root, head = _bumped_repo(tmp_path)
    hub = FakeHub()
    fill = {"HEAD": head}
    ref = {"object": {**ref["object"], "sha": fill.get(ref["object"]["sha"], ref["object"]["sha"])}}
    hub.responses[("GET", "/repos/o/r/git/ref/tags/kit--v1.2.3")] = ref
    for sha, target in objects.items():
        hub.responses[("GET", f"/repos/o/r/git/tags/{sha}")] = {"object": {"type": "commit", "sha": fill.get(target, target)}}
    if want == "moved":
        with pytest.raises(tagging.TagMoved, match="kit--v1.2.3"):
            tagging.publish(root, hub, head, strategy("plugin"))
        assert not [r for r in hub.requests if r[0] == "POST"]
        assert release_cli._stopped_of(tagging.TagMoved("x"))["cause"]["kind"] == "refused"
        return
    made = tagging.publish(root, hub, head, strategy("plugin"))
    assert made[0] == tagging.Made("tag", "kit--v1.2.3", "exists")


def _tag_only() -> Strategy:
    """タグだけの保存先（バージョンのファイルを持たない）で、タグ `v{version}` と GitHub Release を作る設定。"""
    got = section("mobile")
    got["release"].update(version_store={"kind": "tag"}, tag="v{version}", github_release=True)
    return Strategy(got)


_REV = "c" * 40
_MERGED = "2026-11-17T00:00:00Z"


def _tag_only_repo(tmp_path: Path, hub: FakeHub, rows: list) -> tuple[Path, str]:
    """タグだけの保存先のリポジトリ（コミット 1 つ）とその SHA。`rows` の `merge_commit_sha` の `_REV` をその SHA にして返す。"""
    root = init_repo(tmp_path / "repo")
    git_sandbox.sh(root, "commit", "-q", "--allow-empty", "-m", "init")
    sha = git_sandbox.sh(root, "rev-parse", "HEAD").strip()
    hub.responses[("GET", f"/repos/o/r/commits/{sha}/pulls")] = [
        {**r, "merge_commit_sha": sha} if r["merge_commit_sha"] == _REV else r for r in rows
    ]
    return root, sha


def _pr_row(head: str, merged: bool = True, base: str = "main", merge_sha: str = _REV) -> dict:
    """`commits/<SHA>/pulls` の行（マージ済みなら `merged_at` と `merge_commit_sha` を持つ）。"""
    return {"merged_at": _MERGED if merged else None, "merge_commit_sha": merge_sha, "base": {"ref": base}, "head": {"ref": head}}


@pytest.mark.parametrize(
    ("rows", "want"),
    [
        # マージしていない PR・書式に合わない PR を飛ばし、最初に合うマージ済みの PR の head を読む
        (
            [
                _pr_row("release/v9.9.9", merged=False),
                _pr_row("feat/x"),
                _pr_row("release/v1.2.3"),
                _pr_row("release/v1.2.4"),
            ],
            ["v1.2.3"],
        ),
        ([_pr_row("feat/x")], []),
        ([], []),
        # 接尾辞の付いたバージョン（開発版）は出さない
        ([_pr_row("release/v1.2.3-dev.1")], []),
        # 別のブランチへマージした PR・このコミットをマージのコミットにしない PR（コミットを含むだけ）のバージョンでは作らない
        ([_pr_row("release/v1.2.5", base="develop"), _pr_row("release/v1.2.6", merge_sha="d" * 40)], []),
        ([_pr_row("release/v1.2.5", base="develop"), _pr_row("release/v1.2.3")], ["v1.2.3"]),
    ],
)
def test_publish_with_tag_only_store_reads_the_merged_release_branch(tmp_path: Path, rows: list, want: list) -> None:
    """ファイルを持たない保存先は、そのコミットで本番ブランチへ入ったマージ済みの PR の head の名前からバージョンを読む。"""
    hub = FakeHub()
    root, sha = _tag_only_repo(tmp_path, hub, rows)
    for tag in want:
        hub.responses[("GET", f"/repos/o/r/git/ref/tags/{tag}")] = not_found()
        hub.responses[("GET", f"/repos/o/r/releases/tags/{tag}")] = not_found()
    made = tagging.publish(root, hub, sha, _tag_only())
    assert [m.name for m in made if m.kind == "tag"] == want
    posted = [path for m, path, _ in hub.requests if m == "POST"]
    assert posted == (["/repos/o/r/git/refs", "/repos/o/r/releases"] if want else [])


def test_issue_3615_publish_after_a_merged_sprint_pull_reads_no_commit(tmp_path: Path) -> None:
    """そのコミットで本番へマージした PR があれば squash の早送りでないため、コミットを読み足さない（API の呼び出しを増やさない）。"""
    hub = FakeHub()
    root, sha = _tag_only_repo(tmp_path, hub, [_pr_row("feat/x")])
    assert tagging.publish(root, hub, sha, _tag_only()) == []
    assert [path for m, path, _ in hub.requests if m == "GET" and "/git/commits/" in path] == []


@pytest.mark.parametrize(("latest", "want"), [("v1.3.0", "false"), ("v1.2.0", "true"), ("other-tag", "true"), (None, "true")])
def test_publish_keeps_a_newer_latest_release(tmp_path: Path, latest: str | None, want: str) -> None:
    """古いバージョンを後から公開・打ち直しても、今の Latest が同じ単位のより新しいバージョンなら Latest にしない。"""
    hub = FakeHub()
    root, sha = _tag_only_repo(tmp_path, hub, [_pr_row("release/v1.2.3")])
    hub.responses[("GET", "/repos/o/r/git/ref/tags/v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/latest")] = {"tag_name": latest} if latest else not_found()
    tagging.publish(root, hub, sha, _tag_only())
    release = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/releases"))
    assert release["make_latest"] == want


def test_publish_with_tag_only_store_reads_a_release_closed_by_strict_squash(tmp_path: Path) -> None:
    """`strict` の squash で閉じて本番へ入れたリリースの PR も、正規の squash（`record.landed_squash`）ならタグを作る。"""
    hub = FakeHub()
    root, sha = _tag_only_repo(tmp_path, hub, [])
    approved = "h" * 40
    hub.pulls[7] = pull(7, head=approved, ref="release/v1.2.3")._replace(state="closed")
    message = f"Land #7\n\nNDF-Landed-PR: 7\nNDF-Approved-Head: {approved}\nNDF-Tip: {'t' * 40}\n"
    hub.responses[("GET", f"/repos/o/r/git/commits/{sha}")] = {"message": message, "tree": {"sha": "w" * 40}}
    hub.responses[("GET", f"/repos/o/r/git/commits/{approved}")] = {"message": "Release", "tree": {"sha": "w" * 40}}
    hub.responses[("GET", f"/repos/o/r/compare/main...{sha}")] = {"status": "identical"}
    hub.responses[("GET", "/repos/o/r/git/ref/tags/v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/v1.2.3")] = not_found()
    made = tagging.publish(root, hub, sha, _tag_only())
    assert [m.name for m in made if m.kind == "tag"] == ["v1.2.3"]


def test_publish_walks_the_pushed_range_and_reads_each_commit(tmp_path: Path) -> None:
    """`since` を渡せば、その push で入った first-parent のコミットを古い順に公開する。版上げのコミットの CHANGELOG を本文にし、
    作業場所の HEAD（後のコミット）の CHANGELOG を混ぜない。一時の作業場所は残さない。"""
    root, bumped = _bumped_repo(tmp_path)
    before = git_sandbox.sh(root, "rev-parse", f"{bumped}^").strip()
    (root / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 書き換えた\n", encoding="utf-8")
    git_sandbox.sh(root, "commit", "-q", "-am", "後の直し")
    after = git_sandbox.sh(root, "rev-parse", "HEAD").strip()
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/git/ref/tags/kit--v1.2.3")] = not_found()
    hub.responses[("GET", "/repos/o/r/releases/tags/kit--v1.2.3")] = not_found()
    made = tagging.publish(root, hub, after, strategy("plugin"), since=before)
    assert [m.name for m in made] == ["kit--v1.2.3", "kit--v1.2.3"]
    tag = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/git/refs"))
    release = next(p for m, path, p in hub.requests if m == "POST" and path.endswith("/releases"))
    assert tag["sha"] == bumped and release["body"] == "- 直した（#5）"
    assert len(git_mod.Git(root).worktrees()) == 1


def test_publish_refuses_when_the_plugin_definitions_disagree(tmp_path: Path) -> None:
    """Claude Code の定義だけを上げたコミットでは、Codex の定義が旧いままなので正式タグを作らずに止まる。"""
    root = _repo(init_repo(tmp_path / "repo"))
    for rel in ("tools/kit/.claude-plugin/plugin.json", "tools/kit/.codex-plugin/plugin.json"):
        write_json(root / rel, {"name": "kit", "version": "1.2.2"})
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "init")
    write_json(root / "tools/kit/.claude-plugin/plugin.json", {"name": "kit", "version": "1.2.3"})
    git_sandbox.sh(root, "commit", "-q", "-am", "片方だけ")
    hub = FakeHub()
    with pytest.raises(store_mod.StoreError, match="そろわない"):
        tagging.publish(root, hub, git_sandbox.sh(root, "rev-parse", "HEAD").strip(), strategy("plugin"))
    assert not [r for r in hub.requests if r[0] == "POST"]


@pytest.mark.parametrize("version", ["1.2.2", "1.2.3-dev.1"])
def test_publish_with_version_files_skips_unchanged_and_dev_versions(tmp_path: Path, version: str) -> None:
    """現状固定（I-003）: ファイルを持つ保存先は、第 1 親とバージョンが同じ単位と開発版を出さない（何も書かない）。"""
    root = _repo(init_repo(tmp_path / "repo"))
    for rel in ("tools/kit/.claude-plugin/plugin.json", "tools/kit/.codex-plugin/plugin.json"):
        write_json(root / rel, {"name": "kit", "version": "1.2.2"})
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "init")
    for rel in ("tools/kit/.claude-plugin/plugin.json", "tools/kit/.codex-plugin/plugin.json"):
        write_json(root / rel, {"name": "kit", "version": version})
    (root / "tools/kit/main.py").write_text("changed = True\n", encoding="utf-8")
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "next")
    hub = FakeHub()
    assert tagging.publish(root, hub, git_sandbox.sh(root, "rev-parse", "HEAD").strip(), strategy("plugin")) == []
    assert hub.requests == []


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
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: hub)
    code = release_cli.main(["approval-check", "--pr", "7", "--production", "main"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "stopped" and out["cause"]["kind"] == "not_applicable" and code != 0


def test_approval_check_skips_sprint_pulls_on_trunk(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """trunk で本番宛てでも、head がリリースブランチの書式に合わない PR は証跡を読まず not_applicable で終える。"""
    hub = FakeHub(pulls={7: pull(ref="sprint/x")})
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: hub)
    monkeypatch.setattr(release_cli, "_strategy", lambda args: strategy("plugin"))
    monkeypatch.setattr(release_cli.trail, "check", lambda *a, **k: pytest.fail("証跡を読んだ"))
    code = release_cli.main(["approval-check", "--pr", "7", "--production", "main"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "stopped" and out["cause"]["kind"] == "not_applicable" and code != 0
    assert hub.requests == []


def _options(parser: argparse.ArgumentParser) -> dict[str, list[tuple]]:
    """サブコマンドごとの引数の (名前, 必須, 既定, 選択肢, 型, 種類)。"""
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
    """現状固定（_parser）: サブコマンドの並びと、サブコマンドごとの引数の必須・既定・選択肢・型。"""
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
        ("--units", False, (), None, None, "_StoreAction"),
        ("--pr", False, None, None, int, "_StoreAction"),
    ]
    pr = req("--pr", int)
    expected = {
        "assemble": common + target,
        "materials": common + target + [opt("--out")],
        "open-releases": common + [req("--base"), req("--production")],
        "present": common + [pr, opt("--material")],
        "answer": common
        + [
            pr,
            req("--answer", choices=("approved", "rejected")),
            req("--presented"),
            opt("--material-sha256"),
            req("--by", choices=("user", "mvv")),
        ],
        "approval-check": common + [pr, req("--production")],
        "approval-state": common + [pr],
        "pull-checks": common + [pr],
        "merge": common + [pr, req("--budget", float)],
        "record": common + [pr, req("--production"), opt("--production-sha")],
        "verification-record": common + [pr, req("--record")],
        "records": common + [pr],
        "publish": common + [req("--revision"), opt("--since")],
        "sync-base": common + [req("--main-dir"), req("--base")],
        "config-snapshot": common + [req("--out")],
        "config-check": common + [req("--before")],
        "cleanup": common + [("--branch", True, None, None, None, "_AppendAction")],
    }
    got = _options(release_cli._parser())
    assert list(got) == list(expected) == list(release_cli.HANDLERS)
    assert got == expected


def test_release_steps_parser_parses_and_requires_a_command() -> None:
    """現状固定（_parser）: サブコマンドは必須で、引数は名前空間の属性になる。"""
    parser = release_cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args(["cleanup", "--branch", "a", "--branch", "b"])
    assert vars(args) == {"command": "cleanup", "root": ".", "repo": None, "branch": ["a", "b"]}
    # merge のブランチの名前は設定（branch_strategy.branches）から読み、引数で受けない
    args = parser.parse_args(["merge", "--pr", "3", "--budget", "1.5"])
    assert (args.pr, args.budget) == (3, 1.5) and not hasattr(args, "base")


def test_ndf_settings_parse_reads_every_key(tmp_path: Path) -> None:
    """現状固定（_parse）: すべてのキーを読む。`undistributed` は既定（テストのファイルの表）へ足す形で、無ければ空。生の設定を `raw` に持つ。"""
    raw = {
        "version": 1,
        "steps": [{"name": "a", "stage": "verify", "command": ["echo", "{version}"], "writes": ["x"], "timeout_seconds": 5}],
        "undistributed": ["docs/"],
        "bump_undistributed": True,
        "changelog": "docs/CL.md",
    }
    write_json(decl_mod.path_of(tmp_path), raw)
    got = decl_mod.load(tmp_path)
    assert got == decl_mod.NdfSettings((decl_mod.Step("a", "verify", ("echo", "{version}"), ("x",), 5.0),), ("docs/",), True, "docs/CL.md")
    assert got.raw == raw
    write_json(decl_mod.path_of(tmp_path), {"version": 1, "undistributed": []})
    assert decl_mod.load(tmp_path) == decl_mod.NdfSettings(undistributed=())
    write_json(decl_mod.path_of(tmp_path), {"version": 1})
    assert decl_mod.load(tmp_path) == decl_mod.NdfSettings()


# NOTE: 現状固定。timeout_seconds に真偽の true を書くと 1 秒として通る（bool は int の一種）。直すなら別の変更で行う。
def test_ndf_settings_parse_accepts_bool_timeout_as_number(tmp_path: Path) -> None:
    write_json(
        decl_mod.path_of(tmp_path), {"version": 1, "steps": [{"name": "a", "stage": "dev", "command": ["t"], "timeout_seconds": True}]}
    )
    assert decl_mod.load(tmp_path).steps == (decl_mod.Step("a", "dev", ("t",), (), 1.0),)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"version": 2}, "設定の version が 1 でない: 2"),
        ({}, "設定の version が 1 でない: None"),
        ({"version": "1"}, "設定の version が 1 でない: '1'"),
        ({"version": 1, "steps": {}}, "steps は配列"),
        ({"version": 1, "bump_undistributed": "yes"}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "bump_undistributed": None}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "changelog": 3}, "bump_undistributed は真偽、changelog は文字列"),
        ({"version": 1, "undistributed": "x"}, "undistributed は文字列の配列"),
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
def test_ndf_settings_parse_errors(tmp_path: Path, raw: dict, message: str) -> None:
    """現状固定（_parse）: 形の違う設定は、キーごとの文言の `NdfSettingsError`。"""
    write_json(decl_mod.path_of(tmp_path), raw)
    with pytest.raises(decl_mod.NdfSettingsError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value) == message


def test_ndf_settings_load_errors_name_the_file(tmp_path: Path) -> None:
    """現状固定（load）: 読めない・オブジェクトでない設定はファイルのパスを書いた `NdfSettingsError`。"""
    path = decl_mod.path_of(tmp_path)
    assert path == tmp_path / ".ndf" / "release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(decl_mod.NdfSettingsError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value) == f"{path} が JSON のオブジェクトでない"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(decl_mod.NdfSettingsError) as caught:
        decl_mod.load(tmp_path)
    assert str(caught.value).startswith(f"{path} を読めない: ")


def test_release_steps_args_table_matches_handlers() -> None:
    """ハンドラーの表（HANDLERS）は引数の表（ARGS）のハンドラーを同じ順に持つ。"""
    assert list(release_cli.HANDLERS.values()) == list(release_cli.ARGS)


def test_name_formats_current_behavior() -> None:
    """現状固定（I-021）: 名前の書式の作る側と読み戻す側。タグと見出しは基底のバージョン（接尾辞を落とす）で作り、
    リリースブランチは接尾辞もビルドのメタデータも残す。リリースブランチの `{plugin}` は空で埋め、`{version}` の無い書式は読み戻さない。"""
    default = names_decide.NameFormats(None)
    assert default.branch_of("1.2.3-dev.4") == "release/v1.2.3-dev.4" and default.branch_of("1.2.3+b.1") == "release/v1.2.3+b.1"
    with pytest.raises(names_decide.version_decide.VersionError, match="バージョンとして読めない"):
        default.branch_of("1.2")
    assert default.version_of_branch("release/v1.2.3-dev.4") == "1.2.3-dev.4" and default.is_release_branch("release/v1.2.3-dev.4")
    assert not default.is_release_branch("release/v01.2.3")
    with pytest.raises(names_decide.FormatMismatch, match=r"リリースブランチ（release/v\{version\}）でない: 'release/v1.2'"):
        default.version_of_branch("release/v1.2")
    assert default.heading_of("kit", "1.2.3-dev.4", "2026-11-17") == "## [kit 1.2.3] - 2026-11-17"
    assert default.heading_pattern("kit", "1.2.3-dev.4").fullmatch("## [kit 1.2.3] - 2026-11-17")

    custom = names_decide.NameFormats(
        "{plugin}/v{version}/{version}", branch="{plugin}rel/{version}", changelog_heading="### {plugin} v{version} ({date})"
    )
    assert custom.tag_of("a.b", "1.2.3-dev.4") == "a.b/v1.2.3/1.2.3"
    assert custom.version_of_tag("a.b", "a.b/v1.2.3/1.2.3") == "1.2.3" and custom.version_of_tag("a.b", "a.b/v1.2.3/1.2.4") is None
    assert custom.branch_of("1.2.3") == "rel/1.2.3" and custom.version_of_branch("rel/1.2.3") == "1.2.3"
    assert not custom.is_release_branch("a.brel/1.2.3")
    assert custom.heading_of("a.b", "1.2.3-dev.4", "2026-11-17") == "### a.b v1.2.3 (2026-11-17)"
    assert custom.heading_pattern("a.b", "1.2.3-dev.4").fullmatch("### a.b v1.2.3 (2026-11-17)")

    fixed = names_decide.NameFormats("latest", branch="release")
    assert fixed.tag_of("app", "1.2.3") == "latest" and fixed.version_of_tag("app", "latest") is None
    assert fixed.branch_of("1.2.3") == "release" and not fixed.is_release_branch("release")
    with pytest.raises(names_decide.FormatMismatch):
        fixed.version_of_branch("release")
