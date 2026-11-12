"""CI テストの宣言（`test.ci.only`）を決める（#3056 の I1）。標準ライブラリだけで動く純粋な関数。

- 書くのは、`kind` が `test`（未指定を含む）の suite のうち 1 つ以上が `ci_jobs` を持ち、そのジョブのファイル（`#` より前）が
  リポジトリにあるときだけ
- 条件を満たさず、前の値が解析の書いたもの（`analysis.written` の指紋が一致）なら消す
- `false` と、人が書いた `true`（指紋が一致しない）は変えない
"""

from __future__ import annotations

from typing import Any, Callable

from .merge import is_unknown, value_digest

KEY = "test.ci.only"  # `analysis.written` に持つ指紋のキー


def ci_job_files(test: dict) -> list[str]:
    """テストの suite（`kind` が `test` か未指定）の `ci_jobs` が指すワークフローのファイル（`#` より前）。"""
    files: list[str] = []
    for suite in test.get("suites") or []:
        if not isinstance(suite, dict) or (suite.get("kind") or "test") != "test":
            continue
        for job in suite.get("ci_jobs") or []:
            path = str(job).split("#", 1)[0]
            if path and path not in files:
                files.append(path)
    return files


def runs_on_ci(test: dict, exists: Callable[[str], bool]) -> bool:
    """I1 の条件: テストの suite の `ci_jobs` のファイルが 1 つ以上リポジトリにある。"""
    return any(exists(path) for path in ci_job_files(test))


def declare(doc: dict, previous: dict | None, written_before: dict, exists: Callable[[str], bool]) -> tuple[dict, str, str | None]:
    """`doc`（書き出す宣言）の `test.ci.only` を決め、(宣言, 出力の 1 行, 指紋か None) を返す。

    `previous` は書き出しの前の宣言（解析が `test` を置き換えても前の宣言の値を読む）。指紋が None なら `written` に持たない。
    """
    test = doc.get("test")
    if not isinstance(test, dict) or is_unknown(test):
        return doc, "変えない（test が不明）", None
    prev_test = (previous or {}).get("test")
    prev_ci = prev_test.get("ci") if isinstance(prev_test, dict) else None
    cur = prev_ci.get("only") if isinstance(prev_ci, dict) else None
    ours = cur is not None and written_before.get(KEY) == value_digest(cur)
    if cur is False or (cur is True and not ours):
        doc = _put(doc, cur)
        return doc, "変えない（人が書いた値）", None
    if runs_on_ci(test, exists):
        verb = "変えない（条件を満たす）" if cur is True else "書いた"
        return _put(doc, True), verb, value_digest(True)
    if cur is True:
        return _put(doc, None), "消した（テストの suite の CI のジョブが無い）", None
    return _put(doc, None), "変えない（テストの suite の CI のジョブが無い）", None


def _put(doc: dict, value: Any) -> dict:
    """`doc["test"]["ci"]["only"]` を `value` にした写し。None なら消し、空になった `ci` も消す。"""
    test = dict(doc["test"])
    ci = dict(test.get("ci") or {})
    if ci.get("only") == value or (value is None and "only" not in ci):
        return doc
    if value is None:
        ci.pop("only", None)
    else:
        ci["only"] = value
    if ci:
        test["ci"] = ci
    else:
        test.pop("ci", None)
    return {**doc, "test": test}
