"""work のステップの成否を作業の結果ファイルだけで決める（#2337 の受け入れ条件 9・I10）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker_fake  # noqa: E402
from supervise_lib import worker_steps  # noqa: E402
from supervise_lib.state import RunState  # noqa: E402


class FakeClaude:
    """渡した報告を返し、`write` が真のときだけ報告の語に合う作業の結果ファイルを書く。"""

    def __init__(self, texts, write=True):
        self.texts, self.write, self.calls, self.rfile = list(texts), write, [], None

    def call(self, system, prompt, tools, cwd, timeout, **kw):
        self.calls.append(prompt)
        text = self.texts.pop(0) if self.texts else "## 作業の報告\n- 結果: 完了"
        if self.write:
            self.rfile = worker_fake.write_result(prompt, text, self.rfile)
        return {"ok": True, "text": text, "seconds": 0, "session": "s1", "usage": {}}

    def record_usage(self, kind, res):
        pass


def ctx_of(tmp_path: Path, claude) -> SimpleNamespace:
    plan = {"作業場所": str(tmp_path), "課題": []}
    state = RunState(tmp_path / "sv", plan)
    state.cur = {"id": "w"}
    return SimpleNamespace(plan=plan, cwd=str(tmp_path), state=state, claude=claude, inputs_text=lambda s: "", tick=lambda: None)


@pytest.mark.parametrize(
    "text,ok",
    [("## 作業の報告\n- 結果: 完了", True), ("## 作業の報告\n- 結果: 判断が要る", False), ("## 作業の報告\n- 結果: できなかった", False)],
)
def test_only_an_ok_result_file_makes_the_step_succeed(tmp_path, text, ok):
    ctx = ctx_of(tmp_path, FakeClaude([text]))
    got, _ = worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert got is ok and ctx.state.cur["exit"] == (0 if ok else 1)
    assert "作業の結果ファイル: " in ctx.claude.calls[0]


def test_the_report_words_without_a_result_file_fail(tmp_path):
    ctx = ctx_of(tmp_path, FakeClaude(["## 作業の報告\n- 結果: 完了"], write=False))
    got, text = worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert not got and "結果ファイルが無い" in text


def test_a_stale_result_file_from_a_previous_try_is_removed_before_the_launch(tmp_path):
    ctx = ctx_of(tmp_path, FakeClaude(["途中"], write=False))
    stale = worker_steps.result_file_of(ctx, {"id": "w"})
    stale.parent.mkdir(parents=True, exist_ok=True)
    worker_fake.ok_result(stale)
    got, _ = worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す"})
    assert not got and not stale.exists()


def test_a_full_step_without_a_result_file_is_resumed_up_to_three_times(tmp_path):
    ctx = ctx_of(tmp_path, FakeClaude(["途中", "途中", "途中", "## 作業の報告\n- 結果: 完了"]))
    got, _ = worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "/ndf:design #1", "full": True})
    assert got and len(ctx.claude.calls) == 4


def test_a_full_step_that_wrote_gate_is_not_resumed(tmp_path):
    ctx = ctx_of(tmp_path, FakeClaude(["## 作業の報告\n- 結果: 判断が要る"]))
    got, _ = worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "/ndf:design #1", "full": True})
    assert not got and len(ctx.claude.calls) == 1 and ctx.state.cur["work_result"]["status"] == "gate"


def test_the_result_file_is_an_absolute_path_even_for_a_relative_state_dir(tmp_path, monkeypatch):
    """相対の状態ディレクトリでも、worker へ渡す結果ファイルは起動ディレクトリで解決した絶対パスにする。"""
    monkeypatch.chdir(tmp_path)
    ctx = SimpleNamespace(state=SimpleNamespace(dir=Path("sv")))
    assert worker_steps.result_file_of(ctx, {"id": "w"}) == tmp_path.resolve() / "sv" / "w-result.json"
