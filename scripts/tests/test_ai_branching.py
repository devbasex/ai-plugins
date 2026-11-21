"""`scripts/measure/ai-branching.py`（#3412 の Task 2・3）を、模した記録で確かめる。

- 退行: 今の規則の表（形 `before`）で、`main` の今のキーの値が変わらない
- 規則の表（`load_rules`）と分類の表（`load_measures`）の形。誤りは終了コード 2
- 期間は半開区間 `[since, until)` で、会話・選択の記録・旧い進捗・台帳のどれでも `until` ちょうどを数えない
- 前の形と後の形の同じ選択が、同じ規則の番号に 1 回ずつ数えられる（設計書のテスト設計の 7・8）

時刻の形は実データ（2026-11-21 に確かめた）を写す: 会話の `timestamp` は `2026-11-18T02:44:08.680Z`、旧い
`progress.jsonl` の `at` は `2026-11-18T03:41:38+00:00`、旧い台帳の `at` は `2026-11-09T16:54:14Z`、選択の記録の `at` は
`2026-12-01T01:00:00Z`。
"""

from __future__ import annotations

import ast
import datetime
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
AB = HERE.parent / "measure" / "ai-branching.py"
RULES = HERE.parent / "measure" / "ai-branching-rules.json"
MEASURES = HERE.parent / "measure" / "ai-branching-measures.json"
SCHEMA = REPO / "plugins" / "ndf" / "contracts" / "schemas" / "route-chosen.schema.json"
CHOSEN = REPO / "plugins" / "ndf" / "src" / "ndf" / "contracts" / "chosen.py"

# 後の値の期間の形（JST の日付の境）。until は UTC の 2026-11-20T15:00:00Z
SINCE_TEXT = "2026-11-18T00:00:00+09:00"
UNTIL_TEXT = "2026-11-21T00:00:00+09:00"
SINCE = datetime.datetime.fromisoformat(SINCE_TEXT)
UNTIL = datetime.datetime.fromisoformat(UNTIL_TEXT)
CONDUCTOR = {"isSidechain": False, "entrypoint": "cli"}
SUPERVISOR = {"isSidechain": False, "entrypoint": "sdk-cli"}


def load_ab():
    spec = importlib.util.spec_from_file_location("ai_branching", AB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _write(tmp: Path, name: str, data) -> Path:
    path = tmp / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _use(tid: str, at: str, name: str, inp: dict, role: dict | None = None) -> dict:
    return {
        "type": "assistant",
        "timestamp": at,
        **(role or {}),
        "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]},
    }


def _bash(tid: str, at: str, cmd: str, role: dict | None = None) -> dict:
    return _use(tid, at, "Bash", {"command": cmd}, role)


def _tool(tid: str, at: str, name: str, inp: dict, role: dict | None = None) -> dict:
    return _use(tid, at, name, inp, role)


def _result(tid: str, at: str, content, role: dict | None = None) -> dict:
    if isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    return {
        "type": "user",
        "timestamp": at,
        **(role or {}),
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]},
    }


def build(tmp: Path) -> dict[str, Path]:
    """旧い形の記録の見本（役割の欄を持たない会話）。"""
    ndf = tmp / ".ndf"
    state = tmp / "state"
    tr = tmp / "transcripts"
    _jsonl(
        state / "p1-state" / "progress.jsonl",
        [
            {"kind": "run", "at": "2026-10-13T23:00:00+00:00"},
            {"kind": "step", "at": "2026-10-14T01:00:00+00:00", "step": "test-all", "type": "work", "exit": 0, "next": "judge"},
            {"kind": "step", "at": "2026-10-14T01:01:00+00:00", "step": "judge", "type": "judge", "exit": 0, "decision": "fix"},
            {"kind": "step", "at": "2026-10-14T01:02:00+00:00", "step": "impl", "type": "work", "exit": 1, "next": "end"},
            {"kind": "attention", "at": "2026-10-14T01:03:00+00:00", "reason": "スプリント MVV を使えない"},
            {"kind": "run", "at": "2026-10-14T02:00:00+00:00", "from": "impl"},
            {"kind": "step", "at": "2026-10-14T02:01:00+00:00", "step": "impl", "type": "work", "exit": 0, "next": "end"},
        ],
    )
    _jsonl(
        ndf / "parallel" / "p4" / "ledger.jsonl",
        [
            {"kind": "ask", "at": "2026-10-14T03:00:00Z", "msg": {"source": "lane"}},
            {"kind": "resume", "at": "2026-10-14T03:01:00Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-10-13T03:01:00Z", "msg": {"phase": "confirm", "take": True}},
        ],
    )
    _jsonl(
        tr / "s1.jsonl",
        [
            _bash("t1", "2026-10-14T04:00:00.000Z", "python3 supervise.py queue a.json --resume"),
            _bash("t2", "2026-10-14T04:01:00.000Z", "gh pr checks 12 && gh pr merge 12"),
            _bash("t3", "2026-10-13T04:01:00.000Z", "gh pr checks 13"),
            _bash("t4", "2026-10-14T04:02:00.000Z", "ls"),
            _tool("t5", "2026-10-14T04:03:00.000Z", "AskUserQuestion", {"metadata": {"source": "ndf-ask:design:1"}}),
            _tool("t6", "2026-10-14T04:04:00.000Z", "SendMessage", {}),
            _tool("t7", "2026-10-14T04:05:00.000Z", "Write", {"file_path": "/x/plan.json", "content": '{"steps": []}'}),
            _result("t2", "2026-10-14T04:06:00.000Z", "結果: 止まった。基盤待ち"),
        ],
    )
    return {"ndf": ndf, "state": state, "transcripts": tr}


def run_main(paths: dict[str, Path], *extra: str, since: str = "2026-10-14T00:00:00Z", until: str = "2026-11-14T00:00:00Z", env=None):
    cmd = [sys.executable, str(AB), "--since", since, "--until", until, "--ndf", str(paths["ndf"]), "--state", str(paths["state"])]
    cmd += ["--transcripts", str(paths["transcripts"]), "--jobs", "1", *extra]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


# ---------- 退行: 今の規則の表で今のキーの値が変わらない ----------


EXPECTED = {
    "progress": {
        "plans": 1,
        "resume_by_previous_end": {"失敗の後・同じステップから": 1},
        "resume_from_step": {"impl": 1},
        "attention_by_reason": {"スプリント MVV を使えない": 1},
        "judge_by_decision": {"judge → fix": 1},
        "stopped_by_step_exit": {"impl exit=1": 1},
    },
    "ledger": {"kinds": {"ask": 1, "resume": 1}, "ask_by_source": {"lane": 1}, "resume": {"settle: rejected": 1}},
    "transcripts": {
        "files": 1,
        "bash_by_rule": {
            "queue の再開（queue --resume）": 1,
            "CI のチェックを手で読む（gh pr checks / gh run view）": 1,
            "マージを手で打つ（merge-when-green / gh pr merge）": 1,
        },
        "hand_written_plans": 1,
        "tools": {"AskUserQuestion（ndf-ask:design）": 1, "SendMessage": 1},
        "results_with_phrase": {"結果: 止まった": 1, "基盤待ち": 1},
    },
}


def test_main_keeps_current_keys_with_current_rules(tmp_path):
    p = run_main(build(tmp_path))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert list(out) == ["since", "until", "progress", "ledger", "transcripts", "forms"]
    assert {k: out["progress"][k] for k in EXPECTED["progress"]} == EXPECTED["progress"]
    assert out["ledger"] == EXPECTED["ledger"]
    assert {k: out["transcripts"][k] for k in EXPECTED["transcripts"]} == EXPECTED["transcripts"]


def test_main_skip_transcripts_keeps_keys(tmp_path):
    p = run_main(build(tmp_path), "--skip-transcripts")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert list(out) == ["since", "until", "progress", "ledger", "forms"]
    # 会話を読まない形では、会話が出所の規則は 0 でなく unmeasured に入る
    assert "R03" in out["forms"]["before"]["unmeasured"]
    assert out["forms"]["before"]["measures"]["G2"]["partial"] is True


# ---------- 規則の表 ----------


def test_load_rules_splits_rows_by_form():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    assert {r["form"] for r in rules} == {"before", "after"}
    before = [r for r in rules if r["form"] == "before"]
    after = [r for r in rules if r["form"] == "after"]
    assert sum(r["kind"] == "bash" for r in before) == 25
    assert {r["kind"] for r in before} == {"bash", "tool", "phrase"}
    assert {r["rule"] for r in after if r["kind"] == "bash"} >= {"R03", "R06", "R07", "R08"}
    # 後の形の再開のコマンドは R03 の 1 組だけ（R10・R13 は別の正規表現を持たない）
    assert not [r for r in after if r["kind"] == "bash" and r["rule"] in ("R10", "R13")]


def test_load_rules_after_rows_match_11_commands():
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after" and r["kind"] == "bash"]

    def rules_of(cmd: str) -> set[str]:
        return {r["rule"] for r in after if r["compiled"].search(cmd)}

    assert rules_of("ndf ci watch 3900") == {"R06"}
    assert rules_of("ndf ci merge") == {"R08"}
    assert rules_of("ndf plan next impl.json --from merge") == {"R03"}
    assert rules_of("ndf plan queue a.json b.json --from a=impl") == {"R03"}
    assert rules_of("ndf plan queue a.json b.json --resume") == {"R03"}
    assert rules_of("ndf plan wait a.json") == {"R07"}
    # --from を持たない開始と回答は再開に数えない
    assert rules_of("ndf plan next impl.json --answer approved") == set()


def test_load_rules_accepts_zero_rows(tmp_path):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", []))
    assert rules == []
    out = ab.count_transcripts([str(build(tmp_path)["transcripts"])], SINCE - datetime.timedelta(days=60), UNTIL, 1, rules)
    assert out["bash_by_rule"] == {}
    assert out["tools"] == {}
    assert out["results_with_phrase"] == {}
    assert out["by_form"] == {"before": {}, "after": {}}


@pytest.mark.parametrize(
    ("row", "missing"),
    [
        ({"form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R3", "form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R03", "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "later", "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "before", "kind": "file", "name": "x", "pattern": "x"}, "kind"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x"}, "pattern"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x", "pattern": "("}, "pattern"),
        ({"rule": "R12", "form": "before", "kind": "tool", "name": "x"}, "tool"),
        ({"rule": "R04", "form": "before", "kind": "phrase", "name": "x"}, "phrase"),
        ({"rule": "R04", "form": "before", "kind": "phrase", "name": "x", "phrase": "x", "measured": "no"}, "measured"),
        ({"rule": "R06", "form": "before", "kind": "bash", "name": "x", "pattern": "x", "source_prefix": "a"}, "source_prefix"),
    ],
)
def test_load_rules_rejects_bad_row_with_exit_2(tmp_path, capsys, row, missing):
    ab = load_ab()
    ok = {"rule": "R06", "form": "before", "kind": "bash", "name": "ok", "pattern": "ok"}
    with pytest.raises(SystemExit) as e:
        ab.load_rules(_write(tmp_path, "rules.json", [ok, row]))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "2 行目" in err
    assert missing in err


def test_unmeasured_rows_go_to_reference_not_value(tmp_path):
    """measured: false の行（番号を読めない行・補足の数の行）は参考に出し、value にも all_roles にも足さない。"""
    ab = load_ab()
    rules = ab.load_rules(RULES)
    _jsonl(
        tmp_path / "s.jsonl",
        [
            _bash("a", "2026-11-18T02:44:08.680Z", "python3 ci-watch.py 12", CONDUCTOR),
            _bash("b", "2026-11-18T02:45:08.680Z", "gh pr checks 12", CONDUCTOR),
            _bash("c", "2026-11-18T02:46:08.680Z", "ndf ci watch 12", CONDUCTOR),
        ],
    )
    f = ab.count_transcripts([str(tmp_path)], SINCE, UNTIL, 1, rules)["by_form"]
    assert (f["before"]["R06"]["value"], f["before"]["R06"]["reference"]["value"]) == (1, 1)
    assert f["after"]["R06"]["value"] == 1  # gh pr checks は数え、ndf ci watch は参考
    assert f["after"]["R06"]["reference"]["value"] == 1


def test_ask_user_question_splits_gate_questions_by_source(tmp_path):
    ab = load_ab()
    _jsonl(
        tmp_path / "s.jsonl",
        [
            _tool("a", "2026-11-18T02:44:08.680Z", "AskUserQuestion", {"metadata": {"source": "ndf-gate:1"}}, CONDUCTOR),
            _tool("b", "2026-11-18T02:45:08.680Z", "AskUserQuestion", {"metadata": {"source": "ndf-ask:redline"}}, CONDUCTOR),
            _tool("c", "2026-11-18T02:46:08.680Z", "AskUserQuestion", {}, CONDUCTOR),
        ],
    )
    f = ab.count_transcripts([str(tmp_path)], SINCE, UNTIL, 1, ab.load_rules(RULES))["by_form"]
    for form in ("before", "after"):
        assert f[form]["R12"]["value"] == 2
        assert f[form]["R13"]["value"] == 0
        assert f[form]["R13"]["reference"]["value"] == 1


# ---------- 分類の表 ----------


def test_load_measures_covers_every_rule_number():
    ab = load_ab()
    measures = ab.load_measures(MEASURES)
    assert sorted(measures) == [f"R{n:02d}" for n in range(1, 28)]
    by_g: dict[str, set[str]] = {}
    for rule, m in measures.items():
        by_g.setdefault(m["measure"], set()).add(rule)
    assert by_g["G3"] == {"R14", "R21"}
    assert by_g["none"] == {"R26", "R27"}
    assert len(by_g["G1"]) == 11
    assert len(by_g["G2"]) == 12


def test_rules_and_measures_agree():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    measures = ab.load_measures(MEASURES)
    ab.check_measures(rules, measures)
    for r in rules:
        assert measures[r["rule"]][r["form"]]["source"] != "none", (r["rule"], r["form"])


def test_check_measures_rejects_unknown_rule_with_exit_2(tmp_path, capsys):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", [{"rule": "R99", "form": "before", "kind": "tool", "name": "x", "tool": "x"}]))
    with pytest.raises(SystemExit) as e:
        ab.check_measures(rules, ab.load_measures(MEASURES))
    assert e.value.code == 2
    assert "R99" in capsys.readouterr().err


@pytest.mark.parametrize(
    "entry",
    [
        {"before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G4", "before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "logs"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "none"}, "after": {"source": "none", "reason": "x"}},
    ],
)
def test_load_measures_rejects_bad_entry_with_exit_2(tmp_path, capsys, entry):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        ab.load_measures(_write(tmp_path, "measures.json", {"R01": entry}))
    assert e.value.code == 2
    assert "R01" in capsys.readouterr().err


# ---------- main の引数と止まり方 ----------


def _main(ab, monkeypatch, tmp_path, *args: str) -> int:
    argv = ["ai-branching.py", "--skip-transcripts", "--ndf", str(tmp_path), "--state", str(tmp_path), *args]
    monkeypatch.setattr(sys, "argv", argv)
    return ab.main()


def test_main_stops_with_exit_2_on_bad_rules(tmp_path, monkeypatch, capsys):
    ab = load_ab()
    monkeypatch.setattr(ab, "RULES_FILE", _write(tmp_path, "rules.json", [{"form": "before", "kind": "tool", "name": "x", "tool": "x"}]))
    with pytest.raises(SystemExit) as e:
        _main(ab, monkeypatch, tmp_path, "--since", SINCE_TEXT, "--until", UNTIL_TEXT)
    assert e.value.code == 2
    assert "rule" in capsys.readouterr().err


@pytest.mark.parametrize(("since", "until"), [("2026-11-18", UNTIL_TEXT), (SINCE_TEXT, "2026-11-21T00:00:00")])
def test_main_rejects_moment_without_offset_with_exit_2(tmp_path, monkeypatch, capsys, since, until):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        _main(ab, monkeypatch, tmp_path, "--since", since, "--until", until)
    assert e.value.code == 2
    assert "時差" in capsys.readouterr().err


def test_main_stops_with_exit_3_without_schema(tmp_path, monkeypatch, capsys):
    ab = load_ab()
    monkeypatch.setattr(ab, "SCHEMA", tmp_path / "missing.schema.json")
    assert _main(ab, monkeypatch, tmp_path, "--since", SINCE_TEXT, "--until", UNTIL_TEXT) == 3
    assert "missing.schema.json" in capsys.readouterr().err


def test_main_runs_without_importing_ndf(tmp_path):
    """`ndf` を import すると落ちる環境でも打てる（決定 9）。"""
    fake = tmp_path / "fake"
    (fake / "ndf").mkdir(parents=True)
    (fake / "ndf" / "__init__.py").write_text("raise ModuleNotFoundError('ndf を import した')\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(fake)}
    p = run_main(build(tmp_path), env=env)
    assert p.returncode == 0, p.stderr


# ---------- 選択の記録 ----------


def _choice(at: str, rule: str, chooser: str) -> dict:
    return {"event": "route_chosen", "at": at, "run_id": "r1", "rule": rule, "chooser": chooser, "from": "wait", "to": "gate"}


def test_count_choices_counts_by_rule_and_chooser_in_half_open_period(tmp_path):
    ab = load_ab()
    path = tmp_path / "s" / "progress.jsonl"
    _jsonl(
        path,
        [
            {"kind": "step", "at": "2026-11-18T03:41:38+00:00", "step": "impl"},
            _choice("2026-11-17T15:00:00Z", "R03", "conductor"),  # since ちょうど（JST の 11-18 00:00）
            _choice("2026-11-18T01:00:00Z", "R03", "script"),
            _choice("2026-11-18T01:00:00Z", "R13", "human"),
            _choice("2026-11-20T14:59:59.999Z", "R10", "script"),  # until の 1 ミリ秒前
            _choice("2026-11-20T15:00:00Z", "R10", "conductor"),  # until ちょうど
            _choice("2026-11-20T15:00:01Z", "R10", "conductor"),  # until の 1 秒後
            _choice("2026-11-18T01:00:00Z", "R03", "ai"),  # スキーマに合わない
            {**_choice("2026-11-18T01:00:00Z", "R03", "script"), "extra": 1},
        ],
    )
    out = ab.count_choices([path], SINCE, UNTIL, json.loads(SCHEMA.read_text(encoding="utf-8")))
    assert out["by_rule"] == {"R03": {"conductor": 1, "script": 1}, "R10": {"script": 1}, "R13": {"human": 1}}
    assert out["rejected"] == 2
    assert out["files"] == 1


# ---------- 旧い進捗と台帳 ----------


def test_count_progress_counts_rows_in_half_open_period(tmp_path):
    ab = load_ab()
    path = tmp_path / "p-state" / "progress.jsonl"
    _jsonl(
        path,
        [
            # 期間の頭より前に止まった実行（impl が 1 で止まった）
            {"kind": "run", "at": "2026-11-17T10:00:00+00:00"},
            {"kind": "step", "at": "2026-11-17T10:01:00+00:00", "step": "impl", "type": "work", "exit": 1, "next": "end"},
            # 期間の中の再開
            {"kind": "run", "at": "2026-11-18T03:41:38+00:00", "from": "impl"},
            {"kind": "slow", "at": "2026-11-18T03:42:38+00:00", "step": "impl"},
            {"kind": "step", "at": "2026-11-18T03:43:38+00:00", "step": "design", "type": "work", "exit": 1, "next": "end"},
            {"kind": "slow", "at": "2026-11-20T14:59:59+00:00", "step": "impl"},  # until の 1 秒前
            {"kind": "slow", "at": "2026-11-20T15:00:00+00:00", "step": "impl"},  # until ちょうど
            {"kind": "slow", "at": "2026-11-20T15:00:01+00:00", "step": "impl"},  # until の 1 秒後
        ],
    )
    out = ab.count_progress([path], SINCE, UNTIL)
    assert out["kinds"] == {"slow": 2, "run": 1, "step": 1}
    assert out["resume_by_previous_end"] == {"失敗の後・同じステップから": 1}
    # 期間の外で止まった実行は数えず、期間の中で止まった design を数える
    assert out["stopped_by_step_exit"] == {"design exit=1": 1}


def _judge(at: str, decision: str) -> dict:
    return {"kind": "step", "at": at, "step": "judge", "type": "judge", "exit": 0, "decision": decision}


def test_count_progress_splits_judge_by_previous_step(tmp_path):
    ab = load_ab()
    a = tmp_path / "a" / "progress.jsonl"
    _jsonl(
        a,
        [
            {"kind": "run", "at": "2026-11-18T01:00:00+00:00"},
            {"kind": "step", "at": "2026-11-18T01:01:00+00:00", "step": "test-limited", "type": "work", "exit": 1, "next": "judge"},
            _judge("2026-11-18T01:02:00+00:00", "design-tests"),
            {"kind": "step", "at": "2026-11-18T01:03:00+00:00", "step": "pr", "type": "work", "exit": 0, "next": "judge"},
            _judge("2026-11-18T01:04:00+00:00", "design-tests"),
            {"kind": "step", "at": "2026-11-18T01:05:00+00:00", "step": "pr", "type": "work", "exit": 0, "next": "judge"},
            # --from judge で始まる実行は前の実行の最後のステップ（pr）で分ける
            {"kind": "run", "at": "2026-11-18T02:00:00+00:00", "from": "judge"},
            _judge("2026-11-18T02:01:00+00:00", "stop"),
            {"kind": "step", "at": "2026-11-18T02:02:00+00:00", "step": "gate", "type": "judge", "exit": 0, "decision": "gate"},
            {"kind": "step", "at": "2026-11-18T02:03:00+00:00", "step": "judge-record", "type": "judge", "exit": 0, "decision": "end"},
        ],
    )
    b = tmp_path / "b" / "progress.jsonl"
    _jsonl(b, [_judge("2026-11-18T01:00:00+00:00", "fix")])  # ファイルの頭が judge で前のステップが無い
    out = ab.count_progress([a, b], SINCE, UNTIL)
    assert out["judge_tests_by_decision"] == {"judge → design-tests": 1}
    assert out["judge_design_diff_by_decision"] == {"judge → design-tests": 1, "judge → stop": 1}
    assert out["judge_unclassified_by_decision"] == {"judge → fix": 1}
    assert out["judge_by_decision"]["gate → gate"] == 1

    counts = {"transcripts": None, "progress": out, "ledger": {}, "records": {"by_rule": {}, "rejected": 0}}
    forms = ab.summarize(counts, ab.load_measures(MEASURES))
    assert forms["before"]["by_rule"]["R20"]["value"] == 1
    assert forms["before"]["by_rule"]["R17"]["value"] == 1
    assert forms["before"]["unresolved"] == {"R20": 1}
    assert forms["before"]["measures"]["G2"]["partial"] is True


def test_count_ledgers_excludes_until(tmp_path):
    ab = load_ab()
    _jsonl(
        tmp_path / "parallel" / "p5" / "ledger.jsonl",
        [
            {"kind": "resume", "at": "2026-11-17T15:00:00Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-11-20T14:59:59Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-11-20T15:00:00Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-11-20T15:00:01Z", "msg": {"phase": "settle", "result": "rejected"}},
        ],
    )
    assert ab.count_ledgers(tmp_path, SINCE, UNTIL)["resume"] == {"settle: rejected": 2}


# ---------- 物差し ----------


def test_summarize_sums_one_source_per_rule_and_marks_unmeasured():
    ab = load_ab()
    measures = ab.load_measures(MEASURES)
    progress = {
        "kinds": {"slow": 3},
        "stopped_by_step_exit": {"impl exit=1": 2, "design exit=1": 1, "merge exit=4": 9},
        "judge_by_decision": {"gate → gate": 4},
        "judge_tests_by_decision": {"judge → fix": 5},
        "attention_by_reason": {"スプリント MVV を使えない": 6},
    }
    ledger = {"resume": {"settle: rejected": 7}}
    tr = {
        "before": {"R03": {"value": 2, "all_roles": 3, "unresolved": 0, "reference": {"value": 0, "all_roles": 0}}},
        "after": {},
    }
    counts = {"transcripts": tr, "progress": progress, "ledger": ledger, "records": {"by_rule": {"R03": {"script": 4}}, "rejected": 1}}
    out = ab.summarize(counts, measures)
    g = out["before"]["measures"]
    assert g["G3"] == {"value": 3 + 3, "all_roles": 6, "partial": False}  # R14 の slow と R21 の impl・design
    assert g["G1"]["value"] == 4  # R17
    assert g["G2"]["value"] == 2 + 5 + 6 + 7  # R03（会話）・R20・R23・R25
    assert set(out["before"]["unmeasured"]) == {"R26", "R27"}
    # 後の形: 書き手の無い規則は unmeasured で、物差しは partial
    assert "R14" in out["after"]["unmeasured"]
    assert out["after"]["measures"]["G3"]["partial"] is True
    # 選択の記録の script の行は G に足さない（R03 の後の形の出所は会話）
    assert out["after"]["by_rule"]["R03"]["value"] == 0
    assert out["after"]["by_rule"]["R03"]["reference"]["records"] == {"script": 4}
    assert out["after"]["rejected"] == 1


def test_summarize_reads_chooser_from_records():
    """出所が records の規則は chooser が conductor の行だけを数える（chooser を読み捨てると script の回が入る）。"""
    ab = load_ab()
    measures = {
        "R07": {"measure": "G1", "before": {"source": "none", "reason": "x"}, "after": {"source": "records"}},
    }
    counts = {
        "transcripts": None,
        "progress": {},
        "ledger": {},
        "records": {"by_rule": {"R07": {"script": 5, "conductor": 1}}, "rejected": 0},
    }
    out = ab.summarize(counts, measures)
    assert out["after"]["measures"]["G1"]["value"] == 1
    assert out["after"]["measures"]["G1"]["all_roles"] == 6


# ---------- 会話の記録: 役割・期間・follows_next ----------


def _count(ab, tmp: Path, files: dict[str, list[dict]]) -> dict:
    for rel, rows in files.items():
        _jsonl(tmp / "tr" / rel, rows)
    return ab.count_transcripts([str(tmp / "tr")], SINCE, UNTIL, 1, ab.load_rules(RULES))["by_form"]


def test_roles_count_only_conductor_in_value(tmp_path):
    ab = load_ab()
    f = _count(
        ab,
        tmp_path,
        {
            "s1.jsonl": [_bash("c", "2026-11-18T02:44:08.680Z", "ndf ci merge", CONDUCTOR)],
            "s1/subagents/agent-w.jsonl": [_bash("w", "2026-11-18T02:45:08.680Z", "ndf ci merge", CONDUCTOR)],
            "s2.jsonl": [_bash("s", "2026-11-18T02:46:08.680Z", "ndf ci merge", SUPERVISOR)],
        },
    )
    r08 = f["after"]["R08"]
    assert (r08["value"], r08["all_roles"]) == (1, 3)
    assert r08["roles"] == {"conductor": 1, "other": 2}


def test_tool_result_without_role_inherits_from_call(tmp_path):
    ab = load_ab()
    f = _count(
        ab,
        tmp_path,
        {
            "s1.jsonl": [
                _bash("c", "2026-11-18T02:44:08.680Z", "echo", CONDUCTOR),
                _result("c", "2026-11-18T02:44:09.680Z", "基盤待ち（75）で止めた"),
                _result("gone", "2026-11-18T02:44:10.680Z", "基盤待ち（75）で止めた"),  # 継ぐ先が無い
            ]
        },
    )
    r09 = f["before"]["R09"]
    assert (r09["value"], r09["all_roles"]) == (1, 2)
    assert r09["roles"] == {"conductor": 1, "unattributed": 1}


def test_transcripts_exclude_until(tmp_path):
    ab = load_ab()
    f = _count(
        ab,
        tmp_path,
        {
            "s1.jsonl": [
                _bash("a", "2026-11-17T15:00:00.000Z", "ndf ci merge", CONDUCTOR),  # since ちょうど
                _bash("b", "2026-11-20T14:59:59.999Z", "ndf ci merge", CONDUCTOR),
                _bash("c", "2026-11-20T15:00:00.000Z", "ndf ci merge", CONDUCTOR),  # until ちょうど
                _bash("d", "2026-11-20T15:00:01.000Z", "ndf ci merge", CONDUCTOR),
            ]
        },
    )
    assert f["after"]["R08"]["value"] == 2


def _stopped(plan: str, step: str, cause: dict | None, nxt: list[str] | None = None) -> dict:
    res: dict[str, Any] = {
        "tool": "plan",
        "status": "stopped",
        "summary": "止まった",
        "items": [{"kind": "plan", "name": plan, "step": step}],
    }
    if cause is not None:
        res["cause"] = cause
    if nxt is not None:
        res["next"] = {"argv": nxt}
    return res


def _resume_case(tmp: Path, ab, prior: dict | None, cmd: str, before: list[dict] | None = None) -> dict:
    rows = list(before or [])
    if prior is not None:
        rows += [
            _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/impl.json", CONDUCTOR),
            _result("p", "2026-11-18T02:10:00.000Z", prior),
        ]
    rows.append(_bash("r", "2026-11-18T02:20:00.000Z", cmd, CONDUCTOR))
    return _count(ab, tmp, {"s.jsonl": rows})["after"]


def test_resume_follows_next_is_not_value(tmp_path):
    ab = load_ab()
    nxt = ["ndf", "plan", "next", "/w/impl.json", "--from", "merge"]
    prior = _stopped("impl", "merge", {"kind": "ci_failed", "detail": "x"}, nxt)
    f = _resume_case(tmp_path / "a", ab, prior, "ndf plan next /w/impl.json --from merge")
    assert (f["R03"]["value"], f["R03"]["all_roles"], f["R03"]["follows_next"]) == (0, 1, 1)
    f = _resume_case(tmp_path / "b", ab, prior, "cd /w && ndf plan next /w/impl.json --from impl 2>&1 | tail -1")
    assert (f["R03"]["value"], f["R03"]["basis"]) == (1, {"stopped": 1})


@pytest.mark.parametrize(
    ("prior", "rule", "basis"),
    [
        (_stopped("impl", "merge", {"kind": "ci_failed", "detail": "x"}), "R03", "stopped"),
        # 11.0.13 の ndf plan next が返す cause の形をそのまま写した見本。文字列で比べる実装は R03 に入れて落ちる
        (_stopped("impl", "merge", {"kind": "base_conflict", "detail": "ベースブランチと衝突"}), "R10", "base_conflict"),
        (
            {
                "tool": "plan",
                "status": "gate",
                "summary": "待つ",
                "items": [{"kind": "plan", "name": "impl", "step": "gate", "gate": True}],
                "routes": {"approved": {"argv": ["ndf", "plan", "next", "/w/impl.json", "--from", "merge"]}, "rejected": None},
            },
            "R13",
            "gate",
        ),
        (None, "R03", "no_prior_result"),
    ],
)
def test_resume_is_routed_by_prior_result(tmp_path, prior, rule, basis):
    ab = load_ab()
    f = _resume_case(tmp_path, ab, prior, "ndf plan next /w/impl.json --from impl")
    assert f[rule]["value"] == 1
    assert f[rule]["basis"] == {basis: 1}
    assert sum(e["value"] for e in f.values()) == 1


def test_resume_after_answer_is_r13(tmp_path):
    ab = load_ab()
    answered = [
        _bash("a", "2026-11-18T01:00:00.000Z", "ndf plan next /w/impl.json --answer approved", CONDUCTOR),
        _result("a", "2026-11-18T01:01:00.000Z", {"tool": "plan", "status": "ok", "summary": "済み", "items": []}),
    ]
    f = _resume_case(tmp_path, ab, None, "ndf plan next /w/impl.json --from impl", before=answered)
    assert f["R13"]["value"] == 1
    assert f["R13"]["basis"] == {"answer": 1}


def test_gate_routes_followed_is_not_value(tmp_path):
    ab = load_ab()
    gate = {
        "tool": "plan",
        "status": "gate",
        "summary": "待つ",
        "items": [{"kind": "plan", "name": "impl", "step": "gate", "gate": True}],
        "routes": {"approved": {"argv": ["ndf", "plan", "next", "/w/impl.json", "--from", "merge"]}, "rejected": None},
    }
    f = _resume_case(tmp_path / "a", ab, gate, "ndf plan next /w/impl.json --from merge")
    assert (f["R13"]["value"], f["R13"]["all_roles"]) == (0, 1)
    f = _resume_case(tmp_path / "b", ab, gate, "ndf plan next /w/impl.json --from impl")
    assert f["R13"]["value"] == 1


def _queue(tmp: Path, ab, res: dict, cmd: str) -> dict:
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json /w/b.json", CONDUCTOR),
        _result("q", "2026-11-18T02:10:00.000Z", res),
        _bash("r", "2026-11-18T02:20:00.000Z", cmd, CONDUCTOR),
    ]
    return _count(ab, tmp, {"s.jsonl": rows})["after"]


def test_queue_second_conflict_is_unresolved_when_first_stopped(tmp_path):
    ab = load_ab()
    res = {
        "tool": "plan",
        "status": "stopped",
        "summary": "a: CI が落ちた",
        "cause": {"kind": "ci_failed", "detail": "CI が落ちた"},
        "items": [
            {"kind": "plan", "name": "a", "status": "stopped", "detail": "CI が落ちた"},
            {"kind": "plan", "name": "b", "status": "stopped", "detail": "衝突"},
        ],
        "next": {"argv": ["ndf", "plan", "queue", "/w/a.json", "/w/b.json", "--resume"]},
    }
    f = _queue(tmp_path / "b", ab, res, "ndf plan queue /w/a.json /w/b.json --from b=impl")
    assert f["R03"]["value"] == 0
    assert f["R03"]["unresolved"] == 1
    assert f.get("R10", {}).get("value", 0) == 0
    f = _queue(tmp_path / "a", ab, res, "ndf plan queue /w/a.json /w/b.json --from a=impl")
    assert (f["R03"]["value"], f["R03"]["basis"]) == (1, {"stopped": 1})
    # 2 つの対象は 2 回
    f = _queue(tmp_path / "ab", ab, res, "ndf plan queue /w/a.json /w/b.json --from a=impl --from b=merge")
    assert f["R03"]["value"] == 1
    assert f["R03"]["unresolved"] == 1

    counts = {"transcripts": {"before": {}, "after": f}, "progress": {}, "ledger": {}, "records": {"by_rule": {}, "rejected": 0}}
    out = ab.summarize(counts, ab.load_measures(MEASURES))["after"]
    assert out["unresolved"] == {"R03": 1}
    assert out["measures"]["G1"]["partial"] is True  # R10 を含む
    assert out["measures"]["G2"]["partial"] is True  # R03 を含む


def test_queue_only_stopped_conflict_is_r10(tmp_path):
    ab = load_ab()
    res = {
        "tool": "plan",
        "status": "stopped",
        "summary": "b: 衝突",
        "cause": {"kind": "base_conflict", "detail": "衝突"},
        "items": [
            {"kind": "plan", "name": "a", "status": "ok", "detail": "済み"},
            {"kind": "plan", "name": "b", "status": "stopped", "detail": "衝突"},
        ],
    }
    f = _queue(tmp_path, ab, res, "ndf plan queue /w/a.json /w/b.json --from b=impl")
    assert f["R10"]["value"] == 1
    # --resume は ok でないプランだけを対象にする
    f = _queue(tmp_path / "r", ab, res, "ndf plan queue /w/a.json /w/b.json --resume")
    assert f["R10"]["value"] == 1
    assert sum(e["value"] for e in f.values()) == 1


def test_same_choice_counts_once_in_each_form(tmp_path):
    """前の形と後の形の同じ再開が、同じ規則の番号に 1 回ずつ数えられる（テスト設計の 8）。"""
    ab = load_ab()
    f = _count(
        ab,
        tmp_path,
        {
            "s.jsonl": [
                _bash("a", "2026-11-18T02:00:00.000Z", "python3 supervise.py run 6-impl.json --from merge", CONDUCTOR),
                _bash("b", "2026-11-18T02:10:00.000Z", "ndf plan next /w/impl.json --from merge", CONDUCTOR),
            ]
        },
    )
    assert f["before"]["R03"]["value"] == 1
    assert f["after"]["R03"]["value"] == 1


def test_stopped_record_and_manual_resume_count_once(tmp_path):
    """停止の後の自動の遷移（chooser: script の記録）と、conductor の手での再開を一組にすると、後の形の R03 は 1。"""
    paths = {"ndf": tmp_path / ".ndf", "state": tmp_path / "state", "transcripts": tmp_path / "tr"}
    _jsonl(paths["state"] / "impl-state" / "progress.jsonl", [_choice("2026-11-18T01:00:00Z", "R03", "script")])
    nxt = ["ndf", "plan", "next", "/w/impl.json", "--from", "merge"]
    _jsonl(
        paths["transcripts"] / "s.jsonl",
        [
            _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/impl.json", CONDUCTOR),
            _result("p", "2026-11-18T02:10:00.000Z", _stopped("impl", "merge", {"kind": "ci_failed", "detail": "x"}, nxt)),
            _bash("r", "2026-11-18T02:20:00.000Z", "ndf plan next /w/impl.json --from impl", CONDUCTOR),
        ],
    )
    p = run_main(paths, since=SINCE_TEXT, until=UNTIL_TEXT)
    assert p.returncode == 0, p.stderr
    after = json.loads(p.stdout)["forms"]["after"]
    assert after["by_rule"]["R03"]["value"] == 1
    assert after["by_rule"]["R03"]["reference"]["records"] == {"script": 1}
    assert after["measures"]["G2"]["value"] == 1
    # 前の形と後の形の両方に規則の番号ごとの回数と G1〜G3 が出る（テスト設計の 7）
    before = json.loads(p.stdout)["forms"]["before"]
    assert set(before["measures"]) == set(after["measures"]) == {"G1", "G2", "G3"}


# ---------- follows_next は chosen.py の _follows と同じ ----------


def _chosen_follows():
    """`chosen.py` の `_follows` だけを取り出す（`ndf` を import しない）。"""
    tree = ast.parse(CHOSEN.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_follows")
    ns: dict[str, Any] = {"Sequence": Sequence, "Any": Any}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(CHOSEN), "exec"), ns)  # noqa: S102
    return ns["_follows"]


@pytest.mark.parametrize(
    ("suggested", "taken"),
    [
        ({"argv": ["ndf", "plan", "next", "p"]}, ["ndf", "plan", "next", "p"]),
        ({"argv": ["ndf", "plan", "next", "p"]}, ["ndf", "plan", "next", "p", "--from", "impl"]),
        ({"argv": ["ndf", "plan", "next", "p"], "ask": ["answer"]}, ["ndf", "plan", "next", "p", "--answer", "approved"]),
        ({"argv": ["ndf", "plan", "next", "p"], "ask": ["answer"]}, ["ndf", "plan", "next", "p", "--answer", "a", "--answer", "b"]),
        ({"argv": ["ndf", "plan", "next", "p"], "ask": ["answer"]}, ["ndf", "plan", "next", "p", "--answer"]),
        ({"argv": ["ndf", "plan", "next", "p", "--from", "merge"]}, ["ndf", "plan", "next", "p", "--from", "impl"]),
    ],
)
def test_follows_matches_chosen(suggested, taken):
    ab = load_ab()
    assert ab._follows(suggested, taken) == _chosen_follows()(suggested, taken)
