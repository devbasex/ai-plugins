"""レビューの保留（`review_holds`）と、承認ゲートの直前の MVV 判定がそれを読むこと（#3959 の受け入れ条件 4。決定 2・11）。

判定は `test_mvv_gate` と同じく、LLM のアダプターと GitHub をスタブにし、git と記録のファイルは本物を使う。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ndf.domains.records import jsonl, mvv, mvv_gate
from ndf.domains.records import mvv_decide as md
from ndf.progress import gate

from .test_mvv_gate import DOCS, FOLLOW, HEAD, SHA, Ask, _hub, _inputs, notes, repo  # noqa: F401 フィクスチャを読み込む

PR = 5
NOW = "2026-11-24T00:00:0{}+00:00"
MVV = (1, SHA)
HIDDEN = "（伏せた）"


def _row(outcome: str, n: int = 0, key: str = "k", **extra: Any) -> dict[str, Any]:
    """PR #5 の今の MVV の記録の行（`n` 秒の時刻）。"""
    base = {"kind": md.MVV_REVIEW, "repo": "/m", "pr": PR, "key": key, "id": f"r1-{n}", "item": "Value 1", "outcome": outcome}
    return {**base, "at": NOW.format(n), "head": HEAD, "mvv_version": 1, "sha256": SHA, **extra}


def _deferred(n: int, defer: Any = "needs_user", **extra: Any) -> dict[str, Any]:
    return _row("deferred", n, reason=f"理由 {n}", **({} if defer is None else {"defer": defer}), **extra)


def _redline(n: int, item: str = "P1・C7", **extra: Any) -> dict[str, Any]:
    return _row("redline", n, item=item, text=f"本文 {n}", **extra)


OTHER_SHA = {"sha256": "b" * 64}
# （記録の行, 読めない行の数, 返す保留）
HOLDS = {
    # 設計の例: 直っていないレッドラインが、前の見送りより優先される
    "redline-over-deferred": ([_deferred(1), _deferred(2, "filed"), _redline(3)], 0, ["PR #5 の P1・C7（redline）: 本文 3"]),
    "needs-user-kept-past-filed": ([_deferred(1), _deferred(2, "filed")], 0, ["PR #5 の Value 1（deferred・needs_user）: 理由 1"]),
    "redline-fixed": ([_redline(1), _row("fixed", 2)], 0, []),
    "redline-fixed-again": ([_redline(1), _row("fixed", 2), _redline(3, "P1")], 0, ["PR #5 の P1・C7（redline）: 本文 3"]),
    "redline-fixed-under-another-mvv": ([_redline(1), _row("fixed", 2, mvv_version=9, **OTHER_SHA)], 0, []),
    "redline-items-joined": ([_redline(1, "P1"), _redline(2, "P1・C7")], 0, ["PR #5 の P1・C7（redline）: 本文 2"]),
    "redline-withheld": ([_redline(1, withheld=True)], 0, [f"PR #5 の P1・C7（redline）: {HIDDEN}"]),
    "filed-and-not-applicable": ([_deferred(1, "filed"), _deferred(2, "not_applicable", key="l")], 0, []),
    "no-defer": ([_deferred(1, None)], 0, ["PR #5 の Value 1（deferred・区分なし）: 理由 1"]),
    "unknown-defer": ([_deferred(1, "later")], 0, ["PR #5 の Value 1（deferred・later）: 理由 1"]),
    "deferred-then-unresolved": ([_deferred(1), _row("unresolved", 2)], 0, ["PR #5 の Value 1（deferred・needs_user）: 理由 1"]),
    "deferred-fixed": ([_deferred(1), _row("fixed", 2), _deferred(3, "filed")], 0, []),
    "fixed-by-another-sha": ([_deferred(1), _row("fixed", 2, **OTHER_SHA)], 0, ["PR #5 の Value 1（deferred・needs_user）: 理由 1"]),
    "deferred-of-another-sha": ([_deferred(1, **OTHER_SHA)], 0, []),
    # 同じ時刻なら追記の順で後の行が最後になる
    "same-time-fixed-last": ([_deferred(1), _row("fixed", 1)], 0, []),
    "older-fixed-appended-later": ([_deferred(2), _row("fixed", 1)], 0, ["PR #5 の Value 1（deferred・needs_user）: 理由 2"]),
    "withheld-last": ([_deferred(1, "filed"), _row("fixed", 2, withheld=True)], 0, ["PR #5 の Value 1（記録の自由記述が未完了）"]),
    "withheld-of-another-sha": ([_row("fixed", 1, withheld=True, **OTHER_SHA)], 0, []),
    "other-repo-and-pr": ([_redline(1, repo="/other"), _redline(2, pr=PR + 1)], 0, []),
    # head A の見送りの後に、head B で別のキーを直しただけでは外れない
    "other-key-fixed": ([_deferred(1), _row("fixed", 2, key="l", head="b" * 40)], 0, ["PR #5 の Value 1（deferred・needs_user）: 理由 1"]),
    "broken-rows": ([], 2, ["mvv-validation.jsonl に読めない行が 2 件ある"]),
}


@pytest.mark.parametrize(("rows", "broken", "expected"), HOLDS.values(), ids=HOLDS.keys())
def test_review_holds_keep_only_unfixed_redlines_and_deferrals_that_need_the_user(
    rows: list[dict[str, Any]], broken: int, expected: list[str]
) -> None:
    assert md.review_holds(rows, broken, "/m", [PR], MVV, hidden=HIDDEN) == expected


# --- 承認ゲートの直前の判定が保留を読む ---------------------------------------------------------------


def _write(rows: list[dict[str, Any]]) -> None:
    """記録のファイルへ、判定するリポジトリの今の MVV の行として追記する。"""
    for r in rows:
        jsonl.append_locked(mvv.record_path("mvv-validation"), {**r, "repo": RepoKey.value}, kind="mvv-validation")


class RepoKey:
    value = ""


def _unreadable(kind: str) -> Callable[[], None]:
    def make() -> None:
        path = mvv.record_path("mvv-validation")
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "directory":
            path.mkdir()
        elif kind == "dangling":
            os.symlink(path.parent / "nowhere", path)
        elif kind == "bytes":
            path.write_bytes(b'{"kind": "x"}\n\xff\xfe\n')
        else:
            path.write_text(json.dumps({"version": 999, "at": NOW.format(0), "kind": "sprint-mvv", "key": "k", "repo": "/m"}) + "\n")

    return make


# （記録の用意, skip, 保留の件数, why に記録に書けない理由があるか）
GATES = {
    "redline": (lambda: _write([_redline(1)]), False, 1, False),
    "redline-fixed": (lambda: _write([_redline(1), _row("fixed", 2)]), True, 0, False),
    "filed": (lambda: _write([_deferred(1, "filed")]), True, 0, False),
    "needs-user": (lambda: _write([_deferred(1)]), False, 1, False),
    # 読めない記録は保留の 1 件にし、判定は再利用せずに LLM で打つ。ディレクトリは判定の行の追記もできない
    "directory": (_unreadable("directory"), False, 1, True),
    "dangling-symlink": (_unreadable("dangling"), False, 1, False),
    "invalid-bytes": (_unreadable("bytes"), False, 1, False),
    "newer-version": (_unreadable("newer"), False, 1, False),
}


@pytest.mark.parametrize(("prepare", "skip", "held", "unwritten"), GATES.values(), ids=GATES.keys())
def test_fast_design_gate_is_not_skipped_while_the_review_holds_remain(
    repo: Path, notes: list[tuple[int, str]], prepare: Callable[[], None], skip: bool, held: int, unwritten: bool
) -> None:
    # 受け入れ条件 4: 直っていないレッドライン・利用者の判断が要る見送り・読めない記録は、fast でも「従う」の判定で省かない
    RepoKey.value = mvv.repo_key(repo)
    prepare()
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(PR, DOCS), ask)
    assert (out["skip"], len(out["holds"]), len(ask.calls), out["verdict"]) == (skip, held, 1, FOLLOW)
    assert any(w.startswith("判定を記録（mvv-validation.jsonl）に書けない: ") for w in out["why"]) is unwritten
    if not held:
        return
    [hold] = out["holds"]
    # 保留の文は holds にだけ置き、why には件数の固定の文だけ。PR のコメントと承認資料にも保留が載る
    assert "レビューの保留が 1 件ある（内容は保留の欄）" in out["why"] and not any(hold in w for w in out["why"])
    assert hold.startswith("PR #5 の ") or hold.startswith(mvv_gate.RECORDS_UNREADABLE)
    assert f"### レビューの保留\n\n- {hold}" in notes[-1][1]
    assert f"  - {hold}" in gate.mvv_lines(out, "design", PR, HEAD)


def test_records_are_read_once_and_a_broken_row_stops_the_reuse(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録は判定 1 回につき 1 回だけ読む。同じ材料の「従う」は再利用するが、その後ろに読めない行があれば再利用せず保留にする。"""
    reads: list[Path] = []
    real = jsonl.read_rows
    monkeypatch.setattr(jsonl, "read_rows", lambda path, **kw: (reads.append(path), real(path, **kw))[1])
    ask = Ask(FOLLOW)
    for _ in range(2):
        assert mvv_gate.gate(repo, "design", _inputs(), _hub(PR, DOCS), ask)["skip"] is True
    # 1 回目は記録がまだ無い（読まずに空とする）。2 回目は 1 回だけ読み、同じ材料の「従う」を再利用する
    assert (len(reads), len(ask.calls)) == (1, 1)
    [judged] = [r for r in real(mvv.record_path("mvv-validation"), kind="mvv-validation")[0] if r["kind"] == "sprint-mvv"][-1:]
    with mvv.record_path("mvv-validation").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "sprint-mvv", "key": judged["key"]}) + "\n")
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(PR, DOCS), ask)
    assert (out["skip"], out["holds"], len(ask.calls)) == (False, ["mvv-validation.jsonl に読めない行が 1 件ある"], 2)


def test_release_gate_does_not_read_the_holds(repo: Path) -> None:
    # 決定 3: リリース承認ゲートは #4105 まで保留を読まず（null）、fast でも省かない
    RepoKey.value = mvv.repo_key(repo)
    _write([_redline(1)])
    out = mvv_gate.gate(repo, "release", _inputs(pr=PR), _hub(PR, DOCS), Ask(FOLLOW))
    assert (out["skip"], out["holds"], out["why"]) == (False, None, [mvv_gate.RELEASE_KEPT])
