"""原因の項目の判定（#1649 決定 2〜5）。

全体テストで変更起因の失敗が出たとき、落ちたテストを起こした改善項目（原因の項目）を決める。**危険フラグの有無と
修正担当の申告は使わない**（I1）。候補は取り消されていないすべての改善項目である。

| 手がかり | 決め方 |
| --- | --- |
| `path` | 落ちたテストの本文に、項目のコミットが変えたファイルのパスが現れる（落ちたテスト自身のファイルは外す） |
| `isolate` | 項目のコミットを同じ worktree で `git revert --no-commit` して外し、残った ID のファイルだけを走らせ直すと通る |
| `undetermined` | どちらでも決まらない ID が 1 件でも残った |

外す走らせ直しは HEAD を動かさず、1 件ごとに `git reset --hard HEAD` と `git clean -fd` で戻す（I4）。判定は値
（`Verdict`）を作るだけで項目の状態を書き換えない。**原因の項目を順に取り消す経路は無い**（#2457）。原因の項目は
修正の繰り返しへ回り、成立しないと判定した項目だけが打ち消される（`commands/converge.py`）。
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import time
from typing import Any

import failure_paths
import junit
import test_triage

from . import info, timeline, triage, worktree
from .gitfacts import commit_files
from .items import item_shas, live_items, newest_first
from .paths import work_dir

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


def _isolate(state: dict[str, Any], candidates: list[dict[str, Any]], tests: list[str], evidence: dict) -> list[str]:
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
        if not tests:
            break
        shas = list(reversed(item_shas(item)))
        if not shas:
            continue
        try:
            if not git_ok(work, ["revert", "--no-commit", *shas]):
                continue  # 外せない（衝突）。この項目は飛ばす
            worktree.mark_rewritten()
            limit = timeline.state_test_timeout(state)
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


def determine(state: dict[str, Any], caused: list[str], texts: dict[str, str], isolate: bool = True) -> Verdict:
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
        left = _isolate(state, candidates, left, evidence)
        basis = ISOLATE if len(left) < before else basis
    if left or not evidence:
        basis = UNDETERMINED
    culprits = [i["id"] for i in candidates if i["id"] in evidence]
    rest = [i["id"] for i in candidates if i["id"] not in evidence] if basis == UNDETERMINED else []
    return Verdict(culprits, basis, evidence, culprits + rest, round(time.monotonic() - started, 1))


def judge(state: dict[str, Any], holder: dict[str, Any], verdict_of: dict[str, Any], isolate: bool = True) -> Verdict:
    """見分けの結果（`verdict_of` の `caused`・`caused_output`）から原因を決め、`holder` の `culprit` と `items` に残す。"""
    verdict = determine(state, list(verdict_of.get("caused") or []), dict(verdict_of.get("caused_output") or {}), isolate)
    holder["culprit"] = verdict.as_dict()
    holder["items"] = list(verdict.order)
    named = ", ".join(verdict.culprits) or "なし"
    info(f"🔎 原因の項目: {named}（手がかり {verdict.basis} / {verdict.seconds} 秒）")
    return verdict


def fixable(record: dict[str, Any]) -> bool:
    """修正へ回せるか。原因が決まらなければ回さない（決定 5）。判定の無い旧い記録は回す（I9）。"""
    return (record.get("culprit") or {}).get("basis") != UNDETERMINED


def rerun_of(verdict: dict[str, Any]) -> list[str]:
    """見分けの結果から、変更起因の suite ごとの走らせ直すコマンドを返す。複数形の無い旧い記録は単数を使う。"""
    commands = list(verdict.get("rerun_commands") or [])
    return commands or ([verdict["rerun_command"]] if verdict.get("rerun_command") else [])


def one_command(rerun: Any) -> str:
    """走らせ直すコマンドの並びを、suite の区切りを残した 1 本にする（launch-cli.sh は並びを空白で連結するため）。"""
    commands = [rerun] if isinstance(rerun, str) else [str(c) for c in rerun]
    return commands[0] if len(commands) == 1 else " && ".join(f"( {c} )" for c in commands)


_BASIS_TEXT = {PATH: "落ちたテストの出力に現れたパス", ISOLATE: "項目を外した走らせ直し", UNDETERMINED: "決まらず"}


def verdict_line(record: dict[str, Any]) -> str:
    """報告の 1 行。原因の項目と手がかり（`path` / `isolate` / `undetermined`）。"""
    basis = str(record.get("basis") or "")
    named = ", ".join(record.get("culprits") or []) or "なし"
    return f"{named}（手がかり {basis}: {_BASIS_TEXT.get(basis, basis)} / {record.get('seconds', 0)} 秒）"
