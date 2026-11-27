"""指摘のジャーナル（`drive.py` から分けた。#3941）。

相手役の指摘を番号を付けて重ねずに入れ、修正担当の結果・前の実行から引き継いだ指摘・再発を読み込み、
終わり方の判定（`finish_decide`）へ渡す形にする。状態ファイルの `findings` の行の形を持つのはここだけである。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Optional

from ndf.core.converge import oscillation
from ndf.domains.crossreview import criteria_decide, prior_decide, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.report_decide import BY_NDF, JUDGED, MARKS, MVV, PINNED, REDLINE_ITEMS, UNPINNED
from ndf.domains.records import jsonl
from ndf.domains.records.mvv_decide import DEFERS, REVIEW_DEFER, REVIEW_FIXED_AT, State


def _text(value: object) -> Optional[str]:
    """空でない文字列ならそのまま、ほかは None（理由・セクションの無い指摘）。"""
    return value if isinstance(value, str) and value.strip() else None


# --- ジャーナル -------------------------------------------------------------------------------


# 指摘の発生の値の欄。番号は別の状態ファイルでも再発でも使い回されるので、PR のスレッドは番号とこの値の両方で
# 指摘に結び付ける（#4016 の不変条件 I4）
OCCURRENCE = "occurrence"


# 指摘がプロジェクト MVV に反するときの欄のうち、手元の状態ファイルだけに持つもの（#4076。記録のコメントにも載せる欄は
# `report_decide.MARKS`、必ず人の承認が要る操作に当たるかの欄は `finish_decide.REDLINE`）。識別子は、どの MVV かを表す文字列である
# MVV が変わって外した判定の並び（追記だけ）
HISTORY = "mvv_history"
# 見送ったときの MVV の識別子
SKIPPED_MVV = "skipped_mvv"
# 修正担当の結果で終わり方を変えない指摘へ修正担当が返した理由の欄
FIXER_REASON = "fixer_reason"
# 根拠の記述の誤りの指摘が、次の確かめで同じ誤りかを見分ける欄（`citation_key`）
CITATION_KEY = "citation_key"
# 指摘を挙げた相手役の席の並び（#2944 の効果指標。`by` は最初に挙げた席だけを持つ）
RAISERS = "raisers"


def new_occurrence() -> str:
    """指摘の新しい発生の値（`uuid4` の 16 進の先頭 12 字）。"""
    return uuid.uuid4().hex[:12]


def _journal_row(fid: str, round_no: int, text: str, by: Any, key: Optional[str] = None, section: Optional[str] = None) -> dict[str, Any]:
    """ジャーナルの 1 行（未解決で始まり、新しい発生の値を持つ）。新しい行を作る `enter`・`carry`・`apply_fix` がそろって使う。"""
    return {
        "id": fid,
        "key": key,
        "text": text,
        "section": section,
        "round": round_no,
        "outcome": fin.UNRESOLVED,
        "reason": None,
        "by": by,
        OCCURRENCE: new_occurrence(),
    }


def with_occurrences(data: dict[str, Any]) -> bool:
    """状態ファイルの指摘のうち発生の値の無いもの（前のバージョンが書いた指摘）へ新しい値を書く。書いたら真。"""
    wrote = False
    for row in store.list_of(data, store.FINDINGS):
        if isinstance(row, dict) and not (isinstance(row.get(OCCURRENCE), str) and row[OCCURRENCE]):
            row[OCCURRENCE] = new_occurrence()
            wrote = True
    return wrote


def citation_key(error: Mapping[str, Any]) -> str:
    """根拠の記述の誤りを見分ける値。ファイルと引いた項目と誤りの種類で決め、行がずれても変えない。元の文字列のまま比べるので、
    手元の状態ファイルだけに持ち、公開のコメントへ載せない。"""
    raw = json.dumps([error["path"], error["cited"], error["kind"]], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _located(item: Mapping[str, Any]) -> dict[str, Any]:
    """相手役が返した重要度と位置（`severity`・`path`・`line`。#3941）のうち、形の合うものだけ。"""
    out: dict[str, Any] = {}
    severity = criteria_decide.severity_of(item.get("severity"))
    if severity is not None:
        out["severity"] = severity
    path = _text(item.get("path"))
    if path is not None:
        out["path"] = path.strip()
        line = item.get("line")
        if type(line) is int and line > 0:
            out["line"] = line
    return out


def _raised(item: dict[str, Any], named: Optional[str] = None) -> dict[str, Any]:
    """相手役の 1 件（反する MVV の項目は `named`）から、行に足す位置・下げた後の重要度（`severity`）・元の重要度
    （`raised_severity`。尺度に無い・無いときは null）・起きることの 1 文（`impact`。あるときだけ）。#4014"""
    raised = criteria_decide.severity_of(item.get("severity"))
    out = {k: v for k, v in _located(item).items() if k != "severity"}
    effective = criteria_decide.effective_severity(raised, item.get("impact"), redline=criteria_decide.redline(named))
    out |= {"severity": effective, "raised_severity": raised}
    impact = _text(item.get("impact"))
    if impact is not None:
        out["impact"] = impact.strip()
    return out


def _named_any(item: dict[str, Any]) -> bool:
    """相手役が `mvv` に何か書いたか（キーが無い・null・空白だけなら偽）。"""
    value = item.get("mvv")
    return value is not None and bool(str(value).strip())


def _blocking(row: dict[str, Any]) -> bool:
    """修正担当へ渡し、収束を止める指摘か（必ず人の承認が要る操作に当たる指摘は、重さによらず偽）。"""
    return not row.get(fin.REDLINE) and criteria_decide.blocking(criteria_decide.severity_of(row.get("severity")))


def _made_by_ndf(row: Mapping[str, Any]) -> bool:
    """NDF が機械で確かめて作った指摘か。"""
    return row.get("by") == BY_NDF


def _same_mvv(held: Optional[str], now: Optional[str]) -> bool:
    """見送ったときの MVV（`held`）が今回（`now`）と同じか。識別子の無い見送り（識別子を書く前のバージョンの NDF が残した
    もの・どの MVV での見送りか分からないもの）は、今も MVV が無いときだけ同じとみなす。"""
    return held == now or (held is None and now is not None and now.startswith(f"{State.ABSENT}:"))


def _unpin(row: dict[str, Any]) -> None:
    """固定した重さを、固定する前へ戻す。"""
    if UNPINNED in row:
        row["severity"] = row.pop(UNPINNED)
    row.pop(PINNED, None)


def _unclassify(row: dict[str, Any]) -> None:
    """前の MVV で決めた判定を外して履歴に残す。終わり方は変えない。"""
    old = {k: row[k] for k in (JUDGED, MVV, REDLINE_ITEMS, "outcome", SKIPPED_MVV) if k in row}
    row[HISTORY] = [*store.list_of(row, HISTORY), old]
    _unpin(row)
    for key in (MVV, fin.REDLINE, REDLINE_ITEMS, JUDGED):
        row.pop(key, None)


def _classify(row: dict[str, Any], item: dict[str, Any], named: str, items: Sequence[str], mvv: Optional[str]) -> None:
    """相手役が `item` で挙げた MVV の項目 `named` を指摘 `row` に残す。挙げた項目は、言われた順によらず `items`（MVV の項目）の順に並べる。
    必ず人の承認が要る操作が 1 つでもあれば、`Value` などの項目より優先して重さの固定を外す。"""
    held = {named, row.get(MVV), *store.list_of(row, REDLINE_ITEMS)}
    found = [i for i in items if i in held]
    redlines = [i for i in found if criteria_decide.redline(i)]
    if redlines:
        _unpin(row)
        row |= {MVV: redlines[0], fin.REDLINE: True, REDLINE_ITEMS: redlines, JUDGED: mvv}
        return
    severity = criteria_decide.severity_of(row.get("severity"))
    pinned = [criteria_decide.at_least_blocking(s) for s in (severity, criteria_decide.severity_of(item.get("severity")))]
    row.setdefault(UNPINNED, severity)
    row |= {MVV: found[0], PINNED: True, JUDGED: mvv, "severity": criteria_decide.heavier(*pinned)}


def _skip_is_void(row: dict[str, Any], named: Optional[str], mvv: Optional[str]) -> bool:
    """出し直された見送り済みの指摘を、判定し直すか。別の MVV での見送りと、必ず人の承認が要る操作と今回初めて言われた
    指摘（修正担当が見送りを決められない）が当たる。"""
    if row.get("outcome") != fin.SKIPPED:
        return False
    redline = criteria_decide.redline(named) and not row.get(fin.REDLINE)
    return redline or not _same_mvv(row.get(SKIPPED_MVV), mvv)


def _judged_items(marks: Mapping[str, Any], items: Sequence[str]) -> list[str]:
    """前の実行が残した判定 `marks`（`report_decide.MARKS` の欄）の項目のうち、`items`（MVV の項目）にあるもの。"""
    return [i for i in (marks.get(MVV), *store.list_of(marks, REDLINE_ITEMS)) if criteria_decide.mvv_item(i, items)]


def _restore(row: dict[str, Any], c: prior_decide.Carried, items: Sequence[str], mvv: Optional[str]) -> bool:
    """引き継いだ指摘 `row` に、前の実行が残した MVV の判定を戻す。別の MVV での判定は外して履歴に残す。同じ MVV なのに項目が
    `items`（MVV の項目）に無ければ、判定を外して偽。"""
    if c.judged_mvv is None:
        return True
    row |= {k: v for k, v in c._asdict().items() if k in MARKS and v is not None}
    if c.judged_mvv != mvv:
        _unclassify(row)
        return True
    if row["by"] == BY_NDF:
        return True
    named = _judged_items(row, items)
    if not named:
        _unclassify(row)
        return False
    _classify(row, {}, named[0], items, mvv)
    return True


def _unknown_items(where: str, unknown: Sequence[str]) -> str:
    """挙げた項目が MVV に無い指摘の注意。"""
    return f"{where} {'・'.join(dict.fromkeys(unknown))} の mvv が承認済みの MVV の項目に無いので、普通の指摘として扱った"


def _mvv_warnings(round_no: int, unknown: Sequence[str], unexplained: Sequence[str]) -> list[str]:
    """MVV の項目の書き方に問題のある指摘の注意（無ければ空）。相手役が書いた文は載せず、指摘の id だけを並べる。"""
    out = []
    if unknown:
        out.append(_unknown_items(f"round {round_no} の指摘", unknown))
    if unexplained:
        names = "・".join(dict.fromkeys(unexplained))
        out.append(f"round {round_no} の MVV に反する指摘 {names} に起きること（impact）が無いが、重さを下げずに扱った")
    return out


def _serious(row: Mapping[str, Any]) -> bool:
    """重大な指摘（critical か major）か。重要度の分からない指摘は数えない。"""
    severity = criteria_decide.severity_of(row.get("severity"))
    return severity is not None and criteria_decide.blocking(severity)


def experiment_values(
    rows: Sequence[Mapping[str, Any]], first: Optional[Collection[str]], seats: Sequence[str]
) -> tuple[Optional[int], dict[str, Optional[float]]]:
    """効果指標の元の数（#2944）: 1 ラウンド目（`first` の id）の採用された重大な指摘の数と、シートごとの挙げた割合。

    採用は修正担当が直した指摘で、相手役が挙げたもの（`raisers`）だけを数える。数は、`first` が無いか、1 ラウンド目の重大な指摘に
    決めていないものがあれば None。割合は、シートが 2 つ以上で採用が 1 件以上のときだけ値を持つ。
    """
    major = [r for r in rows if _serious(r) and store.list_of(r, RAISERS)]
    mine = [r for r in major if first is not None and r["id"] in first]
    unknown = first is None or any(r["outcome"] not in fin.DECIDED for r in mine)
    count = None if unknown else sum(r["outcome"] == fin.FIXED for r in mine)
    adopted = [store.list_of(r, RAISERS) for r in major if r["outcome"] == fin.FIXED]
    valued = len(seats) > 1 and bool(adopted)
    return count, {s: sum(s in by for by in adopted) / len(adopted) if valued else None for s in seats}


class FindingJournal:
    """指摘のジャーナル（id から行の辞書。状態ファイルの findings に並べる形のまま持つ）と、その規則。

    指摘はキーで重ねず、見送り済みの指摘は出し直されても扱わず、直した指摘が戻れば未解決へ戻す。MVV が変わった後に出し直された
    指摘は、見送り済みでも今の MVV で判定し直す。必ず人の承認が要る操作に当たる指摘は、修正担当へ渡さず、収束も止めずに、
    未解決のまま残す（#4076）。
    """

    def __init__(self, rows: Sequence[Any] = ()):
        self.rows: dict[str, dict[str, Any]] = {str(f["id"]): f for f in rows if isinstance(f, dict) and "id" in f}
        # 直した・見送ったのに出し直されて未解決へ戻した指摘の id（呼び手が古いスレッドの記録を切り離す。PR 3944 の r6-6）
        self.revived: list[str] = []
        # 最後の `enter` で相手役が挙げた指摘の id
        self.entered: list[str] = []
        # 最後の `enter` か `carry` で、指摘の MVV の項目について結果に残す注意
        self.warnings: list[str] = []
        # 最後の `carry` で、前の実行が残した MVV の判定を重ねて変わった、既にある指摘の id
        self.merged: list[str] = []

    def _next_id(self, round_no: int, by_ndf: bool = False) -> str:
        """ラウンド `round_no` で新しく出た指摘の id（`r<ラウンド>-<そのラウンドの通し番号>`。NDF が確かめて作った指摘は番号の前に
        `n` を付け、相手役の指摘と分けて数える）。"""
        mine = [f for f in self.rows.values() if f.get("round") == round_no and (f.get("by") == BY_NDF) == by_ndf]
        return f"r{round_no}-{'n' if by_ndf else ''}{len(mine) + 1}"

    def enter(
        self, round_no: int, raised: Sequence[dict[str, Any]], items: Sequence[str] = (), mvv: Optional[str] = None
    ) -> frozenset[str]:
        """指摘をジャーナルへ入れ、このラウンドで出た収束の妨げになるキーを返す（振動の判定に使う）。`items` は承認済みの
        MVV の項目、`mvv` はその識別子。見送り済みは扱わず（MVV が変わっていれば判定し直す）、
        直した指摘が戻れば未解決へ戻す。同じ指摘がより重い重要度で出し直されたら（同じラウンドの中でも）重い方を残す。
        収束の妨げになるかは、ラウンドのすべての指摘を重ねた後の重さで決める。"""
        by_key = {f.get("key"): f for f in self.rows.values()}
        touched: dict[str, dict[str, Any]] = {}
        unknown: list[str] = []
        unexplained: list[str] = []
        for item in raised:
            text = item.get("text")
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                continue
            key = oscillation.finding_key(text)
            named = criteria_decide.mvv_item(item.get("mvv"), items)
            known = by_key.get(key)
            if known is None:
                fid = self._next_id(round_no)
                row = _journal_row(fid, round_no, text, item.get("by"), key, _text(item.get("section")))
                known = self.rows[fid] = row | _raised(item, named)
                by_key[key] = known
            else:
                self._raised_again(known, item, round_no, named, mvv)
            if isinstance(item.get("by"), str):
                known[RAISERS] = list(dict.fromkeys([*store.list_of(known, RAISERS), item["by"]]))
            if named is not None and known.get("by") != BY_NDF:
                _classify(known, item, named, items, mvv)
                if _text(item.get("impact")) is None:
                    unexplained.append(str(known["id"]))
            elif named is None and items and _named_any(item):
                unknown.append(str(known["id"]))
            touched[key] = known
        self.warnings = _mvv_warnings(round_no, unknown, unexplained)
        self.entered = [str(row["id"]) for row in touched.values()]
        return frozenset(key for key, row in touched.items() if _blocking(row))

    def _raised_again(self, known: dict[str, Any], item: dict[str, Any], round_no: int, named: Optional[str], mvv: Optional[str]) -> None:
        """既にある指摘が出し直されたとき。前の MVV での判定は外し、直した指摘と、判定し直す見送り（`_skip_is_void`）は
        未解決へ戻し、重要度は下げた後の値と既にある値の重い方を残す（重さを固定した指摘は、固定する前の重さも同じに残す）。
        1 文は既にある行に無いときだけ足し、元の重要度は行が既に持つときだけ重い方へ更新する（#4014 の決定 4）。"""
        new = _raised(item, named)
        if known.get(JUDGED) not in (None, mvv):
            _unclassify(known)
        if known.get("outcome") == fin.FIXED or _skip_is_void(known, named, mvv):
            self._revive(known, new, _text(item.get("section")), round_no)
        severity = criteria_decide.heavier(criteria_decide.severity_of(known.get("severity")), new["severity"])
        if severity is not None:
            known["severity"] = severity
        if UNPINNED in known:
            known[UNPINNED] = criteria_decide.heavier(criteria_decide.severity_of(known[UNPINNED]), new["severity"])
        if "impact" not in known and "impact" in new:
            known["impact"] = new["impact"]
        if "raised_severity" in known:
            raised = criteria_decide.severity_of(known["raised_severity"])
            known["raised_severity"] = criteria_decide.heavier_raised(raised, new["raised_severity"])

    def _revive(self, known: dict[str, Any], new: dict[str, Any], section: Optional[str], round_no: int) -> None:
        """直した・見送った指摘が出し直されたとき、未解決へ戻して再発したラウンドの位置とセクションへ置き換える。発生の値も新しくし、
        resolve 済みの古いスレッドへ結び付けない（#4016）。"""
        known |= {"outcome": fin.UNRESOLVED, "reason": None, "round": round_no, OCCURRENCE: new_occurrence()}
        self.revived.append(str(known["id"]))
        # 位置は再発したラウンドの差分のものにする（行が動いた後の新しいスレッドを古い行に付けない。PR 3944 の r7-3）
        # セクションも今回の値で置き換え、省かれていれば消す（古いセクションから場所を復元しない。PR 3944 の r8-5）
        for stale in ("path", "line", SKIPPED_MVV, REVIEW_DEFER):
            known.pop(stale, None)
        known |= {k: v for k, v in new.items() if k in ("path", "line")}
        known["section"] = section

    def ingest_citations(
        self, errors: Optional[Sequence[Mapping[str, Any]]], mvv: Optional[str], round_no: int, *, recorded: bool = True
    ) -> bool:
        """根拠の記述の確かめの結果 `errors`（`changes.citations`。None は確かめられなかった）を NDF の指摘にする。指摘が増えたか
        変わったら真。

        見つかった誤りは、修正担当の回答やスレッドの resolve によらず未解決にし、見つからなかった未解決の指摘は直したとする。
        確かめられなければ何も変えない。`recorded` は、確かめた head のラウンドの記録があるか（無ければ、未解決の指摘の位置を
        記録のあるラウンドのまま残す。PR のスレッドは、ラウンドの記録の head に付けるためである）。"""
        if errors is None:
            return False
        mine = [f for f in self.rows.values() if _made_by_ndf(f)]
        held = {f.get(CITATION_KEY): f for f in mine}
        found = {citation_key(e): e for e in reversed(errors)}
        before = {str(f["id"]): dict(f) for f in mine}
        for key, error in reversed(found.items()):
            self._cite(held.get(key), key, error, mvv, round_no, recorded)
        for row in mine:
            if row.get("outcome") == fin.UNRESOLVED and row.get(CITATION_KEY) not in found:
                row |= {"outcome": fin.FIXED, "reason": None, JUDGED: mvv}
        return before != {str(f["id"]): f for f in self.rows.values() if _made_by_ndf(f)}

    def _cite(
        self, row: Optional[dict[str, Any]], key: str, error: Mapping[str, Any], mvv: Optional[str], round_no: int, recorded: bool
    ) -> None:
        """根拠の記述の誤り 1 件を、同じ誤りの指摘 `row` に書く。無ければ新しい指摘にする。"""
        place = _located(error)
        if row is None:
            fid = self._next_id(round_no, by_ndf=True)
            row = self.rows[fid] = _journal_row(fid, round_no, error["text"], BY_NDF) | place
        elif row.get("outcome") != fin.UNRESOLVED:
            self._revive(row, place, None, round_no)
        elif recorded:
            row |= {"round": round_no, **place}
        row |= {"key": oscillation.finding_key(error["text"]), "text": error["text"], "severity": criteria_decide.at_least_blocking(None)}
        row |= {CITATION_KEY: key, "cited": error["cited"], "unknown": error["unknown"], JUDGED: mvv}

    def carry(self, carried: Sequence[prior_decide.Carried], items: Sequence[str] = (), mvv: Optional[str] = None) -> list[str]:
        """前の実行が PR に残した未解決の指摘をジャーナルへ入れ（ラウンド 0）、入れた id を返す。同じ id か同じキーの行が
        あれば入れず（スレッドと記録のコメントの両方から来た指摘・状態ファイルが既に持つ指摘を重ねない）、MVV の判定だけをその行へ
        重ねる（`merge_marks`）。`items` は承認済みの MVV の項目、`mvv` はその識別子で、同じ MVV での判定だけを戻す。"""
        added = []
        unknown = []
        self.merged = []
        by_key = {f.get("key"): f for f in self.rows.values()}
        for c in carried:
            key = oscillation.finding_key(c.text)
            # スレッドの無い指摘の id（`p-<元の id>`）は別の保存先の違う指摘とも重なるので、同じ本文の行だけに判定を重ねる
            known = by_key.get(key) or (self.rows.get(c.id) if c.comment is not None else None)
            if known is not None:
                place = _located({"path": c.path, "line": c.line}) | {"section": _text(c.section)}
                if self.merge_marks(str(known["id"]), c._asdict(), items, mvv, place):
                    self.merged.append(str(known["id"]))
                continue
            if c.id in self.rows:
                continue
            row = _journal_row(c.id, 0, c.text, _text(c.by), key, _text(c.section))
            located = _located({"severity": c.severity, "path": c.path, "line": c.line})
            row = self.rows[c.id] = by_key[key] = row | located
            if not _restore(row, c, items, mvv):
                unknown.append(c.id)
            added.append(c.id)
        self.warnings = [_unknown_items("引き継いだ指摘", unknown)] if unknown else []
        return added

    def merge_marks(
        self, fid: str, marks: Mapping[str, Any], items: Sequence[str], mvv: Optional[str], place: Optional[Mapping[str, Any]] = None
    ) -> bool:
        """前の実行が残した MVV の判定 `marks` のうち、今回と同じ MVV `mvv` でのものを、既にある指摘 `fid` に重ねる。変わったら真。

        閉じた指摘は、必ず人の承認が要る操作と今回初めて分かったときだけ未解決へ戻す。`place`（位置とセクション）があれば別の場所で
        見つかった再発として戻し（`_revive`）、無ければ開いたままの同じスレッドの指摘として、スレッドを持ったまま戻す。"""
        row = self.rows[fid]
        named = _judged_items(marks, items)
        if not named or mvv is None or marks.get(JUDGED) != mvv or row.get("by") == BY_NDF:
            return False
        before = dict(row)
        closed = row.get("outcome") != fin.UNRESOLVED
        known = bool(row.get(fin.REDLINE)) and row.get(JUDGED) == mvv
        if closed and (known or not any(criteria_decide.redline(i) for i in named)):
            return False
        if row.get(JUDGED) not in (None, mvv):
            _unclassify(row)
        if closed and place is not None:
            self._revive(row, dict(place), place.get("section"), 0)
        elif closed:
            self.reopen([fid])
            row.pop(SKIPPED_MVV, None)
        for item in named:
            _classify(row, {}, item, items, mvv)
        return before != row

    def restate(self, mvv: Optional[str]) -> list[str]:
        """`mvv` と違う MVV で決めた判定を外し、外した指摘の id を返す（出し直されない指摘に前の判定を残さない）。"""
        stale = [f for f in self.rows.values() if f.get(JUDGED) not in (None, mvv)]
        for row in stale:
            _unclassify(row)
        return [str(f["id"]) for f in stale]

    def skipped_texts(self, mvv: Optional[str] = None) -> list[str]:
        """今回と同じ MVV `mvv` で見送った指摘の本文。"""
        return [f["text"] for f in self.rows.values() if f.get("outcome") == fin.SKIPPED and _same_mvv(f.get(SKIPPED_MVV), mvv)]

    def handable(self) -> list[dict[str, Any]]:
        """修正担当へ渡す未解決の指摘。"""
        return [f for f in self.rows.values() if f.get("outcome") == fin.UNRESOLVED and _blocking(f)]

    def pending(self, handed: Sequence[fin.Handed]) -> tuple[list[dict[str, Any]], list[fin.Handed]]:
        """修正担当へ渡す未解決の指摘と、まだ決めていない足すべき内容。"""
        undecided = [h for h in handed if h.id not in self.rows or self.rows[h.id].get("outcome") not in fin.DECIDED]
        return self.handable(), undecided

    def apply_fix(
        self, items: Sequence[Any], handed: Sequence[fin.Handed], round_no: int, *, mvv: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """修正の結果の指摘ごとの終わり方をマージし、決めた（直した・見送った）件を返す。足すべき内容は新しい行にする。
        `mvv` は修正担当を起動したときの MVV の識別子（分からなければ None）で、見送りに残す。見送りの区分（`defer`）は知っている
        値だけを残す（無い・知らない値の見送りは区分の無い見送りとして記録され、承認ゲートの保留になる。#3959）。

        NDF が機械で確かめて作った指摘（閉じるのは機械の確かめだけ）と、必ず人の承認が要る操作に当たる指摘（人の承認を待つ）への
        結果は、終わり方を変えず、修正担当の理由だけを残す。返す件にも入れない。返した件のうち直した指摘は、修正のコミットを
        送り直した後に直したへ戻るためである（#4109）。"""
        texts = {h.id: h.text for h in handed}
        taken = []
        for item in items:
            if not isinstance(item, dict) or item.get("outcome") not in fin.DECIDED:
                continue
            fid = str(item.get("id"))
            reason = _text(item.get("reason"))
            row = self.rows.get(fid)
            if row is not None and (row.get("by") == BY_NDF or row.get(fin.REDLINE)):
                row[FIXER_REASON] = reason
                continue
            taken.append(item)
            if row is None and fid in texts:
                row = self.rows[fid] = _journal_row(fid, round_no, texts[fid], "rebuild")
            if row is None:
                continue
            row |= {"outcome": item["outcome"], "reason": reason}
            row.pop(SKIPPED_MVV, None)
            row.pop(REVIEW_DEFER, None)
            if item["outcome"] == fin.SKIPPED and mvv is not None:
                row[SKIPPED_MVV] = mvv
            if item["outcome"] == fin.SKIPPED and item.get(REVIEW_DEFER) in DEFERS:
                row[REVIEW_DEFER] = item[REVIEW_DEFER]
            # 直した時刻は、別の状態ディレクトリが記録した見送りとの前後を照らすために残す（#3959）
            row.pop(REVIEW_FIXED_AT, None)
            if item["outcome"] == fin.FIXED:
                row[REVIEW_FIXED_AT] = jsonl.now_exact()
        return taken

    def reopen(self, fids: Sequence[str]) -> None:
        """直したとした指摘を未解決へ戻す（修正が PR に届かなかった）。"""
        for fid in fids:
            if fid in self.rows:
                self.rows[fid] |= {"outcome": fin.UNRESOLVED, "reason": None}

    def restore_fixed(self, fids: Sequence[str]) -> None:
        """送れずに未解決へ戻した指摘を、送り直せた後に直したへ戻す（未解決のままの行だけ。必ず人の承認が要る操作に当たる
        指摘は、承認を待つまま残す）。"""
        for fid in fids:
            row = self.rows.get(fid)
            if row is not None and row.get("outcome") == fin.UNRESOLVED and not row.get(fin.REDLINE):
                row |= {"outcome": fin.FIXED, "reason": None}

    def findings(self) -> tuple[fin.Finding, ...]:
        """要約に載せる指摘（終わり方を知っている行）。"""
        return tuple(
            fin.Finding(
                f["id"],
                f["text"] if isinstance(f.get("text"), str) else "",
                f["outcome"],
                _text(f.get("reason")),
                criteria_decide.severity_of(f.get("severity")),
                _text(f.get(MVV)),
                bool(f.get(fin.REDLINE)),
                f.get("by") == BY_NDF,
            )
            for f in self.rows.values()
            if f.get("outcome") in fin.OUTCOMES
        )
