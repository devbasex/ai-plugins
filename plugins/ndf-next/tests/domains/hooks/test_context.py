"""入力の解析は 1 回の事象で 1 度だけ（M3-H1・IH1）と、解析の関数そのもの。"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from ndf.domains.hooks import context

from ._hooks import bash, call_entry, stop

_PARSERS = ("parse_payload", "parse_shell", "read_tail", "classify_reply")


@pytest.fixture
def calls(monkeypatch):
    counted: Counter = Counter()
    for name in _PARSERS:
        real = getattr(context, name)

        def wrapped(*args, _real=real, _name=name, **kw):
            counted[_name] += 1
            return _real(*args, **kw)

        monkeypatch.setattr(context, name, wrapped)
    return counted


def _transcript(tmp_path, tokens: int = 1000) -> None:
    line = {"type": "assistant", "message": {"content": [{"type": "text", "text": "どうしますか？"}], "usage": {"input_tokens": tokens}}}
    (tmp_path / "transcript.jsonl").write_text("x\n" + json.dumps(line) + "\n", encoding="utf-8")


def test_bash_pre_tool_use_parses_payload_shell_and_transcript_once(tmp_path, calls):
    # sleep・script-chain の 2 つが構文木を、context-limit が会話の記録を読む
    _transcript(tmp_path)
    call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 1 && ndf plan status"))
    assert calls["parse_payload"] == 1 and calls["parse_shell"] == 1 and calls["read_tail"] == 1


def test_stop_classifies_the_last_reply_once(tmp_path, calls, monkeypatch):
    # stop-wait・unattended・relay-mark が同じ応答を読む
    _transcript(tmp_path)
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    payload = stop(tmp_path, "x")
    del payload["last_assistant_message"]
    call_entry("claude", "Stop", payload)
    assert calls == Counter({"parse_payload": 1, "read_tail": 1, "classify_reply": 1})


def test_shell_tree_reads_words_background_and_loops():
    script = context.parse_shell("FOO=1 sleep 30 & echo 'a b' \"c\" | ndf x; while true; do ndf plan; done")
    argvs = [c.argv for c in script.commands]
    assert ("sleep", "30") in argvs and ("echo", "a b", "c") in argvs and ("ndf", "plan") in argvs
    assert [c.background for c in script.commands if c.argv[0] == "sleep"] == [True]
    assert script.loops
    assert not context.parse_shell("ls -la").loops


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("", "none"),
        ("作業を終えた。\n\n- 結果: 完了", "report"),
        ("方針は 2 つある。\n\nどちらにしますか？", "prose_wait"),
        ("ここまでで止めた。承認をお願いします", "prose_wait"),
        ("レーン lflow が設計の承認を待っている。こちらは次へ進む", "elsewhere_wait"),
        ("別のセッションが人の答えを待っている", "elsewhere_wait"),
    ],
)
def test_classify_reply(text, kind):
    assert context.classify_reply(text) == kind


def test_classify_reply_ignores_code_lines():
    text = "結果を出した。\n```\nどうしますか？\n```"
    assert context.classify_reply(text, [False, False, True, True]) == "report"


def test_read_tail_drops_the_partial_first_line(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("aaaa\nbbbb\ncccc\n", encoding="utf-8")
    assert context.read_tail(str(path), 7) == "cccc\n"
    assert context.read_tail(str(path), 100) == "aaaa\nbbbb\ncccc\n"
    assert context.read_tail(str(tmp_path / "none"), 10) == ""


def test_context_tokens_sums_input_and_cache():
    usage = {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5}
    tail = "\n".join([json.dumps({"message": {"usage": {"input_tokens": 1}}}), json.dumps({"message": {"usage": usage}})])
    assert context.context_tokens(tail) == 35
    assert context.context_tokens("no usage") is None


def test_last_reply_falls_back_to_the_transcript(tmp_path):
    _transcript(tmp_path)
    ctx = context.HookContext("claude", "Stop", {"transcript_path": str(tmp_path / "transcript.jsonl")})
    assert ctx.last_reply == "どうしますか？" and ctx.last_reply_kind == "prose_wait"


def _assistant(content) -> str:
    return json.dumps({"type": "assistant", "message": {"content": content}})


_EARLIER = _assistant([{"type": "text", "text": "前"}])


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # 会話の記録が空・応答の行が無い
        ([], ""),
        ([json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "人"}]}})], ""),
        # いちばん後ろの応答の text をつなぐ（text でない部品と文字列でない text は外す）
        ([_EARLIER, _assistant([{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}])], "a\nb"),
        ([_assistant([{"type": "text", "text": "a"}, {"type": "text", "text": 3}, "x", {"type": "text", "text": "c"}])], "a\nc"),
        # text の部品があれば、text の値が文字列でなくてもそこで止まる（空を返す）
        ([_EARLIER, _assistant([{"type": "text"}])], ""),
        # text の部品が無い・content が配列でない・message が無い行は飛ばして前を読む
        ([_EARLIER, _assistant([{"type": "tool_use"}])], "前"),
        ([_EARLIER, _assistant("文字列の content")], "前"),
        ([_EARLIER, json.dumps({"type": "assistant", "message": "x"})], "前"),
        ([_EARLIER, json.dumps({"type": "assistant"})], "前"),
        # 書きかけの行・オブジェクトでない行は飛ばす
        ([_EARLIER, '{"type": "assistant", "mess'], "前"),
        ([_EARLIER, json.dumps(["assistant"])], "前"),
        # "assistant" の語を含まない行は読まない
        ([json.dumps({"type": "x", "message": {"content": [{"type": "text", "text": "読まない"}]}})], ""),
    ],
)
def test_last_assistant_text_reads_the_last_text_reply(lines, expected):
    assert context._last_assistant_text("\n".join(lines)) == expected


def test_last_reply_prefers_the_payload_message_over_the_transcript(tmp_path):
    _transcript(tmp_path)
    payload = {"transcript_path": str(tmp_path / "transcript.jsonl"), "last_assistant_message": "入力の応答"}
    assert context.HookContext("claude", "Stop", payload).last_reply == "入力の応答"
    payload["last_assistant_message"] = 1
    assert context.HookContext("claude", "Stop", payload).last_reply == "どうしますか？"
    assert context.HookContext("claude", "Stop", {}).last_reply == ""
