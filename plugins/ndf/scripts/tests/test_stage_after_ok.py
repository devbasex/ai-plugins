"""#2288: 「リリース後テスト」の進行の印は、そのステップが ok で終わった後に付く（ほかの工程は今と同じく始めに付く）。

記録のスクリプト（計画の `記録`）とステップの中身が同じ記録ファイルへ 1 行ずつ足し、呼ばれた順を見る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
WF_LIB = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine, release_templates  # noqa: E402
from supervise_lib.claude import UsageLimit  # noqa: E402
from supervise_lib.slow import SlowAction  # noqa: E402
from supervise_lib.state import STAGES_AFTER_OK  # noqa: E402

VERIFY = "リリース後テスト"


def recorder(tmp_path: Path, code: int = 0) -> tuple[Path, Path]:
    log = tmp_path / "order.log"
    rec = tmp_path / "rec.sh"
    rec.write_text(f'#!/usr/bin/env bash\necho "印 $1 $2 $3" >> {log}\nexit {code}\n', encoding="utf-8")
    return rec, log


def plan_of(tmp_path: Path, rec: Path, log: Path, verify_cmd: str, **verify_extra) -> dict:
    steps = [
        {"id": "merge", "type": "run", "stage": "Pull Request", "cmd": f"echo 中身 merge >> {log}", "next": "rv"},
        {"id": "rv", "type": "run", "stage": VERIFY, "cmd": verify_cmd, "next": "retro", **verify_extra},
        {"id": "retro", "type": "run", "stage": "振り返り", "cmd": f"echo 中身 retro >> {log}", "next": "end"},
    ]
    return {"フェーズ": "試験", "課題": [11], "作業場所": str(tmp_path), "記録": str(rec), "steps": steps}


def lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_verification_mark_comes_after_the_step_and_others_before(tmp_path):
    """受け入れ条件 6・8: リリース後テストの印は中身の後、ほかの工程の印は中身の前に付く。"""
    rec, log = recorder(tmp_path)
    s = engine.Engine(plan_of(tmp_path, rec, log, f"echo 中身 rv >> {log}"), tmp_path / "state")
    assert "結果: 完了" in s.run()
    assert lines(log) == [
        "印 11 stage Pull Request",
        "中身 merge",
        "中身 rv",
        f"印 11 stage {VERIFY}",
        "印 11 stage 振り返り",
        "中身 retro",
    ]


def test_a_failed_verification_leaves_no_mark_and_a_retry_marks_once(tmp_path):
    """受け入れ条件 7: 落ちたら印が付かず、--from でやり直して ok で終われば 1 回だけ付く。"""
    rec, log = recorder(tmp_path)
    flag = tmp_path / "pass"
    cmd = f"echo 中身 rv >> {log}; test -e {flag}"
    s = engine.Engine(plan_of(tmp_path, rec, log, cmd), tmp_path / "state")
    assert "結果: 止まった" in s.run()
    assert f"印 11 stage {VERIFY}" not in lines(log)
    flag.touch()
    s = engine.Engine(plan_of(tmp_path, rec, log, cmd), tmp_path / "state")
    assert "結果: 完了" in s.run(start="rv")
    assert lines(log).count(f"印 11 stage {VERIFY}") == 1
    assert lines(log)[-3:] == [f"印 11 stage {VERIFY}", "印 11 stage 振り返り", "中身 retro"]


def test_a_gate_leaves_no_verification_mark(tmp_path):
    """受け入れ条件 7: 承認ゲート（終了コード 10〜19）で止まると印が付かない。"""
    rec, log = recorder(tmp_path)
    s = engine.Engine(plan_of(tmp_path, rec, log, "exit 10", gate_next="end"), tmp_path / "state")
    s.run()
    assert f"印 11 stage {VERIFY}" not in lines(log)


def test_a_usage_limit_leaves_no_verification_mark(tmp_path, monkeypatch):
    """受け入れ条件 7: 利用上限で止まると印が付かない。"""
    rec, log = recorder(tmp_path)
    s = engine.Engine(plan_of(tmp_path, rec, log, "true"), tmp_path / "state")
    real = s.run_step.execute

    def limited(ctx, step):
        if step["id"] == "rv":
            raise UsageLimit("利用上限")
        return real(ctx, step)

    monkeypatch.setattr(s.run_step, "execute", limited)
    assert "結果: 止まった" in s.run()
    assert f"印 11 stage {VERIFY}" not in lines(log)


def test_a_slow_abort_leaves_no_verification_mark(tmp_path, monkeypatch):
    """受け入れ条件 7: 遅れの見張りが打ち切ると印が付かない。"""
    rec, log = recorder(tmp_path)
    s = engine.Engine(plan_of(tmp_path, rec, log, "true"), tmp_path / "state")
    real = s.run_step.execute

    def slow(ctx, step):
        if step["id"] == "rv":
            raise SlowAction("kill", "試験の遅れ")
        return real(ctx, step)

    monkeypatch.setattr(s.run_step, "execute", slow)
    assert "結果: 止まった" in s.run()
    assert f"印 11 stage {VERIFY}" not in lines(log)


def test_a_failing_record_after_ok_does_not_stop_the_plan(tmp_path):
    """I5: ok の後の印が書けなくても止めず、process に残す。"""
    rec, log = recorder(tmp_path, code=6)
    s = engine.Engine(plan_of(tmp_path, rec, log, "true"), tmp_path / "state")
    assert "結果: 完了" in s.run()
    assert any(p["run"] == f"rec.sh 11 stage {VERIFY}" for p in s.state.process)


def test_the_after_ok_stages_are_workflow_stages_and_the_template_stage():
    """決定 1: 後へ回す工程は工程表の行名で、まとめのプランの release-verify の stage と同じ。"""
    step = release_templates.release_verify_step("m1", "#1", "20")
    assert step["stage"] in STAGES_AFTER_OK
    for stage in STAGES_AFTER_OK:
        p = subprocess.run(["bash", "-c", f'. "{WF_LIB}"; wf_is_stage "$1"', "_", stage], capture_output=True, text=True)
        assert p.returncode == 0, stage


def test_the_release_verify_worker_does_not_record_progress():
    """決定 2・決定 5: まとめのプランの release-verify は worker に進捗記録を打たせない。"""
    prompt = release_templates.release_verify_step("m1", "#1", "20")["prompt"]
    assert "進捗記録" in prompt and "打たない" in prompt
