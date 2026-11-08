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

    def __init__(
        self, fails: dict[str, int] | None = None, pr: str = "40\n", files: str = f"{DOC}\nissues/gone.md\n", shows: list[str] | None = None
    ):
        self.fails = dict(fails or {})
        self.pr, self.files, self.calls = pr, files, []
        self.shows = list(shows or [])  # git show が順に返す文書（尽きたら TABLE）

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
        show = self.shows.pop(0) if key == "git show" and self.shows else TABLE
        out = {"git fetch": "", "git ls-tree": f"{DOC}\0", "git show": show}[key]
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


@pytest.mark.parametrize("row", ["| #11 | 取り込む | #12 | — |", "| #11 | 閉じる | — | — |"], ids=["host-absorbed", "host-closed"])
def test_rewrite_stops_when_the_absorb_host_does_not_implement(row):
    """--absorb 13:11 の取り込み先 #11 を設計の結果が別へ取り込む・閉じると、#13 を実装するプランが無くなる（#2247）。
    書き換える前に止まる。"""
    table = f"# 設計\n\n## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n{row}\n| #12 | 実装する | — | — |\n"
    plans = {11: plan_of(11), 12: plan_of(12), 13: plan_of(13)}
    plans[13]["取り込み先"] = 11
    before = json.dumps(plans, ensure_ascii=False)
    with pytest.raises(dr.DesignResultsError, match="#13 の取り込み先 #11"):
        design_stage.rewrite(plans, results(table))
    assert json.dumps(plans, ensure_ascii=False) == before


# ---------- 設計の結果の食い違いのメッセージ（#2361） ----------

HEAD_FMT = "#{n} の設計の結果が 2 本の設計文書で違う（先: {a} / 後: {b} ）"


def mismatch(old: dr.Row, new: dr.Row) -> str:
    res = dr.DesignResults(rows=[old])
    with pytest.raises(dr.DesignResultsError) as e:
        res.add([new])
    assert res.rows == [old]  # I1: 行を足さない
    return str(e.value)


def test_mismatch_of_kind_names_every_field_in_order():
    """受け入れ条件 1・4・5、I3・I4: 3 つとも違えば扱い → 取り込み先 → 触るファイルの順で先 / 後。空のパスは記録なしと出す。"""
    msg = mismatch(dr.Row(12, dr.IMPLEMENT, None, ["pkg/a.py"], DOC), dr.Row(12, dr.ABSORB, 11))
    assert msg == (
        HEAD_FMT.format(n=12, a=DOC, b="パスの記録なし")
        + "。扱い: 実装する / 取り込む。取り込み先: — / #11。触るファイル: 1 個 / 0 個（先にだけある: pkg/a.py ）"
    )


def test_mismatch_of_host_only():
    """受け入れ条件 2・4: 取り込み先だけが違えば、その項目だけが載り、扱いは載らない。"""
    msg = mismatch(dr.Row(13, dr.ABSORB, 11, doc=DOC), dr.Row(13, dr.ABSORB, 12, doc="issues/issue-12-design.md"))
    assert msg == HEAD_FMT.format(n=13, a=DOC, b="issues/issue-12-design.md") + "。取り込み先: #11 / #12"


def test_mismatch_of_files_shows_counts_and_the_difference():
    """受け入れ条件 3・4: 実例の 5 個と 18 個。個数と、各設計文書にしか無いファイルを書かれた順で出す。"""
    common = [f"pkg/c{i}.py" for i in range(4)]
    old_files = [*common, "pkg/old.py"]
    new_files = [f"pkg/n{i}.py" for i in range(14)] + common[::-1]
    a, b = "issues/issue-2335-design.md", "issues/issue-2336-design.md"
    msg = mismatch(dr.Row(2336, dr.IMPLEMENT, None, old_files, a), dr.Row(2336, dr.IMPLEMENT, None, new_files, b))
    news = ", ".join(f"pkg/n{i}.py" for i in range(14))
    assert msg == HEAD_FMT.format(n=2336, a=a, b=b) + f"。触るファイル: 5 個 / 18 個（先にだけある: pkg/old.py / 後にだけある: {news} ）"
    assert "扱い:" not in msg


def test_mismatch_counts_duplicated_files():
    """決定 2: 片方で 2 回書いたファイルは、多く書いた側にだけあるとして出す。"""
    msg = mismatch(dr.Row(11, dr.IMPLEMENT, None, ["pkg/a.py"], DOC), dr.Row(11, dr.IMPLEMENT, None, ["pkg/a.py", "pkg/a.py"], DOC))
    assert msg.endswith("。触るファイル: 1 個 / 2 個（後にだけある: pkg/a.py ）")


def test_same_results_in_another_order_do_not_stop():
    """受け入れ条件 6・I2: 触るファイルの順序だけが違う・すべて同じなら止まらず、行は 1 つのまま。"""
    res = dr.DesignResults(rows=[dr.Row(11, dr.IMPLEMENT, None, ["a", "b"], DOC)])
    res.add([dr.Row(11, dr.IMPLEMENT, None, ["b", "a"], "issues/issue-12-design.md")])
    res.add([dr.Row(11, dr.IMPLEMENT, None, ["a", "b"], DOC)])
    assert len(res.rows) == 1


def test_mismatch_stops_the_stage_and_writes_nothing(tmp_path, fake):
    """受け入れ条件 7・I5: 食い違いなら stopped・2、要約の前後は変えず、プランも design-results.json も書かない。"""
    a, paths = sprint(tmp_path)
    before = {p.name: p.read_text() for p in paths.values()}
    fake(shows=[TABLE, TABLE.replace("`pkg/a.py`、`pkg/b.py`", "`pkg/a.py`")])
    res, code = design_stage.cmd_design_results(a)
    assert code == 2 and res["status"] == "stopped", res
    assert res["summary"].startswith("設計の結果を書けない: #11 の設計の結果が 2 本の設計文書で違う")
    assert res["summary"].endswith("触るファイル: 2 個 / 1 個（先にだけある: pkg/b.py ）。人へ戻す")
    assert {p.name: p.read_text() for p in paths.values()} == before
    assert not (tmp_path / "design-results.json").exists()


# ---------- 分割した設計文書と触るファイルのセルの形（#2453） ----------


def table_of(*rows: str) -> str:
    return "# 設計\n\n## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n" + "\n".join(rows) + "\n"


D1, LOOP, FAIL = "issues/issue-1-design.md", "issues/issue-1-design-loop.md", "issues/issue-1-design-fail.md"
SPLIT = "# 分割した文書\n\n## ループの側\n\n本文\n"
ROW1 = "| #1 | 実装する | — | `pkg/a.py` |"


class Repo:
    """ベースブランチ（`base`: パス → 中身）と、課題ごとのマージした設計 PR が変えたパス（`changed`）を持つ gh と git の偽物。"""

    def __init__(self, base: dict[str, str], changed: dict[int, list[str]]):
        self.base, self.changed = base, changed

    def gh(self, args, stdin=None, cwd=None):
        if args[1] == "list":
            n = int(args[args.index("--head") + 1].split("-")[-1])
            return gh_call.GhResult(0, f"{100 + n}\n" if n in self.changed else "", "")
        return gh_call.GhResult(0, "\n".join(self.changed[int(args[2]) - 100]) + "\n", "")

    def run(self, cmd, **kw):
        sub = cmd[3]
        if sub == "ls-tree":
            out = "".join(f"{p}\0" for p in self.base)
        elif sub == "show":
            out = self.base[cmd[4].split(":", 1)[1]]
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def repo(monkeypatch):
    def use(base: dict[str, str], changed: dict[int, list[str]]) -> Repo:
        r = Repo(base, changed)
        monkeypatch.setattr(gh_call, "RUNNER", r.gh)
        monkeypatch.setattr(design_stage, "subprocess", types.SimpleNamespace(run=r.run))
        return r

    return use


def stage(tmp_path: Path, issues=(1,)) -> tuple[argparse.Namespace, dict[int, Path]]:
    paths = {n: tmp_path / f"5-impl-{n}.json" for n in issues}
    for n, p in paths.items():
        p.write_text(json.dumps(plan_of(n), ensure_ascii=False))
    manifest = tmp_path / "sprint.json"
    manifest.write_text(json.dumps({"ステージ": [{"name": "実装", "plans": [str(p) for p in paths.values()]}]}))
    return argparse.Namespace(manifest=str(manifest), base="develop", root=str(tmp_path), design=list(issues)), paths


def written(paths: dict[int, Path]) -> dict[int, dict]:
    return {n: json.loads(p.read_text()) for n, p in paths.items()}


def split_base(**extra: str) -> dict[str, str]:
    base = {D1: table_of(ROW1), LOOP: SPLIT, FAIL: SPLIT, "issues/issue-1-requirements.md": "# 要求\n", "issues/issue-99-design.md": SPLIT}
    base.update(extra)
    return base


@pytest.mark.parametrize("changed", [[D1, LOOP, FAIL], [D1, "issues/issue-1-design-gone.md"]], ids=["all-in-pr", "head-only-fix"])
def test_every_design_doc_of_the_issue_is_listed(tmp_path, repo, changed):
    """2453 受け入れ条件 1〜4・6・I1・I2: プランの `設計文書` と指示文の行に 3 本が載り、表を持つ文書が先頭で残りは昇順。
    要求のコピー・番号を共有しない文書・ベースブランチから消えた文書は載らない。"""
    a, paths = stage(tmp_path)
    repo(split_base(), {1: changed})
    res, code = design_stage.cmd_design_results(a)
    assert code == 0, res
    docs = [D1, FAIL, LOOP]
    plan = written(paths)[1]
    assert plan["設計文書"] == docs
    lines = [ln for ln in impl_prompt_of(plan).splitlines() if ln.startswith("設計文書:")]
    assert lines == [templates.DESIGN_DOCS_LINE.format(docs="、".join(docs))]
    assert json.loads((tmp_path / "design-results.json").read_text())["base_docs"] == sorted(split_base())[:3] + [
        "issues/issue-99-design.md"
    ]


def test_the_head_is_the_doc_with_the_table():
    """2453 受け入れ条件 5: 表を分割した文書が持てば、その文書が先頭で、表の無い先頭の名前の文書も載る。"""
    ndf = "issues/issue-1-design-ndf.md"
    res = dr.DesignResults(base_docs=[D1, ndf])
    res.add(dr.parse_results(table_of(ROW1), ndf))
    assert res.docs_of([1]) == [ndf, D1]


def test_rows_of_two_issues_share_the_list():
    """2453 受け入れ条件 7: 1 つの表の #1・#2 は同じ一覧になり、#2 を #1 へ取り込むと #1 のプランに両方の課題の文書が載る。"""
    d2 = "issues/issue-2-design.md"
    res = dr.DesignResults(base_docs=[D1, LOOP, d2, "issues/issue-3-design.md"])
    res.add(dr.parse_results(table_of(ROW1, "| #2 | 実装する | — | `pkg/b.py` |"), D1))
    assert res.docs_of([1]) == res.docs_of([2]) == [D1, LOOP, d2]
    absorbed = dr.DesignResults(base_docs=[D1, LOOP, d2])
    absorbed.add(dr.parse_results(table_of(ROW1), D1))
    absorbed.add(dr.parse_results(table_of("| #2 | 取り込む | #1 | — |"), d2))
    plans = {1: plan_of(1), 2: plan_of(2)}
    design_stage.rewrite(plans, absorbed)
    assert plans[1]["設計文書"] == [D1, d2, LOOP]


@pytest.mark.parametrize("changed", [[LOOP], ["plugins/x.py"]], ids=["split-only-fix", "no-issue-docs"])
def test_the_table_is_found_in_the_base_docs(tmp_path, repo, changed):
    """2453 I6（決定 8）: PR が分割した文書だけを変えた・`issues/` を変えなかった課題も、ベースブランチの文書から表を読む。"""
    a, paths = stage(tmp_path)
    repo(split_base(), {1: changed})
    res, code = design_stage.cmd_design_results(a)
    assert code == 0 and res["metrics"]["unread"] == [], res
    assert written(paths)[1]["設計文書"] == [D1, FAIL, LOOP]
    assert written(paths)[1]["触るファイル"][0] == "pkg/a.py"


def test_no_table_anywhere_stays_unread_and_single_docs_stay_single(tmp_path, repo):
    """2453 受け入れ条件 8・9: 表がどこにも無い課題は unread で `設計文書` を書かない。分割していない課題は 1 本で、
    2 回打っても変わらず、補正の行は残り、「設計文書:」の行は 1 行である。"""
    a, paths = stage(tmp_path, (1, 2))
    plan1 = with_fixes(plan_of(1))
    paths[1].write_text(json.dumps(plan1, ensure_ascii=False))
    repo({D1: table_of(ROW1), "issues/issue-2-design.md": SPLIT}, {1: [D1], 2: ["issues/issue-2-design.md"]})
    res, _ = design_stage.cmd_design_results(a)
    assert res["metrics"]["unread"] == [2]
    once = written(paths)
    assert once[1]["設計文書"] == [D1] and "設計文書" not in once[2]
    design_stage.cmd_design_results(a)
    assert written(paths) == once
    prompt = impl_prompt_of(once[1])
    assert prompt.count("設計文書:") == 1 and FIX_A in prompt and FIX_B in prompt


CELL_2436 = (
    "`plugins/ndf/scripts/parallel.py`、`plugins/ndf/scripts/lib/parallel_plan.py`、`plugins/ndf/scripts/lib/parallel_run.py`、"
    "`plugins/ndf/scripts/lib/parallel_release.py`、`plugins/ndf/scripts/lib/parallel_lane.py`、`plugins/ndf/scripts/lib/durable.py`、"
    "`plugins/ndf/scripts/relay_lib/hub.py`、`plugins/ndf/scripts/relay_lib/hub_client.py`、`plugins/ndf/scripts/relay_lib/__init__.py`、"
    "`plugins/ndf/scripts/supervise.py`、`plugins/ndf/scripts/supervise_lib/sprint.py`、`plugins/ndf/scripts/supervise_lib/sprint_waves.py`、"
    "`plugins/ndf/scripts/supervise_lib/release_templates.py`、`plugins/ndf/scripts/supervise_lib/queue_fill.py`、"
    "`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/tests/`、`plugins/ndf/contracts/forms/parallel-request.json`、"
    "`plugins/ndf/skills/issue-plan-strategy/`、`plugins/ndf/skills/development-workflow/`、`docs/glossary/glossary.json`、`docs/glossary.md`。"
    "ループの側の触るファイルは [issue-2436-design-lane-loop.md](issue-2436-design-lane-loop.md)、"
    "異常系の側は [issue-2436-design-failures.md](issue-2436-design-failures.md)、"
    "承認ゲート 1 の側は [issue-2436-design-approvals.md](issue-2436-design-approvals.md)、"
    "リリースの規則の側は [issue-2436-design-release-rule.md](issue-2436-design-release-rule.md) の「触るファイル」にある"
)


@pytest.mark.parametrize(
    "cell",
    [
        "`a.py`。ループの側は [issue-1-design-loop.md](issue-1-design-loop.md) にある",
        "`a.py`（[issue-1-design-loop.md](issue-1-design-loop.md)）",
        "`a.py`。残りは別の文書にある",
        "`a.py`, b.py",
        "`a.py`（新設",
        CELL_2436,
    ],
    ids=["link", "link-in-parens", "words", "bare-path", "unclosed", "issue-2436"],
)
def test_cells_out_of_the_form_stop(cell):
    """2453 受け入れ条件 10・11・14: リンク（括弧の中を含む）と括弧の外の語は形の誤りで、表の最後まで読んでから止まる。"""
    with pytest.raises(dr.DesignCellError) as e:
        dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |", f"| #2 | 実装する | — | {cell} |"), D1)
    assert [(p.issue, p.doc) for p in e.value.problems] == [(1, D1), (2, D1)]


@pytest.mark.parametrize(
    ("cell", "files"),
    [
        ("`a.py`（新設）、`b/`", ["a.py", "b/"]),
        ("`a.py`, `b.py`", ["a.py", "b.py"]),
        ("`app/(g)/p.tsx`（`c.py` は読まない）", ["app/(g)/p.tsx"]),
        ("—", []),
        ("", []),
    ],
    ids=["note", "comma", "parens-in-path", "dash", "empty"],
)
def test_cells_in_the_form_give_only_the_code_spans(cell, files):
    """2453 受け入れ条件 15・I4: 区切り・括弧の注記・空・— は止まらず、`files` は括弧の外のコードスパンの中身だけ。"""
    assert dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)[0].files == files


def test_cells_of_absorbed_and_closed_rows_are_not_checked():
    """2453 受け入れ条件 16・I5: 「取り込む」「閉じる」の行のセルは読まない。"""
    rows = dr.parse_results(table_of(ROW1, "| #2 | 取り込む | #1 | 残りは [x](x.md) |", "| #3 | 閉じる | — | 語 |"), D1)
    assert [r.files for r in rows] == [["pkg/a.py"], [], []]


def test_cell_errors_stop_the_stage_and_a_fixed_doc_passes(tmp_path, repo):
    """2453 受け入れ条件 12・13・I3: 2 本の文書の誤りを 1 つの要約に並べ、何も書かずに止まる。直した文書の後は通る。"""
    a, paths = stage(tmp_path, (1, 2))
    before = {p.name: p.read_text() for p in paths.values()}
    d2 = "issues/issue-2-design.md"
    bad = {
        D1: table_of("| #1 | 実装する | — | `a.py`。残りは [l](issue-1-design-loop.md) |"),
        d2: table_of("| #2 | 実装する | — | `b.py`, c.py |"),
    }
    r = repo(bad, {1: [D1], 2: [d2]})
    res, code = design_stage.cmd_design_results(a)
    assert code == 2 and res["status"] == "stopped", res
    summary = res["summary"]
    assert f"#1（{D1}）の触るファイルのセルを読めない: " in summary and "issue-1-design-loop.md" in summary
    assert f"#2（{d2}）の触るファイルのセルを読めない: c.py" in summary
    assert summary.count(dr.CELL_FIX) == 1 and summary.endswith(dr.CELL_FIX + "。人へ戻す")
    assert {p.name: p.read_text() for p in paths.values()} == before
    assert not (tmp_path / "design-results.json").exists()
    r.base = {D1: table_of(ROW1), d2: table_of("| #2 | 実装する | — | `b.py`、`c.py` |")}
    res, code = design_stage.cmd_design_results(a)
    assert code == 0 and written(paths)[2]["触るファイル"][:2] == ["b.py", "c.py"], res


# ---------- 現状固定: 触るファイルのセルの読み方（_files の分割の前に固定する） ----------


@pytest.mark.parametrize(
    ("cell", "part"),
    [
        (
            "`a.py`。ループの側は [issue-1-design-loop.md](issue-1-design-loop.md) にある",
            "。ループの側は / issue-1-design-loop.md / にある",
        ),
        ("`a.py`（[l](x.md)）と語", "x.md / と語"),
        ("`a.py`, b.py", "b.py"),
        ("`a.py`（新設", "閉じていない括弧"),
        ("`a.py`）、`b.py`", "）"),
        ("`a.py` **太字** `b.py`", "** / 太字 / **"),
        ("`a.py`<br>`b.py`", "<br>"),
        ("語1　語2,語3、`d.py`", "語1 / 語2 / 語3"),
        ("[`x.py`](x.py)", "x.py"),
    ],
    ids=["link-and-words", "link-in-parens", "bare-path", "unclosed", "stray-close", "emphasis", "html", "separators", "code-in-link"],
)
def test_characterize_the_unreadable_parts_of_a_cell(cell, part):
    """現状固定: 読めなかった部分は出た順に ` / ` で結ばれ、行の課題と文書が添う。"""
    with pytest.raises(dr.DesignCellError) as e:
        dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)
    assert [(p.issue, p.doc, p.part) for p in e.value.problems] == [(1, D1, part)]
    assert str(e.value) == f"#1（{D1}）の触るファイルのセルを読めない: {part}。{dr.CELL_FIX}"


@pytest.mark.parametrize(
    ("cell", "files"),
    [
        ("`a.py`（注（入れ子）記）`b.py`", ["a.py", "b.py"]),
        ("`  `、`c.py`", ["c.py"]),
        ("`a.py`（`b.py`）`c.py`", ["a.py", "c.py"]),
        ("` d.py `", ["d.py"]),
    ],
    ids=["nested-parens", "blank-code-span", "code-in-parens", "stripped"],
)
def test_characterize_the_files_of_a_cell(cell, files):
    """現状固定: 入れ子の括弧は深さで読み飛ばし、空のコードスパンは捨て、中身は前後の空白を落とす。"""
    assert dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)[0].files == files
