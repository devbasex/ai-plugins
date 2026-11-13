"""project-decl.py のブランチ運用（#3121）: 測って案を出す・ずれを出す・答えを書く。

推す規則（`measure_branch_flow.infer`）は合成した Pull Request の並びで、取り込み方の見分け（`classify_release`）と
スプリントの起点（`sprint_start`）は一時の git リポジトリで確かめる。`drift`・`write` は CLI を打つ。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import project_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_flow  # noqa: E402
from project_lib import measure_branch_flow as mbf  # noqa: E402

CLI = SCRIPTS / "project-decl.py"
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}


def pr(base, head):
    return {"base": base, "head": head}


def ai_plugins_history(n=10):
    """今の ai-plugins の使い方: 設計 PR・確定仕様化の PR は develop、検査の PR は sprint/* → develop、本番は丸ごと。"""
    prs = []
    for i in range(n):
        prs += [pr("develop", f"design/issue-{i}"), pr(f"sprint/s{i}", f"feat/issue-{i}-s{i}"), pr("develop", f"sprint/s{i}")]
        prs += [pr("develop", f"spec/s{i}")]
    return prs


def test_measure_proposes_the_current_ai_plugins_flow_with_evidence():
    """受け入れ条件 9・11: 4 項目に値・確からしさ・根拠が出て、案は develop から切り develop を丸ごと出す形になる。"""
    fields = mbf.infer(ai_plugins_history(), "develop", "main", ["whole"] * 5, [])
    assert {k: f["value"] for k, f in fields.items()} == {"start": "base", "integrate": "check", "doc_prs": "base", "promotion": "whole"}
    for f in fields.values():
        assert f["confidence"] == "高" and f["evidence"] and all("command" in e and "text" in e for e in f["evidence"])
    item = mbf.as_item(fields, [])
    assert item["status"] == "measured" and branch_flow.parse_flow(item["value"]).as_dict() == branch_flow.DEFAULT


def test_items_without_enough_samples_say_unknown_with_a_reason():
    """受け入れ条件 9: 標本が 3 件未満か、最も多い値が 6 割に満たない項目は値を出さず理由を出す。"""
    fields = mbf.infer([pr("develop", "design/issue-1")], "develop", "main", ["whole", "selected"], [])
    assert "value" not in fields["doc_prs"] and "3 件に満たない" in fields["doc_prs"]["unknown"]
    assert "value" not in fields["promotion"] and "value" not in fields["start"]
    assert "value" not in fields["integrate"]
    item = mbf.as_item(fields, [])
    assert item["status"] == "question" and item["candidates"] == {"branch_flow": {}}


def test_a_sure_combination_the_table_refuses_goes_back_to_a_question():
    """4 項目が高くても、取り込み方の表に合わない組（selected で start = base）は測った値にせず問いへ戻す。"""
    fields = {k: {"value": v, "confidence": "高", "evidence": []} for k, v in {**SELECTED, "start": "base"}.items()}
    item = mbf.as_item(fields, [])
    assert item["status"] == "question" and item["candidates"] == {"branch_flow": {**SELECTED, "start": "base"}}
    assert "表に合わない" in item["evidence"][0]["text"]


@pytest.mark.parametrize("hits, total, want", [(9, 10, "高"), (6, 10, "中"), (5, 10, None), (2, 2, None)])
def test_confidence_thresholds(hits, total, want):
    assert mbf.confidence(hits, total) == want


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, "init")
    git(r, "checkout", "-q", "-b", "develop")
    commit(r, "dev-bump")
    # 本番の直しを develop へ戻すマージ（本番チャネルの先頭は develop の first-parent の列に無い）
    git(r, "checkout", "-q", "main")
    commit(r, "hotfix")
    git(r, "checkout", "-q", "develop")
    git(r, "merge", "-q", "--no-ff", "--no-edit", "main")
    return r


def publish(repo: Path) -> None:
    """ローカルのブランチを origin/<名前> として見せる（ネットワークを使わない）。"""
    for b in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        git(repo, "update-ref", f"refs/remotes/origin/{b}", b)


def merge_into_main(repo: Path, head: str) -> str:
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", head)
    return git(repo, "rev-parse", "HEAD")


def test_release_kind_is_read_from_the_first_parent_chain(repo):
    """丸ごと出したリリースブランチ（ベースブランチの先頭から切った）と、本番チャネルから切って集めたリリースブランチを見分ける。"""
    git(repo, "checkout", "-q", "-b", "release/v1", "develop")
    commit(repo, "bump1")
    whole = merge_into_main(repo, "release/v1")
    git(repo, "checkout", "-q", "-b", "sprint/a", "main")
    commit(repo, "a")
    git(repo, "checkout", "-q", "-b", "release/v2", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "sprint/a")
    commit(repo, "bump2")
    selected = merge_into_main(repo, "release/v2")
    assert mbf.classify_release(repo, whole, "release/v1", "develop") == "whole"
    assert mbf.classify_release(repo, selected, "release/v2", "develop") == "selected"
    assert mbf.classify_release(repo, selected, "develop", "develop") == "whole"
    assert mbf.classify_release(repo, selected, "feature/x", "develop") is None


def test_sprint_start_is_read_from_base_only_commits(repo):
    """ベースブランチにだけあるコミットを持つスプリントブランチは base、本番チャネルから切ったものは production。"""
    git(repo, "checkout", "-q", "-b", "sprint/from-base", "develop")
    commit(repo, "x")
    git(repo, "checkout", "-q", "-b", "sprint/from-prod", "main")
    commit(repo, "y")
    publish(repo)
    assert mbf.sprint_start(repo, "origin/sprint/from-base", "develop", "main") == "base"
    assert mbf.sprint_start(repo, "origin/sprint/from-prod", "develop", "main") == "production"


def test_drift_lists_only_items_that_differ():
    """受け入れ条件 12: ずれた項目は宣言の値・測った値・根拠・更新の案を持つ。合っていれば空。"""
    item = mbf.as_item(mbf.infer(ai_plugins_history(), "develop", "main", ["whole"] * 5, []), [])
    assert mbf.drift(branch_flow.BranchFlow(), item) == []
    rows = mbf.drift(branch_flow.parse_flow(SELECTED), item)
    assert {r["name"] for r in rows} == {f"branch_flow.{k}" for k in ("start", "doc_prs", "promotion")}
    for r in rows:
        assert r["declared"] != r["measured"] and r["evidence"] and r["proposal"]


def make_tree(tmp_path: Path, wt: dict) -> Path:
    r = tmp_path / "t"
    (r / ".ndf").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    (r / ".ndf" / "worktree.json").write_text(json.dumps(wt))
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def run(*args: str):
    p = inproc.run(CLI, *args)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}, p.stdout + p.stderr


def test_drift_cli_reports_rows_and_stops_on_a_broken_declaration(tmp_path):
    """受け入れ条件 12 と I1: drift はずれた行を出して 0、宣言が壊れていれば 3。"""
    meas = tmp_path / "m.json"
    item = mbf.as_item(mbf.infer(ai_plugins_history(), "develop", "main", ["whole"] * 5, []), [])
    meas.write_text(json.dumps({"items": {"branch_flow": item}}))
    tree = make_tree(tmp_path, {"version": 1, "base_branch": "develop", "branch_flow": SELECTED})
    code, out, text = run("drift", "--measure", str(meas), "--root", str(tree))
    assert code == 0, text
    assert out["metrics"]["drift"] == 3
    (tree / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "branch_flow": {"promotion": "selected"}}))
    code, _, text = run("drift", "--measure", str(meas), "--root", str(tree))
    assert code == 3 and "branch_flow.start" in text


def test_write_refuses_an_answer_outside_the_table(tmp_path):
    """E3: 答えが取り込み方の表に合わなければ書かずに 1。"""
    tree = make_tree(tmp_path, {"version": 1, "base_branch": "develop"})
    meas, answers = tmp_path / "m.json", tmp_path / "a.json"
    meas.write_text(json.dumps({"items": {"branch_flow": {"status": "question", "candidates": {}, "evidence": []}}}))
    answers.write_text(json.dumps({"branch_flow": {"value": {"promotion": "selected"}}}))
    before = (tree / ".ndf" / "worktree.json").read_text()
    code, _, text = run("write", "--measure", str(meas), "--answers", str(answers), "--root", str(tree))
    assert code == 1, text
    assert (tree / ".ndf" / "worktree.json").read_text() == before


# --- レビューの単位（#3163） ---------------------------------------------------------------


class MarksGh:
    """PR の番号 → (レビューの本文, コメントの本文)。番号が無ければ読めない。"""

    repo = "o/r"

    def __init__(self, marks: dict[int, tuple[list[str], list[str]]]):
        self.marks = marks

    def get(self, path: str):
        n = int(path.split("/")[4])
        if n not in self.marks:
            raise RuntimeError("404")
        reviews, comments = self.marks[n]
        return [{"body": b} for b in (reviews if "/reviews" in path else comments)]


def gh_pr(n, base, head, merged=True):
    return {"number": n, "base": {"ref": base}, "head": {"ref": head}, "merged_at": "t" if merged else None}


CR, RF, BH = "## 🤖 cross-review | round 1 | a | APPROVE", "<!-- cross-refactoring plan rf1 -->", "<!-- ndf:behavior-check head=abc1234 -->"


@pytest.mark.parametrize(
    "head, base_ref, kind",
    [
        ("feat/issue-1-s", "sprint/s", "impl"),
        ("sprint/s", "develop", "sprint"),
        ("release/v1.0.0", "main", "release"),
        (branch_flow.REVIEW_PREFIX + "1.0.0", branch_flow.REVIEW_PREFIX + "1.0.0-base", "release"),
        ("design/issue-1", "develop", "design"),
        ("spec/s", "develop", "spec"),
        ("chore/x", "develop", None),
    ],
)
def test_pr_kind_reads_the_head_and_the_target(head, base_ref, kind):
    assert mbf.pr_kind(head, base_ref, "develop", "main") == kind


def test_marks_of_tells_the_three_reviews_apart():
    gh = MarksGh({1: ([CR], [RF, BH]), 2: ([], ["no marks"])})
    assert mbf.marks_of(gh, 1) == frozenset(branch_flow.CHECKS)
    assert mbf.marks_of(gh, 2) == frozenset()


def test_measure_infers_review_units_per_pr_kind():
    """受け入れ条件 10: 種類ごとの PR の印から、推した値と確からしさを出す。標本が 3 件に満たない種類は unknown と理由。"""
    pulls, marks = [], {}
    for i in range(4):
        pulls += [gh_pr(10 + i, "sprint/s", f"feat/issue-{i}-s"), gh_pr(20 + i, "develop", f"sprint/s{i}")]
        pulls += [gh_pr(30 + i, "main", f"release/v1.{i}.0"), gh_pr(40 + i, "x", f"{branch_flow.REVIEW_PREFIX}1.{i}.0", merged=False)]
        marks.update({10 + i: ([CR], []), 20 + i: ([], [RF, BH]), 30 + i: ([], []), 40 + i: ([CR], [])})
    pulls += [gh_pr(50, "develop", "design/issue-1")]
    marks[50] = ([CR], [])
    got = mbf.collect_reviews(MarksGh(marks), pulls, "develop", "main")
    assert got["impl"]["value"] == {"checks": ["cross-review"], "diff": "pr"} and got["impl"]["confidence"] == "高"
    assert got["sprint"]["value"] == {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"}
    assert got["release"]["value"] == {"checks": ["cross-review"], "diff": "resolution"}
    assert "value" not in got["design"] and "3 件に満たない" in got["design"]["unknown"]
    assert "value" not in got["spec"]


def test_unreadable_prs_are_left_out_of_the_samples():
    pulls = [gh_pr(i, "develop", f"spec/s{i}") for i in range(4)]
    got = mbf.collect_reviews(MarksGh({0: ([], []), 1: ([], []), 2: ([], [])}), pulls, "develop", "main")
    assert got["spec"]["value"] == {"checks": [], "diff": "none"}
    assert any("読めない PR 1 件" in e["text"] for e in got["spec"]["evidence"])


def _item(reviews):
    return {"fields": {}, branch_flow.REVIEWS_KEY: {k: {"value": v, "confidence": "高", "evidence": []} for k, v in reviews.items()}}


def test_drift_lists_review_units_that_differ_from_the_declaration():
    """受け入れ条件 11: 宣言と違う種類だけ branch_flow.reviews.<種類> の行を出す。"""
    declared = branch_flow.parse_flow(
        {
            **SELECTED,
            "reviews": {"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": ["behavior"], "diff": "unchecked"}},
        }
    )
    item = _item({"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": list(branch_flow.CHECKS), "diff": "unchecked"}})
    rows = mbf.drift(declared, item)
    assert [r["name"] for r in rows] == ["branch_flow.reviews.sprint"]


def test_drift_compares_unwritten_kinds_with_both_default_columns():
    """受け入れ条件 11: 書いていない種類は既定値の 2 列のどちらかに合えば出さない。測れない種類は比べない。"""
    declared = branch_flow.parse_flow(None)
    item = _item(
        {
            "impl": {"checks": [], "diff": "none"},  # まとまりの記録なしの列
            "sprint": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},  # ありの列
            "design": {"checks": [], "diff": "none"},  # どちらとも違う
        }
    )
    item[branch_flow.REVIEWS_KEY]["spec"] = {"unknown": "標本が無い", "evidence": []}
    assert [r["name"] for r in mbf.drift(declared, item)] == ["branch_flow.reviews.design"]
