from __future__ import annotations

import importlib.util
import json
import pathlib


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


def _load_monitor_outcome():
    spec = importlib.util.spec_from_file_location(
        "ndf_lib_monitor_outcome_unit", LIB / "monitor_outcome.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_read_journal_returns_empty_when_file_is_missing(tmp_path):
    assert _load_monitor_outcome().read_journal(tmp_path) == []


def test_read_journal_ignores_malformed_and_non_object_rows(tmp_path):
    valid = {"status": "OK"}
    (tmp_path / "monitor-outcomes.jsonl").write_text(
        json.dumps(valid) + "\n{not json\n" + json.dumps(["not", "an", "object"]) + "\n",
        encoding="utf-8",
    )

    assert _load_monitor_outcome().read_journal(tmp_path) == [valid]


def test_append_journal_preserves_order_and_utf8_content(tmp_path):
    mod = _load_monitor_outcome()
    outcomes = [
        {"agent": "codex", "status": "OK", "detail": "完了"},
        {"agent": "kiro", "status": "EARLY_ERROR", "detail": "認証が必要"},
    ]

    for outcome in outcomes:
        mod.append_journal(tmp_path, outcome)

    assert mod.read_journal(tmp_path) == outcomes


# ---------- 理由の語彙と起動し直しの可否（#729 の AC1 / AC10） ----------

import pytest  # noqa: E402

MONITOR_REASONS = ("timeout", "stalled", "early_error", "usage_limit", "cli_timeout", "pidfile_bad")


def test_reasons_are_the_nine_words():
    assert _load_monitor_outcome().REASONS == (
        "ok", "timeout", "stalled", "early_error", "missing", "pidfile_bad",
        "usage_limit", "cli_timeout", "unparsable",
    )


@pytest.mark.parametrize(("status", "reason"), [
    ("OK", "ok"), ("TIMEOUT", "timeout"), ("STALLED", "stalled"),
    ("EARLY_ERROR", "early_error"), ("NO_RESULT", "missing"), ("PIDFILE_BAD", "pidfile_bad"),
])
def test_reason_for_keeps_the_six_status_mappings(status, reason):
    assert _load_monitor_outcome().reason_for(status) == reason


def test_only_usage_limit_forbids_relaunching_the_same_agent():
    mod = _load_monitor_outcome()
    assert mod.NO_RELAUNCH_REASONS == frozenset({"usage_limit"})
    for reason in mod.REASONS:
        assert mod.relaunch_same_agent(reason) is (reason != "usage_limit"), reason


# ---------- 結末を 1 つの値として読む（#729 の AC8 / AC9 / AC11） ----------

STEM = "kiro-review-pr7"


def _place(tmp_path, *, monitor=None, result=None):
    """監視の結果ファイルと結果ファイルを置く。`monitor` / `result` は書く中身（None は置かない）。"""
    if monitor is not None:
        (tmp_path / f"{STEM}-monitor.json").write_text(monitor, encoding="utf-8")
    if result is not None:
        (tmp_path / f"{STEM}-result.json").write_text(result, encoding="utf-8")


def _monitor_json(reason, detail="early error (fatal) in err.log: Monthly request limit reached"):
    return json.dumps({"agent": "kiro", "stem": STEM, "status": "EARLY_ERROR",
                       "reason": reason, "detail": detail})


@pytest.mark.parametrize("monitor", [None, "{broken", *[_monitor_json(r) for r in ("ok", "usage_limit")]])
def test_readable_result_object_wins_whatever_the_monitor_says(tmp_path, monitor, capsys):
    mod = _load_monitor_outcome()
    _place(tmp_path, monitor=monitor, result='{"event": "APPROVE"}')

    got = mod.read_launch_outcome(tmp_path, STEM)

    assert got.payload == {"event": "APPROVE"}
    assert got.reason is None
    assert got.relaunch_same_agent is True
    if monitor and monitor != "{broken":
        assert got.monitor == json.loads(monitor)
        assert got.detail == json.loads(monitor)["detail"]
    else:
        assert got.monitor is None
        assert got.detail == ""
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("reason", MONITOR_REASONS)
@pytest.mark.parametrize("result", [None, "", "[1, 2]", "{broken"])
def test_monitor_reason_is_taken_when_the_monitor_knows_why(tmp_path, reason, result):
    mod = _load_monitor_outcome()
    _place(tmp_path, monitor=_monitor_json(reason), result=result)

    got = mod.read_launch_outcome(tmp_path, STEM)

    assert got.payload is None
    assert got.reason == reason
    assert got.relaunch_same_agent is (reason != "usage_limit")
    assert got.monitor["reason"] == reason
    assert got.detail == "early error (fatal) in err.log: Monthly request limit reached"


@pytest.mark.parametrize("monitor", [None, "{broken", '"scalar"', _monitor_json("ok"), _monitor_json("missing")])
@pytest.mark.parametrize(("result", "reason"), [
    (None, "missing"), ("", "missing"), ("   \n", "missing"),
    ("[1, 2]", "unparsable"), ("{broken", "unparsable"), ('"text"', "unparsable"),
])
def test_missing_or_unparsable_is_decided_by_the_result_file(tmp_path, monitor, result, reason, capsys):
    mod = _load_monitor_outcome()
    _place(tmp_path, monitor=monitor, result=result)

    got = mod.read_launch_outcome(tmp_path, STEM)

    assert got.payload is None
    assert got.reason == reason
    assert got.relaunch_same_agent is True
    if monitor in (None, "{broken", '"scalar"'):
        assert got.monitor is None
        # 監視の詳細が無いときは、読めなかった理由を 1 文で持つ
        assert got.detail and got.detail.strip() == got.detail
    else:
        assert got.monitor == json.loads(monitor)
        assert got.detail == json.loads(monitor)["detail"]
    assert capsys.readouterr() == ("", "")


def test_detail_without_monitor_explains_why_the_result_is_unusable(tmp_path):
    mod = _load_monitor_outcome()
    (tmp_path / f"{STEM}-result.json").write_text("[1]", encoding="utf-8")
    assert mod.read_launch_outcome(tmp_path, STEM).detail != mod.read_launch_outcome(
        tmp_path, "other-stem").detail


def test_result_path_overrides_the_default_location(tmp_path):
    mod = _load_monitor_outcome()
    other = tmp_path / "elsewhere.json"
    other.write_text('{"verdict": "ok"}', encoding="utf-8")
    _place(tmp_path, result="[1]")

    got = mod.read_launch_outcome(tmp_path, STEM, result_path=other)

    assert got.payload == {"verdict": "ok"}


def test_result_path_that_is_a_directory_does_not_raise(tmp_path, capsys):
    mod = _load_monitor_outcome()
    (tmp_path / f"{STEM}-result.json").mkdir()

    got = mod.read_launch_outcome(tmp_path, STEM)

    assert got.payload is None
    assert got.reason == "missing"
    assert capsys.readouterr() == ("", "")


def test_launch_outcome_is_frozen(tmp_path):
    mod = _load_monitor_outcome()
    got = mod.read_launch_outcome(tmp_path, STEM)
    with pytest.raises(Exception):
        got.reason = "ok"
