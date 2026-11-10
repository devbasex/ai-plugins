"""フレーキーなテストの直し（#2457 決定 9・受け入れ条件 11）。

仕分けでフレーキーと見分けたテスト（今の HEAD で走らせ直すと通った）を、その実行の中で実装担当に 1 回直させる。
同じテストの直しは 1 回の実行で 1 回だけ依頼する。直しはトレーラー `Flaky-Fix: <テストの ID>` を持ち、テストのファイル
だけを変えるコミットで、オーケストレーターが残すコミットとして台帳へ記録する。項目の合否・停滞の判定・項目の実装時間
には数えず、安全網の累計には数える（起動の秒は `coding-time` が足す）。

手順の違反（`Flaky-Fix` の欠け・テスト以外のファイルの変更）は 1 回だけ差し戻し、それでも直らなければ直しのコミットを
`git revert` で打ち消して報告へ残す。項目には触れない。

規則と見分けの部品の正本は #2458 にある。共通の部品ができたら、ここをその部品へ寄せる。
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

import statefile
import test_triage

from .. import gitfacts, info, ledger, pathkinds, timeline, undo
from ..paths import git_out, load_state, work_dir

TRAILER = "Flaky-Fix"


def queue_flaky(state: dict[str, Any], flaky: Optional[list[str]], log: Optional[str]) -> list[str]:
    """フレーキーと見分けたテストのうち、この実行でまだ依頼していないものを直しの依頼へ積む。積んだ ID を返す。

    依頼には ID・ログの置き場・そのテストのファイルだけを走らせ直すコマンドを載せる。"""
    asked = set(state.get("flaky_requested") or [])
    fresh = [t for t in dict.fromkeys(flaky or []) if t and t not in asked]
    if not fresh:
        return []
    state["flaky_requested"] = sorted(asked | set(fresh))
    pending = state.get("flaky_request") or {"tests": [], "sendback": None, "attempts": 0}
    pending["tests"] = list(dict.fromkeys(list(pending.get("tests") or []) + fresh))
    pending["log"] = log or pending.get("log")
    files = sorted(test_triage.by_file(pending["tests"]))
    pending["commands"] = test_triage.rerun_commands(timeline.strategy_of(state), files)
    state["flaky_request"] = pending
    info(f"🔁 フレーキーと見分けたテスト {len(fresh)} 件の直しを実装担当へ依頼します（項目の合否には数えません）")
    return fresh


def _commits(work: str, base: str) -> list[str]:
    out = git_out(work, ["rev-list", "--reverse", f"{base}..HEAD"]) or ""
    return [s for s in out.split() if s]


def _touched_paths(work: str, sha: str) -> list[str]:
    out = git_out(work, ["diff-tree", "--no-commit-id", "--name-only", "-r", sha]) or ""
    return [p for p in out.splitlines() if p]


def fix_violations(work: str, shas: list[str], tests: list[str]) -> list[dict[str, Any]]:
    """直しのコミットの手順の違反（`Flaky-Fix` の欠けか依頼に無い ID・テスト以外のファイルの変更）。"""
    wanted = set(tests)
    found = []
    for sha in shas:
        named = str(gitfacts.commit_trailers(work, sha).get(TRAILER) or "").strip()
        if named not in wanted:
            found.append({"commit": sha, "kind": "trailer", "detail": f"{TRAILER}: <依頼したテストの ID> が無い（{named or '空'}）"})
        others = [p for p in _touched_paths(work, sha) if not pathkinds.is_test_path(p, work)]
        if others:
            found.append({"commit": sha, "kind": "non_test", "detail": "テスト以外のファイルを変えた: " + ", ".join(others[:5])})
    return found


def cmd_merge_flaky_fix(args: argparse.Namespace) -> None:
    """フレーキーの直しの起動の結果を取り込む。出力は `FLAKY_FIX=kept|none|sendback|reverted`。"""
    path, state = load_state(args.id)
    pending = state.get("flaky_request") or {}
    tests = list(pending.get("tests") or [])
    if not tests:
        statefile.emit(FLAKY_FIX="none")
        return
    work = work_dir(state)
    base = str(((state.get("phases") or {}).get("flaky-fix") or {}).get("base_sha") or "")
    shas = _commits(work, base) if base else []
    by = str(state.get("implementer") or "")
    fixes = state.setdefault("flaky_fixes", [])
    if not shas:
        for test_id in tests:
            fixes.append({"test_id": test_id, "commit": None, "by": by, "result": "none"})
        state["flaky_request"] = None
        statefile.save(path, state)
        info(f"ℹ フレーキーなテスト {len(tests)} 件の直しのコミットがありませんでした（報告に残します）")
        statefile.emit(FLAKY_FIX="none")
        return
    for sha in shas:
        ledger.note_orchestrator_commit(state, sha)
    found = fix_violations(work, shas, tests)
    attempts = int(pending.get("attempts") or 0) + 1
    if found and attempts < 2:
        pending.update({"attempts": attempts, "sendback": found})
        state["flaky_request"] = pending
        statefile.save(path, state)
        info(f"↩ フレーキーの直しに手順の違反が {len(found)} 件あります。1 回だけ差し戻します")
        statefile.emit(FLAKY_FIX="sendback")
        return
    if found:
        undo.revert_unattributed(path, state, shas, "フレーキーの直しの手順の違反")
        path, state = load_state(args.id)
        for test_id in tests:
            state.setdefault("flaky_fixes", []).append(
                {"test_id": test_id, "commit": None, "by": by, "result": "reverted", "violations": found}
            )
        state["flaky_request"] = None
        statefile.save(path, state)
        info("↩ 差し戻しの後も手順の違反が残ったため、フレーキーの直しのコミットを打ち消しました（項目には触れません）")
        statefile.emit(FLAKY_FIX="reverted")
        return
    named = {str(gitfacts.commit_trailers(work, sha).get(TRAILER) or "").strip(): sha for sha in shas}
    for test_id in tests:
        fixes.append({"test_id": test_id, "commit": named.get(test_id), "by": by, "result": "kept" if test_id in named else "none"})
    state["flaky_request"] = None
    statefile.save(path, state)
    info(f"✅ フレーキーなテストの直しを {len(named)} 件残しました（項目とは別のコミット）")
    statefile.emit(FLAKY_FIX="kept")
