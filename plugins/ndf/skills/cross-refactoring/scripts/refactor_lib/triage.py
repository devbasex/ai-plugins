"""危険の印で走らせた全体のテストの失敗を見分ける（#933 決定 22 / 実装計画 I14）。

落ちたテストの ID を出力から取り出し、**それだけを**走らせ直して 3 つに分ける。
全体のテストは走らせ直さない（検証の中の全体のテストは 1 度だけ。決定 14）。

| 分類 | 見分け方 | 扱い |
| --- | --- | --- |
| 揺れ（`flaky`） | 今の HEAD で走らせ直すと通る | 取り消さない |
| 元からの失敗（`preexisting`） | 着手前の HEAD でも落ちる | 取り消さない |
| 変更が原因（`caused`） | 今の HEAD で再び落ち、着手前の HEAD では通る | 直しを試みる |

**迷ったら「変更が原因」の側へ倒す。** 走らせ直しの出力から ID を読めなければ、落ちた
テストはすべて今も落ちているとみなし、着手前の HEAD の出力から読めなければ、元からの
失敗とはみなさない。取り消さずに残す側へ倒すと、壊した変更が Pull Request に残る。

着手前の HEAD は、作業ディレクトリを checkout で動かさず、一時の detach の作業ツリーで
走らせて消す。作業ディレクトリを動かすと、途中で落ちたときに HEAD が戻らない。
"""
from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Any, Optional

from . import info
from .gitfacts import run_with_timeout, safe_int
from .paths import git_out
from .testcmd import failed_nodes, rerun_command
from .vocabulary import DEFAULT_TEST_TIMEOUT


def _read(log: pathlib.Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _reported(nodes: list[str], reported: list[str]) -> list[str]:
    """`nodes` のうち、出力が落ちたと報じたもの。収集の失敗はファイルの単位で出る。"""
    return [n for n in nodes
            if n in reported or any(n.startswith(r + "::") for r in reported)]


def failing_now(words: list[str], cwd: str, nodes: list[str], timeout: int,
                log: pathlib.Path) -> list[str]:
    """落ちたテストだけを走らせ直し、まだ落ちているものを返す。読めなければ全部。"""
    code, timed_out = run_with_timeout(list(words), cwd, timeout, output=log)
    if not timed_out and code == 0:
        return []
    hit = [] if timed_out else _reported(nodes, failed_nodes(_read(log)))
    return hit or list(nodes)


def failing_at(work: str, sha: str, words: list[str], nodes: list[str], timeout: int,
               log: pathlib.Path) -> list[str]:
    """着手前の HEAD（`sha`）でも落ちるものを返す。読めなければ空（元からの失敗とみなさない）。"""
    holder = pathlib.Path(tempfile.mkdtemp(prefix="rf-baseline-"))
    tree = holder / "tree"
    try:
        if git_out(work, ["worktree", "add", "--detach", "-q", str(tree), sha]) is None:
            info(f"⚠ 着手前の HEAD（{sha[:7]}）の作業ツリーを作れませんでした。元からの失敗とはみなしません")
            return []
        code, timed_out = run_with_timeout(list(words), str(tree), timeout, output=log)
        if timed_out or code == 0:
            return []
        return _reported(nodes, failed_nodes(_read(log)))
    finally:
        git_out(work, ["worktree", "remove", "--force", str(tree)])
        shutil.rmtree(holder, ignore_errors=True)
        git_out(work, ["worktree", "prune"])


def baseline_head(state: dict[str, Any]) -> Optional[str]:
    """着手前の HEAD。`init` が残した SHA、無ければ（旧い状態ファイル）計画の起点。"""
    return ((state.get("baseline_test") or {}).get("head")
            or (state.get("plan") or {}).get("base_sha") or None)


def classify(state: dict[str, Any], whole_log: pathlib.Path, timed_out: bool) -> dict[str, Any]:
    """全体のテストの失敗を分ける。取り出せなければ `unparsed_reason` だけを返す。"""
    command = str((state.get("baseline_test") or {}).get("command") or "")
    work = str(state["worktrees"]["work"])
    timeout = safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    tmp = pathlib.Path(state["tmp_dir"])
    if timed_out:
        return {"unparsed_reason": "全体のテストが打ち切られ、落ちたテストを取り出せなかった"}
    if rerun_command(command, [], work) is None:
        return {"unparsed_reason": "全体のテストの実行器が pytest でなく、落ちたテストを取り出せなかった"}
    nodes = failed_nodes(_read(whole_log))
    if not nodes:
        return {"unparsed_reason": "全体のテストの出力に落ちたテストの行（FAILED / ERROR）が無かった"}
    words = rerun_command(command, nodes, work) or []
    rerun_log = tmp / "verify-whole-rerun.log"
    still = failing_now(words, work, nodes, timeout, rerun_log)
    base = baseline_head(state)
    before = (failing_at(work, base, rerun_command(command, still, work) or [], still, timeout,
                         tmp / "verify-whole-baseline.log")
              if still and base else [])
    caused = [n for n in still if n not in before]
    return {
        "failed_tests": nodes,
        "flaky": [n for n in nodes if n not in still],
        "preexisting": before,
        "caused": caused,
        "baseline_head": base,
        "rerun_command": rerun_command(command, caused, work) if caused else None,
        "rerun_log": str(rerun_log),
    }
