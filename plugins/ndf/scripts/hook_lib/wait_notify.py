"""利用者の回答か承認を待つときだけ Slack へ知らせる hook の同期の側（#821・#1142 の決定 20）。

判定と重複の抑止だけを同期で行い、送信は切り離した子（`wait-notify.py --send`）へ渡して戻る。どの失敗も何も出さずに
終わる（`PermissionRequest` の判断に使われないため、標準出力へは何も書かない）。`DEBUG_SLACK_NOTIFY=true` のときだけ
`~/.claude/logs/wait-notify-<日付>.log` へ理由を書く。`.env` の読み取りは `lib/notify.py`、排他は `lib/locks.py` が持つ。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import wait_notice as wn

SCRIPTS = Path(__file__).resolve().parents[1]
ENTRY = SCRIPTS / "wait-notify.py"
RUNTIMES = ("claude", "codex", "kiro")
TRANSCRIPT_TAIL = 1024 * 1024
WINDOW_SECONDS = 60
RECORD_DAYS = 7
SEARCH_TEXT_LIMIT = 20000


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def log(*parts) -> None:
    if os.environ.get("DEBUG_SLACK_NOTIFY") != "true":
        return
    try:
        d = Path.home() / ".claude" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"wait-notify-{_dt.date.today().isoformat()}.log"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{_dt.datetime.now().isoformat()} [{os.getpid()}] " + " ".join(str(p) for p in parts) + "\n")
    except OSError:
        pass


def load_env_file(cwd: str) -> None:
    """`.env` を cwd から git のトップまで上へ探し、無ければスクリプトの置き場から上へ探す。既存の値は上書きしない。"""
    import notify
    found = notify.load_env_upward(cwd, SCRIPTS.parent)
    if found:
        log("env file:", found)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def read_transcript(path: str | None) -> tuple[str, str] | None:
    """Claude Code の transcript の末尾 1 MB から (最後の user の uuid, 最後の assistant の本文) を返す。

    **鍵は最後の user の項目の uuid にする。** 実測（2.1.282）では、`PreToolUse`・`PermissionRequest`・`Stop` の
    起動の時点でその応答の assistant の項目がまだ書かれていない。user の項目（指示・ツールの結果）は利用者が
    応じたときにだけ足されるため、1 つの待ちに届く事象の間で変わらず、応じると変わる。読めなければ None。
    """
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TRANSCRIPT_TAIL))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    user_key, assistant_text = "", ""
    for line in reversed(data.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "user" and not user_key and entry.get("uuid"):
            user_key = str(entry["uuid"])
        elif kind == "assistant" and not assistant_text:
            assistant_text = _text_of((entry.get("message") or {}).get("content"))
        if user_key and assistant_text:
            break
    return (user_key, assistant_text) if user_key else None


# --- 重複の抑止 ------------------------------------------------------------------------------------

def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ndf" / "wait-notify"


def _session_file(session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session) or "_"
    return state_dir() / f"{safe}.json"


def _prune(directory: Path, now: float) -> None:
    for f in [*directory.glob("*.json"), *directory.glob("*.json.lock")]:
        try:
            if now - f.stat().st_mtime > RECORD_DAYS * 86400:
                f.unlink()
        except OSError:
            pass


def claim(session: str, key: str, kind: str, window: bool, now: float | None = None) -> bool:
    """同じ待ちのキーの 2 回目なら False。初めてなら記録を書いて True（送る前に書く）。"""
    import locks
    now = time.time() if now is None else now
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _prune(directory, now)
    path = _session_file(session)
    with locks.exclusive(path, timeout=5):
        try:
            prev = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            prev = {}
        if isinstance(prev, dict) and prev.get("key") == key:
            if not window or now - float(prev.get("sent_at") or 0) < WINDOW_SECONDS:
                return False
        path.write_text(json.dumps({"key": key, "kind": kind, "sent_at": now}, ensure_ascii=False), encoding="utf-8")
    return True


def session_of(runtime: str, hook_input: dict) -> str:
    """セッションの識別子。無ければ空。

    Kiro の stop の標準入力には識別子が無く、環境変数 `KIRO_SESSION_ID` で渡る。この環境変数は Kiro のときだけ読む。
    同じシェルに残った値を別のランタイムの鍵へ混ぜないためである。
    """
    session = hook_input.get("session_id") or hook_input.get("conversation_id")
    if not session and runtime == "kiro":
        session = os.environ.get("KIRO_SESSION_ID")
    return str(session or "")


def wait_key(runtime: str, hook_input: dict, transcript: tuple[str, str] | None) -> tuple[str, bool]:
    """(待ちのキー, 60 秒の窓で扱うか)。"""
    session = session_of(runtime, hook_input)
    if runtime == "claude" and transcript:
        return transcript[0], False
    if runtime == "codex" and hook_input.get("turn_id"):
        tool_input = json.dumps(hook_input.get("tool_input"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(tool_input.encode()).hexdigest()[:12]
        return f"{hook_input['turn_id']}:{hook_input.get('hook_event_name')}:{digest}", False
    # Kiro は応答本文のハッシュを鍵にし、連続した同じ文面を 60 秒の窓で止める（記録は 1 セッションに
    # 最後の 1 鍵だけで、A→B→A のように間に別の文面が入ると止まらない）。Kiro には応じると
    # 変わる値が無く、窓なしでは同じセッションで答えた後の同じ問いまで期限なく止まる。
    if runtime == "kiro" and hook_input.get("assistant_response"):
        digest = hashlib.sha1(str(hook_input["assistant_response"]).encode()).hexdigest()[:16]
        return (f"{session}:{digest}" if session else f"{digest}:window"), True
    # 受け皿: 主鍵が取れないとき、同じセッションの 60 秒の内は 2 件目を止める粗い保険。
    # 「通知が来ない」を調べるときは、ログの `skip: already notified <session>:window` を見る。
    return f"{session}:window", True


def run_hook(runtime: str, hook_input: dict | None) -> None:
    if hook_input is None:
        log("skip: stdin is empty or not JSON")
        return
    cwd = str(hook_input.get("cwd") or os.getcwd())
    load_env_file(cwd)
    if not (os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL_ID")):
        log("skip: SLACK_BOT_TOKEN / SLACK_CHANNEL_ID not set")
        return
    if runtime == "codex" and not _truthy(os.environ.get("NDF_CODEX_SLACK_NOTIFY")):
        log("skip: NDF_CODEX_SLACK_NOTIFY is not true")
        return
    if (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").startswith("sdk-"):
        log("skip: non-interactive run", os.environ.get("CLAUDE_CODE_ENTRYPOINT"))
        return
    if hook_input.get("stop_hook_active") is True:
        log("skip: stop_hook_active")
        return
    transcript = read_transcript(hook_input.get("transcript_path")) if runtime == "claude" else None
    assistant_text = transcript[1] if transcript else ""
    key, window = wait_key(runtime, hook_input, transcript)
    wait = wn.classify_event(runtime, hook_input, assistant_text, key)
    if wait is None:
        log("skip: event is not a wait", hook_input.get("hook_event_name"))
        return
    event = hook_input.get("hook_event_name") or ""
    reply = hook_input.get("last_assistant_message") or hook_input.get("assistant_response") or assistant_text
    if wait.kind == wn.NONE:
        if not _truthy(os.environ.get("NDF_SLACK_NOTIFY_DONE")):
            log("skip: reply does not wait")
            return
        wait = wn.Wait(wn.DONE, wn.done_excerpt(reply), key)
    session = session_of(runtime, hook_input) or cwd
    if not claim(session, wait.key, wait.kind, window):
        log("skip: already notified", wait.key)
        return
    is_stop = event in ("Stop", "stop") or runtime == "kiro"
    search = reply if is_stop else f"{wait.excerpt}\n{assistant_text}"
    payload = {
        "runtime": runtime, "kind": wait.kind, "excerpt": wait.excerpt, "key": wait.key, "cwd": cwd,
        "hook_input": {k: hook_input.get(k) for k in ("session_id", "conversation_id") if hook_input.get(k)},
        "search": search[-SEARCH_TEXT_LIMIT:],
    }
    log("notify:", wait.kind, wait.key)
    subprocess.Popen(
        [sys.executable, str(ENTRY), "--send", json.dumps(payload, ensure_ascii=False)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, cwd=cwd if os.path.isdir(cwd) else None,
    )


def hook(runtime: str, hook_input: dict | None) -> None:
    """hook の入口から呼ぶ。どの失敗も外へ出さない（I9〜I12）。"""
    try:
        run_hook(runtime, hook_input)
    except Exception as exc:  # noqa: BLE001
        log("error:", type(exc).__name__, exc)
