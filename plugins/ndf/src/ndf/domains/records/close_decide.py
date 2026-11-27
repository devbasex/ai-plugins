"""工程の終わりの判断（純粋な関数。N8a・#2293・#1893・#2067）。

- スプリントを閉じる条件 2: リリース後テストの記録があれば、フラグを問わず見る。不合格の行を持つ課題は閉じない
  （フラグの付け忘れで不合格の課題を閉じない）。不合格が無く保留（not_run）の行を持つ課題は、閉じるかを人が決める（#3855）
- 閉じる条件の要否（#4580）: 流れの設定で外した条件と理由（excluded・reason）と、今回の呼び出しで記録を求めたか（enforced）を
  分けて返す。外しても、渡した記録の不合格では閉じない
- クリーンアップの工程の記録: 課題の本文の進行のセクションで、工程「クリーンアップ」の行が未チェックならチェックを付ける。記録済みなら
  打ち直さない
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple, Optional

from ndf.domains.release import record_decide
from ndf.progress import flow
from ndf.progress.flow import CloseRule
from ndf.progress.records import ISSUE_SECTION, SECTION_END

# クリーンアップの工程の名前（進行のセクションの行）
CLEANUP_STAGE = "クリーンアップ"
# 課題を閉じてよいかの判定（閉じてよい / 閉じない / 人が決める）
CLOSE = "close"
KEEP = "keep"
ASK = "ask"


def _rows_with(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], outcome: str) -> list[Mapping[str, Any]]:
    if verification is None:
        return []
    return [r for r in verification if r.get("number") == issue and r.get("result") == outcome]


def blocking_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """課題を閉じない、リリース後テストの行（その課題の failed。記録が無ければ空）。"""
    return _rows_with(issue, verification, "failed")


def waiting_rows(issue: int, verification: Optional[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """閉じるかを人が決める、リリース後テストの行（その課題の not_run。記録が無ければ空）。"""
    return _rows_with(issue, verification, "not_run")


class Verdict(NamedTuple):
    """課題を閉じてよいか（`CLOSE` / `KEEP` / `ASK`）・理由・テストで確認できたか・反映の判定（`--records` のときだけ）。"""

    decided: str
    reason: str
    verified: bool
    release: Optional[dict[str, Any]] = None


def _all_passed(issue: int, rows: list[Mapping[str, Any]]) -> bool:
    """記録にその課題の行があり、すべて合格か。"""
    mine = [r for r in rows if r.get("number") == issue]
    return bool(mine) and all(r.get("result") == "passed" for r in mine)


def verdict(issue: int, verification: Optional[Iterable[Mapping[str, Any]]], enforced: bool) -> Verdict:
    """課題を閉じてよいかと理由。`enforced` は記録を求める（記録が無ければ閉じない）。

    記録があれば条件 2 を必ず見る。failed があれば not_run の有無に依らず閉じない（悪い方を優先する）。
    """
    if verification is None:
        if enforced:
            return Verdict(KEEP, "リリース後テストの記録が無い", False)
        return Verdict(CLOSE, "リリース後テストの記録が無い（条件 2 を見ない）", False)
    rows = list(verification)
    verified = _all_passed(issue, rows)
    bad = blocking_rows(issue, rows)
    if bad:
        return Verdict(KEEP, f"リリース後テストに不合格の行が {len(bad)} 件ある", verified)
    waiting = waiting_rows(issue, rows)
    if waiting:
        return Verdict(ASK, f"保留（not_run）の行が {len(waiting)} 件ある。閉じるかを人が決める", verified)
    return Verdict(CLOSE, "リリース後テストに不合格と保留の行が無い", verified)


def _last_check(view: Mapping[str, Any], rep: Mapping[str, Any]) -> Optional[list[Mapping[str, Any]]]:
    """代表と `version`・`unit` が同じテストの記録のうち、最後の 1 件の行（無ければ None）。"""
    same = [v for v in view["verifications"] if (v.get("version"), v.get("unit")) == (rep.get("version"), rep.get("unit"))]
    return list(same[-1]["issues"]) if same else None


class Reflection(NamedTuple):
    """反映の判定（結果 JSON の `outputs.release`）。`missing` はまだの届け先、`unconfirmed` は CD の結果でなく確かめた届け先と区分。"""

    complete: bool
    missing: list[str]
    unconfirmed: list[dict[str, str]]


def _release_view(rep: record_decide.Record, releases: list[record_decide.Record], targets: tuple[str, ...]) -> Reflection:
    """反映の判定。数えるのは `record_decide.counted` の記録だけ。"""
    complete, missing = record_decide.complete_of(releases, rep, targets)
    counted = record_decide.counted(releases, rep, targets)
    kinds = [(t, _confirmed_by(counted, t)) for t in targets]
    unconfirmed = [{"name": t, "kind": k} for t, k in kinds if k is not None and k != record_decide.CD]
    return Reflection(complete, list(missing), unconfirmed)


def _confirmed_by(counted: list[record_decide.Record], name: str) -> Optional[str]:
    """届け先 `name` の反映を最初に記録した記録の確かめ方（`confirmation` の値。反映の記録が無ければ None）。"""
    for r in counted:
        if name in r.environments and r.confirmation is not None and name in r.confirmation:
            return r.confirmation[name]
    return None


def records_verdict(issue: int, view: Mapping[str, Any], enforced: bool) -> Verdict:
    """`release-steps records --version` の outputs 1 つから、課題を閉じてよいか（設計書の「close-check の結果」）。

    バージョン付きの代表（`targets` を持たない）は、同じ値・単位の最後のテストの記録 1 件を `verdict` で判定し、`release` は None。
    バージョンの無い代表は、反映がそろったかを見てから、対象の届け先ごとに `record_decide.pair_verdict` で判定する
    （悪い方を優先する: 反映がそろわない → 不合格 → 保留 → 行の無い届け先 → 合格）。"""
    rep_json = view["release"]
    targets = tuple(view["targets"]) if view["targets"] is not None else None
    if rep_json is not None and targets is None:
        return verdict(issue, _last_check(view, rep_json), enforced)._replace(release=None)
    rep = record_decide.parse_record_json(rep_json) if rep_json is not None else None
    if rep is None or targets is None:
        missing = [] if targets is None else list(targets)
        return Verdict(KEEP, "今回のリリースのリリース記録が無い", False, Reflection(False, missing, [])._asdict())
    releases = [r for r in (record_decide.parse_record_json(j) for j in view[record_decide.RELEASES]) if r is not None]
    reflection = _release_view(rep, releases, targets)
    release = reflection._asdict()
    if not reflection.complete:
        return Verdict(KEEP, f"反映がそろっていない（まだの届け先: {', '.join(reflection.missing)}）", False, release)
    checks = [v for v in view["verifications"] if (v.get("version"), v.get("unit")) == (rep.version, rep.unit)]
    got = {t: record_decide.pair_verdict(checks, t, issue) for t in targets}
    failed = [t for t, v in got.items() if v == "failed"]
    if failed:
        return Verdict(KEEP, f"リリース後テストが不合格の届け先がある: {', '.join(failed)}", False, release)
    waiting = [t for t, v in got.items() if v == "not_run"]
    if waiting:
        return Verdict(ASK, f"保留（not_run）の届け先がある: {', '.join(waiting)}。閉じるかを人が決める", False, release)
    absent = [t for t, v in got.items() if v is None]
    if absent and enforced:
        return Verdict(KEEP, f"その課題の行を持つリリース後テストの記録が無い届け先がある: {', '.join(absent)}", False, release)
    if absent:
        return Verdict(CLOSE, f"その課題の行が無い届け先がある（条件 2 を見ない）: {', '.join(absent)}", False, release)
    return Verdict(CLOSE, "対象の届け先のすべてでリリース後テストに合格した", True, release)


class Condition(NamedTuple):
    """閉じる条件 1 つの扱い。`excluded` は設定で外したか、`reason` はその理由、`enforced` は今回の呼び出しで求めるか。"""

    excluded: bool
    reason: Optional[str]
    enforced: bool


def conditions_of(rules: Mapping[str, CloseRule], require: bool) -> dict[str, Condition]:
    """閉じる条件ごとの扱い（結果 JSON では `Condition._asdict()` の形）。

    `--require` に当たる条件（リリース後テストの記録）は `--require` があり外していないときだけ求める。
    ほかの条件（振り返りの行）は外していなければ求める（行は手順書の AI が確かめる）。
    """
    flagged = flow.require_flag_condition()
    out: dict[str, Condition] = {}
    for name, rule in rules.items():
        excluded = not rule.required
        enforced = not excluded and (require if name == flagged else True)
        out[name] = Condition(excluded, rule.reason if excluded else None, enforced)
    return out


def excluded_note(conditions: Mapping[str, Condition]) -> str:
    """閉じる報告へ転記する、設定で外した条件と理由（無ければ空）。"""
    names = [f"{name}（{c.reason}）" for name, c in conditions.items() if c.excluded]
    return f"。設定で外した条件: {', '.join(names)}" if names else ""


def _stage_line(stage: str) -> re.Pattern[str]:
    return re.compile(rf"^- \[( |x)\] {re.escape(stage)}(?:[^\S\n].*)?$", re.MULTILINE)


def mark_stage(body: str, stage: str, stamp: str) -> Optional[str]:
    """進行のセクションの工程の行にチェックを付けた本文。記録済み・セクションか行が無ければ None（書き換えない）。"""
    start = body.find(ISSUE_SECTION)
    if start < 0:
        return None
    end = body.find(SECTION_END, start)
    stop = end if end >= 0 else len(body)
    m = _stage_line(stage).search(body, start, stop)
    if m is None or m.group(1) == "x":
        return None
    return body[: m.start()] + f"- [x] {stage} — {stamp}" + body[m.end() :]
