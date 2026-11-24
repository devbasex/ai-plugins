"""guard の本体の判定（sleep・process-wait・script-chain・context-limit・ask-forward・relay-marker・stop-wait の別の場所の待ち）。unattended は test_unattended、stop-wait の判定の選び方は test_stop_wait。"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.domains.hooks import budget, context, dispatch, strict, tables
from ndf.domains.hooks.guards import ask, context_limit, process_wait, sleep
from ndf.domains.records import cli as records_cli
from ndf.domains.relay import signals
from ndf.domains.relay.context_thresholds import thresholds

from ._hooks import advise_text, bash, call_entry, deny_reason, ndf_repo, records, sample, stop, usage_transcript

FENCE = "```ndf-next\n/goal 次のセグメント\n```"


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("sleep 600", True),
        ("sleep 5", False),
        ("sleep 1m", True),
        ("sleep 600 &", False),
        ("sleep 30 && ndf plan status", True),
        ("ls; sleep 2; ndf relay status", True),
        ("sleep $X", True),
    ],
)
def test_sleep_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


# 課題 #3851 の再現手順 1 のコマンド（本体に前景の sleep を持つプロセス名の待ち）
_REPRO = 'until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do sleep 20; done'
_PROCESS_WAIT = 'until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do :; done'


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        # ループの本体の sleep は秒によらず止める（入れ子・until を含む）
        ("while true; do sleep 1; done", True),
        ("until [ -s out ]; do sleep 5; done", True),
        ("while :; do while :; do sleep 1; done; done", True),
        ("while :; do for i in 1 2; do sleep 1; done; done", True),
        # ループの外と for の中は上限（hook.sleep の 30 秒）で判定する
        ("sleep 30", False),
        ("sleep 31", True),
        ("for i in 1 2; do sleep 3; done", False),
        ("for i in 1 2; do sleep 600; done", True),
        # bash -c 系・eval の中身とラッパーのコマンドの後ろ
        ("bash -c 'while true; do sleep 1; done'", True),
        ("sh -c 'sleep 600'", True),
        ("zsh -c 'until false; do sleep 2; done'", True),
        ("dash -c 'sleep 5'", False),
        ("eval 'while :; do sleep 1; done'", True),
        ("timeout 590 sleep 600", True),
        ("timeout --signal TERM 590 sleep 600", True),
        ("nohup sleep 600", True),
        ("env X=1 nice -n 10 sleep 600", True),
        ("time sleep 600", True),
        ("exec sleep 600", True),
        ("command sleep 600", True),
        ("while :; do timeout 5 sleep 1; done", True),
        ("timeout 590 sleep 5", False),
        # 背景になる形は止めない（外側の & を中身が引き継ぐ）
        ("while true; do sleep 1; done &", False),
        ("bash -c 'while :; do sleep 1; done' &", False),
        ("nohup sleep 600 &", False),
        ("(sleep 600) &", False),
        ("{ sleep 600; } &", False),
        # 引数の中の語・読めないラッパーの引数は見ない
        ("echo bash -c 'sleep 600'", False),
        ("timeout --bogus 590 sleep 600", False),
        # ラッパーを外した後の名前はパスを外して読む（ラッパーだけで中身が無い形は見ない）
        ("/bin/sleep 600", True),
        ("timeout 590 /usr/bin/sleep 600", True),
        ("while :; do /bin/sleep 1; done", True),
        ("/bin/bash -c 'sleep 600'", True),
        ("/usr/bin/env X=1 /bin/sh -c 'while :; do sleep 1; done'", True),
        ("while :; do nohup timeout 5; done", False),
        ("/bin/sleeper 600", False),
    ],
)
def test_sleep_guard_reads_loops_inner_scripts_and_wrappers(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


def test_sleep_in_a_loop_names_the_alternative_waits(tmp_path):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "while true; do sleep 1; done")))
    assert "Monitor" in reason and "run_in_background" in reason


def test_sleep_in_a_loop_passes_in_a_background_call(tmp_path):
    payload = bash(tmp_path, "while true; do sleep 1; done", run_in_background=True)
    assert call_entry("claude", "PreToolUse", payload) is None


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        (_PROCESS_WAIT, True),
        ("while pkill --full x; do :; done", True),
        ("while pgrep -af x >/dev/null; do :; done", True),
        ("until ! pgrep -u root -f x; do :; done", True),
        ("while :; do pgrep -f x || break; done", True),
        ("bash -c 'until ! pgrep -f x; do :; done'", True),
        ("while timeout 5 pgrep -f x; do :; done", True),
        # ループの外の単発・-f の無い形・-f がオプションでない形は止めない
        ("pgrep -f x", False),
        ("pgrep -x dockerd >/dev/null 2>&1 || echo x", False),
        ("until ! pgrep -x dockerd; do sleep 1; done &", False),
        ("until ! pgrep -- -f; do :; done", False),
        ("until ! pgrep -d f x; do :; done", False),
        # ラッパーを外した後の名前はパスを外して読む（ラッパーだけで中身が無い形は見ない）
        ("while /usr/bin/pgrep -f x; do :; done", True),
        ("while /usr/bin/timeout 5 /usr/bin/pkill -f x; do :; done", True),
        ("/bin/bash -c 'until ! /usr/bin/pgrep -f x; do :; done'", True),
        ("while timeout 5; do :; done", False),
        ("while command -v pgrep; do :; done", False),
    ],
)
def test_process_wait_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


@pytest.mark.parametrize("command", [_REPRO, _PROCESS_WAIT, "until ! pgrep -f x; do :; done &"])
def test_process_wait_is_denied_in_a_background_call_with_the_file_wait(tmp_path, command):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command, run_in_background=True)))
    assert reason is not None and "完了マーカー" in reason and "Monitor" in reason


def test_process_wait_repro_is_denied_in_the_foreground(tmp_path):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)))
    assert reason is not None and "完了マーカー" in reason


def test_process_wait_is_turned_off_by_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_HOOK_OFF", "process-wait")
    assert call_entry("claude", "PreToolUse", bash(tmp_path, _PROCESS_WAIT)) is None
    assert all(g["kind"] != "deny" for g in records()[-1]["guards"])
    # 本体の前景の sleep は別の guard（sleep）が止め、2 つとも外すと通る
    assert "完了マーカー" not in deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)))
    monkeypatch.setenv("NDF_HOOK_OFF", "process-wait,sleep")
    assert call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)) is None


@pytest.mark.parametrize(
    ("command", "full"),
    [
        ("pgrep -f x", True),
        ("pgrep -af x", True),
        ("pgrep x -f", True),
        ("pgrep -u root -f x", True),
        ("pkill -9 -f x", True),
        ("pkill -TERM -f x", True),
        ("pkill --full x", True),
        ("pgrep -- -f", False),
        ("pgrep -d f x", False),
        ("pgrep -df x", False),
        ("pgrep --delimiter -f x", False),
        ("pgrep -x f", False),
    ],
)
def test_full_match_reads_options_in_getopt_order(command, full):
    name, *args = command.split()
    assert process_wait._full_match(name, tuple(args)) is full


@pytest.mark.parametrize("command", ["if [ ; then", "env -S '\"sleep 600'"])
def test_shell_text_that_cannot_be_read_is_not_denied(tmp_path, command):
    assert call_entry("claude", "PreToolUse", bash(tmp_path, command)) is None


def test_sleep_before_ndf_is_denied_even_in_a_background_call(tmp_path):
    payload = bash(tmp_path, "sleep 60; ndf plan status", run_in_background=True)
    assert "NDF のコマンド" in deny_reason(call_entry("claude", "PreToolUse", payload))
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 600", run_in_background=True)) is None


def test_sleep_seconds():
    assert sleep.seconds(("1", "2s", "1m")) == 63 and sleep.seconds(("0.5",)) == 0.5 and sleep.seconds(("x",)) == float("inf")


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("ndf plan status && ndf plan wait p1", True),
        ("until ndf plan done p1; do echo .; done", True),
        ("ndf plan wait p1", False),
        ("ls && git status", False),
        ("/opt/x/bin/ndf ci wait 1 | ndf relay status", True),
    ],
)
def test_script_chain_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


def test_context_limit_denies_with_one_next_step_outside_the_relay(tmp_path):
    usage_transcript(tmp_path, 900000)
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    assert reason is not None and ".ndf/handoff/" in reason
    assert records()[-1]["notes"]["under_relay"] is False
    handoff = sample(
        "claude", "PreToolUse", tmp_path, tool_name="Write", tool_input={"file_path": str(tmp_path / ".ndf/handoff/a.md"), "content": "x"}
    )
    assert call_entry("claude", "PreToolUse", handoff) is None


def test_context_limit_under_the_relay_asks_for_the_next_section(tmp_path, monkeypatch):
    # 文脈の量が上限を超えたオーガナイザー（中継の下）に、セグメントの切り替えを求める（#2595）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    usage_transcript(tmp_path, 900000)
    assert "次のセグメントのブロック" in deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    assert records()[-1]["notes"]["under_relay"] is True


def test_context_limit_does_not_record_relay_when_it_did_not_judge(tmp_path):
    # 判定しなかった行に under_relay を書かない（#2789）
    usage_transcript(tmp_path, 10)
    call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    assert "under_relay" not in records()[-1]["notes"] and records()[-1]["notes"]["context_tokens"] == 10


@pytest.mark.parametrize(
    ("tokens", "env", "denied"),
    [
        (249_999, None, False),
        (250_000, None, False),
        (499_999, None, False),
        (500_000, None, True),
        (299_999, "300000", False),
        (300_000, "300000", True),
    ],
)
def test_context_limit_stops_at_the_operating_limit(tmp_path, monkeypatch, tokens, env, denied):
    # #3995 の 3995:4・3995:9: PreToolUse は運用上限（500,000。NDF_CONTEXT_LIMIT で替える）から止め、判断開始の値（250,000）では止めない
    if env is not None:
        monkeypatch.setenv("NDF_CONTEXT_LIMIT", env)
    usage_transcript(tmp_path, tokens)
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is not None) is denied


def test_context_limit_reads_the_limit_from_the_limits_table(tmp_path, monkeypatch):
    # #3995 の 3995:1: 上限の設定の 2 つの値を差し替えると、guard は差し替えた運用上限で止める（コードに値を持たない）
    real = tables.policy_value
    swapped = {"context.decide-from": 100_000, "context.limit": 200_000}
    monkeypatch.setattr(tables, "policy_value", lambda name, key: swapped[name] if name in swapped else real(name, key))
    usage_transcript(tmp_path, 199_999)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is None
    usage_transcript(tmp_path, 200_000)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is not None


def _separate(main, meta):
    """管理ディレクトリ `meta` を外に置いたメインディレクトリ（`git init --separate-git-dir` の .git のファイル）。"""
    main.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True)
    (main / ".git").write_text(f"gitdir: {meta}\n", encoding="utf-8")


def _linked(common, worktree):
    """共通の git のディレクトリ `common` に結んだ linked worktree（`git worktree add` が書く .git と commondir）。"""
    admin = common / "worktrees" / worktree.name
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")


def test_context_limit_skips_a_subagent(tmp_path):
    # #3995 の 3995:15: 入力に agent_id があれば（サブエージェント）運用上限を超えても判定しない
    usage_transcript(tmp_path, 600_000)
    payload = {**bash(tmp_path, "ls"), "agent_id": "a0123", "agent_type": "ndf:worker"}
    assert call_entry("claude", "PreToolUse", payload) is None


def test_context_limit_judges_with_only_the_agent_type(tmp_path):
    # #3995 の 3995:15: agent_type だけ（--agent で起動した主の会話）なら判定する
    usage_transcript(tmp_path, 600_000)
    payload = {**bash(tmp_path, "ls"), "agent_type": "ndf:supervisor"}
    assert deny_reason(call_entry("claude", "PreToolUse", payload)) is not None


def test_context_limit_passes_without_usage(tmp_path):
    ndf_repo(tmp_path)
    (tmp_path / "transcript.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n", encoding="utf-8")
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "ls")) is None


@pytest.mark.parametrize("repo", ["outside", "no-ndf"])
def test_context_limit_skips_conversations_outside_ndf_without_reading(tmp_path, counted_tail, repo):
    # #3995 の 3995:15: Git の管理の外と .ndf の無いリポジトリでは、運用上限を超えても判定せず会話の記録を読まない
    cwd = tmp_path / "work"
    cwd.mkdir()
    if repo == "no-ndf":
        (cwd / ".git").mkdir()
    line = {"type": "assistant", "message": {"usage": {"input_tokens": 600_000}}}
    (cwd / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert call_entry("claude", "PreToolUse", bash(cwd, "ls")) is None
    assert counted_tail["n"] == 0


def test_context_limit_judges_a_worktree_by_the_main_directory(tmp_path):
    # #3995 の 3995:15: worktree に .ndf が無くても、メインディレクトリに .ndf があれば判定する
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / ".ndf").mkdir()
    worktree = tmp_path / "wt"
    _linked(main / ".git", worktree)
    assert not (worktree / ".ndf").exists()
    line = {"type": "assistant", "message": {"usage": {"input_tokens": 600_000}}}
    (worktree / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert deny_reason(call_entry("claude", "PreToolUse", bash(worktree, "ls"))) is not None


def test_main_finds_the_main_directory_from_the_main_and_its_worktrees(tmp_path):
    main = tmp_path / "main"
    (main / "src").mkdir(parents=True)
    (main / ".git").mkdir()
    (main / ".ndf").mkdir()
    worktree = tmp_path / "wt"
    _linked(main / ".git", worktree)
    (worktree / "src").mkdir()
    want = os.path.realpath(main)
    for cwd in (main, main / "src", worktree, worktree / "src"):
        assert context_limit._main(str(cwd)) == want


def test_main_of_a_separate_git_dir_is_not_the_parent_of_the_git_dir(tmp_path):
    # 管理ディレクトリを外に置いたメインディレクトリは、管理ディレクトリの親でなくメインディレクトリそのもの
    main = tmp_path / "main"
    meta = tmp_path / "outside" / "meta.git"
    _separate(main, meta)
    (main / "src").mkdir()
    (main / ".ndf").mkdir()
    want = os.path.realpath(main)
    assert context_limit._main(str(main)) == want
    assert context_limit._main(str(main / "src")) == want


def test_main_skips_a_worktree_of_a_separate_git_dir(tmp_path):
    # 管理ディレクトリを外に置いたリポジトリの linked worktree は主 worktree を特定できないので、親の .ndf を検査しない
    main = tmp_path / "main"
    meta = tmp_path / "outside" / "meta.git"
    _separate(main, meta)
    (main / ".ndf").mkdir()
    (meta.parent / ".ndf").mkdir()
    worktree = tmp_path / "wt"
    _linked(meta, worktree)
    assert context_limit._main(str(main)) == os.path.realpath(main)
    assert context_limit._main(str(worktree)) is None


def test_main_is_none_when_ndf_is_a_file(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".ndf").write_text("x", encoding="utf-8")
    assert context_limit._main(str(tmp_path)) is None


@pytest.mark.parametrize("dot_git", [None, "dir", "not-gitdir"])
def test_main_is_none_outside_an_ndf_repository(tmp_path, dot_git):
    # Git の管理の外・.ndf の無いリポジトリ・gitdir: で始まらない .git のファイル
    if dot_git == "dir":
        (tmp_path / ".git").mkdir()
    elif dot_git == "not-gitdir":
        (tmp_path / ".git").write_text("x\n", encoding="utf-8")
        (tmp_path / ".ndf").mkdir()
    assert context_limit._main(str(tmp_path)) is None


def _limit_params():
    return next(g for g in tables.guards() if g["name"] == "context-limit")["params"]


def _passes(cwd, tool, **tool_input):
    payload = sample("claude", "PreToolUse", cwd, tool_name=tool, tool_input=tool_input)
    return context_limit._passes(context.HookContext("claude", "PreToolUse", payload), _limit_params())


@pytest.fixture
def handoff_repos(tmp_path):
    """メインディレクトリ（.ndf/handoff と src）・その linked worktree・別のリポジトリと、それぞれを指すリンク。"""
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / ".ndf" / "handoff").mkdir(parents=True)
    (main / "src").mkdir()
    wt = tmp_path / "wt"
    _linked(main / ".git", wt)
    (wt / ".ndf" / "handoff").mkdir(parents=True)
    (wt / "src").mkdir()
    other = tmp_path / "other"
    ndf_repo(other)
    (tmp_path / "link").symlink_to(main / ".ndf" / "handoff")
    (tmp_path / "sp ace").symlink_to(main / ".ndf" / "handoff")
    (tmp_path / "main-src").symlink_to(main / "src")
    (tmp_path / "wt-src").symlink_to(wt / "src")
    return {"main": main, "wt": wt, "other": other, "tmp": tmp_path}


def test_passes_reads_and_writes_only_the_main_handoff_dir(handoff_repos):
    # #3995 の Task 8: 運用上限でも通すのは、メインディレクトリの .ndf/handoff の直下の Read・Write・Edit だけ
    main, wt, other, tmp = (handoff_repos[k] for k in ("main", "wt", "other", "tmp"))
    for tool in ("Read", "Write", "Edit"):
        assert _passes(main, tool, file_path=".ndf/handoff/x.md")
        assert _passes(main, tool, file_path=str(main / ".ndf/handoff/x.md"))
    assert _passes(wt, "Write", file_path=str(main / ".ndf/handoff/x.md"))
    assert _passes(main, "Write", file_path=str(tmp / "link" / "x.md"))
    for path in (".ndf/handoff/../settings.json", "other/handoff/x.md", ".ndf/handoff/sub/x.md", str(other / ".ndf/handoff/x.md")):
        assert not _passes(main, "Write", file_path=path)
    assert not _passes(wt, "Write", file_path=".ndf/handoff/x.md")
    assert _passes(main, "TaskStop", task_id="x")
    assert not _passes(main, "Glob", pattern="*")


def test_passes_follows_links_out_of_the_handoff_dir(tmp_path):
    # #3995 の Task 8: 文書・handoff・.ndf がリポジトリの外を指すリンクなら、実際の先が外なので止める
    outside = tmp_path / "outside"
    (outside / "handoff").mkdir(parents=True)
    (outside / "f.md").write_text("x", encoding="utf-8")
    doc, handoff, ndf = (tmp_path / name for name in ("doc", "handoff", "ndf"))
    for repo in (doc, handoff, ndf):
        (repo / ".git").mkdir(parents=True)
    (doc / ".ndf" / "handoff").mkdir(parents=True)
    (doc / ".ndf" / "handoff" / "x.md").symlink_to(outside / "f.md")
    (handoff / ".ndf").mkdir()
    (handoff / ".ndf" / "handoff").symlink_to(outside / "handoff")
    (ndf / ".ndf").symlink_to(outside)
    for repo in (doc, handoff, ndf):
        for tool in ("Read", "Write", "Edit"):
            assert not _passes(repo, tool, file_path=".ndf/handoff/x.md")


def test_passes_writes_from_the_main_of_a_separate_git_dir(tmp_path):
    # #3995 の Task 8: 管理ディレクトリを外に置いたメインディレクトリは自分の .ndf/handoff を書け、管理ディレクトリの親のものは書けない
    main = tmp_path / "main"
    meta = tmp_path / "outside" / "meta.git"
    _separate(main, meta)
    (main / ".ndf" / "handoff").mkdir(parents=True)
    (meta.parent / ".ndf" / "handoff").mkdir(parents=True)
    assert _passes(main, "Write", file_path=".ndf/handoff/x.md")
    assert not _passes(main, "Write", file_path=str(meta.parent / ".ndf/handoff/x.md"))


_CHECK = "ndf records handoff check .ndf/handoff/x.md"


@pytest.mark.parametrize(
    ("at", "command", "passes"),
    [
        ("main", "ndf records --cwd {main} handoff check {main}/.ndf/handoff/next.md --trim --history {main}/.ndf/handoff/n-h.md", True),
        ("wt", "ndf records --cwd {main} handoff check {main}/.ndf/handoff/next.md --trim --history {main}/.ndf/handoff/n-h.md", True),
        ("wt", "ndf records --cwd {main} handoff check .ndf/handoff/next.md --trim --history .ndf/handoff/next-history.md", True),
        ("main/src", _CHECK, True),
        ("main", "ndf relay context record --event 引継ぎ開始 --work 3995", True),
        ("main", f"{_CHECK} 2>&1", True),
        ("main", f"{_CHECK} 2>/dev/null", True),
        ("main", f"ndf relay context record --event 引継ぎ開始 --work 3995 && {_CHECK}", True),
        ("main", f"ndf relay context record --event 引継ぎ開始 --work 3995; {_CHECK}", True),
        ("main", f"ndf relay context record --event 引継ぎ開始 --work 3995 | {_CHECK}", True),
        ("main", "ndf records --cwd {tmp}/main-src handoff check .ndf/handoff/next.md", True),
        ("main", "ndf records handoff check {tmp}/sp\\ ace/x.md", True),
        ("main", "ndf records handoff check '{tmp}/sp ace/x.md' --trim --history=\"{tmp}/sp ace/h.md\"", True),
        ("main", f"{_CHECK} --trim --history='.ndf/handoff/h.md'", True),
        ("main", "ndf records handoff check .ndf/hand\\off/x.md", True),
        ("main", "'ndf' records handoff check .ndf/handoff/x.md", True),
        ("main", "ndf records handoff check '.ndf/handoff/../x.md'", False),
        ("main", "ndf records handoff check .ndf/handoff/x.md\\\n.md", False),
        ("main", "ndf records handoff check \\\n  .ndf/handoff/x.md \\\n  --trim --history .ndf/handoff/h.md", True),
        ("main", 'ndf records handoff check ".ndf/handoff/x\\\n.md"', False),
        ("main", "ndf records handoff check\t\\\n.ndf/handoff/x.md", True),
        ("main", "ndf records handoff check .ndf/handoff/x.md\\ \\\n.md", False),
        ("main", "ndf records handoff check .ndf/handoff/x.md\u00a0\\\n.md", False),
        ("main", "ndf records handoff check '.ndf/handoff/x.md'\\\n--trim --history .ndf/handoff/h.md", False),
        ("main", "ndf records handoff check '.ndf/handoff/x.md '\\\n.md", False),
        ("main", f"{_CHECK};\\\n{_CHECK}", False),
        ("main", f"{_CHECK} --trim --history /home/u/notes.md", False),
        ("main", f"{_CHECK} --trim --history .ndf/handoff/../x.md", False),
        ("main", _CHECK + " --trim --history {wt}/.ndf/handoff/h.md", False),
        ("main", "ndf records handoff check README.md", False),
        ("main", "ndf records --cwd {other} handoff check .ndf/handoff/next.md", False),
        ("main", "ndf records --cwd {tmp}/wt-src handoff check .ndf/handoff/next.md", False),
        ("wt", "ndf records handoff check .ndf/handoff/next.md", False),
        ("main", "env -C {wt} ndf records handoff check .ndf/handoff/next.md --trim --history .ndf/handoff/next-history.md", False),
        ("main", "env GIT_DIR={wt}/.git " + _CHECK, False),
        ("main", "GIT_WORK_TREE={wt} " + _CHECK, False),
        ("main", f"GIT_DIR=x; {_CHECK}", False),
        ("main", f"timeout 60 {_CHECK}", False),
        ("main", "cd {wt} && " + _CHECK, False),
        ("main", f'{_CHECK} --trim --history "$H"', False),
        ("main", "ndf records handoff check ~/x.md", False),
        ("main", "ndf records handoff check *.md", False),
        ("main", f"{_CHECK} --force", False),
        ("main", f"{_CHECK} .ndf/handoff/y.md", False),
        ("main", "ndf records handoff other .ndf/handoff/x.md", False),
        ("main", "ndf relay context record --work 1 --out f", False),
        ("main", f"{_CHECK} && rm y", False),
        ("main", "ndf lanes status p1", False),
        ("main", f"{_CHECK} > settings.json", False),
        ("main", f"{_CHECK} >> f", False),
        ("main", f"{_CHECK} >| f", False),
        ("main", f"{_CHECK} &> f", False),
        ("main", f"{_CHECK} 1<>f", False),
        ("main", f"{_CHECK} < f", False),
        ("main", f"{_CHECK} > >(cat)", False),
        ("main", f"{_CHECK} >&out", False),
        ("main", f'{_CHECK} > "$F"', False),
        ("main", f"{_CHECK} <<'E'\na\nE\n", False),
        ("main", f"{_CHECK} &", False),
        ("main", f"{_CHECK} $(ndf relay context record --event 引継ぎ開始 --work 1)", False),
    ],
)
def test_passes_runs_only_the_handoff_commands(handoff_repos, at, command, passes):
    # #3995 の Task 8・3995:6: Bash は引継ぎの 2 つのコマンドだけ（許す引数・許すリダイレクト・許可対象だけの連結）を通す
    words = {k: os.path.realpath(v) for k, v in handoff_repos.items()}
    cwd = handoff_repos["main"] / "src" if at == "main/src" else handoff_repos[at]
    assert _passes(cwd, "Bash", command=command.format(**words)) is passes


def test_passes_expands_a_quoted_tilde_like_the_cli(handoff_repos, monkeypatch):
    # #3995: 引用したチルダは CLI が Path.expanduser で展開するので、展開した先で照らす（メインの ~ というリンクに惑わされない）
    main = handoff_repos["main"]
    (main / "~").symlink_to(main / ".ndf" / "handoff")
    monkeypatch.setenv("HOME", str(handoff_repos["tmp"]))
    for command in ("ndf records handoff check '~/x.md'", f"{_CHECK} --trim --history '~/h.md'"):
        assert not _passes(main, "Bash", command=command)
    monkeypatch.setenv("HOME", str(main / ".ndf" / "handoff"))
    assert _passes(main, "Bash", command="ndf records handoff check '~/x.md' --trim --history=\"~/h.md\"")


def test_handoff_bash_judges_once_per_tool_call(tmp_path, monkeypatch):
    # #3995: 運用上限で通す連結は context-limit と script-chain の両方が見るが、判定は同じ呼び出しの中で 1 回だけ
    usage_transcript(tmp_path, 500_000)
    (tmp_path / ".ndf" / "handoff").mkdir()
    calls = []
    real = context_limit._handoff_bash
    monkeypatch.setattr(context_limit, "_handoff_bash", lambda ctx, params: calls.append(1) or real(ctx, params))
    command = f"ndf relay context record --work 1 && {_CHECK}"
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is None
    assert len(calls) == 1


def test_passes_does_not_run_the_handoff_command_in_the_background(handoff_repos):
    # #3995: Tool の背景起動（run_in_background）は、コマンドの字面に & が無くても止める
    assert not _passes(handoff_repos["main"], "Bash", command=_CHECK, run_in_background=True)
    assert _passes(handoff_repos["main"], "Bash", command=_CHECK, run_in_background=False)


def test_ndf_args_pass_matches_where_the_cli_writes(tmp_path, monkeypatch, capsys):
    # #3995 の Task 8: 真になった引数を実際に打つと、更新されるのはいつもメインディレクトリの .ndf/handoff の文書で、偽の組は worktree を更新する
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    main, wt = tmp_path / "main", tmp_path / "wt"
    git_sandbox.sh(tmp_path, "init", "-q", str(main))
    git_sandbox.sh(main, "commit", "-q", "--allow-empty", "-m", "x", identity=True)
    git_sandbox.sh(main, "worktree", "add", "-q", "-b", "w", str(wt))
    for root in (main, wt):
        (root / ".ndf" / "handoff").mkdir(parents=True)
        (root / "src").mkdir()
    (tmp_path / "main-src").symlink_to(main / "src")
    (tmp_path / "wt-src").symlink_to(wt / "src")
    body = "## 現在地\n\n- x\n\n## 雑記\n\n- 移す\n"
    # CLI が実際に使った根（_root の戻り値）を捕まえ、git rev-parse を 1 ケース 1 回に保つ
    roots: list[Path] = []
    real_root = records_cli._root
    monkeypatch.setattr(records_cli, "_root", lambda args: roots.append(real_root(args)) or roots[-1])
    cases = [
        (main, str(main), True),
        (wt, str(main), True),
        (main / "src", None, True),
        (main, str(tmp_path / "main-src"), True),
        (wt, None, False),
        (main, str(tmp_path / "wt-src"), False),
    ]
    for cwd, at, want in cases:
        for root in (main, wt):
            (root / ".ndf/handoff/next.md").write_text(body, encoding="utf-8")
            (root / ".ndf/handoff/next-history.md").unlink(missing_ok=True)
        opts = ["--cwd", at] if at is not None else []
        words = ["records", *opts, "handoff", "check", ".ndf/handoff/next.md", "--trim", "--history", ".ndf/handoff/next-history.md"]
        assert context_limit._ndf_args_pass(words, str(cwd), _limit_params()) is want
        monkeypatch.chdir(cwd)
        start = Path(os.path.realpath(at if at is not None else cwd))
        roots.clear()
        cli.main(words)
        capsys.readouterr()
        assert roots == [strict._repo_root(start)]
        updated = [root for root in (main, wt) if (root / ".ndf/handoff/next-history.md").exists()]
        assert updated == [main if want else wt]


def test_context_limit_passes_the_handoff_calls_at_the_operating_limit(tmp_path):
    # #3995 の 3995:6: 運用上限で、引継ぎ文書の Read・TaskStop・引継ぎ文書の確かめは通り、ほかの呼び出しは止まる
    usage_transcript(tmp_path, 500_000)
    (tmp_path / ".ndf" / "handoff").mkdir()
    read = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": ".ndf/handoff/next.md"})
    stop_task = sample("claude", "PreToolUse", tmp_path, tool_name="TaskStop", tool_input={"task_id": "x"})
    check = "ndf records handoff check .ndf/handoff/next.md"
    record = "ndf relay context record --event 引継ぎ開始 --work 3995"
    chains = [f"{record} {op} {check}" for op in ("&&", ";", "|")]
    for payload in (read, stop_task, *(bash(tmp_path, c) for c in (check, *chains))):
        assert deny_reason(call_entry("claude", "PreToolUse", payload)) is None
    for payload in (bash(tmp_path, "ndf lanes status p1"), bash(tmp_path, check, run_in_background=True)):
        assert deny_reason(call_entry("claude", "PreToolUse", payload)) is not None


def test_script_chain_still_stops_other_ndf_chains_below_the_limit(tmp_path):
    # #3995: 引継ぎのコマンドだけの連結は運用上限の下でも通り、ほかの NDF のコマンドを混ぜた連結は script-chain が止める
    usage_transcript(tmp_path, 1)
    (tmp_path / ".ndf" / "handoff").mkdir()
    check = "ndf records handoff check .ndf/handoff/next.md"
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, f"ndf relay context record --work 1 && {check}"))) is None
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, f"ndf lanes status p1 && {check}"))) is not None


def test_context_limit_keeps_denying_different_calls(tmp_path):
    # #3995 の 3995:6・決定 6: 運用上限の拒否は数えず、同じ入力の中で違う呼び出しを 3 回続けても 3 回とも止める
    usage_transcript(tmp_path, 500_000)
    read = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / "README.md")})
    for payload in (bash(tmp_path, "ls"), bash(tmp_path, "pwd"), read):
        assert deny_reason(call_entry("claude", "PreToolUse", payload)) is not None


def test_hook_off_context_limit_neither_judges_nor_notifies(tmp_path, monkeypatch, counted_tail):
    # #3995 の 3995:11・テスト設計の 11: NDF_HOOK_OFF=context-limit なら 600,000 でも判定も知らせもせず、会話の記録を読まない
    monkeypatch.setenv("NDF_HOOK_OFF", "context-limit")
    usage_transcript(tmp_path, 600_000)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "ls")) is None
    assert call_entry("claude", "PostToolUse", sample("claude", "PostToolUse", tmp_path)) is None
    assert counted_tail["n"] == 0


def test_context_limit_reason_tells_how_to_stop_the_watches_and_hand_over():
    # #3995 の 3995:8・テスト設計の 8: 監視を TaskStop で止め、supervisor を待ち、wait の行を残し、引継ぎ文書を書いて終える
    t = thresholds({}, tables.policy_value)
    for under, end in ((True, "次のセグメントのブロック"), (False, "ndf-next")):
        text = context_limit._reason(t, under, "/work/repo", ".ndf/handoff/")
        parts = ("parallel-<計画>", "ndf records --cwd /work/repo handoff check '.ndf/handoff/<文書>.md' ")
        for part in ("TaskStop", "supervisor", "wait の行", *parts, end):
            assert part in text
        assert len(text) <= 600


def test_context_limit_reason_quotes_a_main_directory_with_spaces():
    # #3995: 空白を含むメインディレクトリでも、案内のコマンドを Bash がそのまま打てる（--cwd と文書がそれぞれ 1 引数）
    text = context_limit._reason(thresholds({}, tables.policy_value), False, "/work/my repo", ".ndf/handoff/")
    assert "ndf records --cwd '/work/my repo' handoff check '.ndf/handoff/<文書>.md' " in text


@pytest.mark.parametrize("under", [False, True])
def test_context_limit_reason_fits_the_budget_with_a_long_main_directory(tmp_path, monkeypatch, under):
    # #3995（r3-2）: 156 字のメインディレクトリでも、実際の拒否の文が注入の上限で切られず、検査のコマンドと終え方を全文で伝える
    if under:
        (tmp_path / "relay").mkdir()
        monkeypatch.setenv("NDF_RELAY_DIR", str(tmp_path / "relay"))
    main = tmp_path / "m"
    main = tmp_path / ("m" * (156 - len(os.path.realpath(main)) + 1))
    assert len(os.path.realpath(main)) == 156
    usage_transcript(main, 900000)
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(main, "ls")))
    assert "全文:" not in reason and len(reason) <= 600
    assert f"ndf records --cwd {os.path.realpath(main)} handoff check '.ndf/handoff/<文書>.md' " in reason
    assert ("次のセグメントのブロック" if under else "ndf-next") in reason and "wait の行" in reason


def test_context_limit_reason_comes_alone_when_script_chain_also_denies(tmp_path):
    # #3995（r4-1・r6-1）: 運用上限で script-chain も止める連結でも、引継ぎの手順だけを出し、230 字のメインディレクトリで wait の行まで切らない
    main = tmp_path / ("m" * (230 - len(os.path.realpath(tmp_path / "m")) + 1))
    usage_transcript(main, 900000)
    command = "ndf lanes status p1 && ndf records handoff check .ndf/handoff/next.md"
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(main, command)))
    assert "全文:" not in reason and len(reason) <= 600 and reason.endswith("wait の行を残す。")


def test_context_limit_reason_reaches_the_end_with_a_quoted_main_directory_outside_the_relay(tmp_path):
    # #3995（r7-1）: 中継の外で空白を含む 243 字のメインディレクトリ（引用で 2 字増える）でも、wait の行まで切らない
    main = tmp_path / "a b"
    main = tmp_path / ("a b" + "m" * (243 - len(os.path.realpath(main))))
    assert len(os.path.realpath(main)) == 243
    usage_transcript(main, 900000)
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(main, "ls")))
    assert f"--cwd '{os.path.realpath(main)}' " in reason and "ndf-next" in reason
    assert "全文:" not in reason and len(reason) <= 600 and reason.endswith("wait の行を残す。")


def test_context_limit_reason_does_not_carry_the_context_size(tmp_path):
    # #3995: 拒否の文に文脈の量を入れない（呼び出しごとに同じ文）
    usage_transcript(tmp_path, 500_000)
    first = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    usage_transcript(tmp_path, 600_000)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) == first


def test_ask_writes_signals_without_waiting(tmp_path, monkeypatch):
    # 中継の下では今の起動（launch.json）と同じ NDF_RELAY_LAUNCH の hook だけが書ける（#3814 の I7）
    relay = tmp_path / "relay"
    relay.mkdir()
    signals.set_launch(relay, "L1")
    organizer = tmp_path / "org"
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(organizer))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    questions = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="AskUserQuestion", tool_input={"questions": questions})
    assert call_entry("claude", "PreToolUse", payload) is None
    for path in (relay / signals.QUESTION_FILE, organizer / "questions" / "lflow.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["questions"] == questions
    assert records()[-1]["notes"]["forwarded"] == ["relay", "organizer"]
    assert json.loads((relay / signals.QUESTION_FILE).read_text(encoding="utf-8"))["launch"] == "L1"


QUESTIONS = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]


def _ask_payload(cwd, tool_use_id="toolu_A"):
    return sample("claude", "PreToolUse", cwd, tool_name="AskUserQuestion", tool_input={"questions": QUESTIONS}, tool_use_id=tool_use_id)


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """中継の作業ディレクトリ。今の起動は L1 で、hook の環境も L1。"""
    directory = tmp_path / "relay"
    directory.mkdir()
    signals.set_launch(directory, "L1")
    monkeypatch.setenv("NDF_RELAY_DIR", str(directory))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    return directory


def test_i7_ask_is_denied_when_writing_fails(relay, tmp_path, monkeypatch):
    # 受け入れ条件 10・I7: 書き込みが OSError を出したら拒否する
    def broken(*args, **kwargs):
        raise OSError("書けない")

    monkeypatch.setattr(signals, "write_question", broken)
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED


@pytest.mark.parametrize("launch", ["L1", None], ids=["stopped-launch", "no-launch"])
def test_i7_ask_of_a_stopped_launch_is_denied_and_keeps_the_current_question(relay, tmp_path, monkeypatch, launch):
    # I7: 止めた起動 L1 の hook は、次の起動 L2 の質問を上書きせず拒否する。起動の ID が無い hook も拒否する
    signals.set_launch(relay, "L2")
    q2 = {"tool_use_id": "toolu_Q2", "tool_input": {"questions": QUESTIONS}}
    assert signals.write_question(relay / signals.QUESTION_FILE, "s2", q2, 0.3, launch="L2")
    if launch is None:
        monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED
    assert signals.read_question(relay)["tool_use_id"] == "toolu_Q2"


def test_ask_under_a_wrapper_from_before_the_launch_id_is_written_without_it(relay, tmp_path, monkeypatch):
    # 更新前のラッパー（NDF_RELAY_LAUNCH を渡さず launch.json を書かない）の下で、更新後の hook が質問を出せる
    (relay / signals.LAUNCH_FILE).unlink()
    monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    written = signals.read_question(relay)
    assert written["tool_use_id"] == "toolu_A" and "launch" not in written


def test_i7_ask_is_denied_when_the_deadline_passes_before_the_guards(relay, tmp_path, monkeypatch):
    # I7: guard を呼ぶ前に締め切りの残りが無くても、中継の下の質問は通さず拒否する
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, deadline=0.0))
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED
    assert records()[-1]["error"] == "deadline" and not signals.question_present(relay)


def test_deadline_outside_the_relay_still_passes_the_question(tmp_path, monkeypatch):
    # 中継の外では締め切りを過ぎても今と同じく通す
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, deadline=0.0))
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    assert records()[-1]["error"] == "deadline"


def test_i7_ask_guard_exception_denies_under_the_relay(relay, tmp_path, monkeypatch):
    # I7: guard の例外は拒否にする（fail_closed）
    def boom(env):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(signals, "relay_launch", boom)
    reason = deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path)))
    assert reason == dispatch.GUARD_FAILED.format(name="ask-forward", error="RuntimeError")


@pytest.mark.parametrize("under_relay", [True, False], ids=["relay", "outside"])
def test_ask_passes_when_the_organizer_and_the_notice_fail(tmp_path, monkeypatch, under_relay):
    # 拒否にするのは中継の質問ファイルの記録の失敗だけ。オーガナイザーと待ちの知らせの OSError は通して計測に残す
    if under_relay:
        relay = tmp_path / "relay"
        relay.mkdir()
        signals.set_launch(relay, "L1")
        monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
        monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(tmp_path / "org"))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    real = signals.write_question

    def organizer_broken(path, *args, **kwargs):
        if "launch" not in kwargs:
            raise OSError("オーガナイザーへ書けない")
        return real(path, *args, **kwargs)

    def enqueue_broken(*args, **kwargs):
        raise OSError("送信待ちへ書けない")

    monkeypatch.setattr(signals, "write_question", organizer_broken)
    monkeypatch.setattr(ask.outbox, "notify_configured", lambda env: True)
    monkeypatch.setattr(ask.outbox, "enqueue", enqueue_broken)
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    notes = records()[-1]["notes"]
    assert notes["forwarded"] == (["relay"] if under_relay else [])
    assert notes["forward_errors"] == {"organizer": "OSError", "notify": "OSError"}


def test_relay_mark_writes_the_next_section_even_when_goal_continues(tmp_path, monkeypatch):
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    # /goal の続き（stop_hook_active が真）でも切り替える（#2438）
    assert call_entry("claude", "Stop", stop(tmp_path, f"セグメントを終えた。\n\n{FENCE}", stop_hook_active=True)) is None
    mark = signals.read_next(relay)
    assert mark["block"] == "/goal 次のセグメント" and mark["goal_active"] is True


def test_relay_mark_pushes_back_while_background_tasks_remain(tmp_path, monkeypatch):
    # バックグラウンドの作業が残る応答は、/goal の続きでも変更を要求する（#1877）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    out = call_entry("claude", "Stop", stop(tmp_path, FENCE, stop_hook_active=True, background_tasks=[{"id": "b1"}]))
    assert "バックグラウンドの作業が 1 件" in out["reason"] and signals.read_next(relay) is None


def test_relay_mark_does_nothing_outside_the_relay(tmp_path):
    assert call_entry("claude", "Stop", stop(tmp_path, FENCE)) is None


def test_reporting_another_sessions_approval_wait_is_not_pushed_back(tmp_path):
    # 別のセッションの承認待ちを報告しただけの応答を変更を要求しない（#2324）
    assert call_entry("claude", "Stop", stop(tmp_path, "レーン lflow が設計の承認を待っている。こちらの作業は終えた")) is None
    assert records()[-1]["notes"]["reply"] == "elsewhere_wait"


def test_session_start_and_prompt_do_not_inject_when_nothing_is_wrong(tmp_path):
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    assert advise_text(call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path))) is None
