"""`supervise.py design-results` の補正の保持・設計文書のパス・一時的な失敗（#1651 の 1651-1・1651-2、#1941 の 1941-1〜1941-3）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import design_results as dr  # noqa: E402
import gh_call  # noqa: E402
from supervise_lib import design_stage, templates  # noqa: E402

DOC = "issues/issue-11-design.md"
TABLE = """# 設計

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #11 | 実装する | — | `pkg/a.py`、`pkg/b.py` |
| #12 | 取り込む | #11 | — |
"""
FIX_A = "- 補正: 先に pkg/a.py の既存テストを読む"
FIX_B = "課題どうしの重なり: #13 と pkg/b.py を共有する"


def results(text: str = TABLE, doc: str = DOC) -> dr.DesignResults:
    res = dr.DesignResults(design_prs=[40])
    res.add(dr.parse_results(text, doc))
    return res


def plan_of(n: int, prompt: str | None = None) -> dict:
    ns = argparse.Namespace(issue=[n], prompt=None, prompt_file=None, files=None)
    return {"課題": [n], "steps": [{"id": "impl", "type": "work", "prompt": prompt or templates.impl_prompt(ns)}]}


def impl_prompt_of(plan: dict) -> str:
    return next(s for s in plan["steps"] if s["id"] == "impl")["prompt"]


def with_fixes(plan: dict) -> dict:
    """conductor の補正: 規則の途中に 1 行、末尾に 1 行を足す。"""
    lines = impl_prompt_of(plan).splitlines()
    lines.insert(2, FIX_A)
    plan["steps"][0]["prompt"] = "\n".join([*lines, "", FIX_B]) + "\n"
    return plan


# ---------- 補正と設計文書のパス（1651-1・1651-2） ----------


def test_rewrite_keeps_the_fixes_in_order():
    """1651-1・I1: 補正は同じ順で残り、課題と触る範囲は設計の結果のとおり。"""
    plans = {11: with_fixes(plan_of(11)), 12: plan_of(12)}
    design_stage.rewrite(plans, results())
    prompt = impl_prompt_of(plans[11])
    lines = prompt.splitlines()
    assert lines[0].startswith("課題 #11・#12 を実装する")
    assert "触る範囲: pkg/a.py、pkg/b.py" in lines
    kept = [ln for ln in lines if ln in (FIX_A, FIX_B)]
    assert kept == [FIX_A, FIX_B] and lines[-2:] == kept  # 補正は雛形の行の後へ順のまま
    assert sum(ln.startswith("課題 ") for ln in lines) == 1


def test_rewrite_twice_is_the_same():
    """1651-1・I2: 同じ入力で 2 回流しても、補正・触る範囲・設計文書の行が重ならない。"""
    plans = {11: with_fixes(plan_of(11)), 12: plan_of(12)}
    design_stage.rewrite(plans, results())
    once = json.dumps(plans, ensure_ascii=False)
    design_stage.rewrite(plans, results())
    assert json.dumps(plans, ensure_ascii=False) == once


def test_rewrite_puts_the_design_docs():
    """1651-2: 課題の設計文書と、取り込んだ課題の設計文書のパスが入る。"""
    res = dr.DesignResults()
    res.add(dr.parse_results(TABLE.split("| #12")[0], DOC))
    res.add(dr.parse_results(TABLE.replace("| #11 | 実装する | — | `pkg/a.py`、`pkg/b.py` |\n", ""), "issues/issue-12-design.md"))
    plans = {11: plan_of(11), 12: plan_of(12)}
    design_stage.rewrite(plans, res)
    assert plans[11]["設計文書"] == [DOC, "issues/issue-12-design.md"]
    assert f"設計文書: {DOC}、issues/issue-12-design.md" in impl_prompt_of(plans[11])
    assert res.to_json()["rows"][0]["doc"] == DOC
    assert dr.DesignResults.from_json(res.to_json()).rows[1].doc == "issues/issue-12-design.md"


def test_rewrite_with_the_glossary_adds_one_line():
    plans = {11: with_fixes(plan_of(11)), 12: plan_of(12)}
    design_stage.rewrite(plans, results(), glossary=True)
    design_stage.rewrite(plans, results(), glossary=True)
    assert impl_prompt_of(plans[11]).count("glossary.py check") == 1


# ---------- 読み取りの一時的な失敗（1941-1〜1941-3） ----------


class Fake:
    """gh と git の偽物。`fails` は先頭から何回 0 以外で終わるか（コマンドの頭の語ごと）。"""

    def __init__(self, fails: dict[str, int] | None = None, pr: str = "40\n", files: str = f"{DOC}\nissues/gone.md\n"):
        self.fails = dict(fails or {})
        self.pr, self.files, self.calls = pr, files, []

    def _fail(self, key: str):
        if self.fails.get(key, 0) > 0:
            self.fails[key] -= 1
            return subprocess.CompletedProcess([], 1, "", "HTTP 504: 504 Gateway Timeout")
        return None

    def gh(self, args, stdin=None, cwd=None):
        key = " ".join(args[:2])
        self.calls.append(key)
        bad = self._fail(key)
        if bad:
            return gh_call.GhResult(bad.returncode, bad.stdout, bad.stderr)
        return gh_call.GhResult(0, self.pr if args[1] == "list" else self.files, "")

    def run(self, cmd, **kw):
        key = "git " + cmd[3]
        self.calls.append(key)
        bad = self._fail(key)
        if bad:
            return bad
        out = {"git fetch": "", "git ls-tree": f"{DOC}\0", "git show": TABLE}[key]
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def fake(monkeypatch):
    def use(**kw) -> Fake:
        f = Fake(**kw)
        monkeypatch.setattr(gh_call, "RUNNER", f.gh)
        monkeypatch.setattr(design_stage, "subprocess", types.SimpleNamespace(run=f.run))
        monkeypatch.setattr(design_stage, "SLEEP", lambda _s: None)
        return f

    return use


def sprint(tmp_path: Path) -> tuple[argparse.Namespace, dict[int, Path]]:
    paths = {n: tmp_path / f"5-impl-{n}.json" for n in (11, 12)}
    for n, p in paths.items():
        p.write_text(json.dumps(plan_of(n), ensure_ascii=False))
    manifest = tmp_path / "sprint.json"
    manifest.write_text(json.dumps({"ステージ": [{"name": "実装", "plans": [str(p) for p in paths.values()]}]}))
    return argparse.Namespace(manifest=str(manifest), base="develop", root=str(tmp_path), design=[11, 12]), paths


def test_a_transient_failure_is_retried(tmp_path, fake):
    """1941-1: 1 回目に 504 で落ちても打ち直して読めれば、失敗が無いときと同じプランを書いて ok。"""
    (tmp_path / "clean").mkdir()
    (tmp_path / "retried").mkdir()
    a, paths = sprint(tmp_path / "clean")
    fake()
    res, code = design_stage.cmd_design_results(a)
    clean = {p.name: p.read_text() for p in paths.values()}
    a, paths = sprint(tmp_path / "retried")
    f = fake(fails={"pr list": 1, "pr view": 1, "git show": 1})
    res2, code2 = design_stage.cmd_design_results(a)
    assert (code, code2) == (0, 0) and res2["status"] == "ok", res2
    assert {p.name: p.read_text() for p in paths.values()} == clean
    assert f.calls.count("pr list") == 3  # 11 は 1 度打ち直す。12 は PR が 40 と同じ文書を返す
    assert "issues/gone.md" not in json.dumps(res2)  # 起点から消えた文書は読み飛ばす（失敗にしない）


def test_a_lasting_failure_writes_nothing(tmp_path, fake):
    """1941-2・I4: 打ち直しても読めなければ、プランも design-results.json も書かずに stopped・2。"""
    a, paths = sprint(tmp_path)
    before = {p.name: p.read_text() for p in paths.values()}
    f = fake(fails={"pr list": 99})
    res, code = design_stage.cmd_design_results(a)
    assert code == 2 and res["status"] == "stopped"
    assert "#11" in res["summary"] and "504 Gateway Timeout" in res["summary"] and "#12" in res["summary"]
    assert res["metrics"] == {"failed_issue": 11, "not_read": [12]}
    assert {p.name: p.read_text() for p in paths.values()} == before
    assert not (tmp_path / "design-results.json").exists()
    assert f.calls.count("pr list") == 3  # 1 コマンド 30 秒まで（15 秒おきに 3 回）


def test_the_total_wait_is_bounded(fake):
    """決定 9: 1 回の実行の待ちの合計は 120 秒まで。使い切った後は打ち直さない。"""
    fake(fails={"pr list": 99})
    budget = design_stage.Budget()
    for _ in range(5):
        with pytest.raises(design_stage.ReadFailed):
            design_stage.merged_design_pr(".", "develop", 11, budget)
    assert budget.left >= 0 and budget.left < design_stage.RETRY_INTERVAL


def test_no_pr_and_no_table_stay_unread(tmp_path, fake):
    """1941-3: gh が 0 で空（PR が無い）なら今のまま unread で ok。"""
    a, _ = sprint(tmp_path)
    fake(pr="")
    res, code = design_stage.cmd_design_results(a)
    assert code == 0 and res["metrics"]["unread"] == [11, 12]
    assert dr.parse_results("# 設計\n\n## 決定の記録\n", DOC) is None  # 表が無い文書は読めなかった扱い


# ---------- 書き出しの取り込み（new sprint --absorb）と表の取り込みを合わせる（#1426 の決定 8） ----------


def test_rewrite_keeps_absorptions_made_when_writing_the_plans():
    """表に無い書き出しの取り込み（`取り込み先`）を保ち、表の取り込みと合わせて取り込み先の `課題` を作る。"""
    plans = {11: plan_of(11), 12: plan_of(12), 13: plan_of(13)}
    plans[13]["取り込み先"] = 11
    items = design_stage.rewrite(plans, results())
    assert plans[11]["課題"] == [11, 13, 12]
    assert plans[13]["実行の条件"] and plans[12]["実行の条件"]
    assert {"issue": 13, "absorbed_into": 11} in items
