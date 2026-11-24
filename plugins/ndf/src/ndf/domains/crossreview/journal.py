"""指摘のジャーナル（`drive.py` から分けた。#3941）。

相手役の指摘を番号を付けて重ねずに入れ、修正担当の結果・前の実行から引き継いだ指摘・再発を読み込み、
終わり方の判定（`finish_decide`）へ渡す形にする。状態ファイルの `findings` の行の形を持つのはここだけである。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Optional

from ndf.core.converge import oscillation
from ndf.domains.crossreview import criteria_decide, prior_decide, store
from ndf.domains.crossreview import finish_decide as fin


def _text(value: object) -> Optional[str]:
    """空でない文字列ならそのまま、ほかは None（理由・セクションの無い指摘）。"""
    return value if isinstance(value, str) and value.strip() else None


# --- ジャーナル -------------------------------------------------------------------------------


# 指摘の発生の値の欄。番号は別の状態ファイルでも再発でも使い回されるので、PR のスレッドは番号とこの値の両方で
# 指摘に結び付ける（#4016 の不変条件 I4）
OCCURRENCE = "occurrence"


# 必ず人の承認が要る操作に当たる指摘かどうかと、相手役が挙げたその番号（`P1`・`C7`）の欄（#4076）
REDLINE = "redline"
REDLINE_ITEMS = "redline_items"


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


def _located(item: dict[str, Any]) -> dict[str, Any]:
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


def _raised(item: dict[str, Any]) -> dict[str, Any]:
    """相手役の 1 件から、行に足す位置・下げた後の重要度（`severity`）・元の重要度（`raised_severity`。尺度に無い・無いときは
    null）・起きることの 1 文（`impact`。あるときだけ）。#4014"""
    raised = criteria_decide.severity_of(item.get("severity"))
    out = {k: v for k, v in _located(item).items() if k != "severity"}
    out |= {"severity": criteria_decide.effective_severity(raised, item.get("impact")), "raised_severity": raised}
    impact = _text(item.get("impact"))
    if impact is not None:
        out["impact"] = impact.strip()
    return out


def _blocking(row: dict[str, Any]) -> bool:
    return criteria_decide.blocking(criteria_decide.severity_of(row.get("severity")))


class FindingJournal:
    """指摘のジャーナル（id から行の辞書。状態ファイルの findings に並べる形のまま持つ）と、その規則。

    指摘はキーで重ねず、見送り済みの指摘は出し直されても扱わず、直した指摘が戻れば未解決へ戻す。必ず人の承認が要る操作に
    当たる指摘は、修正担当へ渡さず未解決のまま残す（終わり方の判定が欠けにして、人の判断へ戻す）。
    """

    def __init__(self, rows: Sequence[Any] = ()):
        self.rows: dict[str, dict[str, Any]] = {str(f["id"]): f for f in rows if isinstance(f, dict) and "id" in f}
        # 直したのに出し直されて未解決へ戻した指摘の id（呼び手が古いスレッドの記録を切り離す。PR 3944 の r6-6）
        self.revived: list[str] = []

    def _next_id(self, round_no: int) -> str:
        """ラウンド `round_no` で新しく出た指摘の id（`r<ラウンド>-<そのラウンドの通し番号>`）。"""
        return f"r{round_no}-{sum(1 for f in self.rows.values() if f.get('round') == round_no) + 1}"

    def enter(self, round_no: int, raised: Sequence[dict[str, Any]], items: Sequence[str] = ()) -> frozenset[str]:
        """指摘をジャーナルへ入れ、このラウンドで出た収束の妨げになるキーを返す（振動の判定に使う）。`items` は承認済みの
        MVV の項目で、指摘の `mvv` がこの中にあれば行に残す。見送り済みは扱わず、
        直した指摘が戻れば未解決へ戻す。同じ指摘がより重い重要度で出し直されたら（同じラウンドの中でも）重い方を残す。
        収束の妨げになるかは、ラウンドのすべての指摘を重ねた後の重さで決める。"""
        by_key = {f.get("key"): f for f in self.rows.values()}
        touched: dict[str, dict[str, Any]] = {}
        for item in raised:
            text = item.get("text")
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                continue
            key = oscillation.finding_key(text)
            known = by_key.get(key)
            if known is None:
                fid = self._next_id(round_no)
                known = self.rows[fid] = _journal_row(fid, round_no, text, item.get("by"), key, _text(item.get("section"))) | _raised(item)
                by_key[key] = known
            else:
                self._raised_again(known, item, round_no)
            self._classify(known, criteria_decide.mvv_item(item.get("mvv"), items), items)
            touched[key] = known
        return frozenset(key for key, row in touched.items() if _blocking(row) and not row.get(REDLINE))

    def _classify(self, row: dict[str, Any], named: Optional[str], items: Sequence[str]) -> None:
        """指摘が反する MVV の項目 `named` を行に残す。必ず人の承認が要る操作なら、`Value` などの項目や見送りより優先し、
        軽い重さでも人の判断へ戻るよう収束の妨げになる重さにする。前に人が決めた同じ指摘は開き直さない。"""
        if named is None:
            return
        if not criteria_decide.redline(named):
            row.setdefault("mvv", named)
            return
        if not row.get(REDLINE) and row.get("outcome") == fin.SKIPPED:
            row |= {"outcome": fin.UNRESOLVED, "reason": None, OCCURRENCE: new_occurrence()}
            self.revived.append(str(row["id"]))
        held = {named, *store.list_of(row, REDLINE_ITEMS)}
        found = [i for i in items if i in held]
        severity = criteria_decide.at_least_blocking(criteria_decide.severity_of(row.get("severity")))
        row |= {REDLINE: True, REDLINE_ITEMS: found, "mvv": found[0], "severity": severity}

    def _raised_again(self, known: dict[str, Any], item: dict[str, Any], round_no: int) -> None:
        """既にある指摘が出し直されたとき。直した指摘なら未解決へ戻し、重要度は下げた後の値と既にある値の重い方を残す。
        1 文は既にある行に無いときだけ足し、元の重要度は行が既に持つときだけ重い方へ更新する（#4014 の決定 4）。"""
        new = _raised(item)
        if known.get("outcome") == fin.FIXED:
            self._revive(known, new, _text(item.get("section")), round_no)
        severity = criteria_decide.heavier(criteria_decide.severity_of(known.get("severity")), new["severity"])
        if severity is not None:
            known["severity"] = severity
        if "impact" not in known and "impact" in new:
            known["impact"] = new["impact"]
        if "raised_severity" in known:
            raised = criteria_decide.severity_of(known["raised_severity"])
            known["raised_severity"] = criteria_decide.heavier_raised(raised, new["raised_severity"])

    def _revive(self, known: dict[str, Any], new: dict[str, Any], section: Optional[str], round_no: int) -> None:
        """直した指摘が出し直されたとき、未解決へ戻して再発したラウンドの位置とセクションへ置き換える。発生の値も新しくし、
        resolve 済みの古いスレッドへ結び付けない（#4016）。"""
        known |= {"outcome": fin.UNRESOLVED, "reason": None, "round": round_no, OCCURRENCE: new_occurrence()}
        self.revived.append(str(known["id"]))
        # 位置は再発したラウンドの差分のものにする（行が動いた後の新しいスレッドを古い行に付けない。PR 3944 の r7-3）
        # セクションも今回の値で置き換え、省かれていれば消す（古いセクションから場所を復元しない。PR 3944 の r8-5）
        for stale in ("path", "line"):
            known.pop(stale, None)
        known |= {k: v for k, v in new.items() if k in ("path", "line")}
        known["section"] = section

    def carry(self, carried: Sequence[prior_decide.Carried]) -> list[str]:
        """前の実行が PR に残した未解決の指摘をジャーナルへ入れ（ラウンド 0）、入れた id を返す。同じ id か同じキーの行が
        あれば入れない（スレッドと記録のコメントの両方から来た指摘・状態ファイルが既に持つ指摘を重ねない）。"""
        added = []
        keys = {f.get("key") for f in self.rows.values()}
        for c in carried:
            key = oscillation.finding_key(c.text)
            if c.id in self.rows or key in keys:
                continue
            keys.add(key)
            row = _journal_row(c.id, 0, c.text, _text(c.by), key, _text(c.section))
            located = _located({"severity": c.severity, "path": c.path, "line": c.line})
            self.rows[c.id] = row | located
            added.append(c.id)
        return added

    def skipped_texts(self) -> list[str]:
        """見送り済みの指摘の本文。"""
        return [f["text"] for f in self.rows.values() if f.get("outcome") == fin.SKIPPED]

    def pending(self, handed: Sequence[fin.Handed]) -> tuple[list[dict[str, Any]], list[fin.Handed]]:
        """修正担当へ渡す未解決の指摘と、まだ決めていない足すべき内容（必ず人の承認が要る操作に当たる指摘は渡さない）。"""
        open_ = [f for f in self.rows.values() if f.get("outcome") == fin.UNRESOLVED and _blocking(f) and not f.get(REDLINE)]
        undecided = [h for h in handed if h.id not in self.rows or self.rows[h.id].get("outcome") not in fin.DECIDED]
        return open_, undecided

    def redlines(self) -> list[dict[str, Any]]:
        """人の判断を待つ、必ず人の承認が要る操作に当たる未解決の指摘。"""
        return [f for f in self.rows.values() if f.get("outcome") == fin.UNRESOLVED and f.get(REDLINE)]

    def apply_fix(self, items: Sequence[Any], handed: Sequence[fin.Handed], round_no: int) -> list[dict[str, Any]]:
        """修正の結果の指摘ごとの終わり方をマージ、決めた（直した・見送った）件を返す。足すべき内容は新しい行にする。"""
        texts = {h.id: h.text for h in handed}
        reported = [f for f in items if isinstance(f, dict) and f.get("outcome") in fin.DECIDED]
        for item in reported:
            fid = str(item.get("id"))
            reason = _text(item.get("reason"))
            if fid in self.rows:
                self.rows[fid] |= {"outcome": item["outcome"], "reason": reason}
            elif fid in texts:
                self.rows[fid] = _journal_row(fid, round_no, texts[fid], "rebuild") | {"outcome": item["outcome"], "reason": reason}
        return reported

    def reopen(self, fids: Sequence[str]) -> None:
        """直したとした指摘を未解決へ戻す（修正が PR に届かなかった）。"""
        for fid in fids:
            if fid in self.rows:
                self.rows[fid] |= {"outcome": fin.UNRESOLVED, "reason": None}

    def restore_fixed(self, fids: Sequence[str]) -> None:
        """送れずに未解決へ戻した指摘を、送り直せた後に直したへ戻す（未解決のままの行だけ）。"""
        for fid in fids:
            if fid in self.rows and self.rows[fid].get("outcome") == fin.UNRESOLVED:
                self.rows[fid] |= {"outcome": fin.FIXED, "reason": None}

    def findings(self) -> tuple[fin.Finding, ...]:
        """要約に載せる指摘（終わり方を知っている行）。"""
        return tuple(
            fin.Finding(
                f["id"],
                f["text"] if isinstance(f.get("text"), str) else "",
                f["outcome"],
                _text(f.get("reason")),
                criteria_decide.severity_of(f.get("severity")),
            )
            for f in self.rows.values()
            if f.get("outcome") in fin.OUTCOMES
        )
