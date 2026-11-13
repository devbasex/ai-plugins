"""#1485: スプリントの雛形が単発の雛形と同じ手順の定義でプランを組む。

進捗記録（#1299）・PR 本文の材料（#1298）・触るファイルと取り込んだ課題（#1421）・手動確認（#1398）を、
プランの形と、材料を差し替えた単体で見る。検査の記録（#1272）は test_check_trigger.py、マージ前の手動確認で
止まる merge-gate は test_sprint_close_merge_green.py にある。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
SPRINT_STATE = SCRIPTS / "sprint-state.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401
import design_results  # noqa: E402
import manual_checks  # noqa: E402
from supervise_lib import admission, engine, pr_materials  # noqa: E402
from supervise_lib.paths import HERE  # noqa: E402

RECORD = str(HERE / "projects-sync.sh")
PACE = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "true"}


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


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


# ---------- 共有の定義（AC1・AC2） ----------


def new_single(tmp_path: Path, kind: str, *extra: str) -> dict:
    repo = tmp_path / "repo" if (tmp_path / "repo").is_dir() else make_repo(tmp_path)
    out = tmp_path / f"{kind}-{len(list(tmp_path.glob('*.json')))}.json"
    p = inproc.run(SUPERVISE, "new", kind, *extra, "--out", str(out), cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out)


def test_single_plans_keep_their_step_order_and_gain_the_record(tmp_path):
    """AC2・I5・決定 10: 単発の雛形のステップの並びは変わらず、`記録` を持つ。"""
    wt = str(tmp_path / "wt")
    impl = new_single(tmp_path, "impl", "--issue", "5", "--worktree", wt, "--tests", "t", "--title", "T")
    merge = ["merge-gate", "merge", "merge-approved"]
    assert ids(impl) == [
        "impl",
        "base-sync",
        "fix-base",
        *["format", "fix-format"],  # 宣言の整形（.ndf/supervise.json の format。#1651）
        "sync",
        "fix-sync",
        "test-limited",
        "judge",
        "fix",
        *["design-tests", "design-specs", "design-match"],  # standard（既定）の設計との突き合わせ（#1241）
        "pr",
        "fix-push",
        "test-all",
        "doc-lint",
        "fix-doc",
        "ready",
        *merge,
        "base-return",
    ]
    check = new_single(tmp_path, "check", "--pr", "30", "--worktree", wt)
    behavior = ["behavior-targets", "behavior", "behavior-verify"]  # 振る舞いの確認（#2925）
    inspect = ["assess", "refactor", "review", *behavior, "base-sync", "fix-base", "test-all", "judge", "fix", "ready"]
    assert ids(check) == [*inspect, *merge, "record", "abort", "base-return"]
    since = new_single(tmp_path, "check", "--since-last", "--id", "m-1", "--worktree", wt)
    assert ids(since) == ["prepare", "pr", *inspect[:-1], "finish", "ready", *merge, "record", "abort", "abort-before-pr"]
    for plan in (impl, check, since):
        assert plan["記録"] == RECORD
    # 単発の PR を指す検査は check-done/* を進めない（旗を渡さない）
    assert "--advance-done" not in json.dumps(check, ensure_ascii=False)


# ---------- 進捗記録（AC3・AC4） ----------


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


DESIGN_DOC = """# 設計

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #11 | 実装する | — | `pkg/a/`、`pkg/b.py` |
| #12 | 取り込む | #11 | — |
| #13 | 閉じる | — | — |
"""


def results_of(text: str, prs=(40,)) -> design_results.DesignResults:
    res = design_results.DesignResults(design_prs=list(prs))
    res.add(design_results.parse_results(text))
    return res


def test_overlapping_touched_files_are_serialized():
    """AC13: 設計の結果の触るファイルに等しいファイルを持つ 2 本は同じ組になり、1 本ずつ流れる。
    ディレクトリの宣言は重なりに使わない（#1811）。"""
    grouped = admission.groups({"a": ["pkg/a/", "pkg/a/x.py"], "b": ["pkg/a/x.py"], "c": ["pkg/a/", "docs/"]})
    assert sorted(map(sorted, grouped)) == [["a", "b"], ["c"]]


# ---------- PR 本文の材料（AC8〜AC12） ----------


def fake_gh(monkeypatch, prs):
    def gh(args, cwd=None, **kw):
        return subprocess.CompletedProcess(args, 0, json.dumps(prs), "")

    monkeypatch.setattr(pr_materials.gh_call, "gh", gh)


def sprint_materials(collect: str, design_results: str, closes: list[int]) -> dict:
    """スプリント PR の材料（`pr` のステップの `materials`）。"""
    return {"manual": True, "collect": collect, "design_results": design_results, "closes": list(closes)}


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
    mats = sprint_materials("sprint/m17", str(tmp_path / "dr.json"), [11, 12, 15])
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


def test_sprint_pr_collects_the_migration_steps(tmp_path, monkeypatch):
    """#1752 の受け入れ条件 9: スプリント PR の collect は実装の PR の「移行の手順」を PR 番号つきで集める。"""
    fake_gh(
        monkeypatch,
        [
            {"number": 31, "body": "## 利用者向けの変化\n\n- 外した\n\n## 移行の手順\n\n- playwright-kit の引数を外す\n"},
            {"number": 32, "body": "## 利用者向けの変化\n\n- 足した\n\n## 移行の手順\n\n- 無し\n"},
        ],
    )
    got = pr_materials.gather_materials(str(tmp_path), [11], sprint_materials("sprint/m", str(tmp_path / "none.json"), [11]))
    assert got.migration == ["playwright-kit の引数を外す（#31）"]


def test_no_design_and_no_manual_rows(tmp_path, monkeypatch):
    """AC11: 設計の課題が無ければ「設計なし」。AC19: 手動確認の行が無ければ節を置かない。"""
    fake_gh(monkeypatch, [])
    got = pr_materials.gather_materials(str(tmp_path), [11], sprint_materials("sprint/m", str(tmp_path / "none.json"), [11]))
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
    assert rs.included.gh_sections.section_items(body, rs.CHANGES_HEADING, 50) == ["進行が記録される（#21）（#50）"]
