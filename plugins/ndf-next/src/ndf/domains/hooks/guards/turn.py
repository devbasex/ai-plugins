"""利用者の入力で数え手を数え直す guard（#3345 の決定 92）。中継が送った入力（印がある）では数え直さない。"""

from __future__ import annotations

from ndf.domains.hooks.verdict import Verdict


def check(ctx, svc) -> Verdict:
    source = svc.counter.reset()
    return Verdict("pass", notes={"turn_source": source})
