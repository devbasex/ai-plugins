"""計画のレーンの問いを、計画の進行表の依頼として載せる（#3035）。

計画のレーンの中継は、起こした耐久ワークフローから計画の名前（`NDF_RELAY_PLAN`）を受け取っている。子のセッションの
AskUserQuestion を hook が `question.json` へ書くと、ラッパーはそれを `ndf lanes question` へ渡し、計画の進行表に
依頼（`request_opened`）を書かせる。オーガナイザーは `ndf lanes wait` でその依頼を受け取る。
計画のレーンでない中継（`NDF_RELAY_PLAN` が無い）は何もしない（問いは中継のファイルだけに残る）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf.domains.relay import subcommand

# 問いのファイル → 載せられなかった理由（載せたら空）
AskPlan = Callable[[Path], str]
# 計画の名前を渡す環境変数（書く側は hub.spawn_env）
PLAN_ENV = "NDF_RELAY_PLAN"


def plan_asker(
    environ: Mapping[str, str], name: str, cwd: Path, plugin_root: Path, timeout: float, runner: Any = subprocess.run
) -> Optional[AskPlan]:
    """計画のレーンの中継なら、問いを計画の依頼として載せる手。計画のレーンでなければ None。"""
    plan = environ.get(PLAN_ENV)
    if not plan:
        return None

    def ask(question: Path) -> str:
        args = ["lanes", "question", plan, "--wrapper", name, "--question", str(question)]
        return subcommand.call(plugin_root, args, timeout, runner, cwd).note

    return ask
