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
import importlib
import importlib.util
import json
import multiprocessing
import os
import re
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
# 旧い形の見本の期間（2026-10-14 の UTC の日から 1 か月）
OLD_SINCE = datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc)
OLD_UNTIL = datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc)
CONDUCTOR = {"isSidechain": False, "entrypoint": "cli"}
SUPERVISOR = {"isSidechain": False, "entrypoint": "sdk-cli"}


# `ai_branching` の名前で本体を読み込む小さなモジュール。ワーカーのプロセスは関数を名前（`ai_branching.init_worker`）で受け取り、
# 開始の方式が forkserver・spawn（Python 3.14 の Linux の既定は forkserver）なら子は sys.path からこれを import する。
# 照合用の値を組んだ回数は、子でも親でも同じこのモジュールの包みが隣のファイルへ 1 字ずつ書く（親の monkeypatch を継がない）
SHIM = """\
import importlib.util
import pathlib
import sys

_spec = importlib.util.spec_from_file_location("ai_branching", {ab!r})
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_branching"] = _mod
_spec.loader.exec_module(_mod)
_real = _mod.build_matcher
_calls = pathlib.Path(__file__).with_name("matcher-calls")


def _counted(rules):
    with open(_calls, "a", encoding="utf-8") as f:
        f.write("x")
    return _real(rules)


_mod.build_matcher = _counted
"""


@pytest.fixture(autouse=True)
def ab_shim(tmp_path, monkeypatch) -> Path:
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "ai_branching.py").write_text(SHIM.format(ab=str(AB)), encoding="utf-8")
    # 子は起動のときに親の今の sys.path を受け取る
    monkeypatch.syspath_prepend(str(shim))
    monkeypatch.delitem(sys.modules, "ai_branching", raising=False)
    return shim


def load_ab():
    sys.modules.pop("ai_branching", None)
    return importlib.import_module("ai_branching")


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


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        # waiting.md の標準手順の CI の読み方は R06 に入る（旧い gh_parts.py pr-status の置き換え）
        ("gh pr view 3981 --json statusCheckRollup,reviewDecision,mergeable", {"R06"}),
        ("gh pr view 3981 --json=reviewDecision,statusCheckRollup", {"R06"}),
        # CI の状態を取らない通常の PR の表示は入らない
        ("gh pr view 3981", set()),
        ("gh pr view 3981 --json title,body,headRefName", set()),
        ("gh pr view 3981 --json title | jq -r .statusCheckRollup", set()),
    ],
)
def test_after_r06_counts_pr_view_only_with_status_check_rollup(cmd, expected):
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after" and r["kind"] == "bash"]
    assert {r["rule"] for r in after if r["compiled"].search(cmd)} == expected


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        # 旧い sprint-state.py loop にもあった、開発ループの次の手を選ぶサブコマンド
        ("ndf loop start main --session s1", {"R02"}),
        ("ndf loop sprint main --mode build --basis x --session s1", {"R02"}),
        ("ndf loop --cwd /work/x lap main --session s1", {"R02"}),
        ("ndf loop get main --field metrics", {"R02"}),
        # 実装・設計の照合の通常の検査は入らない
        ("ndf loop acceptance check --pr 3981", set()),
        ("ndf loop design-tests --pr 3981", set()),
        ("ndf loop check-trigger --base main", set()),
        ("ndf loop review-args --purpose code --pr 1 --state-dir d --base main", set()),
        ("ndf loop close-plan --pace normal --route impl", set()),
    ],
)
def test_after_r02_counts_only_loop_progress_subcommands(cmd, expected):
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after" and r["kind"] == "bash"]
    assert {r["rule"] for r in after if r["compiled"].search(cmd)} == expected


def test_after_r09_counts_infra_wait_results_apart_from_reruns(tmp_path):
    ab = load_ab()
    rules = ab.load_rules(RULES)
    r09 = [r for r in rules if r["rule"] == "R09"]
    # 旧い指標（基盤待ちの結果）に対応する行と、補助の件数（再実行）の行を分ける
    for form in ("before", "after"):
        main_rows = [r for r in r09 if r["form"] == form and r["measured"]]
        aside_rows = [r for r in r09 if r["form"] == form and not r["measured"]]
        assert [r.get("pattern") for r in aside_rows] == ["gh run rerun"], form
        assert main_rows and all(r["kind"] in ("phrase", "cause") for r in main_rows), form
    after = [r for r in rules if r["form"] == "after"]
    stopped = {"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}
    other = {"tool": "ci", "status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}}
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("w1", "2026-10-14T04:00:00.000Z", "ndf ci watch --pr 1 --required test --budget 600"),
            _result("w1", "2026-10-14T04:10:00.000Z", json.dumps(stopped, ensure_ascii=False, indent=1)),
            _bash("w2", "2026-10-14T04:11:00.000Z", "ndf ci watch --pr 2 --required test --budget 600"),
            _result("w2", "2026-10-14T04:12:00.000Z", json.dumps(other)),
            # ndf ci watch でない呼び出しの結果と、文に infra_wait を含むだけの結果は数えない
            _bash("w3", "2026-10-14T04:13:00.000Z", "cat out.json"),
            _result("w3", "2026-10-14T04:13:01.000Z", json.dumps(stopped)),
            _bash("w4", "2026-10-14T04:14:00.000Z", "ndf ci watch --pr 4 --required test --budget 600"),
            _result("w4", "2026-10-14T04:14:01.000Z", "infra_wait を待つ"),
            _bash("r1", "2026-10-14T04:15:00.000Z", "gh run rerun 99 --failed"),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after)
    name = next(r["name"] for r in after if r["kind"] == "cause")
    assert out["results_with_cause"] == {name: 1}
    # 再実行は補助の件数（measured: false）として参考に出し、R09 の値に足さない
    r09_after = out["by_form"]["after"]["R09"]
    assert (r09_after["all_roles"], r09_after["reference"]["all_roles"]) == (1, 1)


def _background(tid: str, at: str, task: str, *, with_id_field: bool = True, output: Path | None = None) -> dict:
    """バックグラウンド起動の応答。形は 2026-11-20 の実ログ（`toolUseResult.backgroundTaskId` と本文の ID）を写す。"""
    row = _result(
        tid,
        at,
        f"Command running in background with ID: {task}. Output is being written to: {output or f'/tmp/x/tasks/{task}.output'}. "
        "You will be notified when it completes.",
    )
    if with_id_field:
        row["toolUseResult"] = {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": task}
    return row


def _notification(task: str, tid: str, at: str, extra: str = "") -> dict:
    text = (
        f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{tid}</tool-use-id>\n"
        f"<output-file>/tmp/x/tasks/{task}.output</output-file>\n<status>failed</status>\n"
        f'<summary>Background command "CI を待つ" failed with exit code 3</summary>{extra}\n</task-notification>'
    )
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": at, "content": text}


def test_after_r09_counts_infra_wait_from_background_completion(tmp_path):
    """起動応答と完了の結果が別の行に分かれる回も、起動した呼び出しごとに 1 度だけ数える。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    other = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}})
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            # b1: 完了通知の後、出力ファイルを Bash で 2 度読む → 1 件
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa"),
            _notification("t1aaa", "b1", "2026-10-14T04:10:00.000Z"),
            _bash("b1r", "2026-10-14T04:10:01.000Z", "tail -c 1500 /tmp/x/tasks/t1aaa.output"),
            _result("b1r", "2026-10-14T04:10:02.000Z", "...\n" + stopped + "\n\n[exited with code 3]"),
            _bash("b1s", "2026-10-14T04:10:03.000Z", "cat /tmp/x/tasks/t1aaa.output"),
            _result("b1s", "2026-10-14T04:10:04.000Z", stopped),
            # b2: 識別子が本文にだけある版で、Read で読む → 1 件
            _bash("b2", "2026-10-14T04:11:00.000Z", watch),
            _background("b2", "2026-10-14T04:11:01.000Z", "t2bbb", with_id_field=False),
            _tool("b2r", "2026-10-14T04:20:00.000Z", "Read", {"file_path": "/tmp/x/tasks/t2bbb.output"}),
            _result("b2r", "2026-10-14T04:20:01.000Z", stopped),
            # b3: 完了通知が結果を載せる → 1 件
            _bash("b3", "2026-10-14T04:21:00.000Z", watch),
            _background("b3", "2026-10-14T04:21:01.000Z", "t3ccc"),
            _notification("t3ccc", "b3", "2026-10-14T04:30:00.000Z", "\n<result>" + stopped + "</result>"),
            # b4: 結果が infra_wait でない → 数えない
            _bash("b4", "2026-10-14T04:31:00.000Z", watch),
            _background("b4", "2026-10-14T04:31:01.000Z", "t4ddd"),
            _bash("b4r", "2026-10-14T04:40:00.000Z", "tail /tmp/x/tasks/t4ddd.output"),
            _result("b4r", "2026-10-14T04:40:01.000Z", other),
            # b5: ndf ci watch でない起動の出力は数えない
            _bash("b5", "2026-10-14T04:41:00.000Z", "sleep 1; cat out.json"),
            _background("b5", "2026-10-14T04:41:01.000Z", "t5eee"),
            _bash("b5r", "2026-10-14T04:50:00.000Z", "tail /tmp/x/tasks/t5eee.output"),
            _result("b5r", "2026-10-14T04:50:01.000Z", stopped),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after)
    assert out["results_with_cause"] == {name: 3}
    assert out["results_without_cause"] == {}


def _notification_at(task: str, tid: str, at: str, output: Path) -> dict:
    """実ログの形の完了通知（終了コードと `<output-file>` だけで、結果 JSON を載せない）。"""
    text = (
        f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{tid}</tool-use-id>\n"
        f"<output-file>{output}</output-file>\n<status>failed</status>\n"
        f'<summary>Background command "CI を待つ" failed with exit code 3</summary>\n</task-notification>'
    )
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": at, "content": text}


def test_after_r09_reads_saved_output_of_completion_notice(tmp_path):
    """完了通知だけを受け取った回は保存済み出力から読み、出力が無い回は別の件数に出す。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    saved = tmp_path / "tasks" / "t1aaa.output"
    saved.parent.mkdir()
    saved.write_text("待っています\n" + stopped + "\n", encoding="utf-8")
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa", output=saved),
            _notification_at("t1aaa", "b1", "2026-10-14T04:10:00.000Z", saved),
            # b2: 保存済み出力が消えている → 数えず、読めなかった回に出す
            _bash("b2", "2026-10-14T04:11:00.000Z", watch),
            _background("b2", "2026-10-14T04:11:01.000Z", "t2bbb", output=tmp_path / "tasks" / "t2bbb.output"),
            _notification_at("t2bbb", "b2", "2026-10-14T04:20:00.000Z", tmp_path / "tasks" / "t2bbb.output"),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after, [str(tmp_path)])
    assert out["results_with_cause"] == {name: 1}
    assert out["results_without_cause"] == {name: 1}


def test_after_r09_reads_only_the_output_named_by_the_launch(tmp_path):
    """完了通知の `<output-file>` は、起動応答が示した同じタスクの出力の通常のファイルだけを読む。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    # 起動応答と違うパスを示す完了通知（ほかのファイル）
    other = tmp_path / "secret.txt"
    other.write_text(stopped, encoding="utf-8")
    # 起動応答と同じパスでも通常のファイルでないもの（FIFO を開くと読み手が止まる）
    fifo = tasks / "t2bbb.output"
    os.mkfifo(fifo)
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa", output=tasks / "t1aaa.output"),
            _notification_at("t1aaa", "b1", "2026-10-14T04:10:00.000Z", other),
            _bash("b2", "2026-10-14T04:11:00.000Z", watch),
            _background("b2", "2026-10-14T04:11:01.000Z", "t2bbb", output=fifo),
            _notification_at("t2bbb", "b2", "2026-10-14T04:20:00.000Z", fifo),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after, [str(tmp_path)])
    assert out.get("results_with_cause", {}) == {}
    assert out["results_without_cause"] == {name: 2}


def test_read_output_takes_fields_of_a_result_longer_than_the_limit(tmp_path):
    """落ちたテストが多く 1 行の結果が上限を超えても、`tool`・`status`・`cause` を取る（相互レビュー r5-3）。"""
    ab = load_ab()
    big = tmp_path / "tasks" / "t1.output"
    big.parent.mkdir()
    nodes = [f"tests/test_x.py::test_{i}" for i in range(ab.OUTPUT_READ_LIMIT // 20)]
    found = {
        "tool": "ci",
        "status": "stopped",
        "summary": "落ちた",
        "items": [],
        "metrics": {"failed_tests": nodes},
        "cause": {"kind": "test_failed", "detail": "x"},
    }
    line = json.dumps(found, ensure_ascii=False)
    assert len(line) > ab.OUTPUT_READ_LIMIT + ab.OUTPUT_HEAD_LIMIT
    big.write_text("CI を待っている（10 秒）: test\n" + line + "\n", encoding="utf-8")
    text = ab.read_output(str(big), str(big), [str(tmp_path)])
    assert json.loads(text) == {"tool": "ci", "status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}}
    assert ab.read_output(str(big), None, [str(tmp_path)]) is None
    # 上限を超える行が結果 JSON の形でなければ読めなかったとする
    big.write_bytes(b"x" * (ab.OUTPUT_READ_LIMIT + 10) + b"END")
    assert ab.read_output(str(big), str(big), [str(tmp_path)]) is None


def test_read_output_reads_only_under_the_given_task_roots(tmp_path):
    """記録が示すパスが一致しても、渡した根の下の `tasks/<識別子>.output` の実体でなければ読まない（相互レビュー r5-1）。"""
    ab = load_ab()
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "x"}})
    root = tmp_path / "root"
    outside = tmp_path / "outside" / "tasks"
    outside.mkdir(parents=True)
    (outside / "t1.output").write_text(stopped + "\n", encoding="utf-8")
    # 根の外のファイル
    assert ab.read_output(str(outside / "t1.output"), str(outside / "t1.output"), [str(root)]) is None
    # 親のディレクトリが根の外を指すシンボリックリンク
    (root / "s").mkdir(parents=True)
    (root / "s" / "tasks").symlink_to(outside, target_is_directory=True)
    linked = str(root / "s" / "tasks" / "t1.output")
    assert ab.read_output(linked, linked, [str(root)]) is None
    # 根の下の `tasks/` でない通常のファイル
    (root / "other.output").write_text(stopped + "\n", encoding="utf-8")
    assert ab.read_output(str(root / "other.output"), str(root / "other.output"), [str(root)]) is None
    # 根の下の実体は読む
    (root / "r" / "tasks").mkdir(parents=True)
    (root / "r" / "tasks" / "t1.output").write_text(stopped + "\n", encoding="utf-8")
    real = str(root / "r" / "tasks" / "t1.output")
    assert json.loads(ab.read_output(real, real, [str(root)]))["cause"]["kind"] == "infra_wait"


def test_read_output_rejects_a_root_that_is_a_symlink(tmp_path):
    """根がシンボリックリンクなら、リンク先を根にしない（相互レビュー r6-1）。"""
    ab = load_ab()
    real = tmp_path / "real"
    (real / "s" / "tasks").mkdir(parents=True)
    out = real / "s" / "tasks" / "t1.output"
    out.write_text(json.dumps({"tool": "ci", "status": "ok"}) + "\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    via = str(link / "s" / "tasks" / "t1.output")
    assert ab.read_output(via, via, [str(link)]) is None
    assert ab.read_output(str(out), str(out), [str(real)]) is not None


def test_read_output_reads_only_the_tail_of_a_long_log(tmp_path, monkeypatch):
    """長いログの後ろの結果は末尾だけを読んで取り、末尾が行の途中から始まるときはその行の先頭を探す（相互レビュー r6-2）。"""
    ab = load_ab()
    monkeypatch.setattr(ab, "OUTPUT_READ_LIMIT", 256)
    monkeypatch.setattr(ab, "OUTPUT_CHUNK", 16)
    out = tmp_path / "tasks" / "t1.output"
    out.parent.mkdir()
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "x"}})
    log = "".join(f"CI を待っている（{i} 秒）\n" for i in range(200))
    out.write_text(log + stopped + "\n", encoding="utf-8")
    reads: list[int] = []
    pread = os.pread
    monkeypatch.setattr(ab.os, "pread", lambda fd, n, off: reads.append(n) or pread(fd, n, off))
    assert json.loads(ab.read_output(str(out), str(out), [str(tmp_path)]))["cause"]["kind"] == "infra_wait"
    assert sum(reads) <= ab.OUTPUT_READ_LIMIT + 1
    # 結果の行が末尾の読み取りの先頭をまたぐ
    # 上限内の行（行の全体を読み直す）と上限を超える行（先頭と末尾から項目を取る）
    for pad in (150, 400):
        line = json.dumps({"tool": "ci", "status": "stopped", "summary": " " * pad, "cause": {"kind": "infra_wait"}})
        out.write_text(log + line + "\n" + "end\n" * 20, encoding="utf-8")
        assert len(line) + 1 + 80 > ab.OUTPUT_READ_LIMIT
        assert json.loads(ab.read_output(str(out), str(out), [str(tmp_path)]))["cause"]["kind"] == "infra_wait"


def test_after_r09_skips_other_json_with_status_before_the_result(tmp_path):
    """出力を読む Bash が先に GitHub の JSON（`status` を持つ）を表示しても、後の ci の結果を数える（相互レビュー r5-2）。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}})
    gh = json.dumps({"status": "completed", "conclusion": "failure"})
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa"),
            _bash("b1r", "2026-10-14T04:10:00.000Z", "gh run view 9 --json status,conclusion; tail /tmp/x/tasks/t1aaa.output"),
            _result("b1r", "2026-10-14T04:10:01.000Z", gh + "\n" + stopped),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after)
    assert out["results_with_cause"] == {name: 1}
    assert out["results_without_cause"] == {}
    assert ab.result_json(gh) is None


def test_after_r09_separates_successful_results_from_lost_outputs(tmp_path):
    """結果 JSON を取得できた正常終了（`status: ok` で cause なし）は、出力を失った回に入れない。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    ok = json.dumps({"tool": "ci", "status": "ok", "checks": [{"name": "test", "conclusion": "success"}]})
    saved = tmp_path / "tasks" / "t1aaa.output"
    saved.parent.mkdir()
    saved.write_text('{"progress": 1}\n' + ok + "\n", encoding="utf-8")
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            # b1: 保存済み出力の正常終了 → どちらにも数えない
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa", output=saved),
            _notification_at("t1aaa", "b1", "2026-10-14T04:10:00.000Z", saved),
            # b2: 出力を読んだ結果の正常終了 → どちらにも数えない
            _bash("b2", "2026-10-14T04:11:00.000Z", watch),
            _background("b2", "2026-10-14T04:11:01.000Z", "t2bbb"),
            _tool("o2", "2026-10-14T04:12:00.000Z", "TaskOutput", {"task_id": "t2bbb"}),
            _result("o2", "2026-10-14T04:12:01.000Z", ok),
            # b3: 出力が読めない → 失った回に出す
            _bash("b3", "2026-10-14T04:21:00.000Z", watch),
            _background("b3", "2026-10-14T04:21:01.000Z", "t3ccc", output=tmp_path / "tasks" / "t3ccc.output"),
            _notification_at("t3ccc", "b3", "2026-10-14T04:30:00.000Z", tmp_path / "tasks" / "t3ccc.output"),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after, [str(tmp_path)])
    assert out.get("results_with_cause", {}) == {}
    assert out["results_without_cause"] == {name: 1}


def test_after_r09_skips_results_that_read_several_outputs(tmp_path):
    """複数のタスクの出力をまとめて読んだ結果は、どのタスクにも帰属させない。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    other = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}})
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa"),
            _bash("b2", "2026-10-14T04:01:00.000Z", watch),
            _background("b2", "2026-10-14T04:01:01.000Z", "t2bbb"),
            _bash("c", "2026-10-14T04:10:00.000Z", "cat /tmp/x/tasks/t1aaa.output /tmp/x/tasks/t2bbb.output"),
            _result("c", "2026-10-14T04:10:01.000Z", stopped + "\n" + other),
            # 1 つずつ読み直した結果は数える（b2 は test_failed）
            _tool("o2", "2026-10-14T04:11:00.000Z", "TaskOutput", {"task_id": "t2bbb"}),
            _result("o2", "2026-10-14T04:11:01.000Z", other),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], OLD_SINCE, OLD_UNTIL, 1, after)
    assert out.get("results_with_cause", {}) == {}
    assert out["results_without_cause"] == {name: 1}


@pytest.mark.parametrize(
    ("name", "inp", "expected"),
    [
        ("Bash", {"command": "tail -c 1500 /tmp/x/tasks/t1aaa.output"}, {"t1aaa"}),
        ("Bash", {"command": "cat tasks/t1.output tasks/t2.output"}, {"t1", "t2"}),
        ("Read", {"file_path": "/tmp/x/tasks/t2bbb.output"}, {"t2bbb"}),
        ("TaskOutput", {"task_id": "t3ccc"}, {"t3ccc"}),
        ("Write", {"file_path": "/x/a.md", "content": "t1aaa " * 1000}, set()),
    ],
)
def test_read_tasks_takes_ids_from_output_paths(name, inp, expected):
    assert load_ab().read_tasks(name, inp) == expected


@pytest.mark.parametrize(
    "row",
    [
        {"rule": "R09", "form": "after", "kind": "cause", "name": "x", "cause": "infra_wait"},
        {"rule": "R09", "form": "after", "kind": "cause", "name": "x", "command": "ndf ci watch"},
        {"rule": "R09", "form": "after", "kind": "cause", "name": "x", "command": "(", "cause": "infra_wait"},
        {"rule": "R09", "form": "after", "kind": "bash", "name": "x", "pattern": "x", "measured": "yes"},
    ],
)
def test_load_rules_rejects_bad_cause_or_measured_with_exit_2(tmp_path, row):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        ab.load_rules(_write(tmp_path, "rules.json", [row]))
    assert e.value.code == 2


def test_load_rules_accepts_zero_rows(tmp_path):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", []))
    assert rules == []
    out = ab.count_transcripts([str(build(tmp_path)["transcripts"])], SINCE - datetime.timedelta(days=60), UNTIL, 1, rules)
    assert out["bash_by_rule"] == {}
    assert out["tools"] == {}
    assert out["results_with_phrase"] == {}
    assert out["by_form"] == {"before": {}, "after": {}}


@pytest.mark.parametrize("method", ["fork", "forkserver", "spawn"])
def test_count_transcripts_builds_matcher_once_per_worker(tmp_path, monkeypatch, ab_shim, method):
    ab = load_ab()
    paths = build(tmp_path)
    for n in range(2, 5):
        _jsonl(paths["transcripts"] / f"s{n}.jsonl", [_bash(f"u{n}", "2026-10-14T04:00:00.000Z", "gh pr checks 1")])
    calls = ab_shim / "matcher-calls"
    real_pool = ab.ProcessPoolExecutor
    # 開始の方式を変えても、子が本体を import して数えられる
    monkeypatch.setattr(ab, "ProcessPoolExecutor", lambda **kw: real_pool(mp_context=multiprocessing.get_context(method), **kw))
    before = [r for r in ab.load_rules(RULES) if r["form"] == "before"]
    out = ab.count_transcripts([str(paths["transcripts"])], OLD_SINCE, OLD_UNTIL, 1, before)
    assert out["files"] == 4
    assert out["bash_by_rule"]["CI のチェックを手で読む（gh pr checks / gh run view）"] == 4
    # 4 つのファイルを 1 本のワーカーで読み、照合用の値は初期化の 1 度だけ組む（読み込みのときの空の値は数えない）
    assert calls.read_text(encoding="utf-8") == "x"


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
        # 手で編集したときの型の誤り: 配列の kind・数の rule・配列の pattern
        ({"rule": "R03", "form": "before", "kind": ["bash"], "name": "x", "pattern": "x"}, "kind"),
        ({"rule": 3, "form": "before", "kind": "bash", "name": "x", "pattern": "x"}, "rule"),
        ({"rule": "R03", "form": ["before"], "kind": "bash", "name": "x", "pattern": "x"}, "form"),
        ({"rule": "R03", "form": "before", "kind": "bash", "name": "x", "pattern": ["x"]}, "pattern"),
        ({"rule": "R04", "form": "before", "kind": "phrase", "name": "x", "phrase": "x", "measured": "no"}, "measured"),
        ({"rule": "R06", "form": "before", "kind": "bash", "name": "x", "pattern": "x", "source_prefix": "a"}, "source_prefix"),
        ({"rule": "R09", "form": "after", "kind": "cause", "name": "x"}, "cause"),
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
    # 後の形は gh pr checks も手で打った ndf ci watch も数える（ndf ci watch の提案どおりの回は follows_next で外す）
    assert f["after"]["R06"]["value"] == 2
    assert f["after"]["R06"]["reference"]["value"] == 0


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
        # 型の誤り: 文字列の keys・配列の measure・配列の source・数の prefix
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds", "keys": "slow"}, "after": {"source": "none", "reason": "x"}},
        {"measure": ["G1"], "before": {"source": "none", "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": ["none"], "reason": "x"}, "after": {"source": "none", "reason": "x"}},
        {"measure": "G1", "before": {"source": "progress", "counter": "kinds", "prefix": 1}, "after": {"source": "none", "reason": "x"}},
    ],
)
def test_load_measures_rejects_bad_entry_with_exit_2(tmp_path, capsys, entry):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        ab.load_measures(_write(tmp_path, "measures.json", {"R01": entry}))
    assert e.value.code == 2
    assert "R01" in capsys.readouterr().err


@pytest.mark.parametrize("loader", ["load_rules", "load_measures"])
def test_loaders_reject_broken_json_with_exit_2(tmp_path, capsys, loader):
    ab = load_ab()
    path = tmp_path / "table.json"
    # 手で編集したときの末尾のカンマ
    path.write_text('[\n  {"rule": "R01"},\n]\n' if loader == "load_rules" else '{\n  "R01": {},\n}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        getattr(ab, loader)(path)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert str(path) in err
    # 位置（行と列）を示す。末尾のカンマで指す行は Python の版で変わる（3.11・3.12 は閉じ括弧の行）ので値は固定しない
    assert re.search(r": \d+ 行 \d+ 列で JSON として読めない", err), err


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


def test_count_choices_rejects_rows_whose_at_is_not_a_moment(tmp_path):
    """形だけ合う無効な日付（11 月 31 日）や時差の無い `at` は、どの期間でも数えず rejected に入れる。"""
    ab = load_ab()
    path = tmp_path / "s" / "progress.jsonl"
    _jsonl(
        path,
        [
            _choice("2026-11-31T00:00:00Z", "R03", "conductor"),
            _choice("2026-11-18T01:00:00", "R03", "conductor"),
            _choice("2026-11-18T01:00:00Z", "R03", "conductor"),
        ],
    )
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for since, until in ((SINCE, UNTIL), (SINCE - datetime.timedelta(days=3650), UNTIL + datetime.timedelta(days=3650))):
        out = ab.count_choices([path], since, until, schema)
        assert out["by_rule"] == {"R03": {"conductor": 1}}
        assert out["rejected"] == 2


def test_count_plan_records_reads_each_file_once(tmp_path, monkeypatch):
    """進捗と選択の記録を同じ走査で数え、`progress.jsonl` を 1 回ずつしか開かない（選択の記録の無い旧いログも）。"""
    ab = load_ab()
    old = tmp_path / "old" / "progress.jsonl"
    _jsonl(old, [{"kind": "slow", "at": "2026-11-18T03:42:38+00:00", "step": "impl"}])
    new = tmp_path / "new" / "progress.jsonl"
    _jsonl(new, [{"kind": "slow", "at": "2026-11-18T03:42:38+00:00", "step": "impl"}, _choice("2026-11-18T01:00:00Z", "R03", "script")])
    opened: list[str] = []
    real = open

    def counting(file, *args, **kwargs):
        opened.append(str(file))
        return real(file, *args, **kwargs)

    monkeypatch.setattr(ab, "open", counting, raising=False)
    progress, choices = ab.count_plan_records([old, new], SINCE, UNTIL, json.loads(SCHEMA.read_text(encoding="utf-8")))
    assert sorted(opened) == sorted([str(old), str(new)])
    assert progress["kinds"] == {"slow": 2}
    assert choices["by_rule"] == {"R03": {"script": 1}}
    assert choices["files"] == 1


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


def test_ci_watch_by_hand_counts_conductor_once(tmp_path):
    """同じ `ndf ci watch` を conductor・worker・supervisor が 1 回ずつ打つと value 1・all_roles 3（詳細設計の単体テストの観点）。"""
    ab = load_ab()
    f = _count(
        ab,
        tmp_path,
        {
            "s1.jsonl": [_bash("c", "2026-11-18T02:44:08.680Z", "ndf ci watch 3967", CONDUCTOR)],
            "s1/subagents/agent-w.jsonl": [_bash("w", "2026-11-18T02:45:08.680Z", "ndf ci watch 3967", CONDUCTOR)],
            "s2.jsonl": [_bash("s", "2026-11-18T02:46:08.680Z", "ndf ci watch 3967", SUPERVISOR)],
        },
    )
    r06 = f["after"]["R06"]
    assert (r06["value"], r06["all_roles"]) == (1, 3)


def test_ci_watch_suggested_by_next_is_not_value(tmp_path):
    ab = load_ab()
    prior = {"tool": "ci", "status": "stopped", "summary": "x", "items": [], "next": {"argv": ["ndf", "ci", "watch", "3967"]}}
    f = _count(
        ab,
        tmp_path,
        {
            "s.jsonl": [
                _bash("p", "2026-11-18T02:00:00.000Z", "ndf ci watch 3967", CONDUCTOR),
                _result("p", "2026-11-18T02:10:00.000Z", prior),
                _bash("r", "2026-11-18T02:20:00.000Z", "ndf ci watch 3967", CONDUCTOR),
            ]
        },
    )
    r06 = f["after"]["R06"]
    assert (r06["value"], r06["all_roles"], r06["follows_next"]) == (1, 2, 1)


def test_ci_infra_wait_result_is_after_r09(tmp_path):
    """11 系の CI の基盤待ち（`ndf ci` の結果の `cause.kind` が infra_wait）を後の形の R09 に数える。"""
    ab = load_ab()

    def ci(kind: str, tool: str = "ci") -> dict:
        return {"tool": tool, "status": "stopped", "summary": "x", "items": [], "cause": {"kind": kind, "detail": "x"}}

    f = _count(
        ab,
        tmp_path,
        {
            "s.jsonl": [
                _bash("a", "2026-11-18T02:00:00.000Z", "ndf ci watch 3967", CONDUCTOR),
                _result("a", "2026-11-18T02:10:00.000Z", ci("infra_wait")),
                _bash("b", "2026-11-18T02:20:00.000Z", "ndf ci watch 3967", CONDUCTOR),
                _result("b", "2026-11-18T02:30:00.000Z", ci("test_failed")),
                _bash("c", "2026-11-18T02:40:00.000Z", "ndf plan next /w/impl.json", CONDUCTOR),
                _result("c", "2026-11-18T02:50:00.000Z", ci("infra_wait", tool="plan")),
            ]
        },
    )
    r09 = f["after"]["R09"]
    assert (r09["value"], r09["all_roles"]) == (1, 1)
    counts = {"transcripts": f, "progress": {}, "ledger": {}, "records": {"by_rule": {}, "rejected": 0}}
    out = ab.summarize(counts, ab.load_measures(MEASURES))["after"]
    assert out["by_rule"]["R09"]["value"] == 1
    assert "R09" not in out["unmeasured"]


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


# ---------- 再開の対象: サブコマンドとキューをまたいだ直近の結果・コマンド単位 ----------


def _wait_conflict(plan: str) -> dict:
    return {
        "tool": "plan",
        "status": "stopped",
        "summary": f"{plan}: 衝突",
        "cause": {"kind": "base_conflict", "detail": "衝突"},
        "items": [{"kind": "plan", "name": plan, "status": "stopped", "cause": {"kind": "base_conflict", "detail": "衝突"}}],
    }


def test_next_resume_reads_latest_wait_result_of_same_plan(tmp_path):
    """`queue` の後に `wait a.json` で衝突を見て `next a.json --from …` で再開すると、wait の行で R10 に入る。"""
    ab = load_ab()
    queued = {
        "tool": "plan",
        "status": "ok",
        "summary": "流した",
        "items": [{"kind": "plan", "name": "a", "status": "ok", "detail": "x"}],
    }
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json", CONDUCTOR),
        _result("q", "2026-11-18T02:01:00.000Z", queued),
        _bash("w", "2026-11-18T02:02:00.000Z", "ndf plan wait /w/a.json", CONDUCTOR),
        _result("w", "2026-11-18T02:03:00.000Z", _wait_conflict("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (f["R10"]["value"], f["R10"]["basis"]) == (1, {"base_conflict": 1})
    assert f.get("R03", {}).get("value", 0) == 0


def test_queue_resume_reads_only_its_own_queue(tmp_path):
    """キュー A を止めた後にキュー B を待ち、A を `--resume` すると、A の結果だけで数える（B のプランと停止理由を混ぜない）。"""
    ab = load_ab()
    a_stopped = {
        "tool": "plan",
        "status": "stopped",
        "summary": "a1: CI が落ちた",
        "cause": {"kind": "ci_failed", "detail": "x"},
        "items": [
            {"kind": "plan", "name": "a1", "status": "stopped", "detail": "x"},
            {"kind": "plan", "name": "a2", "status": "ok", "detail": "x"},
        ],
    }
    b_conflict = {
        "tool": "plan",
        "status": "stopped",
        "summary": "b1: 衝突",
        "cause": {"kind": "base_conflict", "detail": "衝突"},
        "items": [
            {"kind": "plan", "name": "b1", "status": "stopped", "cause": {"kind": "base_conflict", "detail": "衝突"}},
            {"kind": "plan", "name": "b2", "status": "gate", "gate": True},
        ],
    }
    rows = [
        _bash("qa", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a1.json /w/a2.json", CONDUCTOR),
        _result("qa", "2026-11-18T02:01:00.000Z", a_stopped),
        _bash("wb", "2026-11-18T02:02:00.000Z", "ndf plan wait /w/b1.json /w/b2.json", CONDUCTOR),
        _result("wb", "2026-11-18T02:03:00.000Z", b_conflict),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /w/a1.json /w/a2.json --resume", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (f["R03"]["value"], f["R03"]["basis"]) == (1, {"stopped": 1})
    # B の衝突（R10）と承認ゲート（R13）を混ぜない。B の wait は R07 の待ちとして別に数える
    assert {r: f[r]["value"] for r in ("R03", "R10", "R13") if r in f} == {"R03": 1}


def test_each_command_in_one_bash_call_is_matched_alone(tmp_path):
    """同じ Bash の呼び出しの、提案どおりの再開と手で選んだ再開を、コマンドごとに照らして対象ごとに数える。"""
    ab = load_ab()
    nxt = ["ndf", "plan", "next", "/w/a.json", "--from", "impl"]
    prior = _stopped("a", "impl", {"kind": "ci_failed", "detail": "x"}, nxt)
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", prior),
        _bash("b", "2026-11-18T02:02:00.000Z", "ndf plan next /w/b.json", CONDUCTOR),
        _result("b", "2026-11-18T02:03:00.000Z", _stopped("b", "merge", {"kind": "base_conflict", "detail": "衝突"})),
        _bash("p2", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("p2", "2026-11-18T02:05:00.000Z", prior),
        _bash("r", "2026-11-18T02:06:00.000Z", "ndf plan next /w/a.json --from impl && ndf plan next /w/b.json --from merge", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (f["R03"]["value"], f["R03"]["follows_next"]) == (0, 1)
    assert (f["R10"]["value"], f["R10"]["basis"]) == (1, {"base_conflict": 1})


# ---------- 再開の対象: プランの鍵・キューの記録・1 つの呼び出しの複数の結果・改行 ----------


def _at(cwd: str) -> dict:
    return {**CONDUCTOR, "cwd": cwd}


def _gate(plan: str) -> dict:
    return {
        "tool": "plan",
        "status": "gate",
        "summary": "待つ",
        "items": [{"kind": "plan", "name": plan, "step": "gate", "gate": True}],
    }


def _conflict(plan: str) -> dict:
    return _stopped(plan, "merge", {"kind": "base_conflict", "detail": "衝突"})


def _ok(plan: str) -> dict:
    return {"tool": "plan", "status": "ok", "summary": "済み", "items": [{"kind": "plan", "name": plan, "status": "ok"}]}


def _queued(*items: tuple[str, str]) -> dict:
    return {
        "tool": "plan",
        "status": "stopped",
        "summary": f"{items[0][0]}: CI が落ちた",
        "cause": {"kind": "ci_failed", "detail": "x"},
        "items": [{"kind": "plan", "name": n, "status": s, "detail": "x"} for n, s in items],
    }


def _resumes(f: dict) -> dict:
    return {r: f[r]["all_roles"] for r in ("R03", "R10", "R13") if f.get(r, {}).get("all_roles")}


def test_same_name_plan_in_other_dir_does_not_change_route(tmp_path):
    """`/a/plan.json` が衝突で止まった後に同じ名前の `/b/plan.json` が承認待ちになっても、A の再開は R10。"""
    ab = load_ab()
    rows = [
        _bash("a", "2026-11-18T02:00:00.000Z", "ndf plan next /a/plan.json", CONDUCTOR),
        _result("a", "2026-11-18T02:01:00.000Z", _conflict("plan")),
        _bash("b", "2026-11-18T02:02:00.000Z", "ndf plan next /b/plan.json", CONDUCTOR),
        _result("b", "2026-11-18T02:03:00.000Z", _gate("plan")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /a/plan.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_queue_from_name_is_resolved_in_its_own_plans(tmp_path):
    """`queue --from <名前>=…` の名前は、そのキューに渡したプランの中で解決する（別のディレクトリの同じ名前を引かない）。"""
    ab = load_ab()
    a_conflict = {
        "tool": "plan",
        "status": "stopped",
        "summary": "plan: 衝突",
        "cause": {"kind": "base_conflict", "detail": "衝突"},
        "items": [{"kind": "plan", "name": "plan", "status": "stopped"}, {"kind": "plan", "name": "other", "status": "ok"}],
    }
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /a/plan.json /a/other.json", CONDUCTOR),
        _result("q", "2026-11-18T02:01:00.000Z", a_conflict),
        _bash("b", "2026-11-18T02:02:00.000Z", "ndf plan next /b/plan.json", CONDUCTOR),
        _result("b", "2026-11-18T02:03:00.000Z", _gate("plan")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /a/plan.json /a/other.json --from plan=impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


@pytest.mark.parametrize(
    ("first", "first_cwd", "resume", "resume_cwd"),
    [
        ("ndf plan next a.json", "/w", "ndf plan next /w/a.json --from impl", "/x"),
        ("ndf plan next /w/a.json", "/x", "cd /w && ndf plan next ./a.json --from impl", "/x"),
        ("ndf plan next ../a.json", "/w/sub", "ndf plan next a.json --from impl", "/w"),
    ],
)
def test_relative_and_absolute_paths_find_the_same_result(tmp_path, first, first_cwd, resume, resume_cwd):
    ab = load_ab()
    rows = [
        _bash("a", "2026-11-18T02:00:00.000Z", first, _at(first_cwd)),
        _result("a", "2026-11-18T02:01:00.000Z", _conflict("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", resume, _at(resume_cwd)),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_state_dir_separates_results_of_same_plan(tmp_path):
    """同じプランでも状態の保存先が違えば、別の実行の結果として引かない。"""
    ab = load_ab()
    rows = [
        _bash("a", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json --state-dir /s1", CONDUCTOR),
        _result("a", "2026-11-18T02:01:00.000Z", _conflict("a")),
        _bash("b", "2026-11-18T02:02:00.000Z", "ndf plan next /w/a.json --state-dir /s2", CONDUCTOR),
        _result("b", "2026-11-18T02:03:00.000Z", _gate("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --state-dir /s1 --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_wait_timeout_value_is_not_a_plan(tmp_path):
    """`plan wait a.json --timeout 30` の 30 をプランに数えず、wait の結果で a のパスと名前を結ぶ。"""
    ab = load_ab()
    assert ab.plan_call(["ndf", "plan", "wait", "a.json", "--timeout", "30"])["plans"] == ["a.json"]
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan wait a.json --timeout 30", _at("/w")),
        _result("w", "2026-11-18T02:01:00.000Z", _wait_conflict("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next a.json --from impl", _at("/w")),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_queue_resume_targets_plans_not_done_in_its_own_queue(tmp_path):
    """キューで止まった a が単独の next で済んでも、元のキューの `--resume` は a を流すので対象に入れる。"""
    ab = load_ab()
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json /w/b.json", CONDUCTOR),
        _result("q", "2026-11-18T02:01:00.000Z", _queued(("a", "stopped"), ("b", "ok"))),
        _bash("n", "2026-11-18T02:02:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("n", "2026-11-18T02:03:00.000Z", _ok("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /w/a.json /w/b.json --resume", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R03": 1}
    assert f["R03"]["basis"] == {"other": 1}


def test_queue_resume_reads_done_plans_by_queue_dir(tmp_path):
    """完了済みの判定は `--queue-dir` のキューの結果で決め、別の置き場のキューで済んだプランを外さない。"""
    ab = load_ab()
    rows = [
        _bash("q1", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json --queue-dir /q1", CONDUCTOR),
        _result("q1", "2026-11-18T02:01:00.000Z", _queued(("a", "stopped"))),
        _bash("q2", "2026-11-18T02:02:00.000Z", "ndf plan queue /w/a.json --queue-dir /q2", CONDUCTOR),
        _result("q2", "2026-11-18T02:03:00.000Z", _ok("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /w/a.json --queue-dir /q1 --resume", CONDUCTOR),
        _bash("r2", "2026-11-18T02:05:00.000Z", "ndf plan queue /w/a.json --queue-dir /q2 --resume", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R03": 1}


def test_queue_resume_with_all_done_adds_no_target(tmp_path):
    """キューのプランがすべて完了済みなら、`--resume` に架空の R03 を足さない。"""
    ab = load_ab()
    done = {
        "tool": "plan",
        "status": "ok",
        "summary": "済み",
        "items": [{"kind": "plan", "name": "a", "status": "ok"}, {"kind": "plan", "name": "b", "status": "ok"}],
    }
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json /w/b.json", CONDUCTOR),
        _result("q", "2026-11-18T02:01:00.000Z", done),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /w/a.json /w/b.json --resume", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {}


def _lines(*results: dict) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in results)


def test_each_result_in_one_bash_call_goes_to_its_command(tmp_path):
    """`next a; next b` で a が衝突・b が済むと、a の再開は R10（最後の結果だけを最後のコマンドに結ばない）。"""
    ab = load_ab()
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json; ndf plan next /w/b.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", _lines(_conflict("a"), _ok("b"))),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_infra_wait_before_another_result_is_r09(tmp_path):
    ab = load_ab()
    ci = {"tool": "ci", "status": "stopped", "summary": "x", "items": [], "cause": {"kind": "infra_wait", "detail": "x"}}
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf ci watch 3967; ndf plan next /w/a.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", _lines(ci, _ok("a"))),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (f["R09"]["value"], f["R09"]["all_roles"]) == (1, 1)


def test_newline_ends_a_command(tmp_path):
    """改行で続けた 2 つの `ndf ci watch` を別に数え、`next` のとおりの片方だけを follows_next にする。"""
    ab = load_ab()
    assert ab.segments("ndf ci watch 3967\nndf ci watch 3968") == [["ndf", "ci", "watch", "3967"], ["ndf", "ci", "watch", "3968"]]
    assert ab.segments("ndf plan queue a.json \\\n  --from a=impl") == [["ndf", "plan", "queue", "a.json", "--from", "a=impl"]]
    assert ab.segments("echo 'x\nndf ci watch 1'") == [["echo", "x\nndf ci watch 1"]]
    assert ab.segments("ndf ci watch 1 # 注釈\nndf ci watch 2") == [["ndf", "ci", "watch", "1"], ["ndf", "ci", "watch", "2"]]
    prior = {"tool": "ci", "status": "stopped", "summary": "x", "items": [], "next": {"argv": ["ndf", "ci", "watch", "3967"]}}
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf ci watch 3967", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", prior),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf ci watch 3967\nndf ci watch 3968", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (f["R06"]["value"], f["R06"]["all_roles"], f["R06"]["follows_next"]) == (2, 3, 1)


# ---------- 再開の対象: 保存先の省略形・リダイレクト・初出のプラン・注釈・サブシェル ----------


def test_wait_with_explicit_default_state_dir_is_same_plan(tmp_path):
    """`next a.json` の承認待ちが返す `wait a.json --state-dir a.state` で見た衝突を、保存先を省いた再開でも引く。"""
    ab = load_ab()
    assert (
        ab.plan_call(["ndf", "plan", "next", "/w/a.json"])["ids"]
        != ab.plan_call(["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/a.state"])["ids"]
    )
    gate = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/a.state"]}}
    rows = [
        _bash("n", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("n", "2026-11-18T02:01:00.000Z", gate),
        _bash("w", "2026-11-18T02:02:00.000Z", "ndf plan wait /w/a.json --state-dir /w/a.state", CONDUCTOR),
        _result("w", "2026-11-18T02:03:00.000Z", _wait_conflict("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_declared_state_dir_is_restored_from_the_result_not_the_plan_file(tmp_path):
    """`state_dir: custom` のプランの承認待ちが返す `wait --state-dir /w/custom` で見た衝突を、省略形の再開に結ぶ。

    計測の時点のプランのファイルは読まない。片付けた後に数え直しても同じ結果になる。
    """
    ab = load_ab()
    plan = tmp_path / "w" / "a.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"name": "a", "state_dir": "custom"}), encoding="utf-8")
    gate = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "a.json", "--state-dir", f"{plan.parent}/custom"]}}
    at = _at(str(plan.parent))
    rows = [
        _bash("n", "2026-11-18T02:00:00.000Z", "ndf plan next a.json", at),
        _result("n", "2026-11-18T02:01:00.000Z", gate),
        _bash("w", "2026-11-18T02:02:00.000Z", f"ndf plan wait a.json --state-dir {plan.parent}/custom", at),
        _result("w", "2026-11-18T02:03:00.000Z", _wait_conflict("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next a.json --from impl", at),
    ]
    assert _resumes(_count(ab, tmp_path / "1", {"s.jsonl": rows})["after"]) == {"R10": 1}
    plan.unlink()
    assert _resumes(_count(ab, tmp_path / "2", {"s.jsonl": rows})["after"]) == {"R10": 1}


def test_piped_result_is_shown_only_through_a_pass_through_command(tmp_path):
    """`next a | jq -r .summary; next b` で会話に出た結果は b のもの。b の衝突を b の再開に結ぶ。"""
    ab = load_ab()
    assert [e["shown"] for e in ab.parse_bash("ndf a | jq -r .summary; ndf b") if e["kind"] == "cmd"] == [False, True, True]
    assert ab.parse_bash("ndf a | tee log | tail -n 1")[0]["shown"] is True
    assert ab.parse_bash("ndf a | tail > log")[0]["shown"] is False
    assert ab.parse_bash("(ndf a; ndf b) | jq .x")[1]["shown"] is False
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json | jq -r .summary; ndf plan next /w/b.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", "止まった\n" + json.dumps(_conflict("b"), ensure_ascii=False)),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/b.json --from merge", CONDUCTOR),
    ]
    assert _resumes(_count(ab, tmp_path, {"s.jsonl": rows})["after"]) == {"R10": 1}


def test_redirections_apply_left_to_right_and_subshells_inherit():
    """リダイレクトは左から順に付け替え、サブシェルの中は外の記述子を継ぐ。"""
    ab = load_ab()

    def shown(cmd: str) -> list[bool]:
        return [e["shown"] for e in ab.parse_bash(cmd) if e["kind"] == "cmd"]

    assert shown("ndf a >log >&2") == [True]
    assert shown("(ndf a >&2) >log") == [True]
    assert shown("(ndf a >&2) 2>log") == [False]
    assert shown("ndf a 2>&1 >log") == [False]
    assert shown("ndf a >/dev/stderr") == [True]
    assert shown("(ndf a) >log 2>&1") == [False]
    assert shown("ndf a >&-") == [False]
    assert shown("ndf a >&log") == [False]


def test_redirected_result_is_not_paired_with_another_plan(tmp_path):
    """`next a > log; next b` の会話に出た結果は B のもの。A に記録せず、A の再開を B の停止理由で数えない。"""
    ab = load_ab()
    rows = [
        _bash("a", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("a", "2026-11-18T02:01:00.000Z", _stopped("a", "impl", {"kind": "ci_failed", "detail": "x"})),
        _bash("p", "2026-11-18T02:02:00.000Z", "ndf plan next /w/a.json > /tmp/a.log; ndf plan next /w/b.json", CONDUCTOR),
        _result("p", "2026-11-18T02:03:00.000Z", _conflict("b")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/b.json --from merge", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}
    rows[-1] = _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR)
    f = _count(ab, tmp_path / "2", {"s.jsonl": rows})["after"]
    assert (_resumes(f), f["R03"]["basis"]) == ({"R03": 1}, {"stopped": 1})


def test_results_not_uniquely_paired_do_not_update_history(tmp_path):
    """同じ名前のコマンドと結果の数が合わなければ、どのプランの履歴も更新しない。"""
    ab = load_ab()
    commands = [(["ndf", "plan", "next", "a"], {"ids": ["a"]}, True), (["ndf", "plan", "next", "b"], {"ids": ["b"]}, True)]
    assert ab.pair_results(commands, [_conflict("b")]) == [(None, _conflict("b"))]
    assert ab.parse_bash("ndf x 2>&1 | tail")[0]["shown"] is True
    assert ab.parse_bash("ndf x >&2")[0]["shown"] is True
    assert [e["shown"] for e in ab.parse_bash("(ndf x; ndf y) > log; ndf z") if e["kind"] == "cmd"] == [False, False, True]
    assert ab.parse_bash("ndf x &> /dev/null")[0]["shown"] is False


def test_queue_resume_counts_new_plan_after_done_plan(tmp_path):
    """完了済みの a に初出の b を足した `queue a b --resume` は、b を no_prior_result の対象に数える。"""
    ab = load_ab()
    rows = [
        _bash("q", "2026-11-18T02:00:00.000Z", "ndf plan queue /w/a.json", CONDUCTOR),
        _result("q", "2026-11-18T02:01:00.000Z", _ok("a")),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan queue /w/a.json /w/b.json --resume", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert (_resumes(f), f["R03"]["basis"]) == ({"R03": 1}, {"no_prior_result": 1})


def test_comment_with_unclosed_quote_keeps_following_lines():
    """注釈に閉じていない引用符があっても読め、注釈を終える改行で次のコマンドを分ける。"""
    ab = load_ab()
    assert ab.segments("ndf ci watch 3967 # it's waiting\nndf ci watch 3968") == [
        ["ndf", "ci", "watch", "3967"],
        ["ndf", "ci", "watch", "3968"],
    ]
    assert ab.segments("echo a#b '#c' \"#d\"") == [["echo", "a#b", "#c", "#d"]]


def test_cd_in_subshell_does_not_leak_to_parent(tmp_path):
    """`(cd /a && next a.json); next b.json` を /w から打つと、b は /w/b.json の鍵で記録する。"""
    ab = load_ab()
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "(cd /a && ndf plan next a.json); ndf plan next b.json", _at("/w")),
        _result("p", "2026-11-18T02:01:00.000Z", _lines(_ok("a"), _conflict("b"))),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/b.json --from merge", _at("/x")),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_jq_compact_identity_keeps_the_result_paired(tmp_path):
    """`next a | jq -c .; next b` は a の結果を 1 行のまま出す。a の衝突を a の再開に結ぶ。"""
    ab = load_ab()

    def first(cmd: str) -> bool:
        return ab.parse_bash(cmd)[0]["shown"]

    assert [first(f"ndf a | {jq}") for jq in ("jq -c .", "jq -rc .", "jq --compact-output '.'", "jq -c -S .")] == [True] * 4
    assert [first(f"ndf a | {jq}") for jq in ("jq .", "jq -c .x", "jq -nc .", "jq -sc .", "jq -c . f.json")] == [False] * 5
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json | jq -c .; ndf plan next /w/b.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", _lines(_conflict("a"), _ok("b"))),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from merge", CONDUCTOR),
    ]
    assert _resumes(_count(ab, tmp_path, {"s.jsonl": rows})["after"]) == {"R10": 1}


def test_pipe_reader_with_other_input_does_not_show_the_earlier_command(tmp_path):
    """`next a | cat < notes.txt; next b` で会話に出た結果は b のもの。b の衝突を b の再開に結ぶ。"""
    ab = load_ab()

    def first(cmd: str) -> bool:
        return ab.parse_bash(cmd)[0]["shown"]

    hidden = ("cat < n.txt", "cat 0<n.txt", "cat n.txt", "grep x f.txt", "tail -n 1 f.txt", "grep -r x", "cat <<< x")
    assert [first(f"ndf a | {c}") for c in hidden] == [False] * len(hidden)
    shown = ("cat - n.txt", "grep x", "grep -e x", "head -n 1", "tail -1", "tee log", "cat 2<n.txt")
    assert [first(f"ndf a | {c}") for c in shown] == [True] * len(shown)
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json | cat < notes.txt; ndf plan next /w/b.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", "メモ\n" + json.dumps(_conflict("b"), ensure_ascii=False)),
        _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/b.json --from merge", CONDUCTOR),
    ]
    assert _resumes(_count(ab, tmp_path, {"s.jsonl": rows})["after"]) == {"R10": 1}


def test_omitted_state_dir_does_not_share_history_with_explicit_default(tmp_path):
    """`state_dir: custom` の a.json を省略形で打った回と、`--state-dir a.state` を明示した回の履歴を混ぜない。

    省略形の承認待ちが `wait --state-dir /w/custom` を示した後も、明示した a.state の衝突は省略形の再開に使わない。
    """
    ab = load_ab()
    gate = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/custom"]}}
    for prior in (_gate("a"), gate):
        rows = [
            _bash("n", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
            _result("n", "2026-11-18T02:01:00.000Z", prior),
            _bash("e", "2026-11-18T02:02:00.000Z", "ndf plan next /w/a.json --state-dir /w/a.state", CONDUCTOR),
            _result("e", "2026-11-18T02:03:00.000Z", _conflict("a")),
            _bash("r", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
            _bash("s", "2026-11-18T02:05:00.000Z", "ndf plan next /w/a.json --state-dir /w/a.state --from merge", CONDUCTOR),
        ]
        f = _count(ab, tmp_path / str(id(prior)), {"s.jsonl": rows})["after"]
        assert (_resumes(f), f["R13"]["basis"]) == ({"R10": 1, "R13": 1}, {"gate": 1})


def test_state_dir_shown_by_omitted_result_binds_explicit_resume(tmp_path):
    """省略形の結果が保存先 /w/custom を示した後は、`--state-dir /w/custom` を明示した再開が省略形の履歴を引く。"""
    ab = load_ab()
    gate = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/custom"]}}
    rows = [
        _bash("n", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("n", "2026-11-18T02:01:00.000Z", _conflict("a")),
        _bash("g", "2026-11-18T02:02:00.000Z", "ndf plan next /w/b.json", CONDUCTOR),
        _result("g", "2026-11-18T02:03:00.000Z", _gate("b")),
        _bash("m", "2026-11-18T02:04:00.000Z", "ndf plan next /w/a.json --from merge", CONDUCTOR),
        _result("m", "2026-11-18T02:05:00.000Z", gate),
        _bash("r", "2026-11-18T02:06:00.000Z", "ndf plan next /w/a.json --state-dir /w/custom --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1, "R13": 1}


# ---------- 相互レビュー r1: バックグラウンドの plan・結果の形・コマンドの位置・記述子・follows_next・回答・期間の後 ----------


@pytest.mark.parametrize("delivery", ["saved_output", "notification_text", "task_output"])
def test_background_plan_wait_result_routes_the_resume(tmp_path, delivery):
    """バックグラウンドの `ndf plan wait` が base_conflict を返した直後の再開は R10（完了通知・保存済み出力・TaskOutput のどれでも）。"""
    ab = load_ab()
    conflict = json.dumps(_wait_conflict("a"), ensure_ascii=False)
    saved = tmp_path / "tasks" / "w1aaa.output"
    saved.parent.mkdir()
    saved.write_text("待っています\n" + conflict + "\n", encoding="utf-8")
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan wait /w/a.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "w1aaa", output=saved),
    ]
    if delivery == "saved_output":
        rows.append(_notification_at("w1aaa", "w", "2026-11-18T02:10:00.000Z", saved))
    elif delivery == "notification_text":
        rows.append(_notification("w1aaa", "w", "2026-11-18T02:10:00.000Z", "\n" + conflict))
    else:
        rows += [
            _tool("o", "2026-11-18T02:10:00.000Z", "TaskOutput", {"task_id": "w1aaa"}, CONDUCTOR),
            _result("o", "2026-11-18T02:10:01.000Z", conflict),
        ]
    rows.append(_bash("r", "2026-11-18T02:11:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR))
    for rel, rs in {"s.jsonl": rows}.items():
        _jsonl(tmp_path / "tr" / rel, rs)
    out = ab.count_transcripts([str(tmp_path / "tr")], SINCE, UNTIL, 1, ab.load_rules(RULES), [str(tmp_path)])
    assert _resumes(out["by_form"]["after"]) == {"R10": 1}


def test_background_plan_result_before_since_still_routes_the_resume(tmp_path):
    """期間の前に完了したバックグラウンドの wait の結果も、期間の内の再開の分類に使う。"""
    ab = load_ab()
    conflict = json.dumps(_wait_conflict("a"), ensure_ascii=False)
    rows = [
        _bash("w", "2026-11-17T01:00:00.000Z", "ndf plan wait /w/a.json", CONDUCTOR),
        _background("w", "2026-11-17T01:00:01.000Z", "w2bbb"),
        _notification("w2bbb", "w", "2026-11-17T01:10:00.000Z", "\n" + conflict),
        _bash("r", "2026-11-18T02:11:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_json_with_status_from_other_tools_is_not_a_result(tmp_path):
    """`status` を持つだけのほかの出力（routes が配列）は結果に取り込まず、続く Bash で止まらない。"""
    ab = load_ab()
    assert ab.result_jsons('{"status":"ok","routes":["route"]}') == []
    assert ab.result_jsons('{"tool":"plan","status":"gate","routes":["route"]}') == []
    assert (
        ab._commands({"tool": "plan", "status": "gate", "next": {"argv": "ndf plan next", "ask": 1}, "routes": {"a": {"argv": [1]}}}) == []
    )
    rows = [
        _bash("x", "2026-11-18T02:00:00.000Z", "some-tool", CONDUCTOR),
        _result("x", "2026-11-18T02:01:00.000Z", '{"status":"ok","routes":["route"]}'),
        _bash("y", "2026-11-18T02:02:00.000Z", "ndf ci watch 1", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert f["R06"]["value"] == 1


@pytest.mark.parametrize(
    "cmd",
    [
        "printf '%s\\n' 'ndf plan next p.json --from impl'",
        "echo ndf ci watch 1",
        "ndf plan next p.json --set 'note= --from impl'",
    ],
)
def test_commands_inside_arguments_are_not_counted(tmp_path, cmd):
    """引数に書かれたコマンドと、値の中の `--from` は数えない（実行ファイルとオプションの位置で照らす）。"""
    ab = load_ab()
    f = _count(ab, tmp_path, {"s.jsonl": [_bash("r", "2026-11-18T02:00:00.000Z", cmd, CONDUCTOR)]})["after"]
    assert sum(e["all_roles"] + e["unresolved"] for e in f.values()) == 0


def test_command_after_assignment_and_path_is_counted(tmp_path):
    ab = load_ab()
    cmd = "NDF_X=1 /usr/local/bin/ndf plan next p.json --from impl"
    f = _count(ab, tmp_path, {"s.jsonl": [_bash("r", "2026-11-18T02:00:00.000Z", cmd, CONDUCTOR)]})["after"]
    assert _resumes(f) == {"R03": 1}


def test_digit_before_spaced_redirect_is_an_argument():
    """`--timeout 30 > f` の 30 は引数のまま残し、`2>&1` の 2 は記述子として外す。"""
    ab = load_ab()
    assert ab.parse_bash("ndf plan wait p.json --timeout 30 > result.json") == [
        {"kind": "cmd", "args": ["ndf", "plan", "wait", "p.json", "--timeout", "30"], "shown": False}
    ]
    assert ab.parse_bash("ndf plan wait p.json 2>&1") == [{"kind": "cmd", "args": ["ndf", "plan", "wait", "p.json"], "shown": True}]
    assert ab.parse_bash("echo '2>x' 2 >y")[0]["args"] == ["echo", "2>x", "2"]


def test_before_follows_next_is_judged_per_matched_command(tmp_path):
    """前の形で、提示された待ちと手で選んだ `gh pr checks` をつなぐと、後者の R06 は value に残る。"""
    ab = load_ab()
    prior = {"tool": "plan", "status": "ok", "summary": "x", "items": [], "next": {"argv": ["python3", "supervise.py", "wait", "a.json"]}}
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "python3 supervise.py run a.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", prior),
        _bash("r", "2026-11-18T02:02:00.000Z", "python3 supervise.py wait a.json && gh pr checks 12", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["before"]
    assert (f["R07"]["value"], f["R07"]["follows_next"]) == (0, 1)
    assert (f["R06"]["value"], f["R06"]["follows_next"]) == (1, 0)


def test_answer_earlier_in_the_same_call_routes_the_resume_to_r13(tmp_path):
    ab = load_ab()
    cmd = "ndf plan next /w/p.json --answer approved && ndf plan next /w/p.json --from impl"
    f = _count(ab, tmp_path, {"s.jsonl": [_bash("r", "2026-11-18T02:00:00.000Z", cmd, CONDUCTOR)]})["after"]
    assert (_resumes(f), f["R13"]["basis"]) == ({"R13": 1}, {"answer": 1})


def test_rows_after_until_are_not_parsed(tmp_path, monkeypatch):
    """期間の終了以降の Bash は字句を読まない（会話が伸びても測り直しの処理が増えない）。"""
    ab = load_ab()
    seen: list[str] = []
    real = ab.parse_bash
    monkeypatch.setattr(ab, "parse_bash", lambda cmd: seen.append(cmd) or real(cmd))
    rows = [
        _bash("a", "2026-11-18T02:00:00.000Z", "ndf ci merge", CONDUCTOR),
        _bash("b", "2026-11-20T15:00:00.000Z", "ndf ci watch 2", CONDUCTOR),
        _result("b", "2026-11-20T15:00:01.000Z", _ok("a")),
    ]
    _jsonl(tmp_path / "tr" / "s.jsonl", rows)
    ab.init_worker(ab.load_rules(RULES))
    out = ab.scan_transcript((str(tmp_path / "tr" / "s.jsonl"), SINCE, UNTIL, []))
    assert seen == ["ndf ci merge"]
    assert len(out["hits"]) == 1


# ---------- 相互レビュー r2: 再開の判定・コマンドの位置・バックグラウンドの結果の取り込み・保存済み出力・前の形の next ----------


def test_resume_with_separator_inside_a_value_is_counted(tmp_path):
    """値の中の `;` があっても、字句を読めた再開は `plan_call` のオプションで決める（r2-1）。"""
    ab = load_ab()
    cmd = "ndf plan next p.json --set 'note=fix; retry' --from impl"
    f = _count(ab, tmp_path, {"s.jsonl": [_bash("r", "2026-11-18T02:00:00.000Z", cmd, CONDUCTOR)]})["after"]
    assert _resumes(f) == {"R03": 1}


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("command ndf ci watch --pr 1", 1),
        ("if ndf ci watch --pr 1; then echo ok; fi", 1),
        ("timeout 600 ndf ci watch --pr 1", 1),
        ("env -u X NDF_Y=1 ndf ci watch --pr 1", 1),
        ("{ ndf ci watch --pr 1; }", 1),
        ("printf '%s\\n' 'command ndf ci watch --pr 1'", 0),
        ("command -v ndf ci watch", 0),
    ],
)
def test_command_behind_builtins_and_keywords_is_counted(tmp_path, cmd, expected):
    """組み込み・予約語の後ろで実行する監視は R06 に数え、引数に書かれたコマンドは数えない（r2-2）。"""
    ab = load_ab()
    f = _count(ab, tmp_path, {"s.jsonl": [_bash("r", "2026-11-18T02:00:00.000Z", cmd, CONDUCTOR)]})["after"]
    assert f.get("R06", {}).get("all_roles", 0) == expected


def test_result_arriving_after_a_partial_read_is_fed(tmp_path):
    """同じタスクの next の出力だけを先に読んでも、完了通知が載せた wait の停止結果を後から取り込む（r2-3）。"""
    ab = load_ab()
    progress = json.dumps(_ok("a"), ensure_ascii=False)
    conflict = json.dumps(_wait_conflict("a"), ensure_ascii=False)
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json && ndf plan wait /w/a.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "w3ccc"),
        _tool("o", "2026-11-18T02:01:00.000Z", "TaskOutput", {"task_id": "w3ccc", "block": False}, CONDUCTOR),
        _result("o", "2026-11-18T02:01:01.000Z", progress),
        _notification("w3ccc", "w", "2026-11-18T02:10:00.000Z", "\n" + progress + "\n" + conflict),
        _bash("r", "2026-11-18T02:11:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR),
    ]
    f = _count(ab, tmp_path, {"s.jsonl": rows})["after"]
    assert _resumes(f) == {"R10": 1}


def test_rereading_a_fed_output_keeps_the_newer_result(tmp_path):
    """取り込み済みの出力を後から読み直しても、間の新しい結果が示した next を直前の結果に残す（r2-4）。"""
    ab = load_ab()
    conflict = json.dumps(_wait_conflict("a"), ensure_ascii=False)
    newer = {"tool": "plan", "status": "ok", "summary": "a: CI 待ち", "items": [], "next": {"argv": ["ndf", "ci", "watch", "--pr", "1"]}}
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan wait /w/a.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "w4ddd"),
        _notification("w4ddd", "w", "2026-11-18T02:10:00.000Z", "\n" + conflict),
        _bash("n", "2026-11-18T02:11:00.000Z", "ndf plan next /w/a.json", CONDUCTOR),
        _result("n", "2026-11-18T02:12:00.000Z", newer),
        _tool("o", "2026-11-18T02:13:00.000Z", "Read", {"file_path": "/tmp/x/tasks/w4ddd.output"}, CONDUCTOR),
        _result("o", "2026-11-18T02:13:01.000Z", "1\t" + conflict),
        _bash("c", "2026-11-18T02:14:00.000Z", "ndf ci watch --pr 1", CONDUCTOR),
    ]
    r06 = _count(ab, tmp_path, {"s.jsonl": rows})["after"]["R06"]
    assert (r06["value"], r06["follows_next"]) == (0, 1)


def test_saved_output_is_opened_once_per_notification(tmp_path, monkeypatch):
    """完了通知に出力のパスだけが載ると、保存済み出力を 1 度だけ開き、履歴と R09 の両方に使う（r2-5）。"""
    ab = load_ab()
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    saved = tmp_path / "tasks" / "t5eee.output"
    saved.parent.mkdir()
    saved.write_text("待っています\n" + stopped + "\n", encoding="utf-8")
    rows = [
        _bash("b", "2026-11-18T02:00:00.000Z", "ndf ci watch --pr 1", CONDUCTOR),
        _background("b", "2026-11-18T02:00:01.000Z", "t5eee", output=saved),
        _notification_at("t5eee", "b", "2026-11-18T02:10:00.000Z", saved),
    ]
    _jsonl(tmp_path / "tr" / "s.jsonl", rows)
    opened: list[str] = []
    real = ab.open_task_output
    monkeypatch.setattr(ab, "open_task_output", lambda p, a, r: opened.append(p) or real(p, a, r))
    ab.init_worker(ab.load_rules(RULES))
    out = ab.scan_transcript((str(tmp_path / "tr" / "s.jsonl"), SINCE, UNTIL, [str(tmp_path)]))
    assert len(opened) == 1
    assert list(out["causes"].values()) == [["b"]]


def test_read_saved_output_returns_results_and_the_long_ci_result(tmp_path):
    """1 度の読み取りで NDF の結果の並びと、上限を超える行の ci の結果の両方を返す（r2-5）。"""
    ab = load_ab()
    big = tmp_path / "tasks" / "t6fff.output"
    big.parent.mkdir()
    pad = "x" * (ab.OUTPUT_READ_LIMIT + 10)
    big.write_text(json.dumps({"tool": "ci", "status": "stopped", "pad": pad, "cause": {"kind": "infra_wait"}}) + "\n", encoding="utf-8")
    results, text = ab.read_saved_output(str(big), str(big), [str(tmp_path)])
    assert results == []
    assert json.loads(text)["cause"]["kind"] == "infra_wait"
    small = tmp_path / "tasks" / "t7ggg.output"
    small.write_text(json.dumps(_ok("a")) + "\n", encoding="utf-8")
    assert ab.read_saved_output(str(small), str(small), [str(tmp_path)]) == ([_ok("a")], None)


def test_old_form_string_next_replaces_the_prior_result(tmp_path):
    """前の形の `step_result.py` の文字列の next の結果も直前の結果にし、先行する argv を残さない（r2-6）。"""
    ab = load_ab()
    prior = {"tool": "plan", "status": "ok", "summary": "x", "items": [], "next": {"argv": ["python3", "supervise.py", "wait", "a.json"]}}
    old = {"tool": "merged", "status": "ok", "summary": "y", "items": [], "metrics": {}, "next": "次は CI を待つ"}
    assert ab.result_jsons(json.dumps(old, ensure_ascii=False)) == [old]
    assert ab._commands(old) == []
    rows = [
        _bash("p", "2026-11-18T02:00:00.000Z", "python3 supervise.py run a.json", CONDUCTOR),
        _result("p", "2026-11-18T02:01:00.000Z", prior),
        _bash("m", "2026-11-18T02:02:00.000Z", "python3 merged.py", CONDUCTOR),
        _result("m", "2026-11-18T02:03:00.000Z", old),
        _bash("r", "2026-11-18T02:04:00.000Z", "python3 supervise.py wait a.json", CONDUCTOR),
    ]
    r07 = _count(ab, tmp_path, {"s.jsonl": rows})["before"]["R07"]
    assert (r07["value"], r07["follows_next"]) == (1, 0)


# ---------- 相互レビュー r3: バックグラウンドの結果の順と直前の結果・env の作業場所・途中で読んだ監視の完了 ----------


def _next_to(plan: str, pr: int) -> dict:
    return {**_ok(plan), "next": {"argv": ["ndf", "ci", "watch", "--pr", str(pr)]}}


def test_backfilled_earlier_result_does_not_replace_the_latest_next(tmp_path):
    """末尾で B を読んだ後に完了通知で A・B の全体を読んでも、直前の結果は最後に出た B のまま（r3-1）。"""
    ab = load_ab()
    a, b = (json.dumps(r, ensure_ascii=False) for r in (_next_to("a", 2), _next_to("b", 1)))
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json; ndf plan next /w/b.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "r3aaa"),
        _bash("t", "2026-11-18T02:01:00.000Z", "tail -n 1 /tmp/x/tasks/r3aaa.output", CONDUCTOR),
        _result("t", "2026-11-18T02:01:01.000Z", b),
        _notification("r3aaa", "w", "2026-11-18T02:10:00.000Z", "\n" + a + "\n" + b),
        _bash("c", "2026-11-18T02:11:00.000Z", "ndf ci watch --pr 1", CONDUCTOR),
    ]
    r06 = _count(ab, tmp_path, {"s.jsonl": rows})["after"]["R06"]
    assert (r06["value"], r06["follows_next"]) == (0, 1)


def test_head_after_tail_does_not_pair_results_in_reverse(tmp_path):
    """末尾で B、先頭で A の順に読んでも B を a.json に結ばず、全体を読んだ時点で出た順に組み直す（r3-2）。"""
    ab = load_ab()
    a, b = (json.dumps(r, ensure_ascii=False) for r in (_wait_conflict("a"), _gate("b")))
    head = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan wait /w/a.json; ndf plan wait /w/b.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "r3bbb"),
        _bash("t", "2026-11-18T02:01:00.000Z", "tail -n 1 /tmp/x/tasks/r3bbb.output", CONDUCTOR),
        _result("t", "2026-11-18T02:01:01.000Z", b),
        _bash("h", "2026-11-18T02:02:00.000Z", "head -n 1 /tmp/x/tasks/r3bbb.output", CONDUCTOR),
        _result("h", "2026-11-18T02:02:01.000Z", a),
    ]
    resume = _bash("r", "2026-11-18T02:11:00.000Z", "ndf plan next /w/a.json --from impl", CONDUCTOR)
    full = _notification("r3bbb", "w", "2026-11-18T02:10:00.000Z", "\n" + a + "\n" + b)
    assert _resumes(_count(ab, tmp_path / "partial", {"s.jsonl": [*head, resume]})["after"]) == {"R03": 1}
    assert _resumes(_count(ab, tmp_path / "full", {"s.jsonl": [*head, full, resume]})["after"]) == {"R10": 1}


@pytest.mark.parametrize("wrapper", ["env -C /w", "env --chdir /w", "env --chdir=/w", "env -C/w"])
def test_env_chdir_places_the_plan_in_that_directory(tmp_path, wrapper):
    """`env -C /w ndf plan wait a.json` のプランは /w/a.json の鍵にし、続く再開を R10 に寄せる（r3-3）。"""
    ab = load_ab()
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", f"{wrapper} ndf plan wait a.json", _at("/other")),
        _result("w", "2026-11-18T02:01:00.000Z", _wait_conflict("a")),
        _bash("r", "2026-11-18T02:02:00.000Z", "ndf plan next /w/a.json --from impl", _at("/other")),
    ]
    assert _resumes(_count(ab, tmp_path, {"s.jsonl": rows})["after"]) == {"R10": 1}
    assert ab.command_cwd(["env", "-C", "b", "ndf"], "/w") == "/w/b"
    assert ab.command_cwd(["ndf", "-C", "b"], "/w") == "/w"


def test_completion_is_checked_after_a_mid_run_read(tmp_path):
    """実行中の TaskOutput で先の監視の正常結果を読んでも、完了時の保存済み出力の infra_wait を R09 に数える（r3-4）。"""
    ab = load_ab()
    ok = json.dumps({"tool": "ci", "status": "ok", "summary": "通った"}, ensure_ascii=False)
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    saved = tmp_path / "tasks" / "r3ccc.output"
    saved.parent.mkdir()
    saved.write_text(ok + "\n" + stopped + "\n", encoding="utf-8")
    rows = [
        _bash("b", "2026-11-18T02:00:00.000Z", "ndf ci watch --pr 1 && ndf ci watch --pr 2", CONDUCTOR),
        _background("b", "2026-11-18T02:00:01.000Z", "r3ccc", output=saved),
        _tool("o", "2026-11-18T02:01:00.000Z", "TaskOutput", {"task_id": "r3ccc", "block": False}, CONDUCTOR),
        _result("o", "2026-11-18T02:01:01.000Z", ok),
        _notification_at("r3ccc", "b", "2026-11-18T02:10:00.000Z", saved),
    ]
    _jsonl(tmp_path / "tr" / "s.jsonl", rows)
    out = ab.count_transcripts([str(tmp_path / "tr")], SINCE, UNTIL, 1, ab.load_rules(RULES), [str(tmp_path)])
    assert out["by_form"]["after"]["R09"]["all_roles"] == 1


# ---------- 相互レビュー r4: 出た順が決まった後の直前の結果・前の結果の補完・完了通知の最後の監視の結果 ----------


def test_settled_order_moves_the_latest_next_to_the_last_output(tmp_path):
    """末尾で B、先頭で A を読み、完了通知で A・B の全体を読めば、直前の結果を最後に出た B に直す（r4-1）。"""
    ab = load_ab()
    a, b = (json.dumps(r, ensure_ascii=False) for r in (_next_to("a", 2), _next_to("b", 1)))
    rows = [
        _bash("w", "2026-11-18T02:00:00.000Z", "ndf plan next /w/a.json; ndf plan next /w/b.json", CONDUCTOR),
        _background("w", "2026-11-18T02:00:01.000Z", "r4aaa"),
        _bash("t", "2026-11-18T02:01:00.000Z", "tail -n 1 /tmp/x/tasks/r4aaa.output", CONDUCTOR),
        _result("t", "2026-11-18T02:01:01.000Z", b),
        _bash("h", "2026-11-18T02:02:00.000Z", "head -n 1 /tmp/x/tasks/r4aaa.output", CONDUCTOR),
        _result("h", "2026-11-18T02:02:01.000Z", a),
        _notification("r4aaa", "w", "2026-11-18T02:10:00.000Z", "\n" + a + "\n" + b),
        _bash("c", "2026-11-18T02:11:00.000Z", "ndf ci watch --pr 1", CONDUCTOR),
    ]
    r06 = _count(ab, tmp_path, {"s.jsonl": rows})["after"]["R06"]
    assert (r06["value"], r06["follows_next"]) == (0, 1)


def test_completion_text_uses_the_last_ci_result(tmp_path):
    """完了通知の本文に先の監視の ok と後の infra_wait が載れば、最後の infra_wait を R09 に数える（r4-3）。"""
    ab = load_ab()
    ok = json.dumps({"tool": "ci", "status": "ok", "summary": "通った"}, ensure_ascii=False)
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    rows = [
        _bash("b", "2026-11-18T02:00:00.000Z", "ndf ci watch --pr 1 && ndf ci watch --pr 2", CONDUCTOR),
        _background("b", "2026-11-18T02:00:01.000Z", "r4ccc"),
        _notification("r4ccc", "b", "2026-11-18T02:10:00.000Z", "\n" + ok + "\n" + stopped),
    ]
    _jsonl(tmp_path / "tr" / "s.jsonl", rows)
    out = ab.count_transcripts([str(tmp_path / "tr")], SINCE, UNTIL, 1, ab.load_rules(RULES), [str(tmp_path)])
    assert out["by_form"]["after"]["R09"]["all_roles"] == 1


# ---------- 相互レビュー r5: 完了の結果で途中の読み取りの当たりを置き換える ----------


def _r5_rows(saved, task: str, read_after: bool) -> list[dict]:
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    read = [
        _tool(
            "o",
            "2026-11-18T02:01:00.000Z" if not read_after else "2026-11-18T02:11:00.000Z",
            "TaskOutput",
            {"task_id": task, "block": False},
            CONDUCTOR,
        ),
        _result("o", "2026-11-18T02:01:01.000Z" if not read_after else "2026-11-18T02:11:01.000Z", stopped),
    ]
    done = [_notification_at(task, "b", "2026-11-18T02:10:00.000Z", saved)]
    return [
        _bash("b", "2026-11-18T02:00:00.000Z", "ndf ci watch --pr 1 && ndf ci watch --pr 2", CONDUCTOR),
        _background("b", "2026-11-18T02:00:01.000Z", task, output=saved),
        *(done + read if read_after else read + done),
    ]


@pytest.mark.parametrize("read_after", [False, True])
def test_completion_replaces_a_mid_run_cause(tmp_path, read_after):
    """途中で先の監視の infra_wait を読んでも、完了の最後の結果が ok なら R09 に数えない。完了の後の読み直しでも変えない（r5-1）。"""
    ab = load_ab()
    ok = json.dumps({"tool": "ci", "status": "ok", "summary": "通った"}, ensure_ascii=False)
    stopped = json.dumps({"tool": "ci", "status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    saved = tmp_path / "tasks" / "r5aaa.output"
    saved.parent.mkdir()
    saved.write_text(stopped + "\n" + ok + "\n", encoding="utf-8")
    _jsonl(tmp_path / "tr" / "s.jsonl", _r5_rows(saved, "r5aaa", read_after))
    out = ab.count_transcripts([str(tmp_path / "tr")], SINCE, UNTIL, 1, ab.load_rules(RULES), [str(tmp_path)])
    assert out["by_form"]["after"].get("R09", {}).get("all_roles", 0) == 0
    assert not out.get("results_with_cause")


# ---------- I-001: `_Scan._take` の読み直し・出た順の確定の振る舞いを固定する ----------


def _take_scan(ab):
    scan = ab._Scan(ab.build_matcher(ab.load_rules(RULES)), False, [])
    scan.tool_use(CONDUCTOR, {"id": "w", "name": "Bash", "input": {"command": "ndf plan next /w/a.json; ndf plan next /w/b.json"}}, True)
    return scan


def test_take_ignores_empty_and_ordered_rereads():
    """結果が無い回と、順の決まった並びの読み直しは、取り込み・直前の結果・順の不明の印のどれも変えない（I-001）。"""
    ab = load_ab()
    scan = _take_scan(ab)
    a, b = _next_to("a", 2), _next_to("b", 1)
    scan._take("w", [])
    assert ("w" in scan.fed, "w" in scan.unordered, scan.conv.last) == (False, False, [])
    scan._take("w", [a, b])
    fed, applied, last = list(scan.fed["w"]), set(scan.applied["w"]), scan.conv.last
    assert last == [a, b] and "w" not in scan.unordered
    other = [{"tool": "x"}]
    scan.conv.last = other
    for again in ([a, b], [b], [a]):
        scan._take("w", again)
        assert scan.fed["w"] == fed and scan.applied["w"] == applied and scan.conv.last is other
        assert "w" not in scan.unordered


def test_take_marks_unordered_and_settles_on_a_full_reread():
    """末尾の B・先頭の A の順に読むと順の不明として持ち、新しい結果の無い全体の読み直しで順を決めて直前の結果を B に直す（I-001）。"""
    ab = load_ab()
    scan = _take_scan(ab)
    a, b = _next_to("a", 2), _next_to("b", 1)
    scan._take("w", [b])
    assert scan.conv.last == [b] and "w" not in scan.unordered
    scan._take("w", [a])
    assert "w" in scan.unordered and scan.conv.last == [a]
    assert [r for _, r in scan.fed["w"]] == [b, a]
    scan._take("w", [a, b])
    assert "w" not in scan.unordered
    assert [r for _, r in scan.fed["w"]] == [a, b]
    assert scan.conv.last == [b] and scan.last_of["w"] == [b]


def test_take_settling_keeps_a_newer_result_of_another_call():
    """順が決まっても、部分読みの後に別の呼び出しが置いた直前の結果は B に戻さない（I-001）。"""
    ab = load_ab()
    scan = _take_scan(ab)
    a, b = _next_to("a", 2), _next_to("b", 1)
    scan._take("w", [b])
    scan._take("w", [a])
    newer = [{"tool": "plan", "status": "ok"}]
    scan.conv.last = newer
    scan._take("w", [a, b])
    assert "w" not in scan.unordered and scan.conv.last is newer


def test_take_full_read_without_prior_unordered_does_not_move_the_latest():
    """順の不明でない並びを全体で読み、新しい結果が前の行だけなら、直前の結果を変えない（I-001）。"""
    ab = load_ab()
    scan = _take_scan(ab)
    a, b = _next_to("a", 2), _next_to("b", 1)
    scan._take("w", [b])
    other = [{"tool": "x"}]
    scan.conv.last = other
    scan._take("w", [a, b])
    assert "w" not in scan.unordered
    assert [r for _, r in scan.fed["w"]] == [a, b]
    assert scan.conv.last is other


# ---------- _Conversation.saw_result: 同じ結果で結んだ保存先の鍵で記録する ----------


def _plan(ab, cmd: str) -> dict:
    return ab.plan_call(cmd.split(), "/w")


def test_saw_result_ignores_none_and_planless_calls():
    ab = load_ab()
    conv = ab._Conversation()
    conv.saw_result(None, _gate("a"))
    conv.saw_result({**_plan(ab, "ndf plan next /w/a.json"), "ids": []}, _gate("a"))
    assert (conv.seq, conv.latest, conv.name_of, conv.alias) == (0, {}, {}, {})


def test_saw_result_next_records_under_the_state_dir_learned_from_the_same_result():
    """省略形の `next` の結果が `wait --state-dir` を示したら、その結果自身を実際の保存先の鍵で記録する。"""
    ab = load_ab()
    conv = ab._Conversation()
    call = _plan(ab, "ndf plan next /w/a.json")
    actual = _plan(ab, "ndf plan wait /w/a.json --state-dir /w/a.state")["ids"][0]
    gate = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/a.state"]}}
    conv.saw_result(call, gate)
    assert conv.alias == {call["ids"][0]: actual}
    assert list(conv.latest) == [f"id:{actual}"]
    assert conv.latest[f"id:{actual}"] == (1, gate, gate, None)
    assert conv.name_of == {actual: "a"}


def test_saw_result_queue_rows_use_learned_key_per_plan_and_record_done():
    """items の数が合う queue の行は、同じ結果の routes で結んだ鍵と結ばない鍵をプランごとに使い分ける。"""
    ab = load_ab()
    conv = ab._Conversation()
    call = _plan(ab, "ndf plan queue /w/a.json /w/b.json")
    raw_a, raw_b = call["ids"]
    actual_a = _plan(ab, "ndf plan wait /w/a.json --state-dir /w/x")["ids"][0]
    row_a = {
        "kind": "plan",
        "name": "a",
        "status": "gate",
        "routes": {"ok": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/x"]}},
    }
    row_b = {"kind": "plan", "name": "b", "status": "ok"}
    res = {"tool": "plan", "status": "gate", "summary": "待つ", "items": [row_a, row_b]}
    conv.saw_result(call, res)
    assert conv.alias == {raw_a: actual_a}
    assert conv.latest == {f"id:{actual_a}": (1, row_a, res, "a"), f"id:{raw_b}": (1, row_b, res, "b")}
    assert conv.name_of == {actual_a: "a", raw_b: "b"}
    assert conv.done == {call["queue"]: {"a": "gate", "b": "ok"}}
    assert conv.group_names == {}


def test_saw_result_unmatched_items_use_group_of_learned_keys():
    """items の数が渡したプランと合わない行は、同じ結果で結んだ鍵を含むプランの鍵の組で記録する。"""
    ab = load_ab()
    conv = ab._Conversation()
    call = _plan(ab, "ndf plan wait /w/a.json /w/b.json")
    raw_b = call["ids"][1]
    actual_a = _plan(ab, "ndf plan wait /w/a.json --state-dir /w/x")["ids"][0]
    res = {
        "tool": "plan",
        "status": "stopped",
        "summary": "x",
        "items": [{"kind": "plan", "name": "c", "status": "stopped"}],
        "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/x"]},
    }
    conv.saw_result(call, res)
    group = "\x00".join(sorted([actual_a, raw_b]))
    assert conv.group_names == {group: ["c"]}
    assert list(conv.latest) == [f"group:{group}|c"]
    assert conv.name_of == {}
    assert conv.done == {}


def test_saw_result_rebinding_keeps_previous_history_on_the_old_state_dir():
    """2 回目の結果が別の保存先を示したら、その結果は新しい保存先の鍵で記録し、前の鍵の履歴は動かさない。"""
    ab = load_ab()
    conv = ab._Conversation()
    call = _plan(ab, "ndf plan next /w/a.json")
    first = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/x"]}}
    second = {**_gate("a"), "next": {"argv": ["ndf", "plan", "wait", "/w/a.json", "--state-dir", "/w/y"]}}
    conv.saw_result(call, first)
    conv.saw_result(call, second)
    x = _plan(ab, "ndf plan wait /w/a.json --state-dir /w/x")["ids"][0]
    y = _plan(ab, "ndf plan wait /w/a.json --state-dir /w/y")["ids"][0]
    assert conv.alias == {call["ids"][0]: y}
    assert conv.latest == {f"id:{x}": (1, first, first, None), f"id:{y}": (2, second, second, None)}
    assert conv.name_of == {x: "a", y: "a"}
