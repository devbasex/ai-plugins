"""drive.py が結果の出口（done・stopped・pause）でリファクタリング計画のコメントを 1 度だけ書き直す（#1692 の決定 1）。

`refactor.py` の子コマンドは偽物の `call` が模す。`plan-comment` を打った順番と回数、結果 JSON の `unpublished` を見る。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PY = sys.executable
ARGV = ["7", "--scope", "src", "--baseline-test", "pytest"]


def _load():
    spec = importlib.util.spec_from_file_location("cross_refactoring_drive_plan_comment", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rf = _load()


@pytest.fixture(autouse=True)
def durable_in_process(monkeypatch):
    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(rf.durable, "exit_leaving_pending", leave)
    yield
    rf.durable.close()


class Fake:
    """`refactor.py` と起動のスクリプトを模す。`plan-comment` の応答は `comment` で決める。"""

    def __init__(self, tmp: Path, gate: str = "passed", gate_rc: int | None = None, phase: str = "final", launch_rc: int = 0):
        self.tmp, self.gate, self.gate_rc, self.phase, self.launch_rc = tmp, gate, gate_rc, phase, launch_rc
        self.comment = (0, "PLAN_COMMENT=updated\nUNPUBLISHED=0\n")
        self.init_rc = 0
        self.calls: list[str] = []
        self.state = {"items": [{"status": "verified"}, {"status": "verified"}, {"status": "reverted"}], "plan": {"base_sha": "x"}}
        self.save()

    def save(self):
        (self.tmp / "cross-refactoring-rf7-state.json").write_text(json.dumps(self.state))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name == "launch-cli.sh":
            self.calls.append("launch")
            return self.launch_rc, ""
        if name != "refactor.py":
            return 0, "abc\n"
        sub = cmd[2]
        self.calls.append(sub)
        if sub == "init":
            return self.init_rc, f"ID=7\nTMP_DIR={self.tmp}\nPHASE={self.phase}\nIMPL=codex\nRUNTIMES=codex\nWORK={self.tmp}\n"
        if sub == "plan-comment":
            return self.comment
        if sub == "readopt":
            return 2, ""  # 入る候補が無い（最終ゲートへ）
        if sub == "final-gate":
            self.state["final_gate"] = {"status": "passed" if self.gate in ("passed", "cross-review") else self.gate}
            self.save()
            rc = self.gate_rc if self.gate_rc is not None else (1 if self.gate == "failed" else 0)
            return rc, f"FINAL_GATE={self.gate}\n"
        if sub == "finalize":
            self.state["phase"] = "done"
            self.save()
        return 0, ""

    def comments(self) -> int:
        return self.calls.count("plan-comment")


def run(fake, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(rf, "call", fake)
    with pytest.raises(SystemExit) as e:
        rf.main(ARGV)
    captured = capsys.readouterr()
    return e.value.code, json.loads(captured.out.strip().splitlines()[-1]), captured.err


def test_done_rewrites_the_comment_once_after_finalize(tmp_path, monkeypatch, capsys):
    """AC4・AC6: 最終ゲートが通った実行は finalize の後に 1 度だけ書き直し、未公開かを結果 JSON に出す。"""
    fake = Fake(tmp_path)
    code, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert (code, out["status"]) == (0, "ok")
    assert fake.comments() == 1 and fake.calls.index("plan-comment") > fake.calls.index("finalize")
    assert out["metrics"]["unpublished"] is False and out["metrics"]["adopted"] == 2


@pytest.mark.parametrize(("gate", "gate_rc"), [("failed", None), ("failed", 4)])
def test_a_run_that_did_not_pass_the_final_gate_rewrites_the_comment_once(tmp_path, monkeypatch, capsys, gate, gate_rc):
    """AC7・AC8・I8: 打ち切り（1）でも案 B の後の中断（4）でも stopped の前に書き直し、done から移っても 2 度目を打たない。"""
    fake = Fake(tmp_path, gate=gate, gate_rc=gate_rc)
    code, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert out["status"] == "stopped"
    assert fake.comments() == 1
    assert (out["metrics"]["adopted"], out["metrics"]["unconfirmed"]) == (0, 2)


def test_a_run_stopped_by_a_launch_failure_after_the_plan_rewrites_the_comment(tmp_path, monkeypatch, capsys):
    """AC9: 計画の後に起動の失敗で止まった駆動も書き直す。"""
    fake = Fake(tmp_path, phase="implement", launch_rc=1)
    _, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert out["status"] == "stopped" and "launch-cli.sh" in out["summary"]
    assert fake.comments() == 1 and out["metrics"]["unpublished"] is False


def test_a_run_stopped_before_init_does_not_call_plan_comment(tmp_path, monkeypatch, capsys):
    fake = Fake(tmp_path)
    fake.init_rc = 4
    _, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert out["status"] == "stopped" and fake.comments() == 0


def test_a_failed_comment_does_not_change_the_result(tmp_path, monkeypatch, capsys):
    """AC14・I5: plan-comment が 1 で終わっても、終了コードと結果 JSON は成功したときと同じで、1 行だけ残す。"""
    (tmp_path / "ok").mkdir()
    ok = Fake(tmp_path / "ok")
    code_ok, out_ok, _ = run(ok, monkeypatch, capsys, tmp_path / "ok")
    rf.durable.close()

    (tmp_path / "ng").mkdir()
    ng = Fake(tmp_path / "ng")
    ng.comment = (1, "PLAN_COMMENT=failed\nUNPUBLISHED=0\n")
    code_ng, out_ng, err = run(ng, monkeypatch, capsys, tmp_path / "ng")
    assert code_ng == code_ok
    strip = lambda o: {k: v for k, v in o.items() if k != "items"}  # noqa: E731  報告のパスだけが違う
    assert strip(out_ng) == strip(out_ok)
    assert "リファクタリング計画のコメントを書き直せなかった" in err


def test_the_pause_and_the_done_each_rewrite_once(tmp_path, monkeypatch, capsys):
    """pause の前に 1 度、続きから done へ進んだ後に 1 度。再生の記録済みの呼び出しは打ち直さない。"""
    fake = Fake(tmp_path, gate="cross-review")
    code, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert out["status"] == "gate" and fake.comments() == 1
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    code, out, _ = run(fake, monkeypatch, capsys, tmp_path)
    assert out["status"] == "ok" and fake.comments() == 2
