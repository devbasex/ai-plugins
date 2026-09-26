"""#1319: `measure` が測って指標のファイルを書き、測れなくても止めずに提案へ進む。

偽の測定ツール（`metrics_fakes.py`）を PATH の先頭に置き、git の作業ディレクトリで `measure` を打つ。
ツールが無い・落ちる・読めない・遅い経路もこの偽物で起こす。実物の取得（通信）には頼らない。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import sys
import time

import pytest

from crossref_helpers import make_state_v2, read_state
from metrics_fakes import ALL_TOOLS, calls, env_for, git, install, make_repo, which_in

FILES = {
    "src/app.py": "class Box:\n    def run(self):\n        x = 1\n        return x\n\n\n"
                  "def helper(a):\n    return a\n",
    "src/b.ts": "function alpha() {\n  return 1;\n}\nfunction beta() {\n  return 2;\n}\n",
    "src/c.ts": "function gamma() {\n  return 3;\n}\n",
    "tests/test_app.py": "def test_run():\n    assert True\n",
    "other/outside.py": "def outside_fn():\n    return 1\n",
    "README.md": "# x\n",
}
UNTRACKED = {"src/untracked.py": "def untracked_fn():\n    return 1\n"}


class Run:
    def __init__(self, tmp_path, monkeypatch, codemetrics_record, files=FILES, tools=ALL_TOOLS,
                 declaration=None, enabled=True, scope=("src", "tests"), **overrides):
        self.work = tmp_path / "work"
        make_repo(self.work, files, UNTRACKED)
        if declaration is not None:
            (self.work / ".ndf").mkdir()
            (self.work / ".ndf" / "code-metrics.json").write_text(declaration)
        self.bin = install(tmp_path / "bin", tools)
        self.log = env_for(monkeypatch, tmp_path, self.bin)
        monkeypatch.setattr(shutil, "which", which_in(self.bin))
        record = codemetrics_record.code_metrics_record(self.work, enabled)
        self.path = make_state_v2(tmp_path, self.work, target_scope=list(scope),
                                  code_metrics=record, **overrides)
        self.tmp = self.path.parent
        monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(self.tmp))

    def measure(self, cmd_measure, capsys) -> dict[str, str]:
        cmd_measure.cmd_measure(argparse.Namespace(id=130))
        out = capsys.readouterr().out
        pairs = (line.split("=", 1) for line in out.splitlines() if "=" in line)
        return {k: " ".join(shlex.split(v)) for k, v in pairs}

    @property
    def state(self) -> dict:
        return read_state(self.path)

    @property
    def record(self) -> dict:
        return self.state["code_metrics"]

    def text(self) -> str:
        return pathlib.Path(self.record["file"]).read_text(encoding="utf-8")

    def by_language(self) -> dict[str, dict]:
        return {r["language"]: r for r in self.record["languages"]}

    def by_tool(self) -> dict[str, dict]:
        return {r["tool"]: r for r in self.record["duplication"]}


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return text[start:end if end != -1 else None]


@pytest.fixture
def run(tmp_path, monkeypatch, codemetrics_record):
    return lambda **kw: Run(tmp_path, monkeypatch, codemetrics_record, **kw)


def test_measure_writes_one_file_per_language_with_main_and_test(run, cmd_measure, capsys):
    """AC1 AC2 AC3 AC4 AC21 と重複検出の起動。"""
    r = run()
    before = git("status", "--porcelain", cwd=r.work)
    out = r.measure(cmd_measure, capsys)
    assert out["CODE_METRICS"] == "written" and out["CODE_METRICS_FILE"] == r.record["file"]
    text = r.text()
    # AC2: 言語ごとの節と、見出しのツールの名前と版
    assert "## python（ruff-complexipy ・ ruff 9.9.9 / complexipy 9.9.9 ・ path）" in text
    assert "## typescript（lizard ・ lizard 9.9.9 ・ path）" in text
    # AC1: 関数の CC と認知的複雑度・ファイルの大きさ・行数
    main = _section(text, "### 複雑な関数（本体・認知的複雑度の降順")
    assert "| src/app.py | Box.run | 20 | 5 |" in main and "helper" in main
    files = _section(text, "### ファイル（本体・行数の降順")
    assert "| src/app.py | 6 | 2 | 3 |" in files
    # AC4: テストはテストの表に分けて載る
    tests = _section(text, "### 複雑な関数（テスト・認知的複雑度の降順")
    assert "test_run" in tests and "helper" not in tests
    # AC3: 範囲外の追跡ファイルと、範囲内の追跡していないファイルは載らない
    assert "outside" not in text and "untracked" not in text
    # AC21: 対象リポジトリの状態が変わらない
    assert git("status", "--porcelain", cwd=r.work) == before
    # 重複検出: symilar に .py だけ、jscpd に .ts の絶対パスだけを 1 回で。どちらも言語ごとの測定の後
    log = [c for c in calls(r.log) if "--version" not in c["argv"]]
    tools = [c["tool"] for c in log]
    assert tools.index("symilar") > max(tools.index("ruff"), tools.index("lizard"))
    assert tools.index("jscpd") > tools.index("symilar")
    symilar = next(c for c in log if c["tool"] == "symilar")
    assert [a for a in symilar["argv"] if a.endswith(".py")] == ["src/app.py", "tests/test_app.py"]
    jscpd = next(c for c in log if c["tool"] == "jscpd")
    assert [a for a in jscpd["argv"] if a.endswith(".ts")] == [
        str(r.work / "src/b.ts"), str(r.work / "src/c.ts")]
    assert "--no-gitignore" in jscpd["argv"]
    assert pathlib.Path(jscpd["cwd"]) == r.tmp / "jscpd-rf130"
    assert r.by_tool()["jscpd"]["clones"] == 1
    assert "src/b.ts:1-9 ・ src/c.ts:3-11" in _section(text, "### 重複の箇所（本体")


def test_uvx_pins_every_version(run, cmd_measure, capsys):
    """AC22: uvx があれば `--from <パッケージ>==<版>` で起動し、ランナーを記録する。"""
    r = run(tools=(*ALL_TOOLS, "uvx", "npx"))
    r.measure(cmd_measure, capsys)
    froms = {c["argv"][1] for c in calls(r.log) if c["argv"][0] == "--from"}
    assert froms == {"ruff==0.16.9", "complexipy==8.0.1", "lizard==1.24.0", "pylint==4.0.9"}
    assert any(c["argv"][:2] == ["-y", "jscpd@4.3.0"] for c in calls(r.log))
    langs = r.by_language()
    assert langs["python"]["runner"] == "uvx"
    assert langs["python"]["version"] == "ruff 0.16.9 / complexipy 8.0.1"
    assert r.by_tool()["jscpd"]["runner"] == "npx"


def test_no_tools_and_no_runner_still_reaches_the_proposal(run, cmd_measure, capsys, plan, cmd_report):
    """AC9 AC19 AC20: 終了コードは 0 のまま、ファイル・計画・報告に `tool_missing` が載る。"""
    r = run(tools=())
    out = r.measure(cmd_measure, capsys)
    assert out["CODE_METRICS"] == "written"
    assert {x["reason"] for x in r.record["languages"]} == {"tool_missing"}
    assert {x["reason"] for x in r.record["duplication"]} == {"tool_missing"}
    assert "tool_missing" in r.text()
    lines = [line for line in sys.modules["refactor_lib.codemetrics_view"].record_lines(r.state)
             if line.strip()]
    body = plan.format_plan(r.state)
    cmd_report.cmd_report(argparse.Namespace(id=130, metrics=False))
    report = capsys.readouterr().out
    assert all(line in body and line in report for line in lines)
    assert any("tool_missing" in line for line in lines)


def test_jscpd_missing_leaves_the_other_metrics(run, cmd_measure, capsys):
    """AC25: npx も jscpd も無ければ jscpd の分だけ `tool_missing`、ほかは測る。"""
    r = run(tools=("ruff", "complexipy", "lizard", "symilar"))
    r.measure(cmd_measure, capsys)
    assert {x["status"] for x in r.record["languages"]} == {"measured"}
    assert r.by_tool()["symilar"]["status"] == "measured"
    assert r.by_tool()["jscpd"]["reason"] == "tool_missing"
    assert r.by_tool()["jscpd"]["detail"] == "npx / jscpd"


@pytest.mark.parametrize("files, language, status, reason", [
    ({"src/main.go": "func main() {\n}\n"}, "go", "measured", None),
    ({"src/run.sh": "echo hi\n"}, "shell", "failed", "unsupported_language"),
])
def test_languages_without_a_dedicated_tool(run, cmd_measure, capsys, files, language, status, reason):
    """AC10: `.go` だけの範囲は lizard で測る。`.sh` だけは `unsupported_language` で進む（重複は探す）。"""
    r = run(files=files, scope=("src",))
    out = r.measure(cmd_measure, capsys)
    assert out["CODE_METRICS"] == "written"
    result = r.by_language()[language]
    assert (result["status"], result["reason"]) == (status, reason)
    assert r.by_tool()["jscpd"]["languages"] == [language]


def test_no_language_in_scope(run, cmd_measure, capsys):
    r = run(files={"docs/a.md": "# a\n"}, scope=("docs",))
    out = r.measure(cmd_measure, capsys)
    assert out == {"CODE_METRICS": "no_language", "CODE_METRICS_FILE": ""}
    assert calls(r.log) == []


@pytest.mark.parametrize("env, language, reason", [
    ({"FAKE_RUFF": "fail"}, "python", "tool_failed"),
    ({"FAKE_COMPLEXIPY": "fail"}, "python", "tool_failed"),
    ({"FAKE_RUFF": "garbage"}, "python", "unreadable_output"),
    ({"FAKE_LIZARD": "garbage"}, "typescript", "unreadable_output"),
    ({"FAKE_LIZARD": "fail"}, "typescript", "tool_failed"),
])
def test_a_failing_tool_fails_only_its_language(run, cmd_measure, capsys, monkeypatch,
                                                env, language, reason):
    """AC11: その言語だけが理由つきで失敗になり、ほかの言語と重複検出は測る。片方の値だけで載せない。"""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    r = run()
    out = r.measure(cmd_measure, capsys)
    assert out["CODE_METRICS"] == "written"
    langs = r.by_language()
    assert (langs[language]["status"], langs[language]["reason"]) == ("failed", reason)
    other = "typescript" if language == "python" else "python"
    assert langs[other]["status"] == "measured"
    assert reason in _section(r.text(), "## 測れなかった言語")
    if reason == "tool_failed":
        assert "終了コード 3: boom" in langs[language]["detail"]


def test_complexipy_exit_one_with_output_counts_unreadable_files(run, cmd_measure, capsys):
    """AC11: complexipy が 1 で `--output` を書いた（構文を読めないファイルがある）なら測った扱い。"""
    r = run(files={**FILES, "src/bad.py": "def f(:\n  pass\n"})
    r.measure(cmd_measure, capsys)
    python = r.by_language()["python"]
    assert python["status"] == "measured" and python["unreadable_files"] == 1
    assert "読めなかったファイル 1 本" in r.text()
    symilar = next(c for c in calls(r.log) if c["tool"] == "symilar")
    assert "src/bad.py" not in symilar["argv"]


def test_symilar_failure_leaves_the_languages(run, cmd_measure, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_SYMILAR", "fail")
    r = run()
    r.measure(cmd_measure, capsys)
    assert r.by_tool()["symilar"]["reason"] == "tool_failed"
    assert {x["status"] for x in r.record["languages"]} == {"measured"}


def test_too_many_files_for_duplication(run, cmd_measure, capsys, monkeypatch, codemetrics):
    monkeypatch.setattr(codemetrics, "DUPLICATE_ARG_BYTES", 10)
    r = run()
    r.measure(cmd_measure, capsys)
    assert r.by_tool()["symilar"]["reason"] == "too_many_files"
    assert "symilar" not in {c["tool"] for c in calls(r.log)}


def test_broken_declaration_measures_with_defaults(run, cmd_measure, capsys, plan):
    """AC12: 壊れた宣言は既定で測り、ファイルと計画に `declaration_invalid` と理由が載る。"""
    r = run(declaration='{"version": 1, "tools": {"typescript": "ruff-complexipy"}}')
    r.measure(cmd_measure, capsys)
    assert r.by_language()["python"]["tool"] == "ruff-complexipy"
    assert "declaration_invalid" in r.text()
    assert "declaration_invalid" in plan.format_plan(r.state)


def test_declaration_replaces_one_language(run, cmd_measure, capsys):
    """AC6: 宣言で python を lizard にすると、python は lizard、typescript は既定の lizard。"""
    r = run(declaration='{"version": 1, "tools": {"python": "lizard"}}')
    r.measure(cmd_measure, capsys)
    assert {k: v["tool"] for k, v in r.by_language().items()} == {"python": "lizard",
                                                                  "typescript": "lizard"}
    assert "ruff" not in {c["tool"] for c in calls(r.log)}


def test_disabled_writes_no_file(run, cmd_measure, capsys):
    """AC7: `--no-code-metrics` ならファイルを作らず `disabled`。"""
    r = run(enabled=False)
    out = r.measure(cmd_measure, capsys)
    assert out == {"CODE_METRICS": "disabled", "CODE_METRICS_FILE": ""}
    assert calls(r.log) == [] and not list(r.tmp.glob("code-metrics-*"))


def test_second_measure_and_after_propose_do_not_launch(run, cmd_measure, capsys):
    """I1 I2: 2 回目は測らずに同じ記録を返す。提案を始めた後は測らない。"""
    r = run()
    first = r.measure(cmd_measure, capsys)
    count = len(calls(r.log))
    assert r.measure(cmd_measure, capsys) == first and len(calls(r.log)) == count

    state = r.state
    state["code_metrics"]["status"] = "pending"
    state["phases"] = {"propose": {"started_at": "2026-09-26T10:00:00+09:00"}}
    r.path.write_text(json.dumps(state))
    assert r.measure(cmd_measure, capsys)["CODE_METRICS"] == "pending"
    assert len(calls(r.log)) == count


def test_unwritable_file_still_exits_zero(run, cmd_measure, capsys, monkeypatch, tmp_path):
    """I6: 書けなければ `write_failed` で、ファイルなしで提案へ進む。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(cmd_measure, "metrics_path", lambda tmp, rid: blocker / "x.md")
    r = run()
    out = r.measure(cmd_measure, capsys)
    assert out == {"CODE_METRICS": "write_failed", "CODE_METRICS_FILE": ""}
    assert r.record["error"]


def _dead(pid: int) -> bool:
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False


def test_timeout_stops_the_process_group(run, cmd_measure, monkeypatch, tmp_path):
    """AC14 I9: 締め切りで子プロセスまで止め `timeout` にする。残りが無ければ次を起動しない。"""
    monkeypatch.setenv("FAKE_LIZARD", "slow")
    now = time.time()
    import datetime as _dt
    end = _dt.datetime.fromtimestamp(now + 4).astimezone().isoformat(timespec="seconds")
    r = run(files={"src/b.ts": FILES["src/b.ts"]}, scope=("src",),
            limits={"measure_timeout": 90, "propose_end_at": end})
    state = r.state
    started = time.monotonic()
    cmd_measure.Measurement(state, state["code_metrics"], kill_grace=0.5).run()
    elapsed = time.monotonic() - started
    record = state["code_metrics"]
    assert record["deadline_seconds"] <= 2
    assert elapsed < record["deadline_seconds"] + 0.5 + 1.5
    lizard = {x["language"]: x for x in record["languages"]}["typescript"]
    assert lizard["reason"] == "timeout"
    assert record["duplication"][0]["reason"] == "timeout"
    assert "jscpd" not in {c["tool"] for c in calls(r.log)}
    pids = [int(p) for p in (tmp_path / "fake-pids").read_text().split()]
    deadline = time.monotonic() + 3
    while not all(_dead(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert all(_dead(p) for p in pids)


# ---------------- init と再開 ----------------

def test_init_writes_measure_timeout_and_a_pending_record(codemetrics_record, tmp_path):
    """AC13: 状態の `limits` に `measure_timeout` が載る。記録は `pending` から始まる。"""
    record = codemetrics_record.code_metrics_record(tmp_path, True)
    assert record["status"] == "pending" and record["config"]["source"] == "default"
    timeline = sys.modules["refactor_lib.timeline"]
    state = {"started_at": "2026-09-26T10:00:00+09:00", "budget_minutes": 30}
    assert timeline.of_state(state)["measure_timeout"] == 90


def test_resume_of_an_old_state_measures_only_before_the_proposal(codemetrics_record, tmp_path):
    """決定 8: 記録の無い状態で再開したとき、提案を始める前だけ `pending` の記録を作る。"""
    before = {"worktrees": {"work": str(tmp_path)}, "phases": {}}
    codemetrics_record.ensure_record(before, None)
    assert before["code_metrics"]["status"] == "pending"
    assert codemetrics_record.recorded_enabled(before) is True
    after = {"worktrees": {"work": str(tmp_path)},
             "phases": {"propose": {"started_at": "2026-09-26T10:00:00+09:00"}}}
    codemetrics_record.ensure_record(after, None)
    assert "code_metrics" not in after and codemetrics_record.recorded_enabled(after) is None


def test_record_lines_without_a_record(codemetrics_view):
    assert "（記録なし）" in codemetrics_view.record_lines({})
