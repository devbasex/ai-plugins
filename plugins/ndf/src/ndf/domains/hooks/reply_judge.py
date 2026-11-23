"""軽いモデルへの判定のリクエスト（#4000 の決定 1・3・6・7・10・11）。

hook の閉包の中にあるので、標準ライブラリと同じパッケージの `tables` だけを読む。軽いモデルの起動は hook の外の
子プロセス（`ndf.adapters.reply_judge`）が行い、ここはその子を新しいプロセスの組で起動して秒の上限で待つ。

- `judge_text`: コードブロックの行と空行を除いた本文の末尾（設定の字数）を、質問へ渡す文にする（決定 7）
- `parent_launch`: 送信先の確かめの hook の側。起動の形と親の Claude Code の起動引数を読む（決定 11）
- `ask`: 判定の子を起動して待ち、どの終わり方でも組ごと止めて回収する（決定 3・6）
- `resting` / `mark_rest`: 起動が続けて失敗する環境で、休む間だけ子を起動しないためのファイル（決定 10）

設定（モデル・秒・字数・休む間・送信先の確かめの値）は `limits.json` の `hook.stop-wait.judge` から `_judge` が読む。
"""

from __future__ import annotations

import fnmatch
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional

from ndf.domains.hooks import tables

# 判定の子のモジュール（hook の閉包の外。import しない）
CHILD_MODULE = "ndf.adapters.reply_judge"
# 休みのマーカーのファイルの名前（状態の保存先の hook の保存先）
REST_NAME = "judge-rest.json"
# 組へ SIGTERM を送った後、SIGKILL までに待つ秒
GROUP_GRACE = 0.2
# 子が返してよい判定
KINDS = ("wait", "report")


class Launch(NamedTuple):
    """親の Claude Code の起動引数とプロジェクトの根。`ask` が子の標準入力へ渡す。"""

    argv: list[str]
    project_dir: str


class Judged(NamedTuple):
    """判定の結果。回答が得られなければ `kind` が None で `failure` が種類を持つ。"""

    kind: Optional[str]
    failure: Optional[str]
    ms: float
    calls: int


def _judge(key: str) -> Any:
    """判定の設定（`limits.json` の `hook.stop-wait.judge`）の 1 つの値。"""
    return tables.policy_value("hook.stop-wait.judge", key)


def judge_text(text: str, code_lines: list[bool], max_chars: int) -> str:
    """コードブロックの行と空行を除いた本文。`max_chars` を超えれば末尾の `max_chars` 字（決定 7）。"""
    lines = text.splitlines()
    flags = code_lines if len(code_lines) == len(lines) else [False] * len(lines)
    prose = "\n".join(line for line, code in zip(lines, flags) if not code and line.strip())
    return prose if len(prose) <= max_chars else prose[-max_chars:]


def _proc_file(pid: int, name: str) -> Path:
    return Path("/proc") / str(pid) / name


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _readlink(path: Path) -> str:
    """リンクの指す実パス（`/proc/<pid>/exe`）。"""
    return os.path.realpath(os.readlink(path))


def _ppid(read: Callable[[Path], bytes], pid: int) -> int:
    """`/proc/<pid>/stat` の親のプロセスの番号。実行ファイルの名前は括弧を含むので、最後の括弧の後ろから読む。"""
    fields = read(_proc_file(pid, "stat")).decode("utf-8", "replace").rpartition(")")[2].split()
    if len(fields) < 2:
        raise OSError(f"/proc/{pid}/stat の形が違う")
    return int(fields[1])


def _cmdline(read: Callable[[Path], bytes], pid: int) -> list[str]:
    """`/proc/<pid>/cmdline` の起動引数（NUL 区切り）。"""
    raw = read(_proc_file(pid, "cmdline")).decode("utf-8", "replace")
    return [arg for arg in raw.split("\0") if arg]


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _is_claude(exe: str, cmdline: list[str], endpoint: Mapping[str, Any]) -> bool:
    """Claude Code の実プロセスか。実行ファイルと起動の形だけで決め、引数の中身では決めない（決定 11）。"""
    if _matches(exe, endpoint["cli_exe"]):
        return True
    if not os.path.basename(exe).startswith("node") or len(cmdline) < 2:
        return False
    return _matches(os.path.realpath(cmdline[1]), endpoint["cli_script"])


def _claude_argv(pid: int, endpoint: Mapping[str, Any], read: Callable[..., bytes], readlink: Callable[..., str]) -> Optional[list[str]]:
    """hook のプロセスの親からたどり、最も近い Claude Code の起動引数を返す。見つからなければ None。"""
    current = pid
    for _ in range(int(endpoint["ancestors"])):
        try:
            current = _ppid(read, current)
            cmdline = _cmdline(read, current)
            found = _is_claude(readlink(_proc_file(current, "exe")), cmdline, endpoint)
        except (OSError, ValueError):  # noqa: NDF401 ファイル: /proc を読めない環境（macOS を含む）では親を確かめない
            return None
        if found:
            return cmdline
    return None


def parent_launch(
    env: Mapping[str, str],
    pid: int,
    read: Callable[..., bytes] = _read_bytes,
    readlink: Callable[..., str] = _readlink,
) -> Optional[Launch]:
    """送信先の確かめの hook の側（決定 11）。親の Claude Code の起動引数とプロジェクトの根、確かめられなければ None。

    起動の形（`CLAUDE_CODE_ENTRYPOINT`）・`CLAUDE_PROJECT_DIR`・`CLAUDE_CONFIG_DIR` を見てから `/proc` をたどる。
    引数の中身（`--settings` など）はここでは見ない（判定の子の `endpoint_refusal` が実効の設定で確かめる）。
    """
    endpoint = _judge("endpoint")
    project_dir = env.get("CLAUDE_PROJECT_DIR")
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    comparable = (
        env.get("CLAUDE_CODE_ENTRYPOINT") in endpoint["entrypoints"]
        and bool(project_dir)
        and os.path.isabs(project_dir)
        and (config_dir is None or os.path.isabs(config_dir))
    )
    if not comparable:
        return None
    argv = _claude_argv(pid, endpoint, read, readlink)
    return Launch(argv=argv, project_dir=str(project_dir)) if argv else None


def _child_env(env: Mapping[str, str]) -> dict[str, str]:
    """子へ渡す環境。環境変数の表の `child` が `drop` の行を外し、`PYTHONPATH` と思考の token の上限を入れる。"""
    drop = {row["id"] for row in tables.load("domains/env.json")["rows"] if row.get("child") == "drop"}
    child = {name: value for name, value in env.items() if name not in drop}
    child["PYTHONPATH"] = str(tables.PLUGIN_ROOT / "src")
    child["MAX_THINKING_TOKENS"] = "0"
    return child


def _ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _signal_group(pid: int, sig: int, killpg: Callable[[int, int], None]) -> None:
    """プロセスの組へ 1 つ送る。組が先に終わっていれば送る相手がいない。"""
    try:
        killpg(pid, sig)
    except (ProcessLookupError, PermissionError):  # noqa: S110 外部コマンド: 組が先に終わっていれば送る相手がいない
        pass


def _stop_group(proc: Any, killpg: Callable[[int, int], None]) -> None:
    """組へ SIGTERM を送り、`GROUP_GRACE` 秒で終わらなければ SIGKILL を送って回収する。"""
    _signal_group(proc.pid, signal.SIGTERM, killpg)
    try:
        proc.wait(timeout=GROUP_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL, killpg)
        proc.wait()


def _judged(out: str, code: int, ms: float) -> Judged:
    """子の 1 行の JSON を判定の結果にする。形が違えば `shape`。"""
    if code != 0:
        return Judged(None, "exit", ms, 0)
    answer = tables.json_object(out)
    if answer is None:
        return Judged(None, "shape", ms, 0)
    raw_calls = answer.get("calls")
    calls = raw_calls if isinstance(raw_calls, int) else 0
    failure = answer.get("failure")
    if isinstance(failure, str) and failure:
        return Judged(None, failure, ms, calls)
    kind = answer.get("kind")
    return Judged(kind, None, ms, calls) if kind in KINDS else Judged(None, "shape", ms, calls)


def _awaited(proc: Any, payload: str, seconds: float, started: float, killpg: Callable[[int, int], None]) -> Judged:
    """子の回答を待つ。どの終わり方でも組を止めて回収し、割り込みは投げ直す。"""
    try:
        out, _ = proc.communicate(payload, timeout=seconds)
    except subprocess.TimeoutExpired:
        _stop_group(proc, killpg)
        return Judged(None, "timeout", _ms(started), 0)
    except BaseException:
        _stop_group(proc, killpg)
        raise
    _signal_group(proc.pid, signal.SIGKILL, killpg)
    return _judged(out, proc.returncode, _ms(started))


def ask(
    text: str,
    seconds: float,
    env: Mapping[str, str],
    launch: Launch,
    popen: Callable[..., Any] = subprocess.Popen,
    killpg: Callable[[int, int], None] = os.killpg,
) -> Judged:
    """判定の子を新しいプロセスの組で起動し、`seconds` で待って回答を読む（決定 3）。

    子は `claude` を同じ組で起動するので、組への送信が `claude` まで届く。時間切れ・割り込み・正常な終わりの
    どれでも組を止めて回収する。回答が得られなければ `kind` が None の `Judged` を返す（決定 6）。
    """
    started = time.monotonic()
    payload = json.dumps(
        {"text": text, "seconds": seconds, "parent_argv": list(launch.argv), "project_dir": launch.project_dir},
        ensure_ascii=False,
    )
    try:
        proc = popen(
            [sys.executable, "-P", "-m", CHILD_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_child_env(env),
            cwd=str(tables.PLUGIN_ROOT),
            text=True,
        )
    except OSError:
        # 外部コマンド: 子を起動できない環境では文字列の判定へ戻す（決定 6）
        return Judged(None, "launch", _ms(started), 0)
    return _awaited(proc, payload, seconds, started, killpg)


def rest_path(state_dir: Path) -> Path:
    """休みのマーカーのファイル。"""
    return tables.hooks_dir(state_dir) / REST_NAME


def resting(state_dir: Path, now: float) -> bool:
    """起動の失敗から休む間が過ぎていなければ真。マーカーが無い・読めないときは偽（決定 10）。"""
    try:
        marker = tables.json_object(rest_path(state_dir).read_text(encoding="utf-8"))
    except OSError:  # noqa: NDF401 ファイル: マーカーが無い・読めないときは休まない
        return False
    if marker is None:
        return False
    at = marker.get("at")
    if not isinstance(at, (int, float)):
        return False
    return now - float(at) < float(_judge("rest_seconds"))


def mark_rest(state_dir: Path, failure: str, now: float) -> None:
    """起動の失敗と送信先の不一致のときだけ休みのマーカーを書く。書けなくても例外を投げない（決定 10）。"""
    # 起動の失敗（launch・refused・usage_limit・auth）と送信先の不一致（endpoint）。時間切れとスロットの空き待ちは書かない
    rest_failures = ("launch", "refused", "usage_limit", "auth", "endpoint")
    if failure not in rest_failures:
        return
    path = rest_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": now, "failure": failure}), encoding="utf-8")
    except OSError:  # noqa: S110 ファイル: マーカーを書けなくても判定は変えない
        pass
