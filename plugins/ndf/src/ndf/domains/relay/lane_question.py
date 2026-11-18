"""計画のレーンの質問を、計画のステートマシンのリクエストとして載せる（#3035）。

計画のレーンの中継は、起動した耐久ワークフローから計画の名前（`NDF_RELAY_PLAN`）を受け取っている。子のセッションの
AskUserQuestion を hook が `question.json` へ書くと、ラッパーはそれを `ndf lanes question` へ渡し、計画のステートマシンに
リクエスト（`request_opened`）を書かせる。オーガナイザーは `ndf lanes wait` でそのリクエストを受け取る。
計画のレーンでない中継（`NDF_RELAY_PLAN` が無い）は何もしない（質問は中継のファイルだけに残る）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf.domains.relay import subcommand

# 質問のファイル → 載せられなかった理由（載せたら空）
AskPlan = Callable[[Path], str]


def plan_asker(
    environ: Mapping[str, str], name: str, cwd: Path, plugin_root: Path, timeout: float, runner: Any = subprocess.run
) -> Optional[AskPlan]:
    """計画のレーンの中継なら、質問を計画のリクエストとして載せる手。計画のレーンでなければ None。"""
    plan = environ.get("NDF_RELAY_PLAN")
    if not plan:
        return None

    def ask(question: Path) -> str:
        args = ["lanes", "question", plan, "--wrapper", name, "--question", str(question)]
        return subcommand.call(plugin_root, args, timeout, runner, cwd).note

    return ask
