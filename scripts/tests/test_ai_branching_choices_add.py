"""`scripts/measure/ai-branching.py` の `_Choices.add` の今の振る舞いを固定する（I-005 の直しの前）。

固定する点:
- 行が None（JSON として読めない行）は `rejected` に 1 足し、何も数えない
- `at` を時刻に直せない行（無い・文字列でない・日付だけ・時差が無い・無効な日付）は `rejected` に 1 足し、何も数えない
- 期間の外の行はスキーマに合わなくても `rejected` に入れない（期間の確かめがスキーマより先）
- 期間の中でスキーマに合わない行は `rejected` に 1 足す。合う行は規則の番号と `chooser` で数える
- `add` は `files` を変えない
"""

import datetime
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
AB = HERE.parent / "measure" / "ai-branching.py"
SCHEMA = REPO / "plugins" / "ndf" / "contracts" / "schemas" / "route-chosen.schema.json"

SINCE = datetime.datetime.fromisoformat("2026-11-18T00:00:00+09:00")
UNTIL = datetime.datetime.fromisoformat("2026-11-21T00:00:00+09:00")


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("ai_branching_choices_add_under_test", AB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def choices(ab):
    return ab._Choices(json.loads(SCHEMA.read_text(encoding="utf-8")))


def _choice(at, rule: str = "R03", chooser: str = "conductor") -> dict:
    return {"event": "route_chosen", "at": at, "run_id": "r1", "rule": rule, "chooser": chooser, "from": "wait", "to": "gate"}


def test_none_row_is_rejected(choices):
    choices.add(None, SINCE, UNTIL)
    assert choices.out() == {"by_rule": {}, "rejected": 1, "files": 0}


@pytest.mark.parametrize(
    "row",
    [
        {k: v for k, v in _choice("x").items() if k != "at"},  # at が無い
        _choice(None),
        _choice(1763427600),  # 文字列でない
        _choice("2026-11-18"),  # 日付だけ
        _choice("2026-11-18T01:00:00"),  # 時差が無い
        _choice("2026-11-31T00:00:00Z"),  # 無効な日付
        _choice("not a moment"),
    ],
)
def test_row_whose_at_is_not_a_moment_is_rejected(choices, row):
    choices.add(row, SINCE, UNTIL)
    assert choices.out() == {"by_rule": {}, "rejected": 1, "files": 0}


def test_none_and_bad_at_each_add_one(choices):
    """None の行と時刻を読めない行は、どちらも 1 行につき 1 ずつ足される。"""
    for row in (None, _choice("2026-11-18T01:00:00"), None, _choice("bad")):
        choices.add(row, SINCE, UNTIL)
    assert choices.rejected == 4
    assert choices.by_rule == {}


@pytest.mark.parametrize("at", ["2026-11-17T14:59:59Z", "2026-11-20T15:00:00Z"])
def test_out_of_period_row_is_not_rejected_even_if_invalid(choices, at):
    choices.add(_choice(at, chooser="ai"), SINCE, UNTIL)
    assert choices.out() == {"by_rule": {}, "rejected": 0, "files": 0}


def test_in_period_invalid_row_is_rejected(choices):
    choices.add(_choice("2026-11-18T01:00:00Z", chooser="ai"), SINCE, UNTIL)
    assert choices.out() == {"by_rule": {}, "rejected": 1, "files": 0}


def test_in_period_valid_rows_are_counted(choices):
    choices.add(_choice("2026-11-17T15:00:00Z"), SINCE, UNTIL)  # since ちょうど
    choices.add(_choice("2026-11-18T01:00:00Z", chooser="script"), SINCE, UNTIL)
    choices.add(_choice("2026-11-18T01:00:00Z", rule="R13", chooser="human"), SINCE, UNTIL)
    assert choices.out() == {"by_rule": {"R03": {"conductor": 1, "script": 1}, "R13": {"human": 1}}, "rejected": 0, "files": 0}
