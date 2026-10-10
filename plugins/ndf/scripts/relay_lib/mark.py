"""hook の本体: Stop（`mark`。承認待ちの差し戻しを含む）・StopFailure（`limit`）・停止（`stop`）・質問の合図（`question`）・カットポイントの告知（`notice`）。

#895・#980・#1016・#1142 の C6。合図 `next.json` と `log.jsonl` は `record.RelayRecord` を通して書く。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from . import claude as cl
from . import proc
from .common import (
    ASKED_FILE,
    HELD_FILE,
    LIMIT_FILE,
    MARK_FILE,
    PID_FILE,
    QUESTION_FILE,
    QUESTION_LOCK,
    STOP_FILE,
    WAIT_HELD_FILE,
    LockBusy,
    _lock,
    _unlock,
    env_num,
    load_json,
    parse_iso,
    quiet_seconds,
    relay_running,
    remove,
    stamp,
    state_root,
    write_json_atomic,
)
from .record import RelayRecord, _start_rows, current_section

import md  # noqa: E402,I001  common が lib/ を sys.path に置く
import wait_notice  # noqa: E402,I001


def next_blocks(text: str) -> list[str]:
    """情報文字列が `ndf-next` の、3 つのバッククォートの囲みの中身を返す。

    囲みは Markdown の包み `lib/md.py`（CommonMark）で読む。ほかの囲みの中の囲みは中身の文字列なので拾わない。"""
    return [t.content.strip("\n") for t in md.md_tokens(text) if t.type == "fence" and t.markup == "```" and t.info.strip() == "ndf-next"]


def running_tasks(tasks) -> list[dict]:
    """Stop hook の入力の `background_tasks` のうち動いているもの。

    Claude Code（2.1.282 で実測）の要素は `id`・`type`・`status`・`description`・`command` を持つ。"""
    if not isinstance(tasks, list):
        return []
    return [
        {
            "id": str(t.get("id") or ""),
            "type": str(t.get("type") or ""),
            "command": str(t.get("command") or t.get("description") or "")[:80],
        }
        for t in tasks
        if isinstance(t, dict) and t.get("status") == "running"
    ]


def background_running(tasks) -> bool:
    return bool(running_tasks(tasks))


def hold_reason(tasks: list[dict]) -> str:
    """背景の作業が残っていて合図を書けないときに、Stop を止めて conductor へ渡す文。"""
    rows = [f"- {t['id'] or '(id 不明)'}: {t['command'] or '(コマンド不明)'}" for t in tasks]
    return "\n".join(
        [
            f"ndf-relay: 背景の作業が {len(tasks)} 件動いているので、ndf-next の合図を書かなかった（ラッパーは切り替わらない）。",
            *rows,
            "背景の作業を止めるのは TaskStop <id>。pkill -f / pgrep -f で止めたり確かめたりしない"
            "（Claude Code が包んだコマンド行に一致しない）。止まったかは完了通知（failed / killed）で確かめる。",
            "止めてから ndf-next を出し直す。supervisor や supervise.py queue のように止めてはいけない作業なら、"
            "止めずに終わりを待ってから ndf-next を出し直す。",
            *(
                [
                    "ScheduleWakeup の予約は ScheduleWakeup を stop: true で呼んで取り消す（残すと /exit で選択肢が出て、"
                    "ラッパーが 30 秒で SIGTERM を送る）。"
                ]
                if any(t["type"] == "wakeup" for t in tasks)
                else []
            ),
        ]
    )


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


def _hook_input() -> tuple[str, dict] | None:
    """ラッパーの直接の子が送った hook の入力と状態ディレクトリ。対象でなければ None。"""
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not relay_running(d):
        return None
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return None
    if not isinstance(data, dict) or not proc.is_direct_child(d):
        return None
    return d, data


def _mark_action(blocks: list[str], tasks: list[dict]) -> str:
    """合図の扱い: `skip`（背景の作業か複数の候補で書かない）・`idle`（候補なし）・`write`（書く）。"""
    if tasks or len(blocks) > 1:
        return "skip"
    return "write" if blocks else "idle"


def _skip_mark(d: str, record: RelayRecord, blocks: list[str], tasks: list[dict], active: bool) -> None:
    record.drop_mark()
    if not blocks:
        return
    section = current_section(d)
    reason = "blocks" if len(blocks) > 1 else "background"
    held = reason == "background" and hold_once(d, section, blocks[0], active)
    record.mark_skipped(section, reason, tasks, held)
    if held:
        print(json.dumps({"decision": "block", "reason": hold_reason(tasks)}, ensure_ascii=False))


WAIT_REASON = (
    "ndf-relay: この応答は AskUserQuestion を呼ばずに、本文で承認・判断を待って終わった。/goal の判定は本文の待ちでは止まらず、"
    "承認のないまま先へ進む。利用者の承認・判断が要るなら、同じ問いを AskUserQuestion で出し直す（判断の材料は本文に書いてよい）。"
    "問いでなければ、そのまま作業を続ける。"
)


def _section_started(d: str, section: int | None) -> float | None:
    for row in _start_rows(d):
        if section is None or row.get("section") == section:
            return parse_iso(row.get("at"))
    return None


def prose_wait_reason(d: str, record: RelayRecord, data: dict, asked_open: bool) -> str | None:
    """承認待ちの差し戻し（#1492）。ブロックの無い応答が本文の待ちで終わったら差し戻しの文を返す。

    合図 `next.json` が保留中（在り、質問がその後に出ていない）なら判定しない。差し戻した直後の同じ区間の Stop では
    差し戻さない（`wait-held.json`。`stop_hook_active` は `/goal` の続きで常に真になるため読まない）。応答の中で
    質問を出した（質問の合図が在った・質問の時刻が前の Stop より後）なら差し戻さない。どの失敗も差し戻さない側へ倒す。"""
    try:
        mark_path = record.path(MARK_FILE)
        if os.path.exists(mark_path) and not asked_after(d, mark_path):
            return None
        section = current_section(d)
        state_path = os.path.join(d, WAIT_HELD_FILE)
        prev = load_json(state_path)
        same = isinstance(prev, dict) and prev.get("section") == section
        prev_at = parse_iso(prev.get("at")) if same else _section_started(d, section)
        state = {"section": section, "at": stamp(), "held": False}
        write_json_atomic(state_path, state)
        if same and prev.get("held"):
            return None
        asked = load_json(os.path.join(d, ASKED_FILE))
        asked_at = parse_iso(asked.get("at")) if isinstance(asked, dict) else None
        if asked_open or (asked_at is not None and (prev_at is None or asked_at >= prev_at)):
            return None
        kind, _ = wait_notice.classify_text(str(data.get("last_assistant_message") or ""))
        if kind not in (wait_notice.ANSWER, wait_notice.APPROVAL):
            return None
        write_json_atomic(state_path, {**state, "held": True})
        try:
            record.prose_wait(section, kind)
        except OSError:
            pass
        return WAIT_REASON
    except Exception:  # noqa: BLE001 — 判定・状態の読み書きの失敗: Stop を止めない
        return None


def cmd_mark() -> int:
    got = _hook_input()
    if got is None:
        return 0
    d, data = got
    # Stop が起きたなら質問は表示されていない（Esc で取り消した合図もここで消える）
    question = os.path.join(d, QUESTION_FILE)
    asked_open = os.path.exists(question)
    remove(question)
    record = RelayRecord(d)
    blocks = next_blocks(str(data.get("last_assistant_message") or ""))
    tasks = running_tasks(data.get("background_tasks")) + cl.pending_wakeups(str(data.get("transcript_path") or ""), time.time())
    action = _mark_action(blocks, tasks)
    if action == "skip":
        _skip_mark(d, record, blocks, tasks, bool(data.get("stop_hook_active")))
    elif action == "idle":
        # 合図の後に応答が続いた（目標が未達など）。質問が出ていなければ前の合図を残す
        if asked_after(d, record.path(MARK_FILE)):
            record.drop_mark()
    else:
        record.write_mark(blocks[0], data)
    if not blocks:
        reason = prose_wait_reason(d, record, data, asked_open)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def cmd_limit() -> int:
    """StopFailure hook の本体（#1389）。ラッパーの直接の子の応答が API の失敗で終わったら `limit.json` を書く。

    書くのは `written_at`・`error`・`transcript_path`・`session_id`・`cwd` だけで、応答の本文とトークンは書かない。
    上限の種類とリセット時刻は、ラッパーが会話の記録の最後の合成応答から読む。StopFailure の出力と終了コードは
    Claude Code が無視するので、常に 0 を返す。"""
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not relay_running(d):
        return 0
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(data, dict) or not proc.is_direct_child(d):
        return 0
    write_json_atomic(
        os.path.join(d, LIMIT_FILE),
        {
            "written_at": stamp(),
            "error": str(data.get("error") or ""),
            "transcript_path": str(data.get("transcript_path") or ""),
            "session_id": str(data.get("session_id") or ""),
            "cwd": str(data.get("cwd") or ""),
        },
    )
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


DENY_REASON = (
    "ndf-relay: ラッパーが入力を書いている最中だったため、質問を出さなかった。利用者へ返さずに、同じ AskUserQuestion を今すぐもう一度呼ぶ"
)


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
        fd = _lock(os.path.join(d, QUESTION_LOCK), max(0.0, started + env_num("NDF_RELAY_QUESTION_WAIT", 3) - time.time()))
        if fd is None:
            raise LockBusy()
        try:
            os.close(os.open(os.path.join(d, QUESTION_FILE), os.O_WRONLY | os.O_CREAT, 0o600))
            # 質問が出た時刻を残す。これより前の合図では切り替えない
            write_json_atomic(os.path.join(d, ASKED_FILE), {"at": stamp()})
        finally:
            _unlock(fd)
    except Exception:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": DENY_REASON,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


# ---------------------------------------------------------------- カットポイントの告知（#980）


def notice_lines() -> tuple[str, str]:
    """カットポイントの告知。文面と秒数の唯一の定義。

    1 行目は `is-child` と同じ判定（ラッパーの直接の子か）。自動で切り替わるかは 2 行目が表す。
    外のときは 2 行目を理由（`relay_position()`）で変え、原因と対処を書く（#1016）。
    秒数はラッパー本体の静止（`NDF_RELAY_QUIET`）そのもので、切り替えの時間は足さない。
    """
    outside = "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（/ndf:install-wrapper でラッパーを入れると自動になる）"
    try:
        pos = proc.relay_position()
        if pos == "not-running":
            return "outside", ("ラッパーは既に終わっている。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
        if pos == "not-child":
            child = proc.relay_child_pid()
            who = f"元の会話（子 pid {child}）" if child is not None else "元の会話"
            return "outside", (
                f"ラッパーは{who}しか見ていないため、この会話で出した ndf-next は自動では拾われない。"
                "元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから"
                " claude を起動し、下の中身を最初の入力として貼り付ける"
            )
        if pos != "relay":
            return "outside", outside
        quiet = quiet_seconds()
    except Exception:
        return "outside", outside
    if quiet == float("inf"):
        return "relay", (
            "NDF_RELAY_QUIET が有限でないため、ラッパーは自動で切り替えない。"
            "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける"
        )
    # ラッパー本体は負・nan・-inf の静止を待たずに通すので、0 として数える
    q = quiet if quiet == quiet and quiet > 0 else 0.0
    n = round(q)
    when = f"約 {n} 秒後に" if n > 0 else "まもなく"
    return "relay", (
        f"{when}自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ"
        "（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）"
    )


def cmd_notice() -> int:
    for line in notice_lines():
        print(line)
    return 0
