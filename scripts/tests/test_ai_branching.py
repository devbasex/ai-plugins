"""`scripts/measure/ai-branching.py` の規則の表と分類の表（#3412 の Task 2）を、模した記録で確かめる。

- 退行: 今の規則の表（形 `before`）で、`main` の出力の形と値が変わらない
- 規則の表（`load_rules`）: `rule`・`form`・`kind` を確かめ、前と後の行が形ごとに分かれる。欠けた行は終了コード 2
- 分類の表（`load_measures`）: 規則の番号ごとの `measure` と形ごとの出所。規則の表の番号が分類の表に無ければ終了コード 2
"""

from __future__ import annotations

import importlib
import json
import multiprocessing
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AB = HERE.parent / "measure" / "ai-branching.py"
RULES = HERE.parent / "measure" / "ai-branching-rules.json"
MEASURES = HERE.parent / "measure" / "ai-branching-measures.json"


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


def _bash(tid: str, at: str, cmd: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": at,
        "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]},
    }


def _tool(tid: str, at: str, name: str, inp: dict) -> dict:
    return {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid: str, at: str, text: str) -> dict:
    return {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def build(tmp: Path) -> dict[str, Path]:
    """旧い形の記録の見本。時刻の形は実データ（2026-11-21 に確かめた）を写す。"""
    ndf = tmp / ".ndf"
    state = tmp / "state"
    tr = tmp / "transcripts"
    # 旧い progress.jsonl の at は秒まで・+00:00
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
    # 旧い台帳の at は秒まで・Z
    _jsonl(
        ndf / "parallel" / "p4" / "ledger.jsonl",
        [
            {"kind": "ask", "at": "2026-10-14T03:00:00Z", "msg": {"source": "lane"}},
            {"kind": "resume", "at": "2026-10-14T03:01:00Z", "msg": {"phase": "settle", "result": "rejected"}},
            {"kind": "resume", "at": "2026-10-13T03:01:00Z", "msg": {"phase": "confirm", "take": True}},
        ],
    )
    # 会話の JSONL の timestamp は UTC・ミリ秒・Z
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


def run_main(paths: dict[str, Path], *extra: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(AB),
        "--since",
        "2026-10-14",
        "--ndf",
        str(paths["ndf"]),
        "--state",
        str(paths["state"]),
        "--transcripts",
        str(paths["transcripts"]),
        "--jobs",
        "1",
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# ---------- 退行: 今の規則の表で出力が変わらない ----------


EXPECTED = {
    "since": "2026-10-14",
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


def test_main_output_is_unchanged_with_current_rules(tmp_path):
    p = run_main(build(tmp_path))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == EXPECTED


def test_main_skip_transcripts_keeps_keys(tmp_path):
    p = run_main(build(tmp_path), "--skip-transcripts")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert list(out) == ["since", "progress", "ledger"]
    assert out["progress"] == EXPECTED["progress"]


# ---------- 規則の表 ----------


def _write(tmp: Path, name: str, data) -> Path:
    path = tmp / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_rules_splits_rows_by_form():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    forms = {r["form"] for r in rules}
    assert forms == {"before", "after"}
    before = [r for r in rules if r["form"] == "before"]
    after = [r for r in rules if r["form"] == "after"]
    # 前の形は今の 25 行の Bash の規則に、Tool と文の行を足したもの
    assert sum(r["kind"] == "bash" for r in before) == 25
    assert {r["kind"] for r in before} == {"bash", "tool", "phrase"}
    # 後の形の再開のコマンドは R03 の 1 組だけ（R10・R13 は別の正規表現を持たない）
    assert {r["rule"] for r in after if r["kind"] == "bash"} >= {"R03", "R06", "R07", "R08"}
    assert not [r for r in after if r["rule"] in ("R10", "R13")]


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
        main_rows = [r for r in r09 if r["form"] == form and not r.get("aside")]
        aside_rows = [r for r in r09 if r["form"] == form and r.get("aside")]
        assert [r.get("pattern") for r in aside_rows] == ["gh run rerun"], form
        assert main_rows and all(r["kind"] in ("phrase", "cause") for r in main_rows), form
    after = [r for r in rules if r["form"] == "after"]
    stopped = {"status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}
    other = {"status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}}
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
    out = ab.count_transcripts([str(tmp_path / "tr")], "2026-10-14", 1, after)
    name = next(r["name"] for r in after if r["kind"] == "cause")
    assert out["results_with_cause"] == {name: 1}
    assert out["bash_by_rule"]["CI の打ち直し（gh run rerun）"] == 1


def _background(tid: str, at: str, task: str, *, with_id_field: bool = True) -> dict:
    """バックグラウンド起動の応答。形は 2026-11-20 の実ログ（`toolUseResult.backgroundTaskId` と本文の ID）を写す。"""
    row = _result(
        tid,
        at,
        f"Command running in background with ID: {task}. Output is being written to: /tmp/x/tasks/{task}.output. "
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
    stopped = json.dumps({"status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    other = json.dumps({"status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}})
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
    out = ab.count_transcripts([str(tmp_path / "tr")], "2026-10-14", 1, after)
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
    stopped = json.dumps({"status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    saved = tmp_path / "tasks" / "t1aaa.output"
    saved.parent.mkdir()
    saved.write_text("待っています\n" + stopped + "\n", encoding="utf-8")
    watch = "ndf ci watch --pr 1 --required test --budget 600"
    _jsonl(
        tmp_path / "tr" / "s.jsonl",
        [
            _bash("b1", "2026-10-14T04:00:00.000Z", watch),
            _background("b1", "2026-10-14T04:00:01.000Z", "t1aaa"),
            _notification_at("t1aaa", "b1", "2026-10-14T04:10:00.000Z", saved),
            # b2: 保存済み出力が消えている → 数えず、読めなかった回に出す
            _bash("b2", "2026-10-14T04:11:00.000Z", watch),
            _background("b2", "2026-10-14T04:11:01.000Z", "t2bbb"),
            _notification_at("t2bbb", "b2", "2026-10-14T04:20:00.000Z", tmp_path / "tasks" / "t2bbb.output"),
        ],
    )
    out = ab.count_transcripts([str(tmp_path / "tr")], "2026-10-14", 1, after)
    assert out["results_with_cause"] == {name: 1}
    assert out["results_without_cause"] == {name: 1}


def test_after_r09_skips_results_that_read_several_outputs(tmp_path):
    """複数のタスクの出力をまとめて読んだ結果は、どのタスクにも帰属させない。"""
    ab = load_ab()
    after = [r for r in ab.load_rules(RULES) if r["form"] == "after"]
    name = next(r["name"] for r in after if r["kind"] == "cause")
    stopped = json.dumps({"status": "stopped", "cause": {"kind": "infra_wait", "detail": "ランナー待ち"}}, ensure_ascii=False)
    other = json.dumps({"status": "stopped", "cause": {"kind": "test_failed", "detail": "x"}})
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
    out = ab.count_transcripts([str(tmp_path / "tr")], "2026-10-14", 1, after)
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
        {"rule": "R09", "form": "after", "kind": "bash", "name": "x", "pattern": "x", "aside": "yes"},
    ],
)
def test_load_rules_rejects_bad_cause_or_aside_with_exit_2(tmp_path, row):
    ab = load_ab()
    with pytest.raises(SystemExit) as e:
        ab.load_rules(_write(tmp_path, "rules.json", [row]))
    assert e.value.code == 2


def test_load_rules_accepts_zero_rows(tmp_path):
    ab = load_ab()
    rules = ab.load_rules(_write(tmp_path, "rules.json", []))
    assert rules == []
    # 規則が 0 行でも会話の数えは形を保って空を返す
    paths = build(tmp_path)
    out = ab.count_transcripts([str(paths["transcripts"])], "2026-10-14", 1, rules)
    assert out["bash_by_rule"] == {}
    assert out["tools"] == {}
    assert out["results_with_phrase"] == {}


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
    out = ab.count_transcripts([str(paths["transcripts"])], "2026-10-14", 1, before)
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


# ---------- 分類の表 ----------


def test_load_measures_covers_every_rule_number():
    ab = load_ab()
    measures = ab.load_measures(MEASURES)
    assert sorted(measures) == [f"R{n:02d}" for n in range(1, 28)]
    by_g: dict[str, set[str]] = {}
    for rule, m in measures.items():
        by_g.setdefault(m["measure"], set()).add(rule)
    # 洗い出しの表の判定: できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、数えられない → none
    assert by_g["G3"] == {"R14", "R21"}
    assert by_g["none"] == {"R26", "R27"}
    assert len(by_g["G1"]) == 11
    assert len(by_g["G2"]) == 12


def test_rules_and_measures_agree():
    ab = load_ab()
    rules = ab.load_rules(RULES)
    measures = ab.load_measures(MEASURES)
    ab.check_measures(rules, measures)
    # 規則の行を持つ形の出所は none でない（数える行があるのに数えない、を作らない）
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


def test_main_stops_with_exit_2_on_bad_rules(tmp_path, monkeypatch, capsys):
    ab = load_ab()
    monkeypatch.setattr(ab, "RULES_FILE", _write(tmp_path, "rules.json", [{"form": "before", "kind": "tool", "name": "x", "tool": "x"}]))
    monkeypatch.setattr(sys, "argv", ["ai-branching.py", "--skip-transcripts", "--ndf", str(tmp_path), "--state", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        ab.main()
    assert e.value.code == 2
    assert "rule" in capsys.readouterr().err
