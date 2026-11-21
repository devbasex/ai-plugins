"""`scripts/measure/ai-branching.py` の `plan_call` の今の振る舞いを固定する（I-004 の直しの前）。

固定する点:
- `--from` は `--from X` と `--from=X` のどちらでも `from` に出た順で入る
- queue の既定の置き場は先頭のプランの絶対のパスの拡張子を `.queue` に替えたもの。`--queue-dir` があればそちら
"""

import importlib.util
from pathlib import Path

import pytest

AB = Path(__file__).resolve().parent.parent / "measure" / "ai-branching.py"


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("ai_branching_plan_call_under_test", AB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(ab, cmd: str, cwd: str | None = "/w"):
    return ab.plan_call(cmd.split(), cwd)


# ---------- --from の記録 ----------


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("ndf plan next a.json --from s1", ["s1"]),
        ("ndf plan next a.json --from=s1", ["s1"]),
        ("ndf plan queue a.json --from s1 --from=s2", ["s1", "s2"]),
        ("ndf plan queue a.json --from=s1 --from s2", ["s1", "s2"]),
        ("ndf plan next a.json", []),
        ("ndf plan next a.json --from", []),
    ],
)
def test_from_records_both_forms_in_order(ab, cmd, expected):
    assert call(ab, cmd)["from"] == expected


def test_from_value_is_not_taken_as_plan(ab):
    got = call(ab, "ndf plan next a.json --from s1")
    assert got["plans"] == ["a.json"]
    got = call(ab, "ndf plan next a.json --from=s1")
    assert got["plans"] == ["a.json"]


def test_from_does_not_mark_answer_or_resume(ab):
    got = call(ab, "ndf plan queue a.json --from s1")
    assert got["answer"] is False
    assert got["resume"] is False
    assert call(ab, "ndf plan queue a.json --resume")["resume"] is True


# ---------- queue の既定の置き場 ----------


@pytest.mark.parametrize(
    "cmd, cwd, expected",
    [
        ("ndf plan queue a.json", "/w", "/w/a.queue"),
        ("ndf plan queue sub/../p/a.json", "/w", "/w/p/a.queue"),
        ("ndf plan queue /abs/a.json", "/w", "/abs/a.queue"),
        ("ndf plan queue /abs/a.json", None, "/abs/a.queue"),
        ("ndf plan queue a.json", None, "a.queue"),
        ("ndf plan queue a.json b.json", "/w", "/w/a.queue"),
        ("ndf plan queue a.json --then b.json c.json", "/w", "/w/a.queue"),
        ("ndf plan queue a", "/w", "/w/a.queue"),
        ("ndf plan queue a.plan.json", "/w", "/w/a.plan.queue"),
    ],
)
def test_queue_default_is_beside_first_plan(ab, cmd, cwd, expected):
    got = call(ab, cmd, cwd)
    assert got["queue"] == expected
    assert got["queue"] == ab.os.path.splitext(got["paths"][0])[0] + ab.QUEUE_SUFFIX


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("ndf plan queue a.json --queue-dir q", "/w/q"),
        ("ndf plan queue a.json --queue-dir=/x/q", "/x/q"),
    ],
)
def test_queue_dir_overrides_default(ab, cmd, expected):
    assert call(ab, cmd)["queue"] == expected


@pytest.mark.parametrize(
    "cmd",
    ["ndf plan next a.json", "ndf plan wait a.json", "ndf plan queue", "ndf plan queue --resume"],
)
def test_queue_is_none_outside_queue_with_plans(ab, cmd):
    assert call(ab, cmd)["queue"] is None


# ---------- 周りの欄（直しで変わらないこと） ----------


def test_queue_fields_with_then(ab):
    got = call(ab, "ndf plan queue a.json --then b.json --from s1 --state-dir st")
    assert got["sub"] == "queue"
    assert got["plans"] == ["a.json", "b.json"]
    assert got["paths"] == ["/w/a.json", "/w/b.json"]
    # queue は --state-dir を渡しても省いた形の鍵
    assert got["given"] == [None, None]
    assert got["ids"] == [f"/w/a.json|{ab.OMITTED}", f"/w/b.json|{ab.OMITTED}"]
    assert got["from"] == ["s1"]
    assert got["cwd"] == "/w"


def test_next_with_state_dir_keys_by_given(ab):
    got = call(ab, "ndf plan next a.json --state-dir st --from=s1")
    assert got["given"] == ["/w/st"]
    assert got["ids"] == ["/w/a.json|/w/st"]
    assert got["queue"] is None
    assert got["from"] == ["s1"]


@pytest.mark.parametrize("cmd", ["ndf plan show a.json", "ndf loop next", "git status", "ndf plan"])
def test_non_plan_commands_are_none(ab, cmd):
    assert call(ab, cmd) is None
