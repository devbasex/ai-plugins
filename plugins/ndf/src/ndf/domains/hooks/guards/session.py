"""SessionStart で予算の順序を検査する guard（#3345 の決定 90）。破れがあれば 1 行の案内を出して続ける。"""

from __future__ import annotations

from ndf.domains.hooks import budget, tables
from ndf.domains.hooks.verdict import Verdict, advise


def check(ctx, svc) -> Verdict | None:
    errors = budget.check_budget(tables.policies(), budget.registrations_of(tables.PLUGIN_ROOT))
    if not errors:
        return None
    return advise(f"hook の予算の検査で破れが {len(errors)} 件ある（先頭: {errors[0]}）。contracts/limits.json の hook の予算を直す")
