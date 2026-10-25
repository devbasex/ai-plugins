"""#1630: `init` が着手前のテストの後に範囲テストのテンプレートを試走し、`{paths}` に入るテストのファイルを受けなければ止まる。

再現のリポジトリ（`scripts/storylib/{__init__,x}.py` と `tests/{__init__,test_x,test_y}.py`）を git で作り、宣言から
解いた戦略で `_verify_init` を走らせる。着手前のテストも試走も実際にシェルで走らせる。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import types

import pytest

PY = shlex.quote(sys.executable)
DISCOVER = f"{PY} -m unittest discover -s {{paths}}"
WHOLE = f"{PY} -m unittest discover -s tests"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    for rel, body in {
        "scripts/storylib/__init__.py": "",
        "scripts/storylib/x.py": "X = 1\n",
        # 環境の PYTHONPATH に別の `tests` パッケージがあっても、この `tests` を読むように通常のパッケージにする
        "tests/__init__.py": "",
        "tests/test_x.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n",
        "tests/test_y.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_y(self):\n        pass\n",
    }.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git("init", "-q", cwd=work)
    _git("add", "-A", cwd=work)
    _git("-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "-qm", "init", cwd=work)
    return work


@pytest.fixture
def verify(refactor, monkeypatch, tmp_path, repo):
    """宣言を受けて `_verify_init` を呼ぶ。止まれば `(終了コード, None)`、続けば `(None, prep)`。"""
    setup = sys.modules["refactor_lib.commands.setup"]
    ts = sys.modules["test_strategy"]
    monkeypatch.setenv("NDF_METRICS_DIR", str(tmp_path / "metrics"))
    monkeypatch.setattr(setup, "resolve_participants", lambda *a, **k: {"available": ["claude"]})
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    def call(suite, strategy=None, scope=("scripts/storylib",), round_test=None, extra=()):
        decl = {"version": 1, "test": {"suites": [suite, *extra]}}
        if strategy:
            decl["test"]["strategy"] = strategy
        resolved = ts.resolve(decl, round_test=round_test, scope_paths=list(scope))
        prep = types.SimpleNamespace(strategy=resolved, work=repo, tmp_dir=tmp_dir, decl=decl, repo="acme/demo", template_checks=[])
        args = types.SimpleNamespace(budget_minutes=30, scope=list(scope), pr=1630, require_all=None, round_test=round_test)
        inputs = types.SimpleNamespace(host="claude", include=[], exclude=[], model_spec={})
        try:
            _, baseline, _ = setup._verify_init(args, inputs, prep)
        except SystemExit as e:
            return e.code, None, None
        return None, prep, baseline

    def record():
        return json.loads((tmp_dir / "template-check.json").read_text(encoding="utf-8"))

    return types.SimpleNamespace(call=call, record=record, setup=setup, ts=ts)


def _suite(scope_command=DISCOVER, paths=None, **over):
    suite = {"name": "unit", "runner": "unittest", "command": WHOLE, "scope_command": scope_command}
    if paths is not None:
        suite["paths"] = paths
    suite.update(over)
    return suite


def test_a_template_that_rejects_files_stops_with_where_to_fix(verify, capsys):
    """受け入れ条件 1・2・3: 全体を走らせる戦略で suite が paths を宣言していれば `defect` で止まり、直す箇所が出る。"""
    code, prep, _ = verify.call(_suite(paths=["tests"]))
    assert code == 4 and prep is None, "状態ファイルを作る手前で止まる（改修の範囲テストへ進まない）"
    err = capsys.readouterr().err
    assert "suite: unit" in err and DISCOVER in err
    assert "tests/test_x.py tests/test_y.py" in err
    assert ".ndf/project.json の test.suites[0].scope_command" in err
    row = verify.record()["suites"][0]
    assert verify.record()["stopped"] is True
    assert (row["verdict"], row["source"], row["reference"], row["index"]) == ("defect", "repo", "green", 0)
    assert row["paths"] == ["tests/test_x.py", "tests/test_y.py"]
    assert row["command"] == verify.ts.fill(DISCOVER, row["paths"]), "I1: 改修の範囲テストと同じ組み立て"
    assert row["code"] == 1


def test_a_suite_that_declares_only_dot_stops_as_undecided(verify, capsys):
    """受け入れ条件 1・2: `paths` が `.` なら受け持ちが未確定のため `undecided` で止まり、直す箇所と paths の宣言を出す。"""
    code, _, _ = verify.call(_suite())
    assert code == 4
    err = capsys.readouterr().err
    assert "受けるかを確かめられません" in err and ".ndf/project.json の test.suites[0].scope_command" in err
    assert "--round-test" in err
    assert verify.record()["suites"][0]["verdict"] == "undecided"


def test_the_ci_strategy_stops_as_undecided_without_junit(verify, capsys):
    """受け入れ条件 1: CI に任せる戦略では基準が `none` で、unittest は JUnit を書かないため `undecided` で止まる。"""
    code, _, _ = verify.call(_suite(paths=["tests"]), strategy="local-scoped-ci-whole")
    assert code == 4
    row = verify.record()["suites"][0]
    assert (row["verdict"], row["reference"], row["fix_at"]) == ("undecided", "none", ".ndf/project.json の test.suites[0].scope_command")


def test_a_template_that_takes_files_continues_and_keeps_the_record(verify, capsys):
    """受け入れ条件 6・I6・決定 7: ファイルを受けるテンプレートは `ok` で続き、記録はそのまま `template_checks` へ置かれる。"""
    code, prep, baseline = verify.call(_suite(scope_command=f"{PY} -m unittest {{paths}}", paths=["tests"]))
    assert code is None
    assert prep.template_checks == verify.record()["suites"]
    assert [r["verdict"] for r in prep.template_checks] == ["ok"]
    assert "template_checks" not in baseline and baseline["status"] == "green", "着手前のテストの記録は書き換えない"
    assert "✅ 範囲テストのテンプレートを試走: unit" in capsys.readouterr().err


def test_a_pytest_template_continues(verify):
    """受け入れ条件 6: このリポジトリと同じ pytest の雛形は試走が通る。"""
    pytest_suite = _suite(
        scope_command=f"{PY} -m pytest -q -p no:cacheprovider {{paths}}", command=f"{PY} -m pytest -q -p no:cacheprovider tests"
    )
    code, prep, _ = verify.call(pytest_suite, scope=("scripts/storylib", "tests"))
    assert code is None
    row = prep.template_checks[0]
    assert (row["verdict"], row["source"], row["paths"]) == ("ok", "scope", ["tests/test_x.py", "tests/test_y.py"])


def test_lint_and_unscoped_suites_are_not_probed(verify, monkeypatch):
    """受け入れ条件 7・I7: 静的解析の suite と scope_command の無い suite は試走しない。試走は suite ごとに 1 回（I2）。"""
    template_check = sys.modules["refactor_lib.template_check"]
    seen = []
    real = template_check.run_with_timeout

    def counting(command, cwd, timeout, output=None):
        seen.append(command)
        return real(command, cwd, timeout, output=output)

    monkeypatch.setattr(template_check, "run_with_timeout", counting)
    lint = {"name": "lint", "kind": "lint", "command": "true", "scope_command": "false {paths}", "paths": ["."]}
    plain = {"name": "plain", "command": "true"}
    code, prep, _ = verify.call(_suite(scope_command=f"{PY} -m unittest {{paths}}", paths=["tests"]), extra=(lint, plain))
    assert code is None
    assert [r["suite"] for r in prep.template_checks] == ["unit"]
    assert seen == [f"{PY} -m unittest tests/test_x.py tests/test_y.py"]


def test_no_time_left_stops_without_running(verify, monkeypatch):
    """I2: 着手前のテストで上限を使い切ったら走らせずに `undecided` で止まる。"""
    setup = verify.setup
    real = setup.baseline_lib.run_baseline
    monkeypatch.setattr(
        setup.baseline_lib,
        "run_baseline",
        lambda strategy, work, timeout, *a: {**real(strategy, work, timeout, *a), "seconds": float(timeout)},
    )
    code, _, _ = verify.call(_suite(scope_command=f"{PY} -m unittest {{paths}}", paths=["tests"]))
    assert code == 4
    row = verify.record()["suites"][0]
    assert (row["verdict"], row["outcome"], row["code"]) == ("undecided", "not_run", None)
    assert "上限の残りが無い" in row["reason"]


def test_an_argument_template_names_the_argument(verify, capsys):
    """受け入れ条件 2: `--round-test` の雛形で止まったときは、直す箇所を引数の名前で出す。"""
    code, _, _ = verify.call(_suite(paths=["tests"]), round_test=DISCOVER)
    assert code == 4
    row = verify.record()["suites"][0]
    assert (row["suite"], row["index"], row["fix_at"]) == ("args", None, "--round-test の値")
    assert "--round-test の値" in capsys.readouterr().err
