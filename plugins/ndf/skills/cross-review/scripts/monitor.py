#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review codex/gemini プロセス監視 CLI。

`launch-codex.sh` / `launch-gemini.sh` で起動したバックグラウンドプロセスを
**複数の根拠で多重監視** し、失敗パターン (sentinel 不在 / 早期エラー / ハング /
pidfile stale / result.json 不在) を構造化して扱う。

監視軸:
  1. **pidfile** + `kill -0` でプロセス生存確認
     - 可能なら `/proc/<pid>/cmdline` で codex/gemini であることを再確認 (PID 再利用対策)
  2. **sentinel** (codex のみ): err.log に `^tokens used$` 出現
  3. **early-error pattern**: err.log に既知の致命的キーワードが出たら即中断
     - **FATAL** (auth/quota/sandbox 等の明確な致命): 検知時に kill
     - **WARN** (生の `Error:` / `Traceback` 等の曖昧パターン): 警告ログのみ、kill せず通常判定を継続
     - `--no-early-error` / `MONITOR_NO_EARLY_ERROR=1` で検知自体を無効化可
  4. **result.json**: プロセス終了後に `/tmp/<agent>-review-pr<PR>-result.json` が
     生成されていなければ失敗扱い
  5. **hard timeout**: 既定 7 分。`--timeout` または `MONITOR_TIMEOUT` で上書き可
  6. **stall timeout**: err.log + stdout.log の合計サイズが一定時間変化しなければ
     STALLED として中断。既定は agent 別 (codex=180s, gemini=480s。gemini は err.log
     にほぼ進捗を出さないため大きめ)。`--stall-timeout` で CLI 明示、
     `MONITOR_STALL_<AGENT>` env で per-agent 上書き、`MONITOR_STALL` env で共通上書き可
  7. **失敗時 kill**: TIMEOUT / STALLED / EARLY_ERROR (FATAL のみ) / PIDFILE_BAD で
     返るとき、対象プロセスを SIGTERM (3 秒後に SIGKILL) で停止する

Usage:
  monitor.py <PR> <target>          target ∈ {codex, gemini, both}
  monitor.py <PR> both --timeout 1200 --stall-timeout 600
  monitor.py <PR> both --no-early-error    # EARLY_ERROR 検知を完全無効化

Exit codes (target=both は最悪値を返す):
  0  OK            プロセス正常終了 + result.json 確認
  1  USAGE / IO error
  2  TIMEOUT       hard timeout 超過
  3  NO_RESULT     プロセス終了したが result.json 未生成
  4  EARLY_ERROR   err.log に致命的パターン検出
  5  STALLED       err.log が一定時間進捗なし
  6  PIDFILE_BAD   pidfile が無い / 内容が不正 / プロセスが起動していない

Stdout: 各 agent の最終ステータスを JSON で 1 行ずつ吐く（メインがパース可能）。
Stderr: 人間向けの進捗ログ（poll ごとに 1 行）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional


# ---------- 設定 ----------

DEFAULT_TIMEOUT = int(os.environ.get("MONITOR_TIMEOUT", "420"))    # 7 min
# 既定 stall timeout (後方互換のため env MONITOR_STALL は残す)。
# 両 agent 共通のデフォルトとして引き続き受け付ける。
DEFAULT_STALL = int(os.environ.get("MONITOR_STALL", "180"))       # 3 min no progress
# per-agent 上書き: gemini は err.log がほぼ無音なため大きめに取る。
# 解決順は `_agent_stall_default()` 参照。
DEFAULT_STALL_AGENT_BUILTIN = {
    "codex": 180,    # 推論ログを逐次出すので 3 min で十分
    "gemini": 480,   # err.log が静かなため 8 min まで許容
}
DEFAULT_POLL = int(os.environ.get("MONITOR_POLL", "15"))          # 15 sec
# `MONITOR_NO_EARLY_ERROR=1` で EARLY_ERROR 検知を無効化 (escape hatch)
DEFAULT_NO_EARLY_ERROR = os.environ.get("MONITOR_NO_EARLY_ERROR", "").lower() in {
    "1", "true", "yes", "on",
}

# err.log の行頭に近い形で出る **明確な致命** パターン (kill 対象)。
# auth / quota / sandbox / HTTP 401-403-429 / gemini の YOLO 降格など、
# プロセスが続行しても result を生成できないと判明しているケースだけを入れる。
EARLY_ERROR_FATAL = [
    # HTTP エラーステータス行 (`HTTP/1.1 401 Unauthorized` 等)
    re.compile(r"^HTTP/\d\S* (?:401|403|429) ", re.MULTILINE),
    # gemini 固有: untrusted directory で YOLO が降格される
    re.compile(r'^Approval mode overridden to "default"', re.MULTILINE),
    # 認証 / 権限系（行頭限定）
    re.compile(r"^(?:Authentication failed|Permission denied)", re.MULTILINE),
    # quota / rate limit （`m.start()` をキーワード位置に合わせるため `^.*` を付けない。
    # `_match_is_quoted()` が backtick / 「」 引用を判定するために match 開始位置を使うため）
    re.compile(r"\b(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE),
    # API key 系
    re.compile(r"\bAPI key (?:not found|missing|invalid)\b", re.IGNORECASE),
    # codex 固有: sandbox エラー
    re.compile(r"\bsandbox error\b", re.IGNORECASE),
]

# 行頭の生 `Error:` / `Traceback` 系は **kill しない警告のみ** に降格。
# - gemini-cli の `Error in: mcpServers.<name>` 警告 (起動時の config 検証)
# - codex がレビュー対象 diff の test コード片を echo して `Traceback` が混入するケース
# など、続行可能な誤検知が頻発するため。プロセスは sentinel / result.json / timeout
# で別途判定する。
EARLY_ERROR_WARN = [
    re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE),
]

# 文脈に含まれていたら benign（doc 引用 / コードレビューコメント等）と見なし誤検知扱い。
# FATAL / WARN 双方のスキャンに適用される。
EARLY_ERROR_BENIGN = [
    # gemini-cli の config validation 警告 (`Error in: mcpServers.<name>` / `Error in: ...`):
    # 設定の Unrecognized キーを通知するだけで、gemini 本体は起動継続する。
    re.compile(r"^Error in: mcpServers\.", re.MULTILINE),
    re.compile(r"^Error in: \S+\s*$", re.MULTILINE),
    # diff のコンテキスト行 (` `, `+`, `-` で始まり、その後 markdown 表記が来る)
    re.compile(r"^[ +-].*[\|`]", re.MULTILINE),
    # markdown のリスト / 引用
    re.compile(r"^\s*[-*>]\s", re.MULTILINE),
    # markdown の表セル行 (`| ... | ...` 形式)。SKILL.md / docs/*.md が
    # 検知パターンを表で列挙しており、それを codex が echo すると誤検知する。
    re.compile(r"^\|", re.MULTILINE),
    # warning は致命ではない
    re.compile(r"^warning: ", re.IGNORECASE | re.MULTILINE),
]


def _match_is_quoted(line: str, match_start: int, match_end: int) -> bool:
    """マッチ位置がドキュメント引用 (backtick / 日本語「」) に囲まれているか判定。

    - backtick: マッチ開始までの `` ` `` カウントが奇数 かつ マッチ終了以降に `` ` `` がある
    - 日本語クォート: マッチ開始までに直近の `「` が `」` よりも後 かつ マッチ終了以降に `」` がある

    Why: SKILL.md / docs/*.md 内で FATAL キーワードを `「quota exceeded」` のように
    引用列挙しており、codex がそれを echo する。引用形は本物のエラーではない。
    """
    before = line[:match_start]
    after = line[match_end:]
    if before.count("`") % 2 == 1 and "`" in after:
        return True
    if before.rfind("「") > before.rfind("」") and "」" in after:
        return True
    return False

CODEX_SENTINEL = re.compile(r"^tokens used$", re.MULTILINE)


def _agent_stall_default(agent: str) -> int:
    """agent ごとの既定 stall timeout を解決する。

    解決順:
      1. env `MONITOR_STALL_<AGENT>` (per-agent 明示)
      2. env `MONITOR_STALL` (両 agent 共通)
      3. `DEFAULT_STALL_AGENT_BUILTIN[agent]` (codex=180, gemini=480)
      4. `DEFAULT_STALL` (フォールバック)

    Note (codex round 3 指摘): 2 は **呼び出し時** に `os.environ["MONITOR_STALL"]`
    を再評価する。`DEFAULT_STALL` モジュール定数は import 時に env を読むため
    プロセス起動後の env 変更に追随できず、テストの monkeypatch も効かない。
    """
    env_key = f"MONITOR_STALL_{agent.upper()}"
    if env_key in os.environ:
        return int(os.environ[env_key])
    if "MONITOR_STALL" in os.environ:
        return int(os.environ["MONITOR_STALL"])
    return DEFAULT_STALL_AGENT_BUILTIN.get(agent, DEFAULT_STALL)


def _tmp_dir() -> pathlib.Path:
    """cross-review 用 tmp ディレクトリ。

    state.py の `_tmp_dir()` と同じロジック。優先:
      1. `CROSS_REVIEW_TMP_DIR` env
      2. `~/.gemini/tmp/<cwd-basename>/` (`~/.gemini/tmp/` が存在するとき)
      3. `/tmp/` (フォールバック)
    """
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        d = pathlib.Path(env)
        d.mkdir(parents=True, exist_ok=True)
        return d
    base_name = pathlib.Path(os.getcwd()).name
    gemini_root = pathlib.Path.home() / ".gemini" / "tmp"
    if gemini_root.is_dir() and base_name:
        d = gemini_root / base_name
        d.mkdir(parents=True, exist_ok=True)
        return d
    return pathlib.Path("/tmp")


# ---------- データ型 ----------

@dataclass
class AgentPaths:
    agent: str
    pr: int
    pidfile: pathlib.Path
    err_log: pathlib.Path
    stdout_log: pathlib.Path
    result: pathlib.Path

    @classmethod
    def for_(cls, agent: str, pr: int) -> "AgentPaths":
        base = _tmp_dir() / f"{agent}-review-pr{pr}"
        return cls(
            agent=agent, pr=pr,
            pidfile=pathlib.Path(f"{base}.pid"),
            err_log=pathlib.Path(f"{base}-err.log"),
            stdout_log=pathlib.Path(f"{base}-stdout.log"),
            result=pathlib.Path(f"{base}-result.json"),
        )


@dataclass
class AgentStatus:
    agent: str
    status: str = "RUNNING"
    exit_code: int = 0
    pid: Optional[int] = None
    elapsed: float = 0.0
    detail: str = ""
    err_log_size: int = 0
    result_exists: bool = False
    sentinel_seen: bool = False


# ---------- 監視ロジック ----------

def _read_pidfile(p: pathlib.Path) -> Optional[int]:
    try:
        s = p.read_text().strip()
        return int(s) if s else None
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """`kill -0` 相当。0 シグナルを送って例外で判定。"""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _kill_pid(pid: int, sigterm_grace: float = 3.0) -> None:
    """対象プロセスに SIGTERM、`sigterm_grace` 秒後も生きていたら SIGKILL。

    TIMEOUT / STALLED / EARLY_ERROR で監視を打ち切るとき、対象プロセスが残ったまま
    だと後から `gh api` 投稿や result.json 書き込みを実行してメインフローと
    競合する。失敗扱いで返るときは必ず停止させる。
    """
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + sigterm_grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _pid_cmdline_matches(pid: int, expected: str) -> Optional[bool]:
    """`/proc/<pid>/cmdline` を読んで `expected` を含むか。

    /proc が読めない環境では None を返す（PID 再利用チェック非対応）。
    """
    try:
        cmdline = pathlib.Path(f"/proc/{pid}/cmdline").read_text()
        return expected.lower() in cmdline.lower()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _scan_patterns(
    path: pathlib.Path, patterns: list[re.Pattern[str]]
) -> Optional[str]:
    """err.log を末尾 200KB だけ読み、`patterns` の最初の **non-benign** ヒット行を返す。

    BENIGN フィルタは **マッチ行そのもの** に対して適用し、誤検知 (markdown 引用 /
    diff body / gemini の config validation 警告) を除外する。

    重要 1: `pat.search()` ではなく `pat.finditer()` を使い、benign で除外された場合も
    後続の一致を継続して走査する。これにより benign な先行ヒットの後ろにある本物の
    エラーを見逃さない。

    重要 2: benign 判定は「マッチ行」だけを対象にする。以前は前後 40 文字の文脈窓を
    使っていたが、それだと benign 行が直前にあるだけで後続の本物エラーを誤って benign
    扱いしてしまった (例: `Error in: mcpServers.serena\\n...\\nTraceback ...` で
    Traceback が誤抑制された)。
    """
    if not path.exists():
        return None
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > 200 * 1024:
                f.seek(sz - 200 * 1024)
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    for pat in patterns:
        for m in pat.finditer(data):
            line_start = data.rfind("\n", 0, m.start()) + 1
            line_end = data.find("\n", m.end())
            line_end = line_end if line_end != -1 else len(data)
            line = data[line_start:line_end]
            # benign パターンはマッチ行そのものに当てる。markdown 引用や
            # `Error in: mcpServers.X` のような行単位パターンは「その行」だけを
            # 評価すれば判定可能で、文脈窓を広げると誤判定の原因になる。
            if any(b.search(line) for b in EARLY_ERROR_BENIGN):
                continue
            # マッチ部位が backtick / 日本語「」 で引用されている場合も benign。
            if _match_is_quoted(line, m.start() - line_start, m.end() - line_start):
                continue
            return line.strip()
    return None


def _scan_early_fatal(path: pathlib.Path) -> Optional[str]:
    return _scan_patterns(path, EARLY_ERROR_FATAL)


def _scan_early_warn(path: pathlib.Path) -> Optional[str]:
    return _scan_patterns(path, EARLY_ERROR_WARN)


def _scan_codex_sentinel(path: pathlib.Path) -> bool:
    if not path.exists():
        return False
    try:
        # 末尾 64KB を読む（sentinel は最後の方に出る）
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > 64 * 1024:
                f.seek(sz - 64 * 1024)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(CODEX_SENTINEL.search(tail))


def monitor_agent(
    agent: str,
    pr: int,
    timeout: int,
    stall_timeout: int,
    poll: int,
    require_result: bool,
    no_early_error: bool = False,
    log_prefix: str = "",
) -> AgentStatus:
    """1 agent を監視する。

    `no_early_error=True` のとき、EARLY_ERROR 検知 (FATAL/WARN とも) を完全に無効化し、
    hard timeout / stall / sentinel / result.json のみで判定する。
    """
    paths = AgentPaths.for_(agent, pr)
    status = AgentStatus(agent=agent)
    started = time.monotonic()

    # 起動チェック: 30 秒待っても pidfile が無ければ起動失敗
    grace_end = started + 30
    while time.monotonic() < grace_end:
        if paths.pidfile.exists():
            break
        time.sleep(2)
    pid = _read_pidfile(paths.pidfile)
    if pid is None:
        status.status = "PIDFILE_BAD"
        status.exit_code = 6
        status.detail = f"pidfile not found: {paths.pidfile}"
        _emit_log(log_prefix, agent, status)
        return status

    status.pid = pid
    # cmdline 検証 (PID 再利用対策) は **プロセスが生きていると確認できたときのみ** 行う。
    # 起動直後に既にプロセスが exit していると /proc/<pid> が消えるか、別プロセスに
    # 再利用されている可能性があり、ここで PIDFILE_BAD を返すと「完了している（result.json は出ている）」
    # ケースを誤って失敗にしてしまう。alive=True と確認した瞬間のみ cmdline 一致を検証する。

    last_err_size = paths.err_log.stat().st_size if paths.err_log.exists() else 0
    last_progress = time.monotonic()
    cmdline_validated = False
    warned_early_error = False

    while True:
        elapsed = time.monotonic() - started
        status.elapsed = elapsed

        # 1. プロセス生存確認 → 死んでいたら最終判定へ (result.json 存在をチェック)
        alive = _pid_alive(pid)
        if agent == "codex":
            status.sentinel_seen = _scan_codex_sentinel(paths.err_log)

        # codex は `tokens used` sentinel を出した後もプロセスが exit せず常駐し続ける
        # ケースがある (実機で観測)。result.json は正常に書かれているのに alive=True の
        # まま stall_timeout に達して STALLED 化してしまう。sentinel + result.json が
        # 揃った瞬間に対象プロセスを kill して OK 判定で返す。
        if (
            agent == "codex"
            and alive
            and status.sentinel_seen
            and paths.result.exists()
            and paths.result.stat().st_size > 0
        ):
            _kill_pid(pid)
            status.result_exists = True
            status.status = "OK"
            status.exit_code = 0
            status.detail = (
                f"codex sentinel + result.json detected; killed lingering pid {pid}"
            )
            _emit_log(log_prefix, agent, status)
            return status

        if alive and not cmdline_validated:
            # cmdline 検証は alive 確認後に 1 回だけ。生きていない瞬間に proc/<pid> を読むと
            # ファイル不在で None 扱いになり判定不能のため。
            cmdline_ok = _pid_cmdline_matches(pid, agent)
            if cmdline_ok is False:
                _kill_pid(pid)
                status.status = "PIDFILE_BAD"
                status.exit_code = 6
                status.detail = f"pid {pid} cmdline does not contain '{agent}' (stale pidfile?)"
                _emit_log(log_prefix, agent, status)
                return status
            if cmdline_ok is True:
                cmdline_validated = True

        # 2. hard timeout
        if elapsed >= timeout:
            if alive:
                _kill_pid(pid)
            status.status = "TIMEOUT"
            status.exit_code = 2
            status.detail = f"hard timeout {timeout}s reached (pid {pid})"
            _emit_log(log_prefix, agent, status)
            return status

        # 3. early error
        # 明確な致命 (FATAL) のみ kill する。曖昧パターン (生 Error: / Traceback) は
        # WARN として警告ログのみ。codex がレビュー対象 diff の test コード片を
        # echo するケースや gemini の config validation 警告で誤 kill されるのを防ぐ。
        if not no_early_error:
            fatal_err = _scan_early_fatal(paths.err_log)
            if fatal_err:
                if alive:
                    _kill_pid(pid)
                status.status = "EARLY_ERROR"
                status.exit_code = 4
                status.detail = f"early error (fatal) in err.log: {fatal_err[:200]}"
                _emit_log(log_prefix, agent, status)
                return status

            if not warned_early_error:
                warn_err = _scan_early_warn(paths.err_log)
                if warn_err:
                    print(
                        f"{log_prefix}⚠️  {agent} early-error WARN "
                        f"(non-fatal, not killing): {warn_err[:200]}",
                        file=sys.stderr, flush=True,
                    )
                    warned_early_error = True

        if not alive:
            # プロセス終了 — result.json を確認
            status.result_exists = paths.result.exists() and paths.result.stat().st_size > 0
            if status.result_exists or not require_result:
                status.status = "OK"
                status.exit_code = 0
                status.detail = (
                    f"process exited; sentinel={status.sentinel_seen}; "
                    f"result_exists={status.result_exists}"
                )
            else:
                status.status = "NO_RESULT"
                status.exit_code = 3
                status.detail = f"process exited but result.json missing: {paths.result}"
            _emit_log(log_prefix, agent, status)
            return status

        # 4. stall detection (err.log と stdout.log の **両方** をモニタ。
        # gemini は stdout 側だけ進捗が出るケースがあるため、片方でも更新があれば
        # progress として扱う)
        progress_size = 0
        for p in (paths.err_log, paths.stdout_log):
            if p.exists():
                progress_size += p.stat().st_size
        status.err_log_size = progress_size
        if progress_size != last_err_size:
            last_err_size = progress_size
            last_progress = time.monotonic()
        if (time.monotonic() - last_progress) >= stall_timeout:
            if alive:
                _kill_pid(pid)
            status.status = "STALLED"
            status.exit_code = 5
            status.detail = (
                f"no log progress for {stall_timeout}s "
                f"(pid {pid}, last size {last_err_size}B)"
            )
            _emit_log(log_prefix, agent, status)
            return status

        # poll 中の進捗ログ
        _emit_progress(log_prefix, agent, status, last_err_size)
        time.sleep(poll)


def _emit_progress(prefix: str, agent: str, st: AgentStatus, log_size: int) -> None:
    print(
        f"{prefix}⏳ {agent} elapsed={st.elapsed:.0f}s pid={st.pid} "
        f"err_log={log_size}B sentinel={'Y' if st.sentinel_seen else '-'}",
        file=sys.stderr, flush=True,
    )


def _emit_log(prefix: str, agent: str, st: AgentStatus) -> None:
    icon = {
        "OK": "✅", "TIMEOUT": "⏰", "NO_RESULT": "❌",
        "EARLY_ERROR": "💥", "STALLED": "🛑", "PIDFILE_BAD": "❓",
    }.get(st.status, "?")
    print(
        f"{prefix}{icon} {agent} {st.status} ({st.elapsed:.0f}s) — {st.detail}",
        file=sys.stderr, flush=True,
    )


# ---------- CLI ----------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pr", type=int)
    p.add_argument("target", choices=["codex", "gemini", "both"])
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"hard timeout in seconds (default: {DEFAULT_TIMEOUT})")
    p.add_argument("--stall-timeout", type=int, default=None,
                   help="stall timeout (err.log no progress) in seconds. "
                        "未指定時は agent 別既定 (codex=180, gemini=480) または "
                        "env MONITOR_STALL_<AGENT> / MONITOR_STALL を参照")
    p.add_argument("--poll", type=int, default=DEFAULT_POLL,
                   help=f"poll interval in seconds (default: {DEFAULT_POLL})")
    p.add_argument("--no-require-result", action="store_true",
                   help="プロセス終了後に result.json が無くても OK 扱い")
    p.add_argument("--no-early-error", action="store_true",
                   default=DEFAULT_NO_EARLY_ERROR,
                   help="EARLY_ERROR 検知を無効化 "
                        "(hard timeout / stall / sentinel / result.json のみで判定) "
                        f"[env: MONITOR_NO_EARLY_ERROR; default: {DEFAULT_NO_EARLY_ERROR}]")
    args = p.parse_args()

    agents = ["codex", "gemini"] if args.target == "both" else [args.target]
    require_result = not args.no_require_result

    results: dict[str, AgentStatus] = {}

    def run(agent: str) -> None:
        stall = args.stall_timeout if args.stall_timeout is not None \
            else _agent_stall_default(agent)
        results[agent] = monitor_agent(
            agent=agent, pr=args.pr,
            timeout=args.timeout, stall_timeout=stall,
            poll=args.poll, require_result=require_result,
            no_early_error=args.no_early_error,
            log_prefix=f"[{agent}] ",
        )

    threads = [threading.Thread(target=run, args=(a,), daemon=False) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 結果出力: 1 行 1 JSON
    for agent in agents:
        st = results[agent]
        print(json.dumps({
            "agent": agent,
            "status": st.status,
            "exit_code": st.exit_code,
            "pid": st.pid,
            "elapsed": round(st.elapsed, 1),
            "detail": st.detail,
            "err_log_size": st.err_log_size,
            "result_exists": st.result_exists,
            "sentinel_seen": st.sentinel_seen,
        }, ensure_ascii=False))

    # exit code: 全エージェントの最大値（OK=0 が最良、それ以外は失敗）
    sys.exit(max(results[a].exit_code for a in agents))


if __name__ == "__main__":
    main()
