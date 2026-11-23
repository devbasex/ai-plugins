"""claude の起動と回答の読み方（縛った回答・本文の JSON・失敗の種類・期限切れ・子へ渡す環境）。"""

from __future__ import annotations

import json
import os
import sys

import pytest

from ndf.adapters import claude


def test_call_claude_missing_command_is_not_ok(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "質問", 30)

    assert not got.ok and got.failure is claude.Failure.LAUNCH


# --- #4013: 結果の行の読み取り ------------------------------------------------------------------

# 実測の形（2026-11-21、claude 2.1.330 の修正担当の結果の行から値だけを残した）
USAGE = {"iterations": [{"input_tokens": 2, "cache_read_input_tokens": 130418, "cache_creation_input_tokens": 695}]}
MODEL_USAGE = {
    "claude-opus-5-5": {
        "inputTokens": 10,
        "outputTokens": 20,
        "cacheReadInputTokens": 130418,
        "cacheCreationInputTokens": 695,
        "contextWindow": 1000000,
    }
}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "usage": USAGE, "modelUsage": MODEL_USAGE}


def _lines(*rows: object) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def test_issue_4013_result_line_is_the_last_result_object():
    """#4013: 1 歩ごとの行の後の結果の行と、`--output-format json` の 1 行を返す。末尾の改行・本文の U+2028 で割らない。"""
    steps = _lines({"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}}, RESULT)
    assert claude.result_line(steps) == RESULT
    assert claude.result_line(json.dumps(RESULT)) == RESULT
    assert claude.result_line(steps + "\n\n") == RESULT
    texted = {**RESULT, "result": "一行目\u2028二行目"}
    assert claude.result_line(_lines({"type": "assistant"}) + json.dumps(texted, ensure_ascii=False)) == texted


@pytest.mark.parametrize("text", [_lines({"type": "assistant"}), "途中で止まった", "", _lines([1, 2])])
def test_issue_4013_result_line_without_a_result_is_none(text):
    assert claude.result_line(text) is None


def test_issue_4013_context_tokens_add_the_last_iteration():
    """#4013: `usage.iterations` の最後の要素の入力・キャッシュの読み・キャッシュの作成の和。"""
    assert claude.context_tokens(RESULT) == 131115
    two = {
        "usage": {"iterations": [{"input_tokens": 1, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1}, *USAGE["iterations"]]}
    }
    assert claude.context_tokens(two) == 131115


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"usage": {}},
        {"usage": {"iterations": []}},
        {"usage": {"iterations": [{"input_tokens": "2", "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1}]}},
        {"usage": {"iterations": [{"input_tokens": 2, "cache_read_input_tokens": 1}]}},
    ],
)
def test_issue_4013_context_tokens_unreadable_is_none(result):
    assert claude.context_tokens(result) is None


def test_issue_4013_context_window_is_the_main_models_window():
    """#4013: 主モデル（トークンの和が最も大きい）の窓。和が同じなら窓の小さい方。"""
    assert claude.context_window(RESULT) == 1000000
    small = {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "contextWindow": 200000}
    assert claude.context_window({"modelUsage": {**MODEL_USAGE, "haiku": small}}) == 1000000
    same = {"a": {"inputTokens": 5, "contextWindow": 1000000}, "b": {"inputTokens": 5, "outputTokens": "x", "contextWindow": 200000}}
    assert claude.context_window({"modelUsage": same}) == 200000


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"modelUsage": {}},
        {"modelUsage": {"m": {"inputTokens": 1, "contextWindow": "1000000"}}},
        {"modelUsage": {"m": {"inputTokens": 1, "contextWindow": 0}}},
    ],
)
def test_issue_4013_context_window_unreadable_is_none(result):
    assert claude.context_window(result) is None


def test_issue_4013_compacted_reads_the_whole_output(tmp_path):
    """#4013: 途中の行の自動の要約は、その後に末尾の読み取りの上限を超える行が続いても見つける。"""
    from ndf.adapters import agents

    out = tmp_path / "round1-fix.out"
    filler = json.dumps({"type": "assistant", "message": "x" * (agents.TAIL_BYTES + 10)})
    out.write_text(_lines({"type": "system", "subtype": "compact_boundary"}) + filler + "\n", encoding="utf-8")
    assert claude.compacted(out) is True
    out.write_text(_lines({"type": "assistant"}, {"type": "system", "subtype": "compact_boundary"}), encoding="utf-8")
    assert claude.compacted(out) is True


def test_issue_4013_compacted_is_false_without_the_line(tmp_path):
    out = tmp_path / "round1-fix.out"
    assert claude.compacted(out) is False
    out.write_text("JSON でない\n{壊れた\n", encoding="utf-8")
    assert claude.compacted(out) is False
    out.write_text(_lines({"type": "system", "subtype": "init"}, ["compact_boundary"]), encoding="utf-8")
    assert claude.compacted(out) is False


def test_issue_4013_conversation_missing_reads_the_errors_or_stderr():
    """#4013: 結果の行の `errors` か標準エラーに、会話が見つからない文言（後ろに ID が続く）があるか。"""
    mark = f"{claude.NO_CONVERSATION}: 0f8e"
    assert claude.conversation_missing(_lines({**RESULT, "is_error": True, "errors": [mark]}), "") is True
    assert claude.conversation_missing("", f"Error: {mark}\n") is True
    assert claude.conversation_missing(_lines(RESULT), "別の誤り") is False
    assert claude.conversation_missing(_lines({**RESULT, "errors": mark}), "") is False
    assert claude.conversation_missing("", "") is False


# ---------------------------------------------------------------- 中継の確かめの差し込み（#3604 の 11。確かめの本体は relay のテスト）


RECORD = "import json, os, sys; sys.stdin.read(); open(os.environ['OUT'], 'a').write('x'); print(json.dumps({'result': '{}'}))"


class FakeChecks:
    """差し込む偽の確かめ。`begin` は `got` を返し、`retry` は `again` を返す。呼ばれた回数を数える。"""

    def __init__(self, got, again=None):
        self.got, self.again, self.calls = got, again, []

    def begin(self, argv, env, cwd, keys):
        self.calls.append(("begin", keys))
        return self.got

    def retry(self, argv, env, cwd, keys):
        self.calls.append(("retry", keys))
        return self.again


def _inherit(monkeypatch, tmp_path, name: str) -> None:
    """中継の子の環境（中継が確定した起動先 `NDF_CLAUDE_ACCOUNT` と、中継のマーカー `NDF_RELAY_DIR`）を作る。"""
    monkeypatch.setenv("NDF_CLAUDE_ACCOUNT", name)
    monkeypatch.setenv("NDF_RELAY_DIR", str(tmp_path / "relay"))


def test_a_gate_that_cannot_be_spawned_ends_without_launching(tmp_path, monkeypatch):
    ended = []
    run = claude.AccountRun(dict(os.environ), ["x"], gate=lambda argv, fd, sfd: [str(tmp_path / "missing")], end=ended.append)
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.failure is claude.Failure.LAUNCH and ended == [False]


def test_the_entry_point_injects_the_relay_checks(monkeypatch):
    import runpy

    from ndf import cli
    from ndf.domains.relay import plan_claude

    monkeypatch.setattr(claude, "relay_checks", None)
    monkeypatch.setattr(cli, "main", lambda: 0)
    codes = []
    monkeypatch.setattr(sys, "exit", codes.append)

    runpy.run_module("ndf", run_name="__main__")

    assert claude.relay_checks is plan_claude and codes == [0]


def test_a_failed_second_pipe_closes_the_first_and_ends(tmp_path, monkeypatch):
    ended, made = [], []
    real = os.pipe

    def pipe():
        if made:
            raise OSError(24, "Too many open files")
        made.extend(real())
        return made[0], made[1]

    run = claude.AccountRun(dict(os.environ), ["x"], gate=lambda argv, fd, sfd: list(argv), end=ended.append)
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")
    monkeypatch.setattr(claude.os, "pipe", pipe)

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    monkeypatch.setattr(claude.os, "pipe", real)
    assert got.failure is claude.Failure.LAUNCH and ended == [False]
    for fd in made:
        with pytest.raises(OSError):
            os.fstat(fd)
