"""リリースのブランチの組み立て（本物の git と、偽の GitHub）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters import git as git_mod
from ndf.domains.release import assemble, changes_decide, checks_decide, gather, materials_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import FakeHub, clone, git, init_repo, write_json


def _node(number: int, title: str, conclusion: str) -> dict:
    run = {"__typename": "CheckRun", "name": "pytest", "status": "COMPLETED", "conclusion": conclusion}
    skipped = {"__typename": "CheckRun", "name": "smoke", "status": "COMPLETED", "conclusion": "SKIPPED"}
    status = {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS"}
    rollup = {"contexts": {"nodes": [run, skipped, status]}}
    return {"number": number, "title": title, "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]}}


def _origin_with_change(tmp_path: Path, test_only: bool, extra: tuple[str, ...] = ()) -> Path:
    """本番（main）の後に、ベースブランチ（develop）へ PR #5 をマージした origin と、その clone。`extra` は同じ PR で足すファイル。"""
    seed = init_repo(tmp_path / "seed")
    write_json(
        seed / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]}
    )
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (seed / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 前\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "main")
    git(seed, "switch", "-q", "-c", "feat/x")
    changed = seed / "tools" / "kit" / ("tests/test_a.py" if test_only else "a.py")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("x = 1\n", encoding="utf-8")
    for rel in extra:
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text("{}\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "change")
    git(seed, "switch", "-q", "develop")
    git(seed, "merge", "-q", "--no-ff", "feat/x", "-m", "Merge pull request #5 from o/feat/x")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    return clone(tmp_path, origin, "work")


def _hub() -> FakeHub:
    hub = FakeHub(node_data={5: _node(5, "直した", "SUCCESS")})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 9, "html_url": "https://example.invalid/pull/9"}
    hub.responses[("PATCH", "/repos/o/r/issues/9")] = {"html_url": "https://example.invalid/pull/9"}
    return hub


def test_assemble_builds_branch_and_pull_with_materials(tmp_path: Path) -> None:
    work = _origin_with_change(tmp_path, test_only=False)
    hub = _hub()
    built = assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert (built.branch, built.version, built.pr) == ("release/v1.2.4", "1.2.4", 9)
    shown = git(work, "show", "origin/release/v1.2.4:tools/kit/.claude-plugin/plugin.json")
    assert json.loads(shown)["version"] == "1.2.4"
    log = git(work, "show", "origin/release/v1.2.4:CHANGELOG.md")
    assert log.index("## [kit 1.2.4]") < log.index("## [kit 1.2.3]") and "- 直した（#5）" in log
    create = next(p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/pulls"))
    assert (create["head"], create["base"]) == ("release/v1.2.4", "main")
    body = next(p for m, path, p in hub.requests if m == "PATCH")["body"]
    assert "- #5 直した（CI 2/2（スキップ 1））" in body and f"`{built.facts.head}`" in body
    assert built.facts.head == git(work, "rev-parse", "HEAD").strip()


def test_issue_2217_tests_only_change_is_not_released(tmp_path: Path) -> None:
    """#2217: テストだけの変化は、宣言が無ければ版を上げず、ブランチも PR も作らない。"""
    work = _origin_with_change(tmp_path, test_only=True)
    hub = _hub()
    with pytest.raises(assemble.NothingToRelease):
        assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert hub.requests == []


def test_collect_current_behavior(tmp_path: Path) -> None:
    """現状固定（collect）: 範囲の変化から版上げの計画と承認資料の材料を返し、ブランチも PR も作らない。"""
    work = _origin_with_change(tmp_path, test_only=False)
    hub = _hub()
    facts = assemble.collect(work, hub, assemble.Target("production", "patch", "develop", "main"))
    expected = [version_decide.Planned("kit", "1.2.3", "1.2.4")]
    assert list(facts.planned) == expected
    assert facts == materials_decide.Facts(
        "production",
        tuple(expected),
        (materials_decide.PullFact(5, "直した", checks_decide.Tally(2, 1, (), ())),),
        (changes_decide.Change("kit", ("tools/kit/a.py",), ()),),
        (),
        git(work, "rev-parse", "origin/develop").strip(),
    )
    assert hub.requests == []
    assert git(work, "branch", "--list").strip() == "* develop"
    assert git(work, "status", "--porcelain") == ""


def test_collect_dev_channel_plans_dev_version(tmp_path: Path) -> None:
    """現状固定（collect）: 開発版のチャネルは接尾辞の付いた次の版を計画する。"""
    work = _origin_with_change(tmp_path, test_only=False)
    facts = assemble.collect(work, _hub(), assemble.Target("dev", "minor", "develop", "main"))
    assert list(facts.planned) == [version_decide.Planned("kit", "1.2.3", "1.3.0-dev.1")]
    assert facts.channel == "dev" and facts.changes == (changes_decide.Change("kit", ("tools/kit/a.py",), ()),)


def test_collect_without_bump_raises_with_span(tmp_path: Path) -> None:
    """現状固定（collect）: 上げるものが無ければ範囲を書いた `NothingToRelease`。"""
    work = _origin_with_change(tmp_path, test_only=True)
    with pytest.raises(assemble.NothingToRelease, match="^origin/main..origin/develop に版を上げる変化が無い$"):
        assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main"))


def test_collect_mixed_range_splits_changes_and_instructions(tmp_path: Path) -> None:
    """現状固定（collect）: 同じ範囲のリリース対象・テスト・指示書と `.ndf/` の変化を、Facts.changes と Facts.instructions に分ける。"""
    extra = ("tools/kit/tests/test_b.py", "AGENTS.md", ".ndf/worktree.json")
    work = _origin_with_change(tmp_path, test_only=False, extra=extra)
    facts = assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert facts.changes == (changes_decide.Change("kit", ("tools/kit/a.py",), ("tools/kit/tests/test_b.py",)),)
    assert facts.instructions == (".ndf/worktree.json", "AGENTS.md")


@pytest.mark.parametrize(
    ("test_only", "expected"),
    [
        (False, [changes_decide.Change("kit", ("tools/kit/a.py",), ())]),
        (True, [changes_decide.Change("kit", (), ("tools/kit/tests/test_a.py",))]),
    ],
)
def test_gather_changes_current_behavior(tmp_path: Path, test_only: bool, expected: list[changes_decide.Change]) -> None:
    """現状固定（gather.changes）: 範囲の変わったファイルをプラグインごとに配る・配らないへ分ける。空の範囲は空。"""
    work = _origin_with_change(tmp_path, test_only=test_only)
    repo_git = git_mod.Git(work)
    found, decl = plugins_mod.load(work), decl_mod.load(work)
    assert gather.changes(repo_git.changed_files("origin/main", "origin/develop"), found, decl) == expected
    assert gather.changes(repo_git.changed_files("origin/develop", "origin/develop"), found, decl) == []


def _develop_ahead(tmp_path: Path) -> Path:
    """develop の版を 1.3.0 へ上げた origin の、main を取り出した clone。"""
    work = _origin_with_change(tmp_path, test_only=False)
    write_json(work / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.3.0"})
    git(work, "commit", "-q", "-am", "bump")
    git(work, "push", "-q", "origin", "develop")
    return clone(tmp_path, tmp_path / "origin.git", "on-main", branch="main")


def test_assemble_plans_from_base_tip_even_when_started_on_production(tmp_path: Path) -> None:
    """既定ブランチ（main）で起動しても、版は範囲の先端（develop）から読んで計画する（版が逆行しない）。"""
    work = _develop_ahead(tmp_path)
    built = assemble.assemble(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert built.version == "1.3.1"


def test_collect_refuses_when_head_is_not_base_tip(tmp_path: Path) -> None:
    """collect はworktreeが範囲の先端でなければ止まる（ほかのブランチの版から計画しない）。"""
    work = _develop_ahead(tmp_path)
    with pytest.raises(assemble.AssembleError, match="origin/develop の先端でない"):
        assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main"))


def _develop_at(tmp_path: Path, develop: str, main: str | None = None, tags: tuple[str, ...] = ()) -> Path:
    """develop の版を `develop` にした origin の clone。`main` を渡せば本番ブランチの版もそれにし、`tags` のタグを origin に置く。"""
    work = _origin_with_change(tmp_path, test_only=False)
    manifest = work / "tools" / "kit" / ".claude-plugin" / "plugin.json"
    write_json(manifest, {"name": "kit", "version": develop})
    git(work, "commit", "-q", "-am", "dev bump")
    git(work, "push", "-q", "origin", "develop")
    if main is not None:
        git(work, "switch", "-q", "main")
        write_json(manifest, {"name": "kit", "version": main})
        git(work, "commit", "-q", "-am", "released")
        git(work, "push", "-q", "origin", "main")
        git(work, "switch", "-q", "develop")
    for tag in tags:
        git(work, "tag", tag, "origin/main")
        git(work, "push", "-q", "origin", tag)
    return work


def test_issue_2554_production_skips_tagged_version(tmp_path: Path) -> None:
    """#2554: develop の版の基底（1.2.4）が既にタグにあれば、その版数で組まず次に空いている版へ繰り上げる。"""
    work = _develop_at(tmp_path, "1.2.4-dev.2", tags=("kit--v1.2.4",))
    facts = assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert list(facts.planned) == [version_decide.Planned("kit", "1.2.4-dev.2", "1.2.5")]


def test_issue_2554_reversed_reservation_stays_above_production(tmp_path: Path) -> None:
    """#2554: 低い版を予約したレーンが後に出しても（本番 1.2.5、develop 1.2.4-dev.1）、本番の版より大きい版で組む。"""
    work = _develop_at(tmp_path, "1.2.4-dev.1", main="1.2.5")
    facts = assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert [p.next for p in facts.planned] == ["1.2.6"]


def test_issue_2554_dev_channel_skips_shipped_base(tmp_path: Path) -> None:
    """#2554: 開発版のチャネルも、出し済みの基底の接尾辞の版を組まない。"""
    work = _develop_at(tmp_path, "1.2.4-dev.2", tags=("kit--v1.2.4",))
    facts = assemble.collect(work, _hub(), assemble.Target("dev", "patch", "develop", "main"))
    assert [p.next for p in facts.planned] == ["1.2.5-dev.1"]
