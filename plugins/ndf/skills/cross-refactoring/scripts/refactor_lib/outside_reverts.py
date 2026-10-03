"""プランの外の取り消しの読み取り（#1692 の決定 4・I7）。

conductor がスクリプトの後に `git revert` で項目のコミットを取り消し、push した後に使う。origin の head ブランチを
取り込み、`plan.base_sha..FETCH_HEAD` のコミットの本文の `This reverts commit <SHA>` の行を古い順に読んで、
**取り消されたままのコミット**を決める。取り消しのコミットが取り消されていれば、元のコミットは取り消されていない。

git を書き換えない。状態ファイルも書かない（項目を取り消しにするのは `ledger.mark_outside_revert`）。
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

from .publish import credential_fallback_args, gh_available

_REVERTS = re.compile(r"^This reverts commit ([0-9a-f]{7,40})", re.MULTILINE)
_RECORD = "\x1e"


def still_reverted(entries: list[tuple[str, str]]) -> dict[str, str]:
    """古い順の `(SHA, 本文)` から、取り消されたままのコミット → それを取り消しているコミット を返す。

    取り消しのコミット R が取り消されていれば R は効いていない。取り消しの取り消しの取り消しも同じ規則で決まる。
    """
    reverters: dict[str, list[str]] = {}
    for sha, body in entries:
        for target in _REVERTS.findall(body):
            reverters.setdefault(_expand(target, entries), []).append(sha)
    memo: dict[str, Optional[str]] = {}

    def effective_reverter(sha: str) -> Optional[str]:
        if sha not in memo:
            memo[sha] = None  # 循環はしないが、念のため再帰を止める
            memo[sha] = next((r for r in reverters.get(sha, []) if effective_reverter(r) is None), None)
        return memo[sha]

    return {sha: r for sha in reverters if (r := effective_reverter(sha)) is not None}


def _expand(target: str, entries: list[tuple[str, str]]) -> str:
    """短い SHA を、読んだ範囲のコミットの完全な SHA へ広げる。範囲に無ければそのまま返す。"""
    return next((sha for sha, _ in entries if sha.startswith(target)), target)


def reverter_of(reverted: dict[str, str], sha: str) -> Optional[str]:
    """`sha` を取り消しているコミット。短い SHA どうしでも前方一致で引く。"""
    if not sha:
        return None
    return next((r for target, r in reverted.items() if target.startswith(sha) or sha.startswith(target)), None)


def _git(work: str, *args: str) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    if p.returncode != 0 and gh_available():
        # push と同じく、認証で落ちたときは helper を退避して 1 度だけやり直す
        p = subprocess.run(["git", *credential_fallback_args(), *args], cwd=work, capture_output=True, text=True)
    return p


def scan(work: str, base_sha: str, head_branch: str) -> Optional[tuple[str, dict[str, str]]]:
    """origin の head ブランチを取り込み、`(FETCH_HEAD の SHA, 取り消されたままのコミット)` を返す。取り込めなければ `None`。"""
    if _git(work, "fetch", "origin", head_branch).returncode != 0:
        return None
    tip = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], cwd=work, capture_output=True, text=True)
    log = subprocess.run(
        ["git", "log", "--reverse", f"--format=%H%x00%B{_RECORD}", f"{base_sha}..FETCH_HEAD"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    if tip.returncode != 0 or log.returncode != 0:
        return None
    entries = []
    for record in log.stdout.split(_RECORD):
        sha, sep, body = record.strip("\n").partition("\x00")
        if sep:
            entries.append((sha.strip(), body))
    return tip.stdout.strip(), still_reverted(entries)
