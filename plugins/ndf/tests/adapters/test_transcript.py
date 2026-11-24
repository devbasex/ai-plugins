"""会話の記録の読み手（#4089 の「記録の読み手」。#4068 の実装の中で先に作る）。"""

from __future__ import annotations

import json

import pytest

from ndf.adapters import transcript
from ndf.adapters.transcript import ContextRead, Reply, Tail, ToolUse, last_context, read_tail, timestamp_seconds

T0 = "2026-11-22T12:00:00.000Z"


def usage(inp=1, read=0, write=0, **extra):
    return {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": write, "output_tokens": 5, **extra}


def reply(mid, *content, use=None, ts=T0, model="claude-opus-5-5", **kw):
    msg = {"id": mid, "model": model, "content": list(content), "usage": use if use is not None else usage()}
    if use is False:
        del msg["usage"]
    return json.dumps({"type": "assistant", "timestamp": ts, "message": msg, **kw})


def text(s):
    return {"type": "text", "text": s}


def tool(i):
    return {"type": "tool_use", "id": i, "name": "Bash", "input": {}}


def user(s="やって", **kw):
    return json.dumps({"type": "user", "timestamp": T0, "message": {"role": "user", "content": s}, **kw})


def result(i):
    return json.dumps({"type": "user", "timestamp": T0, "message": {"content": [{"type": "tool_result", "tool_use_id": i}]}})


def other():
    return json.dumps({"type": "last-prompt", "lastPrompt": "x"})


BOUNDARY = json.dumps({"type": "system", "subtype": "compact_boundary"})


def tail(*lines):
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- timestamp_seconds


def test_timestamp_seconds_reads_offsets_and_rejects_naive_or_broken():
    assert timestamp_seconds("2026-11-22T12:00:00.123Z") == pytest.approx(timestamp_seconds("2026-11-22T21:00:00.123+09:00"))
    assert timestamp_seconds("2026-11-22T12:00:00Z") is not None
    for bad in ("yesterday", "", "2026-11-22T12:00:00", 12, None):
        assert timestamp_seconds(bad) is None


# ---------------------------------------------------------------- read_tail（テスト設計 15）


def test_read_tail_keeps_a_whole_first_line_and_drops_a_cut_one(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"A" * 9 + b"\n" + b"B" * 9 + b"\n" + b"C" * 9 + b"\n")
    assert read_tail(str(path), 20) == "B" * 9 + "\n" + "C" * 9 + "\n"
    assert read_tail(str(path), 15) == "C" * 9 + "\n"
    assert read_tail(str(path), 40) == path.read_text()
    path.write_bytes(b"x" * 30)
    assert read_tail(str(path), 10) == ""
    assert read_tail(str(tmp_path / "none"), 10) == ""
    path.write_text("aaaa\nbbbb\ncccc\n", encoding="utf-8")
    assert read_tail(str(path), 7) == "cccc\n"


def test_read_tail_has_no_fixed_cap(tmp_path):
    path = tmp_path / "t.jsonl"
    first = "first-line\n"
    path.write_text(first + "y" * 600 * 1024 + "\n", encoding="utf-8")
    assert read_tail(str(path), 1024 * 1024).startswith(first)


# ---------------------------------------------------------------- last_context


def test_tool_use_finds_the_reply_and_reads_the_adopted_line():
    rows = tail(
        user(),
        reply("m1", {"type": "thinking", "thinking": "."}, use=usage(1, 10, 0), ts="2026-11-22T12:00:00Z"),
        other(),
        reply("m1", text("見る"), use=usage(1, 20, 0), ts="2026-11-22T12:00:01Z"),
        reply("m1", tool("t1"), use=usage(1, 30, 5), ts="2026-11-22T12:00:02Z"),
    )
    got = last_context(rows, ToolUse("t1"))
    assert (got.state, got.tokens, got.message_id, got.timestamp) == ("current", 36, "m1", "2026-11-22T12:00:02Z")
    assert got.entry == json.loads(rows.splitlines()[-1])
    later = tail(rows.rstrip("\n"), result("t1"), reply("m2", text("done"), use=usage(1, 100, 0)))
    assert last_context(later, ToolUse("t1")).tokens == 101 and last_context(later, ToolUse("t1")).state == "current"


def test_tool_use_missing_is_behind_and_parallel_ids_both_match():
    rows = tail(reply("m1", tool("a"), use=usage(1, 50)), result("a"), user("次"))
    got = last_context(rows, ToolUse("zz"))
    assert (got.state, got.reason, got.tokens) == ("behind", "unrecorded", 51)
    par = tail(user(), reply("m2", tool("a")), reply("m2", tool("b")))
    assert last_context(par, ToolUse("a")).state == "current" and last_context(par, ToolUse("b")).state == "current"
    assert last_context(par, ToolUse("")).state == "behind"


def test_reply_matches_text_ignoring_whitespace_and_needs_no_user_line_after():
    rows = tail(user(), reply("m1", text("a")), reply("m1", text("b")))
    for said in ("a\n\nb", "ab", "ab\n"):
        assert last_context(rows, Reply(said)).state == "current"
    assert last_context(rows, Reply("ba")).state == "behind"
    assert last_context(rows, Reply(None)) == ContextRead("behind", "unrecorded", 1, "m1", T0)
    newline = tail(user(), reply("m1", text("done\n")))
    assert last_context(newline, Reply("done")).state == "current"
    woke = tail(reply("m1", text("待機を続ける")), user("【ndf-relay】起きて"))
    assert last_context(woke, Reply("待機を続ける")).state == "behind"


def test_tail_counts_user_lines_after_the_reply_but_not_other_lines():
    base = [user(), reply("m1", text("x"))]
    assert last_context(tail(*base, other(), reply("s", text("y"), isSidechain=True)), Tail()).state == "current"
    for after in (result("t"), user("次"), user("skill", isMeta=True)):
        got = last_context(tail(*base, after), Tail())
        assert (got.state, got.reason) == ("behind", "after")


def test_synthetic_reply_is_skipped():
    rows = tail(user(), reply("m1", text("x"), use=usage(1, 99)), reply("s", text("x"), model="<synthetic>", use=usage(0)))
    assert last_context(rows, Tail()).tokens == 100


def test_compact_boundary_stops_the_scan():
    big = reply("m0", text("x"), use=usage(1, 600_000))
    assert last_context(tail(big, BOUNDARY, user("summary")), Tail()) == ContextRead("none", "compacted")
    assert last_context(tail(big, BOUNDARY, user("s"), reply("m1", text("y"), use=usage(1, 30_000))), Tail()).tokens == 30_001
    assert last_context("", Tail()) == ContextRead("none", "empty")
    assert last_context(tail(user(), other()), Tail()) == ContextRead("none", "empty")


def test_unreadable_line_newer_than_the_reply_is_partial():
    rows = tail(user(), reply("m1", text("x")))
    assert last_context(rows + '{"type": "assi', Tail()) == ContextRead("none", "partial")
    assert last_context("{broken\n" + rows, Tail()).state == "current"


def test_form_is_only_for_the_matched_reply():
    no_input = usage()
    del no_input["input_tokens"]
    rows = tail(user(), reply("m1", text("x"), use=no_input))
    assert last_context(rows, Reply("x")).reason == "form"
    assert last_context(tail(user(), reply("m1", text("x"), use=usage(1, "9"))), Reply("x")).reason == "form"
    lacks_cache = {"input_tokens": 4, "output_tokens": 1}
    assert last_context(tail(user(), reply("m1", text("x"), use=lacks_cache)), Reply("x")).tokens == 4
    # 外れた応答の形の違いは、突き合わせの理由のまま
    assert last_context(rows, Reply("other")) == ContextRead("none", "unrecorded")


def test_adopted_line_without_usage_is_form_and_keeps_id_and_time():
    rows = tail(user(), reply("m1", text("x"), use=usage(1, 50)), reply("m1", text("y"), use=False, ts="2026-11-22T12:00:09Z"))
    got = last_context(rows, Reply("xy"))
    assert (got.state, got.reason, got.tokens, got.message_id, got.timestamp) == ("none", "form", None, "m1", "2026-11-22T12:00:09Z")
    assert got.entry == json.loads(rows.splitlines()[-1])
    assert last_context(rows, Reply("zz")) == ContextRead("none", "unrecorded")
    for ts in (None, 5, "yesterday", "2026-11-22T12:00:00"):
        line = json.loads(reply("m1", text("x")))
        line["timestamp"] = ts
        got = last_context(tail(user(), json.dumps(line)), Reply("x"))
        assert (got.reason, got.timestamp, got.message_id) == ("form", None, "m1")
    nid = json.loads(reply("m1", text("x")))
    nid["message"]["id"] = 7
    assert last_context(tail(user(), json.dumps(nid)), Reply("x")).message_id is None
    broken_prev = tail(user(), reply("m1", text("x"), use={"bad": 1}), reply("m1", text("y"), use=usage(1, 2)))
    assert last_context(broken_prev, Reply("xy")).tokens == 3


def test_scans_stop_early(monkeypatch):
    loads = {"n": 0}
    real = transcript.json_line

    def counted(line):
        loads["n"] += 1
        return real(line)

    monkeypatch.setattr(transcript, "json_line", counted)
    old = [user(f"古い {i}") for i in range(100)]
    rows = tail(*old, reply("m0", tool("deep")), user("次"), reply("m1", text("a")), reply("m1", tool("t1")))
    last_context(rows, Tail())
    assert loads["n"] == 1
    loads["n"] = 0
    last_context(rows, Reply("a"))
    assert loads["n"] == 4  # まとまりの 2 行・利用者の行・前の応答の 1 行
    loads["n"] = 0
    assert last_context(rows, ToolUse("t1")).state == "current" and loads["n"] == 4
    loads["n"] = 0
    assert last_context(rows, ToolUse("deep")).state == "current" and loads["n"] == 4
    loads["n"] = 0
    assert last_context(rows, ToolUse("nowhere")).state == "behind" and loads["n"] == 4
    loads["n"] = 0
    assert last_context(rows, ToolUse("")).state == "behind" and loads["n"] == 1
