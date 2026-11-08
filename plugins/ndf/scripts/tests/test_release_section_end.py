"""release-steps.py: 版の節と承認資料に、出す版のコミット（範囲の終点）に入った PR だけを載せる（#2555）。

test_release_range.py の一時リポジトリ（タグ foo--v1.0.0・直接の PR #21 #22・スプリント PR #30）を使い、
worktree（work）を承認したコミットに置いたまま、ベースブランチ（trunk）へ後から PR をマージする。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_release_range import changelog, facts, git, included_rows, load, make_world, merge_into, merge_oid, notes, numbers  # noqa: E402


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path)


def merge_late(w: dict, n: int = 60) -> None:
    """承認したコミット（work の HEAD）より後に、ベースブランチへ PR n をマージする。"""
    merge_into(w["seed"], n, f"feat/late{n}", "trunk")
    git(w["seed"], "push", "-q", "origin", "HEAD:trunk")


def text(w: dict) -> str:
    return (w["work"] / "CHANGELOG.md").read_text(encoding="utf-8")


def ancestor_checks(monkeypatch, mod) -> list:
    """included の `git merge-base --is-ancestor` の呼び出しを記録する。"""
    seen, real = [], mod.included.proc.git

    def spy(root, *args, **kw):
        if args[:2] == ("merge-base", "--is-ancestor"):
            seen.append(args[2])
        return real(root, *args, **kw)

    monkeypatch.setattr(mod.included.proc, "git", spy)
    return seen


def test_later_merges_stay_out_of_the_section(monkeypatch, world):
    """受け入れ条件 5・I1: 承認したコミットより後にマージした PR は、--prs で渡しても版の節に載らず、outside に出る。"""
    mod = load(monkeypatch, world)
    merge_late(world)
    seen = ancestor_checks(monkeypatch, mod)
    out, _ = changelog(mod, world, [21, 30, 60])
    assert "#60" not in text(world) and "（#21）" in text(world)
    assert out["metrics"]["outside"] == [60] and out["metrics"]["unmerged"] == []
    assert {"kind": "pr", "name": "#60", "result": "skipped", "reason": "範囲の終点に入っていない"} in out["items"]
    assert "範囲の終点に入っていない #60" in out["summary"]
    assert seen == [merge_oid(world["work"], 60)]  # 範囲の PR（#21・#30）には祖先の確かめを打たない
    out, _ = notes(mod, world, [21, 30, 60])
    assert "#60" not in text(world) and out["metrics"]["outside"] == [60]


def test_prs_inside_the_end_but_outside_the_range_are_listed(monkeypatch, world):
    """I1: 範囲の PR に無い --prs でも、マージコミットが終点の祖先なら載る。"""
    mod = load(monkeypatch, world)
    squash = git(world["work"], "rev-parse", "HEAD")  # 版上げの PR として範囲から外れるコミット
    real = mod.included.pr_fields

    def fields(ns, cwd=None, comments=False):
        got, why = real(ns, cwd, comments)
        got[70] = {**got[70], "mergeCommit": {"oid": squash}}
        return got, why

    monkeypatch.setattr(mod.included, "pr_fields", fields)
    out, _ = changelog(mod, world, [30, 70])
    assert "（#70）" in text(world) and out["metrics"]["outside"] == []


def test_approval_facts_uses_the_approved_commit_as_the_end(monkeypatch, world):
    """受け入れ条件 5・I2: 承認に出すコミットを決めた後のマージは「含む PR」に無く、終点と承認したコミットが同じ。"""
    mod = load(monkeypatch, world)
    work = world["work"]
    approved = git(work, "rev-parse", "origin/trunk")
    real, ends = mod.included.release_range, []

    def moved(root, prev, to):
        merge_late(world)
        git(work, "fetch", "-q", "origin")
        ends.append(to)
        return real(root, prev, to)

    monkeypatch.setattr(mod.included, "release_range", moved)
    out, _ = facts(mod, world, [30, 60])
    assert 60 not in numbers(included_rows(out["presentation_path"]))
    assert ends == [approved] and out["metrics"]["approved_sha"] == approved and out["metrics"]["outside"] == [60]


def test_notes_with_approval_keeps_the_listed_prs(monkeypatch, world):
    """notes --approval は承認資料の「含む PR」を外さない（HEAD がその PR のマージより前でも）。"""
    mod = load(monkeypatch, world)
    path = facts(mod, world, [30])[0]["presentation_path"]
    git(world["work"], "checkout", "-q", "foo--v1.0.0")
    notes(mod, world, [30], approval=path)
    assert "実装 1 の変化（#31）" in Path(path).read_text(encoding="utf-8")


@pytest.mark.parametrize("sub", ["changelog", "notes"])
def test_a_tagged_version_section_is_not_written(monkeypatch, world, sub):
    """受け入れ条件 6・I3: 基底の版にリリースタグがあれば、書き込まずに 3 で止まり、理由にタグの名前が出る。"""
    mod = load(monkeypatch, world)
    changelog(mod, world, [30], "1.0.1-dev.1")
    git(world["seed"], "tag", "foo--v1.0.1")
    git(world["seed"], "push", "-q", "origin", "foo--v1.0.1")
    before = text(world)
    with pytest.raises(mod.StepError) as e:
        (changelog if sub == "changelog" else notes)(mod, world, [30], version="1.0.1-dev.2")
    assert e.value.code == 3 and "foo--v1.0.1" in str(e.value)
    assert text(world) == before


def middle_sections(w: dict) -> None:
    cl = w["work"] / "CHANGELOG.md"
    cl.write_text(
        "# Changelog\n\n## [foo 1.0.2] - 2026-10-03\n\n- 題 30（#30）\n\n"
        "## [foo 1.0.1] - 2026-10-02\n\n- 直接（#21）\n\n## [foo 1.0.0] - 2026-10-01\n\n- 前の版\n",
        encoding="utf-8",
    )


def test_release_drops_untagged_sections_since_the_last_tag(monkeypatch, world):
    """受け入れ条件 7・I4: 正式版 1.0.3 の changelog が、タグの無い 1.0.1・1.0.2 の節を消し、その PR を 1.0.3 に並べる。"""
    mod = load(monkeypatch, world)
    middle_sections(world)
    out, _ = changelog(mod, world, [30], "1.0.3")
    t = text(world)
    assert "## [foo 1.0.2]" not in t and "## [foo 1.0.1]" not in t and "## [foo 1.0.0]" in t and "- 前の版" in t
    sec = t[t.index("## [foo 1.0.3]") : t.index("## [foo 1.0.0]")]
    assert "（#21）" in sec and "（#30）" in sec
    removed = [i["heading"] for i in out["items"] if i.get("result") == "removed"]
    assert removed == ["## [foo 1.0.2] - 2026-10-03", "## [foo 1.0.1] - 2026-10-02"]


def test_dev_versions_keep_the_middle_sections(monkeypatch, world):
    """受け入れ条件 7（開発版）: 開発版の changelog は途中の版の節を消さない。"""
    mod = load(monkeypatch, world)
    middle_sections(world)
    changelog(mod, world, [30], "1.0.3-dev.1")
    assert "## [foo 1.0.2]" in text(world) and "## [foo 1.0.1]" in text(world)


def test_head_at_the_base_tip_keeps_the_same_range(monkeypatch, world):
    """受け入れ条件 9: 終点がベースブランチの先頭と同じなら、範囲の PR は origin/<ベースブランチ> を終点にしたときと同じ。"""
    mod = load(monkeypatch, world)
    work = world["work"]
    head = git(work, "rev-parse", "HEAD")
    assert head == git(work, "rev-parse", "origin/trunk")
    got, extra_out = mod.included.section_range(work, "foo--v1.0.0", head, [30])
    assert got == mod.included.release_range(work, "foo--v1.0.0", "origin/trunk") and extra_out == []
