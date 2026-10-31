"""release-steps.py notes --approval が承認の材料（レビュー・リファクタリング・全体テスト・設計文書の未確認・
未実施のリリース後テスト）を承認資料へ集める（#1488）。

範囲の PR の世界は `test_release_range.py` を使う。検査のプランの状態ディレクトリは実物の出力の形（結果 JSON が
最後の行）で置き、課題の本文と設計文書は gh の代役とベースブランチのコミットで置く。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_release_range as rr  # noqa: E402

ISSUE = "## 受け入れ条件\n\n- [ ] 条件 A\n- [ ] 条件 B\n- [x] 条件 C（印あり）\n- [ ] 条件 D\n"
DESIGN = (
    "# d\n\n## 未確認のまま残ること\n\n| 項目 | 内容 |\n| --- | --- |\n"
    "| 置き場 | 開発版で決まっていない |\n| 所要 | 範囲が多い版で増える |\n"
)
VERIFY = (
    "## リリース後テスト\n\n対象の版: 1.0.1-dev.1\n\n| 課題 | 受け入れ条件 | 結果 |\n| --- | --- | --- |\n"
    "| #7 | 1. 条件 A | 合格 |\n\n合否: 保留\n"
)
REVIEW = {
    "tool": "cross-review-drive",
    "status": "ok",
    "summary": "s",
    "items": [{"report": "r"}, {"kind": "deferred", "path": "lib/x.py", "line": 191, "summary": "照合を足す", "round": 1}],
    "metrics": {"rounds": 2, "deferred": 1},
}
REFACTOR = {"tool": "cross-refactoring-drive", "status": "ok", "summary": "s", "items": [], "metrics": {"adopted": 1, "reverted": 0}}
WHOLE = {
    "tool": "test-run",
    "status": "ok",
    "summary": "s",
    "items": [{"strategy": "local-full"}, {"failed_tests": ["t::a"], "caused": []}, {"ran": ["pytest . -q"], "on_ci": False}],
    "metrics": {"caused": 0},
}


def put_out(d: Path, name: str, obj: dict | None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("前の出力\n" + (json.dumps(obj, ensure_ascii=False) + "\n✅ 終わり\n" if obj else ""), encoding="utf-8")


def check_state(tmp_path: Path, review=REVIEW, refactor=REFACTOR, whole=WHOLE) -> Path:
    d = tmp_path / "6-check-state"
    put_out(d, "04-refactor.out", refactor)
    put_out(d, "05-review.out", review)
    put_out(d, "06-test-all.out", whole)
    return d


def world_with_design(tmp_path: Path) -> dict:
    """範囲の PR の世界で、#30 が課題 #7 を閉じ、ベースブランチに #7 の設計文書がある。"""
    w = rr.make_world(tmp_path)
    seed = w["seed"]
    (seed / "issues").mkdir(exist_ok=True)
    (seed / "issues" / "issue-7-design.md").write_text(DESIGN, encoding="utf-8")
    rr.git(seed, "add", "-A")
    rr.git(seed, "commit", "-q", "-m", "設計")
    rr.git(seed, "push", "-q", "origin", "HEAD:trunk")
    rr.git(w["work"], "fetch", "-q", "origin")
    return w


def load(monkeypatch, w, issue=ISSUE, said=(), risks30=""):
    bodies = {30: rr.BODIES[30] + "\nCloses #7\n" + risks30}
    mod = rr.load(monkeypatch, w, bodies)
    real = mod.included.pr_fields

    def pr_fields(ns, cwd=None, comments=False):
        got, err = real(ns, cwd, comments)
        if comments and 30 in got:
            got[30]["comments"] = [{"body": c} for c in said]
        return got, err

    asked = []

    def issue_body(root, n):
        asked.append(n)
        return issue

    monkeypatch.setattr(mod.included, "pr_fields", pr_fields)
    monkeypatch.setattr(mod, "issue_body", issue_body)
    mod.issues_asked = asked
    return mod


def run(mod, w, state, version=rr.VER):
    path = rr.facts(mod, w, [30])[0]["presentation_path"]
    out, _ = rr.notes(mod, w, [30], approval=path, version=version, check_state=str(state) if state is not None else None)
    return Path(path), out


def section(text: str, heading: str) -> list[str]:
    lines = text.split("\n")
    at = lines.index(heading)
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [x for x in lines[at + 1 : end] if x.strip()]


def test_all_materials_land_in_the_approval(monkeypatch, tmp_path):
    """1488-1〜1488-5・1488-7: 検査の 3 つ・設計の未確認 2 行・未実施の条件（合格の 1 と印の 3 を除く 2 つ）が並ぶ。"""
    w = world_with_design(tmp_path)
    mod = load(monkeypatch, w, said=[VERIFY])
    path, out = run(mod, w, check_state(tmp_path), "1.0.1-dev.1")
    text = path.read_text(encoding="utf-8")
    check = section(text, "## 検査の結果")
    assert check[0].startswith("- レビュー:") and "2 ラウンド" in check[0] and "1 件" in check[0]
    assert check[1].startswith("  - `lib/x.py:191`")
    assert check[2].startswith("- リファクタリング:") and "採用 1 件" in check[2] and "取り消し 0 件" in check[2]
    assert check[3].startswith("- 全体テスト: `pytest . -q` 合格") and "1 件" in check[3]
    risks = section(text, "## 未検証・残る危険")
    assert [r for r in risks if r.startswith("- 設計の未確認 #7")] == [
        "- 設計の未確認 #7: 置き場: 開発版で決まっていない",
        "- 設計の未確認 #7: 所要: 範囲が多い版で増える",
    ]
    assert [r.split(":")[0] for r in risks if r.startswith("- 未実施のリリース後テスト")] == [
        "- 未実施のリリース後テスト #7 の条件 2",
        "- 未実施のリリース後テスト #7 の条件 4",
    ]
    assert "- 実装 1 の危険（#31）" in risks and mod.issues_asked == [7]
    assert {i["name"]: i["result"] for i in out["items"] if i["kind"] == "material"} == {n: "written" for n in mod.materials.NAMES.values()}


def test_without_a_table_every_criterion_is_unverified(monkeypatch, tmp_path):
    """1488-5: リリース後テストの表が無ければ、印の無い条件がすべて並ぶ。"""
    w = world_with_design(tmp_path)
    path, _ = run(load(monkeypatch, w), w, check_state(tmp_path))
    risks = section(path.read_text(encoding="utf-8"), "## 未検証・残る危険")
    assert len([r for r in risks if r.startswith("- 未実施のリリース後テスト #7")]) == 3


def test_nothing_at_all_says_so_with_every_name(monkeypatch, tmp_path):
    """1488-6・I7: 材料も PR の危険も無いと「記載が無い」の 1 行に 6 つの名前が並び、古い「PR の本文に記載が無い」は出ない。"""
    w = rr.make_world(tmp_path)
    mod = rr.load(monkeypatch, w, {21: "## 利用者向けの変化\n\n- x\n", 31: "## 利用者向けの変化\n\n- y\n"})
    monkeypatch.setattr(mod, "issue_body", lambda root, n: "")
    path = rr.facts(mod, w, [30])[0]["presentation_path"]
    rr.notes(mod, w, [30], approval=path)
    risks = section(Path(path).read_text(encoding="utf-8"), "## 未検証・残る危険")
    assert len(risks) == 1 and risks[0].startswith("- 記載が無い")
    names = [*mod.materials.NAMES.values(), mod.materials.PR_RISKS]
    assert all(n in risks[0] for n in names) and "PR の本文に記載が無い" not in risks[0]


def test_some_missing_lists_the_missing_after_the_present(monkeypatch, tmp_path):
    """1488-6・I10: --check-state のディレクトリに結果 JSON が無いと止まらず、ある材料の行の後に無い 3 つの名前を 1 行で書く。"""
    w = world_with_design(tmp_path)
    (tmp_path / "空").mkdir()
    path, out = run(load(monkeypatch, w), w, tmp_path / "空")
    assert section(path.read_text(encoding="utf-8"), "## 検査の結果")[0].startswith("- 無し")
    risks = section(path.read_text(encoding="utf-8"), "## 未検証・残る危険")
    assert risks[-1].startswith("- 無い材料:") and all(n in risks[-1] for n in ("レビュー", "リファクタリング", "全体テスト"))
    assert "設計文書の未確認" not in risks[-1] and any(r.startswith("- 設計の未確認 #7") for r in risks)
    got = {i["name"]: i["result"] for i in out["items"] if i["kind"] == "material"}
    assert got["レビュー"] == "missing" and got["設計文書の未確認"] == "written"


def test_rewriting_replaces_machine_lines_and_keeps_hand_lines(monkeypatch, tmp_path):
    """1488-8・I8: 2 回目は 2 回目の材料だけが残り、手で足した危険の行は残る。"""
    w = world_with_design(tmp_path)
    mod = load(monkeypatch, w)
    state = check_state(tmp_path)
    path, _ = run(mod, w, state)
    text = path.read_text(encoding="utf-8").replace("## 未検証・残る危険\n\n", "## 未検証・残る危険\n\n- 手で書いた危険\n")
    path.write_text(text, encoding="utf-8")
    put_out(state, "07-review.out", {**REVIEW, "items": [{"report": "r"}], "metrics": {"rounds": 3}})  # 再開で増えた出力
    monkeypatch.setattr(mod, "issue_body", lambda root, n: "## 受け入れ条件\n\n- [x] 済み\n")
    rr.notes(mod, w, [30], approval=str(path), check_state=str(state))
    text = path.read_text(encoding="utf-8")
    risks = section(text, "## 未検証・残る危険")
    assert "- 手で書いた危険" in risks and not [r for r in risks if r.startswith("- 未実施のリリース後テスト")]
    assert len([r for r in risks if r.startswith("- 設計の未確認")]) == 2
    assert "3 ラウンド" in section(text, "## 検査の結果")[0] and text.count("## 検査の結果") == 1


@pytest.mark.parametrize("state", ["", "無い"])
def test_check_state_without_the_directory_stops(monkeypatch, tmp_path, state):
    """#2079: 置き場が空文字・無いディレクトリなら「無し」と書かずに 3 で止まる。"""
    w = world_with_design(tmp_path)
    mod = load(monkeypatch, w)
    path = rr.facts(mod, w, [30])[0]["presentation_path"]
    before = Path(path).read_text(encoding="utf-8")
    with pytest.raises(mod.StepError) as e:
        rr.notes(mod, w, [30], approval=path, version=rr.VER, check_state=str(tmp_path / state) if state else "")
    assert e.value.code == 3 and Path(path).read_text(encoding="utf-8") == before


def test_check_state_needs_approval(monkeypatch, tmp_path):
    w = rr.make_world(tmp_path)
    mod = rr.load(monkeypatch, w)
    with pytest.raises(mod.StepError) as e:
        rr.notes(mod, w, [30], check_state=str(tmp_path))
    assert e.value.code == 2


def test_unreadable_issue_stops(monkeypatch, tmp_path):
    """共通-2・I10: 閉じる課題を gh で読めなければ、材料を空にせず止まる。"""
    w = world_with_design(tmp_path)
    mod = load(monkeypatch, w)

    def boom(root, n):
        raise mod.StepError("gh issue view 7 が失敗: x")

    monkeypatch.setattr(mod, "issue_body", boom)
    path = rr.facts(mod, w, [30])[0]["presentation_path"]
    with pytest.raises(mod.StepError):
        rr.notes(mod, w, [30], approval=path, check_state=str(check_state(tmp_path)))


def test_an_issue_in_another_repository_keeps_its_repository(monkeypatch, tmp_path):
    """別のリポジトリの課題（`Closes other/repo#7`）を今のリポジトリの #7 として読まず、本文と合否もその課題で照合する。"""
    w = world_with_design(tmp_path)
    mod = load(monkeypatch, w, said=[VERIFY], risks30="\nCloses other/repo#7\n")
    asked = []

    def issue_body(root, n, repo=None):
        asked.append((repo, n))
        return ISSUE

    monkeypatch.setattr(mod, "issue_body", issue_body)
    path, _ = run(mod, w, check_state(tmp_path), "1.0.1-dev.1")
    risks = section(path.read_text(encoding="utf-8"), "## 未検証・残る危険")
    assert sorted(asked, key=str) == sorted([(None, 7), ("other/repo", 7)], key=str)
    assert [r.split(":")[0] for r in risks if r.startswith("- 未実施のリリース後テスト")] == [
        "- 未実施のリリース後テスト #7 の条件 2",
        "- 未実施のリリース後テスト #7 の条件 4",
        "- 未実施のリリース後テスト other/repo#7 の条件 1",
        "- 未実施のリリース後テスト other/repo#7 の条件 2",
        "- 未実施のリリース後テスト other/repo#7 の条件 4",
    ]
    assert len([r for r in risks if r.startswith("- 設計の未確認")]) == 2


def test_whole_test_without_ran_reads_the_declared_command(tmp_path):
    """1488-3: `ran` の無い古い出力は宣言の suite のコマンドを書く。宣言が CI に任せるなら「CI に任せた」。"""
    sys.path.insert(0, str(Path(rr.SCRIPT).parent / "lib"))
    sys.path.insert(0, str(Path(rr.SCRIPT).parent))
    from release_lib import materials

    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": [{"name": "py", "command": "pytest ."}]}}))
    state = check_state(tmp_path, whole={**WHOLE, "items": WHOLE["items"][:2]})
    whole = materials.read_check(state, tmp_path)[2]
    assert whole.commands == ("pytest .",) and whole.passed and whole.failed == 1
    on_ci = materials.WholeTest((), True, True, 0, 0)
    assert "CI に任せた" in materials.check_lines(materials.Materials(whole_test=on_ci))[0]


def _materials():
    sys.path.insert(0, str(Path(rr.SCRIPT).parent / "lib"))
    sys.path.insert(0, str(Path(rr.SCRIPT).parent))
    from release_lib import materials

    return materials


def _check(tmp_path, **kw) -> list[str]:
    m = _materials()
    review, refactor, whole = m.read_check(check_state(tmp_path, **kw), tmp_path)
    return m.check_lines(m.Materials(review, refactor, whole))


FAILED = {
    "tool": "cross-refactoring-drive",
    "status": "error",
    "summary": "打ち切り",
    "items": [],
    "metrics": {"adopted": 0, "reverted": 0},
}


def test_refactor_reads_the_ok_attempt_when_a_later_attempt_failed(tmp_path):
    """2129-1: 番号の最大の試行が落ち、前の試行が ok なら、ok の試行の件数を書く。"""
    d = tmp_path / "6-check-state"
    put_out(d, "01-refactor.out", {**REFACTOR, "metrics": {"adopted": 14, "reverted": 0}})
    lines = _check(tmp_path, refactor=FAILED)  # 04-refactor.out が落ちた試行
    assert "- リファクタリング: 採用 14 件 / 取り消し 0 件" in lines


def test_refactor_without_an_ok_attempt_says_it_did_not_finish(tmp_path):
    """2129-2: ok の試行が 1 つも無ければ、落ちた試行の件数を載せず、ok で終わっていないと書く。"""
    put_out(tmp_path / "6-check-state", "01-refactor.out", FAILED)
    line = next(x for x in _check(tmp_path, refactor=FAILED) if x.startswith("- リファクタリング:"))
    assert "採用" not in line and "`ok` で終わっていない" in line and "`error`" in line


def test_refactor_with_one_ok_attempt_keeps_its_counts(tmp_path):
    """2129-3: 試行が 1 回で ok なら今と同じ値。"""
    assert "- リファクタリング: 採用 1 件 / 取り消し 0 件" in _check(tmp_path)


def test_deferred_findings_fold_by_path_and_summary(tmp_path):
    """2129-4・2129-5: path と summary が同じ見送りは line の有無・値が違っても 1 件。違うものは別の件。"""
    same = [{"kind": "deferred", "path": "a.py", "line": n, "summary": "同じ指摘", "round": r} for r, n in enumerate((None, 3, 9))]
    other = [
        {"kind": "deferred", "path": "b.py", "summary": "同じ指摘"},
        {"kind": "deferred", "path": "a.py", "line": 3, "summary": "別の指摘"},
    ]
    lines = _check(tmp_path, review={**REVIEW, "items": same + other})
    assert lines[0].endswith("見送った指摘 3 件")
    assert lines[1:4] == ["  - `a.py:3` 同じ指摘", "  - `b.py` 同じ指摘", "  - `a.py:3` 別の指摘"]
