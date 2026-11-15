"""CD の版上げ（本物の git と、偽の GitHub。#3572 の決定 7・I7・I8・I11）。"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import git_sandbox
import pytest

from ndf.adapters import ci
from ndf.adapters import git as git_mod
from ndf.adapters.github import MergeOutcome
from ndf.domains.release import bump, bump_decide, land, version_decide, version_store
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod

from ._fakes import FakeHub, clone, init_repo, release_lander, write_json
from ._fakes import pull as fake_pull

REVIEW_RULE = {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}
BUMP_PR = 20
# CD のバージョン上げを使うリポジトリの宣言（ブランチ戦略 github-flow と、バージョンの置き場。#3572 の決定 16・22）
TRUNK_DECL = {"base_branch": "main", "production_branch": "main", "branch_flow": {"promotion": "direct"}}
PLUGIN_STORE = {"version": 1, "version_store": {"kind": "plugin"}}


def _declare(root: Path, store: dict | None = None) -> None:
    write_json(root / ".ndf" / "worktree.json", TRUNK_DECL)
    write_json(root / ".ndf" / "release.json", store if store is not None else PLUGIN_STORE)


@dataclass
class BumpHub(FakeHub):
    """迂回のマージを数え、作った PR を `pulls` に足す偽物。"""

    admin_merged: list[int] = field(default_factory=list)
    created: list[dict] = field(default_factory=list)
    # 版上げの PR の先端を origin の実物から読む clone（無ければ偽の先端のまま）
    work: Path | None = None

    def pull(self, number: int) -> Any:
        got = super().pull(number)
        if self.work is None:
            return got
        found = git_sandbox.sh(self.work, "ls-remote", "origin", f"refs/heads/{got.head_ref}").split()
        return got._replace(head_sha=found[0]) if found else got

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


def _origin(tmp_path: Path, test_only: bool = False, tagged: bool = True, other: bool = False) -> Path:
    """kit 1.2.3（タグあり）の main へ、スプリントの PR #5 をマージした origin と、main を取り出した clone。

    `other` なら、版はあるがタグの無い 2 つ目のプラグイン other 0.1.0 も登録する。"""
    seed = init_repo(tmp_path / "seed")
    listed = [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]
    if other:
        listed.append({"name": "other", "source": "./tools/other", "description": "Other"})
        write_json(seed / "tools" / "other" / ".claude-plugin" / "plugin.json", {"name": "other", "version": "0.1.0"})
    write_json(seed / ".claude-plugin" / "marketplace.json", {"plugins": listed})
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (seed / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 前\n", encoding="utf-8")
    _declare(seed)
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
    hub.work = work
    return bump.Bumper(work, hub, dataclasses.replace(lander, admin=True), "main", "2026-11-15")


def _open(hub: BumpHub, number: int, ref: str, repo: str = "o/r") -> None:
    hub.responses[("GET", "/repos/o/r/pulls")] = [{"number": number, "head": {"ref": ref, "sha": "a" * 40, "repo": {"full_name": repo}}}]
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
    decl = decl_mod.load(work)
    assert not bump_decide.only_bump(changed, bump.allowed_paths(version_store.of(work, decl, decl_mod.flow_of(work)), decl))
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


def _push_old_bump(work: Path, extra: bool = False) -> None:
    """main の先端から版だけを 1.2.4 へ上げた release/v1.2.4 を origin へ置く（`extra` なら利用者の直しも積む）。"""
    git_sandbox.sh(work, "switch", "-q", "-c", "release/v1.2.4", "origin/main")
    write_json(work / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.4"})
    if extra:
        (work / "tools" / "kit" / "fix.py").write_text("z = 1\n", encoding="utf-8")
    git_sandbox.sh(work, "add", "-A")
    git_sandbox.sh(work, "commit", "-q", "-m", "Release: kit v1.2.4")
    git_sandbox.sh(work, "push", "-q", "origin", "HEAD:refs/heads/release/v1.2.4")
    git_sandbox.sh(work, "switch", "-q", "--detach", "origin/main")


def test_a_larger_level_replaces_the_open_bump_pr(tmp_path: Path) -> None:
    """受け入れ条件 10（競合）: PATCH の版上げの PR が開いたまま MINOR が入れば、古い PR を閉じてブランチを消し、新しい版で作る。"""
    work = _origin(tmp_path)
    _push_old_bump(work)
    hub = _hub()
    _open(hub, 19, "release/v1.2.4")
    made = _bumper(work, hub).bump()
    assert made.version == "1.3.0" and made.state == "merged"
    writes = [(m, p) for m, p, _b in hub.requests if m in ("PATCH", "DELETE")]
    assert writes == [("PATCH", "/repos/o/r/pulls/19"), ("DELETE", "/repos/o/r/git/refs/heads/release/v1.2.4")]
    comment = next(b for m, p, b in hub.requests if (m, p) == ("POST", "/repos/o/r/issues/19/comments"))
    assert "版 1.2.4 を 1.3.0 へ置き換えた" in comment["body"]


@pytest.mark.parametrize("fork", [False, True])
def test_replacing_a_bump_pr_with_user_changes_or_from_a_fork_stops_without_closing(tmp_path: Path, fork: bool) -> None:
    """置き換えで閉じて消すのは、同じリポジトリの head で差分が版上げだけの版上げの PR だけ。利用者の直しやフォークなら止まる。"""
    work = _origin(tmp_path)
    _push_old_bump(work, extra=not fork)
    hub = _hub()
    _open(hub, 19, "release/v1.2.4", repo="someone/r" if fork else "o/r")
    with pytest.raises(bump.BumpError, match="#19"):
        _bumper(work, hub).bump()
    assert [(m, p) for m, p, _b in hub.requests if m in ("PATCH", "DELETE")] == [] and hub.created == []


def test_only_test_changes_do_not_bump(tmp_path: Path) -> None:
    """I11: 範囲にリリース対象の変化が無ければ not_applicable で、版上げの PR を作らない。"""
    work = _origin(tmp_path, test_only=True)
    hub = _hub()
    assert _bumper(work, hub).bump().state == "not_applicable"
    assert hub.created == [] and hub.admin_merged == []


def test_an_untagged_tip_without_a_bump_merge_does_not_go_to_publish(tmp_path: Path) -> None:
    """版上げの履歴が無く、今の版にタグが無い main（新しく導入したリポジトリ）は published に倒れず、版上げの PR を作る。"""
    work = _origin(tmp_path, tagged=False)
    hub = _hub()
    made = _bumper(work, hub).bump()
    assert made.state != "published" and len(hub.created) == 1


def test_an_untagged_plugin_that_the_last_bump_did_not_change_does_not_go_to_publish(tmp_path: Path) -> None:
    """タグを待つのは直前の版上げで定義を変えたプラグインだけ。タグの無いほかのプラグインで PUBLISH に倒れない。"""
    work = _origin(tmp_path, other=True)
    made = _bumper(work, _hub()).bump()
    assert (made.state, made.version) == ("merged", "1.3.0")


def _merge_bump_and_add_a_sprint(work: Path) -> str:
    """版上げの PR（release/v1.3.0）を main へマージし、その後に次のスプリントを main へ入れる。マージコミットを返す。"""
    git_sandbox.sh(work, "switch", "-q", "--detach", "origin/main")
    git_sandbox.sh(work, "merge", "-q", "--no-ff", "origin/release/v1.3.0", "-m", "Merge pull request #20 from o/release/v1.3.0")
    merged = git_sandbox.sh(work, "rev-parse", "HEAD").strip()
    (work / "tools" / "kit" / "b.py").write_text("y = 1\n", encoding="utf-8")
    git_sandbox.sh(work, "add", "-A")
    git_sandbox.sh(work, "commit", "-q", "-m", "next")
    git_sandbox.sh(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    return merged


def test_publish_takes_the_bump_merge_even_after_the_next_sprint_lands(tmp_path: Path) -> None:
    """版上げの PR のマージの後に次のスプリントが main へ入っても、タグを打つコミットは版上げの PR のマージコミット。"""
    work = _origin(tmp_path)
    _bumper(work, _hub()).bump()
    merged = _merge_bump_and_add_a_sprint(work)
    made = _bumper(work, _hub()).bump()
    assert (made.state, made.version, made.revision) == ("published", "1.3.0", merged)


def test_a_bump_pr_that_changes_more_than_the_version_in_an_allowed_file_is_refused(tmp_path: Path) -> None:
    """I7: 許したパス（plugin.json）の中でも、版の外の変更（hook の追加）があれば承認なしでマージしない。"""
    work = _origin(tmp_path)
    tip = git_sandbox.sh(work, "rev-parse", "origin/main").strip()
    git_sandbox.sh(work, "switch", "-q", "-c", "release/v1.3.0", tip)
    write_json(work / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.3.0", "hooks": "x"})
    git_sandbox.sh(work, "commit", "-q", "-am", "Release: kit v1.3.0")
    git_sandbox.sh(work, "push", "-q", "origin", "HEAD:refs/heads/release/v1.3.0")
    hub = _hub()
    hub.pulls[BUMP_PR] = fake_pull(number=BUMP_PR, ref="release/v1.3.0")
    got = _bumper(work, hub)._merge(BUMP_PR, "1.3.0", "minor")
    assert got.state == "stopped" and got.landed is not None and got.landed.kind == "refused"
    assert "plugin.json（版の外の変更）" in got.landed.detail and hub.admin_merged == []


def test_updating_an_open_bump_pr_with_changes_outside_the_version_stops_before_taking_its_tree(tmp_path: Path) -> None:
    """既存の版上げの PR を進める前に、信頼する main の先端の宣言で head の差分を検査し、版上げの外があればその木を取り出さずに止まる。"""
    work = _origin(tmp_path)
    tip = git_sandbox.sh(work, "rev-parse", "origin/main").strip()
    git_sandbox.sh(work, "switch", "-q", "-c", "release/v1.3.0", tip)
    write_json(work / ".ndf" / "release.json", {"steps": []})
    git_sandbox.sh(work, "add", "-A")
    git_sandbox.sh(work, "commit", "-q", "-m", "Release: kit v1.3.0")
    git_sandbox.sh(work, "push", "-q", "origin", "HEAD:refs/heads/release/v1.3.0")
    hub = _hub()
    _open(hub, BUMP_PR, "release/v1.3.0")
    with pytest.raises(bump.BumpError, match=r"\.ndf/release\.json"):
        _bumper(work, hub).bump()
    assert git_sandbox.sh(work, "rev-parse", "HEAD").strip() == tip and hub.admin_merged == []


def test_a_bump_pr_whose_head_moved_after_the_fetch_is_refused(tmp_path: Path) -> None:
    """差分の検査はマージへ渡す先端そのもので行う。取り込んだ先端と PR の先端が違えばマージしない。"""
    work = _origin(tmp_path)
    hub = _hub()
    _bumper(work, hub).bump()
    hub.pulls[BUMP_PR] = fake_pull(number=BUMP_PR, ref="release/v1.3.0")
    bumper = _bumper(work, hub)
    hub.work = None
    got = bumper._merge(BUMP_PR, "1.3.0", "minor")
    assert got.state == "stopped" and got.landed is not None and "先端が" in got.landed.detail


def test_version_only_checks_take_only_the_version_strings() -> None:
    pairs = [("1.2.3", "1.3.0")]
    diff = "diff --git a/v b/v\n--- a/v\n+++ b/v\n@@ -1 +1 @@\n-version = 1.2.3\n+version = 1.3.0\n"
    assert bump_decide.diff_lines(diff) == (["version = 1.2.3"], ["version = 1.3.0"])
    assert bump_decide.version_lines_only(["version = 1.2.3"], ["version = 1.3.0"], pairs)
    assert not bump_decide.version_lines_only(["version = 1.2.3"], ["version = 1.3.0", "hook = x"], pairs)
    assert bump_decide.json_version_only({"v": "1.2.3", "d": "K (v1.2.3)"}, {"v": "1.3.0", "d": "K (v1.3.0)"}, pairs)
    assert not bump_decide.json_version_only({"v": "1.2.3"}, {"v": "1.3.0", "hooks": "x"}, pairs)
    same = [{"d": "Kit (v1.2.3)"}, {"d": "Other (v1.2.3)"}]
    assert bump_decide.json_version_only(same, [{"d": "Kit (v1.3.0)"}, {"d": "Other (v1.2.3)"}], pairs)
    assert not bump_decide.json_version_only(same, [{"d": "Kit (v1.3.0)"}, {"d": "Other"}], pairs)
    generated = frozenset({"docs/metrics/x/"})
    assert bump_decide.covered("docs/metrics/x/a.md", generated) and not bump_decide.covered("docs/metrics/xy.md", generated)
    assert bump_decide.only_bump(["docs/metrics/x/a.json", "a.py"], generated) == ["a.py"]
    swap = [("1.2.3", "1.3.0"), ("1.3.0", "1.4.0")]
    assert bump_decide.version_lines_only(["a 1.2.3 b 1.3.0"], ["a 1.3.0 b 1.4.0"], swap)


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


def test_the_bump_does_nothing_outside_the_github_flow(tmp_path: Path) -> None:
    """決定 16: github-flow でない行ではリリースブランチがバージョンを上げるため、何も読まずに not_applicable。"""
    work = _origin(tmp_path)
    write_json(work / ".ndf" / "worktree.json", {"branch_flow": {"promotion": "whole"}})
    hub = _hub()
    assert _bumper(work, hub).bump().state == "not_applicable" and hub.created == []


def test_the_github_flow_without_a_version_store_stops(tmp_path: Path) -> None:
    """I18: github-flow で置き場を書いていなければ、既定の置き場へ倒さずに止まる。"""
    work = _origin(tmp_path)
    write_json(work / ".ndf" / "release.json", {"version": 1})
    with pytest.raises(version_store.VersionStoreError, match="version_store"):
        _bumper(work, _hub()).bump()


def _json_origin(tmp_path: Path) -> Path:
    """marketplace.json を持たず、バージョンを package.json に持つ web 1.2.3（タグあり）の main へ PR #5 をマージした origin の clone。"""
    seed = init_repo(tmp_path / "seed")
    write_json(seed / "package.json", {"name": "web", "version": "1.2.3", "private": True})
    _declare(seed, {"version": 1, "version_store": {"kind": "json", "path": "package.json", "key": "version", "name": "web"}})
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "init")
    git_sandbox.sh(seed, "branch", "-M", "main")
    git_sandbox.sh(seed, "tag", "web--v1.2.3")
    git_sandbox.sh(seed, "switch", "-q", "-c", "sprint/x")
    (seed / "src").mkdir()
    (seed / "src" / "a.js").write_text("export const a = 1\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "change")
    git_sandbox.sh(seed, "switch", "-q", "main")
    git_sandbox.sh(seed, "merge", "-q", "--no-ff", "sprint/x", "-m", "Merge pull request #5 from o/sprint/x")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    return clone(tmp_path, origin, "work", branch="main")


def test_a_json_store_bumps_only_the_package_version_without_a_marketplace(tmp_path: Path) -> None:
    """受け入れ条件 21: marketplace.json の無いリポジトリで、置き場 json の package.json だけを上げ、プラグインの登録で止まらない。"""
    work = _json_origin(tmp_path)
    hub = _hub()
    made = _bumper(work, hub).bump()
    assert (made.state, made.version, made.pr) == ("merged", "1.3.0", BUMP_PR)
    changed = git_sandbox.sh(work, "diff", "--name-only", "origin/main", "origin/release/v1.3.0").split()
    assert changed == ["package.json"]
    after = json.loads(git_sandbox.sh(work, "show", "origin/release/v1.3.0:package.json"))
    assert after == {"name": "web", "version": "1.3.0", "private": True}


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


# --- 現状固定（I-018）: release-steps bump の結果 JSON（止まった版上げと、進んだ版上げ）---


def _cli_bump(monkeypatch, capsys, tmp_path: Path, made: bump.Bumped) -> tuple[int, dict, FakeHub]:
    _declare(tmp_path)
    hub = FakeHub()
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)

    class _Bumper:
        def __init__(self, *args: object) -> None:
            pass

        def bump(self) -> bump.Bumped:
            return made

    monkeypatch.setattr(bump, "Bumper", _Bumper)
    code = release_cli.main(["bump", "--root", str(tmp_path), "--production", "main", "--budget", "0"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1]), hub


def _posted(hub: FakeHub) -> list[tuple[str, Any]]:
    return [(path, payload) for method, path, payload in hub.requests if method == "POST"]


def test_characterize_cli_bump_stopped_with_a_landed_failure_comments_on_the_bump_pr(monkeypatch, capsys, tmp_path: Path) -> None:
    landed = land.Landed(False, "test_failed", "テストが落ちた", failed=(ci.Check("unit", "done", "failure", 1),))
    made = bump.Bumped("stopped", version="1.3.0", level="minor", pr=12, landed=landed)
    code, out, hub = _cli_bump(monkeypatch, capsys, tmp_path, made)
    assert code != 0 and out["status"] == "stopped"
    assert (out["cause"]["kind"], out["cause"]["detail"]) == ("test_failed", "テストが落ちた")
    assert out["summary"] == "テストが落ちた"
    assert out["items"] == [
        {"kind": "check", "name": "unit", "outcome": "failure"},
        {"kind": "value", "name": "state", "value": "stopped"},
        {"kind": "value", "name": "level", "value": "minor"},
        {"kind": "value", "name": "pr_url", "value": "https://github.com/o/r/pull/12"},
    ]
    assert out["outputs"] == {"version": "1.3.0", "pr": 12}
    assert _posted(hub) == [("/repos/o/r/issues/12/comments", {"body": "版上げが止まった（test_failed）: テストが落ちた"})]


@pytest.mark.parametrize(
    ("landed", "detail"),
    [(None, "版上げが止まった"), ("empty-kind", "読めない")],
)
def test_characterize_cli_bump_stopped_without_a_kind_is_unknown(monkeypatch, capsys, tmp_path: Path, landed, detail: str) -> None:
    got = land.Landed(False, None, detail) if landed else None
    code, out, hub = _cli_bump(monkeypatch, capsys, tmp_path, bump.Bumped("stopped", landed=got))
    assert code != 0 and out["status"] == "stopped"
    assert (out["cause"]["kind"], out["cause"]["detail"]) == ("unknown", detail)
    assert out["items"] == [{"kind": "value", "name": "state", "value": "stopped"}]
    assert "outputs" not in out
    assert _posted(hub) == []


def test_characterize_cli_bump_stopped_with_a_pr_but_no_landed_still_comments(monkeypatch, capsys, tmp_path: Path) -> None:
    code, out, hub = _cli_bump(monkeypatch, capsys, tmp_path, bump.Bumped("stopped", pr=5))
    assert code != 0 and out["cause"]["kind"] == "unknown"
    assert out["outputs"] == {"pr": 5}
    assert out["items"] == [
        {"kind": "value", "name": "state", "value": "stopped"},
        {"kind": "value", "name": "pr_url", "value": "https://github.com/o/r/pull/5"},
    ]
    assert _posted(hub) == [("/repos/o/r/issues/5/comments", {"body": "版上げが止まった（unknown）: 版上げが止まった"})]


def test_characterize_cli_bump_ok_states(monkeypatch, capsys, tmp_path: Path) -> None:
    landed = land.Landed(True, None, "マージした", production_sha="f" * 40)
    made = bump.Bumped("merged", version="1.3.0", level="minor", pr=12, landed=landed)
    code, out, hub = _cli_bump(monkeypatch, capsys, tmp_path, made)
    assert code == 0 and out["status"] == "ok"
    assert out["summary"] == "版上げ: merged（版 1.3.0・版上げの PR #12）"
    assert out["items"] == [
        {"kind": "value", "name": "state", "value": "merged"},
        {"kind": "value", "name": "level", "value": "minor"},
        {"kind": "value", "name": "production_sha", "value": "f" * 40},
        {"kind": "value", "name": "store", "value": "plugin"},
    ]
    assert out["outputs"] == {"version": "1.3.0", "pr": 12}
    assert _posted(hub) == []
    code, out, _unused = _cli_bump(monkeypatch, capsys, tmp_path, bump.Bumped("not_applicable"))
    assert code == 0 and out["summary"] == "版上げ: not_applicable（版 無し・版上げの PR 無し）"
    assert out["items"] == [
        {"kind": "value", "name": "state", "value": "not_applicable"},
        {"kind": "value", "name": "store", "value": "plugin"},
    ]
    assert "outputs" not in out


def test_cli_bump_outside_the_github_flow_is_not_applicable_without_reading_the_store(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(release_cli, "_hub", lambda args: FakeHub())
    assert release_cli.main(["bump", "--root", str(tmp_path), "--production", "main", "--budget", "0"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["items"] == [{"kind": "value", "name": "state", "value": "not_applicable"}]
