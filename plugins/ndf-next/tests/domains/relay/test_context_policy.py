"""再起動の方針の解決・判定の記録・見込みの較正（`ndf relay context resolve`・`record`・`calibrate`。#3569）。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.domains.records import files
from ndf.domains.relay import cli, context_policy, context_records, registry


def _setting(root, data) -> None:
    path = files.path(root, context_policy.setting_id())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_resolve_defaults_without_setting(tmp_path):
    got = context_policy.resolve(tmp_path, None, {})
    assert (got.policy, got.decide_from, got.limit, got.margin) == ("current", 200_000, 200_000, 50_000)
    assert got.sources["policy"] == "default" and got.config_error is None


def test_resolve_trial_from_setting_and_env_overrides(tmp_path):
    _setting(tmp_path, {"version": 1, "policy": "trial", "trial": {"decide_from": 150_000, "limit": 300_000}})
    got = context_policy.resolve(tmp_path, None, {})
    assert (got.policy, got.decide_from, got.limit) == ("trial", 150_000, 300_000)
    assert got.sources["limit"] == "config"
    lowered = context_policy.resolve(tmp_path, None, {"NDF_CONTEXT_LIMIT": "100000"})
    assert (lowered.decide_from, lowered.limit, lowered.sources["limit"]) == (100_000, 100_000, "env")
    cli_current = context_policy.resolve(tmp_path, "current", {"NDF_CONTEXT_POLICY": "trial"})
    assert (cli_current.policy, cli_current.sources["policy"]) == ("current", "cli")
    ignored = context_policy.resolve(tmp_path, None, {"NDF_CONTEXT_POLICY": "fast"})
    assert ignored.sources["ignored"] == "NDF_CONTEXT_POLICY=fast"


@pytest.mark.parametrize(
    "data",
    ["{not json", {"version": 2}, {"policy": "fast"}, {"trial": []}, {"trial": {"decide_from": 5, "limit": 1}}, {"margin": -1}],
)
def test_broken_setting_falls_back_to_current(tmp_path, data):
    _setting(tmp_path, data)
    got = context_policy.resolve(tmp_path, None, {})
    assert got.policy == "current" and got.limit == 200_000
    assert got.config_error


def test_context_dir_follows_the_legacy_state_root(tmp_path):
    assert context_records.context_dir({"NDF_CONTEXT_DIR": str(tmp_path)}) == tmp_path
    env = {"NDF_STATE_HOME": str(tmp_path / "s")}
    assert context_records.context_dir(env) == tmp_path / "s" / "context"
    assert registry.legacy_root(env) == identity.legacy_state_root(env) / "relay"
    assert identity.legacy_state_root({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "ndf"


def test_recorded_work_normalizes_issue_numbers():
    assert context_records.recorded_work("#830 と #829（2026-11-15・v1.2.3）", None) == "#829 #830"
    assert context_records.recorded_work("設計", "sid") == ""
    assert context_records.recorded_work("設計", None) == "設計"


def test_append_and_read_skip_broken_lines(tmp_path):
    env = {"NDF_CONTEXT_DIR": str(tmp_path)}
    applied = context_policy.resolve(None, None, {})
    late = context_records.record_row("s1", "#1", applied, context_records.MARK_HANDOFF, None) | {"at": "2026-11-15T02:00:00+00:00"}
    early = context_records.record_row("s1", "#1", applied, None, "手動") | {"at": "2026-11-15T01:00:00+00:00"}
    path = context_records.append("o__r", late, env)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"cut\n')
    context_records.append("o__r", early, env)
    rows, skipped = context_records.read("o__r", env)
    assert [r["at"] for r in rows] == [early["at"], late["at"]]
    assert skipped == 1
    assert rows[0]["verdict"] == "再起動" and rows[0]["reason"] == "手動" and rows[0]["policy"] == "current"


def _transcript(path, calls) -> str:
    lines = [{"type": "user", "message": {"content": "続きは .ndf/handoff/a.md から"}}]
    for i, (ts, ctx) in enumerate(calls):
        usage = {"input_tokens": ctx, "output_tokens": 10, "cache_read_input_tokens": 0}
        lines.append({"type": "assistant", "timestamp": ts, "message": {"id": f"m{i}", "model": "x", "usage": usage}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def test_estimate_counts_samples_and_needs_three(tmp_path):
    rows = []
    for n in range(3):
        t = _transcript(tmp_path / f"t{n}.jsonl", [("2026-11-15T01:00:00Z", 100 + n), ("2026-11-15T01:10:00Z", 200 + n)])
        rows.append({"at": "2026-11-15T01:05:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_RESTORED})
        rows.append({"at": "2026-11-15T01:06:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_HANDOFF})
    got = context_records.estimate(rows, None)
    assert got["samples"]["restore"] == 3 and got["samples"]["handoff"] == 3
    assert got["restore_context"] == 101.0 and got["restore_calls"] == 1.0
    assert got["handoff_calls"] == 1.0 and got["handoff_output"] == 10.0
    assert context_records.estimate(rows[:4], None)["restore_context"] is None


def test_calibrate_writes_estimates_and_keeps_old_on_failure(tmp_path, monkeypatch):
    env = {"NDF_CONTEXT_DIR": str(tmp_path)}
    monkeypatch.setattr(context_records, "repo_key", lambda root: "o__r")
    body = context_records.calibrate(None, 30, 0.0, env)
    path = context_records.estimates_path("o__r", env)
    assert json.loads(path.read_text(encoding="utf-8"))["samples"] == body["samples"]
    path.parent.chmod(0o500)
    try:
        with pytest.raises(OSError):
            context_records.calibrate(None, 30, 0.0, env)
    finally:
        path.parent.chmod(0o700)
    assert path.is_file()


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_record_rejects_a_reason_outside_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(tmp_path))
    assert cli.main(["context", "record", "--verdict", "再起動", "--reason", "気分", "--work", "#1"]) == 2
    assert cli.main(["context", "record", "--verdict", "再起動", "--work", "#1"]) == 2
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--reason", "手動", "--work", "#1"]) == 2
    capsys.readouterr()
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--work", "#1"]) == 0
    assert _last(capsys)["items"][0]["event"] == "引継ぎ開始"


def test_cli_record_fails_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(blocked))
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--work", "#1"]) == 1
    assert _last(capsys)["status"] == "stopped"


def test_estimate_samples_restore_handoff_and_stage_segments(tmp_path):
    """現状固定（I-005）: 復元・引継ぎ・工程のセグメントの標本の集め方と、見込みの値の形。"""
    calls = ["2026-11-15T01:00:00Z", "2026-11-15T01:10:00Z", "2026-11-15T01:20:00Z", "2026-11-15T03:00:00Z"]
    rows = []
    for n in range(3):
        sid = f"s{n}"
        t = _transcript(tmp_path / f"t{n}.jsonl", [(ts, 100 + n) for ts in calls])
        rows += [
            {"at": "2026-11-15T00:59:00Z", "session_id": sid, "transcript": t, "stage": "design", "verdict": context_policy.CONTINUE},
            {"at": "2026-11-15T01:05:00Z", "session_id": sid, "event": context_records.MARK_RESTORED},
            {"at": "2026-11-15T01:12:00Z", "session_id": sid, "event": context_records.MARK_HANDOFF},
            {"at": "2026-11-15T01:15:00Z", "session_id": sid, "stage": "impl", "verdict": context_policy.CONTINUE},
            {"at": "2026-11-15T05:00:00Z", "session_id": sid, "stage": "review", "verdict": context_policy.CONTINUE},
        ]
    plain = tmp_path / "plain.jsonl"
    plain.write_text(json.dumps({"type": "user", "message": {"content": "始める"}}) + "\n", encoding="utf-8")
    rows += [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s9", "transcript": str(plain)},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s9", "event": context_records.MARK_RESTORED},
    ]
    rows.sort(key=lambda r: r["at"])
    got = context_records.estimate(rows, None)
    got.pop("at")
    assert got == {
        "restore_context": 101.0,
        "restore_calls": 1.0,
        "handoff_calls": 2.0,
        "handoff_output": 20.0,
        "stage_calls": {"design": 2.0, "*": 2.0, "impl": 2.0},
        "stage_expiries": {"design": 0.0, "*": 0.5, "impl": 1.0},
        "samples": {"restore": 3, "handoff": 3, "stage:design": 3, "stage:*": 6, "stage:impl": 3},
    }
    late = context_records.estimate(rows, context_records.parse_ts("2026-11-15T06:00:00Z"))
    late.pop("at")
    assert late == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {},
        "stage_expiries": {},
        "samples": {"restore": 0, "handoff": 0},
    }


def test_estimate_stage_segments_current_behavior_edges(tmp_path):
    """現状固定（I-026）: 工程の区間の切り方の端。根拠は仕様ではなく今の出力。

    - 区切りは工程つきの「継続」の行だけ（工程の無い行・別の判定・読めない時刻は区切らない）。行の並びは時刻で並べ直す
    - 区間は [始まり, 次の区切り) で、始まりちょうどの呼び出しは入る。時刻の無い呼び出しは数えない
    - 間隔がちょうど 1 時間はキャッシュ切れに数えず、1 秒でも超えれば数える
    - 呼び出しの無い区間と最後の区間は標本にしない。同じ工程の区間は別の標本として足す
    """
    calls = [
        "2026-11-15T01:00:00Z",
        "2026-11-15T01:10:00Z",
        "2026-11-15T02:10:00Z",
        "2026-11-15T03:10:00Z",
        "2026-11-15T04:10:01Z",
        None,
        "2026-11-15T04:20:00Z",
        "2026-11-15T06:00:00Z",
    ]
    lines: list[dict] = [{"type": "user", "message": {"content": "始める"}}]
    for i, ts in enumerate(calls):
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}
        entry: dict = {"type": "assistant", "message": {"id": f"m{i}", "model": "x", "usage": usage}}
        if ts is not None:
            entry["timestamp"] = ts
        lines.append(entry)
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    go = context_policy.CONTINUE
    rows = [
        {"at": "2026-11-15T04:30:00Z", "session_id": "s", "stage": "empty", "verdict": go},
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": str(t), "stage": "a", "verdict": go},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s", "verdict": go},
        {"at": "2026-11-15T01:06:00Z", "session_id": "s", "stage": "x", "verdict": "再起動"},
        {"at": "not a time", "session_id": "s", "stage": "y", "verdict": go},
        {"at": "2026-11-15T02:10:00Z", "session_id": "s", "stage": "b", "verdict": go},
        {"at": "2026-11-15T04:40:00Z", "session_id": "s", "stage": "a", "verdict": go},
        {"at": "2026-11-15T07:00:00Z", "session_id": "s", "stage": "last", "verdict": go},
    ]
    got = context_records.estimate(rows, None)
    got.pop("at")
    # 区間: a=[00:59,02:10) 2 呼び出し・切れ 0 / b=[02:10,04:30) 4 呼び出し・切れ 1 / empty は空 / a=[04:40,07:00) 1 呼び出し / last は最後
    assert got == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {"a": 1.5, "*": 2.0, "b": 4.0},
        "stage_expiries": {"a": 0.0, "*": 0.0, "b": 1.0},
        "samples": {"restore": 0, "handoff": 0, "stage:a": 2, "stage:*": 3, "stage:b": 1},
    }
