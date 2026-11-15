"""リリースのブランチの組み立て（本物の git と、偽の GitHub）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import git_sandbox
import pytest

from ndf.adapters import git as git_mod
from ndf.domains.release import approval_decide, assemble, changes_decide, checks_decide, gather, materials_decide, version_decide
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import FakeHub, clone, init_repo, write_json
from ._fakes import pull as fake_pull


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
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "init")
    git_sandbox.sh(seed, "branch", "main")
    git_sandbox.sh(seed, "switch", "-q", "-c", "feat/x")
    changed = seed / "tools" / "kit" / ("tests/test_a.py" if test_only else "a.py")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("x = 1\n", encoding="utf-8")
    for rel in extra:
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text("{}\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "change")
    git_sandbox.sh(seed, "switch", "-q", "develop")
    git_sandbox.sh(seed, "merge", "-q", "--no-ff", "feat/x", "-m", "Merge pull request #5 from o/feat/x")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
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
    shown = git_sandbox.sh(work, "show", "origin/release/v1.2.4:tools/kit/.claude-plugin/plugin.json")
    assert json.loads(shown)["version"] == "1.2.4"
    log = git_sandbox.sh(work, "show", "origin/release/v1.2.4:CHANGELOG.md")
    assert log.index("## [kit 1.2.4]") < log.index("## [kit 1.2.3]") and "- 直した（#5）" in log
    create = next(p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/pulls"))
    assert (create["head"], create["base"]) == ("release/v1.2.4", "main")
    body = next(p for m, path, p in hub.requests if m == "PATCH")["body"]
    assert "- #5 直した（CI 2/2（スキップ 1））" in body and f"`{built.facts.head}`" in body
    assert built.facts.head == git_sandbox.sh(work, "rev-parse", "HEAD").strip()


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
        git_sandbox.sh(work, "rev-parse", "origin/develop").strip(),
    )
    assert hub.requests == []
    assert git_sandbox.sh(work, "branch", "--list").strip() == "* develop"
    assert git_sandbox.sh(work, "status", "--porcelain") == ""


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
    git_sandbox.sh(work, "commit", "-q", "-am", "bump")
    git_sandbox.sh(work, "push", "-q", "origin", "develop")
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
    git_sandbox.sh(work, "commit", "-q", "-am", "dev bump")
    git_sandbox.sh(work, "push", "-q", "origin", "develop")
    if main is not None:
        git_sandbox.sh(work, "switch", "-q", "main")
        write_json(manifest, {"name": "kit", "version": main})
        git_sandbox.sh(work, "commit", "-q", "-am", "released")
        git_sandbox.sh(work, "push", "-q", "origin", "main")
        git_sandbox.sh(work, "switch", "-q", "develop")
    for tag in tags:
        git_sandbox.sh(work, "tag", tag, "origin/main")
        git_sandbox.sh(work, "push", "-q", "origin", tag)
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


def _open_pulls(hub: FakeHub, *heads: tuple[int, str]) -> FakeHub:
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": n, "head": {"ref": ref}} for n, ref in heads]
    return hub


@pytest.mark.parametrize("head", ["release/v9.9.9", "develop"])
def test_issue_2441_production_waits_while_another_release_is_open(tmp_path: Path, head: str) -> None:
    """#2441: 本番ブランチ宛ての別のリリースの PR が開いている間は、ブランチも PR も作らずに止まる。"""
    work = _origin_with_change(tmp_path, test_only=False)
    hub = _open_pulls(_hub(), (41, head))
    with pytest.raises(assemble.ReleaseOpen, match="#41"):
        assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert not [r for r in hub.requests if r[0] == "POST"]
    assert git_sandbox.sh(work, "ls-remote", "--heads", "origin", "release/v1.2.4") == ""


def test_issue_2441_builds_once_the_other_release_is_closed(tmp_path: Path) -> None:
    """#2441: 本番ブランチ宛ての PR がリリースでなければ（閉じた後の観測）組む。開発版のチャネルは待たない。"""
    work = _origin_with_change(tmp_path, test_only=False)
    built = assemble.assemble(work, _open_pulls(_hub(), (42, "feat/y")), assemble.Target("production", "patch", "develop", "main"))
    assert built.branch == "release/v1.2.4"
    other = _origin_with_change(tmp_path / "dev", test_only=False)
    dev = assemble.assemble(other, _open_pulls(_hub(), (41, "release/v9.9.9")), assemble.Target("dev", "patch", "develop", "main"))
    assert dev.version == "1.2.4-dev.1"


def test_issue_2441_cli_reports_refused() -> None:
    """#2441: 待ちは止まった理由の種類 refused で返す（呼び手は閉じた後に打ち直す）。"""
    exc = assemble.ReleaseOpen("main 宛てのリリースの PR #41 が開いている")
    assert release_cli._stopped_of(exc)["cause"]["kind"] == "refused"


@pytest.mark.parametrize("synced", [False, True])
def test_issue_3172_no_empty_release_after_release_when_only_a_closed_pull_remains(tmp_path: Path, synced: bool) -> None:
    """#3172: 出した後にマージされずに閉じた PR しか無ければ（ベースブランチに新しい変化が無い）、何度打っても空のリリースを組まない。

    組み立ては依頼の PR ではなく範囲（本番ブランチ...ベースブランチ）から読むため、閉じた PR は入らない。戻す PR を
    マージする前（`synced` が偽）と後のどちらでも同じ。
    """
    work = _origin_with_change(tmp_path, test_only=False)
    git_sandbox.sh(work, "switch", "-q", "-c", "release/v1.2.4")
    write_json(work / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.4"})
    git_sandbox.sh(work, "commit", "-q", "-am", "Release: kit v1.2.4")
    git_sandbox.sh(work, "switch", "-q", "main")
    git_sandbox.sh(work, "merge", "-q", "--no-ff", "release/v1.2.4", "-m", "Merge pull request #9 from o/release/v1.2.4")
    git_sandbox.sh(work, "push", "-q", "origin", "main")
    git_sandbox.sh(work, "tag", "kit--v1.2.4")
    git_sandbox.sh(work, "push", "-q", "origin", "kit--v1.2.4")
    git_sandbox.sh(work, "switch", "-q", "develop")
    if synced:
        git_sandbox.sh(work, "merge", "-q", "--no-ff", "main", "-m", "Merge pull request #10 from o/main")
        git_sandbox.sh(work, "push", "-q", "origin", "develop")
    for _ in range(2):
        hub = _hub()
        with pytest.raises(assemble.NothingToRelease):
            assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
        assert hub.requests == []


# --- #3572: 既にあるリリースブランチを進め、開いた本番の PR を組み直す ---------------------------------------


def _remote_tip(work: Path, branch: str) -> str:
    return git_sandbox.sh(work, "ls-remote", "origin", f"refs/heads/{branch}").split("\t")[0]


def _push_at(work: Path, branch: str, ref: str) -> None:
    git_sandbox.sh(work, "push", "-q", "origin", f"{ref}:refs/heads/{branch}")


def _commit_on(work: Path, branch: str, rel: str, text: str) -> None:
    """origin の `branch` の先端に 1 コミット足して push する（取り出しは develop へ戻す）。"""
    git_sandbox.sh(work, "fetch", "-q", "origin", branch)
    git_sandbox.sh(work, "switch", "-q", "--detach", f"origin/{branch}")
    (work / rel).parent.mkdir(parents=True, exist_ok=True)
    (work / rel).write_text(text, encoding="utf-8")
    git_sandbox.sh(work, "add", "-A")
    git_sandbox.sh(work, "commit", "-q", "-m", f"fix {rel}")
    git_sandbox.sh(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    git_sandbox.sh(work, "switch", "-q", "--force", "develop")


def _created_pulls(hub: FakeHub) -> list[dict]:
    return [p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/pulls")]


def test_a_prepared_branch_is_advanced_by_fast_forward_and_not_rebuilt(tmp_path: Path) -> None:
    """受け入れ条件 10・I6: 前の版の直後に本番ブランチから用意したブランチを、作り直さずに fast-forward で進めて PR を作る。"""
    work = _origin_with_change(tmp_path, test_only=False)
    _push_at(work, "release/v1.2.4", "origin/main")
    prepared = _remote_tip(work, "release/v1.2.4")
    built = assemble.assemble(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert (built.branch, built.reassembled, built.renamed_from) == ("release/v1.2.4", False, None)
    assert git_sandbox.sh(work, "merge-base", "--is-ancestor", prepared, built.facts.head) == ""


def test_a_foreign_branch_without_an_open_pr_stops_the_base_assembly(tmp_path: Path) -> None:
    """I6: 先端が組む起点の祖先でなく、開いた本番の PR も無いブランチは進めずに止まる（push しない）。"""
    work = _origin_with_change(tmp_path, test_only=False)
    _push_at(work, "release/v1.2.4", "origin/main")
    _commit_on(work, "release/v1.2.4", "stray.txt", "x\n")
    before = _remote_tip(work, "release/v1.2.4")
    with pytest.raises(assemble.AssembleError, match="祖先でなく"):
        assemble.assemble(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    assert _remote_tip(work, "release/v1.2.4") == before


@pytest.mark.parametrize("source", ["base", "release"])
def test_issue_3572_rebuilds_the_open_production_pr_on_the_new_head(tmp_path: Path, source: str) -> None:
    """受け入れ条件 9・I7: 開いた本番の PR のブランチへ直しが入れば、同じブランチと PR を組み直し（新しいブランチと PR を作らず、
    強制 push をせず）、承認資料の対象を新しい先端 C にする。A への承認は stale になる。"""
    work = _origin_with_change(tmp_path, test_only=False)
    if source == "release":
        _push_at(work, "release/v1.2.4", "origin/develop")
    target = assemble.Target("production", "patch", "develop", "main", source=source)
    first = assemble.assemble(work, _hub(), target)
    _commit_on(work, "develop" if source == "base" else "release/v1.2.4", "tools/kit/fix.py", "fixed = 1\n")
    hub = _open_pulls(_hub(), (9, "release/v1.2.4"))
    again = assemble.assemble(work, hub, target)
    assert (again.branch, again.version, again.pr, again.reassembled) == ("release/v1.2.4", "1.2.4", 9, True)
    assert _created_pulls(hub) == [] and again.facts.head != first.facts.head
    assert git_sandbox.sh(work, "merge-base", "--is-ancestor", first.facts.head, again.facts.head) == ""
    assert "fixed = 1" in git_sandbox.sh(work, "show", f"{again.facts.head}:tools/kit/fix.py")
    assert json.loads(git_sandbox.sh(work, "show", f"{again.facts.head}:tools/kit/.claude-plugin/plugin.json"))["version"] == "1.2.4"
    body = next(p for m, path, p in hub.requests if (m, path) == ("PATCH", "/repos/o/r/issues/9"))["body"]
    assert f"`{again.facts.head}`" in body and f"`{first.facts.head}`" not in body
    names = git_sandbox.sh(work, "ls-remote", "--heads", "origin", "release/*").splitlines()
    assert len(names) == 1
    stale = approval_decide.judge([approval_decide.Trail(0, "octocat", "approved", first.facts.head)], again.facts.head)
    assert stale.state == approval_decide.STALE


def test_issue_3587_a_deferred_branch_is_reassembled_with_a_new_pr(tmp_path: Path) -> None:
    """#3587: defer で本番の PR を閉じたリリースブランチ（版上げのコミットが develop の祖先でない）も、後の直しを取り込みの
    マージで入れて進め、新しい本番の PR を作る（決定 3 の「止まった → 用意済み」）。"""
    work = _origin_with_change(tmp_path, test_only=False)
    first = assemble.assemble(work, _hub(), assemble.Target("production", "patch", "develop", "main"))
    _commit_on(work, "develop", "tools/kit/fix.py", "fixed = 1\n")
    hub = _hub()
    hub.heads["release/v1.2.4"] = [fake_pull(9, first.facts.head, ref="release/v1.2.4")._replace(state="closed")]
    again = assemble.assemble(work, hub, assemble.Target("production", "patch", "develop", "main"))
    assert (again.branch, again.pr, again.reassembled) == ("release/v1.2.4", 9, False) and len(_created_pulls(hub)) == 1
    assert git_sandbox.sh(work, "merge-base", "--is-ancestor", first.facts.head, again.facts.head) == ""
    assert "fixed = 1" in git_sandbox.sh(work, "show", f"{again.facts.head}:tools/kit/fix.py")


def test_issue_3587_a_code_conflict_in_the_reassembly_stops_without_dropping_the_release_fix(tmp_path: Path) -> None:
    """#3587: 組み直しの取り込みでコードが衝突すれば、起点の側で上書きせずに止まり、push しない（リリース側の直しを捨てない）。"""
    work = _origin_with_change(tmp_path, test_only=False)
    target = assemble.Target("production", "patch", "develop", "main")
    assemble.assemble(work, _hub(), target)
    _commit_on(work, "release/v1.2.4", "tools/kit/a.py", "x = 2  # リリース側の直し\n")
    _commit_on(work, "develop", "tools/kit/a.py", "x = 3\n")
    before = _remote_tip(work, "release/v1.2.4")
    with pytest.raises(git_mod.GitError, match="tools/kit/a.py"):
        assemble.assemble(work, _open_pulls(_hub(), (9, "release/v1.2.4")), target)
    assert _remote_tip(work, "release/v1.2.4") == before


@dataclass
class _RenamingHub(FakeHub):
    """改名の REST を、手元の bare の origin のブランチの改名で受ける偽物。"""

    origin: Path = Path()

    def rest(self, method, path, payload=None, params=None):
        if method == "POST" and path.endswith("/rename"):
            old = path.removeprefix("/repos/o/r/branches/").removesuffix("/rename")
            git_sandbox.sh(self.origin, "branch", "-m", old, payload["new_name"])
        return super().rest(method, path, payload, params)


def test_issue_3572_a_larger_bump_renames_the_prepared_branch_before_the_pr(tmp_path: Path) -> None:
    """決定 5: 計画した版の上げ幅が用意した PATCH より大きく、本番の PR が無ければ、PR を作る前に改名する。"""
    work = _origin_with_change(tmp_path, test_only=False)
    _push_at(work, "release/v1.2.4", "origin/main")
    hub = _RenamingHub(node_data=_hub().node_data, responses=dict(_hub().responses), origin=tmp_path / "origin.git")
    built = assemble.assemble(work, hub, assemble.Target("production", "minor", "develop", "main"))
    assert (built.branch, built.renamed_from) == ("release/v1.3.0", "release/v1.2.4")
    assert _remote_tip(work, "release/v1.2.4") == "" and _created_pulls(hub)[0]["head"] == "release/v1.3.0"


def test_issue_3572_a_larger_bump_with_an_open_pr_stops_without_renaming(tmp_path: Path) -> None:
    """決定 5: 本番の PR を作った後に上げ幅が変われば、改名せずに止まる（改名すると head の PR が閉じる）。"""
    work = _origin_with_change(tmp_path, test_only=False)
    _push_at(work, "release/v1.2.4", "origin/main")
    hub = _open_pulls(_hub(), (9, "release/v1.2.4"))
    with pytest.raises(assemble.AssembleError, match="defer"):
        assemble.assemble(work, hub, assemble.Target("production", "minor", "develop", "main"))
    assert not [r for r in hub.requests if r[0] == "POST"]
