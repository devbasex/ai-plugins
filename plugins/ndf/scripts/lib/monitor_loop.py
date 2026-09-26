"""担当 1 者の監視ループ（#1142 の C3 で `monitor.py` から分けた）。

pid ファイル・ログ・結果ファイルを周期ごとに見て、上限・無進捗・早期のエラー・プロセスの終了から結末を決める。
PID の判定は `monitor_proc`、ログの判定は `monitor_scan` の属性として呼ぶ（テストの差し替えを効かせるため）。
"""
from __future__ import annotations

import sys
import time

import monitor_patterns
import monitor_proc
import monitor_scan
import monitor_types


def _finish_monitor(
    status: monitor_types.AgentStatus,
    outcome: monitor_types.MonitorOutcome,
    log_context: tuple[str, str],
) -> monitor_types.AgentStatus:
    status.outcome = outcome
    status.status = outcome.status
    status.exit_code = outcome.exit_code
    status.detail = outcome.detail
    _emit_log(*log_context, status)
    return status


def _lingering_completion(
    paths: monitor_types.AgentPaths,
    status: monitor_types.AgentStatus,
    pid: int,
    started_wall: float,
) -> str | None:
    has_result = paths.result.exists() and paths.result.stat().st_size > 0
    if monitor_types._agent_runtime(status.agent) == "codex" and status.sentinel_seen and has_result:
        monitor_proc._kill_pid(pid)
        status.result_exists = True
        return f"codex sentinel + result.json detected; killed lingering pid {pid}"
    if status.sentinel_seen or not has_result:
        return None
    result_mtime = paths.result.stat().st_mtime
    if result_mtime < started_wall:
        return None
    result_age = time.time() - result_mtime
    if result_age < monitor_types.RESULT_AGE_GRACE:
        return None
    monitor_proc._kill_pid(pid)
    status.result_exists = True
    return (
        f"result.json exists for {result_age:.0f}s without process exit; "
        f"killed lingering pid {pid}"
    )


def _update_progress(
    paths: monitor_types.AgentPaths,
    status: monitor_types.AgentStatus,
    last_progress_size: int,
    last_progress: float,
) -> tuple[int, float]:
    status.err_log_size = monitor_scan._safe_size(paths.err_log)
    status.stdout_log_size = monitor_scan._safe_size(paths.stdout_log)
    status.progress_log_size = monitor_scan._safe_size(paths.progress_log)
    status.progress_tail = monitor_scan._tail_last_nonempty_line(paths.progress_log)
    progress_size = (
        status.err_log_size + status.stdout_log_size + status.progress_log_size
    )
    if progress_size != last_progress_size:
        last_progress_size = progress_size
        last_progress = time.monotonic()
    status.idle_seconds = time.monotonic() - last_progress
    return last_progress_size, last_progress


def _initialize_monitor(
    agent: str, pr: int, stem_template: str
) -> tuple[monitor_types.AgentPaths, monitor_types.AgentStatus, float, int | None]:
    paths = monitor_types.AgentPaths.for_(agent, pr, stem_template)
    status = monitor_types.AgentStatus(agent=agent)
    started = time.monotonic()
    grace_end = started + 30
    while time.monotonic() < grace_end:
        if paths.pidfile.exists():
            break
        time.sleep(2)
    return paths, status, started, monitor_proc._read_pidfile(paths.pidfile)


def _validate_pid_cmdline(
    pid: int, agent: str, alive: bool, validated: bool
) -> tuple[bool, monitor_types.MonitorOutcome | None]:
    if not alive or validated:
        return validated, None
    cmdline_ok = monitor_proc._pid_cmdline_matches(pid, agent)
    if cmdline_ok is False:
        monitor_proc._kill_pid(pid)
        return validated, monitor_types.MonitorOutcome.create(
            "PIDFILE_BAD",
            f"pid {pid} cmdline does not contain '{agent}' (stale pidfile?)",
        )
    return cmdline_ok is True, None


def _timeout_outcome(
    elapsed: float, timeout: int, alive: bool, pid: int
) -> monitor_types.MonitorOutcome | None:
    if elapsed < timeout:
        return None
    if alive:
        monitor_proc._kill_pid(pid)
    return monitor_types.MonitorOutcome.create("TIMEOUT", f"hard timeout {timeout}s reached (pid {pid})")


def _early_error_outcome(
    paths: monitor_types.AgentPaths, status: monitor_types.AgentStatus, alive: bool, disabled: bool
) -> tuple[monitor_types.MonitorOutcome | None, str | None]:
    fatal, warning = monitor_scan._early_error(paths, status.agent, disabled)
    if not fatal:
        return None, warning
    if alive:
        monitor_proc._kill_pid(status.pid)
    return monitor_types.MonitorOutcome.create(
        "EARLY_ERROR", f"early error (fatal) in {fatal.source}: {fatal.message[:200]}",
        reason=fatal.reason,
    ), warning


def _process_exit_outcome(
    paths: monitor_types.AgentPaths, status: monitor_types.AgentStatus, alive: bool, require_result: bool
) -> monitor_types.MonitorOutcome | None:
    if alive:
        return None
    status.result_exists = paths.result.exists() and paths.result.stat().st_size > 0
    if status.result_exists or not require_result:
        return monitor_types.MonitorOutcome.create(
            "OK",
            f"process exited; sentinel={status.sentinel_seen}; "
            f"result_exists={status.result_exists}",
        )
    # 結果なしの理由を err.log から引く。CLI の上限の文言があれば `cli_timeout`、無ければ
    # 状態からの既定（`missing`）に落ちる。
    cli_timeout = monitor_scan._scan_patterns(paths.err_log, monitor_patterns.CLI_TIMEOUT_AFTER_EXIT)
    if cli_timeout:
        return monitor_types.MonitorOutcome.create(
            "NO_RESULT",
            f"process exited but result.json missing (CLI timeout): {cli_timeout[:200]}",
            reason="cli_timeout",
        )
    return monitor_types.MonitorOutcome.create(
        "NO_RESULT", f"process exited but result.json missing: {paths.result}"
    )


def _stall_outcome(
    status: monitor_types.AgentStatus, stall_timeout: int, pid: int, last_progress_size: int
) -> monitor_types.MonitorOutcome | None:
    if status.idle_seconds < stall_timeout:
        return None
    monitor_proc._kill_pid(pid)
    return monitor_types.MonitorOutcome.create(
        "STALLED",
        f"no log progress for {stall_timeout}s "
        f"(pid {pid}, last size {last_progress_size}B)",
    )


def monitor_agent(
    agent: str,
    pr: int,
    config: monitor_types.MonitorConfig,
) -> monitor_types.AgentStatus:
    """1 agent を監視する。

    `config.no_early_error=True` のとき、EARLY_ERROR 検知 (FATAL/WARN とも) を完全に無効化し、
    hard timeout / stall / sentinel / result.json のみで判定する。
    """
    paths, status, started, pid = _initialize_monitor(agent, pr, config.stem_template)

    def finish(outcome: monitor_types.MonitorOutcome) -> monitor_types.AgentStatus:
        """status とログ文脈を閉じ込めて結末を確定する。終了時のログ文脈を変えるときは
        ここ 1 か所を直せばよい（各終了分岐が同じ呼び出しを繰り返さない）。"""
        return _finish_monitor(status, outcome, (config.log_prefix, agent))

    if pid is None:
        return finish(
            monitor_types.MonitorOutcome.create("PIDFILE_BAD", f"pidfile not found: {paths.pidfile}"),
        )

    status.pid = pid
    # cmdline 検証 (PID 再利用対策) は **プロセスが生きていると確認できたときのみ** 行う。
    # 起動直後に既にプロセスが exit していると /proc/<pid> が消えるか、別プロセスに
    # 再利用されている可能性があり、ここで PIDFILE_BAD を返すと「完了している（result.json は出ている）」
    # ケースを誤って失敗にしてしまう。alive=True と確認した瞬間のみ cmdline 一致を検証する。

    started_wall = time.time()
    last_progress_size = (
        monitor_scan._safe_size(paths.err_log)
        + monitor_scan._safe_size(paths.stdout_log)
        + monitor_scan._safe_size(paths.progress_log)
    )
    last_progress = time.monotonic()
    cmdline_validated = False
    warned_early_error = False

    while True:
        elapsed = time.monotonic() - started
        status.elapsed = elapsed

        # 1. プロセス生存確認 → 死んでいたら最終判定へ (result.json 存在をチェック)
        alive = monitor_proc._pid_alive(pid)
        if monitor_types._agent_runtime(agent) == "codex":
            status.sentinel_seen = monitor_scan._scan_codex_sentinel(paths.err_log)

        # codex は `tokens used` sentinel を出した後もプロセスが exit せず常駐し続ける
        # ケースがある (実機で観測)。result.json は正常に書かれているのに alive=True の
        # まま stall_timeout に達して STALLED 化してしまう。sentinel + result.json が
        # 揃った瞬間に対象プロセスを kill して OK 判定で返す。
        completion_detail = None
        if alive and (status.sentinel_seen or cmdline_validated):
            completion_detail = _lingering_completion(paths, status, pid, started_wall)
        if completion_detail is not None:
            return finish(monitor_types.MonitorOutcome.create("OK", completion_detail))

        # result.json が書かれた後もプロセスがハングするケース (実測:
        # MCP サーバー切断待ち等で exit しない)。sentinel 機構を持たない agent 向け
        # の fallback: result.json の mtime が RESULT_AGE_GRACE 秒以上前であれば
        # 完了とみなし、プロセスを kill → OK。
        # 安全条件:
        #   - cmdline_validated: PID 再利用でない (または検証不能環境) ことを確認済み
        #   - mtime >= started_wall: 前 round の stale result.json を拾わない
        cmdline_validated, outcome = _validate_pid_cmdline(
            pid, agent, alive, cmdline_validated
        )
        if outcome:
            return finish(outcome)

        # 2. hard timeout
        outcome = _timeout_outcome(elapsed, config.timeout, alive, pid)
        if outcome:
            return finish(outcome)

        # 3. early error
        # 明確な致命 (FATAL) のみ kill する。曖昧パターン (生 Error: / Traceback) は
        # WARN として警告ログのみ。codex がレビュー対象 diff の test コード片を
        # echo するケースで誤 kill されるのを防ぐ。
        outcome, warn_err = _early_error_outcome(paths, status, alive, config.no_early_error)
        if outcome:
            return finish(outcome)
        if not warned_early_error and warn_err:
            print(
                f"{config.log_prefix}⚠️  {agent} early-error WARN "
                f"(non-fatal, not killing): {warn_err[:200]}",
                file=sys.stderr, flush=True,
            )
            warned_early_error = True

        outcome = _process_exit_outcome(paths, status, alive, config.require_result)
        if outcome:
            return finish(outcome)

        # 4. stall detection (err.log / stdout.log / progress.log をモニタ。
        # agy は stdout 側だけ進捗が出るケースがあり、progress.log には
        # launcher が要求した短いフェーズマーカーが出るため、いずれかが
        # 更新されれば progress として扱う)
        last_progress_size, last_progress = _update_progress(
            paths, status, last_progress_size, last_progress
        )
        outcome = _stall_outcome(status, config.stall_timeout, pid, last_progress_size)
        if outcome:
            return finish(outcome)

        # poll 中の進捗ログ
        _emit_progress(config.log_prefix, agent, status)
        time.sleep(config.poll)


def _emit_progress(prefix: str, agent: str, st: monitor_types.AgentStatus) -> None:
    progress = f" progress={st.progress_tail!r}" if st.progress_tail else ""
    print(
        f"{prefix}⏳ {agent} elapsed={st.elapsed:.0f}s pid={st.pid} "
        f"idle={st.idle_seconds:.0f}s "
        f"err={st.err_log_size}B stdout={st.stdout_log_size}B "
        f"progress_log={st.progress_log_size}B "
        f"sentinel={'Y' if st.sentinel_seen else '-'}{progress}",
        file=sys.stderr, flush=True,
    )


def _emit_log(prefix: str, agent: str, st: monitor_types.AgentStatus) -> None:
    icon = st.outcome.icon if st.outcome else "?"
    print(
        f"{prefix}{icon} {agent} {st.status} ({st.elapsed:.0f}s) — {st.detail}",
        file=sys.stderr, flush=True,
    )
