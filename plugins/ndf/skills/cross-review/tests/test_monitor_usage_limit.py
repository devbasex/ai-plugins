"""利用上限と CLI の上限の文言の検知（#729 の AC2〜AC7、#619）。

監視は PATH の偽物ではなく **実プロセス** を相手にする（`test_monitor_outcome_file.py` と
同じ形）。終わったプロセスの pid ファイルと、文言を 1 行書いた err.log / stdout.log を
置いて監視を呼び、状態・終了コード・監視の結果ファイルの `reason` を見る。

- AC2: `Monthly request limit reached`（kiro の実物）→ `EARLY_ERROR` / `usage_limit`
- AC3: `"api_error_status":429`（claude）は err.log（全担当）と stdout.log（claude だけ）で拾う
- AC4: 既存の `quota exceeded` / `rate limit exceeded` / `HTTP/x 429` は `usage_limit`、
  `HTTP/x 401` / `403` と他の致命は `early_error` のまま
- AC5: 結果ファイル無しで終わり err.log に `print timeout after <時間> with turn in progress`
  → `NO_RESULT` / `cli_timeout`。結果ファイルがあれば `OK` / `ok`
- AC6: 表・引用・バッククォート・grep 形式の中の文言は一致しない。stdout.log の JSON は除外を掛けない
- AC7: `usage_limit` は監視の結果ファイルと記録の `reason` に入り、標準出力のキーは変わらない
- 照合の順序は利用上限 → 致命 → 警告の見た目の致命（設計文書の未確認 5 を固定する）
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_MONITOR_LIB = _HERE.parents[2] / "scripts" / "lib" / "monitor.py"

STDOUT_KEYS = {
    "agent", "status", "exit_code", "pid", "elapsed", "detail", "err_log_size",
    "stdout_log_size", "progress_log_size", "progress_tail", "idle_seconds",
    "result_exists", "sentinel_seen",
}

# 設計文書「実測」の 10 行。見出しはそのまま、行は実物の形に合わせて組み立てた。
KIRO_LIMIT = "Monthly request limit reached"
CLAUDE_429 = ('{"type":"result","subtype":"success","is_error":false,'
              '"api_error_status":429,"result":"rate limited"}')
CLAUDE_429_SPACED = '{"type": "result", "api_error_status" : 429, "is_error": false}'
HTTP_429 = "HTTP/1.1 429 Too Many Requests"
HTTP_401 = "HTTP/1.1 401 Unauthorized"
IN_TABLE = "| usage_limit | Monthly request limit reached | 利用上限 |"
IN_BACKTICKS = "see `Monthly request limit reached` in err.log"
IN_QUOTE = "> Monthly request limit reached"
AGY_PRINT_TIMEOUT = "[agy] print timeout after 10m0s with turn in progress; returning partial output"
IN_GREP = ('plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py:12:'
           '    KIRO_LIMIT = "Monthly request limit reached"')

# (見出し, 行, 利用上限の表の一致, CLI の上限の表の一致, 利用上限を除いた致命の表の一致)
# 設計文書の「現行fatal」は変更前の表で測った値。HTTP 429 はこの変更で利用上限の表へ移る（AC4）。
TEN_LINES = [
    ("kiro 実物", KIRO_LIMIT, True, False, False),
    ("claude 429 JSON 1 行", CLAUDE_429, True, False, False),
    ("claude 429 空白あり", CLAUDE_429_SPACED, True, False, False),
    ("HTTP 429 行", HTTP_429, True, False, False),
    ("HTTP 401 行", HTTP_401, False, False, True),
    ("表の中", IN_TABLE, False, False, False),
    ("バッククォート", IN_BACKTICKS, False, False, False),
    ("引用行", IN_QUOTE, False, False, False),
    ("agy print timeout", AGY_PRINT_TIMEOUT, False, True, False),
    ("grep 形式", IN_GREP, False, False, False),
]


# 実物の行の逐語の正本（#811）。確定仕様
# （docs/specifications/cross-review-launch-outcome.md）の「背景」は出所と形の種類だけを持ち、
# 文言そのものはここにある。
# 推測で作った文言は入れない。出所は記録（`~/.codex/sessions` / `~/.claude/projects`）と
# 導入済みの実行ファイルの文字列である。
CODEX_USAGE_LIMIT = ("You've hit your usage limit. Visit "
                     "https://chatgpt.com/codex/settings/usage to purchase more credits "
                     "or try again at 5:44 PM.")
CODEX_USAGE_LIMIT_PREFIXED = f"ERROR: {CODEX_USAGE_LIMIT}"
CODEX_RETRY_429 = "ERROR: exceeded retry limit, last status: 429"
CODEX_RETRY_503 = "ERROR: exceeded retry limit, last status: 503 Service Unavailable"
CODEX_BAD_REQUEST = ('ERROR: {"type":"error","status":400,"error":{"type":'
                     '"invalid_request_error","message":"The \'ndf-no-such-model-xyz\' model '
                     'is not supported when using Codex with a ChatGPT account."}}')
CLAUDE_WEEKLY = "You've hit your weekly limit · resets Sep 22, 6am (UTC)"
CLAUDE_SESSION = "You've hit your session limit · resets 6:30pm (UTC)"
CLAUDE_INDIVIDUAL_SPEND = ("You've hit your individual spend limit · run /usage-credits "
                           "to raise it, or visit claude.ai/admin-settings/usage")
CLAUDE_MONTHLY_SPEND = ("You've hit your monthly spend limit. Run /usage-credits to manage "
                        "your limit and keep using the model or switch models to continue "
                        "this chat.")
CLAUDE_BARE_LIMIT = "You've hit your limit"

# 実物として一致すべき 7 行（codex 2 形 + claude 5 形）。行頭の印の有無は codex が決める。
MEASURED_USAGE_LIMIT_LINES = [
    ("codex 利用上限", CODEX_USAGE_LIMIT),
    ("codex 利用上限 行頭の印あり", CODEX_USAGE_LIMIT_PREFIXED),
    ("codex 再試行の上限 429", CODEX_RETRY_429),
    ("claude 週", CLAUDE_WEEKLY),
    ("claude セッション", CLAUDE_SESSION),
    ("claude 個人の支出", CLAUDE_INDIVIDUAL_SPEND),
    ("claude 月の支出", CLAUDE_MONTHLY_SPEND),
    ("claude 期間なし", CLAUDE_BARE_LIMIT),
]

# 一致してはいけない行。状態が 429 でない再試行の上限・400 の行・引用と差分の形。
NOT_USAGE_LIMIT_LINES = [
    ("codex 再試行の上限 503", CODEX_RETRY_503),
    ("codex 400 の行", CODEX_BAD_REQUEST),
    ("表（バッククォートあり）", f"| usage_limit | `{CODEX_USAGE_LIMIT}` | 利用上限 |"),
    ("表（バッククォートなし）", f"| usage_limit | {CLAUDE_WEEKLY} | 利用上限 |"),
    ("本文のバッククォート", f"上限の文言は `{CLAUDE_SESSION}` である"),
    ("本文の「」", f"上限の文言は「{CLAUDE_WEEKLY}」である"),
    ("文の途中", f"担当は {CODEX_USAGE_LIMIT} を出して止まった"),
    ("リスト", f"- {CLAUDE_MONTHLY_SPEND}"),
    ("引用", f"> {CLAUDE_WEEKLY}"),
    ("grep 形式", f"plugins/ndf/scripts/lib/monitor.py:160:    # {CLAUDE_BARE_LIMIT}"),
    ("Python の文字列", f'    CLAUDE_WEEKLY = "{CLAUDE_WEEKLY}"'),
    ("差分の追加行", f"+{CODEX_USAGE_LIMIT_PREFIXED}"),
    ("差分の文脈行", f" {CLAUDE_SESSION}"),
]


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.write_text(text + "\n", encoding="utf-8")
    return path


# ---------- 照合の単体（AC4 / AC6） ----------

@pytest.mark.parametrize(("label", "line", "usage_hit", "cli_timeout_hit", "fatal_hit"), TEN_LINES,
                         ids=[t[0] for t in TEN_LINES])
def test_ten_measured_lines_match_as_the_design_records(tmp_path, monitor_mod, label, line,
                                                        usage_hit, cli_timeout_hit, fatal_hit):
    log = _write(tmp_path / "err.log", line)
    assert (monitor_mod._scan_patterns(log, monitor_mod.USAGE_LIMIT_FATAL) is not None) is usage_hit
    assert (monitor_mod._scan_patterns(log, monitor_mod.CLI_TIMEOUT_AFTER_EXIT) is not None) is cli_timeout_hit
    assert (monitor_mod._scan_patterns(log, monitor_mod.EARLY_ERROR_FATAL) is not None) is fatal_hit
    # 止めるべき文言があるかを返す `_scan_early_fatal` は、どちらの表の一致も拾う（既存テストの契約）
    assert (monitor_mod._scan_early_fatal(log) is not None) is (usage_hit or fatal_hit)


@pytest.mark.parametrize(("label", "line"), MEASURED_USAGE_LIMIT_LINES,
                         ids=[t[0] for t in MEASURED_USAGE_LIMIT_LINES])
def test_measured_codex_and_claude_lines_are_usage_limits(tmp_path, monitor_mod, label, line):
    """AC1〜AC3 / AC6: 実測の実物の行は利用上限の表に一致する。"""
    log = _write(tmp_path / "err.log", line)
    assert monitor_mod._scan_patterns(log, monitor_mod.USAGE_LIMIT_FATAL) is not None


@pytest.mark.parametrize(("label", "line"), NOT_USAGE_LIMIT_LINES,
                         ids=[t[0] for t in NOT_USAGE_LIMIT_LINES])
def test_quoted_and_non_limit_lines_are_not_usage_limits(tmp_path, monitor_mod, label, line):
    """AC2 の後半 / AC5: 状態が 429 でない行と、引用・差分・文の途中は一致しない。"""
    log = _write(tmp_path / "err.log", line)
    assert monitor_mod._scan_patterns(log, monitor_mod.USAGE_LIMIT_FATAL) is None


def test_claude_stdout_json_is_matched_without_the_quote_exclusion(tmp_path, monitor_mod):
    """stdout.log の JSON は 1 行に引用符が多く、行単位の除外を掛けると取りこぼす。"""
    out = _write(tmp_path / "stdout.log", CLAUDE_429)
    assert monitor_mod._scan_claude_stdout_usage_limit(out) is not None
    assert monitor_mod._scan_claude_stdout_usage_limit(_write(tmp_path / "ok.log", '{"is_error":false}')) is None


@pytest.mark.parametrize("line", ["quota exceeded: please upgrade", "Rate limit exceeded for model", HTTP_429])
def test_existing_limit_matches_moved_to_usage_limit(tmp_path, monitor_mod, line):
    log = _write(tmp_path / "err.log", line)
    assert monitor_mod._scan_patterns(log, monitor_mod.USAGE_LIMIT_FATAL) is not None
    assert monitor_mod._scan_patterns(log, monitor_mod.EARLY_ERROR_FATAL) is None


@pytest.mark.parametrize("line", [HTTP_401, "HTTP/2 403 Forbidden", "Authentication failed: token",
                                  "Internal sandbox error: cannot start"])
def test_other_fatal_lines_stay_early_error(tmp_path, monitor_mod, line):
    log = _write(tmp_path / "err.log", line)
    assert monitor_mod._scan_patterns(log, monitor_mod.USAGE_LIMIT_FATAL) is None
    assert monitor_mod._scan_early_fatal(log) is not None


# ---------- 照合の順序（利用上限 → 致命 → 警告の見た目の致命） ----------

def _paths(monitor_mod, tmp_path, agent="kiro"):
    return monitor_mod.AgentPaths(
        agent=agent, pr=7,
        pidfile=tmp_path / "x.pid", err_log=tmp_path / "err.log",
        stdout_log=tmp_path / "stdout.log", progress_log=tmp_path / "progress.log",
        result=tmp_path / "result.json",
    )


@pytest.mark.parametrize("order", ["limit_first", "fatal_first"])
def test_usage_limit_wins_over_other_fatal_lines_in_either_order(tmp_path, monitor_mod, order):
    lines = [KIRO_LIMIT, "Authentication failed: retry"]
    if order == "fatal_first":
        lines.reverse()
    _write(tmp_path / "err.log", "\n".join(lines))
    fatal, _warn = monitor_mod._early_error(_paths(monitor_mod, tmp_path), "kiro", False)
    assert fatal is not None
    assert fatal.reason == "usage_limit"
    assert fatal.source == "err.log"


def test_usage_limit_wins_over_warning_shaped_fatal(tmp_path, monitor_mod):
    _write(tmp_path / "err.log",
           "WARNING: --trust-tools arg for custom tool foo\n" + KIRO_LIMIT)
    fatal, _warn = monitor_mod._early_error(_paths(monitor_mod, tmp_path), "kiro", False)
    assert fatal.reason == "usage_limit"


def test_fatal_without_usage_limit_has_no_reason(tmp_path, monitor_mod):
    _write(tmp_path / "err.log", "Authentication failed: retry")
    fatal, _warn = monitor_mod._early_error(_paths(monitor_mod, tmp_path), "kiro", False)
    assert fatal.reason is None
    assert fatal.message == "Authentication failed: retry"


def test_claude_stdout_usage_limit_is_seen_only_for_claude(tmp_path, monitor_mod):
    _write(tmp_path / "stdout.log", CLAUDE_429)
    fatal, _ = monitor_mod._early_error(_paths(monitor_mod, tmp_path, "claude"), "claude", False)
    assert (fatal.source, fatal.reason) == ("stdout.log", "usage_limit")
    fatal, _ = monitor_mod._early_error(_paths(monitor_mod, tmp_path, "kiro"), "kiro", False)
    assert fatal is None


def test_disabled_early_error_disables_usage_limit_too(tmp_path, monitor_mod):
    _write(tmp_path / "err.log", KIRO_LIMIT)
    assert monitor_mod._early_error(_paths(monitor_mod, tmp_path), "kiro", True) == (None, None)


def test_monitor_outcome_create_keeps_the_two_argument_form(monitor_mod):
    plain = monitor_mod.MonitorOutcome.create("EARLY_ERROR", "x")
    assert (plain.reason, plain.exit_code) == (None, 4)
    limited = monitor_mod.MonitorOutcome.create("EARLY_ERROR", "x", reason="usage_limit")
    assert limited.reason == "usage_limit"


# ---------- 監視を実プロセスで呼ぶ（AC2 / AC3 / AC4 / AC7） ----------

def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _run_monitor(tmp_dir: pathlib.Path, agent: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MONITOR_LIB), "7", "--agents", agent,
         "--tmp-dir", str(tmp_dir), "--poll", "1", *extra],
        capture_output=True, text=True, timeout=60,
    )


def _finished(tmp_dir: pathlib.Path, agent: str, *, err: str = "", stdout: str = "",
              result: bool = False) -> str:
    stem = f"{agent}-review-pr7"
    (tmp_dir / f"{stem}.pid").write_text(str(_dead_pid()))
    if err:
        _write(tmp_dir / f"{stem}-err.log", err)
    if stdout:
        _write(tmp_dir / f"{stem}-stdout.log", stdout)
    if result:
        (tmp_dir / f"{stem}-result.json").write_text('{"event": "APPROVE"}')
    return stem


def _outcome(tmp_dir: pathlib.Path, stem: str) -> dict:
    return json.loads((tmp_dir / f"{stem}-monitor.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("agent", "err", "stdout"), [
    ("kiro", KIRO_LIMIT, ""),            # AC2
    ("codex", CLAUDE_429, ""),           # AC3: err.log は全担当
    ("claude", "", CLAUDE_429),          # AC3: stdout.log は claude だけ
    ("claude", "", CLAUDE_429_SPACED),   # AC3: 空白の有無を問わない
    ("agy", "quota exceeded: upgrade", ""),  # AC4
    ("agy", HTTP_429, ""),               # AC4
])
def test_usage_limit_stops_the_agent_as_early_error_with_reason_usage_limit(tmp_path, agent, err, stdout):
    stem = _finished(tmp_path, agent, err=err, stdout=stdout)

    proc = _run_monitor(tmp_path, agent)

    assert proc.returncode == 4, proc.stderr
    outcome = _outcome(tmp_path, stem)
    assert (outcome["status"], outcome["reason"]) == ("EARLY_ERROR", "usage_limit")
    assert outcome["detail"].startswith("early error (fatal) in ")
    rows = [json.loads(l) for l in (tmp_path / "monitor-outcomes.jsonl").read_text().splitlines()]
    assert rows[-1]["reason"] == "usage_limit"
    # AC7: 標準出力のキーは変わらない
    out = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert len(out) == 1 and set(out[0]) == STDOUT_KEYS and out[0]["exit_code"] == 4


@pytest.mark.parametrize(("agent", "err"), [
    ("codex", CODEX_USAGE_LIMIT_PREFIXED),   # AC1
    ("codex", CODEX_USAGE_LIMIT),            # AC1
    ("codex", CODEX_RETRY_429),              # AC2
    ("claude", CLAUDE_WEEKLY),               # AC3
    ("claude", CLAUDE_SESSION),              # AC3
])
def test_measured_lines_stop_the_agent_with_reason_usage_limit(tmp_path, agent, err):
    """AC1〜AC3: 実測の行で担当が止まり、理由は利用上限、終了コードは 4。"""
    stem = _finished(tmp_path, agent, err=err)

    proc = _run_monitor(tmp_path, agent)

    assert proc.returncode == 4, proc.stderr
    outcome = _outcome(tmp_path, stem)
    assert (outcome["status"], outcome["reason"]) == ("EARLY_ERROR", "usage_limit")


def test_the_retry_limit_with_another_status_is_not_a_usage_limit(tmp_path):
    """AC2 の後半: 最後の状態が 429 以外なら利用上限にしない。"""
    stem = _finished(tmp_path, "codex", err=CODEX_RETRY_503)

    proc = _run_monitor(tmp_path, "codex")

    assert proc.returncode == 3, proc.stderr
    assert _outcome(tmp_path, stem)["reason"] == "missing"


def test_quoted_measured_lines_do_not_stop_the_agent(tmp_path):
    """AC5: 引用・差分・文の途中に出た実物の文言では止まらない。"""
    stem = _finished(tmp_path, "codex",
                     err="\n".join(line for _label, line in NOT_USAGE_LIMIT_LINES),
                     result=True)

    proc = _run_monitor(tmp_path, "codex")

    assert proc.returncode == 0, proc.stderr
    assert _outcome(tmp_path, stem)["reason"] == "ok"


@pytest.mark.parametrize("err", [HTTP_401, "HTTP/1.1 403 Forbidden", "Permission denied"])
def test_other_fatal_lines_keep_reason_early_error(tmp_path, err):
    stem = _finished(tmp_path, "kiro", err=err)

    proc = _run_monitor(tmp_path, "kiro")

    assert proc.returncode == 4, proc.stderr
    assert (_outcome(tmp_path, stem)["status"], _outcome(tmp_path, stem)["reason"]) == (
        "EARLY_ERROR", "early_error")


def test_claude_json_in_kiro_stdout_is_not_seen(tmp_path):
    """stdout.log を見るのは claude だけ。他の担当の stdout は結果なしのまま。"""
    stem = _finished(tmp_path, "kiro", stdout=CLAUDE_429)

    proc = _run_monitor(tmp_path, "kiro")

    assert proc.returncode == 3
    assert _outcome(tmp_path, stem)["reason"] == "missing"


def test_quoted_usage_limit_in_err_log_is_not_a_hit(tmp_path):
    stem = _finished(tmp_path, "kiro", err="\n".join([IN_TABLE, IN_BACKTICKS, IN_QUOTE, IN_GREP]),
                     result=True)

    proc = _run_monitor(tmp_path, "kiro")

    assert proc.returncode == 0, proc.stderr
    assert _outcome(tmp_path, stem)["reason"] == "ok"


# ---------- CLI の上限（AC5） ----------

def test_cli_timeout_without_result_is_no_result_with_reason_cli_timeout(tmp_path):
    stem = _finished(tmp_path, "agy", err=AGY_PRINT_TIMEOUT)

    proc = _run_monitor(tmp_path, "agy")

    assert proc.returncode == 3, proc.stderr
    outcome = _outcome(tmp_path, stem)
    assert (outcome["status"], outcome["reason"]) == ("NO_RESULT", "cli_timeout")
    rows = [json.loads(l) for l in (tmp_path / "monitor-outcomes.jsonl").read_text().splitlines()]
    assert rows[-1]["reason"] == "cli_timeout"


def test_cli_timeout_with_result_is_still_ok(tmp_path):
    """上限に当たっても結果を書き終えていれば使える。"""
    stem = _finished(tmp_path, "agy", err=AGY_PRINT_TIMEOUT, result=True)

    proc = _run_monitor(tmp_path, "agy")

    assert proc.returncode == 0, proc.stderr
    assert (_outcome(tmp_path, stem)["status"], _outcome(tmp_path, stem)["reason"]) == ("OK", "ok")


def test_cli_timeout_in_a_table_row_is_not_a_hit(tmp_path):
    stem = _finished(tmp_path, "agy", err=f"| cli_timeout | {AGY_PRINT_TIMEOUT} |")

    proc = _run_monitor(tmp_path, "agy")

    assert proc.returncode == 3
    assert _outcome(tmp_path, stem)["reason"] == "missing"
