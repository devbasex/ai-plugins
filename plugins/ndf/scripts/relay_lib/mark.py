"""hook の本体: Stop（`mark`）・停止（`stop`）・質問の合図（`question`）・カットポイントの告知（`notice`）。

#895・#980・#1016・#1142 の C6。合図 `next.json` と `log.jsonl` は `record.RelayRecord` を通して書く。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

from . import proc
from .common import (ASKED_FILE, HELD_FILE, MARK_FILE, PID_FILE, QUESTION_FILE, QUESTION_LOCK, STOP_FILE, LockBusy,
                     _lock, env_num, load_json, parse_iso, quiet_seconds, relay_running, remove, stamp,
                     state_root, write_json_atomic)
from .record import RelayRecord, current_section

FENCE_RE = re.compile(r"^(`{3,})(.*)$")


def next_blocks(text: str) -> list[str]:
    """外側の囲みの中を除き、情報文字列が `ndf-next` の 3 つのバッククォートの囲みの中身を返す。"""
    blocks: list[str] = []
    open_len = 0
    cur: list[str] | None = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line.rstrip("\r"))
        if not open_len:
            if m and "`" not in m.group(2):
                open_len = len(m.group(1))
                cur = [] if open_len == 3 and m.group(2).strip() == "ndf-next" else None
            continue
        if m and len(m.group(1)) >= open_len and not m.group(2).strip():
            if cur is not None:
                blocks.append("\n".join(cur).strip("\n"))
            open_len, cur = 0, None
        elif cur is not None:
            cur.append(line)
    return blocks


def running_tasks(tasks) -> list[dict]:
    """Stop hook の入力の `background_tasks` のうち動いているもの。

    Claude Code（2.1.282 で実測）の要素は `id`・`type`・`status`・`description`・`command` を持つ。"""
    if not isinstance(tasks, list):
        return []
    return [{"id": str(t.get("id") or ""), "type": str(t.get("type") or ""),
             "command": str(t.get("command") or t.get("description") or "")[:80]}
            for t in tasks if isinstance(t, dict) and t.get("status") == "running"]


def background_running(tasks) -> bool:
    return bool(running_tasks(tasks))


def hold_reason(tasks: list[dict]) -> str:
    """背景の作業が残っていて合図を書けないときに、Stop を止めて conductor へ渡す文。"""
    rows = [f"- {t['id'] or '(id 不明)'}: {t['command'] or '(コマンド不明)'}" for t in tasks]
    return "\n".join([
        f"ndf-relay: 背景の作業が {len(tasks)} 件動いているので、ndf-next の合図を書かなかった（ラッパーは切り替わらない）。",
        *rows,
        "背景の作業を止めるのは TaskStop <id>。pkill -f / pgrep -f で止めたり確かめたりしない"
        "（Claude Code が包んだコマンド行に一致しない）。止まったかは完了通知（failed / killed）で確かめる。",
        "止めてから ndf-next を出し直す。supervisor や supervise.py queue のように止めてはいけない作業なら、"
        "止めずに終わりを待ってから ndf-next を出し直す。",
    ])


def hold_once(d: str, section: int | None, block: str, active: bool) -> bool:
    """同じ区間・同じ候補で 1 度だけ真を返す（Stop を止める）。stop_hook_active が真なら止めない。"""
    if active:
        return False
    key = {"section": section, "hash": hashlib.sha256(block.encode()).hexdigest()}
    held = os.path.join(d, HELD_FILE)
    if load_json(held) == key:
        return False
    write_json_atomic(held, key)
    return True


def asked_after(d: str, mark_path: str) -> bool:
    """合図を書いた後に質問が出ていれば真。"""
    m = load_json(mark_path)
    asked = load_json(os.path.join(d, ASKED_FILE))
    if not isinstance(m, dict) or not isinstance(asked, dict):
        return False
    w, a = parse_iso(m.get("written_at")), parse_iso(asked.get("at"))
    return w is not None and a is not None and a >= w


def cmd_mark() -> int:
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not relay_running(d):
        return 0
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(data, dict) or not proc.is_direct_child(d):
        return 0
    # Stop が起きたなら質問は表示されていない（Esc で取り消した合図もここで消える）
    remove(os.path.join(d, QUESTION_FILE))
    record = RelayRecord(d)
    blocks = next_blocks(str(data.get("last_assistant_message") or ""))
    tasks = running_tasks(data.get("background_tasks"))
    if tasks or len(blocks) > 1:
        record.drop_mark()
        if blocks:
            section = current_section(d)
            reason = "blocks" if len(blocks) > 1 else "background"
            held = (reason == "background"
                    and hold_once(d, section, blocks[0], bool(data.get("stop_hook_active"))))
            record.mark_skipped(section, reason, tasks, held)
            if held:
                print(json.dumps({"decision": "block", "reason": hold_reason(tasks)}, ensure_ascii=False))
        return 0
    if not blocks:
        # 合図の後に応答が続いた（目標が未達など）。質問が出ていなければ前の合図を残す
        if asked_after(d, record.path(MARK_FILE)):
            record.drop_mark()
        return 0
    record.write_mark(blocks[0], data)
    return 0


def cmd_stop() -> int:
    root = state_root()
    found = 0
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        d = os.path.join(root, name)
        if not os.path.isdir(d) or not relay_running(d):
            continue
        with open(os.path.join(d, STOP_FILE), "w"):
            pass
        try:
            with open(os.path.join(d, PID_FILE)) as f:
                print(f.read().strip() or name)
        except OSError:
            print(name)
        found += 1
    return 0 if found else 1


# ---------------------------------------------------------------- 質問の合図（関門を越えない守り）


DENY_REASON = ("ndf-relay: ラッパーが入力を書いている最中だったため、質問を出さなかった。"
               "利用者へ返さずに、同じ AskUserQuestion を今すぐもう一度呼ぶ")


def cmd_question(action: str) -> int:
    """`open`: 質問の合図を作る（ロックが取れなければ質問を拒否する）。`close`: 消す。"""
    started = time.time()
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()  # hook の JSON は読み捨てる
    except (OSError, ValueError):
        pass
    try:
        d = proc.under_relay()
        if d is None:
            return 0
    except Exception:
        return 0
    if action == "close":
        try:
            remove(os.path.join(d, QUESTION_FILE))
        except Exception:
            pass
        return 0
    if action != "open":
        return 0
    try:
        fd = _lock(os.path.join(d, QUESTION_LOCK),
                   max(0.0, started + env_num("NDF_RELAY_QUESTION_WAIT", 3) - time.time()))
        if fd is None:
            raise LockBusy()
        try:
            os.close(os.open(os.path.join(d, QUESTION_FILE), os.O_WRONLY | os.O_CREAT, 0o600))
            # 質問が出た時刻を残す。これより前の合図では切り替えない
            write_json_atomic(os.path.join(d, ASKED_FILE), {"at": stamp()})
        finally:
            os.close(fd)
    except Exception:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON}}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- カットポイントの告知（#980）


def notice_lines() -> tuple[str, str]:
    """カットポイントの告知。文面と秒数の唯一の定義。

    1 行目は `is-child` と同じ判定（ラッパーの直接の子か）。自動で切り替わるかは 2 行目が表す。
    外のときは 2 行目を理由（`relay_position()`）で変え、原因と対処を書く（#1016）。
    秒数はラッパー本体の静止（`NDF_RELAY_QUIET`）そのもので、切り替えの時間は足さない。
    """
    outside = ("/exit してから claude を起動し、下の中身を最初の入力として貼り付ける"
               "（/ndf:install-wrapper でラッパーを入れると自動になる）")
    try:
        pos = proc.relay_position()
        if pos == "not-running":
            return "outside", ("ラッパーは既に終わっている。"
                               "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
        if pos == "not-child":
            child = proc.relay_child_pid()
            who = f"元の会話（子 pid {child}）" if child is not None else "元の会話"
            return "outside", (f"ラッパーは{who}しか見ていないため、この会話で出した ndf-next は自動では拾われない。"
                               "元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから"
                               " claude を起動し、下の中身を最初の入力として貼り付ける")
        if pos != "relay":
            return "outside", outside
        quiet = quiet_seconds()
    except Exception:
        return "outside", outside
    if quiet == float("inf"):
        return "relay", ("NDF_RELAY_QUIET が有限でないため、ラッパーは自動で切り替えない。"
                         "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
    # ラッパー本体は負・nan・-inf の静止を待たずに通すので、0 として数える
    q = quiet if quiet == quiet and quiet > 0 else 0.0
    n = round(q)
    when = f"約 {n} 秒後に" if n > 0 else "まもなく"
    return "relay", (f"{when}自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ"
                     "（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）")


def cmd_notice() -> int:
    for line in notice_lines():
        print(line)
    return 0
