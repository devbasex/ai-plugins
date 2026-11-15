"""本番の PR（main 宛てのスプリントの PR）の承認資料の組み立て（本物の git と、偽の GitHub。#3572 の決定 6）。"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf.adapters import git as git_mod
from ndf.domains.release import (
    approval_decide,
    assemble,
    bump_decide,
    changes_decide,
    checks_decide,
    gather,
    materials_decide,
    records_trail,
    trail,
    version_decide,
)
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

from ._fakes import FakeHub, clone, init_repo, write_json
from ._fakes import pull as fake_pull

PR = 9
SPRINT = "sprint/x"


def _node(number: int, title: str, conclusion: str) -> dict:
    run = {"__typename": "CheckRun", "name": "pytest", "status": "COMPLETED", "conclusion": conclusion}
    skipped = {"__typename": "CheckRun", "name": "smoke", "status": "COMPLETED", "conclusion": "SKIPPED"}
    status = {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS"}
    rollup = {"contexts": {"nodes": [run, skipped, status]}}
    return {"number": number, "title": title, "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]}}


def _origin(tmp_path: Path, test_only: bool = False, extra: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> Path:
    """main から切ったスプリントブランチへ PR #5 をマージし、その先端を PR #9 の先端（`refs/pull/9/head`）にした origin と、
    スプリントブランチを取り出した clone。`extra` は同じ PR で足すファイル、`tags` は main に置くタグ。"""
    seed = init_repo(tmp_path / "seed")
    write_json(
        seed / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./tools/kit", "description": "Kit (v1.2.3)"}]}
    )
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    (seed / "CHANGELOG.md").write_text("# 変更履歴\n\n## [kit 1.2.3] - 2026-01-01\n\n- 前\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "init")
    git_sandbox.sh(seed, "branch", "-M", "main")
    for tag in tags:
        git_sandbox.sh(seed, "tag", tag)
    git_sandbox.sh(seed, "switch", "-q", "-c", SPRINT)
    git_sandbox.sh(seed, "switch", "-q", "-c", "feat/x")
    changed = seed / "tools" / "kit" / ("tests/test_a.py" if test_only else "a.py")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("x = 1\n", encoding="utf-8")
    for rel in extra:
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        # 宣言のファイルは読み手（declaration.load）が読める版 1 の形で置く
        (seed / rel).write_text('{"version": 1}\n', encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "change")
    git_sandbox.sh(seed, "switch", "-q", SPRINT)
    git_sandbox.sh(seed, "merge", "-q", "--no-ff", "feat/x", "-m", "Merge pull request #5 from o/feat/x")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    git_sandbox.sh(origin, "update-ref", f"refs/pull/{PR}/head", f"refs/heads/{SPRINT}")
    for tag in tags:
        git_sandbox.sh(seed, "push", "-q", str(origin), tag)
    return clone(tmp_path, origin, "work", branch=SPRINT)


def _hub(labels: tuple[str, ...] = (), body: str = "スプリントの説明") -> FakeHub:
    hub = FakeHub(pulls={PR: fake_pull(number=PR, ref=SPRINT, body=body)}, node_data={5: _node(5, "直した", "SUCCESS")})
    hub.responses[("GET", f"/repos/o/r/issues/{PR}/labels")] = [{"name": x} for x in labels]
    hub.responses[("PATCH", f"/repos/o/r/issues/{PR}")] = {"html_url": f"https://example.invalid/pull/{PR}"}
    return hub


def _target() -> assemble.Target:
    return assemble.Target("main", PR)


def _written_body(hub: FakeHub) -> str:
    return next(p for m, path, p in hub.requests if (m, path) == ("PATCH", f"/repos/o/r/issues/{PR}"))["body"]


def test_assemble_writes_only_the_materials_section_and_makes_no_branch(tmp_path: Path) -> None:
    """受け入れ条件 9・決定 6: 承認資料を PR の本文の節へ書き、節の外の本文を残す。ブランチの作成・push・PR の作成をしない。"""
    work = _origin(tmp_path)
    hub = _hub()
    before = git_sandbox.sh(work, "ls-remote", "--heads", "origin")
    built = assemble.assemble(work, hub, _target())
    assert (built.pr, built.version) == (PR, "1.2.4")
    body = _written_body(hub)
    assert body.startswith("スプリントの説明") and materials_decide.MATERIALS_START in body and materials_decide.MATERIALS_END in body
    assert "- #5 直した（CI 2/2（スキップ 1））" in body and f"{approval_decide.HEAD_LINE} `{built.facts.head}`" in body
    assert built.facts.head == git_sandbox.sh(work, "rev-parse", "origin/sprint/x").strip()
    assert git_sandbox.sh(work, "ls-remote", "--heads", "origin") == before
    assert not any(m == "POST" for m, _p, _b in hub.requests)


def test_assemble_reads_the_planned_version_from_the_label(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    assert assemble.assemble(work, _hub(labels=("release:minor",)), _target()).version == "1.3.0"


def test_assemble_with_two_level_labels_stops(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    with pytest.raises(bump_decide.LevelError):
        assemble.assemble(work, _hub(labels=("release:minor", "release:major")), _target())


def test_issue_2217_tests_only_change_is_not_released(tmp_path: Path) -> None:
    """#2217: テストだけの変化は、宣言が無ければ版を上げず、本文も書かない。"""
    work = _origin(tmp_path, test_only=True)
    hub = _hub()
    with pytest.raises(assemble.NothingToRelease, match=f"^origin/main..{gather.pull_ref(PR)} に版を上げる変化が無い$"):
        assemble.assemble(work, hub, _target())
    assert not any(m == "PATCH" for m, _p, _b in hub.requests)


def test_collect_reads_the_range_from_main_to_the_pr_head(tmp_path: Path) -> None:
    """collect は範囲（main..PR の先端）の変化から版上げの計画と承認資料の材料を返し、本文を書かない。"""
    work = _origin(tmp_path)
    hub = _hub()
    facts = assemble.collect(work, hub, _target())
    expected = [version_decide.Planned("kit", "1.2.3", "1.2.4")]
    assert facts == materials_decide.Facts(
        "production",
        tuple(expected),
        (materials_decide.PullFact(5, "直した", checks_decide.Tally(2, 1, (), ())),),
        (changes_decide.Change("kit", ("tools/kit/a.py",), ()),),
        (),
        git_sandbox.sh(work, "rev-parse", "origin/sprint/x").strip(),
    )
    assert not any(m != "GET" for m, _p, _b in hub.requests)


def test_collect_mixed_range_splits_changes_and_instructions(tmp_path: Path) -> None:
    extra = ("tools/kit/tests/test_b.py", "AGENTS.md", ".ndf/release.json")
    work = _origin(tmp_path, extra=extra)
    facts = assemble.collect(work, _hub(), _target())
    assert facts.changes == (changes_decide.Change("kit", ("tools/kit/a.py",), ("tools/kit/tests/test_b.py",)),)
    assert facts.instructions == (".ndf/release.json", "AGENTS.md")


def test_collect_refuses_when_head_is_not_the_pr_head(tmp_path: Path) -> None:
    work = _origin(tmp_path)
    git_sandbox.sh(work, "switch", "-q", "--detach", "origin/main")
    with pytest.raises(assemble.AssembleError, match=f"#{PR} の先端でない"):
        assemble.collect(work, _hub(), _target())


def test_collect_stops_on_a_flow_that_ndf_next_does_not_release(tmp_path: Path) -> None:
    """決定 5: 取り込み方の行が github-flow でなければ、既定の行へ倒さずに止まる。"""
    work = _origin(tmp_path)
    write_json(work / ".ndf" / "worktree.json", {"branch_flow": {"promotion": "whole"}})
    with pytest.raises(decl_mod.DeclarationError, match="promote-whole は ndf-next のリリースで扱わない"):
        assemble.collect(work, _hub(), _target())


def test_issue_2554_skips_a_tagged_version(tmp_path: Path) -> None:
    """#2554: 次の版（1.2.4）が既にタグにあれば、その版数で組まず次に空いている版へ繰り上げる。"""
    work = _origin(tmp_path, tags=("kit--v1.2.4",))
    facts = assemble.collect(work, _hub(), _target())
    assert [p.next for p in facts.planned] == ["1.2.5"]


def test_reassembling_after_a_fix_targets_the_new_head_and_makes_the_old_approval_stale(tmp_path: Path) -> None:
    """受け入れ条件 9: 差し戻しの後に直しが同じ PR へ入り先端が C になれば、資料の対象は C で、A への承認は stale。"""
    work = _origin(tmp_path)
    hub = _hub()
    first = assemble.assemble(work, hub, _target())
    a = first.facts.head
    hub.pulls[PR] = hub.pulls[PR]._replace(body=_written_body(hub))
    git_sandbox.sh(work, "switch", "-q", SPRINT)
    (work / "tools" / "kit" / "b.py").write_text("y = 2\n", encoding="utf-8")
    git_sandbox.sh(work, "add", "-A")
    git_sandbox.sh(work, "commit", "-q", "-m", "直し")
    git_sandbox.sh(work, "push", "-q", "origin", SPRINT)
    git_sandbox.sh(tmp_path / "origin.git", "update-ref", f"refs/pull/{PR}/head", f"refs/heads/{SPRINT}")
    hub.requests.clear()
    second = assemble.assemble(work, hub, _target())
    c = second.facts.head
    body = _written_body(hub)
    assert c != a and f"`{c}`" in body and f"`{a}`" not in body and body.count(materials_decide.MATERIALS_START) == 1
    hub.pulls[PR] = hub.pulls[PR]._replace(head_sha=c)
    hub.comments_of[PR] = [trail.Authored("octocat", "OWNER", approval_decide.render_trail("approved", a, trail.USER_BY))]
    assert trail.read(hub, PR).judgment.state == approval_decide.STALE


@pytest.mark.parametrize(
    ("test_only", "expected"),
    [
        (False, [changes_decide.Change("kit", ("tools/kit/a.py",), ())]),
        (True, [changes_decide.Change("kit", (), ("tools/kit/tests/test_a.py",))]),
    ],
)
def test_gather_changes_current_behavior(tmp_path: Path, test_only: bool, expected: list[changes_decide.Change]) -> None:
    """現状固定（gather.changes）: 範囲の変わったファイルをプラグインごとに配る・配らないへ分ける。空の範囲は空。"""
    work = _origin(tmp_path, test_only=test_only)
    repo_git = git_mod.Git(work)
    found, decl = plugins_mod.load(work), decl_mod.load(work)
    assert gather.changes(repo_git.changed_files("origin/main", f"origin/{SPRINT}"), found, decl) == expected
    assert gather.changes(repo_git.changed_files("origin/main", "origin/main"), found, decl) == []


def test_cli_assemble_outputs_the_pr_and_the_version_and_refuses_the_channel(monkeypatch, capsys, tmp_path: Path) -> None:
    work = _origin(tmp_path)
    hub = _hub()
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    assert release_cli.main(["assemble", "--root", str(work), "--pr", str(PR), "--production", "main"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["outputs"] == {"pr": PR, "version": "1.2.4"}
    with pytest.raises(SystemExit):
        release_cli.main(["assemble", "--root", str(work), "--pr", str(PR), "--channel", "dev"])


def test_splice_replaces_only_the_section_and_drops_a_broken_mark() -> None:
    start, end = materials_decide.MATERIALS_START, materials_decide.MATERIALS_END
    body = f"前\n\n{start}\n古い\n{end}\n後\n"
    assert materials_decide.splice(body, "新しい\n") == f"前\n\n{start}\n新しい\n{end}\n後\n"
    assert materials_decide.splice("本文", "資料") == f"本文\n\n{start}\n資料\n{end}\n"
    assert materials_decide.splice("", "資料") == f"{start}\n資料\n{end}\n"
    broken = materials_decide.splice(f"本文\n{start}\n半端", "資料")
    assert broken.count(start) == 1 and broken.endswith(f"{start}\n資料\n{end}\n") and "半端" in broken


def test_the_version_record_refuses_a_suffixed_version() -> None:
    assert records_trail.VERSION_MARK in records_trail.render_version_record("1.3.0", "kit--v1.3.0", 20)
    with pytest.raises(ValueError):
        records_trail.render_version_record("1.3.0-next.1", "kit--v1.3.0", 20)
