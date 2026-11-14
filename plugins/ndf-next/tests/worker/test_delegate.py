"""worker の受け渡し: 結果ファイルが無いまま終えた worker を同じ会話で起こし直す。"""

from __future__ import annotations

from ndf.adapters import claude
from ndf.core import worker


class FakeClaude:
    """呼ばれた引数を控え、`write_on` 回目の呼び出しで結果ファイルを書く。"""

    def __init__(self, result_file, write_on=None, failure=None):
        self.calls = []
        self.result_file = result_file
        self.write_on = write_on
        self.failure = failure

    def __call__(self, argv, prompt, timeout, cwd):
        self.calls.append((argv, prompt))
        if self.failure is not None:
            return claude.ClaudeReply.failed("", "上限", 0.1, self.failure)
        if len(self.calls) == self.write_on:
            self.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.5)


def _handoff(tmp_path):
    return worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")


def test_written_on_the_second_call_returns_after_one_resume(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, write_on=2)
    done = worker.delegate(h, "規則", root=tmp_path, call=fake, session="s-1")
    assert done.cause is None and done.attempts == 2 and done.cost == 1.0
    first, second = fake.calls
    assert first[0][-2:] == ["--session-id", "s-1"] and first[1].startswith("直す\n")
    assert str(h.result_file) in first[1]
    assert second[0][-2:] == ["--resume", "s-1"] and second[1] == worker.RESUME_PROMPT


def test_never_written_stops_as_work_stopped_after_the_limit(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file)
    done = worker.delegate(h, "規則", root=tmp_path, call=fake)
    assert done.cause == "work_stopped"
    assert len(fake.calls) == worker.RESUMES + 1


def test_written_on_the_last_resume_is_taken(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, write_on=worker.RESUMES + 1)
    assert worker.delegate(h, "規則", root=tmp_path, call=fake).cause is None


def test_a_stale_result_file_is_removed_before_launch(tmp_path):
    h = _handoff(tmp_path)
    h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
    fake = FakeClaude(h.result_file)
    assert worker.delegate(h, "規則", root=tmp_path, call=fake).cause == "work_stopped"


def test_a_failure_that_resuming_cannot_fix_is_not_resumed(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, failure=claude.Failure.USAGE_LIMIT)
    done = worker.delegate(h, "規則", root=tmp_path, call=fake)
    assert done.cause == "usage_limit" and len(fake.calls) == 1


def test_run_resolves_relative_paths_from_the_launcher(tmp_path, monkeypatch, capsys):
    (tmp_path / "prompt.md").write_text("直す", encoding="utf-8")
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_delegate(handoff, system, root=None):
        seen["handoff"] = handoff
        handoff.result_file.write_text("{}", encoding="utf-8")
        return worker.Delivery(attempts=1, session="s")

    monkeypatch.setattr(worker, "delegate", fake_delegate)
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", "out.json"]
    assert worker.main(args + ["--cwd", "work", "--state-dir", "state"]) == 0
    h = seen["handoff"]
    assert (h.result_file, h.cwd, h.state_dir) == (tmp_path / "out.json", tmp_path / "work", tmp_path / "state")
    assert '"result_file": "' + str(tmp_path / "out.json") in capsys.readouterr().out
