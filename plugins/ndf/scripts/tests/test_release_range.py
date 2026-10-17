"""release-steps.py: 範囲の PR（develop へ直接入った PR とスプリント経由の PR）が CHANGELOG と承認資料に載る（#1885・#1686）。

一時の git リポジトリ（origin は bare。ベースブランチは trunk・本番チャネルは live）に正式版のタグを打ち、
ベースブランチへ直接入った PR、スプリントブランチへ実装 PR を merge commit で集めたスプリント PR、版上げの PR を入れる。
GitHub の読み取り（PR の中身・CI・squash の head）だけを差し替える。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release-steps.py"
VER = "1.0.1"
CH = "## 利用者向けの変化\n\n"
BODIES = {
    21: CH + "- 直接の変化\n\n## 移行の手順\n\n- bar を入れ直す\n\n## 未検証・残る危険\n\n- 直接の PR は実機で未確認\n",
    22: CH + "- なし\n",
    23: CH + "- 無し\n",
    24: "節の無い本文\n",
    30: CH + "- 実装 1 の変化（#31）\n",
    31: CH + "- 実装 1 の変化\n\n## 移行の手順\n\n- 実装 1 の手順\n\n## 未検証・残る危険\n\n- 実装 1 の危険\n",
    32: CH + "- 無し\n",
    50: CH + "- dev.2 の変化\n",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit_file(root: Path, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"change {name}")


def merge_into(root: Path, n: int, branch: str, into: str, name: str | None = None) -> None:
    git(root, "checkout", "-q", "-b", branch, into)
    commit_file(root, name or f"f{n}.txt")
    git(root, "checkout", "-q", into)
    git(root, "merge", "--no-ff", "-q", branch, "-m", f"Merge pull request #{n} from o/{branch}")


def add_sprint(root: Path) -> None:
    """スプリントブランチへ実装 PR 31・32 を merge commit で入れ、スプリント PR 30 を trunk へ入れる。"""
    git(root, "branch", "sprint/m1", "trunk")
    merge_into(root, 31, "feat/i1", "sprint/m1")
    merge_into(root, 32, "feat/i2", "sprint/m1")
    git(root, "checkout", "-q", "trunk")
    git(root, "merge", "--no-ff", "-q", "sprint/m1", "-m", "Merge pull request #30 from o/sprint/m1")


def make_world(tmp_path: Path) -> dict:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "trunk", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "trunk")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "t")
    (seed / ".ndf").mkdir()
    (seed / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "trunk", "production_branch": "live"}))
    (seed / ".ndf" / "supervise.json").write_text(json.dumps({"release": {"form": "package-plugin", "plugin": "foo"}}))
    (seed / "CHANGELOG.md").write_text("# Changelog\n\n## [foo 1.0.0] - 2026-10-01\n\n- 前の版\n", encoding="utf-8")
    commit_file(seed, "plugins/foo/.claude-plugin/plugin.json")
    commit_file(seed, "plugins/playwright-kit/.claude-plugin/plugin.json")
    (seed / "plugins/playwright-kit/.claude-plugin/plugin.json").write_text(json.dumps({"version": "2.0.6"}))
    (seed / "plugins/foo/.claude-plugin/plugin.json").write_text(json.dumps({"version": "1.0.0"}))
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "HEAD:trunk", "HEAD:live")
    git(seed, "tag", "foo--v1.0.0")
    git(seed, "push", "-q", "origin", "foo--v1.0.0")
    merge_into(seed, 21, "feat/direct", "trunk", "plugins/playwright-kit/skills/x.md")  # develop へ直接（移行の手順あり）
    merge_into(seed, 22, "spec/none", "trunk")  # 配布物の変化なし（「なし」）
    add_sprint(seed)
    commit_file(seed, "release.txt")
    git(seed, "commit", "-q", "--amend", "-m", "release: v1.0.1-dev.1 (#40)")
    git(seed, "push", "-q", "origin", "HEAD:trunk")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    return {"seed": seed, "work": work, "present": tmp_path / "present"}


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path)


def merge_oid(root: Path, n: int) -> str:
    return git(root, "log", "--all", "--format=%H", f"--grep=Merge pull request #{n} from") or f"{n:040d}"


def load(monkeypatch, w: dict, bodies=None):
    spec = importlib.util.spec_from_file_location("release_steps_1885", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_1885", mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "emit", lambda obj, code=None, **k: (_ for _ in ()).throw(SystemExit((obj, code))))
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(w["present"]))
    monkeypatch.setattr(mod.repo_lib, "owner_repo", lambda r: "o/n")
    bodies = {**BODIES, **(bodies or {})}
    reads = []

    def pr_fields(ns, cwd=None, comments=False):
        reads.append(list(ns))
        return {
            n: {
                "number": n,
                "title": f"題 {n}",
                "state": "MERGED",
                "body": bodies.get(n, ""),
                "url": f"https://github.com/o/n/pull/{n}",
                "mergeCommit": {"oid": merge_oid(w["work"], n)},
                "closingIssuesReferences": [],
                **({"comments": []} if comments else {}),
            }
            for n in ns
        }, ""

    def no_view(root, n, fields):
        raise AssertionError(f"PR ごとの読み出し #{n}")

    monkeypatch.setattr(mod.included, "pr_fields", pr_fields)
    monkeypatch.setattr(mod, "pr_view", no_view)
    monkeypatch.setattr(
        mod.included.merged_range.gh_rest, "pr_head_branches", lambda ns, cwd=None: ({n: "release/v1.0.1-dev.1" for n in ns}, "")
    )
    monkeypatch.setattr(mod, "pr_check_buckets", lambda r, n: [{"bucket": "pass"}])
    mod.reads = reads
    return mod


def call(fn, **kw):
    with pytest.raises(SystemExit) as e:
        fn(argparse.Namespace(**kw))
    return e.value.code


def changelog(mod, w, prs, version=VER):
    return call(mod.cmd_changelog, root=str(w["work"]), version=version, prs=list(prs), plugin=None)


def notes(mod, w, prs, approval=None, version=VER, check_state=None):
    kw = dict(root=str(w["work"]), version=version, prs=list(prs), plugin=None, approval=approval, verified="", ref="trunk")
    return call(mod.cmd_notes, **kw, check_state=check_state)


def facts(mod, w, prs):
    return call(mod.cmd_approval_facts, root=str(w["work"]), version=VER, prs=list(prs), prev_tag=None, plugin=None, approval=None)


def section(w) -> str:
    text = (w["work"] / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [foo 1.0.1]")
    return text[start : text.index("## [foo 1.0.0]")]


def included_rows(path) -> list[str]:
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if line.startswith("| 含む PR |"):
            return line.split(" | ", 1)[1].rstrip(" |").split("<br>")
    raise AssertionError("含む PR の欄が無い")


def cell(path, key) -> str:
    return next(line for line in Path(path).read_text(encoding="utf-8").split("\n") if line.startswith(f"| {key} |"))


def numbers(rows) -> list[int]:
    return [int(r.split()[0][1:]) for r in rows]


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
    assert "スプリント PR #30 経由" in by[31] and "スプリント PR #30 経由" in by[32] and "配布物の変化なし" in by[32]
    assert "配布物の変化なし" in by[22] and "経由" not in by[21] and "配布物の変化なし" not in by[21]
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
