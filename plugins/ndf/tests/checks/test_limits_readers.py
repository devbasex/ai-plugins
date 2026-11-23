"""規則 `limit-readers`（方針の読み手の指定と、実際に方針を引くモジュールの突き合わせ）。#3684 の受け入れ条件 1・6。"""

from __future__ import annotations

import ast
import json

import pytest

from ndf.checks import limits_readers, run
from ndf.checks.model import CheckError

READERS = {
    "ndf.contracts.limits.policy": {"arg": "name", "index": 0},
    "ndf.contracts.limits.value": {"arg": "name", "index": 0},
    "ndf.contracts.limits.seconds": {"arg": "name", "index": 0},
    "ndf.contracts.limits.from_env": {"arg": "policy_name", "index": 2},
    "ndf.domains.hooks.tables.policy_value": {"arg": "name", "index": 0},
}
IMPORT = "from ndf.contracts import limits\n"


def rules_of(readers=(), dynamic=(), whole=(), tables=("ndf.contracts.limits.table",)) -> dict:
    """規則のデータ（読み口は上限の設定の読み手と、`readers` の追加）。"""
    return {"limits": {"readers": {**READERS, **dict(readers)}, "tables": list(tables), "whole": list(whole), "dynamic": list(dynamic)}}


def got(tmp_path, files: dict[str, str], rules=None) -> dict[str, list[str]]:
    """合成したモジュールが引く方針 → モジュールの名前（`ndf.` を除く）。"""
    trees = {f"src/ndf/{rel}": ast.parse(text) for rel, text in files.items()}
    conf = limits_readers.settings(rules if rules is not None else rules_of(), tmp_path)
    found = limits_readers.actual(trees, conf)
    return {name: sorted(m.removeprefix("ndf.") for m in mods) for name, mods in found.items()}


def unresolved(tmp_path, files: dict[str, str], rules=None) -> list[tuple[str, int]]:
    trees = {f"src/ndf/{rel}": ast.parse(text) for rel, text in files.items()}
    conf = limits_readers.settings(rules if rules is not None else rules_of(), tmp_path)
    return [(r.module.removeprefix("ndf."), r.line) for r in limits_readers.reads(trees, conf) if r.names is None]


# ---------------------------------------------------------------- actual: 読み口と引数の解き方


@pytest.mark.parametrize(
    "text",
    [
        IMPORT + 'limits.seconds("a.b")\n',
        IMPORT + 'NAME = "a.b"\nlimits.seconds(NAME)\n',
        IMPORT + 'POLICY = ("a.b", "count")\nlimits.value(*POLICY)\n',
        IMPORT + 'def f(env):\n    return limits.from_env(env, "N", "a.b", "seconds")\n',
        IMPORT + 'def f(env):\n    return limits.from_env(env, "N", policy_name="a.b", key="seconds")\n',
        IMPORT + 'def f():\n    name = "a.b"\n    return limits.value(name, "interval")\n',
        IMPORT + 'A = "a.b"\nALL = (A,)\n\ndef f():\n    return [limits.seconds(n) for n in ALL]\n',
        IMPORT + 'BY = {"x": "a.b"}\n\ndef f(k):\n    return limits.seconds(BY[k])\n',
        'from ndf.contracts import limits as limits_mod\nlimits_mod.value("a.b", "count")\n',
        'from ndf.contracts.limits import seconds\nseconds("a.b")\n',
        'import ndf.contracts.limits\nndf.contracts.limits.policy("a.b")\n',
        'from ..contracts import limits\nlimits.policy("a.b")\n',
        'from ndf.domains.hooks import tables\ntables.policy_value("a.b", "attempts")\n',
    ],
)
def test_actual_reads_every_call_form_of_the_shipped_code(tmp_path, text):
    assert got(tmp_path, {"core/m.py": text}) == {"a.b": ["core.m"]}
    assert unresolved(tmp_path, {"core/m.py": text}) == []


def test_another_module_of_the_same_name_is_not_a_reader(tmp_path):
    files = {"m.py": 'from ndf.testing import limits as limits_mod\nlimits_mod.value("a.b", "count")\nlimits_mod.seconds("c.d")\n'}
    assert got(tmp_path, files) == {} and unresolved(tmp_path, files) == []


def test_a_constant_of_another_module_is_followed_one_step(tmp_path):
    files = {
        "cap.py": 'POLICY = ("a.b", "count")\nPOLL = "c.d"\n',
        "machine.py": "from ndf import cap\nCAP_POLICY = cap.POLICY\n",
        "one.py": IMPORT + "from ndf import cap\nPOLL = cap.POLL\nlimits.value(*cap.POLICY)\nlimits.value(POLL, 'interval')\n",
        "two.py": IMPORT + "from ndf import machine\nlimits.value(*machine.CAP_POLICY)\n",
    }
    assert got(tmp_path, files) == {"a.b": ["one"], "c.d": ["one"]}
    assert unresolved(tmp_path, files) == [("two", 3)]


def test_a_name_made_from_itself_or_from_names_of_another_module_is_not_resolved(tmp_path):
    files = {
        "low.py": 'A = "a.b"\nNAMES = (A,)\n',
        "own.py": IMPORT + 'def f(env):\n    x = env["X"]\n    x = x if x else "a.b"\n    return limits.seconds(x)\n',
        "use.py": IMPORT + 'from ndf import low\nA = "c.d"\n\ndef f():\n    return [limits.seconds(n) for n in low.NAMES]\n',
    }
    assert got(tmp_path, files) == {}
    assert unresolved(tmp_path, files) == [("own", 5), ("use", 6)]


def test_both_arms_of_a_conditional_are_read(tmp_path):
    text = IMPORT + 'def f(x):\n    return limits.seconds("a.b" if x else "c.d")\n'
    assert got(tmp_path, {"m.py": text}) == {"a.b": ["m"], "c.d": ["m"]}


# ---------------------------------------------------------------- 名前を引数で受ける関数・欄・読み口


def test_callers_of_a_function_that_takes_the_name_are_the_readers(tmp_path):
    rules = rules_of({"ndf.low.stop": {"arg": "key", "index": 1}, "ndf.low.weight": {"arg": "kind", "index": 0, "template": "w.{}"}})
    files = {
        "low.py": IMPORT
        + 'TREE = "a.tree"\nHEAD = "w."\n\ndef stop(proc, key=TREE):\n    return limits.seconds(key)\n\n'
        + 'def weight(kind):\n    return limits.value(HEAD + kind, "gib")\n',
        "use.py": 'from ndf import low\nlow.stop(1)\nlow.stop(2, "a.test")\nlow.weight("cli")\n',
    }
    # 既定値の方針は、既定値を持つモジュールが引く
    assert got(tmp_path, files, rules) == {"a.tree": ["low"], "a.test": ["use"], "w.cli": ["use"]}
    assert unresolved(tmp_path, files, rules) == []
    assert unresolved(tmp_path, {"low.py": files["low.py"]}) == [("low", 6), ("low", 9)]


def test_methods_and_fields_that_take_the_name(tmp_path):
    rules = rules_of({"ndf.hub.Hub.nodes": {"arg": "batch", "index": 1}, "ndf.hub.Row": {"arg": "cap", "index": 0}})
    files = {
        "hub.py": IMPORT
        + 'class Row:\n    cap: str\n\nclass Hub:\n    def nodes(self, items, batch="a.batch"):\n        return limits.value(batch, "count")\n\n'
        + 'def pages(spec):\n    return limits.value(spec.cap, "count")\n\nROWS = Row("a.cap")\n',
        "use.py": 'def f(hub, other):\n    hub.nodes([1], batch="a.checks")\n    hub.nodes([1])\n    other.seconds("not.a.policy")\n',
    }
    assert got(tmp_path, files, rules) == {"a.batch": ["hub"], "a.cap": ["hub"], "a.checks": ["use"]}
    assert unresolved(tmp_path, files, rules) == []


def test_a_reader_handed_as_an_argument(tmp_path):
    files = {
        "low.py": 'FROM = "a.b"\n\ndef thresholds(environ, value):\n    return value(FROM, "count")\n',
        "use.py": IMPORT + "from ndf import low\nlow.thresholds({}, limits.value)\n",
    }
    rules = rules_of({"ndf.low.thresholds.value": {"arg": "name", "index": 0}})
    assert got(tmp_path, files, rules) == {"a.b": ["low"]} and unresolved(tmp_path, files, rules) == []
    assert unresolved(tmp_path, files) == [("use", 3)]


# ---------------------------------------------------------------- 動的な読み口と、表を丸ごと受け取る読み


BUDGETS = {
    "runtimes": {"claude": {"events": {"Stop": {"budget": "stop"}}}, "codex": {"events": {"End": {"budget": "end"}, "X": {"budget": None}}}}
}
BUDGET = {"module": "ndf.budget", "vocabulary": "r.json#/runtimes/*/events/*/budget", "reason": "r"}
DYNAMIC = [{**BUDGET, "template": "hook.budget.{}"}, {**BUDGET, "template": "hook.budget.{}.{1}"}]


def test_dynamic_readers_count_only_the_names_made_from_the_vocabulary(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(BUDGETS))
    files = {"budget.py": IMPORT + 'def f(event):\n    return limits.value(f"hook.budget.{event}", "deadline")\n'}
    assert got(tmp_path, files, rules_of(dynamic=DYNAMIC)) == {
        n: ["budget"] for n in ("hook.budget.stop", "hook.budget.stop.claude", "hook.budget.end", "hook.budget.end.codex")
    }
    assert unresolved(tmp_path, files, rules_of(dynamic=DYNAMIC)) == []
    # 指定が無ければ数えず、解けない読み口として残す
    assert got(tmp_path, files) == {} and unresolved(tmp_path, files) == [("budget", 3)]
    # 動的に引かないモジュールの指定は、何も引いたことにしない
    assert got(tmp_path, {"budget.py": IMPORT + 'limits.seconds("a.b")\n'}, rules_of(dynamic=DYNAMIC)) == {"a.b": ["budget"]}


@pytest.mark.parametrize("vocabulary", ["missing.json#/a", "r.json#/nope", "r.json#/runtimes/claude/events/Stop/budget/x"])
def test_a_vocabulary_that_cannot_be_read_stops(tmp_path, vocabulary):
    (tmp_path / "r.json").write_text(json.dumps(BUDGETS))
    row = {**BUDGET, "template": "{}", "vocabulary": vocabulary}
    with pytest.raises(CheckError, match="動的な読み口の語彙"):
        limits_readers.settings(rules_of(dynamic=[row]), tmp_path)


def test_reading_the_whole_table_needs_a_reason(tmp_path):
    files = {"m.py": IMPORT + 'limits.table()["a.b"]\n'}
    assert unresolved(tmp_path, files) == [("m", 2)]
    assert unresolved(tmp_path, files, rules_of(whole=[{"module": "ndf.m", "reason": "名前の有無だけを見る"}])) == []


# ---------------------------------------------------------------- check


def policy(*read_by: str) -> dict:
    return {"summary": "s", "seconds": 1, "read_by": list(read_by)}


@pytest.fixture
def with_limits(plugin):
    """上限の設定を `policies` にした plugin。"""

    def write(**policies):
        rows = {name.replace("_", "."): row for name, row in policies.items()}
        plugin.write("contracts/limits.json", json.dumps({"owner": "o", "policies": rows}, indent=1))
        return plugin

    return write


def findings(plugin, rules=None) -> list[tuple[str, int, str]]:
    found = limits_readers.check(plugin.scope(), rules if rules is not None else rules_of())
    assert {f.rule for f in found} <= {"limit-readers"}
    return [(f.path.rsplit("/", 1)[-1], f.line, f.detail) for f in found]


def test_declared_returns_the_readers_of_each_policy(with_limits):
    plugin = with_limits(a_b=policy("ndf.m"), c_d=policy("ndf.m", "ndf.n"))
    assert limits_readers.declared(plugin.root / "contracts") == {"a.b": frozenset({"ndf.m"}), "c.d": frozenset({"ndf.m", "ndf.n"})}


def test_no_findings_when_the_readers_match(with_limits):
    plugin = with_limits(a_b=policy("ndf.m"))
    plugin.write("src/ndf/m.py", IMPORT + 'limits.seconds("a.b")\nlimits.seconds("a.b")\n')
    assert findings(plugin) == []


def test_a_declared_reader_that_does_not_read_is_unread(with_limits):
    plugin = with_limits(a_b=policy("ndf.m"))
    [(path, line, detail)] = findings(plugin)
    assert path == "limits.json" and line == 4
    assert detail.startswith("unread:") and "a.b" in detail and "ndf.m" in detail


def test_a_reader_that_is_not_declared_is_undeclared_once(with_limits):
    plugin = with_limits(a_b=policy("ndf.m"))
    plugin.write("src/ndf/m.py", IMPORT + 'limits.seconds("a.b")\n')
    plugin.write("src/ndf/n.py", IMPORT + '\nlimits.seconds("a.b")\nlimits.seconds("a.b")\n')
    [(path, line, detail)] = findings(plugin)
    assert (path, line) == ("n.py", 3)
    assert detail.startswith("undeclared:") and "a.b" in detail and "ndf.n" in detail


def test_a_read_that_cannot_be_resolved_is_a_finding(with_limits):
    plugin = with_limits(a_b=policy("ndf.m"))
    plugin.write("src/ndf/m.py", IMPORT + 'limits.seconds("a.b")\n\n\ndef f(x):\n    return limits.seconds(f"a.{x}")\n')
    [(path, line, detail)] = findings(plugin)
    assert (path, line) == ("m.py", 6)
    assert detail.startswith("unresolved:") and "ndf.m" in detail


def test_a_policy_outside_the_vocabulary_of_a_dynamic_reader_is_unread(with_limits):
    plugin = with_limits(**{"hook.budget.stop": policy("ndf.budget"), "hook.budget.unused": policy("ndf.budget")})
    plugin.write("contracts/r.json", json.dumps(BUDGETS))
    plugin.write("src/ndf/budget.py", IMPORT + 'def f(event):\n    return limits.value(f"hook.budget.{event}", "deadline")\n')
    [(path, _, detail)] = findings(plugin, rules_of(dynamic=DYNAMIC))
    assert path == "limits.json" and detail.startswith("unread:") and "hook.budget.unused" in detail


def test_broken_settings_stop_and_a_plugin_without_settings_is_skipped(plugin, with_limits):
    plugin.write("src/ndf/m.py", IMPORT + 'limits.seconds("a.b")\n')
    assert findings(plugin) == []
    with_limits(a_b={"summary": "s", "seconds": 1})
    with pytest.raises(CheckError, match="read_by"):
        findings(plugin)
    plugin.write("contracts/limits.json", "{")
    with pytest.raises(CheckError, match="上限の設定を読めない"):
        findings(plugin)


def test_the_rule_runs_in_the_check(with_limits, rules, monkeypatch):
    plugin = with_limits(a_b=policy("ndf.m"))
    monkeypatch.setattr(run.spec_docs, "check", lambda *_: [])  # 規約の生成物を持たない合成のプラグイン
    rules["limits"] = rules_of()["limits"]
    assert [f.path.rsplit("/", 1)[-1] for f in run.check(plugin.scope(), rules) if f.rule == "limit-readers"] == ["limits.json"]
