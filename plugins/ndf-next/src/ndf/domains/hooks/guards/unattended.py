"""人のいない実行（`NDF_FACTORY_RUN`）の Stop の guard（#3242）。

最後の応答が区間の報告の形（表の `params.labels` の行がすべてある）に合わなければ差し戻す。背景の作業が残る Stop は
差し戻さない。差し戻しはセッションの数え手が理由をまたいで数える（#3345 の決定 92）。
"""

from __future__ import annotations

from ndf.domains.hooks.verdict import Verdict, pushback


def missing_labels(text: str, labels: list) -> list:
    """応答に無い行の見出し（行頭の `- ` を許す）。"""
    heads = [line.strip().lstrip("-").strip() for line in text.splitlines()]
    return [label for label in labels if not any(h.startswith(label) for h in heads)]


def check(ctx, svc) -> Verdict | None:
    if not ctx.env.get("NDF_FACTORY_RUN") or ctx.background_tasks:
        return None
    labels = svc.params["labels"]
    missing = missing_labels(ctx.last_reply, labels)
    if not missing:
        return None
    form = " / ".join(f"- {label} <中身>" for label in labels)
    return pushback(f"人のいない実行の区間の報告の形に合わない（無い行: {'・'.join(missing)}）。最後に次の行を書いて終える: {form}")
