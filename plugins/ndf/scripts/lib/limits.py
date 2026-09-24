#!/usr/bin/env python3
"""上限の表（収束ループ共通層、#598 / #537）。

監視の上限・無進捗の許容・CLI の上限の **既定値はこの表だけが持つ。** 監視（`monitor.py`）と
起動（`launch-cli.sh`）はここから引く。

- 監視の上限は **工程** で決める。レビューと適用では必要な時間の桁が違う
- 無進捗の許容は **担当** で決める。ログの出し方が担当で違う（claude は完了まで出さない）
- CLI の上限は、環境変数を解決した監視の上限 + `CLI_MARGIN` として **導く**。独立に持つと、
  利用者が `MONITOR_TIMEOUT` だけを延ばしたときに CLI が先に打ち切る

表の既定値では、全工程 × 全担当の組で「無進捗の許容 < 監視の上限 < CLI の上限」が成り立つ
（`check`）。上書きした結果が崩れたときは、監視が担当ごとに警告する。

cross-refactoring の `add-tests` / `implement` / `fix` / `final-fix` には、骨組みが `--stall-timeout` を引数で
渡す（テスト 1 回を含む無出力の最長が工程で決まるため）。その許容はこの表に持たない。

Usage:
  limits.py cli-timeout <工程> <担当>      # 秒数を 1 行
  limits.py monitor-timeout <工程> <担当>  # 同上
  limits.py check                          # 表の順序の検査。崩れていれば組を出して終了コード 1

表に無い工程名は終了コード 1 で拒む。別名は持たない（呼び出し側が正規化する）。
"""
from __future__ import annotations

import os
import sys
from typing import Optional

# 工程ごとの監視の上限（秒）。
PHASE_TIMEOUT = {
    "review": 1200,
    "critique": 1200,
    "propose": 1200,
    # cross-refactoring の 5 フェーズ（#933）。計画は提案と同じ桁。テストの追加と実装の
    # 値は既定の下限で、駆動が予算から導いた上限を `--timeout` で渡す。
    "plan": 1200,
    "judge-test-changes": 1200,
    "add-tests": 3600,
    "implement": 3600,
    "fix": 3600,
    "final-fix": 3600,
}

# 担当ごとの無進捗の許容（秒）。
AGENT_STALL = {
    "codex": 180,   # 推論ログを逐次出す
    "agy": 480,     # err.log がほぼ無音
    "kiro": 480,    # ツール実行の待ちで数分沈黙することがある
    "claude": 900,  # `--output-format json` は完了まで 1 バイトも出さない
}

# 表に無い担当の無進捗の許容。
DEFAULT_STALL = 180

# CLI の上限 = 監視の上限 + この秒数。監視の 1 周期（15 秒）と停止の猶予（3 秒）に対して
# 十分な差を取り、監視が止める前に CLI が打ち切らないようにする。
CLI_MARGIN = 120

# `--phase` を省いたときの工程。
DEFAULT_PHASE = "review"


def safe_int_env(name: str, fallback: int) -> int:
    """環境変数を整数で読む。数字でなければ標準エラーへ 1 行出して `fallback` を返す。

    設定の誤りで監視や起動を落とさない。
    """
    if name not in os.environ:
        return fallback
    raw = os.environ[name]
    try:
        return int(raw)
    except (ValueError, TypeError):
        print(
            f"⚠ env {name}={raw!r} が int に変換できません — {fallback} を使用",
            file=sys.stderr, flush=True,
        )
        return fallback


def _check_phase(phase: str) -> None:
    if phase not in PHASE_TIMEOUT:
        raise KeyError(f"上限の表に無い工程です: {phase!r}（{' / '.join(PHASE_TIMEOUT)}）")


def _resolve_timeout(
    explicit: Optional[int], per_agent_env: str, shared_env: str, builtin: int
) -> int:
    """明示値 → 担当別環境変数 → 共通環境変数 → 組み込み値の順で解決する。

    監視の上限（`monitor_timeout`）と無進捗の許容（`stall_timeout`）は同じ優先順位で
    解決するため、順序をこの 1 か所に持つ。非数値の環境変数は `safe_int_env` が
    組み込み値へ戻す。
    """
    if explicit is not None:
        return explicit
    if per_agent_env in os.environ:
        return safe_int_env(per_agent_env, builtin)
    return safe_int_env(shared_env, builtin)


def monitor_timeout(phase: str, agent: str, explicit: Optional[int] = None) -> int:
    """監視の上限。`explicit`（`--timeout`）→ `MONITOR_TIMEOUT_<担当>` → `MONITOR_TIMEOUT` → 表。"""
    _check_phase(phase)
    return _resolve_timeout(
        explicit, f"MONITOR_TIMEOUT_{agent.upper()}", "MONITOR_TIMEOUT",
        PHASE_TIMEOUT[phase],
    )


def stall_timeout(agent: str, explicit: Optional[int] = None) -> int:
    """無進捗の許容。`explicit`（`--stall-timeout`）→ `MONITOR_STALL_<担当>` → `MONITOR_STALL` → 表。"""
    return _resolve_timeout(
        explicit, f"MONITOR_STALL_{agent.upper()}", "MONITOR_STALL",
        AGENT_STALL.get(agent, DEFAULT_STALL),
    )


def cli_timeout(phase: str, agent: str) -> int:
    """CLI の上限。環境変数を解決した監視の上限 + `CLI_MARGIN`。"""
    return monitor_timeout(phase, agent) + CLI_MARGIN


def check() -> list[tuple[str, str, int, int, int]]:
    """表の既定値の全組で順序を検査し、崩れた組を返す。環境変数は見ない。"""
    broken = []
    for phase, monitor in PHASE_TIMEOUT.items():
        for agent, stall in AGENT_STALL.items():
            cli = monitor + CLI_MARGIN
            if not stall < monitor < cli:
                broken.append((phase, agent, stall, monitor, cli))
    return broken


def main(argv: list[str]) -> int:
    if argv[:1] == ["check"] and len(argv) == 1:
        broken = check()
        for phase, agent, stall, monitor, cli in broken:
            print(f"{phase} × {agent}: 無進捗の許容 {stall} / 監視の上限 {monitor} / "
                  f"CLI の上限 {cli}", file=sys.stderr)
        return 1 if broken else 0
    if len(argv) == 3 and argv[0] in ("cli-timeout", "monitor-timeout"):
        command, phase, agent = argv
        try:
            value = cli_timeout(phase, agent) if command == "cli-timeout" \
                else monitor_timeout(phase, agent)
        except KeyError as exc:
            print(exc.args[0], file=sys.stderr)
            return 1
        print(value)
        return 0
    print(__doc__.split("Usage:", 1)[1].strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
