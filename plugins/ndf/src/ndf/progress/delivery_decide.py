"""手で終えるデプロイの工程を、リリース記録と CD の実行で終えてよいかを決める（純粋な処理。ファイルもプロセスも時刻も読まない。#4520）。

出力の `product_version` と `delivery` は書いた側の申告で、リリースが成功した証拠ではないので、PR のリリース記録と CD の
実行の結果を確かめる。記録は 2 つの形がある。release の workflow が書いた記録（JSON を持つ）と、手で置いた
`## リリース記録` のブロックである。読むのは呼び手（`ndf.progress.gate.delivery_check`）で、本文は信用する書き手のものを
古い順に受ける。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple, Optional

from ndf.core import release_record

# リリース記録の種類
WORKFLOW, MANUAL = "workflow", "manual"
# 手で置いた記録の段階の頭の語
PRODUCTION, FAILED_STAGE, NO_DEPLOY = "本番", "反映失敗", "デプロイなし"
# CD の結果を記録できなかったときの語（手で置いた記録の `CD の結果:`）と、事象に残す値
UNCONFIRMED, SUCCESS, UNKNOWN = "未確定", "success", "unknown"

_HEADING = re.compile(r"^## リリース記録[ \t]*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,2} ", re.MULTILINE)
_LINE = re.compile(r"^(?P<key>段階|バージョン|スプリント|CD の結果)[ \t]*[:：][ \t]*(?P<value>.*?)[ \t\r]*$", re.MULTILINE)
_PARENTHESES = re.compile(r"（[^（）]*）|\([^()]*\)")


class Found(NamedTuple):
    """リリース記録 1 件。`at` は渡した本文の並びでの位置、`release` は workflow の記録のリリースの PR（手の記録は None）。"""

    at: int
    kind: str
    stage: str
    version: Optional[str]
    pulls: frozenset[int]
    unit: Optional[str] = None
    release: Optional[int] = None
    cd: Optional[str] = None


class Verdict(NamedTuple):
    """デプロイの工程を終えてよいか。`why` は足りないもの、`cd_result` は読んだ CD の結果（読まなければ None）、
    `no_release_reason` はリリースしないで終える記録の理由。"""

    ok: bool
    why: str = ""
    cd_result: Optional[str] = None
    no_release_reason: Optional[str] = None


class Unreadable(Exception):
    """記録・CD の実行・設定を読めない。`kind` は結果の `cause.kind`。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def unconfirmable(v: Any, held: Mapping[str, Any]) -> Verdict:
    """記録を読む口を持たない呼び手の既定。終了を書かずに止める。"""
    raise Unreadable("precondition", "リリース記録を読めないので、手で終えたデプロイの工程を確かめられない")


def _new_version(value: str) -> Optional[str]:
    """`バージョン:` の行の値から、`→` の後の括弧の根拠を除いた新しいバージョン。読めなければ None。"""
    new = value.rpartition("→")[2]
    while _PARENTHESES.search(new):
        new = _PARENTHESES.sub("", new)
    words = new.split()
    return " ".join(words) if words else None


def _manual(at: int, body: str) -> Optional[Found]:
    """手で置いた `## リリース記録` のブロック（本文の最後のもの）。`段階:` の行が無ければ記録でない。"""
    headings = list(_HEADING.finditer(body))
    if not headings:
        return None
    block = body[headings[-1].end() :]
    end = _NEXT_HEADING.search(block)
    lines = {m["key"]: m["value"] for m in _LINE.finditer(block if end is None else block[: end.start()])}
    if "段階" not in lines:
        return None
    pulls = frozenset(int(n) for n in re.findall(r"#(\d+)", lines["スプリント"])) if "スプリント" in lines else frozenset()
    version = _new_version(lines["バージョン"]) if "バージョン" in lines else None
    return Found(at, MANUAL, lines["段階"], version, pulls, cd=lines.get("CD の結果"))


def _parse(at: int, body: str) -> Optional[Found]:
    """本文 1 件のリリース記録。workflow の記録のマーカーがあれば JSON だけを読む（読めなければ `UnreadableRecord`）。"""
    if release_record.RECORD_MARK not in body:
        return _manual(at, body)
    made = release_record.parse_record(body)
    if made is None:
        return None
    pulls = frozenset({*made.included, made.release})
    return Found(at, WORKFLOW, PRODUCTION, made.version, pulls, made.unit, made.release)


def _records(bodies: Sequence[str]) -> list[Found]:
    return [found for found in (_parse(at, body) for at, body in enumerate(bodies)) if found is not None]


def pick_records(bodies: Sequence[str], *, sprint_pr: int, version: str) -> list[Found]:
    """スプリントの PR とバージョンの記録の、単位ごとの最新（古い順）。単位の名前を持たない記録は 1 組にまとめる。

    バージョンの読めない `反映失敗` の手の記録は、どの単位の失敗か分からないので、選んだ記録のどれかより後なら並びに残す。"""
    mine = [r for r in _records(bodies) if sprint_pr in r.pulls]
    latest = {r.unit: r for r in mine if r.version == version}
    if not latest:
        return []
    first = min(r.at for r in latest.values())
    unnamed = [r for r in mine if r.version is None and r.stage.startswith(FAILED_STAGE) and r.at > first]
    return sorted([*latest.values(), *unnamed], key=lambda r: r.at)


def pick_run(runs: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """公開の結果を表す実行。`push` で起きたもの、無ければ `workflow_dispatch` で起きたものの最新。ほかは数えない。"""
    for event in ("push", "workflow_dispatch"):
        found = [r for r in runs if r.get("event") == event]
        if found:
            return max(found, key=lambda r: str(r.get("created_at")))
    return None


def _cd_result(record: Found, ended: Optional[str]) -> Verdict:
    """記録 1 件と、そのリリースの CD の実行の結論（実行が無い・終わっていなければ None）。"""
    if ended == SUCCESS:
        return Verdict(True, cd_result=SUCCESS)
    if ended is not None:
        return Verdict(False, f"CD の実行が成功していない（{ended}）。直して成功させてから打ち直す")
    if record.cd is not None and record.cd.startswith(UNCONFIRMED):
        return Verdict(True, cd_result=UNKNOWN)
    return Verdict(
        False,
        "CD の実行の結果が分からない（実行が無いか、完了していない）。完了を待って打ち直すか、"
        f"`CD の結果: {UNCONFIRMED}` を持つ手のリリース記録を置く",
    )


def judge_delivery(found: Sequence[Found], runs: Optional[Mapping[Optional[int], Optional[str]]]) -> Verdict:
    """選んだ記録がすべて本番の記録で、CD を読む場合はすべて成功か未確定の記録か。

    `runs` はリリースの PR（手の記録は None）→ 選んだ実行の結論。CD を読まないなら None。"""
    if not found:
        return Verdict(False, "スプリントの PR とバージョンのリリース記録が無い。リリース記録を置いてから打ち直す")
    refused = next((r for r in found if not r.stage.startswith(PRODUCTION)), None)
    if refused is not None:
        return Verdict(False, f"最新のリリース記録が本番でない（段階: {refused.stage}）。本番の記録を置いてから打ち直す")
    if runs is None:
        return Verdict(True)
    read = [_cd_result(r, runs.get(r.release)) for r in found]
    bad = next((v for v in read if not v.ok), None)
    if bad is not None:
        return bad
    return Verdict(True, cd_result=UNKNOWN if any(v.cd_result == UNKNOWN for v in read) else SUCCESS)


def judge_no_release(bodies: Sequence[str], *, sprint_pr: int) -> Verdict:
    """読む PR の最新のリリース記録が、スプリントの PR を含む `デプロイなし` か。"""
    found = _records(bodies)
    last = found[-1] if found else None
    if last is None or last.kind != MANUAL or not last.stage.startswith(NO_DEPLOY) or sprint_pr not in last.pulls:
        return Verdict(False, f"最新のリリース記録が `段階: {NO_DEPLOY}`（`スプリント:` に #{sprint_pr}）でない")
    reason = last.stage[len(NO_DEPLOY) :].strip()
    return Verdict(True, no_release_reason=reason.removeprefix("（").removesuffix("）"))


def executed(comments: Sequence[tuple[str, Optional[datetime]]], *, sprint_pr: int, since: Optional[datetime]) -> bool:
    """`since`（直近の開き直し）より後に書かれた、workflow の記録か本番・反映失敗の手の記録があるか。

    書いた時刻の分からない記録は、後に書かれたものと数える。"""
    later = [body for body, at in comments if since is None or at is None or at > since]
    return any(sprint_pr in r.pulls and r.stage.startswith((PRODUCTION, FAILED_STAGE)) for r in _records(later))
