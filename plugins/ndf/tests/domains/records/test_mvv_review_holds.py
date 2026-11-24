"""レビューの保留（`review_holds`）と、承認ゲートの直前の MVV 判定がそれを読むこと（#3959 の受け入れ条件 4。決定 2・11）。

保留の有無ごとの判定は Git のアダプターを偽にして確かめ、git と GitHub のスタブを通す承認ゲートの全体は 1 件だけ打つ。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import folded
import pytest

from ndf.domains.records import jsonl, mvv, mvv_gate
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import gate

from . import test_mvv_gate
from .test_mvv_gate import DOCS, FOLLOW, HEAD, SHA, Ask, _hub, _inputs

# 判定の場所と PR のコメントのフィクスチャを test_mvv_gate から読み込む（引数の名前と同じ名前で import すると F811 になる）
repo, notes = test_mvv_gate.repo, test_mvv_gate.notes

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
def _review_holds_keep_only_unfixed_redlines_and_deferrals_that_need_the_user(
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


@pytest.fixture
def fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git のアダプターを偽にした判定の場所。メインディレクトリの一覧だけを返し、git のプロセスを起動しない。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    main = tmp_path / "main"
    monkeypatch.setattr(mvv.git_mod.Git, "worktrees", lambda self: [SimpleNamespace(path=main)])
    monkeypatch.setattr(mvv.git_mod.Git, "run", _no_git)
    return main


def _no_git(self: Any, *args: Any, **kw: Any) -> str:
    raise AssertionError(f"git を起動しない: {args}")


@pytest.mark.parametrize(("prepare", "skip", "held", "unwritten"), GATES.values(), ids=GATES.keys())
def _fast_design_gate_is_not_skipped_while_the_review_holds_remain(
    fake_git: Path, prepare: Callable[[], None], skip: bool, held: int, unwritten: bool
) -> None:
    # 受け入れ条件 4: 直っていないレッドライン・利用者の判断が要る見送り・読めない記録は、fast でも「従う」の判定で省かない。
    # 承認ゲートの直前の判定と同じ手順 (5)・(7)・(8)（記録を読む・判定を記録する・省くかを決める）を、git を起動せずに通す
    RepoKey.value = mvv.repo_key(fake_git)
    prepare()
    pull = SimpleNamespace(head_sha=HEAD, base_ref="main")
    j = mvv_gate._Judging(fake_git, "design", None, PR, pull)  # type: ignore[arg-type]
    rec = mvv_gate._read_records(j, mvv.Snapshot(md.State.APPROVED, 1, SHA, None, (), {}, HEAD, "c" * 64, None), [])
    ask = Ask(FOLLOW)
    asked = {"gate": "design", "question": "q", "material": "m"}
    _, unrecorded = mvv.mvv_validate_sprint(
        fake_git, asked, mvv_gate.judge_with(ask), reuse=rec.rows is not None, rows=rec.rows, broken=rec.broken
    )
    out = mm.gate_outcome("design", FOLLOW, unrecorded is None, [], rec.holds, pull=(PR, HEAD))
    assert (out["skip"], len(out["holds"]), len(ask.calls)) == (skip, held, 1)
    assert (unrecorded is not None) is unwritten
    if held:
        [hold] = out["holds"]
        assert hold.startswith("PR #5 の ") or hold.startswith(mvv_gate.RECORDS_UNREADABLE)


class GateGit:
    """承認ゲートの経路が使う Git のアダプターの偽物。リモート名と設計のブランチ名を返し、ベースの取得と進め方の設定の読み取りは
    何もしない（git のプロセスを起動しない）。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def remote(self) -> str:
        return "origin"

    def run(self, *args: str) -> str:
        assert args == ("rev-parse", "--abbrev-ref", "HEAD")
        return "design/x\n"

    def fetch_branch_or_raise(self, remote: str, branch: str) -> None:
        return None

    def blobs(self, *wheres: str) -> tuple[None, ...]:
        return tuple(None for _ in wheres)


def _the_gate_puts_the_holds_on_the_pr_and_the_approval_material(
    fake_git: Path, notes: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 保留の文は holds にだけ置き、why には件数の固定の文だけ。PR のコメントと承認資料にも保留が載る。
    # 承認ゲートの経路は Git のアダプターとベースの MVV の読み取りを偽にして通す
    body = fake_git.parent / "mvv.md"
    body.write_text(test_mvv_gate.BODY, encoding="utf-8")
    snap = mvv.Snapshot(md.State.APPROVED, 1, SHA, body, (), {}, "b" * 40, "c" * 64, None)
    monkeypatch.setattr(mvv_gate, "Git", GateGit)
    monkeypatch.setattr(mvv, "current", lambda root, ref, allow_mismatch=False, outside=None: snap)
    RepoKey.value = mvv.repo_key(fake_git)
    _write([_deferred(1)])
    out = mvv_gate.gate(fake_git, "design", _inputs(), _hub(PR, DOCS), Ask(FOLLOW))
    [hold] = out["holds"]
    assert (out["skip"], hold) == (False, "PR #5 の Value 1（deferred・needs_user）: 理由 1")
    assert "レビューの保留が 1 件ある（内容は保留の欄）" in out["why"] and not any(hold in w for w in out["why"])
    assert f"### レビューの保留\n\n- {hold}" in notes[-1][1]
    assert f"  - {hold}" in gate.mvv_lines(out, "design", PR, HEAD)


def _records_are_read_once_and_a_broken_row_stops_the_reuse(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def _release_gate_does_not_read_the_holds(repo: Path) -> None:
    # 決定 3: リリース承認ゲートは #4105 まで保留を読まず（null）、fast でも省かない
    RepoKey.value = mvv.repo_key(repo)
    _write([_redline(1)])
    out = mvv_gate.gate(repo, "release", _inputs(pr=PR), _hub(PR, DOCS), Ask(FOLLOW))
    assert (out["skip"], out["holds"], out["why"]) == (False, None, [mvv_gate.RELEASE_KEPT])


# #3959 で足した場合を 1 つのテストの関数へ束ねる（テストの関数の数の予算。束ね方は tests/support/folded.py）
@pytest.mark.parametrize(
    "case",
    folded.cases(
        _review_holds_keep_only_unfixed_redlines_and_deferrals_that_need_the_user,
        _fast_design_gate_is_not_skipped_while_the_review_holds_remain,
        _the_gate_puts_the_holds_on_the_pr_and_the_approval_material,
        _records_are_read_once_and_a_broken_row_stops_the_reuse,
        _release_gate_does_not_read_the_holds,
    ),
)
def test_mvv_review_holds_cases(case: Any, request: pytest.FixtureRequest) -> None:
    folded.run(case, request)
