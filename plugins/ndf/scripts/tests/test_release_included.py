"""release-steps.py: 承認資料の「含む PR」と notes --approval の欄（#1274）。

一時の git リポジトリ（origin は bare。ベースブランチは trunk・本番チャネルは live）に正式版のタグを打ち、
merge commit・squash merge・版上げ（release/）の PR を入れる。GitHub の読み取り（PR の中身・CI・squash の head）だけを差し替える。
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

# squash の件名の (#N) から GitHub が返す head。740 は課題の番号（PR でない）
HEADS = {15: "check/m-1", 16: "release/v1.0.1-dev.2", 740: ""}
BODIES = {
    11: "## 利用者向けの変化\n\n- A が変わる\n\n## 未検証・残る危険\n\n- A は実機で未確認\n",
    13: "## 利用者向けの変化\n\n- B が変わる\n",
    15: "## 利用者向けの変化\n\n- 検査の修正\n",
    21: "## 利用者向けの変化\n\n- 含む PR に無い\n",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def change(root: Path, name: str) -> None:
    (root / name).write_text(f"{name}\n")
    git(root, "add", "-A")


def merge_pr(root: Path, n: int, branch: str) -> None:
    """branch を作って 1 コミットし、GitHub の merge commit の件名で trunk へ --no-ff でマージする。"""
    git(root, "checkout", "-q", "-b", branch)
    change(root, f"f{n}.txt")
    git(root, "commit", "-q", "-m", f"change {n}")
    git(root, "checkout", "-q", "trunk")
    git(root, "merge", "--no-ff", "-q", branch, "-m", f"Merge pull request #{n} from o/{branch}")


def squash_pr(root: Path, n: int, title: str) -> None:
    change(root, f"s{n}.txt")
    git(root, "commit", "-q", "-m", f"{title} (#{n})")


def make_world(tmp_path: Path, tag: bool = True) -> dict:
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
    change(seed, "a.txt")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "HEAD:trunk", "HEAD:live")
    if tag:
        git(seed, "tag", "foo--v1.0.0")
        git(seed, "push", "-q", "origin", "foo--v1.0.0")
    merge_pr(seed, 11, "feat/a")  # dev.1 の PR
    merge_pr(seed, 12, "release/v1.0.1-dev.1")  # dev.1 の版上げ
    merge_pr(seed, 13, "feat/b")  # dev.2 の PR
    squash_pr(seed, 15, "fix: 検査の修正")
    squash_pr(seed, 16, "release: v1.0.1-dev.2")
    squash_pr(seed, 740, "docs: 課題を指す")
    git(seed, "push", "-q", "origin", "HEAD:trunk")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    return {"seed": seed, "work": work, "present": tmp_path / "present"}


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path)


def load(monkeypatch, w: dict, heads=HEADS, states=None):
    spec = importlib.util.spec_from_file_location("release_steps_1274", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_1274", mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "emit", lambda obj, code=None, **k: (_ for _ in ()).throw(SystemExit((obj, code))))
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(w["present"]))
    monkeypatch.setattr(mod.repo_lib, "owner_repo", lambda r: "o/n")
    asked = []

    def pr_head_branches(ns, cwd=None):
        asked.extend(ns)
        return (None, "boom") if heads is None else ({n: heads.get(n, "") for n in ns}, "")

    def pr_view(root, n, fields):
        state = (states or {}).get(n, "MERGED")
        return {"number": n, "title": f"題 {n}", "state": state, "mergeCommit": {"oid": f"{n:040d}"}, "body": BODIES.get(n, "")}

    monkeypatch.setattr(mod.included.merged_range.gh_rest, "pr_head_branches", pr_head_branches)
    monkeypatch.setattr(mod, "pr_view", pr_view)
    monkeypatch.setattr(mod, "pr_check_buckets", lambda r, n: [{"bucket": "pass"}])
    mod.asked = asked
    return mod


def facts(mod, w: dict, prs, prev_tag=None):
    with pytest.raises(SystemExit) as e:
        mod.cmd_approval_facts(argparse.Namespace(root=str(w["work"]), version=VER, prs=list(prs), prev_tag=prev_tag, plugin=None))
    return e.value.code


def included_rows(path) -> list[str]:
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if line.startswith("| 含む PR |"):
            return line.split(" | ", 1)[1].rstrip(" |").split("<br>")
    raise AssertionError("含む PR の欄が無い")


def numbers(rows) -> list[int]:
    return [int(r.split()[0][1:]) for r in rows]


def test_included_lists_the_prs_since_the_tag_without_release_prs(monkeypatch, world):
    """AC1・AC2・AC5・AC6・I4: dev.2 の PR だけを渡しても dev.1 の PR が並び、版上げと課題の番号は並ばない。"""
    mod = load(monkeypatch, world)
    out, code = facts(mod, world, [13])
    rows = included_rows(out["presentation_path"])
    assert code == 10 and numbers(rows) == [11, 13, 15]
    assert [i["name"] for i in out["items"] if i["kind"] == "pr"] == ["#11", "#13", "#15"]
    assert "PR 3 件" in out["summary"]
    assert sorted(mod.asked) == [15, 16, 740]  # head は squash の番号だけを読む


def test_prs_given_twice_are_listed_once(monkeypatch, world):
    """AC3: --prs に範囲の PR と同じ番号を渡しても 1 行。"""
    mod = load(monkeypatch, world)
    out, _ = facts(mod, world, [13, 11, 13])
    assert numbers(included_rows(out["presentation_path"])) == [11, 13, 15]


def test_prs_outside_the_range_are_added_with_their_state(monkeypatch, world):
    """AC4・I4: 範囲の外のマージ前の PR も並び、状態が行に出る。大きい番号を先に渡しても昇順。"""
    mod = load(monkeypatch, world, states={99: "OPEN"})
    out, _ = facts(mod, world, [99, 13])
    rows = included_rows(out["presentation_path"])
    assert numbers(rows) == [11, 13, 15, 99] and "（OPEN・" in rows[-1]
    assert [i["result"] for i in out["items"]][-1] == "open"


def test_compare_and_changes_stay_and_later_merges_are_not_listed(monkeypatch, world):
    """AC10・I3: compare・変更量・approved_sha は承認したコミットのまま。決めた後にベースブランチが進んでも並ばない。"""
    mod = load(monkeypatch, world)
    seed, work = world["seed"], world["work"]
    approved = git(seed, "rev-parse", "HEAD")
    real = mod.included.range_prs

    def moved(root, prev, to):
        merge_pr(seed, 50, "feat/late")
        git(seed, "push", "-q", "origin", "HEAD:trunk")
        git(work, "fetch", "-q", "origin")
        return real(root, prev, to)

    monkeypatch.setattr(mod.included, "range_prs", moved)
    out, _ = facts(mod, world, [13])
    m = out["metrics"]
    stat = git(work, "diff", "--shortstat", "origin/live...origin/trunk")
    assert m["approved_sha"] == approved and m["compare"] == f"https://github.com/o/n/compare/foo--v1.0.0...{approved}"
    assert m["prev_tag"] == "foo--v1.0.0" and f"{m['files']} files changed" in stat
    assert 50 not in numbers(included_rows(out["presentation_path"]))


@pytest.mark.parametrize("case", ["no_log", "no_heads"])
def test_unreadable_range_writes_no_approval(monkeypatch, world, case):
    """AC8・I7: 範囲の git のログか squash の head が読めなければ、承認資料を書かずに 0 以外で止まる。"""
    mod = load(monkeypatch, world, heads=None if case == "no_heads" else HEADS)
    with pytest.raises(mod.StepError) as e:
        facts(mod, world, [13], prev_tag="foo--v9.9.9" if case == "no_log" else None)
    assert e.value.code == (2 if case == "no_log" else 1) and str(e.value)
    assert not world["present"].exists() or not any(world["present"].iterdir())


def test_without_a_tag_stops_with_three(monkeypatch, tmp_path):
    """AC9: 正式版のタグが無く --prev-tag も無いと 3。範囲の収集より前に止まる。"""
    w = make_world(tmp_path, tag=False)
    mod = load(monkeypatch, w)
    with pytest.raises(mod.StepError) as e:
        facts(mod, w, [13])
    assert e.value.code == 3 and mod.asked == []


def notes(mod, w: dict, path, prs):
    a = argparse.Namespace(root=str(w["work"]), version=VER, prs=list(prs), approval=str(path), verified="", ref="trunk", plugin=None)
    with pytest.raises(SystemExit) as e:
        mod.cmd_notes(a)
    return e.value.code


def test_notes_write_the_cells_from_the_included_prs(monkeypatch, world):
    """AC7・I5: notes --prs 13 --approval でも、配る中身と残る危険は含む PR のすべての本文から書く。"""
    mod = load(monkeypatch, world)
    path = facts(mod, world, [13])[0]["presentation_path"]
    out, code = notes(mod, world, path, [13])
    text = Path(path).read_text(encoding="utf-8")
    assert code is None and out["metrics"]["prs"] == 3
    assert "| 配る中身 | - A が変わる（#11）<br>- B が変わる（#13）<br>- 検査の修正（#15） |" in text
    assert "- A は実機で未確認（#11）" in text


def test_notes_stop_when_a_pr_is_not_included(monkeypatch, world):
    """I5: 「含む PR」に無いマージ済みの PR を --prs に渡すと 3 で止まり、欄を書かない。"""
    mod = load(monkeypatch, world)
    path = Path(facts(mod, world, [13])[0]["presentation_path"])
    before = path.read_bytes()
    with pytest.raises(mod.StepError) as e:
        notes(mod, world, path, [13, 21])
    assert e.value.code == 3 and "#21" in str(e.value) and path.read_bytes() == before


def test_notes_stop_without_the_included_cell(monkeypatch, world, tmp_path):
    """AC8・I7: 「含む PR」の欄が無い承認資料には欄を書かない。"""
    mod = load(monkeypatch, world)
    path = tmp_path / "approval.md"
    path.write_text("| 項目 | 内容 |\n| --- | --- |\n| 配る中身 | 前 |\n| 検証への配布で確かめたこと | 前 |\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(mod.StepError) as e:
        notes(mod, world, path, [13])
    assert e.value.code == 3 and path.read_bytes() == before
