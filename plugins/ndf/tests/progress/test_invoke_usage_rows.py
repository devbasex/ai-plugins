"""#1959 の決定 6: Skill を回す起動（`invoke._call_skill`）が使用量ログへ渡す行。`claude -p` は起動せず、偽の呼び出しを渡す。"""

from __future__ import annotations

import pytest

from ndf.adapters import claude
from ndf.contracts import result
from ndf.domains.records import usage
from ndf.progress import invoke


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def _reply(**kw) -> claude.ClaudeReply:
    return claude.ClaudeReply(**{"ok": True, "answer": None, "raw": "", "cost": 0.0, **kw})


class Rows:
    """差し込む偽の使用量ログの書き込み処理。"""

    def __init__(self) -> None:
        self.rows: list[tuple[object, dict]] = []

    def record_cli(self, usage, meta):
        self.rows.append((usage, dict(meta)))


@pytest.fixture(autouse=True)
def _outside_a_plan(monkeypatch):
    for name in ("NDF_PLAN_RUN_ID", "NDF_STEP_ID", "NDF_STEP_ATTEMPT"):
        monkeypatch.delenv(name, raising=False)


def _call(reply: claude.ClaudeReply):
    return lambda argv, prompt, timeout, cwd=None, options=None: reply


def test_skill_writes_one_row_named_after_the_skill_with_the_session_of_the_result(tmp_path, monkeypatch):
    """条件 3: 工程の名前は Skill の名前、会話の id は結果の値、番号は 0。プランの外では run_id を書かない。"""
    rows = Rows()
    monkeypatch.setattr(claude, "launch_recorder", rows)
    answer = result.result("skill", "ok", "済んだ", outputs={"pr": 3})
    reply = _reply(answer=answer, launched=True, session_id="s-7", usage={"output_tokens": 5}, session_cost_usd=0.3)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_call(reply))
    assert got.status == "ok" and len(rows.rows) == 1
    used, row = rows.rows[0]
    assert used == {"output_tokens": 5} and row["name"] == "design" and row["session_id"] == "s-7" and row["session_seq"] == 0
    assert "run_id" not in row and row["session_cost_usd"] == 0.3


def test_skill_result_is_the_same_when_the_usage_log_cannot_be_written(tmp_path, monkeypatch, capsys):
    """I4: 使用量ログへ書けなくても工程の結果は変わらず、警告が 1 行出る。"""
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("NDF_USAGE_DIR", str(blocker / "usage"))
    call = _call(_reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3}), launched=True))
    monkeypatch.setattr(claude, "launch_recorder", None)
    quiet = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=call)
    capsys.readouterr()
    monkeypatch.setattr(claude, "launch_recorder", usage)
    loud = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=call)
    assert (loud.status, loud.outputs, loud.warnings) == (quiet.status, quiet.outputs, quiet.warnings)
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_a_stopped_skill_launch_writes_no_row(tmp_path, monkeypatch):
    rows = Rows()
    monkeypatch.setattr(claude, "launch_recorder", rows)
    reply = _reply(ok=False, error="空かない", failure=claude.Failure.NO_SLOT)
    assert invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_call(reply)).status == "stopped" and rows.rows == []
