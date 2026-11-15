"""フレーキーなテストの直しの判定。純粋な関数だけを置く。

- 検証で落ちた項目の失敗のキーを同じ head でもう 1 度打ち、通ったキーはフレーキーとして項目の失敗に数えない
- フレーキーなキーは、1 回の実行で 1 度だけ修正担当へ直しをリクエストする（同じキーを 2 度リクエストしない）
- 直しのコミットはトレーラー `Flaky-Fix: <キー>` を持ち、テストのファイルだけを変える。違反（トレーラーの欠けかリクエストに無い
  キー・テスト以外のファイルの変更）があれば、その起動のコミットを起点まで取り消して 1 度だけ変更を要求する。変更要求の後も
  違反が残れば取り消して報告へ残す。項目の合否・停滞の判定には数えない
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Optional

TRAILER = "Flaky-Fix"
KEPT = "kept"
NONE = "no_commit"
SENDBACK = "sendback"
REVERTED = "undone"
MAX_ATTEMPTS = 2


def split_flaky(own: Iterable[str], again: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """項目の失敗 `own` を、打ち直しても落ちたキー（`again` に在る）とフレーキーなキーへ分ける。"""
    first = frozenset(own)
    stable = first & frozenset(again)
    return stable, first - stable


def fresh(requested: Iterable[str], flaky: Iterable[str]) -> list[str]:
    """この実行でまだリクエストしていないフレーキーなキー（並びは名前の順）。"""
    asked = set(requested)
    return sorted({k for k in flaky if k and k not in asked})


@dataclass(frozen=True)
class Commit:
    sha: str
    trailer: str
    paths: tuple[str, ...]


RECORD = "\x1e"
UNIT = "\x1f"
# `git log --name-only` の 1 コミットの形（起点の後のコミットを古い順に読む。`parse_log` が分ける）
LOG_FORMAT = f"--format=%x1e%H%x1f%(trailers:key={TRAILER},valueonly,separator=%x2C)%x1f"


def parse_log(text: str) -> list[Commit]:
    """`git log --reverse --name-only` を `LOG_FORMAT` で出した文字列をコミットの並びへ分ける。"""
    out: list[Commit] = []
    for record in text.split(RECORD):
        if UNIT not in record:
            continue
        sha, trailer, rest = record.split(UNIT, 2)
        out.append(Commit(sha.strip(), trailer.strip(), tuple(line.strip() for line in rest.splitlines() if line.strip())))
    return out


def violations(commits: Sequence[Commit], tests: Iterable[str], is_test: Callable[[str], bool]) -> list[dict[str, str]]:
    """直しのコミットの手順の違反。"""
    wanted = set(tests)
    found: list[dict[str, str]] = []
    for c in commits:
        if c.trailer.strip() not in wanted:
            found.append({"commit": c.sha, "kind": "trailer", "detail": f"{TRAILER}: <リクエストしたキー> が無い（{c.trailer.strip() or '空'}）"})
        others = [p for p in c.paths if not is_test(p)]
        if others:
            found.append({"commit": c.sha, "kind": "non_test", "detail": "テスト以外のファイルを変えた: " + ", ".join(others[:5])})
    return found


def outcome(commits: Sequence[Commit], found: Sequence[dict[str, str]], attempts: int) -> str:
    """直しの起動 1 回の後の続け方。`attempts` はこの起動を含む起動の回数。"""
    if not commits:
        return NONE
    if not found:
        return KEPT
    return SENDBACK if attempts < MAX_ATTEMPTS else REVERTED


def fixed(commits: Sequence[Commit]) -> dict[str, str]:
    """キー → 直したコミット。"""
    return {c.trailer.strip(): c.sha for c in commits if c.trailer.strip()}


def results(keys: Iterable[str], commits: Sequence[Commit], kind: str) -> list[dict[str, Optional[str]]]:
    """リクエストしたキーごとの直しの記録（キーの並びのまま）。マージした起動でも、直したコミットの無いキーは `NONE` にする。"""
    by_key = fixed(commits) if kind == KEPT else {}
    return [{"key": k, "result": kind if kind != KEPT or k in by_key else NONE, "commit": by_key.get(k)} for k in keys]
