"""設定で選べる値が基準から黙って減っていないかの検査（`scripts/check-ndf-choices.py`。#3617 の受け入れ条件 5〜7）。

実物のコントラクトは書き換えず、一時ディレクトリへ実物を写した木を作ってそこを検査させる。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("check_ndf_choices", REPO_ROOT / "scripts/check-ndf-choices.py")
assert _SPEC is not None and _SPEC.loader is not None
choices = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(choices)

FLOWS = "plugins/ndf/contracts/domains/branch-flows.json"
PROJECT = "plugins/ndf/contracts/schemas/ndf-settings/project.schema.json"
FORMS = "plugins/ndf/skills/release/references"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """基準と、その 3 つの出所だけを写した木。"""
    for rel in (choices.BASELINE, FLOWS, PROJECT):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, tmp_path / rel)
    (tmp_path / FORMS).mkdir(parents=True)
    for path in (REPO_ROOT / FORMS).glob("form-*.md"):
        (tmp_path / FORMS / path.name).write_text("x\n", encoding="utf-8")
    return tmp_path


def _baseline(tree: Path) -> dict:
    return json.loads((tree / choices.BASELINE).read_text(encoding="utf-8"))


def _write_baseline(tree: Path, data: dict) -> None:
    (tree / choices.BASELINE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _drop_flow(tree: Path, value: str) -> None:
    data = json.loads((tree / FLOWS).read_text(encoding="utf-8"))
    data["rows"] = [r for r in data["rows"] if r["id"] != value]
    (tree / FLOWS).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _drop_delivery(tree: Path, value: str) -> None:
    data = json.loads((tree / PROJECT).read_text(encoding="utf-8"))
    enum = data["$defs"]["Delivery"]["properties"]["kind"]["enum"]
    enum.remove(value)
    (tree / PROJECT).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _drop_form(tree: Path, value: str) -> None:
    (tree / FORMS / f"form-{value}.md").unlink()


DROPS = [
    ("branch-flows", "release-branch", _drop_flow),
    ("delivery-kind", "manual", _drop_delivery),
    ("release-form", "slide", _drop_form),
]


def test_the_real_contracts_match_the_baseline(capsys):
    assert choices.main([]) == 0
    assert "branch-flows: " in capsys.readouterr().out


def test_unchanged_copy_passes(tree):
    assert choices.main(["--root", str(tree)]) == 0


@pytest.mark.parametrize(("sid", "value", "drop"), DROPS)
def test_a_removed_value_fails_and_names_the_value(tree, capsys, sid, value, drop):
    """値を 1 つ外すと 1 で落ち、外れた値と足せば通る例外の行を出す（受け入れ条件 5・7。I4）。"""
    drop(tree, value)
    assert choices.main(["--root", str(tree)]) == 1
    err = capsys.readouterr().err
    assert f"集合 {sid} から {value} が減った" in err and '"issue"' in err


@pytest.mark.parametrize(("sid", "value", "drop"), DROPS)
def test_an_exception_with_an_issue_lets_the_removal_pass(tree, sid, value, drop):
    """外れた値と Issue の番号を持つ例外を足すと通る（受け入れ条件 6・7）。"""
    drop(tree, value)
    data = _baseline(tree)
    data["removed"] = [{"set": sid, "value": value, "issue": 3617, "reason": "試験"}]
    _write_baseline(tree, data)
    assert choices.main(["--root", str(tree)]) == 0


@pytest.mark.parametrize(("sid", "value", "drop"), DROPS)
@pytest.mark.parametrize("broken", [{"reason": "試験"}, {"issue": "3617", "reason": "試験"}, {"issue": 3617, "reason": " "}])
def test_an_exception_without_an_issue_or_reason_fails(tree, capsys, sid, value, drop, broken):
    """Issue の番号の無い例外・理由の空の例外では 2 で落ちる（受け入れ条件 6。I5）。"""
    drop(tree, value)
    data = _baseline(tree)
    data["removed"] = [{"set": sid, "value": value, **broken}]
    _write_baseline(tree, data)
    assert choices.main(["--root", str(tree)]) == 2
    assert "removed[0]" in capsys.readouterr().err


def test_an_exception_whose_value_still_exists_fails(tree, capsys):
    data = _baseline(tree)
    data["removed"] = [{"set": "delivery-kind", "value": "auto", "issue": 1, "reason": "試験"}]
    _write_baseline(tree, data)
    assert choices.main(["--root", str(tree)]) == 2
    assert "delivery-kind/auto" in capsys.readouterr().err


def test_an_added_value_fails_until_the_baseline_knows_it(tree, capsys):
    """基準に無い値がコントラクトに増えると 1 で落ち、基準へ足す行を出す（I6）。"""
    (tree / FORMS / "form-kiosk.md").write_text("x\n", encoding="utf-8")
    assert choices.main(["--root", str(tree)]) == 1
    assert "集合 release-form に kiosk が増えた" in capsys.readouterr().err
    data = _baseline(tree)
    data["sets"][2]["values"].append("kiosk")
    _write_baseline(tree, data)
    assert choices.main(["--root", str(tree)]) == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=2),
        lambda d: d["sets"].append(dict(d["sets"][0])),
        lambda d: d["sets"][0].update(source={"rows": "x.json"}),
        lambda d: d["sets"][0].update(values=[]),
        lambda d: d.update(removed=[{"set": "nothing", "value": "x", "issue": 1, "reason": "r"}]),
    ],
)
def test_a_broken_baseline_fails(tree, mutate):
    data = _baseline(tree)
    mutate(data)
    _write_baseline(tree, data)
    assert choices.main(["--root", str(tree)]) == 2


def test_an_unreadable_source_fails_instead_of_reading_zero(tree, capsys):
    """出所が無い・glob が 0 件なら、全部減ったと読まずに 2 で落ちる。"""
    for path in (tree / FORMS).glob("form-*.md"):
        path.unlink()
    assert choices.main(["--root", str(tree)]) == 2
    assert "集合 release-form の出所を読めない" in capsys.readouterr().err


def test_json_pointer_unescapes_and_refuses_missing_keys():
    assert choices._pointer({"a/b": {"~c": [5]}}, "/a~1b/~0c/0") == 5
    with pytest.raises(choices.SourceError):
        choices._pointer({"a": 1}, "/b")
