#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""CLI プロセス監視 CLI（収束ループ共通層）。

`launch-codex.sh` / `launch-agy.sh` / `lib/launch-cli.sh` で起動した
バックグラウンドプロセスを **複数の根拠で多重監視** し、失敗パターン
(sentinel 不在 / 早期エラー / ハング / pidfile stale / result.json 不在) を
構造化して扱う。

対象ランタイムは codex / agy / claude / kiro の 4 つで、監視対象の一時ファイル名は
`--stem-template` で決まる（既定は cross-review の `{agent}-review-pr{id}`）。
cross-refactoring は `{agent}-propose-rf{id}` のような別の命名を渡す。

**担当の名前は席の名前を取りうる**（`claude-2` のような同じランタイムの 2 つ目。#727）。
一時ファイルの名前はその名前のまま組み、CLI ごとのチェックと**上限の表の参照**は
`_agent_runtime` でランタイム名へ直してから行う。

監視軸:
  1. **pidfile** + `kill -0` でプロセス生存確認
     - 可能なら `/proc/<pid>/cmdline` で codex/agy であることを再確認 (PID 再利用対策)
  2. **sentinel** (codex のみ): err.log に `^tokens used$` 出現
  3. **early-error pattern**: err.log に既知の致命的キーワードが出たら即中断
     - **USAGE LIMIT** (利用上限。kiro の `Monthly request limit reached` / claude の
       `"api_error_status":429` / HTTP 429 / quota・rate limit): 検知時に kill し、
       状態は EARLY_ERROR のまま理由 `usage_limit` を結末に添える（#729 / #619）。
       claude だけは stdout.log の JSON も見る
     - **FATAL** (auth/sandbox 等の明確な致命): 検知時に kill
     - **WARN** (生の `Error:` / `Traceback` 等の曖昧パターン): 警告ログのみ、kill せず通常判定を継続
     - `--no-early-error` / `MONITOR_NO_EARLY_ERROR=1` で検知自体を無効化可
  4. **result.json**: プロセス終了後に `<worktree>/.cross_review/<agent>-review-pr<PR>-result.json` が
     生成されていなければ失敗扱い。err.log に CLI 自身の上限の文言（agy の
     `print timeout after <時間> with turn in progress`）があれば理由 `cli_timeout`（#729）
  5. **hard timeout**: 既定は `--phase` の工程で上限の表（`limits.py`）から引く
     （省略時は `review`）。`--timeout` → `MONITOR_TIMEOUT_<AGENT>` → `MONITOR_TIMEOUT` の順で上書き可
  6. **stall timeout**: err.log + stdout.log の合計サイズが一定時間変化しなければ
     STALLED として中断。既定は agent 別で上限の表から引く。`--stall-timeout` で CLI 明示、
     `MONITOR_STALL_<AGENT>` env で per-agent 上書き、`MONITOR_STALL` env で共通上書き可。
     解決した許容が監視の上限以上になった担当は、標準エラーへ警告を 1 行出す
  7. **progress.log heartbeat**: agent が任意で書く短いフェーズマーカーを stderr に表示。
     stdout/stderr が静かな時間でも、内部推論ではなく監視用の作業段階を確認できる
  8. **result.json + age fallback**: sentinel を持たない agent (agy) 向け。
     result.json の mtime が 30 秒以上前なら完了とみなし kill → OK
  9. **失敗時 kill**: TIMEOUT / STALLED / EARLY_ERROR (FATAL のみ) / PIDFILE_BAD で
     返るとき、対象プロセスを SIGTERM (3 秒後に SIGKILL) で停止する。対象がプロセス
     グループの先頭（`launch-cli.sh` は `set -m` で起動する）なら、グループへ送って
     子プロセスも止める（#584）。監視自身のグループへは送らない

Usage:
  monitor.py <PR> <target>          target ∈ {codex, agy, both}
  monitor.py <PR> both --timeout 1200 --stall-timeout 600
  monitor.py <PR> --agents agy,kiro --phase critique --stem-template '{agent}-critique-pr{id}'
  monitor.py <PR> both --no-early-error    # EARLY_ERROR 検知を完全無効化
  monitor.py <ID> --agents claude,kiro --tmp-dir DIR \
      --stem-template '{agent}-propose-rf{id}'

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
import pathlib
import sys
import threading


def _lib_dir() -> pathlib.Path:
    """この実体が置かれたディレクトリ。

    **`__file__` を使わない。** cross-review のシム（`scripts/monitor.py`）はこの実体を
    `exec` で読み込むため、`__file__` はシムの位置を指す。`compile` に渡した実体の
    パスは関数のコードオブジェクトが持つので、どちらの経路でも実体の隣を指せる。
    """
    return pathlib.Path(_lib_dir.__code__.co_filename).resolve().parent


if str(_lib_dir()) not in sys.path:
    sys.path.insert(0, str(_lib_dir()))
import assignment  # noqa: E402  席の名前の規則（#727）
import limits  # noqa: E402  上限の表（#598 / #537）
import clock  # noqa: E402  時刻の書き出し（#1142 の L0）
import monitor_outcome  # noqa: E402  監視の結果の語彙と読み書き（#662）
import monitor_loop  # noqa: E402
import monitor_patterns  # noqa: E402
import monitor_proc  # noqa: E402
import monitor_scan  # noqa: E402
import monitor_types  # noqa: E402

# 分けた 5 本の名前を再エクスポートする（既存の呼び出し側とシムの名前空間のため）。`_TMP_DIR_OVERRIDE` は
# `monitor_types` だけが持つ（ここで取り込むと代入が `_tmp_dir` へ届かない）。
from monitor_patterns import (  # noqa: E402,F401
    USAGE_LIMIT_FATAL, EARLY_ERROR_FATAL, EARLY_ERROR_FATAL_WARNING_SHAPED, EARLY_ERROR_WARN,
    EARLY_ERROR_BENIGN, EARLY_ERROR_BENIGN_KEEP_WARNINGS, CLI_TIMEOUT_AFTER_EXIT, CODEX_SENTINEL, ANSI_ESCAPE,
    CLAUDE_STDOUT_FATAL, CLAUDE_STDOUT_USAGE_LIMIT, _match_is_quoted, _unescaped_count, _strip_ansi,
)
from monitor_scan import (  # noqa: E402,F401
    _read_tail, _safe_size, _tail_last_nonempty_line, _scan_patterns, _scan_early_fatal, _scan_early_warn,
    _scan_claude_stdout, _scan_claude_stdout_fatal, _scan_claude_stdout_usage_limit, _scan_codex_sentinel,
    EarlyFatal, _scan_usage_limit, _scan_fatal, _early_error,
)
from monitor_proc import (  # noqa: E402,F401
    _read_pidfile, _proc_state, _pid_alive, _is_zombie, _leads_own_group, _kill_pid, _pid_cmdline_matches,
)
from monitor_types import (  # noqa: E402,F401
    DEFAULT_TIMEOUT, DEFAULT_STALL, DEFAULT_STALL_AGENT_BUILTIN, DEFAULT_POLL, RESULT_AGE_GRACE,
    DEFAULT_NO_EARLY_ERROR, _safe_int_env, _agent_runtime, _agent_stall_default, TMP_DIR_ENV_VARS, _tmp_dir,
    DEFAULT_STEM_TEMPLATE, MonitorConfig, AgentPaths, MonitorOutcome, AgentStatus,
)
from monitor_loop import (  # noqa: E402,F401
    monitor_agent, _initialize_monitor, _validate_pid_cmdline, _update_progress, _lingering_completion,
    _timeout_outcome, _early_error_outcome, _process_exit_outcome, _stall_outcome, _finish_monitor,
    _emit_progress, _emit_log,
)
from monitor_outcome import (  # noqa: E402,F401
    _record_outcome,
)


def _seat_or_both(value: str) -> str:
    """位置引数 `target` の型。席の名前か `both` だけを通す。

    通らなければ argparse が終了コード 2 で終わる。`both` はこれまでの 2 者
    （codex / agy）を指す省略形である。
    """
    if value == "both":
        return value
    try:
        assignment.seat_runtime(value)
    except assignment.AssignmentError as e:
        raise argparse.ArgumentTypeError(f"{e}。または both") from e
    return value


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pr", type=int)
    # 後方互換: cross-review は位置引数 `target` で codex / agy / both を渡す。
    # 2 者より多い組み合わせは `--agents` で渡す（どちらか一方だけを使う）。
    # **担当は席の名前を取りうる**（`claude-2` のような同じランタイムの 2 つ目。#727）。
    # `both` はこれまでの 2 者を指す省略形として残す（既存の呼び出し側が使い続けられる
    # ようにする）。3 者以上を監視するときは `--agents` を使う。
    p.add_argument("target", nargs="?", type=_seat_or_both)
    p.add_argument("--agents", default=None,
                   help="監視対象をカンマ区切りで指定 (例: claude,kiro)。"
                        "位置引数 target の代わりに使う")
    p.add_argument("--tmp-dir", default=None,
                   help="一時ファイルの置き場所。未指定時は env "
                        f"({' / '.join(TMP_DIR_ENV_VARS)}) と worktree から解決する")
    p.add_argument("--stem-template", default=DEFAULT_STEM_TEMPLATE,
                   help="一時ファイル名の骨格。`{agent}` と `{id}` を埋める "
                        f"(default: {DEFAULT_STEM_TEMPLATE})")
    # env (MONITOR_TIMEOUT / MONITOR_STALL / MONITOR_POLL) は呼び出し時に safe parse で読む。
    # 非数値設定でも上限の表の値 / `DEFAULT_POLL` に戻す。
    poll_default = _safe_int_env("MONITOR_POLL", DEFAULT_POLL)
    phases = " / ".join(f"{k}={v}" for k, v in limits.PHASE_TIMEOUT.items())
    p.add_argument("--phase", default=None,
                   help="監視の上限を上限の表から引く工程。"
                        f"省略時は {limits.DEFAULT_PHASE} の値 ({phases})")
    p.add_argument("--timeout", type=int, default=None,
                   help="hard timeout in seconds。未指定時は env MONITOR_TIMEOUT_<AGENT> / "
                        "MONITOR_TIMEOUT、無ければ --phase の工程の値")
    stalls = ", ".join(f"{k}={v}" for k, v in limits.AGENT_STALL.items())
    p.add_argument("--stall-timeout", type=int, default=None,
                   help="stall timeout (err.log no progress) in seconds. "
                        f"未指定時は env MONITOR_STALL_<AGENT> / MONITOR_STALL、無ければ agent 別既定 ({stalls})")
    p.add_argument("--poll", type=int, default=poll_default,
                   help=f"poll interval in seconds (default: {poll_default})")
    p.add_argument("--no-require-result", action="store_true",
                   help="プロセス終了後に result.json が無くても OK 扱い")
    p.add_argument("--no-early-error", action="store_true",
                   default=DEFAULT_NO_EARLY_ERROR,
                   help="EARLY_ERROR 検知を無効化 "
                        "(hard timeout / stall / sentinel / result.json のみで判定) "
                        f"[env: MONITOR_NO_EARLY_ERROR; default: {DEFAULT_NO_EARLY_ERROR}]")
    args = p.parse_args()
    # **表に無い工程は USAGE（終了コード 1）で拒む。** `choices` にすると argparse の
    # 終了コード 2（TIMEOUT と同じ値）になる。
    if args.phase is not None and args.phase not in limits.PHASE_TIMEOUT:
        print(f"monitor.py: 上限の表に無い工程です: {args.phase!r} "
              f"（{' / '.join(limits.PHASE_TIMEOUT)}）", file=sys.stderr, flush=True)
        sys.exit(1)
    phase = args.phase or limits.DEFAULT_PHASE

    agents = _resolve_agents(args, p)

    if args.tmp_dir:
        monitor_types._TMP_DIR_OVERRIDE = pathlib.Path(args.tmp_dir).resolve()

    results = _run_all(agents, args, phase)
    _emit_results(agents, results)

    # exit code: 全エージェントの最大値（OK=0 が最良、それ以外は失敗）
    sys.exit(max(results[a].exit_code for a in agents))


def _resolve_agents(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """`--agents`（カンマ区切り）か位置引数 `target` から担当リストを決める。

    `both` はこれまでの 2 者（codex / agy）を指す省略形。どちらも無ければ USAGE で拒む。
    """
    if args.agents:
        agents = [a.strip() for a in args.agents.split(",") if a.strip()]
        if not agents:
            parser.error("--agents が空です")
        return agents
    if args.target:
        return ["codex", "agy"] if args.target == "both" else [args.target]
    parser.error("target か --agents のどちらかを指定してください")


def _run_all(
    agents: list[str], args: argparse.Namespace, phase: str
) -> dict[str, AgentStatus]:
    """各担当をスレッドで並列監視し、担当名から結果を引ける辞書を返す。"""
    require_result = not args.no_require_result
    results: dict[str, AgentStatus] = {}

    def run(agent: str) -> None:
        # 上限の表と担当別の環境変数はランタイム名で引く。席の名前（`claude-2`）のまま
        # 渡すと表に無い担当として既定へ落ち、1 席目より早く無進捗と判定される（#727）。
        runtime = _agent_runtime(agent)
        timeout = limits.monitor_timeout(phase, runtime, args.timeout)
        stall = limits.stall_timeout(runtime, args.stall_timeout)
        print(f"[{agent}] ▶ hard timeout {timeout}s / stall {stall}s (phase {phase})",
              file=sys.stderr, flush=True)
        if stall >= timeout:
            # 上書きの結果、無進捗の許容が効かない組になった。止めはしない（AC33）。
            print(f"[{agent}] ⚠ 無進捗の許容 {stall}s が監視の上限 {timeout}s 以上です"
                  "（無進捗では止まらず、監視の上限で止まります）",
                  file=sys.stderr, flush=True)
        started_at = clock.now_iso()
        config = MonitorConfig(
            timeout=timeout,
            stall_timeout=stall,
            poll=args.poll,
            require_result=require_result,
            no_early_error=args.no_early_error,
            log_prefix=f"[{agent}] ",
            stem_template=args.stem_template,
        )
        results[agent] = monitor_agent(
            agent=agent,
            pr=args.pr,
            config=config,
        )
        _record_outcome(agent, args.pr, args.stem_template, results[agent], started_at,
                        args.phase)

    threads = [threading.Thread(target=run, args=(a,), daemon=False) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _emit_results(agents: list[str], results: dict[str, AgentStatus]) -> None:
    """各担当の最終ステータスを 1 行 1 JSON で標準出力へ書く。"""
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
            "stdout_log_size": st.stdout_log_size,
            "progress_log_size": st.progress_log_size,
            "progress_tail": st.progress_tail,
            "idle_seconds": round(st.idle_seconds, 1),
            "result_exists": st.result_exists,
            "sentinel_seen": st.sentinel_seen,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
