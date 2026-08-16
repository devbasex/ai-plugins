#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""CLI プロセス監視 CLI（収束ループ共通層）。

`launch-codex.sh` / `launch-gemini.sh` / `lib/launch-cli.sh` で起動した
バックグラウンドプロセスを **複数の根拠で多重監視** し、失敗パターン
(sentinel 不在 / 早期エラー / ハング / pidfile stale / result.json 不在) を
構造化して扱う。

対象ランタイムは codex / gemini / claude / kiro の 4 つで、監視対象の一時ファイル名は
`--stem-template` で決まる（既定は cross-review の `{agent}-review-pr{id}`）。
cross-refactoring は `{agent}-propose-rf{id}` のような別の命名を渡す。

監視軸:
  1. **pidfile** + `kill -0` でプロセス生存確認
     - 可能なら `/proc/<pid>/cmdline` で codex/gemini であることを再確認 (PID 再利用対策)
  2. **sentinel** (codex のみ): err.log に `^tokens used$` 出現
  3. **early-error pattern**: err.log に既知の致命的キーワードが出たら即中断
     - **FATAL** (auth/quota/sandbox 等の明確な致命): 検知時に kill
     - **WARN** (生の `Error:` / `Traceback` 等の曖昧パターン): 警告ログのみ、kill せず通常判定を継続
     - `--no-early-error` / `MONITOR_NO_EARLY_ERROR=1` で検知自体を無効化可
  4. **result.json**: プロセス終了後に `<worktree>/.cross_review/<agent>-review-pr<PR>-result.json` が
     生成されていなければ失敗扱い
  5. **hard timeout**: 既定 7 分。`--timeout` または `MONITOR_TIMEOUT` で上書き可
  6. **stall timeout**: err.log + stdout.log の合計サイズが一定時間変化しなければ
     STALLED として中断。既定は agent 別 (codex=180s, gemini=480s。gemini は err.log
     にほぼ進捗を出さないため大きめ)。`--stall-timeout` で CLI 明示、
     `MONITOR_STALL_<AGENT>` env で per-agent 上書き、`MONITOR_STALL` env で共通上書き可
  7. **progress.log heartbeat**: agent が任意で書く短いフェーズマーカーを stderr に表示。
     Gemini の stdout/stderr が静かな時間でも、内部推論ではなく監視用の作業段階を確認できる
  8. **result.json + age fallback**: sentinel を持たない agent (gemini) 向け。
     result.json の mtime が 30 秒以上前なら完了とみなし kill → OK
  9. **失敗時 kill**: TIMEOUT / STALLED / EARLY_ERROR (FATAL のみ) / PIDFILE_BAD で
     返るとき、対象プロセスを SIGTERM (3 秒後に SIGKILL) で停止する

Usage:
  monitor.py <PR> <target>          target ∈ {codex, gemini, both}
  monitor.py <PR> both --timeout 1200 --stall-timeout 600
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


# ---------- 設定 ----------

# 既定値は import 時に **固定数値** で保持する。env (`MONITOR_TIMEOUT` /
# `MONITOR_STALL` / `MONITOR_POLL`) の解釈は **呼び出し時** に try/except
# 付きで行い、非数値 env でも import / 監視プロセスがクラッシュしないようにする。
# (codex round 5 指摘: import 時の `int(os.environ.get(...))` は
#  `MONITOR_STALL=abc` のような誤設定で `_agent_stall_default()` に到達する前に
#  ValueError で落ちてしまうため)
DEFAULT_TIMEOUT = 420    # 7 min — `--timeout` / env `MONITOR_TIMEOUT` で上書き可
# 既定 stall timeout (後方互換のため env MONITOR_STALL は残す)。
# 両 agent 共通のデフォルトとして引き続き受け付ける (解釈は `_agent_stall_default()` 内)。
DEFAULT_STALL = 180       # 3 min no progress
# per-agent 上書き: gemini は err.log がほぼ無音なため大きめに取る。
# 解決順は `_agent_stall_default()` 参照。
DEFAULT_STALL_AGENT_BUILTIN = {
    "codex": 180,    # 推論ログを逐次出すので 3 min で十分
    "gemini": 480,   # err.log が静かなため 8 min まで許容
    # `claude -p --output-format json` は **完了まで 1 バイトも出さない**。
    # 進捗を見て打ち切ると必ず誤検知になるため、ログ無進捗の許容を最も長く取る。
    "claude": 900,
    # kiro-cli は逐次出力するが、ツール実行の待ちで数分沈黙することがある。
    "kiro": 480,
}
DEFAULT_POLL = 15          # 15 sec — env `MONITOR_POLL` で上書き可
# result.json が書き込まれた後もプロセスがハングするケース (gemini で観測) の
# fallback: mtime から RESULT_AGE_GRACE 秒以上経過していれば完了とみなす。
RESULT_AGE_GRACE = 30
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


def _safe_int_env(name: str, fallback: int) -> int:
    """env を safe に int parse する。

    非数値時は warn を stderr に出して fallback 値を返す。
    `_agent_stall_default()` と同じく、env 設定ミスでプロセスを落とさないため。

    Note (codex round 5 指摘): `MONITOR_STALL` 等の env を import 時 / 呼び出し時に
    生の `int(...)` で読むと、非数値設定で監視プロセスが起動できなくなる。
    `DEFAULT_TIMEOUT` / `DEFAULT_POLL` も同じ問題を持つため、共通ヘルパに集約。
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

    Note (gemini round 4 指摘): env が非数値だった場合 (`int(...)` で
    `ValueError` / `TypeError` が裸で上がる) は warn を出して
    `DEFAULT_STALL_AGENT_BUILTIN` / `DEFAULT_STALL` にフォールバックする。
    監視プロセスを env 設定ミスでクラッシュさせない。
    """
    builtin = DEFAULT_STALL_AGENT_BUILTIN.get(agent, DEFAULT_STALL)
    env_key = f"MONITOR_STALL_{agent.upper()}"
    if env_key in os.environ:
        return _safe_int_env(env_key, builtin)
    if "MONITOR_STALL" in os.environ:
        return _safe_int_env("MONITOR_STALL", builtin)
    return builtin


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
      3. `<worktree-root>/.cross_review/` (worktree 内。gemini の workspace 制約を根本回避)

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


# ---------- 監視ロジック ----------

def _read_pidfile(p: pathlib.Path) -> Optional[int]:
    try:
        s = p.read_text().strip()
        return int(s) if s else None
    except (FileNotFoundError, ValueError):
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
    try:
        status_text = pathlib.Path(f"/proc/{pid}/status").read_text()
        for line in status_text.splitlines():
            if line.startswith("State:"):
                return "Z" not in line
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return True


def _is_zombie(pid: int) -> bool:
    """PID がゾンビかどうか。_pid_alive() とは独立に呼べるユーティリティ。"""
    try:
        status_text = pathlib.Path(f"/proc/{pid}/status").read_text()
        for line in status_text.splitlines():
            if line.startswith("State:"):
                return "Z" in line
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return False


def _kill_pid(pid: int, sigterm_grace: float = 3.0) -> None:
    """対象プロセスに SIGTERM、`sigterm_grace` 秒後も生きていたら SIGKILL。

    TIMEOUT / STALLED / EARLY_ERROR で監視を打ち切るとき、対象プロセスが残ったまま
    だと後から `gh api` 投稿や result.json 書き込みを実行してメインフローと
    競合する。失敗扱いで返るときは必ず停止させる。
    ゾンビプロセスにはシグナルを送れないためスキップする。
    """
    if pid <= 0:
        return
    if _is_zombie(pid):
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
    path: pathlib.Path,
    patterns: list[re.Pattern[str]],
    benign: Optional[list[re.Pattern[str]]] = None,
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
    hit = _scan_patterns(path, EARLY_ERROR_FATAL)
    if hit:
        return hit
    return _scan_patterns(
        path,
        EARLY_ERROR_FATAL_WARNING_SHAPED,
        benign=EARLY_ERROR_BENIGN_KEEP_WARNINGS,
    )


def _scan_early_warn(path: pathlib.Path) -> Optional[str]:
    return _scan_patterns(path, EARLY_ERROR_WARN)


def _scan_claude_stdout_fatal(path: pathlib.Path) -> Optional[str]:
    """claude の JSON 出力から承認失敗・実行失敗を検出する。

    `--output-format json` は完了時に 1 個の JSON を吐くため、
    `permission_denials` が非空、または `is_error` が真であれば失敗が確定する。
    err.log 側の行単位パターンでは拾えないので専用に見る。

    `_scan_patterns()` は使わない。あちらは行単位の benign 判定と引用符パリティ判定を
    行うが、JSON は 1 行に多数の引用符を含むため、パリティ判定が「引用の内側」を
    誤って真にして致命を取りこぼす。
    """
    if not path.exists():
        return None
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > 200 * 1024:
                f.seek(sz - 200 * 1024)
            data = _strip_ansi(f.read().decode("utf-8", errors="replace"))
    except OSError:
        return None
    for pat in CLAUDE_STDOUT_FATAL:
        m = pat.search(data)
        if m:
            return data[max(0, m.start() - 80):m.end() + 80].strip()
    return None


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


def _safe_size(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _tail_last_nonempty_line(path: pathlib.Path, limit: int = 4096) -> str:
    """監視用 progress.log の末尾 1 行を安全に読む。

    Gemini に書かせるのは短いフェーズマーカーだけなので、末尾数 KB で十分。
    壊れた UTF-8 や読み取り競合があっても monitor 自体は落とさない。
    """
    if not path.exists():
        return ""
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > limit:
                f.seek(sz - limit)
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    if sz > limit and "\n" in data:
        data = data.split("\n", 1)[1]
    for line in reversed(data.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def _check_early_completion(
    agent: str,
    paths: AgentPaths,
    status: AgentStatus,
    pid: int,
    alive: bool,
    cmdline_validated: bool,
    started_wall: float,
    log_prefix: str,
) -> Optional[AgentStatus]:
    """sentinel または result.json の安定を根拠に常駐プロセスを完了させる。"""
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
        status.detail = f"codex sentinel + result.json detected; killed lingering pid {pid}"
        _emit_log(log_prefix, agent, status)
        return status

    if (
        alive
        and not status.sentinel_seen
        and cmdline_validated
        and paths.result.exists()
        and paths.result.stat().st_size > 0
    ):
        result_mtime = paths.result.stat().st_mtime
        if result_mtime >= started_wall:
            result_age = time.time() - result_mtime
            if result_age >= RESULT_AGE_GRACE:
                _kill_pid(pid)
                status.result_exists = True
                status.status = "OK"
                status.exit_code = 0
                status.detail = (
                    f"result.json exists for {result_age:.0f}s without process exit; "
                    f"killed lingering pid {pid}"
                )
                _emit_log(log_prefix, agent, status)
                return status
    return None


def _check_termination_conditions(
    agent: str,
    paths: AgentPaths,
    status: AgentStatus,
    pid: int,
    alive: bool,
    elapsed: float,
    timeout: int,
    stall_timeout: int,
    last_progress: float,
    last_progress_size: int,
    no_early_error: bool,
    warned_early_error: bool,
    require_result: bool,
    log_prefix: str,
) -> tuple[Optional[AgentStatus], bool, float, int]:
    """タイムアウト、異常、終了、停滞の順で終了条件を判定する。"""
    if elapsed >= timeout:
        if alive:
            _kill_pid(pid)
        status.status = "TIMEOUT"
        status.exit_code = 2
        status.detail = f"hard timeout {timeout}s reached (pid {pid})"
        _emit_log(log_prefix, agent, status)
        return status, warned_early_error, last_progress, last_progress_size

    if not no_early_error:
        fatal_err = _scan_early_fatal(paths.err_log)
        fatal_source = "err.log"
        if not fatal_err and agent == "claude":
            fatal_err = _scan_claude_stdout_fatal(paths.stdout_log)
            fatal_source = "stdout.log"
        if fatal_err:
            if alive:
                _kill_pid(pid)
            status.status = "EARLY_ERROR"
            status.exit_code = 4
            status.detail = f"early error (fatal) in {fatal_source}: {fatal_err[:200]}"
            _emit_log(log_prefix, agent, status)
            return status, warned_early_error, last_progress, last_progress_size
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
        return status, warned_early_error, last_progress, last_progress_size

    status.err_log_size = _safe_size(paths.err_log)
    status.stdout_log_size = _safe_size(paths.stdout_log)
    status.progress_log_size = _safe_size(paths.progress_log)
    status.progress_tail = _tail_last_nonempty_line(paths.progress_log)
    progress_size = status.err_log_size + status.stdout_log_size + status.progress_log_size
    if progress_size != last_progress_size:
        last_progress_size = progress_size
        last_progress = time.monotonic()
    status.idle_seconds = time.monotonic() - last_progress
    if status.idle_seconds >= stall_timeout:
        _kill_pid(pid)
        status.status = "STALLED"
        status.exit_code = 5
        status.detail = (
            f"no log progress for {stall_timeout}s "
            f"(pid {pid}, last size {last_progress_size}B)"
        )
        _emit_log(log_prefix, agent, status)
        return status, warned_early_error, last_progress, last_progress_size
    return None, warned_early_error, last_progress, last_progress_size


def monitor_agent(
    agent: str,
    pr: int,
    timeout: int,
    stall_timeout: int,
    poll: int,
    require_result: bool,
    no_early_error: bool = False,
    log_prefix: str = "",
    stem_template: str = DEFAULT_STEM_TEMPLATE,
) -> AgentStatus:
    """1 agent を監視する。

    `no_early_error=True` のとき、EARLY_ERROR 検知 (FATAL/WARN とも) を完全に無効化し、
    hard timeout / stall / sentinel / result.json のみで判定する。
    """
    paths = AgentPaths.for_(agent, pr, stem_template)
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
        if agent == "codex":
            status.sentinel_seen = _scan_codex_sentinel(paths.err_log)

        # codex は `tokens used` sentinel を出した後もプロセスが exit せず常駐し続ける
        # ケースがある (実機で観測)。result.json は正常に書かれているのに alive=True の
        # まま stall_timeout に達して STALLED 化してしまう。sentinel + result.json が
        # 揃った瞬間に対象プロセスを kill して OK 判定で返す。
        completed = _check_early_completion(
            agent, paths, status, pid, alive, cmdline_validated, started_wall, log_prefix,
        )
        if completed is not None:
            return completed

        # result.json が書かれた後もプロセスがハング��るケース (gemini で観測:
        # MCP サーバー切断待ち等��� exit しない)。sentinel 機構を持たない agent 向け
        # の fallback: result.json の mtime が RESULT_AGE_GRACE 秒以上前であれば
        # 完了とみなし、プロセスを kill → OK。
        # 安全条件:
        #   - cmdline_validated: PID 再利用でない (または検証不能環境) ことを確認済み
        #   - mtime >= started_wall: 前 round の stale result.json を拾わない
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

        terminated, warned_early_error, last_progress, last_progress_size = (
            _check_termination_conditions(
                agent, paths, status, pid, alive, elapsed, timeout, stall_timeout,
                last_progress, last_progress_size, no_early_error,
                warned_early_error, require_result, log_prefix,
            )
        )
        if terminated is not None:
            return terminated

        # poll 中の進捗ログ
        _emit_progress(log_prefix, agent, status)
        time.sleep(poll)


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
    # 後方互換: cross-review は位置引数 `target` で codex / gemini / both を渡す。
    # 4 ランタイム任意の組み合わせは `--agents` で渡す（どちらか一方だけを使う）。
    p.add_argument("target", nargs="?", choices=["codex", "gemini", "both"])
    p.add_argument("--agents", default=None,
                   help="監視対象をカンマ区切りで指定 (例: claude,kiro)。"
                        "位置引数 target の代わりに使う")
    p.add_argument("--tmp-dir", default=None,
                   help="一時ファイルの置き場所。未指定時は env "
                        f"({' / '.join(TMP_DIR_ENV_VARS)}) と worktree から解決する")
    p.add_argument("--stem-template", default=DEFAULT_STEM_TEMPLATE,
                   help="一時ファイル名の骨格。`{agent}` と `{id}` を埋める "
                        f"(default: {DEFAULT_STEM_TEMPLATE})")
    # env (MONITOR_TIMEOUT / MONITOR_POLL) は呼び出し時に safe parse で読む。
    # 非数値設定でも fixed default (`DEFAULT_TIMEOUT` / `DEFAULT_POLL`) に戻す。
    timeout_default = _safe_int_env("MONITOR_TIMEOUT", DEFAULT_TIMEOUT)
    poll_default = _safe_int_env("MONITOR_POLL", DEFAULT_POLL)
    p.add_argument("--timeout", type=int, default=timeout_default,
                   help=f"hard timeout in seconds (default: {timeout_default})")
    p.add_argument("--stall-timeout", type=int, default=None,
                   help="stall timeout (err.log no progress) in seconds. "
                        "未指定時は agent 別既定 (codex=180, gemini=480) または "
                        "env MONITOR_STALL_<AGENT> / MONITOR_STALL を参照")
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

    if args.agents:
        agents = [a.strip() for a in args.agents.split(",") if a.strip()]
        if not agents:
            p.error("--agents が空です")
    elif args.target:
        agents = ["codex", "gemini"] if args.target == "both" else [args.target]
    else:
        p.error("target か --agents のどちらかを指定してください")

    if args.tmp_dir:
        global _TMP_DIR_OVERRIDE
        _TMP_DIR_OVERRIDE = pathlib.Path(args.tmp_dir).resolve()

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
            stem_template=args.stem_template,
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
            "stdout_log_size": st.stdout_log_size,
            "progress_log_size": st.progress_log_size,
            "progress_tail": st.progress_tail,
            "idle_seconds": round(st.idle_seconds, 1),
            "result_exists": st.result_exists,
            "sentinel_seen": st.sentinel_seen,
        }, ensure_ascii=False))

    # exit code: 全エージェントの最大値（OK=0 が最良、それ以外は失敗）
    sys.exit(max(results[a].exit_code for a in agents))


if __name__ == "__main__":
    main()
