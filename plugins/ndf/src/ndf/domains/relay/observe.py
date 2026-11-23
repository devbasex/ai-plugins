"""ラッパーが子の画面と会話の記録から読む事実（#3243・#2595・#3337）。

- 画面の末尾: 利用上限の行（`ndf.adapters.agents.usage_limit`）と、認証の失敗の引き金の行
- 会話の記録（`<CLAUDE_CONFIG_DIR か ~/.claude>/projects/<作業ディレクトリのキー>/<セッション>.jsonl`）の末尾:
  最後の応答の文脈の量（入力・キャッシュの読み・キャッシュの作成の token の和）と、止めてよい時点の事実
  （道具の実行の途中か・応答が終わったか。#3864 の決定 11）。道具の実行中は会話の行の最後が `stop_reason: tool_use` の
  `assistant` の `tool_use` のままで、終わると `user` の `tool_result` が同じ `tool_use_id` で続く。ファイルの最後の行は
  会話の行でないことがある（`last-prompt` など。#4018 の確かめ 3）。認証の失敗は会話の行の最後の API の失敗の記録で確かめる
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from ndf.adapters import agents, claude
from ndf.domains.relay import signals

# 認証の失敗の引き金（Claude Code 2.1.330 の端末の実測。#3864 の判断 13）。これだけでは替えず、会話の記録で確かめる。
# API キーの打ち直しの間の `401 invalid x-api-key · retrying …` は当てず、打ち直しが尽きた後の `Invalid API key` を待つ
AUTH_FAILED = re.compile(r"Failed to authenticate|Invalid API key|Login expired|Not logged in", re.IGNORECASE)
# 資格情報そのものの失敗を示す API の失敗の記録の欄（`error`・`apiErrorStatus`。403 の権限の制限は当てない。#3864 の決定 6）
CREDENTIAL_ERROR = "authentication_failed"
CREDENTIAL_STATUS = 401
# 行を替えるカーソルの移動と復帰（`CSI … A/B/E/F/H/d/f`・`\r`）。Claude Code は応答の行を改行でなくこれで描くので、
# 外す前に改行へ置き換える（置き換えないと上限の行が行頭に来ず、`^` で照らす文言に当たらない。#4227）
CURSOR_LINE = re.compile(r"\x1b\[[0-9;]*[ABEFHdf]|\r")
# 右へのカーソルの移動（`CSI … C`）。字の間を空けるのに使うので空白 1 つにする
CURSOR_FORWARD = re.compile(r"\x1b\[[0-9]*C")
# 画面の制御の列（色・カーソル）。文言を探す前に外す
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# 行頭の字下げと罫線（画面のスロット・応答のマーカー ⎿ など）。利用上限の行は行頭で照らすので外す
LEAD = re.compile(r"^[^\w{]+", re.MULTILINE)
# 背景の作業の `id` がファイル名に使える形（記録の保存先の外を指すパスを組まない。#4036）
TASK_ID = re.compile(r"[A-Za-z0-9_-]+")
# Bash の Tool のシェルの `cmdline` が持つ目印（`claude` の直接の子の `bash -c source <設定ディレクトリ>/shell-snapshots/snapshot-…`。
# Claude Code 2.1.330 で背景の呼び出しも同じ形と実測した。#4036）。設定ディレクトリは既定の `~/.claude` のほか、
# 登録済みアカウントの子では NDF が渡す `CLAUDE_CONFIG_DIR` なので、ディレクトリ名に依存しない
SHELL_SNAPSHOT = "/shell-snapshots/snapshot-"
ENDED = ("end_turn", "stop_sequence")  # 応答が終わった `stop_reason`
USAGE_KEYS = claude.CONTEXT_FIELDS


def plain(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return ANSI.sub("", CURSOR_FORWARD.sub(" ", CURSOR_LINE.sub("\n", text)))


def limit_line(screen: str) -> Optional[str]:
    return agents.usage_limit(LEAD.sub("", screen))


def auth_failed(screen: str) -> bool:
    return AUTH_FAILED.search(screen) is not None


def transcript_path(environ: Mapping[str, str], cwd: Path, session_id: str) -> Path:
    key = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return signals.claude_config_root(environ) / "projects" / key / f"{session_id}.jsonl"


def subagent_transcript_path(transcript: Path, agent_id: str) -> Optional[Path]:
    """セッションの会話の記録の隣の、背景のサブエージェントの会話の記録。`agent_id` がファイル名に使えなければ None。"""
    if not TASK_ID.fullmatch(agent_id):
        return None
    return transcript.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"


def shell_output_path(environ: Mapping[str, str], cwd: Path, session_id: str, task_id: str) -> Optional[Path]:
    """背景のシェルの出力のファイル（`<一時の保存先>/claude-<uid>/<作業ディレクトリのキー>/<session_id>/tasks/<id>.output`）。

    一時の保存先は `CLAUDE_CODE_TMPDIR`、無ければ `/tmp`（Claude Code 2.1.330 の実測）。`task_id` がファイル名に使えなければ None。
    """
    if not TASK_ID.fullmatch(task_id):
        return None
    key = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    root = Path(environ.get("CLAUDE_CODE_TMPDIR") or "/tmp")  # noqa: S108 Claude Code が置く場所を読むだけで、ここでは作らない
    return root / f"claude-{os.getuid()}" / key / session_id / "tasks" / f"{task_id}.output"


def process_parents(proc: Path = Path("/proc")) -> Optional[dict[int, int]]:
    """`/proc` のプロセスごとの親 PID（`/proc/<pid>/stat`）。`/proc` を読めなければ None。

    レーンの判定の読み直しごとに 1 度だけ組み、各レーンの `live_shells` で共有する（レーンごとに全体を読み直さない）。
    走査の間に終わったプロセスと、`stat` を読めないプロセス（`hidepid=1` の他のユーザーのもの。proc(5)）は載せない。
    """
    try:
        entries = list(proc.iterdir())
    except OSError:  # noqa: NDF401 ファイル: /proc を読めなければシェルの生死を確かめられない（呼び手が一覧のシェルを数える）
        return None
    parents: dict[int, int] = {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):  # 終わった・他のユーザーのプロセスは数えない
            continue
        fields = stat.rpartition(")")[2].split()  # `pid (comm) state ppid …`。comm は括弧や空白を含み得る
        if len(fields) >= 2 and fields[1].isdigit():
            parents[int(entry.name)] = int(fields[1])
    return parents


def live_shells(wrapper_pid: int, parents: Optional[Mapping[int, int]], proc: Path = Path("/proc")) -> Optional[int]:
    """ラッパーの子の `claude` の子のうち、Bash の Tool のシェルの数。

    `parents` は `process_parents` の返り（None なら None）。ラッパーか `claude` が見つからない・`cmdline` を読めなければ None。
    子は親 PID で絞る（`task/<tid>/children` は読む間に子が終わると、生きている別の子も欠け得る。proc_tid_children(5)）。
    """
    if parents is None or wrapper_pid not in parents:
        return None
    try:
        conductor = next((c for c in _children(parents, wrapper_pid) if _name(_args(proc, c)) == "claude"), None)
        if conductor is None:
            return None
        return sum(1 for c in _children(parents, conductor) if _is_shell(_args(proc, c)))
    except OSError:  # noqa: NDF401 ファイル: cmdline を読めなければシェルの生死を確かめられない（呼び手が一覧のシェルを数える）
        return None


def _children(parents: Mapping[int, int], pid: int) -> list[int]:
    return sorted(c for c, p in parents.items() if p == pid)


def _args(proc: Path, pid: int) -> list[str]:
    """`cmdline` の引数。読む前か読む間にプロセスが終わっていれば空（後者は `ESRCH`）。"""
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):  # 子を数える間に終わったプロセスは数えない
        return []
    return raw.decode("utf-8", errors="replace").split("\0")[:-1]


def _name(args: list[str]) -> str:
    return Path(args[0]).name if args else ""


def _is_shell(args: list[str]) -> bool:
    return _name(args) == "bash" and any(SHELL_SNAPSHOT in a for a in args[1:])


def context_tokens(path: Path, size: int) -> Optional[int]:
    """会話の記録の末尾 `size` バイトから、最後の応答の文脈の量。読めなければ None。"""
    if not path.is_file():
        return None
    for line in reversed(agents.tail(path, size).splitlines()):
        total = _usage_total(line)
        if total is not None:
            return total
    return None


def _usage_total(line: str) -> Optional[int]:
    try:
        row = json.loads(line)
    except ValueError:  # noqa: NDF401 ファイル: 末尾で切れた行は数えない
        return None
    message = row.get("message") if isinstance(row, dict) else None
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    return sum(int(usage[k]) for k in USAGE_KEYS if isinstance(usage.get(k), int))


class _Unsure(ValueError):
    """止めてよい時点を確定できない（記録を読めない・完全な行が壊れている・会話の記録の形が違う）。"""


def _lines_backward(path: Path, size: int) -> Iterator[str]:
    """末尾 `size` バイトの完全な行を新しい順に。行の境は JSONL の改行で決め、範囲の端で切れた行だけを飛ばす。

    範囲の始まりがファイルの先頭でなければ最初の行は切れている。改行で終わらない最後の行は書きかけである。
    読めなければ `_Unsure`（無いファイルは呼ぶ側が先に調べる）。
    """
    try:
        with open(path, "rb") as fh:
            begin = max(0, os.fstat(fh.fileno()).st_size - size)
            fh.seek(begin)
            data = fh.read()
    except OSError as exc:
        raise _Unsure(str(exc)) from exc
    lo = 0
    if begin > 0:
        cut = data.find(b"\n")
        lo = len(data) if cut < 0 else cut + 1
    hi = data.rfind(b"\n")
    while hi >= lo:
        prev = max(data.rfind(b"\n", lo, hi), lo - 1)  # 範囲内に改行が無ければ最初の完全な行は lo から
        yield data[prev + 1 : hi].decode("utf-8", errors="replace")
        hi = prev


def _talk(path: Path, size: int) -> Optional[list[dict[str, Any]]]:
    """末尾から読み、最後の会話の記録（`user`・`assistant`）と、最後の `assistant` から後ろの会話の記録を古い順に。

    最後の `assistant` を読んだところで読むのを止める。ファイルが無ければ None。会話でない行は飛ばし、
    読めない・完全な行が JSON でない・形の違う会話の記録（`message` が object でない）は `_Unsure`。
    """
    if not path.is_file():
        return None
    rows: list[dict[str, Any]] = []
    for line in _lines_backward(path, size):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise _Unsure("壊れた行") from exc
        if not isinstance(row, dict) or row.get("type") not in ("user", "assistant"):  # 会話の記録だけ
            continue
        if not isinstance(row.get("message"), dict):
            raise _Unsure(str(row.get("type")))
        rows.append(row)
        if row["type"] == "assistant":
            break
    rows.reverse()
    return rows


def _blocks(row: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    content = row["message"].get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind] if isinstance(content, list) else []


def _pending(rows: list[dict[str, Any]]) -> Optional[bool]:
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["type"] == "assistant"), None)
    if last is None:
        return None
    ids = {b.get("id") for b in _blocks(rows[last], "tool_use")}
    done = {b.get("tool_use_id") for r in rows[last + 1 :] if r["type"] == "user" for b in _blocks(r, "tool_result")}
    return bool(ids - done)


def credential_failure(path: Path, size: int, since: float) -> Optional[bool]:
    """会話の行の最後が資格情報の失敗の API の失敗の記録で、その時刻が `since` より後か（#3864 の決定 6）。

    最後の会話の行だけで決める（後ろに利用者の記録があれば当たらない。打ち直しの `system` の記録は読まない）。
    `error` が `authentication_failed` か、そうでなく `apiErrorStatus` が 401 なら当たる。無い・壊れた行・形が違えば偽。
    ファイルを開けない・読めなければ None（権限の直しなどスタンプの変わらない回復があるので、呼ぶ側は結果を残さず次に読み直す）。
    """
    try:
        rows = _talk(path, size)
    except _Unsure as exc:  # 確かめられない記録では替えない（偽か、読み直す None）
        return None if isinstance(exc.__cause__, OSError) else False
    if not rows:
        return False
    last = rows[-1]
    if last["type"] != "assistant" or last.get("isApiErrorMessage") is not True:
        return False
    if last.get("error") != CREDENTIAL_ERROR and last.get("apiErrorStatus") != CREDENTIAL_STATUS:
        return False
    at = _time(last.get("timestamp"))
    return at is not None and at > since


def api_error_since(path: Path, size: int, since: Optional[float]) -> bool:
    """会話の行の最後が API の失敗の記録（`isApiErrorMessage` が真の `assistant`）で、その時刻が `since` より後か。

    読めない・確かめられない記録は偽（呼び手は止めずに待つ。#3864 の決定 12 の `_end_point`）。
    """
    try:
        rows = _talk(path, size)
    except _Unsure:  # noqa: NDF401 ファイル: 確かめられない記録では止めずに待つ
        return False
    if not rows:
        return False
    last = rows[-1]
    at = _time(last.get("timestamp"))
    return last["type"] == "assistant" and last.get("isApiErrorMessage") is True and at is not None and (since is None or at > since)


def tool_pending(path: Path, size: int) -> Optional[bool]:
    """最後の `assistant` の記録の `tool_use` に対応する `tool_result` が後ろに無いか。読めない・壊れた・形が違えば None。"""
    try:
        rows = _talk(path, size)
    except _Unsure:  # noqa: NDF401 ファイル: 確定できない記録は止めてよい時点にしない（None）
        return None
    return None if rows is None else _pending(rows)


def turn_done(path: Path, size: int) -> Optional[tuple[bool, Optional[float]]]:
    """最後の会話の記録が終わった応答なら `(True, その時刻)`、途中なら `(False, None)`。読めない・壊れた・形が違えば None。"""
    try:
        rows = _talk(path, size)
    except _Unsure:  # noqa: NDF401 ファイル: 確定できない記録は止めてよい時点にしない（None）
        return None
    if not rows:
        return None
    last = rows[-1]
    if last["type"] != "assistant" or last["message"].get("stop_reason") not in ENDED or _pending(rows):
        return (False, None)
    return (True, _time(last.get("timestamp")))


def _time(value: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:  # noqa: NDF401 ファイル: 時刻の読めない記録は時刻を持たない
        return None
