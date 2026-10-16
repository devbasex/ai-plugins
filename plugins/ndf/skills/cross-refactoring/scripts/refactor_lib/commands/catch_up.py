"""再開で止まっていた時間の分だけ締め切りをずらす（`catch-up`、#1491）。

drive.py が打ち直しのたびに、耐久ワークフローを続ける前に 1 度打つ。`init` の再開（`setup._resume`）も同じ
`pause.resume_after_pause` を呼ぶ（決定 6）。
"""

from __future__ import annotations

import argparse

import statefile

from .. import pause
from ..paths import existing_state


def cmd_catch_up(args: argparse.Namespace) -> None:
    """再開の前に、止まっていた時間の分だけまだ来ていない締め切りをずらす。終了コードは常に 0。

    状態ファイルが無い（最初の `init` の前）・計画の前・最終ゲートの後は何もしない。
    """
    path = existing_state(args.id)
    if path is None:
        return
    state = statefile.load(path)
    if pause.resume_after_pause(path, state) is not None:
        statefile.save(path, state)
