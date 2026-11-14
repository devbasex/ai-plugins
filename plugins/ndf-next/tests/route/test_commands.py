"""雛形のステップのコマンドを偽の gh・ndf と本物の git で打ち、プランの実行と同じ読み方で止まった理由を確かめる。"""

from __future__ import annotations

import json
import os
import shlex

from ndf.core import route
from ndf.core.plan import model, steps

from ._flow import fake_bin, git, plan_of, repo_with_base, run_step, step_of


def test_conflict_check_reports_dirty_pr_as_base_conflict(tmp_path):
    """#2752・#2680・#2273: 宛先と衝突した PR（mergeStateStatus が DIRTY）は待たずに base_conflict で返す。"""
    step = step_of(plan_of("implement", branch="feat/x"), "conflict-check")
    dirty = fake_bin(tmp_path, "gh", "echo DIRTY")
    assert run_step(step, tmp_path, dirty)["kind"] == "base_conflict"
    clean = fake_bin(tmp_path, "gh", "echo CLEAN")
    assert run_step(step, tmp_path, clean)["status"] == "ok"


def test_done_check_skips_only_when_head_is_merged(tmp_path):
    """#2349: 今の HEAD をマージ済みの PR が持っていれば not_applicable で返し、プランを流し直さない。

    同じブランチの名前で過去にマージした PR があっても、HEAD が違えば（新しい実装）流す。gh を読めなければ precondition。
    """
    step = step_of(plan_of("implement", branch="feat/x"), "done-check")
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    head = git(repo, "rev-parse", "HEAD").strip()
    merged = fake_bin(tmp_path / "m", "gh", f"printf '%s\\n' 0000 {head}")
    assert run_step(step, repo, merged)["kind"] == "not_applicable"
    older = fake_bin(tmp_path / "o", "gh", "echo 0000")
    assert run_step(step, repo, older)["status"] == "ok"
    broken = fake_bin(tmp_path / "b", "gh", "exit 1")
    assert run_step(step, repo, broken)["kind"] == "precondition"


def test_changes_check_finds_no_changes(tmp_path):
    """#3039: 宛先から変更の無いブランチは not_applicable（PR を作らない）。変更があれば ok。"""
    step = step_of(plan_of("close", base="develop"), "changes-check")
    same = repo_with_base(tmp_path / "a", {})
    assert run_step(step, same)["kind"] == "not_applicable"
    changed = repo_with_base(tmp_path / "b", {"x.py": "x = 1\n"})
    assert run_step(step, changed)["status"] == "ok"


def test_assess_detects_docs_only_diff(tmp_path):
    """#3023: 差分が文書（.md）だけなら not_applicable（全体テストを飛ばす）。コードを含めば ok。"""
    step = step_of(plan_of("check", base="develop"), "assess")
    docs = repo_with_base(tmp_path / "a", {"guide.md": "# g\n"})
    assert run_step(step, docs)["kind"] == "not_applicable"
    code = repo_with_base(tmp_path / "b", {"guide.md": "# g\n", "a.py": "a = 1\n"})
    assert run_step(step, code)["status"] == "ok"


def test_assess_stops_as_precondition_when_base_is_not_fetched(tmp_path):
    """差分を取れない（`origin/<base>` が無い）ときは文書だけと読まず precondition で止まる（全体テストを黙って飛ばさない）。"""
    step = step_of(plan_of("check", base="main"), "assess")
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    assert run_step(step, repo)["kind"] == "precondition"


def _ci_result(path, nodes):
    found = {"tool": "ci", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {"failed_tests": nodes}}
    path.write_text("CI を待っている\n" + json.dumps(found) + "\n", encoding="utf-8")


def _slot(monkeypatch, tmp_path, body):
    """`ndf route test` が打つ枠（test-slot）を偽のコマンドへ差し替え、受けた引数を書くファイルを返す。"""
    seen = tmp_path / "seen.txt"
    bindir = fake_bin(tmp_path, "slot", f'printf "%s\\n" "$@" > {shlex.quote(str(seen))}\n{body}')
    monkeypatch.setattr(route, "TEST_SLOT", (str(bindir / "slot"),))
    return seen


def test_test_steps_run_through_route_test():
    """手元のテスト・全体テスト・CI の落ちの確かめ直しは ndf route test で打つ（終了コードの読み替えを 1 か所にする）。"""
    impl = plan_of("implement", test_command="pytest -q")
    assert step_of(impl, "test").run == ("ndf", "route", "test", "--result", "{state_dir}/test.json", "--", "pytest -q")
    ci_local = ("ndf", "route", "test", "--nodes-from", "{state_dir}/ci.json", "--", "pytest -q")
    assert step_of(impl, "ci-local").run == ci_local
    whole = step_of(plan_of("check", whole_test_command="make test"), "test-all").run
    assert whole == ("ndf", "route", "test", "--result", "{state_dir}/test.json", "--", "make test")


def test_route_test_reads_any_non_signal_failure_as_test_failed(tmp_path, monkeypatch, capsys, last_result):
    """pytest の 1 だけでなく、収集の誤りや make の 2・cargo test の 101 も test_failed にし、--result へも書く。"""
    for code in (1, 2, 101):
        _slot(monkeypatch, tmp_path, f"exit {code}")
        out = tmp_path / f"r{code}" / "test.json"
        assert route.main(["test", "--result", str(out), "--", "make test"]) == 1
        found = last_result(capsys.readouterr().out)
        assert (found["cause"]["kind"], found["metrics"]["exit_code"]) == ("test_failed", code)
        assert json.loads(out.read_text(encoding="utf-8")) == found


def test_route_test_passes_signal_and_slot_results_through(tmp_path, monkeypatch, capsys):
    """シグナルで止まった子（128 以上）はテストの落ちと読まず、枠（test-slot）自身の止まった結果はそのまま返す。"""
    _slot(monkeypatch, tmp_path, "exit 130")
    assert route.main(["test", "--", "make test"]) == 130
    assert capsys.readouterr().out == ""
    own = {"tool": "test-slot", "status": "stopped", "summary": "待ちの上限", "cause": {"kind": "wait_timeout", "detail": "x"}}
    _slot(monkeypatch, tmp_path, f"echo {shlex.quote(json.dumps(own))}; exit 3")
    assert route.main(["test", "--", "make test"]) == 3
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == own


def test_ci_local_runs_only_failed_tests_and_reports_failure(tmp_path, monkeypatch, capsys, last_result):
    """#3188: CI の修正の後に、CI で落ちたテストの node id だけを手元のテストのコマンドへ足して打つ。落ちれば test_failed。"""
    ci = tmp_path / "ci.json"
    _ci_result(ci, ["tests/a.py::t1", "tests/b.py::t2"])
    seen = _slot(monkeypatch, tmp_path, "exit 0")
    assert route.main(["test", "--nodes-from", str(ci), "--", "pytest -q"]) == 0
    assert seen.read_text().splitlines() == ["pytest -q tests/a.py::t1 tests/b.py::t2"]
    _slot(monkeypatch, tmp_path, "exit 2")
    route.main(["test", "--nodes-from", str(ci), "--", "pytest -q"])
    found = last_result(capsys.readouterr().out)
    assert (found["cause"]["kind"], found["metrics"]["failed_tests"]) == ("test_failed", ["tests/a.py::t1", "tests/b.py::t2"])


def test_ci_local_without_nodes_runs_whole_local_command(tmp_path, monkeypatch):
    """#3188: 落ちたテストを読めなかったときは、手元のテストのコマンドを丸ごと打ってから push する。"""
    ci = tmp_path / "ci.json"
    _ci_result(ci, [])
    seen = _slot(monkeypatch, tmp_path, "exit 0")
    route.main(["test", "--nodes-from", str(ci), "--", "pytest -q"])
    assert seen.read_text().splitlines() == ["pytest -q"]


def test_ci_local_stops_as_invalid_when_ci_result_is_unreadable(tmp_path, monkeypatch, capsys, last_result):
    """CI の結果を読めないときは、テストを打たずに invalid の結果 JSON で止まる（テストの落ちの終了コードと衝突しない）。"""
    seen = _slot(monkeypatch, tmp_path, "exit 0")
    route.main(["test", "--nodes-from", str(tmp_path / "no.json"), "--", "pytest -q"])
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert not seen.exists()


def test_ci_local_quotes_failed_test_ids_for_the_shell(tmp_path, monkeypatch):
    """落ちたテストの node id は 1 つずつシェルの引用へ通して足す（空白・`$(...)`・`;` を分割も実行もしない）。"""
    ci = tmp_path / "ci.json"
    nodes = ["tests/a.py::t[a b]", "tests/b.py::t[$(touch hit); x]"]
    _ci_result(ci, nodes)
    seen = _slot(monkeypatch, tmp_path, "exit 0")
    assert route.main(["test", "--nodes-from", str(ci), "--", "pytest -q"]) == 0
    assert shlex.split(seen.read_text().splitlines()[-1]) == ["pytest", "-q", *nodes]
    assert not (tmp_path / "hit").exists()


def test_merge_requires_checked_head_and_waits_for_merge_queue(tmp_path):
    """マージは手元の HEAD（CI・承認の対象）との一致を --match-head-commit で強制し、MERGED になるまで済んだとしない。"""
    step = step_of(plan_of("immediate_fix", branch="feat/x", merge_method="merge"), "merge")
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    head = git(repo, "rev-parse", "HEAD").strip()
    seen = tmp_path / "seen.txt"
    record = f'case "$2" in merge) printf "%s\\n" "$@" > {shlex.quote(str(seen))} ;;'
    merged = fake_bin(tmp_path / "m", "gh", f"{record} view) echo MERGED ;; esac")
    assert run_step(step, repo, merged)["status"] == "ok"
    assert seen.read_text().splitlines() == ["pr", "merge", "feat/x", "--merge", "--match-head-commit", head]
    queued = fake_bin(tmp_path / "q", "gh", f"{record} view) echo OPEN ;; esac")
    assert run_step(step, repo, queued)["kind"] == "ci_pending"


def test_pr_step_returns_pr_number_when_created_and_when_found(tmp_path):
    """PR を作ったときも、既にある PR を probe で見つけたときも、番号を items の kind: pr で返す（キューの {queue_prs}）。"""
    step = step_of(plan_of("close", branch="feat/x"), "pr")
    gh = fake_bin(tmp_path, "gh", 'case "$2" in create) echo https://github.com/o/r/pull/7 ;; view) echo 7 ;; list) echo 7 ;; esac')
    assert run_step(step, tmp_path, gh)["prs"] == [7]
    assert step.probe is not None
    probe = model.Step(id="pr-probe", run=step.probe)
    assert run_step(probe, tmp_path, gh)["prs"] == [7]
    open_only = fake_bin(tmp_path, "gh", 'case "$*" in *"--state open"*) echo 7 ;; esac')
    assert run_step(probe, tmp_path, open_only)["prs"] == [7]
    none = fake_bin(tmp_path, "gh", "true")
    found = run_step(probe, tmp_path, none)
    assert (found["status"], found["code"], found["prs"]) == ("stopped", 1, [])


def _design_step(tmp_path, step_id):
    raw = plan_of("design", branch="feat/x", base="develop", title="t", body_file=str(tmp_path / "body.md"))
    found = next(s for s in raw["steps"] if s["id"] == step_id)
    return model.parse({"name": "p", "worktree": str(tmp_path), "steps": [found]})


def test_design_pr_step_keeps_existing_pr_body(tmp_path, monkeypatch):
    """#2853: 設計の出し直しで PR が既にあれば、pr のステップは PR を作り直さず本文を書き換えない
    （worker が本文の節に足した行が次のループの 1 回で消えない）。設計のプランに本文を組み直すステップは無い。"""
    calls = tmp_path / "gh.log"
    gh = fake_bin(tmp_path, "gh", f'echo "$@" >> {shlex.quote(str(calls))}\ncase "$2" in list) echo 7 ;; *) exit 1 ;; esac')
    monkeypatch.setenv("PATH", f"{gh}:{os.environ['PATH']}")
    plan = _design_step(tmp_path, "pr")
    out = steps.run(steps.StepContext(plan, tmp_path / "state", {"state_dir": str(tmp_path / "state")}), plan.steps[0])
    assert out["status"] == "ok" and out["items"] == [{"kind": "pr", "number": 7}]
    assert [line.split()[:2] for line in calls.read_text(encoding="utf-8").splitlines()] == [["pr", "list"]]
    design = plan_of("design")
    assert not any("pr edit" in " ".join(s["run"]) or "--body" in " ".join(s["run"]) for s in design["steps"] if s["id"] != "pr")


def test_design_head_check_compares_pr_head_with_local_head(tmp_path):
    """#2932（#2721 の形）: PR の head が手元の HEAD と同じなら ok、違えば（push されていないコミットがある）
    precondition で止まる。gh を読めなくても precondition（レビューへ進まない）。"""
    repo = repo_with_base(tmp_path / "a", {"a.md": "# a\n"})
    head = git(repo, "rev-parse", "HEAD").strip()
    step = step_of(plan_of("design", branch="feat/x"), "head-check")
    same = fake_bin(tmp_path / "s", "gh", f"echo {head}")
    assert run_step(step, repo, same)["status"] == "ok"
    behind = fake_bin(tmp_path / "b", "gh", "echo 0000")
    assert run_step(step, repo, behind)["kind"] == "precondition"
    broken = fake_bin(tmp_path / "x", "gh", "exit 1")
    assert run_step(step, repo, broken)["kind"] == "precondition"
