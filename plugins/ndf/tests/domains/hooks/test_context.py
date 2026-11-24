"""入力の解析は 1 回の事象で 1 度だけ（M3-H1・IH1）と、解析の関数そのもの。"""

from __future__ import annotations

import json
import shlex
from collections import Counter

import pytest

from ndf.domains.hooks import context, reply_wait
from ndf.testing import weight

from ._hooks import bash, call_entry, stop

# 解析の関数（モジュールと名前）。本文の判定はルール（`reply_wait`）の `prose_lines`（行の抜き出し）・`read_prose`
_PARSERS = (
    (context, "parse_payload"),
    (context, "parse_shell"),
    (context, "read_tail"),
    (reply_wait, "prose_lines"),
    (reply_wait, "read_prose"),
)


@pytest.fixture
def calls(monkeypatch):
    counted: Counter = Counter()
    for module, name in _PARSERS:
        real = getattr(module, name)

        def wrapped(*args, _real=real, _name=name, **kw):
            counted[_name] += 1
            return _real(*args, **kw)

        monkeypatch.setattr(module, name, wrapped)
    return counted


def _transcript(tmp_path, tokens: int = 1000) -> None:
    line = {"type": "assistant", "message": {"content": [{"type": "text", "text": "どうしますか？"}], "usage": {"input_tokens": tokens}}}
    (tmp_path / "transcript.jsonl").write_text("x\n" + json.dumps(line) + "\n", encoding="utf-8")


def test_bash_pre_tool_use_parses_payload_shell_and_transcript_once(tmp_path, calls):
    # sleep・script-chain の 2 つが構文木を、context-limit が会話の記録を読む（context-limit は .ndf のあるリポジトリだけを見る。#3995）
    (tmp_path / ".git").mkdir()
    (tmp_path / ".ndf").mkdir()
    _transcript(tmp_path)
    call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 1 && ndf plan status"))
    assert calls["parse_payload"] == 1 and calls["parse_shell"] == 1 and calls["read_tail"] == 1


def test_stop_classifies_the_last_reply_once(tmp_path, calls, monkeypatch):
    # stop-wait・unattended・relay-marker が同じ応答を読む
    _transcript(tmp_path)
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    payload = stop(tmp_path, "x")
    del payload["last_assistant_message"]
    call_entry("claude", "Stop", payload)
    # 本文の行の抜き出し（`prose_lines`）も 1 回の Stop で 1 回だけ。`read_prose` は抜き出した行を受け、抜き出し直さない（判定の返りは stop-wait も読む）
    assert calls == Counter({"parse_payload": 1, "read_tail": 1, "prose_lines": 1, "read_prose": 1})


def test_shell_tree_reads_words_background_and_loops():
    script = context.parse_shell("FOO=1 sleep 30 & echo 'a b' \"c\" | ndf x; while true; do ndf plan; done")
    argvs = [c.argv for c in script.commands]
    assert ("sleep", "30") in argvs and ("echo", "a b", "c") in argvs and ("ndf", "plan") in argvs
    assert [c.background for c in script.commands if c.argv[0] == "sleep"] == [True]
    assert script.loops
    assert not context.parse_shell("ls -la").loops


def test_shell_tree_reads_redirects_heredocs_and_assignments():
    # #3995 の Task 7: 運用上限の guard が引継ぎのコマンドを照らすための、外側のスクリプトのリダイレクト・ヒアドキュメント・代入
    script = context.parse_shell("ndf records handoff check x 2>&1 >/dev/null")
    assert script.redirects == (context.Redirect(">&", "1", False), context.Redirect(">", "/dev/null", False))
    assert (script.heredocs, script.assignments) == (0, 0)
    assert context.parse_shell("ndf records handoff check x").redirects == ()
    assert context.parse_shell("ndf x 2>&-").redirects == (context.Redirect(">&", "-", False),)
    assert context.parse_shell("cat <<E\na\nE\n").heredocs == 1
    assert context.parse_shell("cat <<< a").heredocs == 1
    assert [r.dynamic for r in context.parse_shell("ndf x > >(cat)").redirects] == [True]
    assert [r.dynamic for r in context.parse_shell('ndf x > "$F"').redirects] == [True]
    assert context.parse_shell("GIT_DIR=x ndf a").assignments == 1
    assert context.parse_shell("GIT_DIR=x; ndf a").assignments == 1


def _positions(text: str) -> dict[tuple[str, ...], tuple[bool, bool, bool]]:
    return {c.argv: (c.loop_body, c.loop_condition, c.background) for c in context.parse_shell(text).waits}


def test_shell_tree_marks_the_body_and_condition_of_while_and_until():
    got = _positions("until ! pgrep -f x >/dev/null; do sleep 20; done; sleep 2")
    assert got[("pgrep", "-f", "x")] == (False, True, False)
    assert got[("sleep", "20")] == (True, False, False)
    assert got[("sleep", "2")] == (False, False, False)
    nested = _positions("while :; do for i in 1; do while true; do sleep 1; done; done; done")
    assert nested[("sleep", "1")] == (True, False, False)
    assert _positions("for i in 1 2; do sleep 3; done")[("sleep", "3")] == (False, False, False)


def test_inner_scripts_inherit_the_outer_background_and_loop_position():
    script = context.parse_shell("bash -c 'while :; do sleep 1; done' &")
    assert [c.argv[0] for c in script.commands] == ["bash"] and not script.loops
    assert _positions("bash -c 'while :; do sleep 1; done' &")[("sleep", "1")] == (True, False, True)
    looped = _positions("while :; do eval 'sleep 1'; done")
    assert looped[("sleep", "1")] == (True, False, False)
    assert ("sleep", "600") not in _positions("echo bash -c 'sleep 600'")


def test_inner_commands_do_not_change_the_outer_commands_and_loops():
    # script-chain と full-test が見る列は外側の単純コマンドだけのまま（#3851 の条件 9）
    script = context.parse_shell("bash -c 'ndf ci watch --pr 1; while :; do ndf plan; done'")
    assert [c.argv for c in script.commands] == [("bash", "-c", "ndf ci watch --pr 1; while :; do ndf plan; done")]
    assert script.loops is False
    assert ("ndf", "ci", "watch", "--pr", "1") in [c.argv for c in script.waits]


@pytest.mark.parametrize(
    ("command", "inner"),
    [
        # 中身を読むシェル・eval の名前は、ラッパーを外した後の先頭の語からパスを外して見る
        ("/bin/bash -c 'sleep 1'", ("sleep", "1")),
        ("/usr/bin/dash -c 'sleep 1'", ("sleep", "1")),
        ("timeout 5 /bin/sh -c 'sleep 1'", ("sleep", "1")),
        ("/usr/bin/env X=1 bash -xc 'sleep 1'", ("sleep", "1")),
        ("nohup eval 'sleep 1'", ("sleep", "1")),
    ],
)
def test_inner_scripts_read_the_name_after_the_wrappers_without_its_path(command, inner):
    assert inner in [c.argv for c in context.parse_shell(command).waits]


@pytest.mark.parametrize(
    "command",
    [
        # ラッパーだけ・コマンドを起動しない形・別の名前・引数の無い eval は中身を読まない
        "nohup timeout 5",
        "command -v bash",
        "/bin/bashx -c 'sleep 1'",
        "eval",
        "bash -c",
    ],
)
def test_inner_scripts_skip_forms_without_a_shell_body(command):
    script = context.parse_shell(command)
    assert script.waits == script.commands


def test_inner_scripts_stop_at_the_depth_limit():
    text = "sleep 1"
    for _ in range(8):
        text = f"bash -c {shlex.quote(text)}"
    waits = context.parse_shell(text).waits
    assert len(waits) == 1 + context._INNER_DEPTH and ("sleep", "1") not in [c.argv for c in waits]


def _waits(text: str) -> bool:
    """待つコマンドが `ndf lanes wait` と `ndf ci watch` だけのときの `only_commands`。"""
    return context.only_commands(text, lambda cmd: cmd.body_argv[:3] in (("ndf", "lanes", "wait"), ("ndf", "ci", "watch")))


@pytest.mark.parametrize(
    "text",
    [
        "ndf lanes wait p",
        "ndf lanes wait a; ndf lanes wait b && ndf ci watch --pr 1 || ndf lanes wait c",
        "ndf lanes wait a\nndf lanes wait b\n",
        "ndf lanes wait p > log 2>&1",
        "> log ndf lanes wait p",
        "ndf lanes wait p &> log",
        'ndf lanes wait "$P"',
        "bash -c 'ndf lanes wait p'",
        'bash -c "ndf lanes wait p"',
        r"bash -c ndf\ lanes\ wait\ p",
        # 外側のシェルは `\$` を展開しない
        r'bash -c "ndf lanes wait \$P"',
        "bash --norc -eu -c 'ndf lanes wait p' name arg",
        'timeout 600 bash -c "ndf ci watch --pr 1"',
        "eval ndf lanes wait p",
        "eval ndf lanes wait p --after 3",
        "ndf lanes wait a && ndf lanes wait b > log",
        "ndf lanes wait a || ndf lanes wait b > log 2>&1",
        "timeout -k 5 600 nice -n 10 ndf lanes wait p",
        "eval 'ndf lanes wait a;' 'ndf lanes wait b'",
    ],
)
def test_only_commands_accepts_a_text_made_of_accepted_commands(text):
    assert _waits(text)


@pytest.mark.parametrize(
    "text",
    [
        # 受け入れないコマンド・空の文
        "ndf lanes wait p && rm x",
        "ndf lanes wait p\nrm x",
        "bash x.sh",
        "",
        "bash -c ''",
        # 単純なコマンドの並びでない形
        "ndf lanes wait p | tail",
        "ndf lanes wait p &",
        "ndf lanes wait p; > output",
        "for i in 1; do ndf lanes wait p; done",
        "until ndf lanes wait p; do ndf lanes wait q; done",
        "if ndf lanes wait p; then ndf lanes wait q; fi",
        "(ndf lanes wait p)",
        "{ ndf lanes wait p; }",
        "! ndf lanes wait p",
        "f() { ndf lanes wait p; }",
        "A=1 ndf lanes wait p",
        "ndf lanes wait p <<E\na\nE\n",
        "ndf lanes wait p <<< a",
        "ndf lanes wait p > >(cat)",
        "ndf lanes wait $(rm x)",
        'ndf lanes wait "${X:-$(rm y)}"',
        # 構文の誤り
        "ndf lanes wait 'p",
        "ndf lanes wait p; done",
        # 中の文に受け入れないコマンドか形がある
        'bash -c "ndf lanes wait p; rm x"',
        "bash -c 'ndf lanes wait p &'",
        r'bash -c "ndf lanes wait \$(rm x)"',
        "eval 'ndf lanes wait p' ';' 'rm x'",
        # 中の文が外側の展開で決まる
        'bash -c "ndf lanes wait $ARGS"',
        'eval "$CMD"',
        "eval ndf lanes wait *",
        r"bash -c ndf\ lanes\ wait\ $P",
        "bash -c 'ndf lanes wait '\"$P\"",
        'timeout 600 bash -c "ndf lanes wait $ARGS"',
        # `-c` が外側のシェルのオプションと決まらない
        "bash x.sh -c 'ndf lanes wait p'",
        "bash -- -c 'ndf lanes wait p'",
        "bash -c$O 'ndf lanes wait p'",
        # シェルが起動ファイルを読みうる
        "bash -lc 'ndf lanes wait p'",
        "bash -l -c 'ndf lanes wait p'",
        "bash -ic 'ndf lanes wait p'",
        "bash --rcfile=x -c 'ndf lanes wait p'",
        "zsh -c 'ndf lanes wait p'",
        "env 'BASH_ENV=./setup.sh' bash -c 'ndf lanes wait p'",
        "exec -l bash -c 'ndf lanes wait p'",
        # ラッパーかコマンド名が、動かすものを展開・置き換えで変えうる
        "timeout $D ndf lanes wait p",
        "timeout 5 $X ndf lanes wait p",
        "$X/ndf lanes wait p",
        "xargs -I wait ndf lanes wait p",
        "timeout 5 'xargs' -I wait ndf lanes wait p",
        # ロケールで翻訳される引用
        'bash -c $"ndf lanes wait p"',
        'ndf $"lanes" wait p',
        'timeout 5 $"ndf" lanes wait p',
        'eval $"ndf lanes wait p"',
        'eval ndf lanes wait "a"$"b"',
        # コマンドの列に付いたリダイレクトの中のコマンドの無いリダイレクト
        "ndf lanes wait p && > output",
        "ndf lanes wait a && rm x > log",
        # `env -S` で分けた語は引数のノードと対応しない
        "env -S \"bash -c 'ndf lanes wait p'\"",
    ],
)
def test_only_commands_rejects_a_text_it_cannot_confirm(text):
    assert not _waits(text)


@pytest.mark.parametrize("depth, expected", [(context._INNER_DEPTH, True), (context._INNER_DEPTH + 1, False)])
def test_only_commands_reads_nested_scripts_up_to_the_depth_limit(depth, expected):
    text = "ndf lanes wait p"
    for _ in range(depth):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        text = f"bash -c {text}"
    assert _waits(text) is expected


@pytest.mark.parametrize(
    ("command", "body"),
    [
        ("sleep 600", ("sleep", "600")),
        ("timeout 590 sleep 600", ("sleep", "600")),
        ("timeout --signal TERM 590 sleep 600", ("sleep", "600")),
        ("timeout -k 5 -sKILL 590 sleep 600", ("sleep", "600")),
        ("timeout -s TERM -k5 590 sleep 600", ("sleep", "600")),
        ("timeout -- 590 sleep 600", ("sleep", "600")),
        ("env X=1 nohup sleep 1", ("sleep", "1")),
        ("env -u HOME sleep 1", ("sleep", "1")),
        ("env -u HOME X=1 sleep 600", ("sleep", "600")),
        ("env - sleep 1", ("sleep", "1")),
        ("env -S 'sleep 600'", ("sleep", "600")),
        ("env -S'sleep 600'", ("sleep", "600")),
        ("env -S '-i sleep 600'", ("sleep", "600")),
        ("env -S '-i X=1 sleep 600'", ("sleep", "600")),
        ("env --split-string='-u HOME X=1 sleep 600'", ("sleep", "600")),
        (r"env -S 'sleep\_600'", ("sleep", "600")),
        (r"""env -S '"-i"\_sleep 600'""", ("sleep", "600")),
        ("env -S 'sleep 600 #x'", ("sleep", "600")),
        (r"env -S 'sleep 600\cx'", ("sleep", "600")),
        ("env -S 'sleep ${X}'", ("sleep", "${X}")),
        ("nice -n 10 sleep 1", ("sleep", "1")),
        ("nice -10 sleep 1", ("sleep", "1")),
        ("ionice -c 3 sleep 600", ("sleep", "600")),
        ("stdbuf -oL sleep 600", ("sleep", "600")),
        ("nohup sleep 600", ("sleep", "600")),
        ("time -f %e sleep 600", ("sleep", "600")),
        ("time -p sleep 600", ("sleep", "600")),
        ("exec -a w sleep 600", ("sleep", "600")),
        ("command -p sleep 600", ("sleep", "600")),
        ("xargs -n 1 sleep", ("sleep",)),
        ("/usr/bin/timeout 5 pgrep -f x", ("pgrep", "-f", "x")),
        # ラッパーのコマンドだけ・コマンドを起動しない形・表に無いオプション・読めない env -S は空
        ("nohup timeout 5", ()),
        ("command -v sleep", ()),
        ("ionice -p 1", ()),
        ("timeout --bogus 590 sleep 600", ()),
        ("""env -S '"sleep 600'""", ()),
        ("env -S 'sleep $X'", ()),
        (r"env -S 'sleep\q'", ()),
    ],
)
def test_body_argv_strips_wrappers_and_their_arguments(command, body):
    assert context.parse_shell(command).commands[0].body_argv == body


@pytest.mark.parametrize(
    ("command", "body"),
    [
        # nice の旧形式の数字オプション（`-10`）は値を付けた 1 語として外す。パス付き・重ね・-n との併用も同じ
        ("nice -5 sleep 600", ("sleep", "600")),
        ("nice -19 sleep 600", ("sleep", "600")),
        ("/usr/bin/nice -10 sleep 600", ("sleep", "600")),
        ("nice -10 -5 sleep 600", ("sleep", "600")),
        ("nice -10 -n 5 sleep 600", ("sleep", "600")),
        ("nice -n 5 -10 sleep 600", ("sleep", "600")),
        ("nice -10 -- sleep 600", ("sleep", "600")),
        ("nice -10 nohup sleep 600", ("sleep", "600")),
        ("timeout 5 nice -10 sleep 600", ("sleep", "600")),
        ("nice -n10 sleep 600", ("sleep", "600")),
        ("nice --adjustment=10 sleep 600", ("sleep", "600")),
        # 数字の後に文字が続く・`--10`・数字の無い `-` は数字オプションではない
        ("nice -10x sleep 600", ()),
        ("nice --10 sleep 600", ()),
        ("nice -x sleep 600", ()),
        ("nice -10", ()),
        # 数字オプションは nice だけの特例。他のラッパーでは表に無いオプションとして読めない
        ("ionice -10 sleep 600", ()),
        ("stdbuf -10 sleep 600", ()),
        ("nohup -10 sleep 600", ()),
        ("time -10 sleep 600", ()),
        ("timeout -10 5 sleep 600", ()),
    ],
)
def test_body_argv_reads_the_numeric_option_only_for_nice(command, body):
    assert context.parse_shell(command).commands[0].body_argv == body


def test_body_argv_keeps_argv_as_it_was():
    cmd = context.parse_shell("timeout 590 sleep 600").commands[0]
    assert cmd.argv == ("timeout", "590", "sleep", "600")


def test_wrapper_table_covers_the_shared_wrappers():
    assert set(context._WRAPPER_ARGS) == weight.WRAPPERS


@pytest.mark.parametrize(
    ("text", "words"),
    [
        (r"sleep\_600", ("sleep", "600")),
        (r'"a\_b" c', ("a b", "c")),
        (r"'a\_b'", (r"a\_b",)),
        (r"a\tb", ("a\tb",)),
        (r"'it\'s'", ("it's",)),
        ('"" x', ("", "x")),
        ("#all", ()),
        ("a#b", ("a#b",)),
        ("'a", None),
        (r"a\z", None),
        ("$X", None),
    ],
)
def test_split_env_string_follows_the_gnu_env_rules(text, words):
    assert context._split_env_string(text) == words


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("", "none"),
        ("作業を終えた。\n\n- 結果: 完了", "report"),
        ("方針は 2 つある。\n\nどちらにしますか？", "prose_wait"),
        ("ここまでで止めた。承認をお願いします", "prose_wait"),
        ("レーン lflow が設計の承認を待っている。こちらは次へ進む", "elsewhere_wait"),
        ("別のセッションが人の回答を待っている", "elsewhere_wait"),
    ],
)
def test_last_reply_kind_follows_the_rule(text, kind):
    ctx = context.HookContext("claude", "Stop", {"last_assistant_message": text})
    assert ctx.last_reply_kind == kind and ctx.has_reply_prose is (kind != "none")


def test_last_reply_kind_ignores_code_lines():
    ctx = context.HookContext("claude", "Stop", {"last_assistant_message": "結果を出した。\n```\nどうしますか？\n```"})
    assert ctx.last_reply_kind == "report"


def test_last_reply_kind_does_not_read_the_prose_lines_twice(monkeypatch):
    # `has_reply_prose` と `last_reply_kind` が同じ行を読み、`prose_lines`（行の抜き出し）を 1 回だけ打つ
    real = reply_wait.prose_lines
    extracted: list[str] = []

    def counted(text):
        extracted.append(text)
        return real(text)

    monkeypatch.setattr(reply_wait, "prose_lines", counted)
    ctx = context.HookContext("claude", "Stop", {"last_assistant_message": "どちらにしますか？"})
    assert ctx.has_reply_prose and ctx.last_reply_kind == "prose_wait"
    assert extracted == ["どちらにしますか？"]


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


def _user(content) -> str:
    return json.dumps({"type": "user", "message": {"content": content}})


_PROMPT = _user([{"type": "text", "text": "進めて"}])
_ASK = _assistant([{"type": "tool_use", "name": "AskUserQuestion", "input": {}}])
_BASH = _assistant([{"type": "tool_use", "name": "Bash", "input": {}}])
_RESULT = _user([{"type": "tool_result", "tool_use_id": "t1", "content": "1 つ目"}])
_REPLY = _assistant([{"type": "text", "text": "本文"}])
_META = json.dumps({"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "メタ"}]}})


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # 入力 → AskUserQuestion → 結果 → 本文（Tool の結果の user の行を入力と読まない）
        ([_PROMPT, _ASK, _RESULT, _REPLY], True),
        ([_PROMPT, _ASK, _RESULT], True),
        # 入力 → Bash → 本文
        ([_PROMPT, _BASH, _RESULT, _REPLY], False),
        # 前の入力の前にだけ AskUserQuestion がある
        ([_PROMPT, _ASK, _RESULT, _PROMPT, _BASH, _REPLY], False),
        # content が文字列の user の行も入力
        ([_ASK, _user("進めて"), _REPLY], False),
        # AskUserQuestion の後のメタの行（isMeta が真）は入力に数えない
        ([_PROMPT, _ASK, _RESULT, _META, _REPLY], True),
        # 末尾に利用者の入力が無ければ読めた範囲で探す
        ([_ASK, _REPLY], True),
        ([], False),
        # 壊れた行・オブジェクトでない行が混じっても読める行で決める
        ([_PROMPT, '{"type": "user", "mess', _ASK, json.dumps(["user"]), _REPLY], True),
    ],
)
def test_asked_since_prompt_looks_back_to_the_last_user_input(lines, expected):
    assert context.asked_since_prompt("\n".join(lines)) is expected


def test_asked_this_turn_reads_the_transcript_tail(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join([_PROMPT, _ASK, _RESULT, _REPLY]) + "\n", encoding="utf-8")
    assert context.HookContext("claude", "Stop", {"transcript_path": str(path)}).asked_this_turn is True
    path.write_text("\n".join([_PROMPT, _BASH, _REPLY]) + "\n", encoding="utf-8")
    assert context.HookContext("claude", "Stop", {"transcript_path": str(path)}).asked_this_turn is False
    assert context.HookContext("claude", "Stop", {}).asked_this_turn is False
