"""規則 contracts-split: 分けた表が 1 項目 1 ファイルのままであること（#3687 の受け入れ条件 2）。"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from ndf import contracts
from ndf.checks import contracts_split, lint, run
from ndf.checks.model import Finding, Scope

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DIR = contracts.DEFAULT_DIR.name
LIST = f"{DIR}/{contracts.SPLIT_TABLES}"
ITEM = f"{DIR}/limits/test-run.test.json"
CAUSE = f"{DIR}/causes/ci_pending.json"
SCHEMAS = [f"{DIR}/{contracts.schema_path(table)}" for table in ("limits", "causes")]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """分ける表の一覧と、表ごとのスキーマと項目 1 つを置いたプラグイン（規則はファイルしか読まないので git を作らない）。"""
    top = tmp_path / "repo" / "plugins" / "p"
    for rel in (LIST, *SCHEMAS, ITEM, CAUSE, "pyproject.toml"):
        (top / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PLUGIN_ROOT / rel, top / rel)
    return top


def _scope(root: Path) -> Scope:
    scope = Scope(root.parents[1], root, "p")
    scope.sources = run.collect(root, scope.repo_root)
    return scope


def _check(root: Path, rules: dict) -> list[Finding]:
    return contracts_split.check(_scope(root), rules)


def _paths(root: Path, found: list[Finding]) -> list[str]:
    """指摘のパス（プラグインの根からの相対）。"""
    return [(root.parents[1] / f.path).relative_to(root).as_posix() for f in found]


def _merge_back(root: Path, table: str) -> None:
    """分けた表を、もとの 1 ファイルへ戻す。"""
    items = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (root / DIR / table).glob("[!_]*.json")}
    (root / DIR / f"{table}.json").write_text(json.dumps({contracts.split_tables()[table]["source_key"]: items}), encoding="utf-8")
    shutil.rmtree(root / DIR / table)


def _link_outside(root: Path, rel: str) -> None:
    outside = root.parents[2] / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / rel).unlink()
    (root / rel).symlink_to(outside)


def _link_directory_outside(root: Path) -> None:
    shutil.move(root / DIR / "causes", root.parents[2] / "outside")
    (root / DIR / "causes").symlink_to(root.parents[2] / "outside")


def _text(rel: str, value: object) -> Callable[[Path], object]:
    """`rel` の中身を書き換える操作。文字列はそのまま、ほかは JSON にして書く。"""
    body = value if isinstance(value, str) else json.dumps(value)
    return lambda root: (root / rel).write_text(body, encoding="utf-8")


def test_the_split_tables_have_no_findings(rules):
    # 実物の表の全件は、写さずにそのまま読む
    scope = Scope(PLUGIN_ROOT.parents[1], PLUGIN_ROOT, "p")
    scope.sources = run.collect(PLUGIN_ROOT / DIR, scope.repo_root)
    assert contracts_split.check(scope, rules) == []


def test_a_table_of_items_has_no_findings(root, rules):
    assert _check(root, rules) == []


@pytest.mark.parametrize(
    ("break_", "paths"),
    [
        (lambda root: _merge_back(root, "limits"), [f"{DIR}/limits.json", f"{DIR}/limits"]),
        (lambda root: shutil.rmtree(root / DIR / "causes"), [f"{DIR}/causes"]),
        (_link_directory_outside, [f"{DIR}/causes"]),
        (_text(f"{DIR}/causes.json", {"causes": []}), [f"{DIR}/causes.json"]),
    ],
    ids=["one-file-again", "no-directory", "directory-linked-outside", "old-file-remains"],
)
def test_a_table_that_is_not_a_directory_of_items_is_a_finding(root, rules, break_, paths):
    break_(root)
    found = _check(root, rules)
    assert _paths(root, found) == paths
    assert {(f.rule, f.line) for f in found} == {("contracts-split", 1)}


@pytest.mark.parametrize(
    ("rel", "value"),
    [
        (ITEM, {"policies": {"a.b": {"summary": "x", "seconds": 1}, "a.c": {"summary": "y", "seconds": 2}}}),
        (ITEM, {"owner": "o", "policies": {"a.b": {"summary": "x", "seconds": 1}}}),
        (CAUSE, {"causes": [{"kind": "a"}, {"kind": "b"}]}),
        (ITEM, {"a.b": {"summary": "x", "seconds": 1}, "a.c": {"summary": "y", "seconds": 2}}),
        (ITEM, {"summary": "x", "seconds": 1, "a.c": {"summary": "y", "seconds": 2}}),
        (ITEM, {"summary": "上限", "a.b": {"seconds": 1}, "a.c": {"count": 2}}),
        (CAUSE, {"retry": {"exit": "a"}, "meaning": "x"}),
        (CAUSE, [{"meaning": "a"}, {"meaning": "b"}]),
        (ITEM, "{not json"),
        (ITEM, '{"seconds": 1, "seconds": 2}'),
        (ITEM, "1"),
    ],
    ids=[
        "source-key",
        "source-key-and-more",
        "source-list",
        "key-to-item",
        "item-in-item",
        "heading-and-items",
        "item-named-as-a-field",
        "list",
        "not-json",
        "same-key-twice",
        "number",
    ],
)
def test_a_file_that_is_not_one_item_is_a_finding(root, rules, rel, value):
    _text(rel, value)(root)
    assert _paths(root, _check(root, rules)) == [rel]


@pytest.mark.parametrize(
    ("field", "paths"),
    [
        ({"type": "object", "required": ["keys"]}, []),
        ({"$ref": "#/$defs/endpoint"}, []),
        ({"type": ["object", "null"]}, []),
        ({"anyOf": [{"$ref": "#/$defs/endpoint"}, {"type": "null"}]}, []),
        ({"type": ["string", "null"]}, [ITEM]),
        ({"$ref": "#/$defs/none"}, [SCHEMAS[0]]),
        ({"$ref": "https://example.invalid/x.json"}, [SCHEMAS[0]]),
    ],
    ids=["object", "local-ref", "type-list", "any-of", "not-object", "unresolved-ref", "remote-ref"],
)
def test_a_field_that_the_schema_allows_to_be_an_object_is_not_another_item(root, rules, field, paths):
    schema = {"$defs": {"endpoint": {"type": "object"}}, "properties": {"summary": {"type": "string"}, "endpoint": field}}
    _text(SCHEMAS[0], schema)(root)
    _text(ITEM, {"summary": "x", "endpoint": {"flags": []}})(root)
    assert _paths(root, _check(root, rules)) == paths


@pytest.mark.parametrize(
    ("break_", "path", "named"),
    [
        (_text(f"{DIR}/limits/b.JSON", {}), f"{DIR}/limits", "b.JSON"),
        (lambda root: (root / DIR / "limits" / "sub").mkdir(), f"{DIR}/limits", "sub"),
        # 大小文字を区別しないファイルシステムには作れないため、Linux の CI だけで確かめる
        pytest.param(
            _text(f"{DIR}/causes/CI_pending.json", {"meaning": "x"}),
            f"{DIR}/causes",
            "CI_pending",
            marks=pytest.mark.skipif("sys.platform != 'linux'"),
        ),
        (lambda root: (root / ITEM).write_bytes(b"\xff"), ITEM, "読めない"),
        (lambda root: _link_outside(root, ITEM), ITEM, "読めない"),
        (lambda root: (root / LIST).write_bytes(b"\xff"), LIST, "一覧を読めない"),
        (lambda root: _link_outside(root, LIST), LIST, "一覧を読めない"),
        (lambda root: (root / SCHEMAS[0]).unlink(), SCHEMAS[0], "スキーマを読めない"),
        (_text(SCHEMAS[0], []), SCHEMAS[0], "スキーマを読めない"),
    ],
    ids=[
        "extension",
        "subdirectory",
        "case",
        "not-utf8",
        "linked-outside",
        "list-not-utf8",
        "list-linked-outside",
        "no-schema",
        "broken-schema",
    ],
)
def test_names_and_files_the_reader_refuses_are_one_finding(root, rules, break_, path, named):
    break_(root)
    scope = _scope(root)
    run.unparsable(scope)
    found = contracts_split.check(scope, rules)
    assert _paths(root, found) == [path]
    assert named in found[0].detail


@pytest.mark.parametrize(
    ("value", "paths"),
    [
        (None, []),
        ("{", [LIST]),
        ({"rows": [{"id": "limits"}]}, [LIST]),
        ({"rows": [{"id": "../limits", "source_key": "policies"}]}, [LIST]),
    ],
    ids=["no-list", "not-json", "no-source-key", "outside"],
)
def test_only_a_plugin_with_the_list_is_checked_and_a_broken_list_is_a_finding(root, rules, value, paths):
    # 一覧の無いプラグインには当てない。自分（NDF）の一覧を代わりに読まない
    shutil.rmtree(root / DIR / "limits")
    (root / LIST).unlink() if value is None else _text(LIST, value)(root)
    assert _paths(root, _check(root, rules)) == paths


@pytest.mark.parametrize(("only", "applied"), [(contracts_split.RULE, 1), ("smells", 0)])
def test_the_check_command_applies_the_rule_only_when_selected(root, rules, monkeypatch, only, applied):
    monkeypatch.setattr(lint, "run_ruff", lambda rules, paths: [])
    # コントラクトの全部を要る規則は、この入力では当てない
    for module, name in ((run.child_deps, "apply"), (run.facts, "check"), (run.spec_docs, "check")):
        monkeypatch.setattr(module, name, lambda *args: [])
    calls = []
    rule = run.SELECTED_RULES[contracts_split.RULE]
    monkeypatch.setitem(run.SELECTED_RULES, contracts_split.RULE, lambda *args: calls.append(args) or rule(*args))
    _text(f"{DIR}/limits.json", {})(root)
    found = run.select(_scope(root), rules, [only])
    assert len(calls) == applied
    assert _paths(root, found) == [f"{DIR}/limits.json"] * applied
