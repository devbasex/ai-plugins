"""指標のファイル（Markdown）と、計画・報告の「指標の測定」の節を組む（#1319）。

**純粋な処理だけを置く。** 読み取った指標から文字列を組むだけで、起動も書き出しもしない。
測れなかった理由は `codemetrics.REASONS` の値をそのまま書く（I5）。3 つの出力（指標の
ファイル・計画・報告）はどれもこのモジュールを通す。
"""

from __future__ import annotations

from typing import Any, Optional

import mdtable

from . import codemetrics as cm

# 表の上限（行）。要約の件数は全件から数える。
TOP_FUNCTIONS_MAIN = 30
TOP_FUNCTIONS_TEST = 10
TOP_FILES_MAIN = 20
TOP_FILES_TEST = 10
TOP_CLONES_MAIN = 20
TOP_CLONES_TEST = 10

# 要約の閾値。認知的複雑度は complexipy の既定の上限 15 を、CC は mccabe の既定の上限 10 を超える値。
COGNITIVE_THRESHOLD = 16
CC_THRESHOLD = 11

_ROLE_LABELS = {"main": "本体", "test": "テスト"}
_SOURCE_LABELS = {cm.SOURCE_DEFAULT: "既定", cm.SOURCE_DECLARED: "宣言"}


def _metric_cell(value: Any) -> str:
    return "—" if value is None else f"{value:,}" if isinstance(value, int) else str(value)


def mapping_label(config: dict[str, Any]) -> str:
    """言語とツールの対応の出所。宣言を使わなかったときは理由を添える（前提 8）。"""
    source = config.get("source")
    declaration = config.get("declaration") or cm.DECLARATION_FILE
    if source == cm.SOURCE_INVALID:
        return f"既定（{cm.DECLARATION_INVALID}: {config.get('error') or '—'}）"
    if source == cm.SOURCE_DECLARED:
        return f"宣言（{declaration}）"
    return f"既定（{declaration} は無い）"


def _heading(result: dict[str, Any]) -> str:
    parts = [str(result.get("tool") or "—")]
    if result.get("version"):
        parts.append(str(result["version"]))
    if result.get("runner"):
        parts.append(str(result["runner"]))
    return " ・ ".join(parts)


def _summary(functions: list[dict[str, Any]], files: list[dict[str, Any]], role: str, python: bool) -> str:
    fns = [f for f in functions if f.get("role") == role]
    n_files = sum(1 for f in files if f.get("role") == role)
    parts = [f"{n_files:,} ファイル", f"関数 {len(fns):,} 個"]
    if python:
        cog = [f["cognitive"] for f in fns if f.get("cognitive") is not None]
        parts.append(f"認知的複雑度 {COGNITIVE_THRESHOLD} 以上 {sum(1 for v in cog if v >= COGNITIVE_THRESHOLD):,} 個")
        parts.append(f"最大 {_metric_cell(max(cog) if cog else None)}")
    cc = [f["cc"] for f in fns if f.get("cc") is not None]
    parts.append(f"CC {CC_THRESHOLD} 以上 {sum(1 for v in cc if v >= CC_THRESHOLD):,} 個")
    parts.append(f"最大 {_metric_cell(max(cc) if cc else None)}")
    return f"{_ROLE_LABELS[role]}: " + "・".join(parts)


def _function_order(python: bool):
    def key(f: dict[str, Any]) -> tuple:
        primary = f.get("cognitive") if python else f.get("cc")
        return (-(primary if primary is not None else -1), -int(f.get("lines") or 0), str(f.get("path")), str(f.get("symbol")))

    return key


def _function_table(functions: list[dict[str, Any]], role: str, python: bool, limit: int) -> list[str]:
    rows = sorted((f for f in functions if f.get("role") == role), key=_function_order(python))
    order = "認知的複雑度" if python else "CC"
    lines = [f"### 複雑な関数（{_ROLE_LABELS[role]}・{order}の降順・上位 {limit}）", ""]
    if not rows:
        return [*lines, "（なし）", ""]
    if python:
        headers = ["ファイル", "関数", "認知", "CC", "分岐", "文", "引数", "return", "行"]
        body = [
            (
                f["path"],
                f["symbol"],
                _metric_cell(f.get("cognitive")),
                _metric_cell(f.get("cc")),
                _metric_cell(f.get("branches")),
                _metric_cell(f.get("statements")),
                _metric_cell(f.get("args")),
                _metric_cell(f.get("returns")),
                _metric_cell(f.get("lines")),
            )
            for f in rows[:limit]
        ]
    else:
        headers = ["ファイル", "関数", "CC", "行"]
        body = [(f["path"], f["symbol"], _metric_cell(f.get("cc")), _metric_cell(f.get("lines"))) for f in rows[:limit]]
    return [*lines, mdtable.table_markdown(headers, body), ""]


def _file_table(files: list[dict[str, Any]], role: str, limit: int) -> list[str]:
    rows = sorted((f for f in files if f.get("role") == role), key=lambda f: (-int(f.get("lines") or 0), str(f.get("path"))))
    lines = [f"### ファイル（{_ROLE_LABELS[role]}・行数の降順・上位 {limit}）", ""]
    if not rows:
        return [*lines, "（なし）", ""]
    body = [
        (f["path"], _metric_cell(f.get("lines")), _metric_cell(f.get("functions")), _metric_cell(f.get("max_function_lines")))
        for f in rows[:limit]
    ]
    return [*lines, mdtable.table_markdown(["ファイル", "行", "関数", "最長の関数の行"], body), ""]


def _failure_text(result: dict[str, Any]) -> str:
    detail = result.get("detail")
    return f"{result.get('reason')}（{detail}）" if detail else str(result.get("reason"))


def _language_section(result: dict[str, Any], section: dict[str, Any]) -> list[str]:
    python = result.get("tool") == cm.TOOL_RUFF_COMPLEXIPY
    functions = section.get("functions") or []
    files = section.get("files") or []
    summary = " / ".join(_summary(functions, files, role, python) for role in ("main", "test"))
    if result.get("unreadable_files"):
        summary += f" / 読めなかったファイル {result['unreadable_files']:,} 本"
    lines = [f"## {result['language']}（{_heading(result)}）", "", summary, ""]
    lines += _function_table(functions, "main", python, TOP_FUNCTIONS_MAIN)
    lines += _function_table(functions, "test", python, TOP_FUNCTIONS_TEST)
    lines += _file_table(files, "main", TOP_FILES_MAIN)
    lines += _file_table(files, "test", TOP_FILES_TEST)
    return lines


def _clone_order(c: dict[str, Any]) -> tuple:
    first = (c.get("locations") or [{}])[0]
    return (-int(c.get("lines") or 0), str(first.get("path")), int(first.get("start") or 0))


def _clone_table(clones: list[dict[str, Any]], role: str, limit: int) -> list[str]:
    rows = sorted((c for c in clones if c.get("role") == role), key=_clone_order)
    lines = [f"### 重複の箇所（{_ROLE_LABELS[role]}・行の降順・上位 {limit}）", ""]
    if not rows:
        return [*lines, "（なし）", ""]
    body = [
        (
            _metric_cell(c.get("lines")),
            c.get("format") or "—",
            " ・ ".join(f"{loc['path']}:{loc['start']}-{loc['end']}" for loc in c["locations"]),
        )
        for c in rows[:limit]
    ]
    return [*lines, mdtable.table_markdown(["行", "形式", "場所"], body), ""]


def _duplication_line(result: dict[str, Any], clones: list[dict[str, Any]]) -> str:
    who = f"{', '.join(result.get('languages') or [])}（{_heading(result)}）"
    if result.get("status") != cm.MEASURED:
        return f"{who}: {_failure_text(result)}"
    mine = [c for c in clones if c.get("tool") == result.get("tool")]
    main = sum(1 for c in mine if c.get("role") == "main")
    total = int(result.get("total_lines") or 0)
    dup = int(result.get("duplicated_lines") or 0)
    share = f"{dup / total * 100:.2f}%" if total else "—"
    return f"{who}: 本体: {main:,} 箇所 / テスト: {len(mine) - main:,} 箇所 / 重複の行 {dup:,}（全 {total:,} 行の {share}）"


def metrics_markdown(
    *,
    run_id: Any,
    head: Optional[str],
    scope: list[str],
    config: dict[str, Any],
    ignored: int,
    languages: list[dict[str, Any]],
    sections: dict[str, dict[str, Any]],
    duplication: list[dict[str, Any]],
    clones: list[dict[str, Any]],
) -> str:
    """指標のファイルの本文。上位だけを載せ、要約の件数は全件から数える（決定 4）。"""
    lines = [
        f"# 指標（cross-refactoring rf{run_id}）",
        "",
        f"- 測った版: {(head or '—')[:7]} / 対象範囲: {' '.join(scope) or '—'}",
        "- 載せたもの: 対象範囲の中で git が追跡しているファイル。本体とテストを分けて載せる。上位だけで、全件ではない",
        f"- 言語とツールの対応: {mapping_label(config)}",
        f"- 言語を判定しなかったファイル: {ignored:,} 本",
        "- 同じ名前の指標でも、ツールが違えば値の意味が違う。言語をまたいで比べない",
        "",
        "## 測れなかった言語",
        "",
    ]
    failed = [r for r in languages if r.get("status") != cm.MEASURED]
    if failed:
        lines.append(
            mdtable.table_markdown(
                ["言語", "ツール", "ファイル", "理由"],
                [(r["language"], r.get("tool") or "—", _metric_cell(r.get("files")), _failure_text(r)) for r in failed],
            )
        )
    else:
        lines.append("（なし）")
    lines.append("")
    for result in languages:
        if result.get("status") == cm.MEASURED:
            lines += _language_section(result, sections.get(result["language"]) or {})
    lines += [f"## 重複（最小 {cm.DUPLICATE_MIN_LINES} 行）", ""]
    if not duplication:
        lines += ["（重複を探す言語が無い）", ""]
    else:
        lines += [_duplication_line(r, clones) for r in duplication]
        lines.append("")
        lines += _clone_table(clones, "main", TOP_CLONES_MAIN)
        lines += _clone_table(clones, "test", TOP_CLONES_TEST)
    return "\n".join(lines).rstrip() + "\n"


def _result_cell(result: dict[str, Any], duplication: bool) -> str:
    if result.get("status") == cm.MEASURED:
        return f"測った（{int(result.get('clones') or 0):,} 箇所）" if duplication else "測った"
    return str(result.get("reason") or "—")


def _seconds_cell(result: dict[str, Any]) -> str:
    seconds = result.get("seconds")
    return "—" if not seconds and result.get("status") != cm.MEASURED else f"{float(seconds or 0):.1f}"


def record_lines(state: dict[str, Any]) -> list[str]:
    """計画と完了報告の「指標の測定」の節（AC19 AC20）。両方がこの並びを使う。"""
    record = state.get("code_metrics")
    lines = ["## 指標の測定", ""]
    if not isinstance(record, dict):
        return [*lines, "（記録なし）", ""]
    config = record.get("config") or {}
    status = record.get("status") or "—"
    limit = (state.get("limits") or {}).get("measure_timeout")
    if status == cm.STATUS_PENDING:
        return [*lines, f"- 状態: {status}（測っていない）", ""]
    if status == cm.STATUS_DISABLED:
        return [*lines, f"- 状態: {status}（--no-code-metrics で測らなかった）", ""]
    seconds = record.get("seconds")
    lines.append(
        f"- 状態: {status} / 言語とツールの対応: {mapping_label(config)} / 所要: "
        f"{'—' if seconds is None else f'{seconds} 秒'}（使えた時間 "
        f"{_metric_cell(record.get('deadline_seconds'))} 秒 / 上限 {_metric_cell(limit)} 秒）"
    )
    lines.append(f"- 指標のファイル: {record.get('file') or '（作らなかった）'}")
    if record.get("error"):
        lines.append(f"- 書き出せなかった理由: {record['error']}")
    rows = [
        (
            r.get("language"),
            r.get("tool") or "—",
            r.get("version") or "—",
            r.get("runner") or "—",
            _metric_cell(r.get("files")),
            _seconds_cell(r),
            _result_cell(r, False),
        )
        for r in record.get("languages") or []
    ]
    rows += [
        (
            f"重複: {', '.join(r.get('languages') or [])}",
            r.get("tool") or "—",
            r.get("version") or "—",
            r.get("runner") or "—",
            _metric_cell(r.get("files")),
            _seconds_cell(r),
            _result_cell(r, True),
        )
        for r in record.get("duplication") or []
    ]
    if rows:
        lines += ["", mdtable.table_markdown(["言語", "ツール", "版", "ランナー", "ファイル", "秒", "結果"], rows)]
    lines.append("")
    return lines


def stderr_line(result: dict[str, Any], duplication: bool = False) -> str:
    """`measure` が標準エラーへ出す 1 行。"""
    who = f"重複: {result.get('tool')}" if duplication else f"{result['language']}: {result.get('tool') or '—'}"
    if result.get("status") != cm.MEASURED:
        return f"⚠ {who} {_failure_text(result)}"
    extra = f" ・ {int(result.get('clones') or 0):,} 箇所" if duplication else ""
    versions = " ・ ".join(v for v in (result.get("version"), result.get("runner")) if v)
    return f"✅ {who}（{versions}）{_metric_cell(result.get('files'))} ファイル {float(result.get('seconds') or 0):.1f} 秒{extra}"
