"""親のセッションの環境: 子へ継がせない Claude Code の変数と、起動の印を持つ `relay.py run` が外す変数（#2656）。"""

from __future__ import annotations

import os

from . import common  # noqa: F401  lib/ を sys.path に置く

import claude_accounts as ca  # noqa: E402,I001

# 子へ継がせない Claude Code の環境変数（中から起こしたプロセスが継ぐもの。Claude Code のセッションごとの値）
DROP_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
)
# 親のセッションの環境のうち、起動の印（`NDF_RELAY_SPAWNED`）を持つ `relay.py run` が無条件に外すもの（#2656）。
# 前方一致で外さない。`CLAUDE_CODE_*` には利用者の設定と認証の宣言が多数あるため名前で列挙する（#2656 の決定 3）
PARENT_ENV = (
    *DROP_ENV,
    "NDF_RELAY_DIR",
    "NDF_RELAY_DEPTH",
    "NDF_USAGE_SOURCE",
    "NDF_USAGE_KIND",
    "NDF_USAGE_PLAN",
    "NDF_USAGE_STEP",
)
# 役割と計画。起動の `-e` で付いた値（tmux のセッションの環境にある値）だけを残す（#2656 の決定 4）
LANE_ENV = ("NDF_RELAY_ROLE", "NDF_RELAY_PLAN")


def drop_parent_env(own: dict[str, str]) -> None:
    """`os.environ` から親のセッションの環境を外す（#2656）。

    `PARENT_ENV` を外し、アカウントの環境を元の値へ戻し（`leave_account`）、`LANE_ENV` は `own`（起動で付いた値）へ
    置き換える（`own` に無ければ外す）。外した値は読み出さず、画面にも記録にも出さない（#2656 の I6）。"""
    for k in PARENT_ENV:
        os.environ.pop(k, None)
    ca.leave_account(os.environ)
    for k in LANE_ENV:
        if k in own:
            os.environ[k] = own[k]
        else:
            os.environ.pop(k, None)
