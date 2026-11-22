"""ラッパーの従量の接続への退避と戻り（U11a の 2 本目。#3604 #3864 の受け入れ条件 #3864 の 9・決定 8・I9）。

- 上限・認証の失敗の後に登録済みの替え先が無ければ、従量の接続（`metered`）で同じ会話を続ける。子の設定ディレクトリは
  共有の設定ディレクトリで、保存した設定なら親の AWS のキーを渡さない
- 上限でない登録済みアカウントが現れたら、見回りで止めてよい時点に戻る（戻りの 1 行）
- API キーの設定の子は、最初の払い出しを見届けるまで会話を子へ渡さない（別冊の「最初の払い出しまで送らない」）
値はすべて偽の値で、実のキー・アカウント・ネットワークを使わない。
"""

from __future__ import annotations

import io
import json
import shlex
import time

import pytest

from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_decide, accounts, key_helper, observe, switch_decide, wrapper
from ndf.domains.relay import account_registry as reg
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, make_settings, put_usage, register, rows_of

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
BEDROCK_DECL = "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1"


@pytest.fixture(autouse=True)
def cli_version(monkeypatch):
    monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd: "2.1.330")


def _envs(spawn):
    return [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls]


def _run(tmp_path, children, environ=None, prepare=None, initial=None, stdin_fd=None, clock=None, wall=None, prompt="最初の 1 行"):
    s = make_settings(tmp_path, environ={**make_settings(tmp_path).environ, **(environ or {})})
    spawn = Spawner(children)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock or clock_ticks(), out=out, stdin_fd=stdin_fd, wall=wall)
    if prepare:
        prepare(w)
    if initial is not None:
        w._initial = lambda now: initial
    try:
        ended = w.run(prompt)
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode("utf-8", errors="replace")


def test_3864_9_d8_a_limit_without_a_registered_target_resumes_on_metered(tmp_path):
    # 決定 8 の (2): 上限でない登録済みアカウントが無ければ、従量の接続で同じ会話を続ける。子の設定ディレクトリは共有側
    register(tmp_path, "work1")
    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL}
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], environ, initial=wrapper.Choice(("work1",)))
    (one, env1), (two, env2) = spawn.calls
    assert _envs(spawn) == ["work1", reg.METERED]
    assert two[1:3] == ["--resume", one[2]] and two[-1] == switch_decide.RESUME_PROMPT
    assert env2["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude") and env2["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert "NDF_ACCOUNT_USAGE_FILE" not in env2 and "NDF_SHARED_CONFIG_DIR" not in env2


def test_3864_9_d8_a_saved_metered_setting_does_not_carry_the_parent_aws_keys(tmp_path):
    store = register(tmp_path, "work1")
    reg.save_metered(store, "bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "prod"}, {"model": "x"}, 0.0)
    environ = {"AWS_ACCESS_KEY_ID": "fake-access-key-id", "AWS_SECRET_ACCESS_KEY": "fake-secret", "AWS_SESSION_TOKEN": "fake-token"}
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], environ, initial=wrapper.Choice(("work1",)))
    env2 = spawn.calls[1][1]
    assert env2["NDF_CLAUDE_ACCOUNT"] == reg.METERED and env2["AWS_PROFILE"] == "prod"
    assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"} & env2.keys()


class JumpWall:
    """上限の観測が書かれるまでは今、書かれた後は 2 日後を返す壁時計（リセットの待ちを壁時計で待たない）。"""

    def __init__(self, state):
        self.state, self.base = state, time.time()

    def now(self) -> float:
        return self.base + (2 * 86400.0 if accounts.limits_path(self.state).exists() else 0.0)


def test_3864_9_d8_a_broken_metered_setting_is_not_a_fallback(tmp_path):
    # 壊れた従量の設定は替え先にしない。替え先が無いので wait_reset で待ち、リセットの後に同じアカウントで続ける
    register(tmp_path, "work1")
    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": "CLAUDE_CODE_USE_BEDROCK=1 NDF_RELAY_DIR=/tmp/x"}
    children = [FakeChild([LIMIT_LINE], None), FakeChild([], 0)]
    wall = JumpWall(tmp_path / "state")
    ended, spawn, w, shown = _run(tmp_path, children, environ, initial=wrapper.Choice(("work1",)), wall=wall)
    assert _envs(spawn) == ["work1", "work1"] and rows_of(w, "wait_reset")
    assert "NDF_RELAY_DIR" in shown and "/tmp/x" not in shown


def test_3864_9_d8_an_auth_failure_without_a_registered_target_resumes_on_metered(tmp_path, monkeypatch):
    register(tmp_path, "work1")
    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL}
    monkeypatch.setattr(wrapper.Wrapper, "_auth_seen", lambda self, sec, screen: sec.account == "work1")
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([b"x"], None), FakeChild([], 0)], environ, initial=wrapper.Choice(("work1",)))
    assert _envs(spawn) == ["work1", reg.METERED] and spawn.calls[1][0][-1] == switch_decide.AUTH_RESUME_PROMPT


def test_3864_9_d8_a_designated_metered_connection_is_launched(tmp_path):
    # 指定（環境変数 `NDF_CLAUDE_ACCOUNT`）の metered は、種類ごとの除外（`_metered` が None でなく tried に無い）だけで試す
    register(tmp_path)
    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL, "NDF_CLAUDE_ACCOUNT": reg.METERED}
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)], environ)
    assert _envs(spawn) == [reg.METERED]


def test_3864_9_d8_metered_goes_back_to_a_registered_account_at_the_safe_point(tmp_path):
    # 上限でない登録済みアカウントが現れたら、見回りの guard で止めてよい時点に戻る。戻りの 1 行で続ける
    register(tmp_path, "work1")

    def prepare(w):
        put_usage(tmp_path, "work1", 10.0, 10.0)
        w._safe_point = lambda sec, idle: True

    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL}
    ended, spawn, w, _ = _run(
        tmp_path, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], environ, prepare, initial=wrapper.Choice((reg.METERED,))
    )
    (one, _), (two, _) = spawn.calls
    assert _envs(spawn) == [reg.METERED, "work1"]
    assert two[1:3] == ["--resume", one[2]] and two[-1] == switch_decide.GUARD_BACK_PROMPT


def test_d8_guard_back_prompt_is_chosen_when_the_current_target_is_metered():
    pick = account_decide.Pick("switch", "work1", "戻る")
    base = dict(
        question=False,
        question_forwarded=False,
        question_id=None,
        next_block=None,
        child_alive=True,
        child_exit=None,
        idle_seconds=10.0,
        next_age_seconds=0.0,
        quiet_seconds=2.0,
        stall_seconds=1000.0,
        context_tokens=0,
        context_limit=800_000,
        handover_asked=False,
        guard_pick=pick,
        safe=True,
    )
    assert switch_decide.decide(switch_decide.SectionState(**base, metered=True)).prompt == switch_decide.GUARD_BACK_PROMPT
    assert switch_decide.decide(switch_decide.SectionState(**base)).prompt == switch_decide.GUARD_RESUME_PROMPT


def test_d8_tried_is_cleared_once_the_switched_child_answers(tmp_path):
    # 替えた子が応答を 1 つ返したら、この続きで認証に失敗した接続先の記録（tried）を空にする
    register(tmp_path, "work1")

    class Answering(FakeChild):
        def started(self, directory, session_id, launch):
            path = observe.transcript_path(make_settings(tmp_path).environ, tmp_path, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 60))
            row = {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def prepare(w):
        w.tried.add(reg.METERED)

    ended, spawn, w, _ = _run(tmp_path, [Answering([b"x", b"y"], 0)], prepare=prepare, initial=wrapper.Choice(("work1",)))
    assert w.tried == set()


# ---------------------------------------------------------------- API キーの設定の子（キーの子）


def _key_env():
    return {"NDF_SUPERVISE_CLAUDE_FALLBACK": f"ANTHROPIC_API_KEY={FAKE_KEY}"}


def _helper_words(argv):
    data = json.loads(argv[argv.index("--settings") + 1])
    return shlex.split(data["apiKeyHelper"])


class Paying(FakeChild):
    """`pay_at` 回目の見回り（isalive の回）で、Claude Code の代わりに helper の払い出しの行を記録へ足す偽のキーの子。"""

    def __init__(self, pay_at=None, outputs=(b"working\r\n",)):
        super().__init__(list(outputs), None)
        self.pay_at, self.polls, self.w = pay_at, 0, None
        self.written_at: list[int] = []

    def isalive(self):
        self.polls += 1
        if self.alive and self.polls == self.pay_at:
            rec = self.w.section.target.helper
            assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY)
        if self.alive and self.polls == 40:
            self.alive, self.exitstatus = False, 0
        return super().isalive()

    def write(self, data):
        self.written_at.append(self.polls)
        super().write(data)


def test_3864_9_an_api_key_child_carries_the_helper_with_the_pin_and_the_record(tmp_path):
    register(tmp_path)
    child = Paying(pay_at=2)

    def prepare(w):
        child.w = w

    ended, spawn, w, _ = _run(tmp_path, [child], _key_env(), prepare, initial=wrapper.Choice((reg.METERED,), fixed=True))
    ((argv, env),) = spawn.calls
    words = _helper_words(argv)
    rec = w.section.target.helper
    assert words[3:5] == ["api-key-helper", "metered"] and words[-4:] == ["--record", str(rec.path), "--id", rec.helper_id]
    assert words[-6] == "--pin" and "ANTHROPIC_API_KEY" not in env and FAKE_KEY not in json.dumps(argv)


def test_i9_the_prompt_waits_for_the_first_payout_of_the_key_child(tmp_path):
    # 別冊の「最初の払い出しまで送らない」: 起動引数に最初のプロンプトを載せず、out: true の行を見てから子の端末へ送る
    register(tmp_path)
    child = Paying(pay_at=4)

    def prepare(w):
        child.w = w

    ended, spawn, w, _ = _run(tmp_path, [child], _key_env(), prepare, initial=wrapper.Choice((reg.METERED,), fixed=True))
    argv = spawn.calls[0][0]
    assert "最初の 1 行" not in argv
    assert child.written and min(child.written_at) >= 4
    assert "最初の 1 行".encode() in b"".join(child.written)


def test_i9_a_key_child_that_never_pays_out_is_stopped_after_first_as_a_settings_hit(tmp_path):
    # `first` を過ぎても払い出しが現れなければ設定の当たりとして止め、会話を送らない（tried に入れない）
    register(tmp_path)
    child = Paying(pay_at=None)
    now = [0.0]

    def clock():
        now[0] += limits.value(key_helper.POLICY, "first") / 8
        return now[0]

    def prepare(w):
        child.w = w

    ended, spawn, w, shown = _run(tmp_path, [child], _key_env(), prepare, initial=wrapper.Choice((reg.METERED,), fixed=True), clock=clock)
    assert child.written == [] and ended.cause == "account_unavailable"
    assert w.section.end_cause == "drift" and reg.METERED not in w.tried and key_helper.SETTINGS_FILE in shown


def test_i9_a_key_child_without_a_first_prompt_still_holds_input_until_the_first_payout(tmp_path):
    # 初回のプロンプトを省いた起動でも保留を始める。端末の入力は払い出しを見届けるまで子へ送らず、`first` の待ちも働く
    register(tmp_path)
    child = Paying(pay_at=None)
    now = [0.0]

    def clock():
        now[0] += limits.value(key_helper.POLICY, "first") / 8
        return now[0]

    def prepare(w):
        child.w = w

    ended, spawn, w, shown = _run(
        tmp_path, [child], _key_env(), prepare, initial=wrapper.Choice((reg.METERED,), fixed=True), clock=clock, prompt=""
    )
    assert child.written == [] and ended.cause == "account_unavailable"
    assert w.section.end_cause == "drift" and key_helper.SETTINGS_FILE in shown


def test_i9_the_settings_and_pin_files_are_removed_when_the_key_child_ends(tmp_path):
    register(tmp_path)
    child = Paying(pay_at=2)

    def prepare(w):
        child.w = w

    ended, spawn, w, _ = _run(tmp_path, [child], _key_env(), prepare, initial=wrapper.Choice((reg.METERED,), fixed=True))
    words = _helper_words(spawn.calls[0][0])
    pin = words[words.index("--pin") + 1]
    assert not (tmp_path / pin).exists()
