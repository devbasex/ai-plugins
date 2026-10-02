"""#1485: スプリントの雛形が単発の雛形と同じ手順の定義でプランを組む。

進捗記録（#1299）・PR 本文の材料（#1298）・触るファイルと取り込んだ課題（#1421）・手動確認（#1398）を、
プランの形と、材料を差し替えた単体で見る。検査の記録（#1272）は test_check_trigger.py、マージ前の手動確認で
止まる merge-gate は test_sprint_close_merge_green.py にある。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
SPRINT_STATE = SCRIPTS / "sprint-state.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401
import design_results  # noqa: E402
import manual_checks  # noqa: E402
from supervise_lib import admission, design_stage, engine, pr_materials, procedures  # noqa: E402
from supervise_lib.paths import HERE  # noqa: E402

RECORD = str(HERE / "projects-sync.sh")
PACE = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "true"}


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def make_repo(tmp_path: Path, base: str = "develop") -> Path:
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": base, "production_branch": "main"}))
    (repo / ".ndf" / "supervise.json").write_text((REPO / ".ndf" / "supervise.json").read_text())
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "fast": PACE, "auto": PACE}))
    return repo


def sprint_state(tmp_path: Path, pace: str) -> Path:
    mvv = tmp_path / "mvv-src.md"
    mvv.write_text("## Mission\n速く\n## Vision\n回る\n## Value\n実測\n")
    state = tmp_path / "state" / "sprint-state.json"
    init = [PY, str(SPRINT_STATE), "init", str(state), "--name", "m", "--pace", pace, "--mvv", str(mvv), "--root", str(tmp_path)]
    subprocess.run(init, check=True, capture_output=True)
    subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "MVV", "--what", "MVV を承認"], check=True, capture_output=True)
    return state


def new_sprint(tmp_path: Path, pace: str, design: bool = True) -> dict:
    repo = make_repo(tmp_path)
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m17", "--worktree", str(repo), "--issue", "11", "12", "--version", "10.18.0-dev.1"]
    args += ["--design", "11"] if design else []
    if pace != "normal":
        args += ["--pace", pace, "--state", str(sprint_state(tmp_path, pace))]
    p = subprocess.run([PY, str(SUPERVISE), *args, "--out", str(out)], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return {w["name"]: w for w in load(out / "sprint.json")["ステージ"]}


# ---------- 共有の定義（AC1・AC2） ----------


def test_sprint_waves_does_not_rebuild_the_shared_procedures():
    """AC1・I6: 5 種の手順の文字列は procedures.py にあり、sprint_waves.py に組み直す箇所が無い。"""
    text = (SCRIPTS / "supervise_lib" / "sprint_waves.py").read_text()
    assert re.findall(r'"記録"|check-done|Closes', text) == []


def new_single(tmp_path: Path, kind: str, *extra: str) -> dict:
    repo = tmp_path / "repo" if (tmp_path / "repo").is_dir() else make_repo(tmp_path)
    out = tmp_path / f"{kind}-{len(list(tmp_path.glob('*.json')))}.json"
    p = subprocess.run([PY, str(SUPERVISE), "new", kind, *extra, "--out", str(out)], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out)


def test_single_plans_keep_their_step_order_and_gain_the_record(tmp_path):
    """AC2・I5・決定 10: 単発の雛形のステップの並びは変わらず、`記録` を持つ。"""
    wt = str(tmp_path / "wt")
    impl = new_single(tmp_path, "impl", "--issue", "5", "--worktree", wt, "--tests", "t", "--title", "T")
    merge = ["merge-gate", "merge", "merge-approved"]
    assert ids(impl) == [
        "impl",
        "sync",
        "fix-sync",
        "test-limited",
        "judge",
        "fix",
        "pr",
        "test-all",
        "doc-lint",
        "fix-doc",
        "ready",
        *merge,
    ]
    check = new_single(tmp_path, "check", "--pr", "30", "--worktree", wt)
    inspect = ["assess", "refactor", "review", "test-all", "judge", "fix", "ready"]
    assert ids(check) == [*inspect, *merge, "record", "abort"]
    since = new_single(tmp_path, "check", "--since-last", "--id", "m-1", "--worktree", wt)
    assert ids(since) == ["prepare", "pr", *inspect[:-1], "finish", "ready", *merge, "record", "abort", "abort-before-pr"]
    for plan in (impl, check, since):
        assert plan["記録"] == RECORD
    # 単発の PR を指す検査は check-done/* を進めない（旗を渡さない）
    assert "--advance-done" not in json.dumps(check, ensure_ascii=False)


# ---------- 進捗記録（AC3・AC4） ----------


@pytest.mark.parametrize("pace", ["normal", "auto", "fast"])
def test_every_sprint_plan_records_the_stages(tmp_path, pace):
    """AC3: スプリントブランチ・実装・検査（と設計の結果）のプランが `記録` を持つ。"""
    waves = new_sprint(tmp_path, pace)
    names = ["設計の結果", "実装", "検査"] + ([] if pace == "fast" else ["スプリントブランチ"])
    for name in names:
        for path in waves[name]["plans"]:
            assert load(path)["記録"] == RECORD, (name, path)


def test_a_plan_records_each_stage_once_per_issue(tmp_path):
    """AC4: `記録` を持つプランは、工程が戻っても課題ごと・工程ごとに 1 回だけ記録する。"""
    log = tmp_path / "rec.log"
    rec = tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    stages = ["実装", "完了判定", "Pull Request", "完了判定", "構造改善", "実装レビュー"]
    steps = [{"id": f"s{i}", "type": "run", "cmd": "true", "stage": st, "next": f"s{i + 1}"} for i, st in enumerate(stages)]
    steps[-1]["next"] = "end"
    plan = {"フェーズ": "試験", "課題": [5, 6], "作業場所": str(tmp_path), "記録": str(rec), "steps": steps}
    engine.Engine(plan, tmp_path / "state").run()
    got = log.read_text().splitlines()
    assert got == [f"{n} stage {s}" for s in ["実装", "完了判定", "Pull Request", "構造改善", "実装レビュー"] for n in (5, 6)]


# ---------- 設計の結果のステージ（AC13〜AC15） ----------


def test_design_results_stage_follows_gate_1_in_every_pace(tmp_path):
    for pace in ("normal", "auto", "fast"):
        sub = tmp_path / pace
        sub.mkdir()
        names = list(new_sprint(sub, pace))
        assert names.index("関門 1") + 1 == names.index("設計の結果") < names.index("実装"), (pace, names)
    assert "設計の結果" not in new_sprint(tmp_path / "none", "normal", design=False)


def test_fast_runs_the_implementation_after_the_design_results(tmp_path):
    waves = new_sprint(tmp_path, "fast")
    cmd = waves["設計の結果"]["command"]
    assert all(p in cmd.split("--then", 1)[1] for p in waves["実装"]["plans"])
    assert "command" not in waves["実装"]


DESIGN_DOC = """# 設計

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #11 | 実装する | — | `pkg/a/`、`pkg/b.py` |
| #12 | 取り込む | #11 | — |
| #13 | 閉じる | — | — |
"""


def sprint_dir(tmp_path: Path) -> Path:
    waves = new_sprint(tmp_path, "normal")
    return Path(waves["実装"]["plans"][0]).parent


def run_design_results(out: Path, res: design_results.DesignResults) -> tuple[dict, int | None]:
    a = argparse.Namespace(manifest=str(out / "sprint.json"), base="develop", root=".", design=[11])
    return design_stage.cmd_design_results(a, read=lambda *_: res)


def results_of(text: str, prs=(40,)) -> design_results.DesignResults:
    res = design_results.DesignResults(design_prs=list(prs))
    res.add(design_results.parse_results(text))
    return res


def test_design_results_write_touched_files_and_absorb(tmp_path):
    """AC13・AC14・I1・I2: 実装する課題に `触るファイル`、取り込んだ課題に `実行の条件`、取り込み先に両方の課題。"""
    out = sprint_dir(tmp_path)
    res, code = run_design_results(out, results_of(DESIGN_DOC))
    assert code == 0, res
    host, absorbed = load(next(out.glob("*-impl-11.json"))), load(next(out.glob("*-impl-12.json")))
    assert host["触るファイル"] == ["pkg/a/", "pkg/b.py"] and host["課題"] == [11, 12]
    prompt = next(s for s in host["steps"] if s["id"] == "impl")["prompt"]
    assert "#11・#12" in prompt
    assert absorbed["実行の条件"]["skip_code"] == 3 and "impl" in ids(absorbed)
    assert load(out / "design-results.json")["design_prs"] == [40]
    # 打ち直しても課題を重ねない
    run_design_results(out, results_of(DESIGN_DOC))
    assert load(next(out.glob("*-impl-11.json")))["課題"] == [11, 12]


def test_absorbed_issue_skips_in_the_queue(tmp_path):
    """AC14: 取り込んだ課題のプランは実装のステップを流さずに「完了」で終わる。"""
    cond = procedures.absorbed_condition(12, 11)
    plan = {"フェーズ": "実装", "課題": [12], "作業場所": str(tmp_path), "実行の条件": cond, "steps": []}
    s = engine.Engine(plan, tmp_path / "state")
    assert s.check_condition(cond)[0] == "完了"


def test_overlapping_touched_files_are_serialized():
    """AC13: 設計の結果の触るファイルが重なる 2 本は同じ組になり、1 本ずつ流れる（包含で見る）。"""
    grouped = admission.groups({"a": ["pkg/a/"], "b": ["pkg/a/x.py"], "c": ["docs/"]})
    assert sorted(map(sorted, grouped)) == [["a", "b"], ["c"]]


def test_unread_design_results_keep_the_plans(tmp_path):
    """AC15: 設計の結果を読めなければプランを書き換えず、読めなかった課題を summary に残す。"""
    out = sprint_dir(tmp_path)
    before = {p.name: p.read_text() for p in out.glob("*-impl-*.json")}
    res, code = run_design_results(out, design_results.DesignResults(unread=[11]))
    assert code == 0 and "#11" in res["summary"] and res["metrics"]["unread"] == [11]
    assert {p.name: p.read_text() for p in out.glob("*-impl-*.json")} == before
    assert design_results.parse_results("# 設計\n\n## 決定の記録\n") is None


def test_design_results_stop_without_a_host(tmp_path):
    """I2: 取り込み先がスプリントの課題に無ければ書き換えずに 2 で止まる。"""
    out = sprint_dir(tmp_path)
    doc = DESIGN_DOC.replace("| #12 | 取り込む | #11 |", "| #12 | 取り込む | #99 |").replace("| #11 | 実装する", "| #14 | 実装する")
    _, code = run_design_results(out, results_of(doc))
    assert code == 2 and "触るファイル" not in load(next(out.glob("*-impl-11.json")))


def test_closed_issue_plan_skips(tmp_path):
    """設計で「閉じる」とした課題のプランは `実行の条件` で飛び、実装のステップを流さない。"""
    out = sprint_dir(tmp_path)
    doc = "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n| #11 | 実装する | — | — |\n| #12 | 閉じる | — | — |\n"
    _, code = run_design_results(out, results_of(doc))
    closed = load(next(out.glob("*-impl-12.json")))
    assert code == 0 and closed["実行の条件"] == procedures.closed_condition(12)
    assert "実行の条件" not in load(next(out.glob("*-impl-11.json")))


@pytest.mark.parametrize(
    "rows",
    [
        "| #11 | 取り込む | #14 |\n| #12 | 実装する | — |\n| #14 | 実装する | — |\n",  # 取り込み先にプランが無い
        "| #11 | 閉じる | — |\n| #12 | 取り込む | #11 |\n",  # 取り込み先を閉じた
    ],
)
def test_design_results_stop_without_a_runnable_host(tmp_path, rows):
    """I2: 取り込み先に流れる実装プランが無ければ書き換えずに 2 で止まる。"""
    out = sprint_dir(tmp_path)
    before = {p.name: p.read_text() for p in out.glob("*-impl-*.json")}
    doc = "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n" + rows.replace(" |\n", " | — |\n")
    _, code = run_design_results(out, results_of(doc))
    assert code == 2 and {p.name: p.read_text() for p in out.glob("*-impl-*.json")} == before


# ---------- PR 本文の材料（AC8〜AC12） ----------


def fake_gh(monkeypatch, prs):
    def gh(args, cwd=None, **kw):
        return subprocess.CompletedProcess(args, 0, json.dumps(prs), "")

    monkeypatch.setattr(pr_materials.gh_call, "gh", gh)


def test_sprint_pr_collects_changes_closes_design_and_manual(tmp_path, monkeypatch):
    fake_gh(
        monkeypatch,
        [
            {"number": 21, "body": "## 利用者向けの変化\n\n- 進行が記録される\n- 本文に集まる\n"},
            {"number": 22, "body": "## 利用者向けの変化\n\n- 無し\n"},
            {"number": 23, "body": "まとめ\n"},
            {"number": 24, "body": None},
        ],
    )
    (tmp_path / "dr.json").write_text(json.dumps(results_of(DESIGN_DOC).to_json()))
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "issue-11-requirements.md").write_text(
        "## 検証手段\n\n| 項目 | 手段 |\n| --- | --- |\n| テスト | pytest |\n"
        "| 手動確認（マージ前） | 実機で欄を見る |\n| 手動確認 | 進行を見る |\n\n"
        "## 前提とする取り決め\n\n| 項目 | 参照先 |\n| --- | --- |\n| 手動確認の方針 | 書かない |\n"
    )
    mats = procedures.sprint_materials("sprint/m17", str(tmp_path / "dr.json"), [11, 12, 15])
    old = "## 手動確認\n\n- [x] #11 マージ前: 実機で欄を見る\n"
    got = pr_materials.gather_materials(str(tmp_path), [11, 12], mats, old)
    # AC8・AC9・I7: 変化のある PR の行だけ。変化なし・読めなかった PR は「集めた実装の PR」へ
    assert got.changes == ["進行が記録される（#21）", "本文に集まる（#21）"]
    collected = got.sections[0]
    assert "#22: 利用者向けの変化なし" in collected and "#23: 利用者向けの変化なし" in collected
    assert "#24: 本文を読めなかった" in collected and "#21: 変化あり" in collected
    # AC10・AC11: Closes を 1 行 1 件（スプリントの課題と設計の結果のすべての行）、設計 PR の番号
    closes = got.sections[1]
    assert [ln for ln in closes.splitlines() if ln.startswith("Closes")] == [f"Closes #{n}" for n in (11, 12, 13, 15)]
    assert got.design == ["- 設計: #40"]
    # AC16・AC18・決定 5: 手動確認の節に時期つきで並び、印は引き継ぐ
    manual = got.sections[2]
    assert manual.splitlines()[2:] == ["- [x] #11 マージ前: 実機で欄を見る", "- #11 リリース後テストへ回す: 進行を見る"]


def test_no_design_and_no_manual_rows(tmp_path, monkeypatch):
    """AC11: 設計の課題が無ければ「設計なし」。AC19: 手動確認の行が無ければ節を置かない。"""
    fake_gh(monkeypatch, [])
    got = pr_materials.gather_materials(str(tmp_path), [11], procedures.sprint_materials("sprint/m", str(tmp_path / "none.json"), [11]))
    assert got.design == ["- 設計: 設計なし"] and got.changes == []
    assert not any(s.startswith(manual_checks.HEADING) for s in got.sections)


def test_release_notes_read_the_collected_changes(tmp_path):
    """AC12: スプリント PR の「利用者向けの変化」の行は release-steps.py notes の箇条になる。"""
    sys.path.insert(0, str(SCRIPTS))
    import importlib.util

    spec = importlib.util.spec_from_file_location("release_steps", SCRIPTS / "release-steps.py")
    rs = importlib.util.module_from_spec(spec)
    sys.modules["release_steps"] = rs
    spec.loader.exec_module(rs)
    body = pr_materials.CHANGES_HEADING + "\n\n- 進行が記録される（#21）\n\n## 課題と設計\n"
    assert rs.change_items(rs.section_lines(body, rs.CHANGES_HEADING), 50) == ["進行が記録される（#21）（#50）"]


def test_sprint_check_plan_uses_the_shared_pr_and_record(tmp_path):
    """非機能（運用・保守性）: 検査のプランの pr は template の本文で、LLM のステップを増やさない。
    AC5: 記録は --advance-done。fast の実装の PR は手動確認を載せ、スプリントブランチへの実装の PR は載せない。"""
    waves = new_sprint(tmp_path / "n", "normal")
    check = load(waves["検査"]["plans"][0])
    pr = next(s for s in check["steps"] if s["id"] == "pr")
    assert pr["body"] == "template" and pr["materials"]["collect"] == "sprint/m17" and pr["materials"]["closes"] == [11, 12]
    assert "関連:" not in pr["summary"]
    record = next(s for s in check["steps"] if s["id"] == "record")
    assert record["cmd"].endswith("--target-pr {pr} --advance-done")
    assert ids(check)[:2] == ["collect", "pr"]
    llm = [s for s in check["steps"] if s["type"] in ("work", "judge", "drive")]
    assert [s["id"] for s in llm] == ["refactor", "review", "judge", "fix"]
    impl = load(waves["実装"]["plans"][0])
    assert "materials" not in next(s for s in impl["steps"] if s["id"] == "pr")
    fast = new_sprint(tmp_path / "f", "fast")
    fimpl = load(fast["実装"]["plans"][0])
    assert next(s for s in fimpl["steps"] if s["id"] == "pr")["materials"] == {"manual": True}
