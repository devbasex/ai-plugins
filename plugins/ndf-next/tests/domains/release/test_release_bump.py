"""CD の版上げ（本物の git と、偽の GitHub。#3572 の決定 7・I7・I8・I11）。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import git_sandbox
import pytest

from ndf.adapters import git as git_mod
from ndf.adapters.github import MergeOutcome
from ndf.domains.release import bump, bump_decide, version_decide
from ndf.domains.release import declaration as decl_mod

from ._fakes import FakeHub, clone, init_repo, release_lander, write_json
from ._fakes import pull as fake_pull

REVIEW_RULE = {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}
BUMP_PR = 20


@dataclass
class BumpHub(FakeHub):
    """迂回のマージを数え、作った PR を `pulls` に足す偽物。"""

    admin_merged: list[int] = field(default_factory=list)
    created: list[dict] = field(default_factory=list)

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if (method, path) == ("POST", "/repos/o/r/pulls"):
            number = BUMP_PR + len(self.created)
            self.created.append(payload)
            self.pulls[number] = fake_pull(number=number, ref=payload["head"], base=payload["base"])
            self.requests.append((method, path, payload))
            return {"number": number, "html_url": f"https://example.invalid/pull/{number}"}
        return super().rest(method, path, payload, params)

    def merge_as_admin(self, number: int, expect_head: str, method: str | None = None) -> MergeOutcome:
        self.admin_merged.append(number)
        return MergeOutcome(True, None, f"#{number} を迂回でマージした", expect_head)


def _origin(tmp_path: Path, test_only: bool = False, tagged: bool = True) -> Path:
    """kit 1.2.3（タグあり）の main へ、スプリントの PR #5 をマージした origin と、main を取り出した clone。"""
    seed = init_repo(tmp_path / "seed")
    write_json(
        seed / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]}
    )
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (seed / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 前\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "init")
    git_sandbox.sh(seed, "branch", "-M", "main")
    if tagged:
        git_sandbox.sh(seed, "tag", "kit--v1.2.3")
    git_sandbox.sh(seed, "switch", "-q", "-c", "sprint/x")
    changed = seed / "tools" / "kit" / ("tests/test_a.py" if test_only else "a.py")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("x = 1\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "change")
    git_sandbox.sh(seed, "switch", "-q", "main")
    git_sandbox.sh(seed, "merge", "-q", "--no-ff", "sprint/x", "-m", "Merge pull request #5 from o/sprint/x")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    return clone(tmp_path, origin, "work", branch="main")


def _hub(labels: tuple[str, ...] = ("release:minor",)) -> BumpHub:
    hub = BumpHub(node_data={5: {"number": 5, "title": "足した", "commits": {"nodes": []}}})
    hub.responses[("GET", "/repos/o/r/issues/5/labels")] = [{"name": x} for x in labels]
    return hub


def _bumper(work: Path, hub: BumpHub, failed: bool = False) -> bump.Bumper:
    lander = release_lander(hub, failed, [])
    rules = hub.responses[("GET", "/repos/o/r/rules/branches/main")]
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [*rules, REVIEW_RULE]
    return bump.Bumper(work, hub, dataclasses.replace(lander, admin=True), "main", "2026-11-15")


def _open(hub: BumpHub, number: int, ref: str) -> None:
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": number, "head": {"ref": ref, "sha": "a" * 40}}]
    hub.pulls[number] = fake_pull(number=number, ref=ref)


def test_a_minor_pr_on_x_y_z_makes_and_merges_one_bump_pr_with_only_the_version_files(tmp_path: Path) -> None:
    """受け入れ条件 10: main 1.2.3 に MINOR の PR が入ると、release/v1.3.0 の版上げの PR を作って迂回でマージする。差分は版と CHANGELOG だけ。"""
    work = _origin(tmp_path)
    hub = _hub()
    tip = git_sandbox.sh(work, "rev-parse", "origin/main").strip()
    made = _bumper(work, hub).bump()
    assert (made.state, made.version, made.level, made.pr) == ("merged", "1.3.0", "minor", BUMP_PR)
    assert hub.admin_merged == [BUMP_PR] and [(c["head"], c["base"]) for c in hub.created] == [("release/v1.3.0", "main")]
    changed = git_sandbox.sh(work, "diff", "--name-only", "origin/main", "origin/release/v1.3.0").split()
    assert "tools/kit/.claude-plugin/plugin.json" in changed and "CHANGELOG.md" in changed
    assert set(changed) <= {"CHANGELOG.md", ".claude-plugin/marketplace.json", "tools/kit/.claude-plugin/plugin.json"}
    assert not bump_decide.only_bump(changed, bump.allowed_paths(work, decl_mod.load(work)))
    assert "tools/kit/a.py" not in changed
    message = git_sandbox.sh(work, "log", "-1", "--format=%B", "origin/release/v1.3.0")
    assert bump_decide.origin_of(message) == tip
    log = git_sandbox.sh(work, "show", "origin/release/v1.3.0:CHANGELOG.md")
    assert "## [kit 1.3.0]" in log and "- 足した（#5）" in log


def test_bumping_twice_on_the_same_tip_keeps_one_bump_pr_and_adds_no_commit(tmp_path: Path) -> None:
    """I8: 同じ main の先端で 2 回打っても版上げの PR は 1 本で、差分が同じならコミットを足さない。"""
    work = _origin(tmp_path)
    hub = _hub()
    _bumper(work, hub).bump()
    before = git_sandbox.sh(work, "rev-parse", "origin/release/v1.3.0").strip()
    _open(hub, BUMP_PR, "release/v1.3.0")
    again = _bumper(work, hub).bump()
    assert again.state == "merged" and again.pr == BUMP_PR and len(hub.created) == 1
    assert git_sandbox.sh(work, "rev-parse", "origin/release/v1.3.0").strip() == before


def test_a_larger_level_replaces_the_open_bump_pr(tmp_path: Path) -> None:
    """受け入れ条件 10（競合）: PATCH の版上げの PR が開いたまま MINOR が入れば、古い PR を閉じてブランチを消し、新しい版で作る。"""
    work = _origin(tmp_path)
    hub = _hub()
    _open(hub, 19, "release/v1.2.4")
    made = _bumper(work, hub).bump()
    assert made.version == "1.3.0" and made.state == "merged"
    writes = [(m, p) for m, p, _b in hub.requests if m in ("PATCH", "DELETE")]
    assert writes == [("PATCH", "/repos/o/r/pulls/19"), ("DELETE", "/repos/o/r/git/refs/heads/release/v1.2.4")]
    comment = next(b for m, p, b in hub.requests if (m, p) == ("POST", "/repos/o/r/issues/19/comments"))
    assert "版 1.2.4 を 1.3.0 へ置き換えた" in comment["body"]


def test_only_test_changes_do_not_bump(tmp_path: Path) -> None:
    """I11: 範囲にリリース対象の変化が無ければ not_applicable で、版上げの PR を作らない。"""
    work = _origin(tmp_path, test_only=True)
    hub = _hub()
    assert _bumper(work, hub).bump().state == "not_applicable"
    assert hub.created == [] and hub.admin_merged == []


def test_an_untagged_tip_version_goes_to_publish(tmp_path: Path) -> None:
    """版上げの PR のマージの push（先端の版にタグが無い）は版を上げずに published を返す。"""
    work = _origin(tmp_path, tagged=False)
    hub = _hub()
    made = _bumper(work, hub).bump()
    assert (made.state, made.version) == ("published", "1.2.3") and hub.created == []


def test_two_open_bump_prs_stop(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    hub = _hub()
    hub.responses[("GET", "/repos/o/r/pulls")] = [
        {"number": 18, "head": {"ref": "release/v1.2.4", "sha": "a" * 40}},
        {"number": 19, "head": {"ref": "release/v1.3.0", "sha": "b" * 40}},
    ]
    with pytest.raises(bump.BumpError, match="#18, #19"):
        _bumper(work, hub).bump()


def test_a_bump_pr_without_the_main_tip_is_not_merged(tmp_path: Path) -> None:
    """main の先端が版上げの PR の head の祖先でなければマージせずに reused（次の push の bump が進める）。"""
    work = _origin(tmp_path)
    hub = _hub()
    git_sandbox.sh(work, "push", "-q", "origin", "origin/main~1:refs/heads/release/v1.3.0")
    hub.pulls[BUMP_PR] = fake_pull(number=BUMP_PR, ref="release/v1.3.0")
    got = _bumper(work, hub)._merge(BUMP_PR, "1.3.0", "minor")
    assert got.state == "reused" and hub.admin_merged == []


def test_a_failed_check_on_the_bump_pr_stops_without_merging(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    hub = _hub()
    made = _bumper(work, hub, failed=True).bump()
    assert made.state == "stopped" and made.landed is not None and made.landed.kind == "test_failed" and hub.admin_merged == []


def test_the_bump_never_force_pushes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """強制 push を使わない（push の引数に強制の指定も + の参照も無い）。"""
    work = _origin(tmp_path)
    hub = _hub()
    pushes: list[tuple[str, ...]] = []
    run = git_mod.Git.run

    def recorded(self: git_mod.Git, *args: str) -> str:
        if args and args[0] == "push":
            pushes.append(args)
        return run(self, *args)

    monkeypatch.setattr(git_mod.Git, "run", recorded)
    _bumper(work, hub).bump()
    assert pushes and not any(a in ("--force", "-f", "--force-with-lease") or a.startswith("+") for p in pushes for a in p)


def test_the_bump_stops_on_a_flow_that_ndf_next_does_not_release(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    write_json(work / ".ndf" / "worktree.json", {"branch_flow": {"promotion": "whole"}})
    with pytest.raises(decl_mod.DeclarationError):
        _bumper(work, _hub()).bump()


@pytest.mark.parametrize(
    ("labels", "level"),
    [((), "patch"), (("release:major",), "major"), (("x", "release:minor"), "minor")],
)
def test_level_of_reads_one_label_and_defaults_to_patch(labels: tuple[str, ...], level: str) -> None:
    assert bump_decide.level_of(labels) == level


def test_level_of_refuses_two_labels_and_widest_takes_the_largest() -> None:
    with pytest.raises(bump_decide.LevelError):
        bump_decide.level_of(["release:patch", "release:minor"])
    assert bump_decide.widest(["patch", "minor"]) == "minor" and bump_decide.widest([]) == "patch"
    assert bump_decide.planned("11.0.0", "minor") == "11.1.0"
    with pytest.raises(version_decide.VersionError):
        bump_decide.planned("11.0.0-next.1", "minor")


def test_the_origin_line_round_trips_and_only_bump_names_outside_paths() -> None:
    sha = "c" * 40
    assert bump_decide.origin_of(bump_decide.message_of("kit", "1.3.0", sha)) == sha
    assert bump_decide.origin_of("Release: kit v1.3.0\n") is None and bump_decide.origin_of("起点: xyz") is None
    allowed = frozenset({"CHANGELOG.md", "a/plugin.json"})
    assert bump_decide.only_bump(["CHANGELOG.md"], allowed) == [] and bump_decide.only_bump([], allowed) == []
    assert bump_decide.only_bump(["CHANGELOG.md", "src/x.py"], allowed) == ["src/x.py"]
    assert bump_decide.late([3, 1, 2], [2]) == [1, 3] and bump_decide.late([1], [1]) == []


@pytest.mark.parametrize(
    ("tagged", "changed", "opened", "action"),
    [
        (False, True, None, bump_decide.PUBLISH),
        (True, False, None, bump_decide.NOTHING),
        (True, True, None, bump_decide.CREATE),
        (True, True, bump_decide.OpenBump(1, "1.3.0", "release/v1.3.0", "a"), bump_decide.UPDATE),
        (True, True, bump_decide.OpenBump(1, "1.2.4", "release/v1.2.4", "a"), bump_decide.REPLACE),
    ],
)
def test_action_follows_the_table(tagged: bool, changed: bool, opened: bump_decide.OpenBump | None, action: str) -> None:
    assert bump_decide.action(tagged, changed, opened, "1.3.0") == action
