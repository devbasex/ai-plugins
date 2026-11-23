"""worker の受け渡し: 結果ファイルが無いまま終えた worker を同じ会話で再起動する。"""

from __future__ import annotations

import json
import os

import pytest

from ndf import identity
from ndf.adapters import claude
from ndf.contracts import causes, result, worker_paths
from ndf.core import worker


@pytest.fixture(autouse=True)
def _no_plugin_list(monkeypatch):
    """導入済みの一覧は空とする（`root` が NDF でないと `delegate` が `claude plugin list --json` を子のプロセスで起動するため）。"""
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])


class FakeClaude:
    """呼ばれた引数をバックアップ、`write_on` 回目の呼び出しで結果ファイルを書く。"""

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


def _delegate(h, **kw):
    """結果の保存先を開いて `delegate` を呼ぶ（`ndf worker run` と同じく、保存先は 1 回だけ開く）。"""
    with worker_paths.open_workdir(h.result_file.parent) as wd:
        return worker.delegate(h, "規則", root=h.cwd, wd=wd, **kw)


def _handoff(tmp_path):
    return worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")


def test_written_on_the_second_call_returns_after_one_resume(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, write_on=2)
    done = _delegate(h, call=fake, session="s-1")
    assert done.cause is None and done.attempts == 2 and done.cost == 1.0
    first, second = fake.calls
    assert first[0][-2:] == ["--session-id", "s-1"] and first[1].startswith("直す\n")
    assert str(h.result_file) in first[1]
    assert second[0][-2:] == ["--resume", "s-1"] and second[1] == worker.RESUME_PROMPT


def test_never_written_stops_as_work_stopped_after_the_limit(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file)
    done = _delegate(h, call=fake)
    assert done.cause == "work_stopped"
    assert len(fake.calls) == worker.RESUMES + 1


def test_the_launch_passes_the_work_kind_only_while_it_runs(tmp_path, monkeypatch):
    monkeypatch.delenv(identity.WORKER_KIND_ENV, raising=False)
    h = _handoff(tmp_path)
    seen = []

    def fake(argv, prompt, timeout, cwd):
        seen.append(os.environ.get(identity.WORKER_KIND_ENV))
        h.result_file.write_text("{}", encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    assert _delegate(h, call=fake).cause is None
    assert seen == ["fix"] and identity.WORKER_KIND_ENV not in os.environ


def test_written_on_the_last_resume_is_taken(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, write_on=worker.RESUMES + 1)
    assert _delegate(h, call=fake).cause is None


def test_a_stale_result_file_is_removed_before_launch(tmp_path):
    h = _handoff(tmp_path)
    h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
    fake = FakeClaude(h.result_file)
    assert _delegate(h, call=fake).cause == "work_stopped"


def test_a_failure_that_resuming_cannot_fix_is_not_resumed(tmp_path):
    h = _handoff(tmp_path)
    fake = FakeClaude(h.result_file, failure=claude.Failure.USAGE_LIMIT)
    done = _delegate(h, call=fake)
    assert done.cause == "usage_limit" and len(fake.calls) == 1


def test_run_resolves_relative_paths_from_the_launcher(tmp_path, monkeypatch, capsys):
    (tmp_path / "prompt.md").write_text("直す", encoding="utf-8")
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_delegate(handoff, system, root=None, wd=None):
        seen["handoff"] = handoff
        handoff.result_file.write_text('{"status": "ok", "summary": "済んだ"}', encoding="utf-8")
        return worker.Delivery(attempts=1, session="s")

    monkeypatch.setattr(worker, "delegate", fake_delegate)
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", "out.json"]
    assert worker.main(args + ["--cwd", "work", "--state-dir", "state"]) == 0
    h = seen["handoff"]
    assert (h.result_file, h.cwd, h.state_dir) == (tmp_path / "out.json", tmp_path / "work", tmp_path / "state")
    assert '"result_file": "' + str(tmp_path / "out.json") in capsys.readouterr().out


def _outcome(tmp_path, report):
    path = tmp_path / "result.json"
    if report is not None:
        path.write_text(report if isinstance(report, str) else json.dumps(report, ensure_ascii=False), encoding="utf-8")
    with worker_paths.open_workdir(tmp_path) as wd:
        got = worker.outcome_of("impl", path, worker._read_result(wd, path.name), [], {}, 1)
    assert result.errors(got) == []
    return got


def test_a_worker_that_left_work_does_not_return_ok(tmp_path):
    # #1682: 手動確認を行えなかった worker の「できなかった」と、欠けの付いた ok は、欠けを付けた incomplete になる
    gap = {"kind": "criterion", "name": "3", "reason": "arm64 で建てられず手動確認（マージ前）を行えない"}
    got = _outcome(tmp_path, {"status": "stopped", "summary": "base を建てられない", "gaps": [gap]})
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete" and got["gaps"] == [gap]
    got = _outcome(tmp_path, {"status": "ok", "summary": "完了", "gaps": [gap]})
    assert got["status"] == "stopped" and got["gaps"] == [gap]
    got = _outcome(tmp_path, {"status": "stopped", "summary": "できなかった"})
    assert got["gaps"][0]["reason"] == "できなかった"


def test_a_malformed_gap_keeps_an_ok_report_from_passing(tmp_path):
    # 形の違う gaps（文字列の配列・文字列・欠けたキー）を捨てると、作業を残した ok が完了に化ける（#1682）
    for gaps in (["3 を確かめられない"], "3 を確かめられない", [{"kind": "criterion", "name": "3"}]):
        got = _outcome(tmp_path, {"status": "ok", "summary": "完了", "gaps": gaps})
        assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
        assert len(got["gaps"]) == 1 and "3" in got["gaps"][0]["reason"]
    assert _outcome(tmp_path, {"status": "ok", "summary": "済んだ", "gaps": []})["status"] == "ok"


def test_a_worker_that_asks_for_a_decision_returns_a_gate(tmp_path):
    got = _outcome(tmp_path, {"status": "gate", "summary": "判断が要る"})
    assert got["status"] == "gate" and "判断が要る" in got["summary"] and got["routes"]


def test_a_worker_that_finished_returns_ok_and_an_unreadable_report_is_bad_report(tmp_path):
    assert _outcome(tmp_path, {"status": "ok", "summary": "済んだ"})["status"] == "ok"
    for i, bad in enumerate(("{}", "壊れた", None)):
        location = tmp_path / f"b{i}"
        location.mkdir()
        got = _outcome(location, bad)
        assert got["status"] == "stopped" and got["cause"]["kind"] == "bad_report"


SEARCHED = {"terms": ["詳細設計"], "files": ["tests/test_stage_check.py"]}


def _ci_fix(tmp_path):
    return worker.Handoff("ci-fix", "CI を直す", tmp_path / "result.json", tmp_path, tmp_path / "state")


def _replies(h, *bodies):
    """呼び出しごとに `bodies` の順で結果ファイルを書く worker（None は書かない）。"""
    calls = []

    def fake(argv, prompt, timeout, cwd):
        calls.append(prompt)
        body = bodies[len(calls) - 1]
        if body is not None:
            h.result_file.write_text(json.dumps(body), encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    return fake, calls


def test_ci_fix_without_the_search_record_is_resumed_until_it_is_written(tmp_path):
    # #3188: 期待値の直書きを探した記録が無い結果ファイルは、書いたとみなさず再起動する
    h = _ci_fix(tmp_path)
    fake, calls = _replies(h, {"status": "ok"}, {"status": "ok", "metrics": {worker.SEARCH_RECORD: SEARCHED}})
    assert _delegate(h, call=fake).cause is None
    assert calls[1] == worker.RECORD_PROMPT
    assert worker.search_record(json.loads(h.result_file.read_text(encoding="utf-8"))) == SEARCHED


def test_ci_fix_that_never_records_the_search_stops(tmp_path):
    h = _ci_fix(tmp_path)
    fake, _ = _replies(h, *[{"status": "ok"}] * (worker.RESUMES + 1))
    done = _delegate(h, call=fake)
    assert done.cause == "work_stopped" and worker.SEARCH_RECORD in done.detail


def test_ci_fix_that_asks_stops_or_changed_no_definition_is_not_resumed(tmp_path):
    # 判断が要る・できなかった結果と、語を空で書いた記録（変えた定義が無い直し）は再起動しない
    no_terms = {"status": "ok", "metrics": {worker.SEARCH_RECORD: {"terms": [], "files": []}}}
    for i, body in enumerate(({"status": "gate", "summary": "判断が要る"}, {"status": "stopped", "summary": "直せない"}, no_terms)):
        location = tmp_path / f"w{i}"
        location.mkdir()
        h = _ci_fix(location)
        fake, calls = _replies(h, body)
        assert _delegate(h, call=fake).cause is None and len(calls) == 1


def test_other_kinds_do_not_need_the_search_record(tmp_path):
    h = _handoff(tmp_path)
    fake, calls = _replies(h, {"status": "ok"})
    assert _delegate(h, call=fake).cause is None and len(calls) == 1


def test_ci_fix_run_carries_the_search_record_into_its_result(tmp_path, monkeypatch, capsys):
    (tmp_path / "prompt.md").write_text("CI を直す", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_delegate(handoff, system, root=None, wd=None):
        seen["system"] = system
        body = {"status": "ok", "metrics": {worker.SEARCH_RECORD: SEARCHED}}
        handoff.result_file.write_text(json.dumps(body), encoding="utf-8")
        return worker.Delivery(attempts=1, session="s")

    monkeypatch.setattr(worker, "delegate", fake_delegate)
    args = ["run", "--kind", "ci-fix", "--prompt-file", "prompt.md", "--result-file", "out.json"]
    assert worker.main(args + ["--cwd", ".", "--state-dir", "state"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"][worker.SEARCH_RECORD] == SEARCHED
    assert "\n".join(worker.KIND_RULES["ci-fix"]) in seen["system"]


def _unreadable_list(monkeypatch):
    """導入済みの一覧を読めなかったときの `plugin_args`（外さずに起動し、知らせを返す）。"""
    monkeypatch.setattr(
        claude, "plugin_args", lambda root, cwd=None: claude.PluginArgs(["--plugin-dir", str(root)], claude.LIST_UNREADABLE)
    )


def test_delegate_keeps_the_notice_of_an_unreadable_plugin_list(tmp_path, monkeypatch):
    _unreadable_list(monkeypatch)
    h = _handoff(tmp_path)
    done = _delegate(h, call=FakeClaude(h.result_file, write_on=1))
    assert done.cause is None and done.notice == claude.LIST_UNREADABLE


def test_run_puts_the_notice_into_the_warnings_of_its_result(tmp_path, monkeypatch, capsys):
    (tmp_path / "prompt.md").write_text("直す", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_delegate(handoff, system, root=None, wd=None):
        handoff.result_file.write_text('{"status": "ok", "summary": "済んだ"}', encoding="utf-8")
        return worker.Delivery(attempts=1, session="s", notice=claude.LIST_UNREADABLE)

    monkeypatch.setattr(worker, "delegate", fake_delegate)
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", "out.json"]
    assert worker.main(args + ["--cwd", ".", "--state-dir", "state"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["warnings"] == [claude.LIST_UNREADABLE]


# --- #3877: 結果ファイルをリンクをたどらずに扱う --------------------------------------------------------------


def _run_with(work, monkeypatch, call, kind="impl"):
    """`work` で `ndf worker run` を打つ。worker の呼び出しは `call` に差し替える（子のプロセスを起動しない）。"""
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    monkeypatch.chdir(work)
    real = worker.delegate
    monkeypatch.setattr(worker, "delegate", lambda h, s, **kw: real(h, s, call=call, **kw))
    args = ["run", "--kind", kind, "--prompt-file", "prompt.md", "--result-file", "out.json"]
    return worker.main(args + ["--cwd", ".", "--state-dir", "state"])


def _ok_writer(work, body, calls):
    def fake(argv, prompt, timeout, cwd):
        calls.append(prompt)
        (work / "out.json").write_text(json.dumps(body), encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    return fake


def _outside(tmp_path):
    """作業ディレクトリ `w` と、その外の JSON（要約と探した記録を持つ）。"""
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"status": "ok", "summary": "外の要約", "metrics": {worker.SEARCH_RECORD: SEARCHED}}), encoding="utf-8")
    work = tmp_path / "w"
    work.mkdir()
    return work, outside


def test_run_refuses_a_result_file_that_links_outside_without_launching(tmp_path, monkeypatch, capsys):
    work, outside = _outside(tmp_path)
    before = outside.read_text(encoding="utf-8")
    (work / "out.json").symlink_to(outside)
    calls = []
    code = _run_with(work, monkeypatch, _ok_writer(work, {"status": "ok"}, calls))
    got = json.loads(capsys.readouterr().out)
    assert (got["status"], got["cause"]["kind"], code) == ("stopped", "invalid", causes.exit_code("invalid"))
    assert calls == [] and outside.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("result_file", ["link/out.json", "no-such/out.json"])
def test_run_refuses_a_result_dir_that_dangles_or_is_missing_without_launching(tmp_path, monkeypatch, capsys, result_file):
    work, _ = _outside(tmp_path)
    # 祖先のリンクは解くので、拒むのは参照先の無いリンク（#3877 r3）
    (work / "link").symlink_to(work / "real")
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    monkeypatch.chdir(work)
    calls = []
    real = worker.delegate
    monkeypatch.setattr(worker, "delegate", lambda h, s, **kw: real(h, s, call=_ok_writer(work, {}, calls), **kw))
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", result_file]
    worker.main(args + ["--cwd", ".", "--state-dir", "state"])
    got = json.loads(capsys.readouterr().out)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid") and calls == []


def test_run_stops_as_bad_report_when_the_result_turns_into_a_link_during_the_call(tmp_path, monkeypatch, capsys):
    work, outside = _outside(tmp_path)
    calls = []

    def fake(argv, prompt, timeout, cwd):
        calls.append(prompt)
        (work / "out.json").symlink_to(outside)
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    _run_with(work, monkeypatch, fake, kind="ci-fix")
    out = capsys.readouterr().out
    got = json.loads(out)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "bad_report") and len(calls) == 1
    assert "外の要約" not in out and worker.SEARCH_RECORD not in got["metrics"] and SEARCHED["files"][0] not in out


@pytest.mark.parametrize("kind", ["impl", "ci-fix"])
def test_run_stops_as_bad_report_on_a_result_that_is_not_utf8(tmp_path, monkeypatch, capsys, kind):
    def fake(argv, prompt, timeout, cwd):
        (tmp_path / "out.json").write_bytes(b'{"status": "ok", "summary": "\xff"}')
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    _run_with(tmp_path, monkeypatch, fake, kind=kind)
    got = json.loads(capsys.readouterr().out)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "bad_report")


@pytest.mark.parametrize(("kind", "reads"), [("impl", 1), ("ci-fix", 2)])
def test_run_reads_the_result_once_during_and_once_after_the_delegation(tmp_path, monkeypatch, capsys, kind, reads):
    counted = []
    real_read = worker_paths.read_file
    monkeypatch.setattr(worker_paths, "read_file", lambda wd, name: counted.append(name) or real_read(wd, name))
    body = {"status": "ok", "summary": "済んだ", "metrics": {worker.SEARCH_RECORD: SEARCHED}}
    assert _run_with(tmp_path, monkeypatch, _ok_writer(tmp_path, body, []), kind=kind) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok" and counted == ["out.json"] * reads


def test_run_refuses_a_result_path_whose_dotdot_cancels_a_link_without_launching(tmp_path, monkeypatch, capsys):
    # #3877 r1: `alias/..` を字面で畳んだ別の場所の結果ファイルを消さず、worker も起動しない
    work, outside = _outside(tmp_path)
    (tmp_path / "far" / "inner").mkdir(parents=True)
    (work / "alias").symlink_to(tmp_path / "far" / "inner")
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    (work / "out.json").write_text("前の結果", encoding="utf-8")
    monkeypatch.chdir(work)
    calls = []
    real = worker.delegate
    monkeypatch.setattr(worker, "delegate", lambda h, s, **kw: real(h, s, call=_ok_writer(work, {}, calls), **kw))
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", "alias/../out.json"]
    worker.main(args + ["--cwd", ".", "--state-dir", "state"])
    got = json.loads(capsys.readouterr().out)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid") and calls == []
    assert (work / "out.json").read_text(encoding="utf-8") == "前の結果"


def test_the_launch_passes_the_opened_result_dir_only_while_it_runs(tmp_path, monkeypatch):
    # #3877 r1: worker の `contracts emit --out` が開き直した保存先を突き合わせられるよう、開いた保存先の識別子を渡す
    monkeypatch.delenv(identity.WORKER_RESULT_DIR_ENV, raising=False)
    h = _handoff(tmp_path)
    seen = []

    def fake(argv, prompt, timeout, cwd):
        seen.append(os.environ.get(identity.WORKER_RESULT_DIR_ENV))
        h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.5)

    _delegate(h, call=fake)
    st = tmp_path.stat()
    assert seen == [f"{st.st_dev}:{st.st_ino}:{tmp_path}"] and identity.WORKER_RESULT_DIR_ENV not in os.environ


def test_run_accepts_a_result_file_under_a_linked_ancestor(tmp_path, monkeypatch, capsys):
    # #3877 r3: macOS の /var→/private/var のように祖先にリンクがある作業ディレクトリでも、解いたパスで起動する
    real_dir = tmp_path / "real"
    work = real_dir / "w"
    work.mkdir(parents=True)
    (tmp_path / "link").symlink_to(real_dir)
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    monkeypatch.chdir(work)
    calls = []
    real = worker.delegate
    writer = _ok_writer(work, {"status": "ok", "summary": "済んだ"}, calls)
    monkeypatch.setattr(worker, "delegate", lambda h, s, **kw: real(h, s, call=writer, **kw))
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", str(tmp_path / "link" / "w" / "out.json")]
    assert worker.main(args + ["--cwd", ".", "--state-dir", "state"]) == 0
    got = json.loads(capsys.readouterr().out)
    resolved = str(work.resolve() / "out.json")
    assert got["status"] == "ok" and got["items"][0]["result_file"] == resolved and resolved in calls[0]


def test_run_refuses_a_result_file_written_as_a_directory_without_removing_the_file(tmp_path, monkeypatch, capsys):
    # #3877 r4: `Path` は末尾の `/` を落とす。生の引数でディレクトリを指す形なら、同名の既存のファイルを消さない
    work, _ = _outside(tmp_path)
    (work / "saved.json").write_text("利用者のファイル", encoding="utf-8")
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    monkeypatch.chdir(work)
    calls = []
    real = worker.delegate
    monkeypatch.setattr(worker, "delegate", lambda h, s, **kw: real(h, s, call=_ok_writer(work, {}, calls), **kw))
    args = ["run", "--kind", "impl", "--prompt-file", "prompt.md", "--result-file", "saved.json/"]
    worker.main(args + ["--cwd", ".", "--state-dir", "state"])
    got = json.loads(capsys.readouterr().out)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid") and calls == []
    assert (work / "saved.json").read_text(encoding="utf-8") == "利用者のファイル"


def test_run_keeps_using_the_resolved_result_dir_when_it_is_swapped_before_launch(tmp_path, monkeypatch, capsys):
    # #3877 r4: 解いた後に保存先を別の実ディレクトリへ差し替えられても、起動前の削除は差し替え先の同名のファイルに当たらない
    work, _ = _outside(tmp_path)
    (work / "prompt.md").write_text("直す", encoding="utf-8")
    (work / "out.json").write_text("前の結果", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    real = worker.delegate

    def swap_then_delegate(h, s, **kw):
        work.rename(tmp_path / "moved")
        work.mkdir()
        (work / "out.json").write_text("差し替え先のファイル", encoding="utf-8")
        return real(h, s, call=lambda *args: claude.ClaudeReply(True, None, "終えた", 0.5), **kw)

    monkeypatch.setattr(worker, "delegate", swap_then_delegate)
    args = ["run", "--kind", "impl", "--prompt-file", "w/prompt.md", "--result-file", "w/out.json"]
    worker.main(args + ["--cwd", ".", "--state-dir", "state"])
    assert (work / "out.json").read_text(encoding="utf-8") == "差し替え先のファイル"
    assert not (tmp_path / "moved" / "out.json").exists()
