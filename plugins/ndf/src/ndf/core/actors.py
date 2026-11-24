"""回答した者の形（工程の承認ゲートの `ndf answer --by` と、プランの承認待ちの `ndf plan next --by` が共有する）。"""

from __future__ import annotations

import re

# 人・conductor・organizer・サービス（`service:<主体>`）
ACTORS = re.compile(r"^(human|conductor|organizer|service):[A-Za-z0-9._@/-]+$|^(human|conductor|organizer)$")
# 利用者が直に答えたときの値
HUMAN = "human"
