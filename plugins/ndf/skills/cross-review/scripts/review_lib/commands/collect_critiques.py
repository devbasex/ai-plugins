"""副命令 `collect-critiques`（#1142 の C2）。"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import review_lib  # noqa: E402
from review_lib import findings as findings_mod, participants as participants_mod, store  # noqa: E402


# 反証で返してよい値（#156）。**一覧に無い値は結ばない。**
CRITIQUE_VERDICTS = (
    "support", "refute", "insufficient_evidence", "duplicate", "out_of_scope",
)


def _mark_evidence_round(st: dict[str, Any], round_no: int) -> None:
    """そのラウンドが証拠集約を通ったことを状態ファイルへ残す（#156）。

    **目印を付けるのは経路の最後（`collect-critiques`）である。** 途中で付けると、
    反証を結ぶ前の区分（`support` も `refute` も 0 件）で数えることになり、根拠を持つ
    `major` の指摘が `insufficient_evidence` へ落ちて収束する。
    """
    marked = st.setdefault("evidence_rounds", [])
    if round_no not in marked:
        marked.append(round_no)


def _critique_targets(st: dict[str, Any], round_no: int, agent: str) -> set[str]:
    """その担当が反証を返す対象の `finding_id`（#549 レビュー対応）。

    **`critique.sh` が渡す射影と同じ条件で選ぶ。** 束ねられた側と、その担当自身が
    提案者である指摘は対象から外れる（統合した組では `origin_runtimes` に載る担当
    すべてが提案者である）。
    """
    ids: set[str] = set()
    for finding in st.get("review_findings") or []:
        if finding.get("round") != round_no or finding.get("merged_into"):
            continue
        if agent in (finding.get("origin_runtimes") or [finding.get("agent")]):
            continue
        ids.add(str(finding.get("finding_id")))
    return ids


def _put_critique(target: dict[str, Any], record: dict[str, Any]) -> None:
    """同じ担当の反証を置き換える（#549 レビュー対応）。

    **`(round, finding_id, agent)` につき残す値は 1 つである。** 積み増すと、同じ
    ラウンドの反証を取り直したときに古い値が残る。`refute` を `support` へ訂正しても
    両方が並ぶため、`_classify_finding` が `refute` を見つけて指摘が `rejected` の
    ままになる（棄却は区分の順で `support` より先に当たる）。
    """
    critiques = target.setdefault("critiques", [])
    for i, existing in enumerate(critiques):
        if existing.get("agent") == record["agent"]:
            critiques[i] = record
            return
    critiques.append(record)


def _load_critique_items(agent: str, path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        review_lib.info(f"⚠ {agent}: 反証の結果を読めません（{path.name}）")
        return []
    raw = payload.get("critiques")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _critique_record(agent: str, item: dict[str, Any]) -> dict[str, Any]:
    record = {
        "agent": agent,
        "verdict": item.get("verdict"),
        "reason": item.get("reason", ""),
    }
    if item.get("duplicate_of"):
        record["duplicate_of"] = item["duplicate_of"]
    return record


def _record_unmatched_critique(
    unmatched: list[dict[str, Any]], record: dict[str, Any], item: dict[str, Any]
) -> None:
    entry = {**record, "finding_id": item.get("finding_id")}
    if entry not in unmatched:
        unmatched.append(entry)


def _attach_critiques(
    st: dict[str, Any],
    pr: int,
    round_no: int,
    findings: dict[str, dict[str, Any]],
    reviewers: list[str],
) -> tuple[int, dict[str, set[str]]]:
    """反証ファイルを読み、結べた反証を指摘へ付ける。"""
    unmatched = st.setdefault("unmatched_critiques", [])
    attached = 0
    covered: dict[str, set[str]] = {a: set() for a in reviewers}

    for agent in reviewers:
        path = store._critique_path(agent, pr, round_no)
        if not path.exists():
            continue
        items = _load_critique_items(agent, path)
        for item in items:
            record = _critique_record(agent, item)
            target = findings.get(item.get("finding_id"))
            if target is None or record["verdict"] not in CRITIQUE_VERDICTS:
                # **取り直しても増やさない。** 同じラウンドで読み直すため、同じ値が
                # 何度も積まれると「結び先なし」の件数が実際より多く見える。
                _record_unmatched_critique(unmatched, record, item)
                continue
            # 提案者は返さない。統合した組では origin_runtimes 全員が提案者である。
            proposers = target.get("origin_runtimes") or [target.get("agent")]
            if agent in proposers:
                continue
            _put_critique(target, record)
            covered[agent].add(str(target.get("finding_id")))
            attached += 1

    return attached, covered


def cmd_collect_critiques(args: argparse.Namespace) -> None:
    """反証の結果を `review_findings[]` へ結ぶ（#156）。

    **担当はファイル名から採る。** 本文の申告を採ると、別の担当を名乗った値をそのまま
    数えることになる。**自分の指摘へは返さない**（統合された指摘では `origin_runtimes`
    に載る担当すべてが提案者である）。

    **結び先の無い値は捨てず `unmatched_critiques` へ残す。** 黙って捨てると、反証が
    0 件のラウンドと、結び先を誤ったラウンドが同じに見える。

    **目印を付けるのは、対象ごとに有効な反証が揃ったときだけである**（#549 レビュー
    対応）。結果ファイルの欠落・不正でも目印を付けると、実行検証を持たない単独の
    `major` が `insufficient_evidence` へ落ち、新規 0 件のまま**未検証で収束する**。
    足りないときは目印を付けず、終了コード 7 と取り直す担当を返して再取得へ戻す。
    """
    pr = args.pr
    st = store._load(pr)
    if not st.get("rounds"):
        review_lib.die("state.rounds が空。`state.py start-round` を先に呼んでください")
    round_no = st["rounds"][-1]["round"]
    findings = _round_finding_index(st, round_no)
    reviewers = participants_mod._round_reviewers(st, round_no)
    attached, covered = _attach_critiques(st, pr, round_no, findings, reviewers)

    # **申告による統合（2 段目）は、反証の後・区分の前に走らせる。** 次のラウンドへ
    # 回すと、同じ主張を別の本文で出した組が統合される前に収束する。
    _merge_declared_duplicates(st, round_no)

    unmatched = st.get("unmatched_critiques") or []
    review_lib.info(f"✅ 反証を取り込みました: {attached} 件"
         + (f"（結び先なし {len(unmatched)} 件）" if unmatched else ""))

    # **揃っていない対象は統合の後に数える。** 束ねられた側は対象から外れるため、
    # 先に数えると、代表へ返された 1 件で足りる組を不足として扱う。
    missing = _missing_critique_targets(st, round_no, reviewers, covered)
    if missing:
        _handle_incomplete_critiques(pr, st, round_no, missing)
        return

    # **経路を通り切ったラウンドだけへ目印を付ける。** 収束の判定はこの目印で母集合を
    # 決める（`_evidence_completed`）。
    _mark_evidence_round(st, round_no)
    store._save(pr, st)


def _round_finding_index(
    st: dict[str, Any], round_no: int
) -> dict[Any, dict[str, Any]]:
    """そのラウンドの指摘を `finding_id` で引ける索引にする。"""
    return {
        f.get("finding_id"): f
        for f in st.get("review_findings") or []
        if f.get("round") == round_no
    }


def _missing_critique_targets(
    st: dict[str, Any],
    round_no: int,
    reviewers: list[str],
    covered: dict[str, set[str]],
) -> dict[str, list[str]]:
    """反証が揃っていない対象を、担当ごとに `finding_id` の並びで返す。"""
    missing: dict[str, list[str]] = {}
    for agent in reviewers:
        unmet = sorted(_critique_targets(st, round_no, agent) - covered[agent])
        if unmet:
            missing[agent] = unmet
    return missing


def _handle_incomplete_critiques(
    pr: int, st: dict[str, Any], round_no: int, missing: dict[str, list[str]]
) -> None:
    """有効な反証が揃わなかったラウンドの扱い（#549 レビュー対応）。

    **目印は付けず、先に付いていた目印は外す**（#732）。目印の無いラウンドは従来どおり全件を
    数えるため、反証が届いていない `major` が区分の絞り込みで落ちて収束することがない。
    取り直しの後もそのラウンドに目印が残ると、「目印を付けないため、このラウンドは全件を
    数えます」の出力と実際の数え方が食い違う。**取り直しは同じラウンドで 1 度だけ
    である**（`judge` の結果なしと同じ作法。2 度続けて揃わないのは対象ではなく実行
    環境の側の事象であり、そのときも目印を付けないまま工程を進める）。
    """
    st["evidence_rounds"] = [
        r for r in st.get("evidence_rounds") or []
        if not _same_round_no(r, round_no)]
    entry = next(
        (r for r in st.get("rounds") or [] if r.get("round") == round_no), None)
    relaunched = list((entry or {}).get("critique_relaunched") or [])
    pending = [a for a in sorted(missing) if a not in relaunched]
    if pending and entry is not None:
        entry["critique_relaunched"] = relaunched + pending
    store._save(pr, st)
    detail = " / ".join(f"{a}: {len(v)} 件" for a, v in sorted(missing.items()))
    if not pending:
        review_lib.info(f"⚠ 取り直した後も反証が揃いません: {detail}。"
             "証拠集約の目印を付けないため、このラウンドは全件を数えます")
        return
    print(f"CRITIQUE_RETRY_AGENTS='{' '.join(pending)}'")
    print(f"CRITIQUE_RETRY_AGENTS_CSV={','.join(pending)}")
    review_lib.info(f"→ 有効な反証が揃っていない: {detail}。目印を付けず、"
         f"同じラウンドで 1 度だけ取り直す: {' '.join(pending)}")
    sys.exit(7)


def _same_round_no(value: Any, round_no: int) -> bool:
    """目印の番号がそのラウンドを指すか。**番号の読み方は `_evidence_completed` と同じ**
    （`int` へ換算して比べ、旧い状態ファイルの文字列の番号も同じラウンドとして読む）。"""
    try:
        return int(value) == int(round_no)
    except (TypeError, ValueError):
        return False


def _declared_duplicate_targets(finding: dict[str, Any]) -> set:
    """その指摘へ付いた `duplicate` の申告が指す先。"""
    targets = set()
    for c in finding.get("critiques") or []:
        if c.get("verdict") == "duplicate" and c.get("duplicate_of"):
            targets.add(c["duplicate_of"])
    return targets


def _merge_declared_duplicates(st: dict[str, Any], round_no: int) -> None:
    """担当が `duplicate` と申告した組を束ねる（#156 の 2 段目）。

    **機械では結べない重複を担当が見つける。その申告は次のラウンドへ回さず、当ラウンドの
    区分の前に適用する。** 回すと、同じ重要度の指摘を 2 者が別の本文で出した組が、
    どちらも `origin_runtimes` 1 者・`support` 0 件のまま `insufficient_evidence` へ
    落ち、統合される前に収束する。

    **相互の申告に限る。** 片側だけの申告では束ねない（`duplicate_candidates` に残る）。
    """
    findings = st.setdefault("review_findings", [])
    targets = [f for f in findings if f.get("round") == round_no]
    # 1 段目を通っていない要素もあるため、ここでも初期化する（順序に依存させない）。
    for f in targets:
        f.setdefault("origin_runtimes", [f.get("agent")])
    by_id = {f.get("finding_id"): f for f in targets}

    for rep in targets:
        if rep.get("merged_into"):
            continue
        for target_id in sorted(_declared_duplicate_targets(rep)):
            other = by_id.get(target_id)
            if other is None or other.get("merged_into") or other is rep:
                continue
            # 相互の申告であることを確かめる
            if rep.get("finding_id") not in _declared_duplicate_targets(other):
                continue
            findings_mod._absorb(rep, other)
