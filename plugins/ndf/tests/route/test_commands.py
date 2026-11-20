"""テンプレートのステップのコマンドを偽の gh・ndf と本物の git で打ち、プランの実行と同じ読み方で停止理由を確かめる。"""

from __future__ import annotations

import json
import os
import re
import shlex

import httpx
from githubkit import GitHub as Client

from ndf import cli
from ndf.adapters import github
from ndf.contracts import causes
from ndf.core import route
from ndf.core.route import local_tests
from ndf.core.plan import model, steps

from ._flow import fake_bin, git, ndf_bin, plan_of, repo_with_base, run_step, step_of, values

_REPO = "devbasex/ai-plugins"


def test_conflict_check_reports_dirty_pr_as_base_conflict(tmp_path):
    """#2752・#2680・#2273: マージ先と衝突した PR（mergeStateStatus が DIRTY）は待たずに base_conflict で返す。"""
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


def test_changes_check_finds_no_changes_since_work_start(tmp_path):
    """#3039・#3474: worker を打つ前の HEAD から変更が無ければ not_applicable（PR を作らない）。変更があれば ok。

    マージ先へまだマージされていない（取得し直していない）ブランチの差分は数えない。記録した起点は打ち直しでも上書きしない。起点を読めなければ precondition（変更なしと読まない）。
    """
    for purpose in ("close", "plan_to_spec"):
        plan = plan_of(purpose, base="develop")
        start, check = step_of(plan, "work-start"), step_of(plan, "changes-check")
        repo = repo_with_base(tmp_path / purpose, {"merged.py": "m = 1\n"})
        fill = {"state_dir": str(tmp_path / purpose / "state")}
        (tmp_path / purpose / "state").mkdir()
        assert run_step(start, repo, fill=fill)["status"] == "ok"
        assert run_step(check, repo, fill=fill)["kind"] == "not_applicable"
        (repo / "spec.md").write_text("# spec\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "spec")
        assert run_step(start, repo, fill=fill)["status"] == "ok"
        assert run_step(check, repo, fill=fill)["status"] == "ok"
    missing = run_step(check, repo, fill={"state_dir": str(tmp_path / "none")})
    assert missing["kind"] == "precondition"


def test_assess_detects_docs_only_diff(tmp_path):
    """#3023: 差分が文書（既定は .md）だけなら not_applicable（全体テストを飛ばす）。コードを含めば ok。"""
    step = step_of(plan_of("check", base="develop", remote="origin"), "assess")
    bindir = ndf_bin(tmp_path)
    docs = repo_with_base(tmp_path / "a", {"guide.md": "# g\n"})
    assert run_step(step, docs, bindir)["kind"] == "not_applicable"
    code = repo_with_base(tmp_path / "b", {"guide.md": "# g\n", "a.py": "a = 1\n"})
    assert run_step(step, code, bindir)["status"] == "ok"


def test_assess_stops_as_precondition_when_base_is_not_fetched(tmp_path):
    """差分を取れない（`origin/<base>` が無い）ときは文書だけと読まず precondition で止まる（全体テストを黙って飛ばさない）。"""
    step = step_of(plan_of("check", base="main", remote="origin"), "assess")
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    assert run_step(step, repo, ndf_bin(tmp_path))["kind"] == "precondition"


def _ci_result(path, nodes):
    found = {"tool": "ci", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {"failed_tests": nodes}}
    path.write_text("CI を待っている\n" + json.dumps(found) + "\n", encoding="utf-8")


def _slot(monkeypatch, tmp_path, body, *, append=False):
    """`ndf route test` が打つスロット（test-slot）を偽のコマンドへ差し替え、受けた引数を書くファイルを返す。"""
    seen = tmp_path / "seen.txt"
    script = (
        f'printf "%s\\n" "$@" >> {shlex.quote(str(seen))}\n{body}' if append else f'printf "%s\\n" "$@" > {shlex.quote(str(seen))}\n{body}'
    )
    bindir = fake_bin(tmp_path, "slot", script)
    monkeypatch.setattr(local_tests, "TEST_SLOT", (str(bindir / "slot"),))
    return seen


def test_test_steps_run_through_route_test():
    """手元のテスト・全体テスト・CI の落ちの確かめ直しは ndf route test で打つ（終了コードの読み替えを 1 か所にする）。"""
    impl = plan_of("implement", test_command="pytest -q")
    assert step_of(impl, "test").run == ("ndf", "route", "test", "--result", "{state_dir}/test.json", "--", "pytest -q")
    ci_local = ("ndf", "route", "test", "--failed-from", "{state_dir}/ci.json", "--result", "{state_dir}/ci-local.json")
    assert step_of(impl, "ci-local").run == ci_local
    whole = step_of(plan_of("check", whole_test_command="make test"), "test-all").run
    assert whole == ("ndf", "route", "test", "--result", "{state_dir}/test.json", "--", "make test")


def test_ci_only_plans_drop_local_test_steps_and_follow_on():
    """#3473: CI テストの設定のあるリポジトリのプランから test・test-all・ci-local を外し、そこへのルートは続きへ付け替える。"""
    for purpose in ("implement", "check", "quality_gates", "merge", "close"):
        t = route.choose(purpose)
        plan = route.render(t, values(purpose), local_tests=False)
        ids = [s["id"] for s in plan["steps"]]
        assert not set(route.LOCAL_TEST_STEPS) & set(ids)
        model.parse(plan)
    assert "test_command" not in route.needed(route.choose("implement"), local_tests=False)
    impl = route.render(route.choose("implement"), {k: v for k, v in values("implement").items() if k != "test_command"}, local_tests=False)
    by_id = {s["id"]: s for s in impl["steps"]}
    assert by_id["fix"]["next"] == "push" and by_id["ci-fix"]["next"] == "push-fixed"
    check = route.render(route.choose("check"), values("check"), local_tests=False)
    assert {s["id"]: s for s in check["steps"]}["fix"]["next"] == "refactor"


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
    """シグナルで止まった子（128 以上）はテストの落ちと読まず、スロット（test-slot）自身の止まった結果はそのまま返す。"""
    _slot(monkeypatch, tmp_path, "exit 130")
    assert route.main(["test", "--", "make test"]) == 130
    assert capsys.readouterr().out == ""
    own = {"tool": "test-slot", "status": "stopped", "summary": "待ちの上限", "cause": {"kind": "wait_timeout", "detail": "x"}}
    _slot(monkeypatch, tmp_path, f"echo {shlex.quote(json.dumps(own))}; exit 3")
    assert route.main(["test", "--", "make test"]) == 3
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == own


SUITES = [
    {"name": "py", "command": "pytest -q", "scope_command": "pytest -q {paths}", "paths": ["tests"]},
    {"name": "web", "command": "vitest run", "scope_command": "vitest run {paths}", "paths": ["web"]},
    {"name": "lint", "kind": "lint", "command": "ruff check", "scope_command": "ruff check {paths}"},
]


def _declare(monkeypatch, tmp_path, suites=SUITES):
    """打つ場所（作業場所）の設定 `.ndf/project.json` にテストの suite を置き、そこへ移る。"""
    (tmp_path / ".ndf").mkdir(exist_ok=True)
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def _slots(monkeypatch, tmp_path, body):
    """`_slot` と同じだが、打つたびに受けたコマンドを 1 行ずつ足す（suite ごとに打つ回数を数える）。"""
    return _slot(monkeypatch, tmp_path, body, append=True)


def _failed_from(ci, out):
    return route.main(["test", "--failed-from", str(ci), "--result", str(out)])


def test_ci_local_runs_failed_test_files_through_their_suite_and_records_the_command(tmp_path, monkeypatch, capsys, last_result):
    """#3188: CI で落ちたテストのファイルだけを、それを持つ suite の scope_command の {paths} へ入れて打つ。

    落ちたテストを持たない suite（web）と検査の suite（lint）は打たない。通ったときも打ったコマンドと合否を結果 JSON に残す。
    """
    _declare(monkeypatch, tmp_path)
    ci, out = tmp_path / "ci.json", tmp_path / "state" / "ci-local.json"
    nodes = ["tests/a.py::t1", "tests/a.py::t2", "tests/b.py::t3"]
    _ci_result(ci, nodes)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    assert _failed_from(ci, out) == 0
    assert seen.read_text().splitlines() == ["pytest -q tests/a.py tests/b.py"]
    found = last_result(capsys.readouterr().out)
    assert found["status"] == "ok" and found["metrics"]["failed_tests"] == nodes
    assert [(i["command"], i["passed"]) for i in found["items"]] == [("pytest -q tests/a.py tests/b.py", True)]
    assert json.loads(out.read_text(encoding="utf-8")) == found


def test_ci_local_runs_each_suite_holding_failed_tests_and_reports_failure(tmp_path, monkeypatch, capsys, last_result):
    """#3188: suite が複数なら、落ちたテストを持つ suite ごとに 1 回打つ。どれかが落ちれば test_failed（push しない）。"""
    _declare(monkeypatch, tmp_path)
    ci, out = tmp_path / "ci.json", tmp_path / "ci-local.json"
    nodes = ["tests/a.py::t1", "web/b.test.ts"]
    _ci_result(ci, nodes)
    seen = _slots(monkeypatch, tmp_path, 'case "$1" in vitest*) exit 1 ;; esac')
    assert _failed_from(ci, out) == causes.exit_code("test_failed")
    assert seen.read_text().splitlines() == ["pytest -q tests/a.py", "vitest run web/b.test.ts"]
    found = last_result(capsys.readouterr().out)
    assert (found["cause"]["kind"], found["metrics"]["failed_tests"]) == ("test_failed", nodes)
    assert [(i["suite"], i["exit_code"], i["passed"]) for i in found["items"]] == [("py", 0, True), ("web", 1, False)]
    assert json.loads(out.read_text(encoding="utf-8")) == found


def test_ci_local_stops_without_running_whole_tests_when_failed_tests_are_unknown(tmp_path, monkeypatch, capsys, last_result):
    """#3188: 落ちたテストを読めない（空）か、範囲を打てる suite に当たらないファイルがあれば、全体テストを打たずに止まる。"""
    _declare(monkeypatch, tmp_path)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    for nodes in ([], ["tests/a.py::t1", "docs/x.py::t"]):
        ci = tmp_path / "ci.json"
        _ci_result(ci, nodes)
        _failed_from(ci, tmp_path / "ci-local.json")
        assert last_result(capsys.readouterr().out)["cause"]["kind"] == "precondition"
    _declare(monkeypatch, tmp_path, [{"name": "py", "command": "pytest -q"}])
    _ci_result(ci, ["tests/a.py::t1"])
    _failed_from(ci, tmp_path / "ci-local.json")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "precondition"
    assert not seen.exists()


def _ci_test_failed(path, metrics):
    """`ndf ci watch` が test_failed で止まった結果（落ちたテストは空）。"""
    found = {
        "tool": "ci", "status": "stopped", "summary": "落ちた", "items": [],
        "cause": {"kind": "test_failed", "detail": "CI: test_failed"},
        "metrics": {"failed_tests": [], "test_files": [], **metrics},
    }  # fmt: skip
    path.write_text(json.dumps(found) + "\n", encoding="utf-8")


def _read(check, runner, reason):
    return {"check": check, "run_id": 9, "runner": runner, "source": "none", "found": 0, "reason": reason}


def test_ci_local_names_the_missing_junit_key_and_the_unread_runner(tmp_path, monkeypatch, capsys, last_result):
    """受け入れ条件 10: glob の設定が無く一覧が空なら、打たずに precondition で止まり、キーと runner を出す。"""
    _declare(monkeypatch, tmp_path)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    ci = tmp_path / "ci.json"
    _ci_test_failed(
        ci, {"failed_test_reads": [_read("factory-check", "vitest", "runner vitest のログのパーサーが無い"), _read("e2e", None, "x")]}
    )
    _failed_from(ci, tmp_path / "ci-local.json")
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "precondition"
    detail = found["cause"]["detail"]
    assert "test.ci.junit_artifacts" in detail and "vitest（チェック factory-check）" in detail and "当たる suite が無い" in detail
    assert not seen.exists()


def test_ci_local_names_the_glob_and_the_reasons_when_neither_source_reads(tmp_path, monkeypatch, capsys, last_result):
    """受け入れ条件 11: glob があって成果物とログのどちらでも読めなければ、glob と理由を出す。"""
    _declare(monkeypatch, tmp_path)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    ci = tmp_path / "ci.json"
    reads = [_read("pytest (0/2)", "pytest", "成果物 junit-0 を読めない: 410; ログを読めない: 403")]
    _ci_test_failed(ci, {"junit_artifacts": "junit-*", "failed_test_reads": reads})
    _failed_from(ci, tmp_path / "ci-local.json")
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "precondition"
    assert "junit-*" in found["cause"]["detail"] and "410" in found["cause"]["detail"] and "403" in found["cause"]["detail"]
    assert not seen.exists()


def test_ci_local_refuses_a_test_failed_result_without_read_records(tmp_path, monkeypatch, capsys, last_result):
    """test_failed なのに読み取りの記録が無い結果は ndf ci watch の結果でないとして invalid（テストを打たない）。"""
    _declare(monkeypatch, tmp_path)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    ci = tmp_path / "ci.json"
    _ci_test_failed(ci, {})
    assert _failed_from(ci, tmp_path / "ci-local.json") == causes.exit_code("invalid")
    assert "failed_test_reads" in last_result(capsys.readouterr().out)["cause"]["detail"]
    assert not seen.exists()


def test_ci_local_stops_as_invalid_when_ci_result_is_unreadable(tmp_path, monkeypatch, capsys, last_result):
    """CI の結果を読めないときは、テストを打たずに invalid の結果 JSON で止まる（テストの落ちの終了コードと衝突しない）。"""
    _declare(monkeypatch, tmp_path)
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    _failed_from(tmp_path / "no.json", tmp_path / "ci-local.json")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert not seen.exists()


def test_route_test_takes_either_a_command_or_failed_tests(tmp_path, monkeypatch, capsys, last_result):
    """コマンドと --failed-from は 1 つだけを渡す（両方・どちらも無しは打たずに invalid）。"""
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    for argv in (["test"], ["test", "--failed-from", str(tmp_path / "ci.json"), "--", "pytest -q"]):
        route.main(argv)
        assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert not seen.exists()


def test_ci_local_quotes_failed_test_files_for_the_shell(tmp_path, monkeypatch):
    """落ちたテストのファイルは 1 つずつシェルの引用へ通して {paths} へ入れる（空白・`$(...)`・`;` を分割も実行もしない）。"""
    _declare(monkeypatch, tmp_path)
    ci = tmp_path / "ci.json"
    files = ["tests/a b.py", "tests/$(touch hit); x.py"]
    _ci_result(ci, [f"{f}::t[1]" for f in files])
    seen = _slots(monkeypatch, tmp_path, "exit 0")
    assert _failed_from(ci, tmp_path / "ci-local.json") == 0
    assert shlex.split(seen.read_text().splitlines()[-1]) == ["pytest", "-q", *files]
    assert not (tmp_path / "hit").exists()


def test_ci_local_passes_signal_and_slot_results_through_without_running_the_rest(tmp_path, monkeypatch, capsys):
    """現状固定: --failed-from でもスロットの止まり・シグナルの終了コードはそのまま返し、残りの suite を打たず結果ファイルも書かない。"""
    _declare(monkeypatch, tmp_path)
    ci, out = tmp_path / "ci.json", tmp_path / "ci-local.json"
    _ci_result(ci, ["tests/a.py::t1", "web/b.test.ts"])
    seen = _slots(monkeypatch, tmp_path, "exit 130")
    assert _failed_from(ci, out) == 130
    assert seen.read_text().splitlines() == ["pytest -q tests/a.py"]
    assert capsys.readouterr().out == "" and not out.exists()
    seen.unlink()
    own = {"tool": "test-slot", "status": "stopped", "summary": "待ちの上限", "cause": {"kind": "wait_timeout", "detail": "x"}}
    seen = _slots(monkeypatch, tmp_path, f"echo {shlex.quote(json.dumps(own))}; exit 3")
    assert _failed_from(ci, out) == 3
    assert seen.read_text().splitlines() == ["pytest -q tests/a.py"]
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == own and not out.exists()


def test_ci_local_writes_the_precondition_result_with_the_failed_tests(tmp_path, monkeypatch, capsys, last_result):
    """現状固定: 打てずに止まるときも --result へ同じ結果を書き、metrics.failed_tests に CI の落ちたテストを残す。"""
    _declare(monkeypatch, tmp_path)
    ci, out = tmp_path / "ci.json", tmp_path / "state" / "ci-local.json"
    nodes = ["tests/a.py::t1", "docs/x.py::t"]
    _ci_result(ci, nodes)
    code = _failed_from(ci, out)
    found = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("precondition")
    assert (found["cause"]["kind"], found["metrics"]) == ("precondition", {"failed_tests": nodes})
    assert json.loads(out.read_text(encoding="utf-8")) == found


def test_ci_local_invalid_result_is_not_written_to_the_result_file(tmp_path, monkeypatch, capsys, last_result):
    """現状固定: CI の結果を読めない invalid は標準出力だけに出し、--result のファイルは作らない。"""
    _declare(monkeypatch, tmp_path)
    out = tmp_path / "ci-local.json"
    assert _failed_from(tmp_path / "no.json", out) == causes.exit_code("invalid")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert not out.exists()


def test_ci_local_counts_runs_and_names_the_first_failed_command(tmp_path, monkeypatch, capsys, last_result):
    """現状固定: metrics の runs・failed_runs は打った本数と落ちた本数。落ちの詳細は最初に落ちたコマンドを名指す。"""
    _declare(monkeypatch, tmp_path)
    ci, out = tmp_path / "ci.json", tmp_path / "ci-local.json"
    nodes = ["tests/a.py::t1", "web/b.test.ts"]
    _ci_result(ci, nodes)
    _slots(monkeypatch, tmp_path, "exit 1")
    _failed_from(ci, out)
    found = last_result(capsys.readouterr().out)
    assert found["metrics"] == {"failed_tests": nodes, "runs": 2, "failed_runs": 2}
    assert found["cause"]["detail"].endswith(": pytest -q tests/a.py")
    assert "2 本のうち 2 本" in found["cause"]["detail"]
    assert [i["kind"] for i in found["items"]] == ["test_run", "test_run"]
    _slots(monkeypatch, tmp_path, "exit 0")
    _failed_from(ci, out)
    found = last_result(capsys.readouterr().out)
    assert found["metrics"] == {"failed_tests": nodes, "runs": 2, "failed_runs": 0}
    assert "2 本" in found["summary"]


_RISK = "- [ ] 残リスク: 設計文書に名前の出るテストの検証が止まっている。マージの前に決める"


def _serve_pull(monkeypatch, head: str, body: str) -> list[dict]:
    """feat/x の開いた PR 7 番（本文 `body`・HEAD `head`）を回答する GitHub。送ったマージの要求の本文を返す並びへ足す。"""
    sent: list[dict] = []
    pull = {
        "number": 7, "state": "open", "merged": False, "body": body, "mergeable_state": "clean",
        "head": {"sha": head, "ref": "feat/x"}, "base": {"ref": "develop"},
        "user": {"login": "octocat"}, "author_association": "OWNER",
    }  # fmt: skip
    node = {
        "number": 7, "state": "OPEN", "merged": False, "headRefOid": head, "headRefName": "feat/x", "baseRefName": "develop",
        "body": body, "mergeStateStatus": "CLEAN", "author": {"login": "octocat"}, "authorAssociation": "OWNER",
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/graphql":
            aliases = re.findall(r"(a\d+): pullRequests", json.loads(request.content)["query"])
            return httpx.Response(200, json={"data": {"repository": {a: {"nodes": [node]} for a in aliases}}})
        if request.method == "PUT":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"merged": True})
        if path.endswith("/rules/branches/develop"):
            return httpx.Response(200, json=[])
        if path == f"/repos/{_REPO}":
            return httpx.Response(200, json={"allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": True})
        return httpx.Response(200, json=pull)

    api = Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)
    monkeypatch.setattr(github, "client", lambda **_: api)
    return sent


def _merge_step(monkeypatch, tmp_path, body: str) -> tuple[int, list[dict], str]:
    """テンプレートの merge のステップの run を、feat/x の作業場所で打つ（GitHub は `_serve_pull` が回答する）。"""
    step = step_of(plan_of("immediate_fix", branch="feat/x", merge_method="merge"), "merge")
    assert list(step.run[:3]) == ["ndf", "ci", "merge"]
    repo = repo_with_base(tmp_path, {"a.py": "a = 1\n"})
    git(repo, "remote", "add", "origin", f"https://github.com/{_REPO}.git")
    head = git(repo, "rev-parse", "HEAD").strip()
    sent = _serve_pull(monkeypatch, head, body)
    monkeypatch.chdir(repo)
    code = cli.main(list(step.run[1:]))
    return code, sent, head


def test_merge_stops_on_an_undecided_premerge_risk_and_names_the_line(tmp_path, monkeypatch, capsys, last_result):
    """#2602: 本文に `- [ ] …マージの前に決める…` が残る PR は、マージの要求を送らずに止まり、残った行を理由に出す。"""
    code, sent, _ = _merge_step(monkeypatch, tmp_path, f"## 残リスク\n\n{_RISK}\n")
    out = last_result(capsys.readouterr().out)
    assert (out["status"], out["cause"]["kind"], code) == ("stopped", "refused", causes.exit_code("refused"))
    assert _RISK.removeprefix("- [ ] ") in out["cause"]["detail"]
    assert sent == []


def test_merge_proceeds_when_the_premerge_risk_is_checked(tmp_path, monkeypatch, capsys, last_result):
    """決めたマーカー（`- [x]`）のある残リスクは判断の記録とみなし、手元の HEAD を添えて設定の方式でマージする。"""
    decided = _RISK.replace("- [ ]", "- [x]", 1) + "（続けると決めた）"
    code, sent, head = _merge_step(monkeypatch, tmp_path, f"## 残リスク\n\n{decided}\n")
    out = last_result(capsys.readouterr().out)
    assert (out["status"], code) == ("ok", 0)
    assert sent == [{"sha": head, "merge_method": "merge"}]


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
    （worker が本文のセクションに足した行が次のループの 1 回で消えない）。設計のプランに本文を組み直すステップは無い。"""
    calls = tmp_path / "gh.log"
    gh = fake_bin(tmp_path, "gh", f'echo "$@" >> {shlex.quote(str(calls))}\ncase "$2" in list) echo 7 ;; *) exit 1 ;; esac')
    monkeypatch.setenv("PATH", f"{gh}:{os.environ['PATH']}")
    plan = _design_step(tmp_path, "pr")
    out = steps.run(steps.StepContext(plan, tmp_path / "state", {"state_dir": str(tmp_path / "state")}), plan.steps[0])
    assert out["status"] == "ok" and out["prs"] == [7]
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


# --- 現状固定（I-017: declared.load_suites と local_tests._declared_suites の重複を寄せる前に、両方の読み方を記録する） ---

_DECLARED_CASES = {
    "壊れた JSON": "{",
    "根が配列": "[1]",
    "test が無い": json.dumps({"x": 1}),
    "test が配列": json.dumps({"test": [1]}),
    "suites が無い": json.dumps({"test": {}}),
    "suites が辞書": json.dumps({"test": {"suites": {"a": 1}}}),
    "混在": json.dumps({"test": {"suites": [{"name": "py"}, "s", {"name": "l", "kind": "lint"}, {"name": "w", "kind": "test"}, 3]}}),
}


def _declared_at(tmp_path, content):
    """`tmp_path/.ndf/project.json` に `content` を置き、2 段下の場所を返す（根へ向かって探すことを通す）。"""
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    if content is not None:
        (tmp_path / ".ndf").mkdir()
        (tmp_path / ".ndf" / "project.json").write_text(content, encoding="utf-8")
    return sub


def _both(sub):
    from ndf.testing import declared

    try:
        raw = local_tests._declared_suites(sub)
    except local_tests.LocalTestError as exc:
        raw = ("LocalTestError", str(exc))
    return declared.load_suites(sub), raw


def test_current_declared_suites_without_a_ndf_settings(tmp_path):
    assert _both(_declared_at(tmp_path, None)) == ([], [])


def test_current_declared_suites_over_each_shape(tmp_path):
    got = {}
    for i, (name, content) in enumerate(_DECLARED_CASES.items()):
        got[name] = _both(_declared_at(tmp_path / str(i), content))
    path = tmp_path.resolve() / "0" / ".ndf" / "project.json"
    broken = got.pop("壊れた JSON")
    assert broken[0] == [] and broken[1][0] == "LocalTestError"
    assert broken[1][1].startswith(f"設定 {path} を読めない: ")
    assert got == {
        "根が配列": ([], None),
        "test が無い": ([], None),
        "test が配列": ([], None),
        "suites が無い": ([], None),
        "suites が辞書": ([], {"a": 1}),
        "混在": (
            [{"name": "py"}, {"name": "w", "kind": "test"}],
            [{"name": "py"}, "s", {"name": "l", "kind": "lint"}, {"name": "w", "kind": "test"}, 3],
        ),
    }


def test_current_declared_suites_take_the_nearest_ndf_settings(tmp_path):
    outer = json.dumps({"test": {"suites": [{"name": "outer"}]}})
    sub = _declared_at(tmp_path, outer)
    (sub.parent / ".ndf").mkdir()
    (sub.parent / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": [{"name": "inner"}]}}), encoding="utf-8")
    assert _both(sub) == ([{"name": "inner"}], [{"name": "inner"}])
    # 近い側の project.json がファイルでなければ、根の側の設定を読む
    other = _declared_at(tmp_path / "x", outer)
    (other / ".ndf" / "project.json").mkdir(parents=True)
    assert _both(other) == ([{"name": "outer"}], [{"name": "outer"}])
