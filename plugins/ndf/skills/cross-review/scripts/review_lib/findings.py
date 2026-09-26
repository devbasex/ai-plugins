"""指摘の区分・統合の順位・引き継いだ指摘（#1142 の C2）。"""
from __future__ import annotations

from typing import Any

import review_lib  # noqa: E402
from classifications import COUNTED_CLASSIFICATIONS  # noqa: E402
from review_lib import github  # noqa: E402


def _record_carried_over(st: dict[str, Any], repo: str, pr: int) -> bool:
    """再開した時点で残っている未解決の指摘を「引き継いだ指摘」として記録する。

    記録があり、修正の工程を通したラウンドが未記録のあいだは収束させない
    （`cmd_judge`）。中断の前に受けた修正必須の指摘が、修正の工程を 1 度も
    通らないまま収束する経路を塞ぐ。

    取得できなかったときは記録を変更しない。0 件として扱うと、GitHub 側の
    一時的な不調で引継ぎが消える。

    修正の工程を 1 度通した後も、deferred / rejected と最終スイープ待ちの指摘は
    Resolve されないまま残る。これを再開のたびに未処理として数え直すと、収束は
    再開のたびに 1 ラウンドずつ先送りされる。**通した後に新しい指摘が出ていない
    あいだは、通したラウンドの記録をそのまま残す**。新しい指摘が出たときだけ、
    それを含めて数え直し、もう 1 度修正の工程へ通す。

    Returns:
      記録を書き換えたかどうか。
    """
    before = st.get("carried_over")
    threads = github._fetch_unresolved_threads(str(repo or ""), int(pr))
    if threads is None:
        review_lib.info("⚠ 未解決の指摘を取得できませんでした — 引継ぎの記録は変更しません")
        return False
    if not threads:
        st["carried_over"] = None
        return before is not None
    prev = before if isinstance(before, dict) else {}
    prev_fixed = prev.get("fixed_in_round")
    prev_ids = set(prev.get("thread_ids") or [])
    ids = [t["id"] for t in threads]
    new_ids = [i for i in ids if i not in prev_ids]
    if prev_fixed is not None and not new_ids:
        review_lib.info(
            f"↻ 残っている {len(ids)} 件は round {prev_fixed} の修正の工程を通した後の"
            " 分です — 収束は抑止しません（最終スイープが受け持ちます）"
        )
        return False
    st["carried_over"] = {
        "detected_at": review_lib._now(),
        "count": len(ids),
        "thread_ids": ids,
        "fixed_in_round": None,
    }
    review_lib.info(
        f"⚠ 引き継いだ指摘が {len(ids)} 件残っています"
        " — 修正の工程を 1 度通すまで収束させません"
    )
    return True


def _carried_over_pending(st: dict[str, Any]) -> dict[str, Any] | None:
    """修正の工程をまだ通していない引き継いだ指摘があれば、その記録を返す。"""
    carried = st.get("carried_over")
    if not isinstance(carried, dict):
        return None
    if not (carried.get("thread_ids") or carried.get("count")):
        return None
    if carried.get("fixed_in_round") is not None:
        return None
    return carried


# 重要度の高さ。区分の判定と、統合した組の代表が引き継ぐ値に使う。
_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1, "nit": 0}


# 実行の結果の強さ。**組から選び直すときの順である。**
_VERIFY_RANK = {"reproduced": 2, "not_reproduced": 1, "not_run": 0}


def _verify_result(finding: dict[str, Any]) -> str:
    v = finding.get("verification")
    return str((v or {}).get("result") or "not_run")


def _verdicts(finding: dict[str, Any], verdict: str) -> list[str]:
    """その値を返した担当の一覧。"""
    return [
        str(c.get("agent"))
        for c in finding.get("critiques") or []
        if c.get("verdict") == verdict
    ]


def _classify_finding(finding: dict[str, Any]) -> str:
    """1 件の指摘を 6 つの区分のいずれかへ分ける（#156、#732）。

    **上から順に見て、最初に当たった区分を採る。** 実行で再現した指摘を先に採ることで、
    「実行の結果を担当の支持より先に見る」を順序そのもので表す。順 3 を先に置くと、
    機械が再現した事実を担当の再評価が覆す。

    **数えない側へ落とすのは、棄却（順 3）と `minor` 以下（順 6）だけである。** 誰にも
    誤りを示されていない `major` は、反証の有無・担当の数・根拠の 2 項目の有無によらず
    `unrefuted`（順 5）として数える。「立証できない」「範囲外」は誤りだという主張では
    ない（#706）。担当 1 者で反証する相手がいない指摘も同じである（#624）。
    """
    result = _verify_result(finding)
    major = _SEVERITY_RANK.get(str(finding.get("severity")), -1) >= _SEVERITY_RANK["major"]

    if result == "reproduced":
        return "verified_blocking" if major else "verified_non_blocking"
    # **`refute` が効くのは再現していない指摘だけである。**
    if result == "not_reproduced" or _verdicts(finding, "refute"):
        return "rejected"
    # **`minor` 以下は数えない。** 支持が 1 件付いただけでラウンドが増えるのを避ける。
    if not major:
        return "insufficient_evidence"
    # **根拠の 2 項目は見ない。** 別の担当が支持した、または 2 者が独立に出した時点で
    # 「確かめる」目的は果たされている（#706 で支持つきの 2 件が根拠の欠けで落ちた）。
    if _verdicts(finding, "support") or len(finding.get("origin_runtimes") or []) >= 2:
        return "needs_human_judgment"
    return "unrefuted"


def _unrefuted_reason(finding: dict[str, Any]) -> str:
    """なぜ独立に確かめられていないか。**反証の記録は提案者以外の値だけを持つ。**

    空は「反証を返した担当が 0 者」を表す（`no_critique`）。1 件以上あれば、反証は
    あるが支持も否定も無い（`not_supported`）。
    """
    return "not_supported" if finding.get("critiques") else "no_critique"


def _apply_classification(finding: dict[str, Any]) -> str:
    """区分を決めて要素へ書く。**棄却と未反証には理由を残し、他の区分では消す。**"""
    classification = _classify_finding(finding)
    finding["classification"] = classification
    if classification == "unrefuted":
        finding["unrefuted_reason"] = _unrefuted_reason(finding)
    else:
        finding.pop("unrefuted_reason", None)
    if classification != "rejected":
        finding.pop("rejection_reason", None)
        return classification
    if _verify_result(finding) == "not_reproduced":
        command = (finding.get("verification") or {}).get("command") or ""
        finding["rejection_reason"] = (
            f"実行して再現しなかった（{command} が終了コード 0 を返した）"
        )
    else:
        agents = _verdicts(finding, "refute")
        reasons = [
            str(c.get("reason") or "")
            for c in finding.get("critiques") or []
            if c.get("verdict") == "refute"
        ]
        finding["rejection_reason"] = (
            "refute: " + " / ".join(f"{a}: {r}" for a, r in zip(agents, reasons))
        )
    return classification


def _counted_finding_ids(st: dict[str, Any], round_no: int) -> list[str]:
    """新規性が数える指摘の `finding_id`（#156）。

    **数えるのは `verified_blocking` と `needs_human_judgment` と `unrefuted` の 3 つ
    である。** 棄却した指摘を数えると、そのぶんラウンドが増える（#69 で同じ論点が
    5 ラウンド続いた事象）。いずれも `major` 以上で、修正の工程へ渡る。
    """
    ids: list[str] = []
    for finding in st.get("review_findings") or []:
        if finding.get("round") != round_no or finding.get("merged_into"):
            continue
        if _apply_classification(finding) in COUNTED_CLASSIFICATIONS:
            ids.append(str(finding.get("finding_id")))
    return ids


def _absorb(rep: dict[str, Any], other: dict[str, Any]) -> None:
    """`other` を `rep` へ束ねる。**集約は代表の値ではなく組から採る。**"""
    other["merged_into"] = rep["finding_id"]
    rep.setdefault("merged_from", []).append(other["finding_id"])
    for agent in other.get("origin_runtimes") or [other.get("agent")]:
        if agent and agent not in rep["origin_runtimes"]:
            rep["origin_runtimes"].append(agent)

    # 重要度は組の中で最も高いものを引き継ぐ。低い側を採ると、区分が下がる。
    if _SEVERITY_RANK.get(str(other.get("severity")), -1) > \
       _SEVERITY_RANK.get(str(rep.get("severity")), -1):
        rep["severity"] = other["severity"]

    # **実行の結果は組から選び直す。** `reproduced` > `not_reproduced` > `not_run` の
    # 順で採り、出所を残す。**実行し直さない**（2 段目は反証の後にあり、その時点では
    # 組の全員が `verification` を持っている）。
    if _VERIFY_RANK.get(_verify_result(other), -1) > \
       _VERIFY_RANK.get(_verify_result(rep), -1):
        rep["verification"] = other.get("verification")

    # **根拠の対は同じ要素から採る。** `evidence` だけの要素と `falsification` だけの
    # 要素を継ぎ合わせると、どちらも根拠として成り立たないのに、誰も書いていない組を
    # 根拠として作り出す。
    if not rep.get("has_evidence") and other.get("has_evidence"):
        rep["evidence"] = other.get("evidence", "")
        rep["falsification"] = other.get("falsification", "")
        rep["has_evidence"] = True
        rep["evidence_from"] = other["finding_id"]
    elif rep.get("has_evidence"):
        rep.setdefault("evidence_from", rep["finding_id"])
