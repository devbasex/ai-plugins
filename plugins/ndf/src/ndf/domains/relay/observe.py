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
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional

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
    return shell_output_in(shell_output_dir(environ, cwd, session_id), task_id)


def shell_output_dir(environ: Mapping[str, str], cwd: Path, session_id: str) -> Path:
    """背景のシェルの出力のファイルを置くディレクトリ（`shell_output_path` の `<id>.output` の親）。"""
    key = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    root = Path(environ.get("CLAUDE_CODE_TMPDIR") or "/tmp")  # noqa: S108 Claude Code が置く場所を読むだけで、ここでは作らない
    return root / f"claude-{os.getuid()}" / key / session_id / "tasks"


def shell_output_in(directory: Path, task_id: str) -> Optional[Path]:
    """`shell_output_dir` の返り `directory` の中の、背景のシェルの出力のファイル。`task_id` がファイル名に使えなければ None。"""
    return directory / f"{task_id}.output" if TASK_ID.fullmatch(task_id) else None


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
        ppid = _parent_of(entry) if entry.name.isdigit() else None
        if ppid is not None:
            parents[int(entry.name)] = ppid
    return parents


def _parent_of(entry: Path) -> Optional[int]:
    """`/proc/<pid>/stat` の親 PID。終わった・他のユーザーのプロセス（読めない `stat`）は None。"""
    try:
        stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError):  # noqa: NDF401 ファイル: 終わった・他のユーザーのプロセスは数えない
        return None
    fields = stat.rpartition(")")[2].split()  # `pid (comm) state ppid …`。comm は括弧や空白を含み得る
    return int(fields[1]) if len(fields) >= 2 and fields[1].isdigit() else None


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


# --- conductor の活動（#4036）。`ndf relay ls --activity` の行に載せ、計画の実行が「何もしていない conductor」を判定する ---

# Stop の hook の入力の `background_tasks` の要素の `type`
SUBAGENT_TASK, SHELL_TASK = "subagent", "shell"
# 活動を読めない行の値
NO_ACTIVITY = {"turn_done": None, "moved": None, "background": None}
# 出力のファイルを読めない、終わった背景のシェルを最初に確かめた時刻（ラッパーの保存先。`ndf relay ls --activity` が書く）
SHELLS_ENDED_FILE = "shells-ended.json"


@dataclass(frozen=True)
class Look:
    """1 回の `ndf relay ls --activity` で全行が共有する活動の材料。`now` は読む時刻、`size` は会話の記録の末尾を読む大きさ、
    `parents` は `process_parents` の返りを返す口（シェルの生死を確かめる行に届いたときだけ呼ぶ。呼ぶ側が 1 回の一覧で
    1 度だけ組むように包む）。"""

    now: float
    size: int
    parents: Callable[[], Optional[Mapping[int, int]]]
    proc: Path = Path("/proc")


class _Turn(NamedTuple):
    """conductor の今の応答: 会話の記録のパス・背景のシェルの出力の保存先・セッション・Stop の記録の時刻・最後の応答の時刻。"""

    transcript: Path
    outputs: Path
    session_id: str
    stop_at: str
    at: float


def conductor_activity(row: Mapping[str, Any], directory: Path, look: Look) -> dict[str, Any]:
    """ラッパーの状態の値（`session_id`・`transcript`・`shell_outputs`・`pid`）とラッパーの保存先 `directory` から、conductor の活動を読む。

    会話の記録と出力の保存先はラッパーが自分の環境で組んで状態に書いた値を使う（一覧を呼ぶ側の `CLAUDE_CONFIG_DIR` に依らない）。
    返すのは `turn_done`（最後の応答が終わったか）・`moved`（最後の応答と、終わった背景の作業の時刻のうち最も遅いもの。
    UNIX 時刻）・`background`（終わった背景の作業を除いた数。確かめられなければ None）。conductor の会話の記録を
    読めなければ 3 つとも None。止まりと知らせる側へ倒さないよう、読めない背景の作業は走っているとみなす。
    """
    session, transcript, outputs = row.get("session_id"), row.get("transcript"), row.get("shell_outputs")
    if not isinstance(session, str) or not isinstance(transcript, str) or not isinstance(outputs, str):
        return dict(NO_ACTIVITY)
    got = turn_done(Path(transcript), look.size)
    if got is None:
        return dict(NO_ACTIVITY)
    done, at = got
    if not done or at is None:
        return {"turn_done": done, "moved": at, "background": None}
    record = signals.read_activity(directory)
    tasks = _tasks_of(record, session, at)
    if record is None or tasks is None:
        return {"turn_done": True, "moved": at, "background": None}
    turn = _Turn(Path(transcript), Path(outputs), session, record["at"], at)
    running, times = _others(turn, tasks, look.size)
    shells = _shells(turn, directory, row.get("pid"), [t for t in tasks if t["type"] == SHELL_TASK], look)
    moved = max([at, *times, *(shells[1] if shells is not None else [])])
    return {"turn_done": True, "moved": moved, "background": None if shells is None else running + shells[0]}


def _tasks_of(record: Optional[Mapping[str, Any]], session: str, at: float) -> Optional[list[dict[str, str]]]:
    """今の応答の Stop が書いた背景の作業の一覧。別のセッション・前の Stop の記録・観測できなかった一覧なら None。

    `at` は秒で切り捨てて書かれる（`signals.now_iso`）ので、最後の応答の時刻も秒に切り捨ててから比べる。
    """
    if record is None or record.get("session_id") != session or record.get("tasks") is None:
        return None
    written = _time(record["at"])
    return record["tasks"] if written is not None and written >= math.floor(at) else None


def _others(turn: _Turn, tasks: list[dict[str, str]], size: int) -> tuple[int, list[float]]:
    """シェルでない背景の作業の、走っているとみなす数と、終わったサブエージェントの最後の応答の時刻。"""
    running, times = 0, []
    for task in (t for t in tasks if t["type"] != SHELL_TASK):
        ended = _ended_subagent(turn.transcript, task["id"], size) if task["type"] == SUBAGENT_TASK else None
        if ended is None:
            running += 1
        else:
            times.append(ended)
    return running, times


def _ended_subagent(transcript: Path, agent_id: str, size: int) -> Optional[float]:
    """サブエージェントの最後の応答が終わっていればその時刻。記録を読めない・応答の途中なら None（走っているとみなす）。"""
    path = subagent_transcript_path(transcript, agent_id)
    got = turn_done(path, size) if path is not None else None
    return got[1] if got is not None and got[0] else None


def _shells(turn: _Turn, directory: Path, pid: Any, listed: list[dict[str, str]], look: Look) -> Optional[tuple[int, list[float]]]:
    """生きている背景のシェルの数と、一覧のシェルが終わった時刻。生死を確かめられなければ None。

    `/proc` はここに届いた行で初めて読む（`look.parents`）。一覧より生きているシェルが少なければ、一覧のシェルの出力の
    ファイルの更新時刻を終わった時刻とする。1 つも読めなければ、終わりを最初に確かめた時刻にする（`_first_seen`）。
    """
    live = live_shells(pid, look.parents(), look.proc) if isinstance(pid, int) and not isinstance(pid, bool) else None
    if live is None:
        return None
    if live >= len(listed):
        return live, []
    ended = [_mtime(shell_output_in(turn.outputs, t["id"])) for t in listed]
    return live, [t for t in ended if t is not None] or [_first_seen(directory, turn, len(listed) - live, look.now)]


def _first_seen(directory: Path, turn: _Turn, ended: int, now: float) -> float:
    """同じセッション・Stop の記録・終わったシェルの数について、シェルの終わりを最初に確かめた時刻。初めてなら `now` を残して返す。

    一覧を読むたびに `now` にすると無活動の時間が毎回 0 に戻り、止まりにならない。終わったシェルの数もキーに入れるのは、
    1 つ目が終わった時刻を 2 つ目の終わりに使い、長いシェルが終わった直後に止まりと読まないためである。
    残せなければ `now`（その読み直しでは止まりにしない側へ倒す）。
    """
    path = directory / SHELLS_ENDED_FILE
    key = {"session_id": turn.session_id, "stop_at": turn.stop_at, "ended": ended}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 無い・壊れた記録は初めて確かめたものとして書き直す
        data = None
    seen = data.get("seen") if isinstance(data, dict) and all(data.get(k) == v for k, v in key.items()) else None
    if isinstance(seen, (int, float)) and not isinstance(seen, bool):
        return float(seen)
    try:
        signals.replace_json(path, {**key, "seen": now})
    except OSError:  # 残せなければ読む時刻にする（次の読み直しが書き直す）
        return now
    return now


def _mtime(path: Optional[Path]) -> Optional[float]:
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:  # noqa: NDF401 ファイル: 出力のファイルを読めないシェルは終わった時刻が分からない（呼び手が最初に確かめた時刻にする）
        return None
