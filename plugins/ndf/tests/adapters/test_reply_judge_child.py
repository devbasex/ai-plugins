"""判定の子（#4000 の u4）。詳細設計書の「判定の子」の「単体テストの観点」の行を縛る。

実時間の経過と実プロセスの起動をテストに書かない（偽の `call`・偽の時計・偽の `resolve`・一時ディレクトリの
設定ファイルで縛り、実の `/etc` と利用者の設定を読まない）。設定（`limits.json` の判定の行）はまだ無いので、
設定を読む口（`reply_judge.setting`）を差し替えて、詳細設計書の「内部のデータと定数」の値をここで与える。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from ndf.adapters import claude
from ndf.adapters import reply_judge as rj
from ndf.domains.relay import account_dir

# 詳細設計書の「内部のデータと定数」の `hook.stop-wait.judge`（設定の行が入るまでテストの中だけで与える）
JUDGE = {
    "model": "haiku",
    "seconds": 5,
    "max_chars": 1600,
    "rest_seconds": 600,
    "rounds": 3,
    "auth_markers": ["Not logged in", "Invalid API key"],
    "endpoint": {
        "entrypoints": ["cli", "sdk-cli"],
        "ancestors": 8,
        "cli_exe": ["*/claude/versions/*"],
        "script_hosts": ["node*"],
        "flags": ["--settings", "--setting-sources"],
        "keys": ["awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh"],
        "env_names": [
            "ANTHROPIC_",
            "CLAUDE_CODE_USE_",
            "CLAUDE_CONFIG_DIR",
            "AWS_",
            "GOOGLE_",
            "CLOUD_ML_",
            "VERTEX_",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
            "NODE_EXTRA_CA_CERTS",
        ],
    },
}


@pytest.fixture(autouse=True)
def judge_settings(monkeypatch):
    """設定を読む口を差し替える（`limits.json` の判定の行はまだ無い）。"""
    values = json.loads(json.dumps(JUDGE))
    monkeypatch.setattr(rj, "setting", lambda key: values[key])
    return values


class FakeCall:
    """偽の 1 回の起動。打たれた引数と前置きの選び方を覚え、台本の回答を順に返す。"""

    def __init__(self, *replies: claude.ClaudeReply) -> None:
        self.replies = list(replies)
        self.argv: list[list[str]] = []
        self.prompts: list[str] = []
        self.options: list[claude.LaunchOptions] = []
        self.timeouts: list[float] = []

    def __call__(self, argv, prompt, timeout, cwd=None, options=claude.DEFAULT_LAUNCH):
        del cwd
        self.argv.append(list(argv))
        self.prompts.append(prompt)
        self.options.append(options)
        self.timeouts.append(timeout)
        return self.replies[len(self.argv) - 1] if len(self.replies) > 1 else self.replies[0]


def answered(text: str) -> claude.ClaudeReply:
    """本文だけを返した 1 回の回答。"""
    return claude.ClaudeReply(True, None, text, 0.0)


def _resolved(**kwargs) -> account_dir.Resolved:
    """空の実効値（渡した欄だけ埋める）。"""
    base = {"env": {}, "origin": {}, "helper": None, "helper_origin": None, "unresolved": [], "stamps": {}}
    return account_dir.Resolved(**{**base, **kwargs})


class FakeResolve:
    """偽の実効値の解き手。渡った引数を覚える。"""

    def __init__(self, resolved: account_dir.Resolved) -> None:
        self.resolved = resolved
        self.keys = None
        self.calls = 0

    def __call__(self, env, config_dir, cwd, argv, user_settings=None, keys=()):
        del env, config_dir, cwd, argv, user_settings
        self.calls += 1
        self.keys = list(keys)
        return self.resolved


# ---------------------------------------------------------------- classify（決定 1・4・8）


@pytest.mark.parametrize("word", ["wait", "report"])
def test_classify_reads_the_one_word_answer(word):
    call = FakeCall(answered(word))
    assert rj.classify("本文", 5.0, None, call) == {"kind": word, "calls": 1}
    assert len(call.argv) == 1  # 同じ Stop で 2 回打たない（決定 4）


def test_classify_launches_the_parent_executable_without_waiting_for_a_slot():
    call = FakeCall(answered("wait"))
    rj.classify("本文", 5.0, "/opt/claude/versions/2.1.330", call)
    assert call.argv[0][0] == "/opt/claude/versions/2.1.330"
    assert call.options[0] == claude.LaunchOptions(slot_wait=0, new_group=False, cli_floor=False, ensure_training=False)


def test_classify_without_a_parent_uses_the_claude_on_the_path():
    call = FakeCall(answered("report"))
    rj.classify("本文", 5.0, None, call)
    assert call.argv[0][0] == claude.CLAUDE
    assert call.options[0] == claude.LaunchOptions(ensure_training=False, switch_account=False)


def test_classify_passes_the_minimal_arguments_and_the_model_of_the_setting(judge_settings):
    call = FakeCall(answered("report"))
    rj.classify("本文", 5.0, None, call)
    argv = call.argv[0]
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "haiku"
    judge_settings["model"] = "sonnet"
    rj.classify("本文", 5.0, None, call)
    assert call.argv[1][call.argv[1].index("--model") + 1] == "sonnet"


def test_classify_wraps_the_reply_in_the_tags():
    call = FakeCall(answered("report"))
    rj.classify("どちらにしますか。", 5.0, None, call)
    assert f"{rj.OPEN_TAG}\nどちらにしますか。\n{rj.CLOSE_TAG}" in call.prompts[0]


def test_classify_does_not_launch_when_less_than_the_least_seconds_is_left():
    call = FakeCall(answered("wait"))
    ticks = iter([100.0, 104.6])  # 経過 4.6 秒で、上限 5 秒の残りが 0.5 秒を切る
    got = rj.classify("本文", 5.0, None, call, lambda: next(ticks))
    assert got == {"failure": "timeout", "calls": 0, "error": None}
    assert call.argv == []


@pytest.mark.parametrize("failure", [claude.Failure.LAUNCH, claude.Failure.TIMEOUT, claude.Failure.NO_SLOT, claude.Failure.USAGE_LIMIT])
def test_classify_passes_the_failure_through(failure):
    call = FakeCall(claude.ClaudeReply.failed("", "理由", failure=failure))
    assert rj.classify("本文", 5.0, None, call)["failure"] == str(failure)


@pytest.mark.parametrize(
    "raw, failure",
    [
        ("Not logged in · Please run /login", "auth"),
        ("Invalid API key · Fix external API key", "auth"),
        ("claude が誤りで終えた", "exit"),
    ],
)
def test_classify_reads_the_authentication_failure_from_the_markers(raw, failure):
    call = FakeCall(claude.ClaudeReply.failed(raw, "理由"))
    assert rj.classify("本文", 5.0, None, call)["failure"] == failure


@pytest.mark.parametrize("raw", ["Wait.", "wait\n理由は人の判断が要るからである", "", "WAIT"])
def test_classify_refuses_an_answer_that_is_not_the_one_word(raw):
    call = FakeCall(answered(raw))
    assert rj.classify("本文", 5.0, None, call)["failure"] == "shape"


def test_classify_keeps_the_reason_of_a_refusal_for_the_caller():
    call = FakeCall(claude.ClaudeReply.failed("", "学習の設定の記録が期限切れ", failure=claude.Failure.REFUSED))
    got = rj.classify("本文", 5.0, None, call)
    assert got == {"failure": "refused", "calls": 1, "error": "学習の設定の記録が期限切れ"}


# ---------------------------------------------------------------- endpoint_refusal（決定 11）


@pytest.mark.parametrize(
    "argv",
    [
        ("claude", "--settings", "x.json"),
        ("claude", "--settings=x.json"),
        ("claude", "--setting-sources=user"),
        ("claude", "--setting-sources", "user"),
    ],
)
def test_endpoint_refusal_sees_the_connection_flags_of_the_parent(argv):
    resolve = FakeResolve(_resolved())
    got = rj.endpoint_refusal({}, argv, Path("/w"), resolve)
    assert got in ("--settings", "--setting-sources")
    assert resolve.calls == 0


def test_endpoint_refusal_passes_the_setting_keys_and_allows_a_clean_environment():
    resolve = FakeResolve(_resolved())
    assert rj.endpoint_refusal({}, ("claude",), Path("/w"), resolve) is None
    assert resolve.keys == JUDGE["endpoint"]["keys"]


def test_endpoint_refusal_allows_a_connection_variable_that_comes_from_the_child_environment():
    resolve = FakeResolve(_resolved(origin={"ANTHROPIC_BASE_URL": account_dir.CHILD_ENV}))
    assert rj.endpoint_refusal({}, ("claude",), Path("/w"), resolve) is None


def test_endpoint_refusal_allows_an_unrelated_variable_of_a_settings_layer():
    resolve = FakeResolve(_resolved(origin={"FOO": "利用者の設定"}))
    assert rj.endpoint_refusal({}, ("claude",), Path("/w"), resolve) is None


@pytest.mark.parametrize(
    "resolved, item",
    [
        (_resolved(unresolved=["管理の設定（managed-settings.json）"]), "管理の設定（managed-settings.json）"),
        (_resolved(helper="echo key"), "apiKeyHelper"),
        (_resolved(key_origin={"awsCredentialExport": "利用者の設定"}), "awsCredentialExport"),
        (_resolved(origin={"ANTHROPIC_BASE_URL": "利用者の設定"}), "ANTHROPIC_BASE_URL"),
        (_resolved(origin={"HTTPS_PROXY": "利用者の設定"}), "HTTPS_PROXY"),
        (_resolved(origin={"https_proxy": "利用者の設定"}), "https_proxy"),
        (_resolved(origin={"NO_PROXY": "利用者の設定"}), "NO_PROXY"),
    ],
)
def test_endpoint_refusal_names_the_item_without_its_value(resolved, item):
    assert rj.endpoint_refusal({}, ("claude",), Path("/w"), FakeResolve(resolved)) == item


# ---------------------------------------------------------------- main（子のエントリポイント）


def _stdin(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _payload(tmp_path: Path, **over) -> dict:
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    found = exe.stat()
    base = {
        "text": "本文",
        "seconds": 5,
        "parent_argv": ["claude", "-p"],
        "project_dir": str(tmp_path),
        "parent_exe": str(exe),
        "parent_exe_id": [found.st_dev, found.st_ino],
    }
    return {**base, **over}


def test_main_writes_the_answer_of_one_call(tmp_path, monkeypatch, capsys):
    _stdin(monkeypatch, _payload(tmp_path))
    monkeypatch.setattr(rj, "endpoint_refusal", lambda *a, **k: None)
    monkeypatch.setattr(rj, "classify", lambda *a, **k: {"kind": "wait", "calls": 1, "error": None})
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"kind": "wait", "calls": 1}


def test_main_does_not_launch_when_the_endpoint_cannot_be_confirmed(tmp_path, monkeypatch, capsys):
    call = FakeCall(answered("wait"))
    _stdin(monkeypatch, _payload(tmp_path))
    monkeypatch.setattr(rj, "endpoint_refusal", lambda *a, **k: "--settings")
    monkeypatch.setattr(rj, "classify", lambda *a, **k: pytest.fail("理由があるのに打った"))
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"failure": "endpoint", "calls": 0}
    assert call.argv == []


def test_main_does_not_launch_when_the_endpoint_check_itself_breaks(tmp_path, monkeypatch, capsys):
    _stdin(monkeypatch, _payload(tmp_path))
    monkeypatch.setattr(rj, "endpoint_refusal", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(rj, "classify", lambda *a, **k: pytest.fail("確かめられないのに打った"))
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"failure": "endpoint", "calls": 0}


def test_main_does_not_launch_when_the_parent_executable_was_replaced(tmp_path, monkeypatch, capsys):
    _stdin(monkeypatch, _payload(tmp_path, parent_exe_id=[1, 2]))
    monkeypatch.setattr(rj, "endpoint_refusal", lambda *a, **k: None)
    monkeypatch.setattr(rj, "classify", lambda *a, **k: pytest.fail("実体が違うのに打った"))
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"failure": "endpoint", "calls": 0}


@pytest.mark.parametrize(
    "over",
    [
        {"parent_exe": None},
        {"parent_exe": "claude"},
        {"parent_exe_id": [1]},
        {"parent_exe_id": ["a", "b"]},
        {"text": 1},
    ],
)
def test_main_writes_shape_for_a_broken_input(tmp_path, monkeypatch, capsys, over):
    payload = _payload(tmp_path, **over)
    if over.get("parent_exe") is None:
        del payload["parent_exe"]
    _stdin(monkeypatch, payload)
    monkeypatch.setattr(rj, "classify", lambda *a, **k: pytest.fail("形が違うのに打った"))
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"failure": "shape", "calls": 0}


def test_main_writes_shape_when_the_input_is_not_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert rj.main() == 0
    assert json.loads(capsys.readouterr().out) == {"failure": "shape", "calls": 0}


# ---------------------------------------------------------------- run_cases（決定 9）


def _cases_file(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"version": 1, "cases": rows}, ensure_ascii=False), encoding="utf-8")
    return path


ROWS = [
    {"id": "a", "text": "どちらにしますか。", "expect": "wait"},
    {"id": "b", "text": "終わりました。", "expect": "report"},
    {"id": "c", "text": "引用です。", "expect": "report"},
]


class ScriptedCall:
    """例の本文ごとに返す語を決めた偽の起動。"""

    def __init__(self, words: dict[str, list[str]]) -> None:
        self.words = {k: list(v) for k, v in words.items()}
        self.argv: list[list[str]] = []
        self.options: list[claude.LaunchOptions] = []

    def __call__(self, argv, prompt, timeout, cwd=None, options=claude.DEFAULT_LAUNCH):
        del cwd, timeout
        self.argv.append(list(argv))
        self.options.append(options)
        row = next(r for r in ROWS if r["text"] in prompt)
        queue = self.words[row["id"]]
        return answered(queue.pop(0) if len(queue) > 1 else queue[0])


def test_run_cases_counts_cases_times_rounds(tmp_path):
    call = ScriptedCall({"a": ["wait"], "b": ["report"], "c": ["report"]})
    got = rj.run_cases(_cases_file(tmp_path, ROWS), 3, call)
    assert got["items"] == []
    assert got["metrics"]["cases"] == 3 and got["metrics"]["calls"] == 9 and got["metrics"]["missed"] == 0
    assert call.argv[0][0] == claude.CLAUDE
    assert call.options[0] == claude.LaunchOptions(ensure_training=False, switch_account=False)


def test_run_cases_one_round(tmp_path):
    call = ScriptedCall({"a": ["wait"], "b": ["report"], "c": ["report"]})
    got = rj.run_cases(_cases_file(tmp_path, ROWS), 1, call)
    assert got["metrics"]["calls"] == 3 and got["items"] == []


def test_run_cases_lists_a_case_that_misses_in_one_round(tmp_path):
    call = ScriptedCall({"a": ["wait", "report", "wait"], "b": ["report"], "c": ["report"]})
    got = rj.run_cases(_cases_file(tmp_path, ROWS), 3, call)
    assert got["items"] == [{"id": "a", "expect": "wait", "got": ["report"]}]
    assert got["metrics"]["missed"] == 1


def test_run_cases_stops_at_a_refusal_and_keeps_the_reason(tmp_path):
    reply = claude.ClaudeReply.failed("", "学習の設定の記録が期限切れ", failure=claude.Failure.REFUSED)
    seen: list[str] = []

    def call(argv, prompt, timeout, cwd=None, options=claude.DEFAULT_LAUNCH):
        del argv, timeout, cwd, options
        row = next(r for r in ROWS if r["text"] in prompt)
        seen.append(row["id"])
        return reply if row["id"] == "b" else answered(row["expect"])

    with pytest.raises(rj.JudgeRefused) as got:
        rj.run_cases(_cases_file(tmp_path, ROWS), 3, call)
    assert got.value.reason == "学習の設定の記録が期限切れ"
    assert "c" not in seen


@pytest.mark.parametrize("text", ["{broken", json.dumps({"cases": []}), json.dumps({"cases": [{"id": "a"}]})])
def test_run_cases_refuses_a_set_it_cannot_read(tmp_path, text):
    path = tmp_path / "cases.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        rj.run_cases(path, 1, FakeCall(answered("wait")))


def test_run_cases_refuses_a_missing_set(tmp_path):
    with pytest.raises(ValueError):
        rj.run_cases(tmp_path / "none.json", 1, FakeCall(answered("wait")))


# ---------------------------------------------------------------- 判定の例の組（受け入れ条件 2）


def test_the_shipped_cases_cover_the_acceptance_condition():
    rows = json.loads(rj.cases_path().read_text(encoding="utf-8"))["cases"]
    assert len(rows) >= 9
    assert sum(1 for r in rows if r["expect"] == "wait") >= 5
    assert sum(1 for r in rows if r["expect"] == "report") >= 3
    assert len({r["id"] for r in rows}) == len(rows)
    assert all(isinstance(r["text"], str) and r["text"] for r in rows)
