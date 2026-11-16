"""release-steps.py: 範囲の PR（develop へ直接入った PR とスプリント経由の PR）が CHANGELOG と承認資料に載る（#1885・#1686）。

一時の git リポジトリ（origin は bare。ベースブランチは trunk・本番チャネルは live）に正式版のタグを打ち、
ベースブランチへ直接入った PR、スプリントブランチへ実装 PR を merge commit で集めたスプリント PR、版上げの PR を入れる。
GitHub の読み取り（PR の中身・CI・squash の head）だけを差し替える。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_range_world import (  # noqa: E402
    BODIES,
    CH,
    call,
    cell,
    changelog,
    facts,
    git,
    included_rows,
    load,
    make_world,
    merge_into,
    notes,
    numbers,
    section,
)


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path)


def test_range_has_direct_and_sprint_routed_prs_without_release_prs(monkeypatch, world):
    """1885-5・1686-1・I1〜I3: 範囲は直接の PR・スプリント PR・スプリント経由の PR で、版上げは入らず、merges_in は変わらない。"""
    mod = load(monkeypatch, world)
    work = world["work"]
    got = mod.included.release_range(work, "foo--v1.0.0", "origin/trunk")
    assert [(p.number, p.via) for p in got] == [(21, None), (22, None), (30, None), (31, 30), (32, 30)]
    top = [m.number for m in mod.included.merged_range.merges_in(work, "foo--v1.0.0", "origin/trunk")]
    assert 31 not in top and 32 not in top and 30 in top


def test_changelog_and_notes_take_the_range_with_only_the_sprint_pr(monkeypatch, world):
    """1885-1・1686-2・1686-3: --prs 30 だけで、直接の PR #21 が版の節に載り、変化なしの #22 とスプリント経由の重複は載らない。"""
    mod = load(monkeypatch, world)
    out, _ = changelog(mod, world, [30])
    assert out["metrics"]["since"] == "foo--v1.0.0"
    sec = section(world)
    assert "（#21）" in sec and "（#30）" in sec and "#22" not in sec and "#31）" not in sec
    assert mod.reads == [[21, 22, 30, 31, 32]]  # 範囲の PR はまとめて 1 回で読む
    notes(mod, world, [30])
    sec = section(world)
    assert "- 直接の変化（#21）" in sec and sec.count("実装 1 の変化（#31）") == 1 and "#22" not in sec
    assert "- bar を入れ直す（#21）" in sec


def test_dev2_keeps_the_dev1_prs(monkeypatch, world):
    """1885-3・I4: dev.1 を --prs 30 で出した後、同じ版の dev.2 を --prs 50 だけで出しても dev.1 の PR の箇条が残る。"""
    mod = load(monkeypatch, world)
    changelog(mod, world, [30], "1.0.1-dev.1")
    notes(mod, world, [30], version="1.0.1-dev.1")
    seed = world["seed"]
    merge_into(seed, 50, "feat/dev2", "trunk")
    git(seed, "push", "-q", "origin", "HEAD:trunk")
    git(world["work"], "pull", "-q", "origin", "trunk")  # dev.2 の worktree はその時点のベースブランチの先頭から作る
    changelog(mod, world, [50], "1.0.1-dev.2")
    notes(mod, world, [50], version="1.0.1-dev.2")
    sec = section(world)
    assert "直接の変化（#21）" in sec and "実装 1 の変化（#31）" in sec and "dev.2 の変化（#50）" in sec


def test_approval_lists_every_pr_with_marks_once(monkeypatch, world):
    """1885-1・1885-4・1686-1・1686-2・I5: 「含む PR」は 5 行で、経由と変化なしの印を持ち、--prs の重複は 1 行。"""
    mod = load(monkeypatch, world)
    out, code = facts(mod, world, [30, 21, 30])
    rows = included_rows(out["presentation_path"])
    assert code == 10 and numbers(rows) == [21, 22, 30, 31, 32]
    by = dict(zip(numbers(rows), rows))
    assert "スプリント PR #30 経由" in by[31] and "スプリント PR #30 経由" in by[32] and "利用者向けの変化なし" in by[32]
    assert "利用者向けの変化なし" in by[22] and "経由" not in by[21] and "利用者向けの変化なし" not in by[21]
    assert mod.included.read_included_rows(out["presentation_path"])[3] == mod.included.IncludedPr(31, 30)
    assert mod.reads == [[21, 22, 30, 31, 32]]


def test_notes_approval_cells_from_the_rows(monkeypatch, world):
    """1885-1・1686-2・1686-3: 配る中身は #21 と #31 の変化を 1 回ずつ持ち、#22 を載せず、移行の手順に #21 が載る。"""
    mod = load(monkeypatch, world)
    path = facts(mod, world, [30])[0]["presentation_path"]
    notes(mod, world, [30], approval=path)
    shown = cell(path, "配る中身")
    assert "直接の変化（#21）" in shown and shown.count("実装 1 の変化（#31）") == 1 and "#22" not in shown
    text = Path(path).read_text(encoding="utf-8")
    assert "- bar を入れ直す（#21）" in text and "- 実装 1 の手順" not in text
    assert "- 実装 1 の危険（#31）" in text and "- 直接の PR は実機で未確認（#21）" in text


@pytest.mark.parametrize("n, listed", [(22, False), (23, False), (24, True)])
def test_no_change_words_and_missing_section(monkeypatch, world, n, listed):
    """1686-2・決定 4: 「なし」「無し」だけの PR は版の節に載らず、節の無い PR は題名で載る。"""
    mod = load(monkeypatch, world, {22: BODIES[n]})
    changelog(mod, world, [30])
    assert ("（#22）" in section(world)) is listed


def test_changed_plugins_reads_the_direct_pr_migration(monkeypatch, world):
    """1885-2: --prs 30 だけでも、直接の PR #21 の移行の手順が playwright-kit に触れ、候補が MAJOR になる。"""
    mod = load(monkeypatch, world, {21: CH + "- 直接\n\n## 移行の手順\n\n- playwright-kit の `--x` を外す\n"})
    out, _ = call(
        mod.cmd_changed_plugins, root=str(world["work"]), plugin=None, since=None, prs=[30], approval=None, set=None, decided=None
    )
    (it,) = [i for i in out["items"] if i.get("name") == "playwright-kit"]
    assert it["level"] == "major", it
