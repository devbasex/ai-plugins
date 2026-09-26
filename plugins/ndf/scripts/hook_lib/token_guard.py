"""待ちの呼び出しと、文脈が上限を超えた conductor の工程の起動を止める PreToolUse の guard（#829・#830）。Claude Code だけ。

| tool_name          | 判定                                                                      |
| ------------------ | ------------------------------------------------------------------------- |
| Bash               | 前景の `sleep` で待つ（ループの本体にあるか、上限を超える）                    |
| Bash               | 文脈が上限を超えた conductor が supervise.py queue / run でプランを起こす      |
| Read               | 変わらないファイルの同じ範囲を続けて読み直す                                  |
| Skill / Agent・Task | 文脈が上限を超えた conductor が工程へ入る                                    |
| Agent・Task        | supervisor の起動に、プランで流せることを案内する（止めない）                  |
| Skill              | 寿命 5 分の supervisor が文脈を伸ばしたまま収束ループを始める                  |

拒否は `permissionDecision: deny`、案内は `additionalContext` で返す。判定が失敗したとき（記録を書けない・読めない・
ロックを待ちの上限の内に取れない・構文を読み切れない）は通す。待ちの上限は `NDF_TOKEN_GUARD_LOCK_WAIT`（秒・0 以上の
整数。既定 1）。規約は skills/development-workflow/references/waiting.md と context-window.md にある。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import payload as pl

SCRIPTS = Path(__file__).resolve().parents[1]
HERE = str(SCRIPTS)
STAGES = SCRIPTS / "lib" / "token-guard-stages.txt"
WAITING_DOC = "development-workflow/references/waiting.md"
CONTEXT_DOC = "development-workflow/references/context-window.md"
STAGE_PREFIXES = ("設計", "実装", "検査", "取り込み", "仕上げ")
TAIL_LINES = 200
RECORD_DAYS = 7


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _off(name: str) -> bool:
    return os.environ.get(name, "1") == "0"


def _field(raw: dict, *path: str) -> str:
    """jq の `<path> // empty | tostring` と同じ（null と false は空）。"""
    v = raw
    for k in path:
        v = v.get(k) if isinstance(v, dict) else None
    if v is None or v is False:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float)) or isinstance(v, bool) else str(v)


def guards_dir() -> Path | None:
    """記録の置き場所。順は workflow-common.sh の wf_state_dir と同じ（あちらは stages/、こちらは guards/）。"""
    fallback = Path(os.environ.get("TMPDIR") or "/tmp") / "ndf-guards"
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        base = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "guards"
    elif os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"]) / "ndf" / "guards"
    elif os.environ.get("HOME"):
        base = Path(os.environ["HOME"]) / ".local" / "state" / "ndf" / "guards"
    else:
        base = fallback
    for d in (base, fallback):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(d, os.W_OK):
            return d
    return None


@contextmanager
def session_lock(directory: Path, sid: str) -> Iterator[bool]:
    """セッションごとの排他（`<dir>/<sid>.lock`）。取れなければ偽を渡す（呼び出し側は判定せずに通す）。"""
    wait = os.environ.get("NDF_TOKEN_GUARD_LOCK_WAIT", "1")
    import locks  # 排他が要る判定のときだけ読む（filelock の import は Bash と Edit の判定に載せない）
    held = locks.exclusive(directory / sid, timeout=int(wait) if wait.isdigit() else 1)
    try:
        held.__enter__()
    except (locks.LockTimeout, OSError):
        yield False
        return
    try:
        yield True
    finally:
        held.__exit__(None, None, None)


def write_json(path: Path, body: dict) -> None:
    """置き換えで書く（途中で落ちても壊れた JSON を残さない）。7 日より古い記録を消す。"""
    try:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError:
        return
    now = time.time()
    for old in path.parent.glob("*.json"):
        try:
            if now - old.stat().st_mtime > RECORD_DAYS * 86400:
                old.unlink()
        except OSError:
            pass


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- sleep ---------------------------------------------------------------------------------------------

def guard_sleep(raw: dict) -> dict | None:
    if _off("NDF_SLEEP_GUARD") or _field(raw, "tool_input", "run_in_background") == "true":
        return None
    cmd = _field(raw, "tool_input", "command")
    if "sleep" not in cmd:
        return None
    mx = os.environ.get("NDF_SLEEP_MAX_SEC") or "5"
    import shparse
    from . import shell_checks
    try:
        if shparse.unreadable(shparse.parse_bash(cmd)) or not shell_checks.sleep_deny(cmd, float(mx)):
            return None
    except (ValueError, RecursionError):  # 読めないコマンド・読めない上限は通す
        return None
    return deny(f"前景で sleep を使って待つと、待つ呼び出しのたびに会話の文脈の全体を読み直す（ループの本体の sleep と、{mx} 秒を超える sleep を止めている）。待つ相手に NDF のスクリプトがあればそれを Bash の run_in_background: true で起動し、完了通知を待つ（通知は 1 回で、待つ間は呼び出しが増えない）。queue の終わり: python3 {HERE}/supervise.py wait <done のパス>（途中の知らせで終了コード 20 で返るので、中身を読んで待ち直す）。PR の CI を待ってマージ（マージの承認を得た後に限る。緑ならそのまま --admin でマージする）: python3 {HERE}/merged-steps.py merge-when-green <PR 番号>。CI を待つだけなら gh pr checks <PR 番号> --watch を同じく背景で起動する。どちらでもなければ同じ条件の until ループ（例: until [ -s <ファイル> ]; do sleep 5; done）を同じく背景で起動する。出来事を 1 つずつ受けるなら Monitor を使う。規約: {WAITING_DOC}（止めるなら NDF_SLEEP_GUARD=0）")


# --- Read ----------------------------------------------------------------------------------------------

def _file_stat(path: str) -> tuple[int, str, int]:
    """(大きさ, 更新時刻（ナノ秒まで）, inode)。無いファイルは (-1, "-1", -1)。"""
    try:
        st = os.stat(path)
    except OSError:
        return -1, "-1", -1
    ns = st.st_mtime_ns
    return st.st_size, f"{ns // 10**9}.{ns % 10**9:09d}", st.st_ino


def guard_read(raw: dict) -> dict | None:
    if _off("NDF_READ_REPEAT_GUARD"):
        return None
    sid, path = _field(raw, "session_id"), _field(raw, "tool_input", "file_path")
    if not sid or not path:
        return None
    key = "\t".join((path, _field(raw, "tool_input", "offset"), _field(raw, "tool_input", "limit")))
    lim = os.environ.get("NDF_READ_REPEAT_LIMIT") or "3"
    limit = int(lim) if lim.isdigit() else 3
    d = guards_dir()
    if d is None:
        return None
    with session_lock(d, sid) as held:
        if not held:
            return None
        size, mtime, inode = _file_stat(path)
        state = d / f"read-{sid}.json"
        prev = _read_json(state)
        same = (prev.get("key") == key and prev.get("size") == size and prev.get("mtime") == mtime
                and prev.get("inode") == inode)
        count = (prev.get("count") if same and isinstance(prev.get("count"), int) else 0) + 1
        write_json(state, {"key": key, "size": size, "mtime": mtime, "inode": inode, "count": count})
    if count < limit:
        return None
    return deny(f"同じファイルの同じ範囲を、変わらないまま {count} 回続けて読もうとした（{path}）。queue の終わりを待つなら python3 {HERE}/supervise.py wait <done のパス> を、それ以外の書き終わりを待つなら until [ -s <ファイル> ]; do sleep 1; done を Bash の run_in_background: true で起動して完了通知を待つか、背景の処理そのものの完了通知を待つ。サブエージェントの tasks/*.output は読まずに完了通知を待つ。規約: {WAITING_DOC}（止めるなら NDF_READ_REPEAT_GUARD=0）")


# --- 文脈量 -------------------------------------------------------------------------------------------

class _Broken(ValueError):
    """記録の行が JSON として読めない（jq が読めないのと同じに、判定をせずに通す）。"""


def _usage_total(line: str) -> int | None:
    """assistant の行の文脈量（入力・キャッシュの読み・書きの和）。ほかの行は None、読めない行は `_Broken`。"""
    if not line.strip():
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        raise _Broken(line[:80]) from None
    msg = entry.get("message") if isinstance(entry, dict) and entry.get("type") == "assistant" else None
    usage = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return None
    try:
        return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
    except (TypeError, ValueError):
        raise _Broken(line[:80]) from None


def context_tokens(path: str) -> int | None:
    """最後の assistant の行の usage から文脈量を読む。末尾の 200 行だけを読む（大きな記録でも速く終える）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=TAIL_LINES)
        totals = [t for t in map(_usage_total, tail) if t is not None]
    except (OSError, _Broken):
        return None
    return totals[-1] if totals else None


def first_context_tokens(path: str) -> int | None:
    """記録の先頭から最初の assistant の行を探し、その文脈量を読む。見つけた時点で読むのをやめる。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                got = _usage_total(line)
                if got is not None:
                    return got
    except (OSError, _Broken):
        return None
    return None


def relay_notice() -> str | None:
    """ラッパー（relay.py）の直接の子なら告知の文面、外なら None（`relay_lib.mark.notice_lines` の 1 回の呼び出し。#980）。"""
    try:
        from relay_lib import mark
        kind, text = mark.notice_lines()
    except Exception:  # noqa: BLE001 — ラッパーの判定が失敗したら、外として扱う
        return None
    return text if kind == "relay" else None


ISSUE = re.compile(r"(?:^|[^0-9A-Za-z_/])#?([0-9]+)\b")
NOISE = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]+(?:\.[0-9]+)+")


def issue_refs(text: str) -> str:
    """課題番号を `#829 #830` の形で並べる。日付・版数・小数は先に取り除く。範囲（#829-830）は両端を残す。"""
    refs = []
    for line in text.split("\n"):
        refs += [f"#{m.group(1)}" for m in ISSUE.finditer(NOISE.sub("", line))]
    return " ".join(refs)


def guard_context(raw: dict, tool: str) -> dict | None:
    if _off("NDF_CONTEXT_GUARD") or _field(raw, "agent_id"):  # agent_id はサブエージェントの中でだけ付く
        return None
    tp, sid = _field(raw, "transcript_path"), _field(raw, "session_id")
    if "/subagents/" in tp or not tp or not sid:
        return None
    if tool == "Bash":
        cmd = _field(raw, "tool_input", "command")
        from . import shell_checks
        if not shell_checks.plan_command(cmd):
            return None
        key, words = f"plan\t{cmd}", ""
    elif tool == "Skill":
        skill = _field(raw, "tool_input", "skill").removeprefix("ndf:")
        try:
            stages = STAGES.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if skill not in stages:
            return None
        words = _field(raw, "tool_input", "args")
        key = f"skill\t{skill}\t{words}"
    else:
        words = _field(raw, "tool_input", "description")
        if ":" not in words or words.split(":", 1)[0] not in STAGE_PREFIXES:
            return None
        key = f"agent\t{words}"
    total = context_tokens(tp)
    if total is None:
        return None
    lim = os.environ.get("NDF_CONTEXT_LIMIT") or "200000"
    limit = int(lim) if lim.isdigit() else 200000
    d = guards_dir()
    if d is None:
        return None
    with session_lock(d, sid) as held:
        if not held:
            return None
        mark = d / f"context-{sid}.json"
        # ラッパーの直接の子の conductor では 1 度の通しをやめ、上限を超えている限り止め続ける（#895）
        notice = relay_notice() if os.environ.get("NDF_RELAY_DIR") and total > limit else None
        if notice is None and _read_json(mark).get("key") == key:
            try:
                mark.unlink()
            except OSError:
                pass
            return None
        if total <= limit:
            return None
        write_json(mark, {"key": key})
    nxt = f"/ndf:development-workflow {issue_refs(words) or '<課題番号>'}"
    if notice is not None:
        return deny(f"会話の文脈が {total} トークンで、上限 {limit} を超えた。ラッパーの下なので、上限を超えている限りこの起動を止め続ける。新しいフェーズ（プランを含む）を起動せず、動いているプランと supervisor の報告を待ってから、引継ぎ文書（/goal の指示が名指ししたもの。無ければ書かない）を更新し、次のコマンドを情報文字列 ndf-next の囲みのコードブロック 1 つで出して応答を終える（中身: /goal {nxt}、名指しの引継ぎ文書があれば「<文書> の続きから」）。<課題番号> のままなら、進めている課題の番号を補う。ブロックの直前に次の 1 文をそのまま書き、承認や確認を挟まずに出して終える: {notice}。規約: {CONTEXT_DOC}（止めるなら NDF_CONTEXT_GUARD=0、上限は NDF_CONTEXT_LIMIT）")
    return deny(f"会話の文脈が {total} トークンで、上限 {limit} を超えた。この工程（プランを含む）は新しい会話で始める。次のコマンドを情報文字列 ndf-next の囲みのコードブロック 1 つで示して応答を終える。中身: {nxt}（今の区間を /goal で始めていたなら /goal {nxt}）。<課題番号> のままなら、進めている課題の番号を補って示す。このまま続けると利用者が決めたら、同じ起動をもう一度行うと 1 度だけ通る。規約: {CONTEXT_DOC}（止めるなら NDF_CONTEXT_GUARD=0、上限は NDF_CONTEXT_LIMIT）")


def guard_supervisor_cut(raw: dict) -> dict | None:
    """寿命 5 分の supervisor（ndf:supervisor）が文脈を伸ばしたまま収束ループの Skill を始めるのを止める（#954）。

    止める条件は「今の文脈 C ≥ 比 × 最初の呼び出しの文脈 P」。1 度だけ通すことはしない。"""
    if _off("NDF_SUPERVISOR_CUT_GUARD"):
        return None
    skill = _field(raw, "tool_input", "skill").removeprefix("ndf:")
    stage = {"cross-refactoring": "構造改善",
             "cross-review": "<実装レビューかドキュメントレビューのうち、始めようとした工程>"}.get(skill)
    aid = _field(raw, "agent_id")
    if stage is None or not aid or _field(raw, "agent_type") != "ndf:supervisor":
        return None
    tp = _field(raw, "transcript_path")
    if not tp:
        return None
    own = f"{tp.removesuffix('.jsonl')}/subagents/agent-{aid}.jsonl"
    if not os.access(own, os.R_OK):
        return None
    first, last = first_context_tokens(own), context_tokens(own)
    if not first or last is None:
        return None
    ratio = os.environ.get("NDF_SUPERVISOR_CUT_RATIO") or "1.5"
    try:
        r = float(ratio)
    except ValueError:
        r = 0.0
    if not (r > 0 and last >= r * first):
        return None
    return deny(f"この supervisor の文脈が {last} トークンで、最初の呼び出し（{first}）の {ratio} 倍以上ある。寿命 5 分のまま収束ループ（{skill}）を始めると、待ちの後のたびに文脈の全体を書き直す。同じ起動をやり直さずに、Pull Request を出す・進行を記録するなど起動の前に済ませることを済ませてから、フェーズの報告を「結果: スイッチポイント」「次のフェーズ: <今と同じフェーズ>」「次の工程: {stage}」で返す（規則 12。conductor が寿命 1 時間の supervisor で続ける）。規約: {CONTEXT_DOC}（止めるなら NDF_SUPERVISOR_CUT_GUARD=0、比は NDF_SUPERVISOR_CUT_RATIO）")


def plan_hint(raw: dict) -> dict | None:
    """conductor が Agent で supervisor を起こすとき、.ndf/ の宣言があればプランで流せることを案内する（#1191）。"""
    if _off("NDF_PLAN_HINT") or _field(raw, "agent_id") or "/subagents/" in _field(raw, "transcript_path"):
        return None
    if _field(raw, "tool_input", "subagent_type") not in ("ndf:supervisor", "ndf:supervisor-waits"):
        return None
    try:
        p = subprocess.run(["git", "-C", _field(raw, "cwd") or ".", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    root = p.stdout.strip()
    if p.returncode != 0 or not root:
        return None
    if not (Path(root, ".ndf", "supervise.json").is_file() or Path(root, ".ndf", "worktree.json").is_file()):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": f"このリポジトリには .ndf/ の宣言がある。このフェーズは python3 {HERE}/supervise.py new <種別>（mission / impl / check / release）のプランで作り、supervise.py queue で流せる。Agent の supervisor に落とすのはプランの雛形が無いときだけ（development-workflow の references/agent-layers.md のフェーズの表）。この案内を止めるなら NDF_PLAN_HINT=0"}}


TOOLS = ("Bash", "Read", "Skill", "Agent", "Task")


def guard(ev: pl.Event) -> dict | None:
    raw, tool = ev.raw, ev.tool
    if tool == "Bash":
        return guard_context(raw, tool) or guard_sleep(raw)
    if tool == "Read":
        return guard_read(raw)
    if tool == "Skill":
        return guard_supervisor_cut(raw) or guard_context(raw, tool)
    if tool in ("Agent", "Task"):
        return guard_context(raw, tool) or plan_hint(raw)
    return None
