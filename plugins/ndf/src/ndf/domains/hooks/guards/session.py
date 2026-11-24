"""SessionStart で予算の順序を検査する guard（docs/specifications/ndf-rebuild.md の決定 90）。破れがあれば 1 行の案内を出して続ける。"""

from __future__ import annotations

from ndf.domains.hooks import budget, tables
from ndf.domains.hooks.verdict import Verdict, advise


def check(ctx, svc) -> Verdict | None:
    # 起床の hook の行は検査しない（#4068）。その予算と `cache.wake` は使う時点のラッパーの起動（`registration.rewake_unreachable`）と
    # `ndf hooks check-budget` が読む。SessionStart の 1 起動で読む方針を増やさない（test_budget の読む方針の数）
    errors = budget.check_budget(budget.registrations_of(tables.PLUGIN_ROOT, rewake=False))
    if not errors:
        return None
    return advise(
        f"hook の予算の検査で破れが {len(errors)} 件ある（先頭: {errors[0]}）。contracts/limits/<方針の名前>.json の hook の予算か部品の上限を直す"
    )
