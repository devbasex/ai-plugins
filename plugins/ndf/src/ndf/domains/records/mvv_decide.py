"""MVV の記録の判断（純粋な関数。N8b・#2603・#2614・#2777・#2970）。

- 記録の `repo` はメインディレクトリのパスにする（worktree で打った判定も同じキーで数える。#2603）
- 承認ゲートの回答から覆しの種類を決める。「判定できない」を人が通したものは覆しに数えず、別の数にする
  （AI が決められないときに人へ戻したのは MVV どおりである。#2777）。同じスプリント状態・承認ゲート・PR の組は 1 件
- スプリント MVV 判定は、MVV 判定の入力の全体（スプリント MVV・プロジェクト MVV・共通原則・指示文）が同じで最新の
  判定が「従う」なら、その判定を再利用する（判定の揺れで開発ループを止めない。#2614）
- 改訂の兆候は、ベースブランチの設定（現行のバージョン）で数える（#2970）
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

# 判定の語は承認ゲートの判定（`ndf.progress.gate.checks`）の綴りを使う
from ndf.progress.gate.checks import FOLLOW, NOT_FOLLOW, UNKNOWN

# 覆しの種類: 「従う」を人が退けた・「反する疑い」を人が通した・「判定できない」を人が通した（覆しに数えない）
OVERRIDE_REJECT = "override_reject"
OVERRIDE_PASS = "override_pass"
OVERRIDE_UNKNOWN_PASS = "unknown_pass"
OVERRIDES = (OVERRIDE_REJECT, OVERRIDE_PASS)
# MVV 判定の記録の種類（スプリント MVV 判定）
MVV_VALIDATION_SPRINT = "sprint-mvv"
# 判定の行が持つ、判定の中身（承認ゲートの判定が読む。再利用の行へも写す）
VERDICT_DETAILS = ("reasons", "boundary", "basis")
# 改訂の設定（mvv.json の settings.revise_after）のキーと、無いときの閾値
REVISE_AFTER = "revise_after"
OVERRIDE_LIMIT_KEY, UNKNOWN_LIMIT_KEY = "overrides", "unknowns"
DEFAULT_LIMITS = {OVERRIDE_LIMIT_KEY: 3, UNKNOWN_LIMIT_KEY: 5}


def override_kind(verdict: Optional[str], approved: bool) -> Optional[str]:
    """MVV 判定と人の回答から覆しの種類を決める。覆しでなければ None。"""
    if verdict == FOLLOW and not approved:
        return OVERRIDE_REJECT
    if verdict == NOT_FOLLOW and approved:
        return OVERRIDE_PASS
    if verdict == UNKNOWN and approved:
        return OVERRIDE_UNKNOWN_PASS
    return None


def signal_kinds(verdict: Optional[str], approved: bool) -> list[str]:
    """承認ゲートの回答 1 件から書く兆候の種類。「判定できない」は人の回答によらず `unknown` を数える。"""
    kind = override_kind(verdict, approved)
    return ([UNKNOWN] if verdict == UNKNOWN else []) + ([kind] if kind is not None else [])


@dataclass(frozen=True)
class Signals:
    """改訂の兆候。`overrides` は覆し、`unknown_pass` は「判定できない」を人が通した数（覆しに数えない）。"""

    mvv_version: Optional[int]
    overrides: int
    unknown_pass: int
    unknown: int
    revision_due: bool


def _pair(row: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("state"), row.get("gate"), row.get("pr")


def _settings(decl: Mapping[str, Any]) -> dict[str, int]:
    settings = decl.get("settings")
    raw = settings.get(REVISE_AFTER) if isinstance(settings, Mapping) else None
    got = dict(DEFAULT_LIMITS)
    if isinstance(raw, Mapping):
        got.update({k: int(raw[k]) for k in DEFAULT_LIMITS if isinstance(raw.get(k), int)})
    return got


def latest(decl: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """設定の現行のバージョンの記録（versions の最後）。無ければ None。"""
    versions = decl.get("versions")
    if isinstance(versions, list) and versions and isinstance(versions[-1], Mapping):
        return versions[-1]
    return None


def current_version(decl: Mapping[str, Any]) -> Optional[int]:
    """設定の現行のバージョン（versions の最後の version）。無ければ None。"""
    last = latest(decl)
    v = last.get("version") if last is not None else None
    return v if isinstance(v, int) else None


class State:
    """MVV の状態の語（承認ゲートの検査 1 の `MvvFacts.status` と同じ綴り）。"""

    APPROVED = "approved"
    MISMATCH = "mismatch"
    UNAPPROVED = "unapproved"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


def status(decl: Optional[Mapping[str, Any]], body_sha256: Optional[str]) -> str:
    """設定と本文の sha256 から MVV の状態を決める（設定が無ければ absent、本文が承認の記録と違えば mismatch）。"""
    if decl is None:
        return State.ABSENT
    last = latest(decl)
    if last is None or not isinstance(last.get("sha256"), str):
        return State.UNAPPROVED
    if body_sha256 is None:
        return State.UNREADABLE
    return State.APPROVED if last["sha256"] == body_sha256 else State.MISMATCH


def count_signals(rows: Iterable[Mapping[str, Any]], decl: Mapping[str, Any], repo: str) -> Signals:
    """設定の現行のバージョンで兆候を数える。覆しは同じ（スプリント状態・承認ゲート・PR）の組を 1 件にまとめる。

    `repo` はメインディレクトリのパスで、記録の `repo` がこれと同じ行だけを数える。
    """
    version = current_version(decl)
    mine = [r for r in rows if r.get("repo") == repo and r.get("mvv_version") == version]
    pairs: dict[str, set[tuple[Any, Any, Any]]] = {k: set() for k in (*OVERRIDES, OVERRIDE_UNKNOWN_PASS, UNKNOWN)}
    for r in mine:
        kind = r.get("kind")
        if kind in pairs:
            pairs[kind].add(_pair(r))
    limits = _settings(decl)
    overrides = len(pairs[OVERRIDE_REJECT]) + len(pairs[OVERRIDE_PASS])
    unknown = len(pairs[UNKNOWN])
    revision_due = overrides >= limits[OVERRIDE_LIMIT_KEY] or unknown >= limits[UNKNOWN_LIMIT_KEY]
    return Signals(version, overrides, len(pairs[OVERRIDE_UNKNOWN_PASS]), unknown, revision_due)


def signals_ref(base_branch: str, remote: str) -> str:
    """改訂の兆候を数える設定を読む参照（ベースブランチ。本番のブランチの古いバージョンを読まない）。"""
    return f"{remote}/{base_branch}"


def mvv_validation_key(inputs: Mapping[str, str]) -> str:
    """MVV 判定の入力の全体を見分ける値（sha256）。どれか 1 字でも変われば別の値になる。"""
    blob = json.dumps(dict(sorted(inputs.items())), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reusable(
    rows: Iterable[Mapping[str, Any]], key: str, excluded: Iterable[tuple[int, Optional[Mapping[str, Any]]]] = ()
) -> Optional[Mapping[str, Any]]:
    """同じ MVV 判定の入力の最新の判定が「従う」なら、その行（再利用の元）。無いか最新が「従う」でなければ None。

    `repo` は見ない（メインディレクトリと worktree のどちらから打っても同じ）。壊れた行（辞書でない・形が違う）は呼び手が除き、
    `excluded`（それより前の読めた行の数と、辞書として読めた中身）で渡す。最新の判定より後に、同じ入力の判定かもしれない除いた行
    （キーが同じか、キーを文字列として読めない）があれば、それが最新の判定かもしれないので None（古い「従う」へさかのぼらない）。
    """
    last_mvv_validation: Optional[Mapping[str, Any]] = None
    seen = 0
    for n, r in enumerate(rows, 1):
        if r.get("kind") == MVV_VALIDATION_SPRINT and r.get("key") == key:
            last_mvv_validation, seen = r, n
    if any(before >= seen and (raw is None or not isinstance(raw.get("key"), str) or raw["key"] == key) for before, raw in excluded):
        return None
    if last_mvv_validation is None or last_mvv_validation.get("verdict") != FOLLOW:
        return None
    return last_mvv_validation


def reuse_row(origin: Mapping[str, Any], at: str, repo: str) -> dict[str, Any]:
    """再利用したときに足す行。元の判定の時刻と、判定の理由・レッドライン（元の行にあれば）を持ち、費用と秒の実測値を持たない。"""
    first = origin["reused_from"] if "reused_from" in origin else origin.get("at")
    held = {k: origin[k] for k in VERDICT_DETAILS if k in origin}
    return {"at": at, "kind": MVV_VALIDATION_SPRINT, "key": origin["key"], "verdict": FOLLOW, "repo": repo, "reused_from": first, **held}


def judged_row(key: str, verdict: str, at: str, repo: str, cost: Mapping[str, Any]) -> dict[str, Any]:
    """LLM が判定したときに足す行（MVV 判定の入力を見分ける値を持ち、次の MVV 判定が再利用できる）。"""
    return {"at": at, "kind": MVV_VALIDATION_SPRINT, "key": key, "verdict": verdict, "repo": repo, **cost}
