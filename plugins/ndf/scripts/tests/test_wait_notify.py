"""利用者の回答か承認を待つときだけ Slack へ知らせるフック（#821）。

入口（`wait-notify.py`）は本物の Slack の代わりに記録用の偽の送信先（`NDF_SLACK_API_BASE`）へ送り、
`git`・`gh` は `PATH` の先頭に置いた偽物を使う。HOME と状態の置き場はテストごとの一時ディレクトリにする。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts" / "wait-notify.py"
CORPUS = pathlib.Path(__file__).resolve().parent / "fixtures" / "wait_notice_corpus.json"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import wait_notice as wn  # noqa: E402


# ---------------------------------------------------------------------------
# 偽の送信先・偽の git / gh・起動
# ---------------------------------------------------------------------------

class FakeSlack:
    def __init__(self, status: int = 200, delay: float = 0.0):
        self.requests: list[tuple[str, dict]] = []
        self.status, self.delay = status, delay
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append((self.path, json.loads(body or b"{}")))
                if outer.delay:
                    time.sleep(outer.delay)
                payload = json.dumps({"ok": outer.status == 200, "ts": f"{len(outer.requests)}.0"}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def texts(self) -> list[str]:
        return [body["text"] for path, body in self.requests if path.endswith("chat.postMessage")]

    def wait_for(self, n: int, timeout: float = 8.0) -> list[str]:
        end = time.time() + timeout
        while time.time() < end and len(self.requests) < n:
            time.sleep(0.05)
        return self.texts()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def slack():
    s = FakeSlack()
    yield s
    s.close()


def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def world(tmp_path, slack):
    """一時の HOME・状態の置き場・プロジェクト・偽の git / gh と、入口を起動する関数。"""
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(bin_dir / "git", f"""#!/bin/sh
case "$*" in
  "rev-parse --show-toplevel") echo "{proj}";;
  "remote get-url origin") echo "https://github.com/o/r.git";;
  *) exit 1;;
esac
""")
    _write_exe(bin_dir / "gh", """#!/bin/sh
[ -n "$FAKE_GH_URL" ] || exit 1
echo "$FAKE_GH_URL"
""")
    base_env = {k: v for k, v in os.environ.items()
                if not k.startswith(("CLAUDE", "SLACK_", "NDF_", "XDG_", "REDMINE", "DEBUG_SLACK", "FAKE_GH", "KIRO_"))}
    base_env.update({
        "HOME": str(home),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_CHANNEL_ID": "C1",
        "SLACK_USER_MENTION": "",
        "NDF_SLACK_API_BASE": slack.base,
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_BRIDGE_SESSION_ID": "",
        "CLAUDE_CODE_REMOTE_SESSION_ID": "",
        "REDMINE_URL": "",
        "NDF_SLACK_NOTIFY_DONE": "",
    })

    class World:
        root = tmp_path
        project = proj
        state = tmp_path / "state" / "ndf" / "wait-notify"

        def transcript(self, *entries) -> str:
            path = tmp_path / "t.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            return str(path)

        def run(self, runtime: str, hook_input, env=None, raw: str | None = None):
            e = dict(base_env)
            e.update(env or {})
            data = raw if raw is not None else json.dumps({"cwd": str(proj), **hook_input}, ensure_ascii=False)
            start = time.time()
            r = subprocess.run([sys.executable, str(ENTRY), "--runtime", runtime], input=data,
                               capture_output=True, text=True, env=e, cwd=str(proj), timeout=30)
            r.elapsed = time.time() - start
            return r

    return World()


def user(uuid: str, text: str = "指示") -> dict:
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}


def assistant(uuid: str, text: str) -> dict:
    return {"type": "assistant", "uuid": uuid, "message": {"content": [{"type": "text", "text": text}]}}


def stop(text: str, transcript: str, session: str = "s1") -> dict:
    return {"hook_event_name": "Stop", "session_id": session, "transcript_path": transcript,
            "last_assistant_message": text, "stop_hook_active": False}


def no_send(world, slack, result) -> None:
    assert result.returncode == 0 and result.stdout == ""
    time.sleep(0.5)
    assert slack.requests == []
    assert not world.state.exists() or not any(world.state.iterdir())


# ---------------------------------------------------------------------------
# 本文の判定（実例）
# ---------------------------------------------------------------------------

def _corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))["examples"]


def test_corpus_precision_and_recall():
    """実例で、待ちの適合率と再現率がそれぞれ 85% 以上、待ちでないを当てる割合が 90% 以上。"""
    ex = _corpus()
    assert len(ex) >= 60
    tp = fp = fn = tn = 0
    for e in ex:
        kind, _ = wn.classify_text(e["text"])
        got, want = kind != wn.NONE, e["label"] != wn.NONE
        tp += got and want
        fp += got and not want
        fn += want and not got
        tn += not got and not want
    assert tp / (tp + fp) >= 0.85
    assert tp / (tp + fn) >= 0.85
    assert tn / (tn + fp) >= 0.90


def test_corpus_has_three_labels():
    labels = {e["label"] for e in _corpus()}
    assert labels == {wn.ANSWER, wn.APPROVAL, wn.NONE}


@pytest.mark.parametrize("text,kind", [
    ("設計 PR を出しました。https://github.com/devbasex/ai-plugins/pull/1150\n"
     "モデルの段と詳細の段のレビューが通りました。この設計でマージしてよいですか。", wn.APPROVAL),
    ("設計 PR を出しました。レビューの結果を待ちます。", wn.NONE),
    ("A と B のどちらにしますか。", wn.ANSWER),
    ("どうしますか。\n- 残す\n- 閉じる\n- 両方閉じる", wn.ANSWER),
    ("```\nどうしますか？\n```\n実装を終えました。", wn.NONE),
    ("## 次にやることは？\n実装を終えました。", wn.NONE),
    ("> 進めてよいですか\n実装を終えました。", wn.NONE),
    ("起票してよいか決めてください。#539 の巻き直しを待っています。", wn.APPROVAL),
    ("本番への配布は、関門 2 のあなたの承認を待っています。", wn.APPROVAL),
    ("変えたい箇所があれば言ってください。", wn.NONE),
    ("#205 の cross-review を回しますか。", wn.ANSWER),
    ("進めてよろしければ `/ndf:pr` で作成します。", wn.APPROVAL),
])
def test_classify_text(text, kind):
    assert wn.classify_text(text)[0] == kind


def test_excerpt_is_the_wait_sentence_up_to_200_chars():
    kind, excerpt = wn.classify_text("作業を終えました。\n" + "あ" * 300 + "ですか。")
    assert kind == wn.ANSWER
    assert len(excerpt) == 200 and excerpt.startswith("あ")


# ---------------------------------------------------------------------------
# 事象ごとに通知する（受け入れ条件: 権限確認・AskUserQuestion・ExitPlanMode）
# ---------------------------------------------------------------------------

def test_permission_prompt_notifies_approval(world, slack):
    t = world.transcript(user("u1"), assistant("a1", "実行します。"))
    r = world.run("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                             "message": "Claude needs your permission to use Bash", "session_id": "s1",
                             "transcript_path": t})
    assert r.returncode == 0 and r.stdout == ""
    texts = slack.wait_for(1)
    assert len(texts) == 1
    assert texts[0].startswith("【承認待ち】[proj] Claude needs your permission to use Bash")


def test_ask_user_question_notifies_answer(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s1",
                         "transcript_path": t,
                         "tool_input": {"questions": [{"question": "どの色にしますか"}, {"question": "大きさは"}]}})
    texts = slack.wait_for(1)
    assert texts[0].startswith("【回答待ち】[proj] どの色にしますか（ほか 1 問）")


def test_exit_plan_mode_notifies_approval(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", {"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode", "session_id": "s1",
                         "transcript_path": t, "tool_input": {"plan": "# hello.txt を作る\n\n手順"}})
    texts = slack.wait_for(1)
    assert texts[0].startswith("【承認待ち】[proj] 計画の承認: hello.txt を作る")


def test_elicitation_notifies_answer(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", {"hook_event_name": "Notification", "notification_type": "elicitation_dialog",
                         "message": "入力してください", "session_id": "s1", "transcript_path": t})
    assert slack.wait_for(1)[0].startswith("【回答待ち】")


@pytest.mark.parametrize("ntype", ["idle_prompt", "auth_success"])
def test_other_notifications_do_not_notify(world, slack, ntype):
    t = world.transcript(user("u1"))
    r = world.run("claude", {"hook_event_name": "Notification", "notification_type": ntype,
                             "message": "x", "session_id": "s1", "transcript_path": t})
    no_send(world, slack, r)


# ---------------------------------------------------------------------------
# 文での待ち・完了（I2・I3）
# ---------------------------------------------------------------------------

def test_stop_with_question_notifies(world, slack):
    text = "設計 PR を出しました。https://github.com/devbasex/ai-plugins/pull/1150\nこの設計でマージしてよいですか。"
    t = world.transcript(user("u1"))
    world.run("claude", stop(text, t))
    texts = slack.wait_for(1)
    assert texts[0].splitlines()[0] == "【承認待ち】[proj] この設計でマージしてよいですか。"
    assert "PR: https://github.com/devbasex/ai-plugins/pull/1150" in texts[0]


def test_stop_with_done_report_does_not_notify(world, slack):
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("実装を終え、テストは 12 件とも通りました。", t))
    no_send(world, slack, r)


def test_done_flag_sends_done(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("前置き。\n\n実装を終え、テストは 12 件とも通りました。", t),
              env={"NDF_SLACK_NOTIFY_DONE": "true"})
    texts = slack.wait_for(1)
    assert texts[0].startswith("【完了】[proj] 実装を終え、テストは 12 件とも通りました。")


def test_stop_without_message_reads_transcript(world, slack):
    t = world.transcript(user("u1"), assistant("a1", "A と B のどちらにしますか。"))
    hook = stop("", t)
    del hook["last_assistant_message"]
    world.run("claude", hook)
    assert slack.wait_for(1)[0].startswith("【回答待ち】[proj] A と B のどちらにしますか。")


# ---------------------------------------------------------------------------
# 非対話・再帰（I4・I5）
# ---------------------------------------------------------------------------

def test_non_interactive_does_not_notify(world, slack):
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("どちらにしますか。", t), env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
    no_send(world, slack, r)


def test_stop_hook_active_does_not_notify(world, slack):
    t = world.transcript(user("u1"))
    hook = stop("どちらにしますか。", t)
    hook["stop_hook_active"] = True
    r = world.run("claude", hook)
    no_send(world, slack, r)


def test_missing_slack_vars_do_nothing(world, slack):
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("どちらにしますか。", t), env={"SLACK_BOT_TOKEN": ""})
    no_send(world, slack, r)


# ---------------------------------------------------------------------------
# 戻り先（I7）
# ---------------------------------------------------------------------------

def test_bridge_session_url_converts_cse(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("どちらにしますか。", t), env={"CLAUDE_CODE_BRIDGE_SESSION_ID": "cse_x"})
    text = slack.wait_for(1)[0]
    assert "セッション: https://claude.ai/code/session_x" in text
    assert f"cwd: {world.project}" in text


def test_remote_session_url(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("どちらにしますか。", t), env={"CLAUDE_CODE_REMOTE_SESSION_ID": "session_y"})
    assert "セッション: https://claude.ai/code/session_y" in slack.wait_for(1)[0]


def test_local_cli_shows_resume_host_and_cwd(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("どちらにしますか。", t, session="abc-123"))
    lines = slack.wait_for(1)[0].splitlines()
    assert lines[1] == "再開: claude --resume abc-123"
    assert lines[2].startswith("host: ") and lines[2].endswith(f" / cwd: {world.project}")


def test_without_session_id_only_host_and_cwd(world, slack):
    t = world.transcript(user("u1"))
    hook = stop("どちらにしますか。", t)
    del hook["session_id"]
    world.run("claude", hook)
    lines = slack.wait_for(1)[0].splitlines()
    assert len(lines) == 2 and lines[1].startswith("host: ")


def test_locator_for_codex_and_kiro():
    assert wn.build_locator("codex", {"session_id": "c1"}, {}, "h", "/w").lines()[0] == "再開: codex resume c1"
    assert wn.build_locator("kiro", {"session_id": "k1"}, {}, "h", "/w").lines()[0] == \
        "再開: kiro-cli chat --resume-id k1"
    assert wn.build_locator("kiro", {}, {}, "h", "/w").lines() == ["再開: kiro-cli chat --resume", "host: h / cwd: /w"]
    assert wn.build_locator("kiro", {}, {"KIRO_SESSION_ID": "k2"}, "h", "/w").lines()[0] == \
        "再開: kiro-cli chat --resume-id k2"


# ---------------------------------------------------------------------------
# 関連 URL（I8）
# ---------------------------------------------------------------------------

def test_answer_lists_issue_and_redmine(world, slack):
    text = "#821 と https://github.com/o/r/issues/5 と Redmine #14952 のどれから直しますか。"
    t = world.transcript(user("u1"))
    world.run("claude", stop(text, t), env={"REDMINE_URL": "https://redmine.example.com"})
    lines = slack.wait_for(1)[0].splitlines()
    assert "issue: https://github.com/o/r/issues/821" in lines
    assert "issue: https://github.com/o/r/issues/5" in lines
    assert "Redmine: https://redmine.example.com/issues/14952" in lines


def test_answer_without_redmine_url_has_no_redmine_line(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("Redmine #14952 から直しますか。", t))
    assert "Redmine" not in "\n".join(slack.wait_for(1)[0].splitlines()[1:])


def test_approval_falls_back_to_current_branch_pr(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("マージしてよいですか。", t), env={"FAKE_GH_URL": "https://github.com/o/r/pull/9"})
    assert "PR: https://github.com/o/r/pull/9" in slack.wait_for(1)[0]


def test_approval_without_pr_omits_line(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("マージしてよいですか。", t))
    assert "PR:" not in slack.wait_for(1)[0]


def test_extract_urls_puts_pr_first_and_limits_to_three():
    text = "#1 #2 #3 https://github.com/o/r/pull/4"
    urls = wn.extract_urls(wn.APPROVAL, text, "o/r")
    assert urls[0] == ("PR", "https://github.com/o/r/pull/4") and len(urls) == 3
    assert all(label != "PR" for label, _ in wn.extract_urls(wn.ANSWER, text, "o/r"))


# ---------------------------------------------------------------------------
# 二重に通知しない（I1）
# ---------------------------------------------------------------------------

def test_same_wait_is_notified_once(world, slack):
    """ExitPlanMode の PermissionRequest と、約 6 秒後の permission_prompt は同じ待ち。"""
    t = world.transcript(user("u1"), assistant("a0", "計画を書きました。"))
    world.run("claude", {"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode", "session_id": "s1",
                         "transcript_path": t, "tool_input": {"plan": "計画"}})
    world.transcript({"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "tool_use"}]}})
    r = world.run("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                             "message": "approve the plan", "session_id": "s1", "transcript_path": t})
    assert r.returncode == 0
    time.sleep(1.0)
    assert len(slack.texts()) == 1


def test_ask_user_question_and_its_permission_prompt_are_one_wait(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s1",
                         "transcript_path": t, "tool_input": {"questions": [{"question": "色は"}]}})
    world.run("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                         "message": "x", "session_id": "s1", "transcript_path": t})
    time.sleep(1.0)
    texts = slack.texts()
    assert len(texts) == 1 and texts[0].startswith("【回答待ち】")


def test_next_wait_after_user_reply_is_notified(world, slack):
    t = world.transcript(user("u1"))
    notice = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
              "message": "x", "session_id": "s1", "transcript_path": t}
    world.run("claude", notice)
    slack.wait_for(1)
    world.transcript({"type": "user", "uuid": "u2", "message": {"content": [{"type": "tool_result"}]}})
    world.run("claude", notice)
    assert len(slack.wait_for(2)) == 2


def test_record_is_per_session_file(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("どちらにしますか。", t, session="a/b"))
    slack.wait_for(1)
    record = json.loads((world.state / "a_b.json").read_text())
    assert record["key"] == "u1" and record["kind"] == wn.ANSWER and record["sent_at"] > 0


# ---------------------------------------------------------------------------
# Codex・Kiro
# ---------------------------------------------------------------------------

def test_codex_needs_opt_in(world, slack):
    r = world.run("codex", {"hook_event_name": "Stop", "session_id": "c1", "turn_id": "t1",
                            "last_assistant_message": "どちらにしますか。"})
    no_send(world, slack, r)


def test_codex_stop_and_permission_request(world, slack):
    env = {"NDF_CODEX_SLACK_NOTIFY": "true"}
    world.run("codex", {"hook_event_name": "Stop", "session_id": "c1", "turn_id": "t1",
                        "last_assistant_message": "どちらにしますか。"}, env=env)
    world.run("codex", {"hook_event_name": "PermissionRequest", "session_id": "c1", "turn_id": "t2",
                        "tool_name": "Bash", "tool_input": {"command": "touch x"}}, env=env)
    texts = slack.wait_for(2)
    assert any(x.startswith("【回答待ち】") and "再開: codex resume c1" in x for x in texts)
    assert any(x.startswith("【承認待ち】[proj] Bash の実行の承認") for x in texts)


def test_kiro_stop(world, slack):
    world.run("kiro", {"hook_event_name": "stop", "assistant_response": "この方針で進めてよいですか。"})
    text = slack.wait_for(1)[0]
    assert text.startswith("【承認待ち】") and "再開: kiro-cli chat --resume" in text


def test_kiro_same_text_in_other_conversations_is_notified(world, slack):
    for conv in ("k1", "k2"):
        world.run("kiro", {"hook_event_name": "stop", "conversation_id": conv,
                           "assistant_response": "この方針で進めてよいですか。"})
    assert len(slack.wait_for(2)) == 2


def test_kiro_session_from_env_is_the_key(world, slack):
    for sid in ("k1", "k2"):
        world.run("kiro", {"hook_event_name": "stop", "assistant_response": "この方針で進めてよいですか。"},
                  env={"KIRO_SESSION_ID": sid})
    texts = slack.wait_for(2)
    assert sorted(x for t in texts for x in t.splitlines() if x.startswith("再開:")) == \
        ["再開: kiro-cli chat --resume-id k1", "再開: kiro-cli chat --resume-id k2"]


def test_kiro_same_text_in_one_session_is_windowed(world, slack):
    world.run("kiro", {"hook_event_name": "stop", "assistant_response": "この方針で進めてよいですか。"},
              env={"KIRO_SESSION_ID": "k1"})
    [f] = list(world.state.glob("*.json"))
    record = json.loads(f.read_text())
    record["sent_at"] = 0
    f.write_text(json.dumps(record))
    world.run("kiro", {"hook_event_name": "stop", "assistant_response": "この方針で進めてよいですか。"},
              env={"KIRO_SESSION_ID": "k1"})
    assert len(slack.wait_for(2)) == 2


def test_kiro_without_session_windows_only_the_same_text(world, slack):
    for text in ("この方針で進めてよいですか。", "この方針で進めてよいですか。", "次はどちらにしますか。"):
        world.run("kiro", {"hook_event_name": "stop", "assistant_response": text})
    assert len(slack.wait_for(2)) == 2
    [record] = [json.loads(f.read_text()) for f in world.state.glob("*.json")]
    assert record["key"].endswith(":window") and record["key"] != ":window"


def test_leftover_kiro_session_is_not_used_by_other_runtimes(world, slack):
    world.run("codex", {"hook_event_name": "Stop", "last_assistant_message": "どちらにしますか。"},
              env={"NDF_CODEX_SLACK_NOTIFY": "true", "KIRO_SESSION_ID": "k9"})
    slack.wait_for(1)
    [f] = list(world.state.glob("*.json"))
    assert "k9" not in f.name and "k9" not in json.loads(f.read_text())["key"]


def test_kiro_non_stop_event_is_not_a_wait():
    hook_input = {"hook_event_name": "preToolUse", "assistant_response": "この方針で進めてよいですか。"}
    assert wn.classify_event("kiro", hook_input) is None


# ---------------------------------------------------------------------------
# 外の系の失敗（I9〜I12）と速さ
# ---------------------------------------------------------------------------

def test_empty_stdin(world, slack):
    no_send(world, slack, world.run("claude", {}, raw=""))


def test_not_json_stdin(world, slack):
    no_send(world, slack, world.run("claude", {}, raw="not json"))


def test_broken_transcript_still_exits_zero(world, slack):
    bad = world.root / "broken.jsonl"
    bad.write_text("{{{\n", encoding="utf-8")
    r = world.run("claude", stop("どちらにしますか。", str(bad)))
    assert r.returncode == 0 and r.stdout == ""


def test_missing_gh_still_sends(world, slack):
    (world.root / "bin" / "gh").unlink()
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("マージしてよいですか。", t))
    assert r.returncode == 0 and r.stdout == ""
    assert slack.wait_for(1)[0].startswith("【承認待ち】")


def test_slack_error_still_exits_zero(world, slack):
    slack.status = 500
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("どちらにしますか。", t))
    assert r.returncode == 0 and r.stdout == ""
    assert slack.wait_for(1)


def test_bad_arguments_exit_zero():
    r = subprocess.run([sys.executable, str(ENTRY), "--runtime", "nope"], input="{}", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""


def test_entry_returns_within_one_second_while_slack_is_slow(world, slack):
    slack.delay = 5
    t = world.transcript(user("u1"))
    r = world.run("claude", stop("どちらにしますか。", t))
    assert r.returncode == 0
    assert r.elapsed < 1.0


def test_mention_is_sent_then_deleted(world, slack):
    t = world.transcript(user("u1"))
    world.run("claude", stop("どちらにしますか。", t), env={"SLACK_USER_MENTION": "<@U1>"})
    slack.wait_for(3)
    paths = [p for p, _ in slack.requests]
    assert paths == ["/api/chat.postMessage", "/api/chat.postMessage", "/api/chat.delete"]
    first, second = slack.texts()
    assert first.startswith("<@U1> 【回答待ち】") and second.startswith("【回答待ち】")


# ---------------------------------------------------------------------------
# フックの定義
# ---------------------------------------------------------------------------

def _commands(hooks: dict, event: str) -> list[dict]:
    return [h for group in hooks["hooks"].get(event, []) for h in group["hooks"]]


def _runs_entry(command: str, runtime: str) -> bool:
    return "scripts/wait-notify.py" in command and f"--runtime {runtime}" in command


def test_claude_hooks_point_to_entry():
    hooks = json.loads((ROOT / "hooks" / "claude.json").read_text(encoding="utf-8"))
    raw = json.dumps(hooks)
    assert "session_end" not in raw and "exits" not in raw and "slack-notify.js" not in raw
    for event in ("Stop", "Notification", "PermissionRequest", "PreToolUse"):
        assert any(_runs_entry(h["command"], "claude") for h in _commands(hooks, event)), event
    matchers = {g.get("matcher") for g in hooks["hooks"]["Notification"]}
    assert "permission_prompt|elicitation_dialog|elicitation_url_dialog" in matchers
    assert {g.get("matcher") for g in hooks["hooks"]["PermissionRequest"]} == {"ExitPlanMode"}


def test_codex_hooks_point_to_entry():
    hooks = json.loads((ROOT / "hooks" / "codex.json").read_text(encoding="utf-8"))
    assert "codex-slack-notify.js" not in json.dumps(hooks)
    for event in ("Stop", "PermissionRequest"):
        assert any(_runs_entry(h["command"], "codex") for h in _commands(hooks, event)), event
