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
一時ファイルの名前はその名前のまま組み、CLI ごとの検査と**上限の表の参照**は
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
import os
import pathlib
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional


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
import monitor_outcome  # noqa: E402  監視の結果の語彙と読み書き（#662）


def _agent_runtime(agent: str) -> str:
    """担当の名前からランタイムを引く（CLI ごとの検査を選ぶために使う）。

    担当の単位は席の名前（`assignment.SEAT_PATTERN`。`claude-2` のように同じランタイムの
    2 つ目を表す）である。**席の形に合わない名前はそのまま返す。** cross-refactoring は
    任意の骨格（`--stem-template`）で担当名を渡せるため、形で弾くとその経路が壊れる。
    """
    try:
        return assignment.seat_runtime(agent)
    except assignment.AssignmentError:
        return agent


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


# ---------- 設定 ----------

# **上限の既定値はこの監視に持たない。** 上限の表（`limits.py`）だけが持ち、ここの名前は
# 表を指す別名である（#598 / #537）。env (`MONITOR_TIMEOUT` / `MONITOR_STALL` /
# `MONITOR_POLL`) の解釈は **呼び出し時** に try/except 付きで行い、非数値 env でも
# import / 監視プロセスがクラッシュしないようにする。
# (codex round 5 指摘: import 時の `int(os.environ.get(...))` は
#  `MONITOR_STALL=abc` のような誤設定で落ちてしまうため)
DEFAULT_TIMEOUT = limits.PHASE_TIMEOUT[limits.DEFAULT_PHASE]
DEFAULT_STALL = limits.DEFAULT_STALL
DEFAULT_STALL_AGENT_BUILTIN = limits.AGENT_STALL
DEFAULT_POLL = 15          # 15 sec — env `MONITOR_POLL` で上書き可
# result.json が書き込まれた後もプロセスがハングするケース (実測) の
# fallback: mtime から RESULT_AGE_GRACE 秒以上経過していれば完了とみなす。
RESULT_AGE_GRACE = 30
# `MONITOR_NO_EARLY_ERROR=1` で EARLY_ERROR 検知を無効化 (escape hatch)
DEFAULT_NO_EARLY_ERROR = os.environ.get("MONITOR_NO_EARLY_ERROR", "").lower() in {
    "1", "true", "yes", "on",
}

# **利用上限** の文言 (kill 対象。理由は `usage_limit`)。起動し直しても解けないため、
# 他の致命と区別して結末に理由を添える（#729 の決定 4）。err.log は全担当で見る。
# 照合は致命の表より **先** に行う。上限で落ちた後に別の致命が続く形が普通で、上限のほうが原因。
USAGE_LIMIT_FATAL = [
    # kiro の実物（#619）
    re.compile(r"Monthly request limit reached"),
    # claude の `--output-format json` の結果行（#647）。`:` の前後の空白は問わない
    re.compile(r'"api_error_status"\s*:\s*429'),
    # quota / rate limit （`m.start()` をキーワード位置に合わせるため `^.*` を付けない。
    # `_match_is_quoted()` が backtick / 「」 引用を判定するために match 開始位置を使うため）
    re.compile(r"\b(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE),
    # HTTP 429 の状態行
    re.compile(r"^HTTP/\d\S* 429 ", re.MULTILINE),
]

# err.log の行頭に近い形で出る **明確な致命** パターン (kill 対象。理由は `early_error`)。
# auth / sandbox / HTTP 401-403 など、プロセスが続行しても result を生成できないと
# 判明しているケースだけを入れる。利用上限は `USAGE_LIMIT_FATAL` の側。
EARLY_ERROR_FATAL = [
    # HTTP エラーステータス行 (`HTTP/1.1 401 Unauthorized` 等)
    re.compile(r"^HTTP/\d\S* (?:401|403) ", re.MULTILINE),
    # 認証 / 権限系（行頭限定）
    re.compile(r"^(?:Authentication failed|Permission denied)", re.MULTILINE),
    # API key 系
    re.compile(r"\bAPI key (?:not found|missing|invalid)\b", re.IGNORECASE),
    # codex 固有: sandbox エラー
    re.compile(r"\bsandbox error\b", re.IGNORECASE),
]

# **CLI 自身の上限** で結果を書かずに終わったことを示す文言（理由は `cli_timeout`）。
# **終了した後、結果ファイルが無いときだけ** 照合する。生きている間に見ると途中の警告を
# 致命と読み、結果ファイルがあれば上限に当たっても書き終えているので使える（#729 の決定 5）。
CLI_TIMEOUT_AFTER_EXIT = [
    # agy の `--print-timeout` の打ち切り（#598 / #537 の実物）
    re.compile(r"print timeout after \S+ with turn in progress"),
]

# **警告の見た目で出る致命** パターン。`EARLY_ERROR_FATAL` と違い、行頭の
# `warning:` を benign とする規則を適用しない（適用すると自分自身が消える）。
# 引用符・バックティック・markdown 引用による誤検知の除外だけを効かせる。
EARLY_ERROR_FATAL_WARNING_SHAPED = [
    # kiro 固有: ツール承認漏れ。**プロセスは終了コード 0 で正常終了する**ため、
    # 終了コードでは検知できない。`--trust-all-tools` を渡していれば本来出ないが、
    # フラグが効かない環境を検知するために残す。
    re.compile(r"is rejected because it matches one or more rules on the denied list"),
    # kiro 固有: `--trust-tools` にツール名の綴り違いを渡すと、警告だけ出して
    # 「何も信頼しない状態」で正常終了する。何も起きていない成功と区別できない。
    re.compile(r"WARNING: --trust-tools arg for custom tool"),
    # claude 固有: root 実行で bypassPermissions が拒否される。
    re.compile(r"--dangerously-skip-permissions cannot be used with root"),
]

# 行頭の生 `Error:` / `Traceback` 系は **kill しない警告のみ** に降格。
# - codex がレビュー対象 diff の test コード片を echo して `Traceback` が混入するケース
# など、続行可能な誤検知が頻発するため。プロセスは sentinel / result.json / timeout
# で別途判定する。
EARLY_ERROR_WARN = [
    re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE),
]

# 文脈に含まれていたら benign（doc 引用 / コードレビューコメント等）と見なし誤検知扱い。
# FATAL / WARN 双方のスキャンに適用される。
EARLY_ERROR_BENIGN = [
    # diff のコンテキスト行 (` `, `+`, `-` で始まり、その後 markdown 表記が来る)
    re.compile(r"^[ +-].*[\|`]", re.MULTILINE),
    # markdown のリスト / 引用
    re.compile(r"^\s*[-*>]\s", re.MULTILINE),
    # markdown の表セル行 (`| ... | ...` 形式)。SKILL.md / docs/*.md が
    # 検知パターンを表で列挙しており、それを codex が echo すると誤検知する。
    re.compile(r"^\|", re.MULTILINE),
    # grep / ripgrep 形式のソース引用行 (`path/to/file.ext:42:    <code>`)。
    # codex がレビュー対象のテストコード片を grep 形式で echo すると、
    # その文字列リテラル内の FATAL キーワードを誤検知する (PR #23 round 2 で発生)。
    re.compile(r"^\S+\.[A-Za-z0-9]+:\d+:", re.MULTILINE),
    # warning は致命ではない
    re.compile(r"^warning: ", re.IGNORECASE | re.MULTILINE),
]

# `EARLY_ERROR_FATAL_WARNING_SHAPED` に適用する benign 規則。
# 「行頭が warning:」だけを外し、ドキュメント引用の除外は維持する。
EARLY_ERROR_BENIGN_KEEP_WARNINGS = [
    p for p in EARLY_ERROR_BENIGN
    if p.pattern != r"^warning: "
]


def _match_is_quoted(line: str, match_start: int, match_end: int) -> bool:
    """マッチ位置がドキュメント引用 / コード文字列リテラルに囲まれているか判定。

    - backtick: マッチ開始までの `` ` `` カウントが奇数 かつ マッチ終了以降に `` ` `` がある
    - 日本語クォート: マッチ開始までに直近の `「` が `」` よりも後 かつ マッチ終了以降に `」` がある
    - ダブル/シングルクォート文字列リテラル: マッチ開始までの `"` (or `'`) カウントが
      奇数 かつ マッチ終了以降に同じクォートがある (= リテラルの内側)

    Why: SKILL.md / docs/*.md 内で FATAL キーワードを `「quota exceeded」` のように
    引用列挙しており、codex がそれを echo する。さらに tests/*.py の
    `"quota exceeded: please upgrade"` のような **テスト用文字列リテラル** を
    codex がレビュー中に echo するケース (PR #23 round 2 で実際に発生) もある。
    いずれも引用形であり本物のエラーではないため benign 扱いする。
    """
    before = line[:match_start]
    after = line[match_end:]
    if before.count("`") % 2 == 1 and "`" in after:
        return True
    if before.rfind("「") > before.rfind("」") and "」" in after:
        return True
    # コード文字列リテラル (ダブル / シングルクォート)。
    # エスケープされたクォート (`\"` / `\'`) はリテラルを開閉しないため
    # パリティ計算から除外する。これを数えると、文字列内にエスケープ
    # クォートを含む行で「引用内/外」の判定がずれ、本物のエラー行を
    # 誤って benign 扱い (= FATAL 見逃し) する恐れがある。
    for q in ('"', "'"):
        if _unescaped_count(before, q) % 2 == 1 and q in after:
            return True
    return False


def _unescaped_count(text: str, quote: str) -> int:
    """`quote` のうちバックスラッシュでエスケープされていない出現数を数える。

    直前の連続バックスラッシュ数が奇数なら、そのクォートはエスケープ
    されている (リテラルを開閉しない) ものとして除外する。
    """
    count = 0
    for i, ch in enumerate(text):
        if ch != quote:
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and text[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            count += 1
    return count

CODEX_SENTINEL = re.compile(r"^tokens used$", re.MULTILINE)

# ANSI エスケープ（CSI / OSC / 単独の 2 文字シーケンス）。
# kiro-cli は `NO_COLOR=1` / `TERM=dumb` / 非 TTY のいずれでも色コードを出し続けるため、
# **パターン照合の前に必ず除去する**。除去しないと行頭アンカー (`^Error:`) が
# 色コードに阻まれて一致せず、致命エラーを取りこぼす。
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


# claude の `--output-format json` 出力に現れる致命パターン。
# 標準出力側に出るため err.log ではなく stdout.log を見る。
CLAUDE_STDOUT_FATAL = [
    # 承認失敗。空配列 `[]` は正常なので「非空」だけを致命とする。
    re.compile(r'"permission_denials"\s*:\s*\[\s*\{'),
    re.compile(r'"is_error"\s*:\s*true'),
]

# claude の stdout.log に出る利用上限（理由は `usage_limit`）。err.log と stdout.log の
# どちらに出るか未確認のため両方を見る（#729 の決定 6）。JSON 向けの照合で除外を掛けない。
CLAUDE_STDOUT_USAGE_LIMIT = [
    re.compile(r'"api_error_status"\s*:\s*429'),
]


# env を safe に int parse する。非数値時は warn を stderr に出して fallback 値を返す。
# 上限の表と同じ規則で読むため、表の側の実装を使う。
_safe_int_env = limits.safe_int_env


def _agent_stall_default(agent: str) -> int:
    """agent ごとの既定 stall timeout を解決する。

    解決順（`limits.stall_timeout`）:
      1. env `MONITOR_STALL_<AGENT>` (per-agent 明示)
      2. env `MONITOR_STALL` (全 agent 共通)
      3. 上限の表の `AGENT_STALL[agent]`
      4. `DEFAULT_STALL` (表に無い agent)

    env は **呼び出し時** に再評価し、非数値なら warn を出して表の値に戻す。

    **席の名前はランタイム名へ直してから引く**（#727）。上限の表も担当別の環境変数も
    ランタイム名で引くため、`claude-2` のまま渡すと表に無い担当として `DEFAULT_STALL`
    へ落ち、1 席目より早く無進捗と判定される。
    """
    return limits.stall_timeout(_agent_runtime(agent))


# `--tmp-dir` で明示指定された一時ディレクトリ。CLI の解析時にだけ設定する。
# 収束ループごとに一時ディレクトリが違うため（cross-review は `.cross_review/`、
# cross-refactoring は `work/.cross_refactoring/`）、呼び出し側が明示できる経路を持つ。
_TMP_DIR_OVERRIDE: Optional[pathlib.Path] = None

# 一時ディレクトリを指す環境変数。先に見つかったものを採る。
TMP_DIR_ENV_VARS = ("CROSS_REVIEW_TMP_DIR", "CROSS_REFACTORING_TMP_DIR")


def _tmp_dir() -> pathlib.Path:
    """監視対象の一時ファイルを置くディレクトリ。

    state.py の `_tmp_dir()` と同じロジック。優先:
      1. `--tmp-dir` の明示指定
      2. `CROSS_REVIEW_TMP_DIR` / `CROSS_REFACTORING_TMP_DIR` env
      3. `<worktree-root>/.cross_review/` (worktree 内。作業領域を 1 つに保つため)

    worktree root は `git rev-parse --show-toplevel` で取得する。
    サブディレクトリから起動した場合でも一貫したパスを返す。
    """
    if _TMP_DIR_OVERRIDE is not None:
        _TMP_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _TMP_DIR_OVERRIDE
    env = next((os.environ[k] for k in TMP_DIR_ENV_VARS if os.environ.get(k)), None)
    if env:
        d = pathlib.Path(env).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    # git worktree root を取得。サブディレクトリから起動しても一貫したパスにする。
    import subprocess as _sp
    r = _sp.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        root = pathlib.Path(r.stdout.strip()).resolve()
    else:
        root = pathlib.Path.cwd().resolve()
    d = root / ".cross_review"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- データ型 ----------

# 一時ファイル名の骨格。`{agent}` と `{id}` を埋めて `<stem>.pid` などを作る。
# 既定は cross-review の命名で、後方互換のために変えない。
DEFAULT_STEM_TEMPLATE = "{agent}-review-pr{id}"


@dataclass(frozen=True)
class MonitorConfig:
    """1 agent の監視実行設定。"""
    timeout: int
    stall_timeout: int
    poll: int
    require_result: bool
    no_early_error: bool = False
    log_prefix: str = ""
    stem_template: str = DEFAULT_STEM_TEMPLATE


@dataclass
class AgentPaths:
    agent: str
    pr: int
    pidfile: pathlib.Path
    err_log: pathlib.Path
    stdout_log: pathlib.Path
    progress_log: pathlib.Path
    result: pathlib.Path

    @classmethod
    def for_(
        cls, agent: str, pr: int, stem_template: str = DEFAULT_STEM_TEMPLATE
    ) -> "AgentPaths":
        base = _tmp_dir() / stem_template.format(agent=agent, id=pr)
        return cls(
            agent=agent, pr=pr,
            pidfile=pathlib.Path(f"{base}.pid"),
            err_log=pathlib.Path(f"{base}-err.log"),
            stdout_log=pathlib.Path(f"{base}-stdout.log"),
            progress_log=pathlib.Path(f"{base}-progress.log"),
            result=pathlib.Path(f"{base}-result.json"),
        )


@dataclass(frozen=True)
class MonitorOutcome:
    status: str
    exit_code: int
    icon: str
    detail: str
    # 状態からは決まらない理由（`usage_limit` / `cli_timeout`）。`None` なら結末を書くときに
    # `monitor_outcome.reason_for(status)` へ落ちる（#729 の決定 8）。
    reason: Optional[str] = None

    @classmethod
    def create(cls, status: str, detail: str, reason: Optional[str] = None) -> "MonitorOutcome":
        exit_code, icon = {
            "OK": (0, "✅"),
            "TIMEOUT": (2, "⏰"),
            "NO_RESULT": (3, "❌"),
            "EARLY_ERROR": (4, "💥"),
            "STALLED": (5, "🛑"),
            "PIDFILE_BAD": (6, "❓"),
        }[status]
        return cls(status, exit_code, icon, detail, reason)


@dataclass(frozen=True)
class EarlyFatal:
    """早期の致命の一致。どのファイルで・何が・理由は何か（`None` なら `early_error`）。"""
    source: str
    message: str
    reason: Optional[str] = None


@dataclass
class AgentStatus:
    agent: str
    status: str = "RUNNING"
    exit_code: int = 0
    pid: Optional[int] = None
    elapsed: float = 0.0
    detail: str = ""
    err_log_size: int = 0
    stdout_log_size: int = 0
    progress_log_size: int = 0
    progress_tail: str = ""
    idle_seconds: float = 0.0
    result_exists: bool = False
    sentinel_seen: bool = False
    # 監視の結果ファイルだけに書く欄（#662）。**標準出力の JSON には載せない**
    # （標準出力のキーは上の欄から明示で組み立てる）。
    reason: str = ""
    launched_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    outcome: Optional[MonitorOutcome] = None


# ---------- 監視ロジック ----------

def _read_pidfile(p: pathlib.Path) -> Optional[int]:
    try:
        s = p.read_text().strip()
        return int(s) if s else None
    except (FileNotFoundError, ValueError):
        return None


def _proc_state(pid: int) -> Optional[str]:
    """`/proc/<pid>/status` の State 行の値。読めない・State 行が無いときは None。"""
    try:
        status_text = pathlib.Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for line in status_text.splitlines():
        if line.startswith("State:"):
            return line[len("State:"):]
    return None


def _pid_alive(pid: int) -> bool:
    """`kill -0` + ゾンビ検出。

    `os.kill(pid, 0)` はゾンビプロセスに対しても成功する (PID エントリが
    残っているため)。Docker without `--init` 環境では orphan プロセスが
    ゾンビ化して永久に残るため、`/proc/<pid>/status` で State: Z を検出する。
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    state = _proc_state(pid)
    return state is None or "Z" not in state


def _is_zombie(pid: int) -> bool:
    """PID がゾンビかどうか。_pid_alive() とは独立に呼べるユーティリティ。"""
    state = _proc_state(pid)
    return state is not None and "Z" in state


def _leads_own_group(pid: int) -> bool:
    """pid がプロセスグループの先頭で、かつ監視自身のグループではないか。

    `launch-cli.sh` は `set -m` で起動するため CLI の pid = pgid になる。そうでない pid
    （古い起動の手順・別の経路）は先頭でないか、監視と同じグループに居る。**監視自身の
    グループへ送ると、進行側のシェルまで止まる**（#584 の候補で退けた形）。
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    return pgid == pid and pgid != os.getpgrp()


def _kill_pid(pid: int, sigterm_grace: float = 3.0) -> None:
    """対象プロセスに SIGTERM、`sigterm_grace` 秒後も生きていたら SIGKILL。

    TIMEOUT / STALLED / EARLY_ERROR で監視を打ち切るとき、対象プロセスが残ったまま
    だと後から `gh api` 投稿や result.json 書き込みを実行してメインフローと
    競合する。失敗扱いで返るときは必ず停止させる。
    ゾンビプロセスにはシグナルを送れないためスキップする。

    対象がプロセスグループの先頭なら **グループへ送る**（#584 / #729 の決定 10）。pid だけへ
    送ると、CLI の子プロセスが残って止めた後に結果ファイルを書く。生存の確認は先頭の pid で見る。
    """
    if pid <= 0:
        return
    if _is_zombie(pid):
        return
    send = (lambda sig: os.killpg(pid, sig)) if _leads_own_group(pid) else (
        lambda sig: os.kill(pid, sig))
    try:
        send(signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + sigterm_grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.5)
    try:
        send(signal.SIGKILL)
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


def _read_tail(path: pathlib.Path, limit: int) -> Optional[str]:
    """末尾 `limit` バイトを utf-8 で読み出す（存在しない場合や OSError は None）。"""
    if not path.exists():
        return None
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > limit:
                f.seek(sz - limit)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _scan_patterns(
    path: pathlib.Path,
    patterns: list[re.Pattern[str]],
    benign: Optional[list[re.Pattern[str]]] = None,
) -> Optional[str]:
    """err.log を末尾 200KB だけ読み、`patterns` の最初の **non-benign** ヒット行を返す。

    BENIGN フィルタは **マッチ行そのもの** に対して適用し、誤検知 (markdown 引用 /
    diff body / 引用された警告) を除外する。

    重要 1: `pat.search()` ではなく `pat.finditer()` を使い、benign で除外された場合も
    後続の一致を継続して走査する。これにより benign な先行ヒットの後ろにある本物の
    エラーを見逃さない。

    重要 2: benign 判定は「マッチ行」だけを対象にする。以前は前後 40 文字の文脈窓を
    使っていたが、それだと benign 行が直前にあるだけで後続の本物エラーを誤って benign
    扱いしてしまった (例: `Error in: mcpServers.serena\\n...\\nTraceback ...` で
    Traceback が誤抑制された)。
    """
    data = _read_tail(path, 200 * 1024)
    if data is None:
        return None
    data = _strip_ansi(data)
    benign_patterns = EARLY_ERROR_BENIGN if benign is None else benign

    for pat in patterns:
        for m in pat.finditer(data):
            line_start = data.rfind("\n", 0, m.start()) + 1
            line_end = data.find("\n", m.end())
            line_end = line_end if line_end != -1 else len(data)
            line = data[line_start:line_end]
            # benign パターンはマッチ行そのものに当てる。markdown 引用や
            # `Error in: mcpServers.X` のような行単位パターンは「その行」だけを
            # 評価すれば判定可能で、文脈窓を広げると誤判定の原因になる。
            if any(b.search(line) for b in benign_patterns):
                continue
            # マッチ部位が backtick / 日本語「」 で引用されている場合も benign。
            if _match_is_quoted(line, m.start() - line_start, m.end() - line_start):
                continue
            return line.strip()
    return None


def _scan_early_fatal(path: pathlib.Path) -> Optional[str]:
    """err.log の致命の一致（kill 対象）。**利用上限も含む。**

    理由（`usage_limit` か `early_error` か）の区別はここでは行わず、`_early_error` が
    `USAGE_LIMIT_FATAL` を先に照合して決める。この関数は「止めるべき文言があるか」だけを返す。
    """
    hit = _scan_patterns(path, USAGE_LIMIT_FATAL) or _scan_patterns(path, EARLY_ERROR_FATAL)
    if hit:
        return hit
    return _scan_patterns(
        path,
        EARLY_ERROR_FATAL_WARNING_SHAPED,
        benign=EARLY_ERROR_BENIGN_KEEP_WARNINGS,
    )


def _scan_early_warn(path: pathlib.Path) -> Optional[str]:
    return _scan_patterns(path, EARLY_ERROR_WARN)


def _scan_claude_stdout(path: pathlib.Path, patterns: list[re.Pattern[str]]) -> Optional[str]:
    """claude の JSON 出力を `patterns` で照合し、一致の前後 80 文字を返す。

    `_scan_patterns()` は使わない。あちらは行単位の benign 判定と引用符パリティ判定を
    行うが、JSON は 1 行に多数の引用符を含むため、パリティ判定が「引用の内側」を
    誤って真にして致命を取りこぼす。
    """
    data = _read_tail(path, 200 * 1024)
    if data is None:
        return None
    data = _strip_ansi(data)
    for pat in patterns:
        m = pat.search(data)
        if m:
            return data[max(0, m.start() - 80):m.end() + 80].strip()
    return None


def _scan_claude_stdout_fatal(path: pathlib.Path) -> Optional[str]:
    """claude の JSON 出力から承認失敗・実行失敗を検出する。

    `--output-format json` は完了時に 1 個の JSON を吐くため、
    `permission_denials` が非空、または `is_error` が真であれば失敗が確定する。
    err.log 側の行単位パターンでは拾えないので専用に見る。
    """
    return _scan_claude_stdout(path, CLAUDE_STDOUT_FATAL)


def _scan_claude_stdout_usage_limit(path: pathlib.Path) -> Optional[str]:
    """claude の JSON 出力から利用上限（`"api_error_status":429`）を検出する。"""
    return _scan_claude_stdout(path, CLAUDE_STDOUT_USAGE_LIMIT)


def _scan_codex_sentinel(path: pathlib.Path) -> bool:
    tail = _read_tail(path, 64 * 1024)
    if tail is None:
        return False
    return bool(CODEX_SENTINEL.search(tail))


def _safe_size(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _tail_last_nonempty_line(path: pathlib.Path, limit: int = 4096) -> str:
    """監視用 progress.log の末尾 1 行を安全に読む。

    委譲先に書かせるのは短いフェーズマーカーだけなので、末尾数 KB で十分。
    壊れた UTF-8 や読み取り競合があっても monitor 自体は落とさない。
    """
    data = _read_tail(path, limit)
    if data is None:
        return ""
    if _safe_size(path) > limit and "\n" in data:
        data = data.split("\n", 1)[1]
    for line in reversed(data.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def _finish_monitor(
    status: AgentStatus,
    outcome: MonitorOutcome,
    log_context: tuple[str, str],
) -> AgentStatus:
    status.outcome = outcome
    status.status = outcome.status
    status.exit_code = outcome.exit_code
    status.detail = outcome.detail
    _emit_log(*log_context, status)
    return status


def _lingering_completion(
    paths: AgentPaths,
    status: AgentStatus,
    pid: int,
    started_wall: float,
) -> str | None:
    has_result = paths.result.exists() and paths.result.stat().st_size > 0
    if _agent_runtime(status.agent) == "codex" and status.sentinel_seen and has_result:
        _kill_pid(pid)
        status.result_exists = True
        return f"codex sentinel + result.json detected; killed lingering pid {pid}"
    if status.sentinel_seen or not has_result:
        return None
    result_mtime = paths.result.stat().st_mtime
    if result_mtime < started_wall:
        return None
    result_age = time.time() - result_mtime
    if result_age < RESULT_AGE_GRACE:
        return None
    _kill_pid(pid)
    status.result_exists = True
    return (
        f"result.json exists for {result_age:.0f}s without process exit; "
        f"killed lingering pid {pid}"
    )


def _scan_usage_limit(paths: AgentPaths, agent: str) -> EarlyFatal | None:
    """利用上限の文言。err.log は全担当、stdout.log は claude だけ JSON 向けの照合で見る。"""
    hit = _scan_patterns(paths.err_log, USAGE_LIMIT_FATAL)
    if hit:
        return EarlyFatal("err.log", hit, "usage_limit")
    if _agent_runtime(agent) == "claude":
        hit = _scan_claude_stdout_usage_limit(paths.stdout_log)
        if hit:
            return EarlyFatal("stdout.log", hit, "usage_limit")
    return None


def _scan_fatal(paths: AgentPaths, agent: str) -> EarlyFatal | None:
    """利用上限以外の致命（理由は `early_error`）。致命 → 警告の見た目の致命の順。"""
    hit = _scan_early_fatal(paths.err_log)
    if hit:
        return EarlyFatal("err.log", hit)
    if _agent_runtime(agent) == "claude":
        hit = _scan_claude_stdout_fatal(paths.stdout_log)
        if hit:
            return EarlyFatal("stdout.log", hit)
    return None


def _early_error(
    paths: AgentPaths,
    agent: str,
    disabled: bool,
) -> tuple[EarlyFatal | None, str | None]:
    """早期の致命と警告。**照合の順序は利用上限 → 致命 → 警告の見た目の致命。**

    同じ err.log に利用上限と他の致命が並んでいれば理由は `usage_limit` になる（#729 の
    決定 6）。`disabled`（`--no-early-error`）は利用上限の検知も一緒に無効にする。
    """
    if disabled:
        return None, None
    fatal = _scan_usage_limit(paths, agent) or _scan_fatal(paths, agent)
    return fatal, _scan_early_warn(paths.err_log)


def _update_progress(
    paths: AgentPaths,
    status: AgentStatus,
    last_progress_size: int,
    last_progress: float,
) -> tuple[int, float]:
    status.err_log_size = _safe_size(paths.err_log)
    status.stdout_log_size = _safe_size(paths.stdout_log)
    status.progress_log_size = _safe_size(paths.progress_log)
    status.progress_tail = _tail_last_nonempty_line(paths.progress_log)
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
) -> tuple[AgentPaths, AgentStatus, float, int | None]:
    paths = AgentPaths.for_(agent, pr, stem_template)
    status = AgentStatus(agent=agent)
    started = time.monotonic()
    grace_end = started + 30
    while time.monotonic() < grace_end:
        if paths.pidfile.exists():
            break
        time.sleep(2)
    return paths, status, started, _read_pidfile(paths.pidfile)


def _validate_pid_cmdline(
    pid: int, agent: str, alive: bool, validated: bool
) -> tuple[bool, MonitorOutcome | None]:
    if not alive or validated:
        return validated, None
    cmdline_ok = _pid_cmdline_matches(pid, agent)
    if cmdline_ok is False:
        _kill_pid(pid)
        return validated, MonitorOutcome.create(
            "PIDFILE_BAD",
            f"pid {pid} cmdline does not contain '{agent}' (stale pidfile?)",
        )
    return cmdline_ok is True, None


def _timeout_outcome(
    elapsed: float, timeout: int, alive: bool, pid: int
) -> MonitorOutcome | None:
    if elapsed < timeout:
        return None
    if alive:
        _kill_pid(pid)
    return MonitorOutcome.create("TIMEOUT", f"hard timeout {timeout}s reached (pid {pid})")


def _early_error_outcome(
    paths: AgentPaths, status: AgentStatus, alive: bool, disabled: bool
) -> tuple[MonitorOutcome | None, str | None]:
    fatal, warning = _early_error(paths, status.agent, disabled)
    if not fatal:
        return None, warning
    if alive:
        _kill_pid(status.pid)
    return MonitorOutcome.create(
        "EARLY_ERROR", f"early error (fatal) in {fatal.source}: {fatal.message[:200]}",
        reason=fatal.reason,
    ), warning


def _process_exit_outcome(
    paths: AgentPaths, status: AgentStatus, alive: bool, require_result: bool
) -> MonitorOutcome | None:
    if alive:
        return None
    status.result_exists = paths.result.exists() and paths.result.stat().st_size > 0
    if status.result_exists or not require_result:
        return MonitorOutcome.create(
            "OK",
            f"process exited; sentinel={status.sentinel_seen}; "
            f"result_exists={status.result_exists}",
        )
    # 結果なしの理由を err.log から引く。CLI の上限の文言があれば `cli_timeout`、無ければ
    # 状態からの既定（`missing`）に落ちる。
    cli_timeout = _scan_patterns(paths.err_log, CLI_TIMEOUT_AFTER_EXIT)
    if cli_timeout:
        return MonitorOutcome.create(
            "NO_RESULT",
            f"process exited but result.json missing (CLI timeout): {cli_timeout[:200]}",
            reason="cli_timeout",
        )
    return MonitorOutcome.create(
        "NO_RESULT", f"process exited but result.json missing: {paths.result}"
    )


def _stall_outcome(
    status: AgentStatus, stall_timeout: int, pid: int, last_progress_size: int
) -> MonitorOutcome | None:
    if status.idle_seconds < stall_timeout:
        return None
    _kill_pid(pid)
    return MonitorOutcome.create(
        "STALLED",
        f"no log progress for {stall_timeout}s "
        f"(pid {pid}, last size {last_progress_size}B)",
    )


def monitor_agent(
    agent: str,
    pr: int,
    config: MonitorConfig,
) -> AgentStatus:
    """1 agent を監視する。

    `config.no_early_error=True` のとき、EARLY_ERROR 検知 (FATAL/WARN とも) を完全に無効化し、
    hard timeout / stall / sentinel / result.json のみで判定する。
    """
    paths, status, started, pid = _initialize_monitor(agent, pr, config.stem_template)

    def finish(outcome: MonitorOutcome) -> AgentStatus:
        """status とログ文脈を閉じ込めて結末を確定する。終了時のログ文脈を変えるときは
        ここ 1 か所を直せばよい（各終了分岐が同じ呼び出しを繰り返さない）。"""
        return _finish_monitor(status, outcome, (config.log_prefix, agent))

    if pid is None:
        return finish(
            MonitorOutcome.create("PIDFILE_BAD", f"pidfile not found: {paths.pidfile}"),
        )

    status.pid = pid
    # cmdline 検証 (PID 再利用対策) は **プロセスが生きていると確認できたときのみ** 行う。
    # 起動直後に既にプロセスが exit していると /proc/<pid> が消えるか、別プロセスに
    # 再利用されている可能性があり、ここで PIDFILE_BAD を返すと「完了している（result.json は出ている）」
    # ケースを誤って失敗にしてしまう。alive=True と確認した瞬間のみ cmdline 一致を検証する。

    started_wall = time.time()
    last_progress_size = (
        _safe_size(paths.err_log)
        + _safe_size(paths.stdout_log)
        + _safe_size(paths.progress_log)
    )
    last_progress = time.monotonic()
    cmdline_validated = False
    warned_early_error = False

    while True:
        elapsed = time.monotonic() - started
        status.elapsed = elapsed

        # 1. プロセス生存確認 → 死んでいたら最終判定へ (result.json 存在をチェック)
        alive = _pid_alive(pid)
        if _agent_runtime(agent) == "codex":
            status.sentinel_seen = _scan_codex_sentinel(paths.err_log)

        # codex は `tokens used` sentinel を出した後もプロセスが exit せず常駐し続ける
        # ケースがある (実機で観測)。result.json は正常に書かれているのに alive=True の
        # まま stall_timeout に達して STALLED 化してしまう。sentinel + result.json が
        # 揃った瞬間に対象プロセスを kill して OK 判定で返す。
        completion_detail = None
        if alive and (status.sentinel_seen or cmdline_validated):
            completion_detail = _lingering_completion(paths, status, pid, started_wall)
        if completion_detail is not None:
            return finish(MonitorOutcome.create("OK", completion_detail))

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


def _emit_progress(prefix: str, agent: str, st: AgentStatus) -> None:
    progress = f" progress={st.progress_tail!r}" if st.progress_tail else ""
    print(
        f"{prefix}⏳ {agent} elapsed={st.elapsed:.0f}s pid={st.pid} "
        f"idle={st.idle_seconds:.0f}s "
        f"err={st.err_log_size}B stdout={st.stdout_log_size}B "
        f"progress_log={st.progress_log_size}B "
        f"sentinel={'Y' if st.sentinel_seen else '-'}{progress}",
        file=sys.stderr, flush=True,
    )


def _emit_log(prefix: str, agent: str, st: AgentStatus) -> None:
    icon = st.outcome.icon if st.outcome else "?"
    print(
        f"{prefix}{icon} {agent} {st.status} ({st.elapsed:.0f}s) — {st.detail}",
        file=sys.stderr, flush=True,
    )


def _record_outcome(
    agent: str, pr: int, stem_template: str, st: AgentStatus, started_at: str,
    phase: Optional[str] = None,
) -> None:
    """担当 1 者の監視の結果を、結果ファイルと記録へ書く（#662）。

    **書けなくても監視の結果は変えない。** 終了コードと標準出力は呼び出し側の分岐が
    読むため、書き出しの失敗は標準エラーへ 1 行出すだけにする。
    """
    stem = stem_template.format(agent=agent, id=pr)
    try:
        paths = AgentPaths.for_(agent, pr, stem_template)
        # 結末が理由を持てばそれを、無ければ状態からの既定を書く（#729 の決定 8）
        st.reason = (st.outcome.reason if st.outcome and st.outcome.reason
                     else monitor_outcome.reason_for(st.status))
        st.started_at = started_at
        st.ended_at = monitor_outcome.now_iso()
        try:
            st.launched_at = monitor_outcome.iso_from_timestamp(
                paths.pidfile.stat().st_mtime)
        except OSError:
            st.launched_at = None
        outcome = {
            "agent": agent,
            "stem": stem,
            "status": st.status,
            "exit_code": st.exit_code,
            "reason": st.reason,
            "detail": st.detail,
            "launched_at": st.launched_at,
            "started_at": st.started_at,
            "ended_at": st.ended_at,
            "elapsed": round(st.elapsed, 1),
            "idle_seconds": round(st.idle_seconds, 1),
            "progress_tail": st.progress_tail,
            "result_exists": st.result_exists,
            "pid": st.pid,
            # `--phase` の値。省いたときは null（#598 / #537）
            "phase": phase,
        }
        # 組み立てたキー集合を正本（`monitor_outcome.OUTCOME_KEYS`）と突き合わせる。
        # キーを片方だけへ足すと、ここで食い違いがその場で落ちる（#662）。
        assert set(outcome) == set(monitor_outcome.OUTCOME_KEYS), (
            set(outcome).symmetric_difference(monitor_outcome.OUTCOME_KEYS))
        tmp_dir = paths.pidfile.parent
        monitor_outcome.write_outcome(tmp_dir, stem, outcome)
        monitor_outcome.append_journal(tmp_dir, outcome)
    except Exception as exc:  # noqa: BLE001  書き出しの失敗で監視を落とさない
        print(f"[{agent}] ⚠ 監視の結果を書けません（{stem}）: {exc}",
              file=sys.stderr, flush=True)


# ---------- CLI ----------

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
        global _TMP_DIR_OVERRIDE
        _TMP_DIR_OVERRIDE = pathlib.Path(args.tmp_dir).resolve()

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
        started_at = monitor_outcome.now_iso()
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
