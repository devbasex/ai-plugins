"""担当ごとの指標算出と報告の整形（収束ループ共通層）。

**新しい計測の仕組みは作らない。** 状態ファイルに既に記録されている値だけを
集計する。集計の単位は「ランタイム × モデル」である。実行環境（ハーネス）と
モデルは独立に選べる（kiro は claude 系も gpt 系も提供する）ため、ランタイムだけで
まとめると、差がハーネス由来かモデル由来か切り分けられない。
"""
from __future__ import annotations

from typing import Any, Optional

import models as _models

# 集計結果に必ず添える注意書き。**この数値は厳密なベンチマークではない。**
COMPARISON_CAVEATS = [
    "改善項目の難易度が揃わない。輪番はラウンド単位なので、重い項目群を引いた"
    "ランタイムは不利になる。ラウンド数が少ないほど差は偶然に支配される",
    "提案と適用の相性がある。自分が提案した項目を自分が適用するラウンドでは有利になりうる",
    "レビュー担当の厳しさは指標に直結する。指摘件数は「優秀」とも「過剰」とも読めるため、"
    "指摘が修正に至った率と併せて見る",
    "ハーネスとモデルが交絡する。ランタイムを跨いだ比較は、モデルを揃えない限り"
    "「どちらのモデルが優秀か」の答えにならない",
    "kiro の既定 auto は比較に使えない。ラウンドごとに違うモデルが動きうる",
    "公平に比べたいなら、同じ対象・同じ範囲で --model だけ変えて複数回走らせる。"
    "1 回の実行内での比較は参考値にとどまる",
]


def _key(runtime: str, model: Optional[str]) -> str:
    return f"{runtime} / {_models.label(model)}"


def _round_reviews(round_entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in round_entry.get("reviews", []) if isinstance(r, dict)]


def _verdict(review: dict[str, Any], reviewer: str) -> Optional[str]:
    value = review.get(reviewer)
    return value if isinstance(value, str) else None


def aggregate(state: dict[str, Any]) -> dict[str, Any]:
    """状態ファイルから実装担当・レビュー担当それぞれの指標を出す。

    戻り値は `{"impl": {キー: 指標}, "reviewer": {キー: 指標}, "unmeasured": [...]}`。
    `unmeasured` には「何が動いたか分からない」ラウンド（kiro の既定 `auto`）と、
    指定値と実測値が食い違ったラウンドの警告が入る。
    """
    items_by_id = {i["item_id"]: i for i in state.get("items", []) if "item_id" in i}
    impl: dict[str, dict[str, Any]] = {}
    reviewer: dict[str, dict[str, Any]] = {}
    unmeasured: list[str] = []

    for entry in state.get("rounds", []):
        round_no = entry.get("round")
        impl_runtime = entry.get("impl")
        if not impl_runtime:
            continue
        impl_model = (entry.get("impl_model") or {})
        requested = impl_model.get("requested")
        observed = impl_model.get("observed")

        _append_model_measurement_warnings(
            unmeasured, round_no, impl_runtime, requested, observed, "実装担当"
        )

        reviews = _round_reviews(entry)
        _aggregate_impl_round(impl, entry, items_by_id, impl_runtime, requested, reviews)

        reviewer_models = entry.get("reviewer_models") or {}
        for name in entry.get("reviewers", []):
            spec = reviewer_models.get(name) or {}
            r_requested = spec.get("requested")
            r_observed = spec.get("observed")
            _append_model_measurement_warnings(
                unmeasured, round_no, name, r_requested, r_observed, "レビュー担当"
            )
            _aggregate_reviewer_round(reviewer, entry, name, r_requested, reviews)

    return {
        "impl": {k: _finish_impl(v) for k, v in sorted(impl.items())},
        "reviewer": {k: _finish_reviewer(v) for k, v in sorted(reviewer.items())},
        "unmeasured": unmeasured,
    }


def _aggregate_impl_round(
    impl: dict[str, dict[str, Any]],
    entry: dict[str, Any],
    items_by_id: dict[str, dict[str, Any]],
    impl_runtime: str,
    requested: Optional[str],
    reviews: list[dict[str, Any]],
) -> None:
    bucket = impl.setdefault(_key(impl_runtime, requested), _new_impl_bucket())
    bucket["rounds"] += 1
    bucket["seconds"] += _duration(entry, ("apply", "fix"))

    round_items = [items_by_id[i] for i in entry.get("items", []) if i in items_by_id]
    bucket["applied"] += sum(1 for i in round_items if i.get("status") == "done")
    bucket["abandoned"] += sum(
        1 for i in round_items if i.get("status") in {"abandoned", "blocked"}
    )
    bucket["budget_exceeded"] += sum(
        1 for i in round_items if i.get("budget_exceeded")
    )
    bucket["test_failed"] += sum(1 for i in round_items if i.get("test_failed"))
    bucket["fix_rounds"] += int(entry.get("fix_rounds") or 0)

    if reviews:
        first = reviews[0]
        approved_first = all(
            _verdict(first, r) == "APPROVE" for r in entry.get("reviewers", [])
        )
        bucket["first_review_total"] += 1
        bucket["first_review_approved"] += 1 if approved_first else 0


def _aggregate_reviewer_round(
    reviewer: dict[str, dict[str, Any]],
    entry: dict[str, Any],
    name: str,
    requested: Optional[str],
    reviews: list[dict[str, Any]],
) -> None:
    rb = reviewer.setdefault(_key(name, requested), _new_reviewer_bucket())
    # 担当ごとの所要時間があればそれを使う。無ければ 0 のままにする。
    # ラウンドの合計を配ると 2 者分を両方に数えてしまい、比較が成り立たない。
    rb["seconds"] += float((entry.get("reviewer_seconds") or {}).get(name, 0))
    for review in reviews:
        if _verdict(review, name) is None:
            continue
        rb["reviews"] += 1
        findings = [
            f for f in review.get("findings", [])
            if isinstance(f, dict) and f.get("reviewer") == name
        ]
        rb["findings"] += len(findings)
        rb["findings_resolved"] += sum(1 for f in findings if f.get("resolved"))
        others = [o for o in entry.get("reviewers", []) if o != name]
        for other in others:
            other_verdict = _verdict(review, other)
            if other_verdict is None:
                continue
            rb["verdict_pairs"] += 1
            rb["verdict_agreements"] += (
                1 if other_verdict == _verdict(review, name) else 0
            )


def _append_model_measurement_warnings(
    unmeasured: list[str],
    round_no: Any,
    runtime: str,
    requested: Optional[str],
    observed: Optional[str],
    role_label: str,
) -> None:
    warning = _models.mismatch_warning(runtime, requested, observed)
    if warning:
        unmeasured.append(f"round {round_no}: {warning}")
    if not _models.is_measurable(runtime, requested):
        unmeasured.append(
            f"round {round_no}: {runtime} が既定モデル（auto）で動いたため、"
            f"{role_label}の集計から分離する"
        )


def _duration(entry: dict[str, Any], phases: tuple[str, ...]) -> float:
    durations = entry.get("durations") or {}
    return sum(float(durations.get(p) or 0) for p in phases)


def _new_impl_bucket() -> dict[str, Any]:
    return {
        "rounds": 0, "applied": 0, "abandoned": 0, "fix_rounds": 0,
        "budget_exceeded": 0, "test_failed": 0,
        "first_review_total": 0, "first_review_approved": 0, "seconds": 0.0,
    }


def _new_reviewer_bucket() -> dict[str, Any]:
    return {
        "reviews": 0, "findings": 0, "findings_resolved": 0,
        "verdict_pairs": 0, "verdict_agreements": 0, "seconds": 0.0,
    }


def _ratio(num: int, den: int) -> Optional[float]:
    """割合。母数が 0 のときは `None`（0.0 と区別する）。"""
    return None if den == 0 else round(num / den, 3)


def _finish_impl(b: dict[str, Any]) -> dict[str, Any]:
    total_items = b["applied"] + b["abandoned"]
    return {
        **b,
        "first_review_approval_rate": _ratio(
            b["first_review_approved"], b["first_review_total"]
        ),
        "avg_fix_rounds": None if b["rounds"] == 0 else round(b["fix_rounds"] / b["rounds"], 2),
        "budget_exceeded_rate": _ratio(b["budget_exceeded"], total_items),
        "test_failure_rate": _ratio(b["test_failed"], total_items),
    }


def _finish_reviewer(b: dict[str, Any]) -> dict[str, Any]:
    return {
        **b,
        "resolution_rate": _ratio(b["findings_resolved"], b["findings"]),
        "agreement_rate": _ratio(b["verdict_agreements"], b["verdict_pairs"]),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_report(metrics: dict[str, Any]) -> str:
    """人が読む形へ整形する。比較の限界を必ず添える。"""
    lines: list[str] = ["## 実装担当", ""]
    if metrics["impl"]:
        lines += [
            "| ランタイム / モデル | 担当R | 適用 | 見送り | 初回承認率 | 平均修正R | 予算超過率 | テスト失敗率 | 所要秒 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for key, m in metrics["impl"].items():
            lines.append(
                f"| {key} | {m['rounds']} | {m['applied']} | {m['abandoned']} | "
                f"{_fmt(m['first_review_approval_rate'])} | {_fmt(m['avg_fix_rounds'])} | "
                f"{_fmt(m['budget_exceeded_rate'])} | {_fmt(m['test_failure_rate'])} | "
                f"{m['seconds']:.0f} |"
            )
    else:
        lines.append("（記録なし）")

    lines += ["", "## レビュー担当", ""]
    if metrics["reviewer"]:
        lines += [
            "| ランタイム / モデル | レビュー回数 | 指摘 | 修正に至った率 | 判定一致率 | 所要秒 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for key, m in metrics["reviewer"].items():
            lines.append(
                f"| {key} | {m['reviews']} | {m['findings']} | "
                f"{_fmt(m['resolution_rate'])} | {_fmt(m['agreement_rate'])} | "
                f"{m['seconds']:.0f} |"
            )
    else:
        lines.append("（記録なし）")

    if metrics["unmeasured"]:
        lines += ["", "## 集計から分離したラウンド", ""]
        lines += [f"- {w}" for w in dict.fromkeys(metrics["unmeasured"])]

    lines += ["", "## 比較として読むときの限界", ""]
    lines += [f"- {c}" for c in COMPARISON_CAVEATS]
    return "\n".join(lines)
