"""規則 contracts-split: 分けた表が 1 項目 1 ファイルのままであること（#3687 の受け入れ条件 2）。"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from ndf import contracts
from ndf.checks import contracts_split, lint, run
from ndf.checks.model import CheckError, Finding, Scope
from ndf.contracts import causes, exit_codes
from ndf.identity import PluginIdentity

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DIR = contracts.DEFAULT_DIR.name
LIST = f"{DIR}/{contracts.SPLIT_TABLES}"
ITEM = f"{DIR}/limits/test-run.test.json"
CAUSE = f"{DIR}/causes/ci_pending.json"
SCHEMAS = [f"{DIR}/{contracts.schema_path(table)}" for table in ("limits", "causes")]
RESULT_SCHEMA = f"{DIR}/{contracts.schema_path('result')}"


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


def _item_paths(root: Path, report: dict) -> list[str]:
    """結果 JSON の `items` のパス（プラグインの根からの相対）。"""
    return [(root.parents[1] / item["path"]).relative_to(root).as_posix() for item in report["items"]]


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
        ({"$ref": "#/$defs/variants/anyOf/0"}, []),
        ({"type": ["object", "null"]}, []),
        ({"anyOf": [{"type": "object", "required": ["keys"]}, {"type": "null"}]}, []),
        ({"type": "string"}, [ITEM]),
        ({"type": ["string", "null"]}, [ITEM]),
    ],
    ids=["object", "ref", "type-list", "any-of", "not-object", "not-object-list"],
)
def test_a_field_that_the_schema_allows_to_be_an_object_is_not_another_item(root, rules, field, paths):
    schema = {"properties": {"summary": {"type": "string"}, "endpoint": field}}
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
    rule = run.CONTRACT_RULES[contracts_split.RULE]
    monkeypatch.setitem(run.CONTRACT_RULES, contracts_split.RULE, lambda *args: calls.append(args) or rule(*args))
    _text(f"{DIR}/limits.json", {})(root)
    found = run.select(_scope(root), rules, [only])
    assert len(calls) == applied
    assert _paths(root, found) == [f"{DIR}/limits.json"] * applied


def test_the_rule_alone_does_not_collect_the_files_of_the_plugin(root, rules, monkeypatch):
    monkeypatch.setattr(run, "collect", lambda *args: pytest.fail("プラグインのファイルを集めた"))
    _text(f"{DIR}/limits.json", {})(root)
    found = run.select(Scope(root.parents[1], root, "p"), rules, [contracts_split.RULE])
    assert _paths(root, found) == [f"{DIR}/limits.json"]


def test_files_that_the_check_has_collected_are_not_read_again(root, rules):
    scope = _scope(root)
    for src in scope.sources:
        _ = src.text
    _text(ITEM, "{not json")(root)
    assert contracts_split.check(scope, rules) == []


@pytest.fixture
def own_contracts(root, monkeypatch):
    """検査の対象のプラグインの表を、結果 JSON を出す側のコントラクトの読み手にも読ませる。"""
    shutil.copy(PLUGIN_ROOT / RESULT_SCHEMA, root / RESULT_SCHEMA)
    monkeypatch.setattr(run.identity, "load", lambda: PluginIdentity(root, "p", "P_", root))
    monkeypatch.setattr(run, "_repo_root", lambda plugin_root: root.parents[1])
    contracts.use_dir(root / DIR)
    yield root
    contracts.use_dir(None)


@pytest.mark.parametrize(
    ("break_", "broken"),
    [
        (lambda root: _merge_back(root, "causes"), [f"{DIR}/causes", f"{DIR}/causes.json"]),
        # ディレクトリは残り、検査の結果に使う種類（check_failed）だけが無い
        (_text(f"{DIR}/causes.json", {"causes": [{"kind": "check_failed"}]}), [f"{DIR}/causes.json"]),
    ],
    ids=["all", "only-check-failed"],
)
def test_the_command_reports_the_findings_when_the_table_of_causes_is_one_file_again(own_contracts, capsys, last_result, break_, broken):
    # 結果 JSON の cause を確かめる停止理由の表そのものが、分ける前の 1 ファイルに戻っている
    root = own_contracts
    break_(root)
    assert run.main(["--only", contracts_split.RULE]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert report["cause"]["kind"] == "config_broken"
    assert _item_paths(root, report) == broken


def _read_causes(*args: object) -> list[Finding]:
    """停止理由の表を読む後続の規則（規約の規則）の代わり。"""
    try:
        causes.rows()
    except contracts.ContractError as exc:
        raise CheckError(str(exc)) from exc
    return []


def test_the_findings_remain_when_a_later_rule_stops_on_the_broken_table(own_contracts, monkeypatch, capsys, last_result):
    root = own_contracts
    monkeypatch.setattr(run.lint, "run_ruff", lambda rules, paths: [])
    for module, name in ((run.child_deps, "apply"), (run.facts, "check"), (run.words, "check"), (run.invariants, "check")):
        monkeypatch.setattr(module, name, lambda *args: [])
    monkeypatch.setattr(run.spec_docs, "check", _read_causes)
    _merge_back(root, "causes")
    assert run.main([]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert report["cause"]["kind"] == "config_broken"
    assert _item_paths(root, report) == [f"{DIR}/causes", f"{DIR}/causes.json"]


def _link_parent_outside(root: Path) -> None:
    """コントラクトの保存先はリポジトリの外を指し、一覧だけがプラグインの中へ戻る。"""
    outside = root.parents[2] / "outside"
    shutil.move(root / DIR, outside)
    (root / DIR).symlink_to(outside)
    shutil.move(outside / "domains", root / "domains")
    (outside / "domains").symlink_to(root / "domains")
    (outside / "limits" / "SECRET.txt").write_text("x", encoding="utf-8")


def _link_item_out_of_the_plugin(root: Path) -> None:
    """項目が、リポジトリの中だがプラグインの外のファイルを指す。"""
    outside = root.parents[1] / "outside.json"
    outside.write_text('{"SECRET": 1, "SECRET": 2}', encoding="utf-8")
    (root / ITEM).unlink()
    (root / ITEM).symlink_to(outside)


@pytest.mark.parametrize("collected", [True, False], ids=["collected", "alone"])
@pytest.mark.parametrize(
    ("break_", "paths"),
    [(_link_item_out_of_the_plugin, [ITEM]), (_link_parent_outside, [f"{DIR}/limits", f"{DIR}/causes"])],
    ids=["item-in-the-repository", "parent-directory"],
)
def test_nothing_outside_the_plugin_is_read_or_listed(root, rules, break_, paths, collected):
    break_(root)
    scope = _scope(root) if collected else Scope(root.parents[1], root, "p")
    found = contracts_split.check(scope, rules)
    assert _paths(root, found) == paths
    assert "SECRET" not in str(found)


@pytest.mark.parametrize("linked", [True, False], ids=["linked-outside", "not-utf8"])
def test_the_cause_of_the_result_is_not_read_from_outside_or_from_broken_bytes(own_contracts, capsys, last_result, linked):
    root = own_contracts
    rel = f"{DIR}/causes/check_failed.json"
    outside = root.parents[2] / "outside.json"
    outside.write_text('{"route": "SECRET"}', encoding="utf-8")
    if linked:
        (root / rel).symlink_to(outside)
    else:
        (root / rel).write_bytes(b"\xff")
    assert run.main(["--only", contracts_split.RULE]) == exit_codes.unreadable_code()
    out = capsys.readouterr()
    report = last_result(out.out)
    assert report["cause"]["kind"] == "config_broken"
    assert _item_paths(root, report) == [rel]
    assert "SECRET" not in out.out + out.err
