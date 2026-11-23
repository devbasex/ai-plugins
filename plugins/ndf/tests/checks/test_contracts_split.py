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


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """分ける表の一覧と、一覧が挙げる実物の表を置いたプラグイン（規則はファイルしか読まないので git を作らない）。"""
    top = tmp_path / "repo" / "plugins" / "p"
    (top / LIST).parent.mkdir(parents=True)
    shutil.copy(PLUGIN_ROOT / LIST, top / LIST)
    shutil.copy(PLUGIN_ROOT / "pyproject.toml", top / "pyproject.toml")
    for table in contracts.split_tables():
        shutil.copytree(PLUGIN_ROOT / DIR / table, top / DIR / table)
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


def _link_outside(root: Path) -> None:
    outside = root.parents[2] / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / ITEM).unlink()
    (root / ITEM).symlink_to(outside)


def _link_directory_outside(root: Path) -> None:
    shutil.move(root / DIR / "causes", root.parents[2] / "outside")
    (root / DIR / "causes").symlink_to(root.parents[2] / "outside")


def _text(rel: str, value: object) -> Callable[[Path], object]:
    """`rel` の中身を書き換える操作。文字列はそのまま、ほかは JSON にして書く。"""
    body = value if isinstance(value, str) else json.dumps(value)
    return lambda root: (root / rel).write_text(body, encoding="utf-8")


def test_the_split_tables_have_no_findings(root, rules):
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
        (CAUSE, [{"meaning": "a"}, {"meaning": "b"}]),
        (ITEM, "{not json"),
        (ITEM, '{"seconds": 1, "seconds": 2}'),
        (ITEM, "1"),
    ],
    ids=["source-key", "source-key-and-more", "source-list", "key-to-item", "item-in-item", "list", "not-json", "same-key-twice", "number"],
)
def test_a_file_that_is_not_one_item_is_a_finding(root, rules, rel, value):
    _text(rel, value)(root)
    assert _paths(root, _check(root, rules)) == [rel]


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
        (_link_outside, ITEM, "読めない"),
    ],
    ids=["extension", "subdirectory", "case", "not-utf8", "linked-outside"],
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


def test_the_check_command_applies_the_rule(root, rules, monkeypatch):
    monkeypatch.setattr(lint, "run_ruff", lambda rules, paths: [])
    # コントラクトの全部を要る規則は、この入力では当てない
    for module, name in ((run.child_deps, "apply"), (run.facts, "check"), (run.spec_docs, "check")):
        monkeypatch.setattr(module, name, lambda *args: [])
    _text(f"{DIR}/limits.json", {})(root)
    found = run.select(_scope(root), rules, [contracts_split.RULE])
    assert _paths(root, found) == [f"{DIR}/limits.json"]
