"""監視のログの読み取りと致命の判定（#1142 の C3 で `monitor.py` から分けた）。

err.log・stdout.log の末尾を読み、照合の表（`monitor_patterns`）で利用上限・致命・警告を見分ける。
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Optional

import monitor_patterns
import monitor_types


@dataclass(frozen=True)
class EarlyFatal:
    """早期の致命の一致。どのファイルで・何が・理由は何か（`None` なら `early_error`）。"""
    source: str
    message: str
    reason: Optional[str] = None


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
    data = monitor_patterns._strip_ansi(data)
    benign_patterns = monitor_patterns.EARLY_ERROR_BENIGN if benign is None else benign

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
            if monitor_patterns._match_is_quoted(line, m.start() - line_start, m.end() - line_start):
                continue
            return line.strip()
    return None


def _scan_early_fatal(path: pathlib.Path) -> Optional[str]:
    """err.log の致命の一致（kill 対象）。**利用上限も含む。**

    理由（`usage_limit` か `early_error` か）の区別はここでは行わず、`_early_error` が
    `USAGE_LIMIT_FATAL` を先に照合して決める。この関数は「止めるべき文言があるか」だけを返す。
    """
    hit = _scan_patterns(path, monitor_patterns.USAGE_LIMIT_FATAL) or _scan_patterns(path, monitor_patterns.EARLY_ERROR_FATAL)
    if hit:
        return hit
    return _scan_patterns(
        path,
        monitor_patterns.EARLY_ERROR_FATAL_WARNING_SHAPED,
        benign=monitor_patterns.EARLY_ERROR_BENIGN_KEEP_WARNINGS,
    )


def _scan_early_warn(path: pathlib.Path) -> Optional[str]:
    return _scan_patterns(path, monitor_patterns.EARLY_ERROR_WARN)


def _scan_claude_stdout(path: pathlib.Path, patterns: list[re.Pattern[str]]) -> Optional[str]:
    """claude の JSON 出力を `patterns` で照合し、一致の前後 80 文字を返す。

    `_scan_patterns()` は使わない。あちらは行単位の benign 判定と引用符パリティ判定を
    行うが、JSON は 1 行に多数の引用符を含むため、パリティ判定が「引用の内側」を
    誤って真にして致命を取りこぼす。
    """
    data = _read_tail(path, 200 * 1024)
    if data is None:
        return None
    data = monitor_patterns._strip_ansi(data)
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
    return _scan_claude_stdout(path, monitor_patterns.CLAUDE_STDOUT_FATAL)


def _scan_claude_stdout_usage_limit(path: pathlib.Path) -> Optional[str]:
    """claude の JSON 出力から利用上限（`"api_error_status":429`）を検出する。"""
    return _scan_claude_stdout(path, monitor_patterns.CLAUDE_STDOUT_USAGE_LIMIT)


def _scan_codex_sentinel(path: pathlib.Path) -> bool:
    tail = _read_tail(path, 64 * 1024)
    if tail is None:
        return False
    return bool(monitor_patterns.CODEX_SENTINEL.search(tail))


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


def _scan_usage_limit(paths: monitor_types.AgentPaths, agent: str) -> EarlyFatal | None:
    """利用上限の文言。err.log は全担当、stdout.log は claude だけ JSON 向けの照合で見る。"""
    hit = _scan_patterns(paths.err_log, monitor_patterns.USAGE_LIMIT_FATAL)
    if hit:
        return EarlyFatal("err.log", hit, "usage_limit")
    if monitor_types._agent_runtime(agent) == "claude":
        hit = _scan_claude_stdout_usage_limit(paths.stdout_log)
        if hit:
            return EarlyFatal("stdout.log", hit, "usage_limit")
    return None


def _scan_fatal(paths: monitor_types.AgentPaths, agent: str) -> EarlyFatal | None:
    """利用上限以外の致命（理由は `early_error`）。致命 → 警告の見た目の致命の順。"""
    hit = _scan_early_fatal(paths.err_log)
    if hit:
        return EarlyFatal("err.log", hit)
    if monitor_types._agent_runtime(agent) == "claude":
        hit = _scan_claude_stdout_fatal(paths.stdout_log)
        if hit:
            return EarlyFatal("stdout.log", hit)
    return None


def _early_error(
    paths: monitor_types.AgentPaths,
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
