"""待ちの hook と文脈量の hook（#829 / #830）。

`token-guard.sh` は Claude Code の PreToolUse で動き、3 つを判定する。

- 前景の `sleep` で待つ Bash（ループの本体にあるか、秒数が上限を超える）
- 変わらないファイルの同じ範囲を続けて読み直す Read
- 文脈が上限を超えた conductor が工程へ入る起動（工程 Skill・持ち場の supervisor）

**判定が失敗してもツールを止めない。** 拒否は `permissionDecision: deny` で返し、終了コードは
常に 0 にする。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "token-guard.sh"
STAGES = ROOT / "scripts" / "lib" / "token-guard-stages.txt"
WF_DOCS = ROOT / "skills" / "development-workflow"
WAITING = WF_DOCS / "references" / "waiting.md"
LAYERS = WF_DOCS / "references" / "agent-layers.md"
CONTEXT = WF_DOCS / "references" / "context-window.md"
WF_SKILL = WF_DOCS / "SKILL.md"
README = ROOT / "README.md"
HOOKS = ROOT / "hooks" / "claude.json"
MANIFESTS = ROOT / "manifests"

STAGE_SKILLS = {
    "implementation-plan", "document-drafting",
    "cross-refactoring", "cross-review", "pr-review", "quality-gates",
    "plan-to-spec", "merged",
    "layout-review", "release-verification", "retrospective",
    "development-workflow", "issue-plan-strategy",
}


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """状態の置き場所をテストごとに分ける。"""
    base = tmp_path / "plugin-data"
    return base


def run(payload, state_dir, env=None, raw=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    e.pop("CLAUDE_PLUGIN_DATA", None)
    e["CLAUDE_PLUGIN_DATA"] = str(state_dir)
    if env:
        e.update(env)
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run(["bash", str(SCRIPT)], input=data, capture_output=True,
                          text=True, env=e, timeout=20)


def denied(proc):
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    out = json.loads(proc.stdout)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    return spec["permissionDecisionReason"]


def bash(cmd, **extra):
    ti = {"command": cmd}
    ti.update(extra)
    return {"tool_name": "Bash", "tool_input": ti, "session_id": "s1"}


# ---------------------------------------------------------------- sleep（AC5 / AC6）

DENY_SLEEP = [
    "sleep 30 && tail -5 x.log",
    "while ! test -s x; do sleep 5; done",
    "until [ -s f ]; do sleep 1; done",
    "bash -c 'sleep 30'",
    'timeout 590 bash -c "until [ -s f ]; do sleep 5; done"',
    "sh -c 'until test -s x; do sleep 1; done'",
    "zsh -c 'sleep 30'",
    "zsh -c 'until [ -s f ]; do sleep 5; done'",
    "dash -c 'sleep 30'",
    "dash -c 'until [ -s f ]; do sleep 5; done'",
    "for i in 1 2; do sleep 10; done",
    "select x in a b; do sleep 10; done",
    "while a; do while b; do sleep 1; done; done",
    "while a; do\n  for i in 1 2; do sleep 1; done\ndone",
    'eval "sleep 30"',
    "echo start\nsleep 60\necho end",
    "sleep 1m",
    "X=1 sleep 30",
    "bash -O extglob -c 'sleep 30'",
    "until [ -s f ]; do sleep $X; done",
    "until [ -s f ]; do sleep $(cat n); done",
]

ALLOW_SLEEP = [
    "while read l; do echo \"$l\"; done < f; sleep 1",
    "python3 -m http.server & sleep 2",
    "for p in 1 2; do gh api x; sleep 1; done",
    "for i in 1 2; do sleep 3; done",
    "select x in a b; do sleep 3; done",
    "echo sleep 30",
    'git commit -m "sleep 60"',
    "# sleep 30",
    "ls # sleep 30",
    'echo "while x; do sleep 9; done"',
    "cat <<'EOF'\nsleep 60\nwhile true; do sleep 1; done\nEOF",
    "cat <<-EOF > f\n\tsleep 60\n\tEOF\necho ok",
    "sleep 5",
    "sleep $X",
    "ls -la",
    "while read l; do echo $l; done < f",
    "sleep 30 & echo done",
    "echo X=1 sleep 30",
]


@pytest.mark.parametrize("cmd", DENY_SLEEP)
def test_sleep_denied(cmd, state):
    reason = denied(run(bash(cmd), state))
    assert reason, cmd
    assert "run_in_background" in reason
    assert "waiting.md" in reason
    assert "Monitor" in reason


@pytest.mark.parametrize("cmd", ALLOW_SLEEP)
def test_sleep_allowed(cmd, state):
    assert denied(run(bash(cmd), state)) is None, cmd


def test_sleep_in_here_string_is_allowed(state):
    # 現状固定: here-string の内容はヒアドキュメント本文ではなく、sleep 判定の対象外になる。
    assert denied(run(bash('grep x <<< "sleep 60"'), state)) is None


@pytest.mark.parametrize("cmd", ["sleep 0.1h", "sleep 1d"])
def test_sleep_denied_on_hour_and_day_units(cmd, state):
    # 現状固定: DENY_SLEEP は 'sleep 1m'（分）だけを固定していたが、時間・日の単位と
    # 小数の秒換算の経路は固定されていなかった。既定の上限 5 秒に対し 'sleep 0.1h' は
    # 0.1*3600=360 秒、'sleep 1d' は 86400 秒へ換算され、いずれも拒否される（deny）。
    # 拒否理由は waiting.md への部分一致だけで確かめ、文言全体には結合しない。
    reason = denied(run(bash(cmd), state))
    assert reason, cmd
    assert "waiting.md" in reason


def test_background_bash_is_allowed(state):
    p = bash("sleep 30 && tail x", run_in_background=True)
    assert denied(run(p, state)) is None


def test_monitor_is_not_judged(state):
    p = {"tool_name": "Monitor", "tool_input": {"command": "while true; do sleep 1; done"}}
    assert denied(run(p, state)) is None


def test_sleep_guard_env(state):
    assert denied(run(bash("sleep 30"), state, {"NDF_SLEEP_GUARD": "0"})) is None
    assert denied(run(bash("sleep 10"), state, {"NDF_SLEEP_MAX_SEC": "30"})) is None
    assert denied(run(bash("sleep 40"), state, {"NDF_SLEEP_MAX_SEC": "30"}))


# ---------------------------------------------------------------- 連続 Read（AC7）

def read(path, session="s1", **extra):
    ti = {"file_path": str(path)}
    ti.update(extra)
    return {"tool_name": "Read", "tool_input": ti, "session_id": session}


def test_repeat_read_denied_on_third(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("")
    assert denied(run(read(f), state)) is None
    assert denied(run(read(f), state)) is None
    reason = denied(run(read(f), state))
    assert reason and "run_in_background" in reason and "waiting.md" in reason
    # 拒否の後も同じなら拒否が続く
    assert denied(run(read(f), state))


def test_repeat_read_denied_on_third_when_file_is_missing(tmp_path, state):
    # 現状固定: 存在しないファイルも file_stat の sentinel 値で同じ状態として数える。
    missing = tmp_path / "missing.txt"
    assert denied(run(read(missing), state)) is None
    assert denied(run(read(missing), state)) is None
    reason = denied(run(read(missing), state))
    assert reason and "3 回" in reason


def test_repeat_read_resets_when_file_changes(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("a")
    run(read(f), state)
    run(read(f), state)
    f.write_text("ab")
    assert denied(run(read(f), state)) is None
    assert denied(run(read(f), state)) is None
    assert denied(run(read(f), state))


def test_repeat_read_resets_on_other_range(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("a\nb\n")
    run(read(f), state)
    run(read(f), state)
    assert denied(run(read(f, offset=2), state)) is None
    assert denied(run(read(f), state)) is None


def test_repeat_read_resets_on_replaced_file(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("aaaa")
    run(read(f), state)
    run(read(f), state)
    g = tmp_path / "new.txt"
    g.write_text("bbbb")
    os.utime(g, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns))
    os.replace(g, f)
    assert denied(run(read(f), state)) is None


def test_repeat_read_recovers_from_broken_state(tmp_path, state):
    # 現状固定: 既存の read 状態 JSON が壊れているとき、読み直し判定は 0 から数え直す。
    # 最初の Read は通り、状態は有効な JSON（count=1）へ置き換わる。以後は同じ範囲を
    # 続けて読むと現在の上限（既定 3）回で拒否される。
    f = tmp_path / "out.txt"
    f.write_text("")
    guards = state / "guards"
    guards.mkdir(parents=True)
    broken = guards / "read-s1.json"
    broken.write_text("{not json")
    assert denied(run(read(f), state)) is None
    saved = json.loads(broken.read_text())
    assert saved["count"] == 1
    assert denied(run(read(f), state)) is None
    reason = denied(run(read(f), state))
    assert reason and "3 回" in reason


def test_repeat_read_is_per_session(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("")
    run(read(f), state)
    run(read(f), state)
    assert denied(run(read(f, session="s2"), state)) is None


def test_repeat_read_env(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("")
    env = {"NDF_READ_REPEAT_LIMIT": "2"}
    assert denied(run(read(f), state, env)) is None
    assert denied(run(read(f), state, env))
    g = tmp_path / "g.txt"
    g.write_text("")
    off = {"NDF_READ_REPEAT_GUARD": "0"}
    for _ in range(4):
        assert denied(run(read(g), state, off)) is None


def test_parallel_reads_do_not_lose_updates(tmp_path, state):
    f = tmp_path / "out.txt"
    f.write_text("")
    env = {"NDF_READ_REPEAT_LIMIT": "10"}
    run(read(f), state, env)
    threads = [threading.Thread(target=run, args=(read(f), state, env)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    saved = json.loads((state / "guards" / "read-s1.json").read_text())
    assert saved["count"] == 3


# ---------------------------------------------------------------- 可用性（AC9）

def test_broken_json_passes(state):
    p = run(None, state, raw="{not json")
    assert p.returncode == 0 and p.stdout == ""


def test_without_jq_passes(tmp_path, state):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "cat", "tail", "stat", "mkdir", "date", "mv", "rm", "find", "python3"):
        src = shutil.which(tool)
        if src:
            (bindir / tool).symlink_to(src)
    p = run(bash("sleep 30"), state, {"PATH": str(bindir)})
    assert p.returncode == 0 and p.stdout == ""


def test_unwritable_state_skips_read_but_keeps_sleep(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        env = {"CLAUDE_PLUGIN_DATA": "", "XDG_STATE_HOME": str(ro), "HOME": str(ro),
               "TMPDIR": str(ro)}
        f = tmp_path / "x.txt"
        f.write_text("")
        for _ in range(4):
            assert denied(run(read(f), ro / "none", env)) is None
        assert denied(run(bash("sleep 30"), ro / "none", env))
    finally:
        ro.chmod(0o700)


def test_lock_held_passes(tmp_path, state):
    f = tmp_path / "x.txt"
    f.write_text("")
    guards = state / "guards"
    guards.mkdir(parents=True)
    lock = guards / "s1.lock"
    lock.mkdir()
    (lock / "held").write_text("")
    (lock / "pid").write_text(str(os.getpid()))
    (lock / "token").write_text("t")
    for _ in range(4):
        assert denied(run(read(f), state)) is None


@pytest.mark.parametrize("env,expect", [
    ({"CLAUDE_PLUGIN_DATA": "{d}/pd"}, "{d}/pd/guards"),
    ({"CLAUDE_PLUGIN_DATA": "", "XDG_STATE_HOME": "{d}/xdg"}, "{d}/xdg/ndf/guards"),
    ({"CLAUDE_PLUGIN_DATA": "", "XDG_STATE_HOME": "", "HOME": "{d}/home"},
     "{d}/home/.local/state/ndf/guards"),
    ({"CLAUDE_PLUGIN_DATA": "", "XDG_STATE_HOME": "", "HOME": "", "TMPDIR": "{d}/tmp"},
     "{d}/tmp/ndf-guards"),
])
def test_guards_dir_follows_wf_state_dir(tmp_path, env, expect):
    env = {k: v.format(d=tmp_path) for k, v in env.items()}
    (tmp_path / "tmp").mkdir()
    f = tmp_path / "x.txt"
    f.write_text("")
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    e.update(env)
    subprocess.run(["bash", str(SCRIPT)], input=json.dumps(read(f)), text=True,
                   capture_output=True, env=e, check=True)
    assert (pathlib.Path(expect.format(d=tmp_path)) / "read-s1.json").is_file()
    wf = subprocess.run(
        ["bash", "-c", f". '{WF_DOCS}/scripts/lib/workflow-common.sh'; wf_state_dir"],
        text=True, capture_output=True, env=e).stdout.strip()
    assert pathlib.Path(wf).parent == pathlib.Path(expect.format(d=tmp_path)).parent


# ---------------------------------------------------------------- 文脈量（AC12〜AC16）

def transcript(tmp_path, total, name="t.jsonl", usage=True):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"content": "x"}}]
    msg = {"role": "assistant", "content": []}
    if usage:
        msg["usage"] = {"input_tokens": 10, "cache_read_input_tokens": total - 110,
                        "cache_creation_input_tokens": 100, "output_tokens": 5}
    lines.append({"type": "assistant", "message": msg})
    lines.append({"type": "attachment"})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def transcript_multi(tmp_path, totals, name="t.jsonl"):
    """複数の assistant usage を順に持つ transcript。最後の usage が判定に使われる。"""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"content": "x"}}]
    for total in totals:
        lines.append({"type": "assistant", "message": {
            "role": "assistant", "content": [],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": total - 110,
                      "cache_creation_input_tokens": 100, "output_tokens": 5}}})
    lines.append({"type": "attachment"})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


def skill(tp, name="ndf:implementation-plan", args="#829", session="s1", **extra):
    p = {"tool_name": "Skill", "tool_input": {"skill": name, "args": args},
         "session_id": session, "transcript_path": str(tp)}
    p.update(extra)
    return p


def agent(tp, desc="設計: #829 #830", session="s1", tool="Agent", **extra):
    p = {"tool_name": tool, "tool_input": {"description": desc, "prompt": "x"},
         "session_id": session, "transcript_path": str(tp)}
    p.update(extra)
    return p


def test_context_over_limit_denies_stage_skill(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    reason = denied(run(skill(tp), state))
    assert reason and "/ndf:development-workflow #829" in reason
    assert "context-window.md" in reason
    assert "250000" in reason or "250,000" in reason


def test_context_over_limit_denies_supervisor(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    reason = denied(run(agent(tp), state))
    assert reason and "/ndf:development-workflow #829 #830" in reason
    assert "/goal" in reason
    assert denied(run(agent(tp, desc="実装: #1", session="s9", tool="Task"), state))


def test_context_issue_placeholder(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    reason = denied(run(skill(tp, args=""), state))
    assert "/ndf:development-workflow <課題番号>" in reason


def test_context_within_limit_passes(tmp_path, state):
    tp = transcript(tmp_path, 150_000)
    assert denied(run(skill(tp), state)) is None


def test_context_uses_last_assistant_usage(tmp_path, state):
    # 現状固定: assistant の usage が複数あるとき、最後の値で上限判定する。
    # 上限超過の後に上限以内が来れば通り、順序を逆にすると拒否される。
    over_then_under = transcript_multi(tmp_path, [250_000, 150_000], name="ou.jsonl")
    assert denied(run(skill(over_then_under, session="sou"), state)) is None
    under_then_over = transcript_multi(tmp_path, [150_000, 250_000], name="uo.jsonl")
    assert denied(run(skill(under_then_over, session="suo"), state))


def test_context_only_reads_last_200_lines(tmp_path, state):
    # 現状固定: 上限超過の usage が末尾 200 行から外れると通り、範囲内なら拒否される。
    outside_tail = transcript(tmp_path, 250_000, name="outside.jsonl")
    with outside_tail.open("a") as fh:
        for _ in range(201):
            fh.write(json.dumps({"type": "attachment"}) + "\n")
    assert denied(run(skill(outside_tail, session="sout"), state)) is None

    inside_tail = transcript(tmp_path, 250_000, name="inside.jsonl")
    with inside_tail.open("a") as fh:
        for _ in range(198):
            fh.write(json.dumps({"type": "attachment"}) + "\n")
    assert denied(run(skill(inside_tail, session="sin"), state))


def test_context_malformed_transcript_passes(tmp_path, state):
    # 現状固定: usage が上限超過でも、壊れた JSON 行がある記録は読めず fail-open する。
    tp = transcript(tmp_path, 250_000)
    with tp.open("a") as fh:
        fh.write("{not json\n")

    proc = run(skill(tp), state)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_context_subagent_passes(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    assert denied(run(skill(tp, agent_id="a1"), state)) is None
    assert denied(run(agent(tp, agent_id="a1"), state)) is None
    sub = transcript(tmp_path, 250_000, name="sess/subagents/agent-1.jsonl")
    assert denied(run(skill(sub), state)) is None
    assert denied(run(agent(sub), state)) is None


def test_context_non_stage_passes(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    for name in ("ndf:markdown-writing", "ndf:worktree", "ndf:progress-tracking",
                 "ndf:out-of-scope"):
        assert denied(run(skill(tp, name=name), state)) is None, name
    assert denied(run(agent(tp, desc="調査: 既存の規約"), state)) is None
    assert denied(run(agent(tp, desc="何かの説明"), state)) is None


def test_context_once_then_pass_same_key(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    f = tmp_path / "x.txt"
    f.write_text("")
    assert denied(run(skill(tp), state))
    run(bash("ls"), state)
    run(read(f), state)
    run(skill(tp, name="ndf:markdown-writing"), state)
    assert denied(run(skill(tp), state)) is None
    assert denied(run(skill(tp, name="ndf:merged"), state))


def test_context_other_key_replaces_mark(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    assert denied(run(skill(tp), state))
    assert denied(run(skill(tp, args="#830"), state))
    assert denied(run(skill(tp, args="#830"), state)) is None


def test_context_agent_once_then_pass(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    assert denied(run(agent(tp, desc="設計: #829"), state))
    assert denied(run(agent(tp, desc="設計: #829"), state)) is None
    assert denied(run(agent(tp, desc="実装: #829"), state))


@pytest.mark.parametrize("desc", ["設計: v10.16.1 のリリース作業", "実装: リリース 2.0.3"])
def test_context_version_is_not_issue_number(tmp_path, state, desc):
    # 版数・小数を課題番号と読まない。番号が無ければ <課題番号> へ落ちる
    tp = transcript(tmp_path, 250_000)
    reason = denied(run(agent(tp, desc=desc, session="sver" + desc[:1]), state))
    assert reason and "/ndf:development-workflow <課題番号>" in reason


@pytest.mark.parametrize("desc", ["検査: 829", "取り込み: 829", "仕上げ: 829"])
def test_context_agent_bare_issue_number_is_normalized(tmp_path, state, desc):
    # 現状固定: description の課題番号が # を付けない裸の番号でも、案内は
    # sed 's/^/#/' で # 付きへ整えられる。各入力は終了コード 0 の deny になり、
    # 案内は /ndf:development-workflow #829 を示す（session を分けて 1 回目で拒否）。
    tp = transcript(tmp_path, 250_000)
    session = "sbare" + desc[:1]
    reason = denied(run(agent(tp, desc=desc, session=session), state))
    assert reason and "/ndf:development-workflow #829" in reason


def test_context_guard_env(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    assert denied(run(skill(tp), state, {"NDF_CONTEXT_GUARD": "0"})) is None
    assert denied(run(skill(tp), state, {"NDF_CONTEXT_LIMIT": "300000"})) is None


def test_context_unreadable_passes(tmp_path, state):
    assert denied(run(skill(tmp_path / "missing.jsonl"), state)) is None
    tp = transcript(tmp_path, 250_000, usage=False)
    assert denied(run(skill(tp), state)) is None
    p = skill(tp)
    del p["transcript_path"]
    assert denied(run(p, state)) is None


def test_context_parallel_second_call_passes_once(tmp_path, state):
    tp = transcript(tmp_path, 250_000)
    assert denied(run(skill(tp), state))
    results = []

    def go():
        results.append(denied(run(skill(tp), state)))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r is None) == 1


def test_context_large_transcript_is_fast(tmp_path, state):
    import time
    tp = tmp_path / "big.jsonl"
    line = json.dumps({"type": "user", "message": {"content": "y" * 1000}}) + "\n"
    with tp.open("w") as fh:
        for _ in range(50_000):
            fh.write(line)
    transcript_tail = transcript(tmp_path, 250_000, name="tail.jsonl").read_text()
    with tp.open("a") as fh:
        fh.write(transcript_tail)
    start = time.monotonic()
    assert denied(run(skill(tp), state))
    assert time.monotonic() - start < 1.5


# ---------------------------------------------------------------- 一覧と登録（AC14 / AC24）

def test_stage_list_matches_design():
    names = {l.strip() for l in STAGES.read_text().splitlines()
             if l.strip() and not l.startswith("#")}
    assert names == STAGE_SKILLS
    listed = set()
    for m in MANIFESTS.glob("*-skills.txt"):
        listed |= {l.strip() for l in m.read_text().splitlines() if l.strip()}
    assert names <= listed


def test_hook_registered_for_claude():
    hooks = json.loads(HOOKS.read_text())["hooks"]["PreToolUse"]
    ours = [h for h in hooks if any("token-guard.sh" in x["command"] for x in h["hooks"])]
    assert len(ours) == 1
    assert set(ours[0]["matcher"].split("|")) == {"Bash", "Read", "Skill", "Agent", "Task"}
    entry = ours[0]["hooks"][0]
    assert entry["continueOnError"] is True and entry["timeout"] == 5
    assert "worktree-guard.sh" in hooks[0]["hooks"][0]["command"]


def test_hook_not_registered_for_other_runtimes():
    for f in (ROOT / "hooks").glob("*.json"):
        if f.name != "claude.json":
            assert "token-guard" not in f.read_text(), f


# ---------------------------------------------------------------- 文書（AC1〜AC4 / AC18〜AC23）

def test_waiting_doc_lists_methods():
    text = WAITING.read_text()
    for word in ("run_in_background", "Monitor", "token-guard.sh", "NDF_SLEEP_GUARD",
                 "NDF_READ_REPEAT_GUARD", "tasks/*.output"):
        assert word in text, word


def _code_blocks(text):
    import re
    return re.findall(r"```[a-z]*\n(.*?)```", text, re.S)


def test_no_foreground_sleep_loop_examples():
    import re
    for doc in (WAITING, LAYERS):
        for block in _code_blocks(doc.read_text()):
            if "run_in_background" in block:
                continue
            assert not re.search(r"\b(while|until)\b.*\bdo\b[^`]*\bsleep\b", block, re.S), doc


def test_agent_layers_refers_waiting():
    text = LAYERS.read_text()
    sup = text.split("supervisor が守る規則:")[1].split("\n\n")[1]
    wrk = text.split("worker が守る規則:")[1].split("\n\n")[1]
    assert "waiting.md" in sup
    assert "waiting.md" in wrk


def test_foreground_loop_docs_point_to_waiting():
    skills = ROOT / "skills"
    for rel in ("external-ai/references/cli-codex.md", "external-ai/references/cli-agy.md",
                "qa-security-scan/03-report-template.md",
                "release/references/completion-check.md"):
        assert "development-workflow/references/waiting.md" in (skills / rel).read_text(), rel


def test_context_window_doc():
    text = CONTEXT.read_text()
    assert "実測ではない" not in text
    assert "#827" in text
    assert "NDF_CONTEXT_LIMIT" in text
    assert "200,000" in text or "200000" in text
    assert "/ndf:development-workflow #" in text
    assert "stage-check.sh report" in text
    assert "closedByPullRequestsReferences" in text


def test_context_limit_default_matches_doc(tmp_path, state):
    tp = transcript(tmp_path, 200_001)
    assert denied(run(skill(tp), state))
    tp2 = transcript(tmp_path, 200_000, name="t2.jsonl")
    assert denied(run(skill(tp2, session="s2"), state)) is None


def test_workflow_skill_handover_rule():
    text = WF_SKILL.read_text()
    assert "引き継ぎの 1 行" in text
    assert "## 持ち場の報告" in text
    assert "結果: 関門" in text
    assert "context-window.md" in text


def test_readme_runtime_table():
    text = README.read_text()
    assert "token-guard.sh" in text
    for rt in ("Claude Code", "Codex", "Kiro", "agy"):
        assert rt in text
