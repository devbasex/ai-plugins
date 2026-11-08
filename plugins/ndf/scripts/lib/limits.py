#!/usr/bin/env python3
"""上限の宣言の読み手（#2343 の決定 1〜3。収束ループ共通層 #598 / #537 の表を含む）。

上限の秒・再試行の回数・待ちの間隔の **既定値は上限の宣言（プラグインの `contracts/limits.json`）だけが持つ。**
部品は値をコピーして持たず、方針の名前で引く（`policy("<名前>")`・`seconds("<名前>")`・`value("<名前>", "<キー>")`）。
宣言は標準ライブラリの `json` だけで 1 プロセスに 1 回読む（hook の経路でも読むため）。宣言が無い・壊れている・
方針やその値が無いときは、組み込みの値へ戻らずに `LimitsError`（理由に宣言のパスと方針の名前）を投げる。

監視の工程 × 担当の表は、宣言の `monitor.phase.<工程>`・`monitor.stall.<担当>`・`monitor.stall-default`・
`monitor.cli-margin` の方針で持つ。監視（`monitor.py`）と起動（`launch-cli.sh`）はここから引く。

- 監視の上限は **工程** で決める。レビューと適用では必要な時間の桁が違う
- 無進捗の許容は **担当** で決める。ログの出し方が担当で違う（claude は完了まで出さない）
- CLI の上限は、環境変数を解決した監視の上限 + `cli_margin()` として **導く**。独立に持つと、
  利用者が `MONITOR_TIMEOUT` だけを延ばしたときに CLI が先に打ち切る

宣言の既定値では、全工程 × 全担当の組で「無進捗の許容 < 監視の上限 < CLI の上限」が成り立つ
（`check`）。上書きした結果が崩れたときは、監視が担当ごとに警告する。

cross-refactoring の `add-tests` / `implement` / `fix` / `final-fix` には、骨組みが `--stall-timeout` を引数で
渡す（テスト 1 回を含む無出力の最長が工程で決まるため）。その許容は宣言の表に持たない。

Usage:
  limits.py cli-timeout <工程> <担当> [--override N] [--no-floor]
                                           # 秒数を 1 行。--override は導いた値より短くできない（--no-floor でそのまま）
  limits.py monitor-timeout <工程> <担当>  # 同上
  limits.py check                          # 表の順序のチェック。崩れていれば組を出して終了コード 1

表に無い工程名は終了コード 1 で拒む。別名は持たない（呼び出し側が正規化する）。宣言の誤りは理由を 1 行出して終了コード 1。
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import NamedTuple, Optional

# 置き場はプラグインからの相対で固定する: 配布物ではプラグインの `contracts/`、ラッパーの複製（バージョンディレクトリ。
# `relay_lib/version_dir.py`）では `lib/` と並ぶ `contracts/`。上書きの環境変数は持たない（決定 2）
_LIB_DIR = Path(__file__).resolve().parent
DECLARATION = next(
    (p for p in (_LIB_DIR.parents[1] / "contracts" / "limits.json", _LIB_DIR.parent / "contracts" / "limits.json") if p.is_file()),
    _LIB_DIR.parents[1] / "contracts" / "limits.json",
)
VERSION = 1
PHASE_PREFIX = "monitor.phase."
STALL_PREFIX = "monitor.stall."
STALL_DEFAULT = "monitor.stall-default"
CLI_MARGIN = "monitor.cli-margin"

# `--phase` を省いたときの工程。
DEFAULT_PHASE = "review"

# 方針が持てる値: キー → (整数に限るか, 下限)
_KEYS = {
    "seconds": (False, 0),
    "interval": (False, 0),
    "factor": (False, 1),
    "max_interval": (False, 0),
    "per_call": (False, 0),
    "attempts": (True, 1),
    "same_cause": (True, 1),
}


class LimitsError(Exception):
    """上限の宣言が無い・壊れている・引いた方針や値が無い。"""


class Policy(NamedTuple):
    """上限の宣言の 1 行。方針に無い値は None。"""

    name: str
    summary: str
    seconds: Optional[float] = None
    interval: Optional[float] = None
    factor: Optional[float] = None
    max_interval: Optional[float] = None
    per_call: Optional[float] = None
    attempts: Optional[int] = None
    same_cause: Optional[int] = None


_path = DECLARATION
_cache: Optional[dict[str, Policy]] = None


def use(path) -> None:
    """読み込み先を差し替え、読んだ宣言を捨てる（テストが使う）。`None` で配布物の宣言へ戻す。"""
    global _path, _cache
    _path = Path(path) if path is not None else DECLARATION
    _cache = None


def _refuse_duplicate_names(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise LimitsError(f"上限の宣言 {_path} に同じ名前が 2 つある: {', '.join(dup)}")
    return dict(pairs)


def _number_ok(value, integer: bool, low: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if integer and not isinstance(value, int):
        return False
    return (math.isinf(value) and value > 0) or (not math.isnan(value) and value >= low)


def validate(raw: dict, where: str) -> dict[str, Policy]:
    """宣言の中身を確かめて方針の表にする（不変条件 I1）。形の誤りは `LimitsError`。"""
    if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("policies"), dict) or not raw["policies"]:
        raise LimitsError(f"上限の宣言 {where} の形が違う（version {VERSION} と空でない policies が要る）")
    out = {}
    for name, row in raw["policies"].items():
        if not isinstance(row, dict) or not isinstance(row.get("summary"), str) or not row["summary"].strip():
            raise LimitsError(f"上限の宣言 {where} の方針 {name} に summary が無い")
        unknown = sorted(set(row) - set(_KEYS) - {"summary"})
        if unknown:
            raise LimitsError(f"上限の宣言 {where} の方針 {name} に知らないキーがある: {', '.join(unknown)}")
        if len(row) < 2:
            raise LimitsError(f"上限の宣言 {where} の方針 {name} に値が無い")
        for key, (integer, low) in _KEYS.items():
            if key in row and not _number_ok(row[key], integer, low):
                kind = "整数" if integer else "数"
                raise LimitsError(f"上限の宣言 {where} の方針 {name} の {key} は {low} 以上の{kind}: {row[key]!r}")
        out[name] = Policy(name=name, **row)
    return out


def read_table() -> dict[str, Policy]:
    """宣言を 1 プロセスに 1 回読む。"""
    global _cache
    if _cache is None:
        try:
            text = _path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LimitsError(f"上限の宣言 {_path} を読めない: {exc.strerror or exc}") from None
        try:
            raw = json.loads(text, object_pairs_hook=_refuse_duplicate_names)
        except ValueError as exc:
            raise LimitsError(f"上限の宣言 {_path} が JSON として壊れている: {exc}") from None
        _cache = validate(raw, str(_path))
    return _cache


def policy(name: str) -> Policy:
    """方針の名前で 1 行を引く。無ければ `LimitsError`。"""
    table = read_table()
    if name not in table:
        raise LimitsError(f"上限の宣言 {_path} に方針 {name} が無い")
    return table[name]


def value(name: str, key: str):
    """方針の 1 つの値。方針にその値が無ければ `LimitsError`。"""
    got = getattr(policy(name), key, None) if key in _KEYS else None
    if got is None:
        raise LimitsError(f"上限の宣言 {_path} の方針 {name} に {key} が無い")
    return got


def seconds(name: str):
    """方針の上限の秒。"""
    return value(name, "seconds")


def _seconds_by_prefix(prefix: str) -> dict:
    return {name[len(prefix) :]: p.seconds for name, p in read_table().items() if name.startswith(prefix)}


def phases() -> dict[str, int]:
    """工程 → 監視の上限（秒）。"""
    return _seconds_by_prefix(PHASE_PREFIX)


def stalls() -> dict[str, int]:
    """担当 → 無進捗の許容（秒）。"""
    return _seconds_by_prefix(STALL_PREFIX)


def stall_default() -> int:
    """表に無い担当の無進捗の許容（秒）。"""
    return seconds(STALL_DEFAULT)


def cli_margin() -> int:
    """CLI の上限 = 監視の上限 + この秒数。"""
    return seconds(CLI_MARGIN)


def safe_int_env(name: str, fallback: int) -> int:
    """環境変数を整数で読む。数字でなければ標準エラーへ 1 行出して `fallback` を返す。

    設定の誤りで監視や起動を落とさない（利用者の入力への扱い。宣言の誤りとは別）。
    """
    if name not in os.environ:
        return fallback
    raw = os.environ[name]
    try:
        return int(raw)
    except (ValueError, TypeError):
        print(
            f"⚠ env {name}={raw!r} が int に変換できません — {fallback} を使用",
            file=sys.stderr,
            flush=True,
        )
        return fallback


def _check_phase(phase: str) -> None:
    table = phases()
    if phase not in table:
        raise KeyError(f"上限の表に無い工程です: {phase!r}（{' / '.join(table)}）")


def _resolve_timeout(explicit: Optional[int], per_agent_env: str, shared_env: str, builtin: int) -> int:
    """明示値 → 担当別環境変数 → 共通環境変数 → 宣言の値の順で解決する。

    監視の上限（`monitor_timeout`）と無進捗の許容（`stall_timeout`）は同じ優先順位で
    解決するため、順序をこの 1 か所に持つ。非数値の環境変数は `safe_int_env` が
    宣言の値へ戻す。
    """
    if explicit is not None:
        return explicit
    if per_agent_env in os.environ:
        return safe_int_env(per_agent_env, builtin)
    return safe_int_env(shared_env, builtin)


def monitor_timeout(phase: str, agent: str, explicit: Optional[int] = None) -> int:
    """監視の上限。`explicit`（`--timeout`）→ `MONITOR_TIMEOUT_<担当>` → `MONITOR_TIMEOUT` → 宣言。"""
    _check_phase(phase)
    return _resolve_timeout(
        explicit,
        f"MONITOR_TIMEOUT_{agent.upper()}",
        "MONITOR_TIMEOUT",
        phases()[phase],
    )


def stall_timeout(agent: str, explicit: Optional[int] = None) -> int:
    """無進捗の許容。`explicit`（`--stall-timeout`）→ `MONITOR_STALL_<担当>` → `MONITOR_STALL` → 宣言。"""
    table = stalls()
    return _resolve_timeout(
        explicit,
        f"MONITOR_STALL_{agent.upper()}",
        "MONITOR_STALL",
        table[agent] if agent in table else stall_default(),
    )


def cli_timeout(phase: str, agent: str) -> int:
    """CLI の上限。環境変数を解決した監視の上限 + `cli_margin()`。"""
    return monitor_timeout(phase, agent) + cli_margin()


def resolve_cli_timeout(phase: str, agent: str, override=None, floor: bool = True) -> tuple[int, Optional[str]]:
    """CLI の上限と、標準エラーへ出す 1 行（無ければ `None`）。3 つの起動のスクリプトの規則を 1 つにしたもの。

    `override` が無い（`None`・空）なら導いた値（`cli_timeout`）。秒数でなければ導いた値を使い、そのことを返す。
    `floor` なら導いた値より短くできない（短いと CLI が監視より先に打ち切り、結果ファイルが残らない）。
    `floor` でなければ `override` をそのまま使う（駆動が予算から導いた上限）。工程名は常に表と照らす。
    """
    derived = cli_timeout(phase, agent)
    if override is None or override == "":
        return derived, None
    text = str(override)
    if not text.isdigit():
        return derived, f"⚠ CLI の上限 {text!r} は秒数ではないため、監視の上限から導いた {derived} 秒を使います"
    got = int(text)
    if floor and got < derived:
        return derived, f"⚠ CLI の上限 {got} 秒は監視の上限から導いた値より短いため、{derived} 秒を使います"
    return got, None


def _parse_cli_options(rest: list[str]) -> Optional[tuple[Optional[str], bool]]:
    override, floor = None, True
    i = 0
    while i < len(rest):
        if rest[i] == "--no-floor":
            floor = False
        elif rest[i] == "--override" and i + 1 < len(rest):
            override = rest[i + 1]
            i += 1
        else:
            return None
        i += 1
    return override, floor


def check() -> list[tuple[str, str, int, int, int]]:
    """宣言の既定値の全組で順序をチェックし、崩れた組を返す。環境変数は見ない。"""
    broken = []
    margin = cli_margin()
    for phase, monitor in phases().items():
        for agent, stall in stalls().items():
            cli = monitor + margin
            if not stall < monitor < cli:
                broken.append((phase, agent, stall, monitor, cli))
    return broken


def _dispatch(argv: list[str]) -> int:
    if argv[:1] == ["check"] and len(argv) == 1:
        broken = check()
        for phase, agent, stall, monitor, cli in broken:
            print(f"{phase} × {agent}: 無進捗の許容 {stall} / 監視の上限 {monitor} / CLI の上限 {cli}", file=sys.stderr)
        return 1 if broken else 0
    options = _parse_cli_options(argv[3:]) if argv[:1] == ["cli-timeout"] else ((None, True) if len(argv) == 3 else None)
    if len(argv) >= 3 and argv[0] in ("cli-timeout", "monitor-timeout") and options is not None:
        command, phase, agent = argv[:3]
        warning = None
        try:
            if command == "cli-timeout":
                got, warning = resolve_cli_timeout(phase, agent, *options)
            else:
                got = monitor_timeout(phase, agent)
        except KeyError as exc:
            print(exc.args[0], file=sys.stderr)
            return 1
        if warning:
            print(warning, file=sys.stderr)
        print(got)
        return 0
    print(__doc__.split("Usage:", 1)[1].strip(), file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    try:
        return _dispatch(argv)
    except LimitsError as exc:
        print(f"limits.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
