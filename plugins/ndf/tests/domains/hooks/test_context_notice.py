"""文脈の量の guard の判断開始の値の知らせ（#3995 の 3995:5・3995:16）。運用上限で止める側は test_guards。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks import counter, tables
from ndf.domains.hooks.guards import context_limit
from ndf.domains.relay.context_thresholds import thresholds

from ._hooks import advise_text, call_entry, deny_reason, sample, state_dir, usage_transcript


def _tool(cwd, event, tool_use_id="toolu_A"):
    """Bash の PreToolUse・PostToolUse を流した出力。`tool_use_id` が None なら入力に ID を置かない。"""
    payload = sample("claude", event, cwd, tool_name="Bash", tool_input={"command": "ls"}, tool_use_id=tool_use_id)
    if tool_use_id is None:
        del payload["tool_use_id"]
    return call_entry("claude", event, payload)


def _notice(out):
    text = advise_text(out)
    return text is not None and "判断開始の値" in text


def _user_input(cwd, relay=False):
    """利用者の入力（`relay` なら中継が送った入力: マーカーを立ててから送る）。"""
    if relay:
        counter.mark_relay(sample("claude", "UserPromptSubmit", cwd)["session_id"], state_dir())
    call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", cwd))


def _memo_size(cwd):
    session = sample("claude", "PostToolUse", cwd)["session_id"]
    data = json.loads((state_dir() / "hooks" / "sessions" / f"{session}.json").read_text(encoding="utf-8"))
    return len(data.get("memo", {}).get(context_limit.MEMO_KEY, {}))


@pytest.mark.parametrize(("tokens", "notified"), [(249_999, False), (250_000, True), (499_999, True), (500_000, False)])
def test_context_limit_notices_from_the_decide_from_value_below_the_limit(tmp_path, tokens, notified):
    # #3995 の 3995:5・3995:16: PostToolUse は判断開始の値（250,000）以上・運用上限（500,000）未満で知らせる
    usage_transcript(tmp_path, tokens)
    out = _tool(tmp_path, "PostToolUse")
    assert _notice(out) is notified
    assert notified or out is None


def test_context_limit_notices_once_per_user_input(tmp_path):
    # #3995 の 3995:5: 同じ入力の 2 回目は知らせず、利用者の入力の後は知らせ、中継が送った入力の後は知らせない
    usage_transcript(tmp_path, 250_000)
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_1"))
    assert _tool(tmp_path, "PostToolUse", "toolu_2") is None
    _user_input(tmp_path)
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_3"))
    _user_input(tmp_path, relay=True)
    assert _tool(tmp_path, "PostToolUse", "toolu_4") is None


def test_context_limit_does_not_notice_in_pre_tool_use(tmp_path):
    usage_transcript(tmp_path, 300_000)
    assert _tool(tmp_path, "PreToolUse") is None


def test_context_limit_reads_nothing_after_the_notice(tmp_path, counted_tail):
    # #3995 の 3995:5: 知らせた後の同じ入力の PostToolUse は会話の記録を読まない
    usage_transcript(tmp_path, 250_000)
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_0"))
    counted_tail["n"] = 0
    for i in range(10):
        assert _tool(tmp_path, "PostToolUse", f"toolu_{i + 1}") is None
    assert counted_tail["n"] == 0


def test_context_limit_neither_notices_nor_reads_without_a_usable_counter(tmp_path, counted_tail):
    usage_transcript(tmp_path, 250_000)
    session = sample("claude", "PostToolUse", tmp_path)["session_id"]
    path = state_dir() / "hooks" / "sessions" / f"{session}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    assert _tool(tmp_path, "PostToolUse") is None
    assert counted_tail["n"] == 0


def test_context_limit_reads_once_per_tool_call_below_the_decide_from_value(tmp_path, counted_tail):
    # #3995 の 3995:5: 同じ ID の PreToolUse と PostToolUse の組は PreToolUse だけが読む。続く応答の usage が 250,000 なら知らせる
    usage_transcript(tmp_path, 200_000)
    for i in range(10):
        assert _tool(tmp_path, "PreToolUse", f"toolu_{i}") is None
        assert _tool(tmp_path, "PostToolUse", f"toolu_{i}") is None
    assert counted_tail["n"] == 10
    usage_transcript(tmp_path, 250_000)
    assert _tool(tmp_path, "PreToolUse", "toolu_next") is None
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_next"))
    assert counted_tail["n"] == 11


@pytest.mark.parametrize("calls", [2, 3])
def test_context_limit_reads_once_per_parallel_call(tmp_path, counted_tail, calls):
    # #3995 の 3995:5: 1 つの応答の並列の呼び出し（Pre(A) → Pre(B) → Post(A) → Post(B)）は ID ごとに覚え、読み取りは PreToolUse の回数
    usage_transcript(tmp_path, 200_000)
    ids = [f"toolu_{i}" for i in range(calls)]
    for event in ("PreToolUse", "PostToolUse"):
        for i in ids:
            assert _tool(tmp_path, event, i) is None
    assert counted_tail["n"] == calls


@pytest.mark.parametrize("tool_use_id", ["toolu_other", None])
def test_context_limit_reads_for_a_post_tool_use_without_its_pre_tool_use(tmp_path, counted_tail, tool_use_id):
    # PreToolUse の無い・ID の違う・ID の無い PostToolUse は 1 回読んで判定する
    usage_transcript(tmp_path, 250_000)
    assert _tool(tmp_path, "PreToolUse", "toolu_A") is None
    counted_tail["n"] = 0
    assert _notice(_tool(tmp_path, "PostToolUse", tool_use_id))
    assert counted_tail["n"] == 1


def test_context_limit_reads_again_for_the_same_post_tool_use(tmp_path, counted_tail):
    # 同じ ID の PostToolUse の 2 度目は覚えた値が消えているので 1 回読む
    usage_transcript(tmp_path, 200_000)
    _tool(tmp_path, "PreToolUse")
    _tool(tmp_path, "PostToolUse")
    counted_tail["n"] = 0
    assert _tool(tmp_path, "PostToolUse") is None
    assert counted_tail["n"] == 1


@pytest.fixture
def counted_main(monkeypatch):
    """メインディレクトリを探した回数（`n`）。"""
    calls = {"n": 0}
    real = context_limit._main

    def counting(cwd):
        calls["n"] += 1
        return real(cwd)

    monkeypatch.setattr(context_limit, "_main", counting)
    return calls


def test_context_limit_post_tool_use_reuses_the_pre_tool_use_check(tmp_path, counted_main):
    # 同じ ID・同じ cwd の PreToolUse が覚えた値があれば PostToolUse はリポジトリを探さない。知らせ済みの後も探さない
    usage_transcript(tmp_path, 200_000)
    assert _tool(tmp_path, "PreToolUse") is None
    counted_main["n"] = 0
    assert _tool(tmp_path, "PostToolUse") is None
    assert counted_main["n"] == 0
    usage_transcript(tmp_path, 250_000)
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_B"))
    counted_main["n"] = 0
    for i in range(3):
        assert _tool(tmp_path, "PostToolUse", f"toolu_{i}") is None
    assert counted_main["n"] == 0


def test_context_limit_post_tool_use_checks_again_for_another_cwd(tmp_path, counted_main):
    # 覚えた値の cwd と違う PostToolUse は、その cwd のリポジトリを探し直して判定する
    usage_transcript(tmp_path, 300_000)
    assert _tool(tmp_path, "PreToolUse") is None
    other = tmp_path / "sub"
    other.mkdir()
    payload = sample("claude", "PostToolUse", tmp_path, tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="toolu_A")
    counted_main["n"] = 0
    assert _notice(call_entry("claude", "PostToolUse", {**payload, "cwd": str(other)}))
    assert counted_main["n"] == 1


def test_context_limit_post_tool_use_checks_the_repo_again_before_the_notice(tmp_path):
    # Tool の実行で判定の対象から外れたら（PreToolUse の後に .ndf が無くなる）、覚えた値があっても知らせず、通知枠も使わない
    usage_transcript(tmp_path, 300_000)
    assert _tool(tmp_path, "PreToolUse") is None
    (tmp_path / ".ndf").rename(tmp_path / "ndf-off")
    assert _tool(tmp_path, "PostToolUse") is None
    (tmp_path / "ndf-off").rename(tmp_path / ".ndf")
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_B"))


def test_context_limit_remembers_the_cwd_as_a_digest(tmp_path):
    # 覚えた値の cwd は作業ディレクトリのパスを平文で残さない
    usage_transcript(tmp_path, 200_000)
    assert _tool(tmp_path, "PreToolUse") is None
    session = sample("claude", "PreToolUse", tmp_path)["session_id"]
    text = (state_dir() / "hooks" / "sessions" / f"{session}.json").read_text(encoding="utf-8")
    assert _memo_size(tmp_path) == 1
    assert str(tmp_path) not in text


def test_context_limit_remembers_nothing_after_the_notice(tmp_path):
    # #3995 の 3995:5: 知らせた後の同じ入力の組は覚えた項目を増やさない。運用上限の判定は今のまま。利用者の入力の後は再び覚える
    usage_transcript(tmp_path, 250_000)
    assert _notice(_tool(tmp_path, "PostToolUse", "toolu_0"))
    size = _memo_size(tmp_path)
    usage_transcript(tmp_path, 400_000)
    for i in range(10):
        assert _tool(tmp_path, "PreToolUse", f"toolu_{i + 1}") is None
        assert _tool(tmp_path, "PostToolUse", f"toolu_{i + 1}") is None
    assert _memo_size(tmp_path) == size
    usage_transcript(tmp_path, 500_000)
    assert deny_reason(_tool(tmp_path, "PreToolUse", "toolu_x")) is not None
    assert _memo_size(tmp_path) == size
    usage_transcript(tmp_path, 400_000)
    _user_input(tmp_path)
    _tool(tmp_path, "PreToolUse", "toolu_y")
    assert _memo_size(tmp_path) == 1


def test_context_limit_skips_the_post_tool_use_of_a_subagent(tmp_path, counted_tail):
    # #3995 の 3995:15: agent_id があれば 300,000 の PostToolUse も知らせず、会話の記録を読まない
    usage_transcript(tmp_path, 300_000)
    payload = {**sample("claude", "PostToolUse", tmp_path), "agent_id": "a0123"}
    assert call_entry("claude", "PostToolUse", payload) is None
    assert counted_tail["n"] == 0


@pytest.mark.parametrize("repo", ["outside", "no-ndf"])
def test_context_limit_skips_post_tool_use_outside_ndf_without_reading(tmp_path, counted_tail, repo):
    cwd = tmp_path / "work"
    cwd.mkdir()
    if repo == "no-ndf":
        (cwd / ".git").mkdir()
    line = {"type": "assistant", "message": {"usage": {"input_tokens": 300_000}}}
    (cwd / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert _tool(cwd, "PostToolUse") is None
    assert counted_tail["n"] == 0


def test_context_limit_notice_names_the_values_and_the_cut_points():
    # #3995: 知らせの文は文脈の量・判断開始の値・運用上限と役ごとの切りどころを書き、注入の上限（600 字）以下
    t = thresholds({}, tables.policy_value)
    text = context_limit._notice(499_999, t)
    for part in (str(t.decide_from), str(t.limit), "499999", "カットポイント", "wait"):
        assert part in text
    assert len(text) <= tables.policy_value("hook.budget.post-tool-use", "inject_chars")
