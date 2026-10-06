"""原因の項目の判定と、その順の取り消し（#1649 決定 2〜5）。

全体テストで変更起因の失敗が出たとき、落ちたテストを起こした改善項目（原因の項目）を決める。**危険フラグの有無と
修正担当の申告は使わない**（I1）。候補は取り消されていないすべての改善項目である。

| 手がかり | 決め方 |
| --- | --- |
| `path` | 落ちたテストの本文に、項目のコミットが変えたファイルのパスが現れる（落ちたテスト自身のファイルは外す） |
| `isolate` | 項目のコミットを同じ worktree で `git revert --no-commit` して外し、残った ID のファイルだけを走らせ直すと通る |
| `undetermined` | どちらでも決まらない ID が 1 件でも残った |

外す走らせ直しは HEAD を動かさず、1 件ごとに `git reset --hard HEAD` と `git clean -fd` で戻す（I4）。締め切りを過ぎたら
次の候補へ進まない。判定は値（`Verdict`）を作るだけで項目の状態を書き換えない。取り消しは `revert_in_order` が `undo.drop`
で行う。検証の全体テスト（`commands/converge.py`・`wholetest.py`）・最終ゲートへ寄せた危険フラグ（`gate_ci.py`）・
打ち切りの後の取り消し（`stop_revert.py`）が同じものを呼ぶ。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import pathlib
import subprocess
import time
from typing import Any, Callable, Optional

import failure_paths
import junit
import statefile
import test_triage

from . import budget, clock, info, ledger, timeline, triage, worktree
from .gitfacts import commit_files, run_with_timeout
from .items import find_item, item_shas, live_items, newest_first
from .paths import work_dir
from .undo import ON_CONFLICT_STOP, DropConflict, drop

PATH = "path"
ISOLATE = "isolate"
UNDETERMINED = "undetermined"


@dataclasses.dataclass
class Verdict:
    """原因の判定。`culprits` と `order` は項目の ID で、新しい順。"""

    culprits: list[str]
    basis: str
    evidence: dict[str, dict[str, list[str]]]
    order: list[str]
    seconds: float

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _changed_by(work: str, item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for sha in item_shas(item):
        out.extend(f for f in commit_files(work, sha) if f not in out)
    return out


def _past(deadline: Optional[_dt.datetime]) -> bool:
    return deadline is not None and clock.now() >= deadline


def _by_paths(
    candidates: list[dict[str, Any]], files: dict[str, list[str]], caused: list[str], texts: dict[str, str], evidence: dict
) -> list[str]:
    """手がかり 1。本文に項目の変えたファイルが現れた ID を `evidence` へ書き、決まらない ID を返す。"""
    left: list[str] = []
    for test in caused:
        hit = False
        for item in candidates:
            found = failure_paths.mentioned(texts.get(test, ""), files[item["id"]], exclude=[junit.file_of(test)])
            if found:
                hit = True
                paths = evidence.setdefault(item["id"], {}).setdefault("paths", [])
                paths.extend(p for p in found if p not in paths)
        if not hit:
            left.append(test)
    return left


def git_ok(work: str, args: list[str]) -> bool:
    return subprocess.run(["git", *args], cwd=work, capture_output=True, text=True).returncode == 0


def _isolate(
    state: dict[str, Any], candidates: list[dict[str, Any]], tests: list[str], deadline: Optional[_dt.datetime], evidence: dict
) -> list[str]:
    """手がかり 2。候補を新しい順に 1 件ずつ外して残った ID を走らせ直し、通るようになった ID を `evidence` へ書く。"""
    work, strategy = work_dir(state), timeline.strategy_of(state)
    if not test_triage.rerun_groups(strategy, list(test_triage.by_file(tests))):
        return tests  # 走らせ直すコマンドが無い
    dirty = worktree._dirty_paths(state, work)
    if dirty:
        # 外した後の `reset --hard`・`clean -fd` は未コミットの変更を戻せずに消す（C4）。消さずに外すのをやめる
        info(f"⚠ 未コミットの変更があるため、項目を外した走らせ直しをしません（{', '.join(dirty[:5])}）")
        return tests
    log_dir = pathlib.Path(state["tmp_dir"])
    for item in candidates:
        if not tests or _past(deadline):
            break
        shas = list(reversed(item_shas(item)))
        if not shas:
            continue
        try:
            if not git_ok(work, ["revert", "--no-commit", *shas]):
                continue  # 外せない（衝突）。この項目は飛ばす
            worktree.mark_rewritten()
            limit = timeline.state_test_timeout(state)
            if deadline is not None:
                limit = max(1, min(limit, int((deadline - clock.now()).total_seconds())))
            still, _, _ = test_triage.failing_in(work, strategy, tests, limit, log_dir, f"isolate-{item['id']}", triage.run_test)
        finally:
            # 外した後の内容で走らせ直したキャッシュを、項目の内容へ戻した後に読ませない（#1806 決定 6）
            worktree.wait_past_rewrite()
            git_ok(work, ["revert", "--quit"])
            worktree._discard_worktree_changes(work)
            worktree.mark_rewritten()
        passed = [t for t in tests if t not in still]
        if passed:
            evidence.setdefault(item["id"], {})["tests"] = passed
            tests = [t for t in tests if t not in passed]
    return tests


def determine(
    state: dict[str, Any], caused: list[str], texts: dict[str, str], deadline: Optional[_dt.datetime], isolate: bool = True
) -> Verdict:
    """変更起因の ID（`caused`）と本文（`texts`）から原因の項目を決める。`isolate=False` なら手がかり 1 だけを試す。"""
    started = time.monotonic()
    work = work_dir(state)
    candidates = newest_first(live_items(state))
    files = {i["id"]: _changed_by(work, i) for i in candidates}
    evidence: dict[str, dict[str, list[str]]] = {}
    left = _by_paths(candidates, files, list(caused), texts or {}, evidence)
    basis = PATH
    if left and isolate:
        before = len(left)
        left = _isolate(state, candidates, left, deadline, evidence)
        basis = ISOLATE if len(left) < before else basis
    if left or not evidence:
        basis = UNDETERMINED
    culprits = [i["id"] for i in candidates if i["id"] in evidence]
    rest = [i["id"] for i in candidates if i["id"] not in evidence] if basis == UNDETERMINED else []
    return Verdict(culprits, basis, evidence, culprits + rest, round(time.monotonic() - started, 1))


def fix_deadline(state: dict[str, Any]) -> _dt.datetime:
    """検証の修正の締め切り（`budget.fix_end`）。原因を決める走らせ直しはこの内に数える（#1649 前提 6）。"""
    reserve = (state.get("plan") or {}).get("reserve") or {}
    return budget.fix_end(clock.parse(state["started_at"]), int(state["budget_minutes"]), reserve)


def judge(
    state: dict[str, Any], holder: dict[str, Any], verdict_of: dict[str, Any], deadline: Optional[_dt.datetime], isolate: bool = True
) -> Verdict:
    """見分けの結果（`verdict_of` の `caused`・`caused_output`）から原因を決め、`holder` の `culprit` と `items` に残す。"""
    verdict = determine(state, list(verdict_of.get("caused") or []), dict(verdict_of.get("caused_output") or {}), deadline, isolate)
    holder["culprit"] = verdict.as_dict()
    holder["items"] = list(verdict.order)
    named = ", ".join(verdict.culprits) or "なし"
    info(f"🔎 原因の項目: {named}（手がかり {verdict.basis} / {verdict.seconds} 秒）")
    return verdict


def fixable(record: dict[str, Any]) -> bool:
    """修正へ回せるか。原因が決まらなければ回さない（決定 5）。判定の無い旧い記録は回す（I9）。"""
    return (record.get("culprit") or {}).get("basis") != UNDETERMINED


CUT_CONFLICT = "conflict"  # 積み直しの衝突で止まった
CUT_DEADLINE = "deadline"  # 締め切りを過ぎて止まった


@dataclasses.dataclass
class Narrowed:
    """`revert_in_order` の結果。`cut` は止まった理由（`CUT_CONFLICT` / `CUT_DEADLINE`）で、尽きた・通ったときは `None`。"""

    reverted: list[str]
    passed: bool
    cut: Optional[str] = None


def rerun_of(verdict: dict[str, Any]) -> list[str]:
    """見分けの結果から、変更起因の suite ごとの走らせ直すコマンドを返す。複数形の無い旧い記録は単数を使う。"""
    commands = list(verdict.get("rerun_commands") or [])
    return commands or ([verdict["rerun_command"]] if verdict.get("rerun_command") else [])


def one_command(rerun: Any) -> str:
    """走らせ直すコマンドの並びを、suite の区切りを残した 1 本にする（launch-cli.sh は並びを空白で連結するため）。"""
    commands = [rerun] if isinstance(rerun, str) else [str(c) for c in rerun]
    return commands[0] if len(commands) == 1 else " && ".join(f"( {c} )" for c in commands)


def rerun_passes(state: dict[str, Any], rerun: Any, deadline: Optional[_dt.datetime] = None) -> Callable[[], bool]:
    """落ちたテストを走らせ直し、時間内に通ったかを返す関数を作る（revert_in_order へ渡す）。

    `rerun` はシェルで走らせる 1 本か、その並び（変更起因の suite ごと）で、すべて通ったときだけ真。各回の上限は
    `deadline` までの残りで切り詰め、過ぎていれば走らせずに偽を返す。
    """
    work, limit = work_dir(state), timeline.state_test_timeout(state)
    commands = [rerun] if isinstance(rerun, str) else list(rerun)

    def passes() -> bool:
        for command in commands:
            left = limit
            if deadline is not None:
                left = min(limit, int((deadline - clock.now()).total_seconds()))
                if left < 1:
                    return False
            code, timed_out = run_with_timeout(command, work, left)
            if timed_out or code != 0:
                return False
        return True

    return passes


def revert_in_order(
    path: pathlib.Path,
    state: dict[str, Any],
    order: list[str],
    reason: str,
    passes: Callable[[], bool],
    *,
    on_conflict: str = ON_CONFLICT_STOP,
    deadline: Optional[_dt.datetime] = None,
) -> Narrowed:
    """`order` の順に、続けて残りの項目を新しい順に 1 件ずつ取り消し、`passes()` が真になった時点で止める（I2）。

    通った時点で、修正へ回していた（`failing`）残りの項目は `verified` へ戻す。`on_conflict="raise"` なら積み直しの
    衝突で止めて `cut="conflict"` を返す。`deadline` を過ぎたら次の取り消しを始めず `cut="deadline"` を返す。
    最初の取り消しの前に未コミットの変更があれば、`drop` の `reset --hard` が消すため捨てずに終了コード 4 で止まる。
    """
    queue = list(order) + [i["id"] for i in newest_first(live_items(state)) if i["id"] not in order]
    reverted: list[str] = []
    for item_id in queue:
        item = find_item(state, item_id, required=False)
        if not ledger.is_live(item):
            continue
        if _past(deadline):
            return Narrowed(reverted, False, CUT_DEADLINE)
        if not reverted:
            worktree.stop_if_dirty(state, work_dir(state), "項目を取り消す")
        item["failure_reason"] = reason
        try:
            drop(path, state, [item_id], reason, on_conflict=on_conflict)
        except DropConflict as exc:
            item.pop("failure_reason", None)
            info(f"⚠ {exc}")
            return Narrowed(reverted, False, CUT_CONFLICT)
        reverted.append(item_id)
        if passes():
            for rest in live_items(state):
                if rest.get("status") == "failing" and rest["id"] in order:
                    rest["status"] = "verified"
            statefile.save(path, state)
            return Narrowed(reverted, True)
    return Narrowed(reverted, False)


def narrow(path: pathlib.Path, state: dict[str, Any], record: dict[str, Any], stop_reason: str, passes: Callable[[], bool]) -> bool:
    """検証の全体テストで、修正の締め切りを過ぎたか原因が決まらないとき、`record["items"]` の順に取り消す。通ったら真。"""
    reason = f"全体テストで落ちたテストが{stop_reason}" if fixable(record) else "全体テストで落ちたテストの原因の項目を決められなかった"
    passed = revert_in_order(path, state, list(record.get("items") or []), reason, passes).passed
    record.update(reverted=True, resolution="narrowed")
    return passed


_BASIS_TEXT = {PATH: "落ちたテストの出力に現れたパス", ISOLATE: "項目を外した走らせ直し", UNDETERMINED: "決まらず"}


def verdict_line(record: dict[str, Any]) -> str:
    """報告の 1 行。原因の項目と手がかり（`path` / `isolate` / `undetermined`）。"""
    basis = str(record.get("basis") or "")
    named = ", ".join(record.get("culprits") or []) or "なし"
    return f"{named}（手がかり {basis}: {_BASIS_TEXT.get(basis, basis)} / {record.get('seconds', 0)} 秒）"
