"""trunk の本番（版上げの PR があり開発版の PR を作らない取り込み方。#3703）のプランに置くステップ（`release_templates` から分けた）。

- `trial_step`: 承認前の試用（版上げの PR の head から導入を確かめる。決定 7）
- `landed_step`: 公開の欠け（6。merge queue へ入れた・人のマージを待つ）の後に release を打ち直す（決定 3・9。trunk に限らず本番で使う）
"""

from __future__ import annotations

from strategy_rows import RELEASE_PREFIX
from supervise_lib.paths import VERIFY_PY
from supervise_lib.queue_fill import RELEASE_VERSION

RULE_RELEASE_PROD_TRUNK = (
    "trunk の本番（#3703）: release は承認の前に版上げの PR を開いて 10 で止まり、その head（版上げのブランチ）から導入を確かめ（trial）、"
    "承認資料を作ってからリリース承認ゲートに入る。承認の後は approve から続け、release が宣言のマージの主体と方式で本番ブランチへ入れる。"
    "merge queue へ入れた・人のマージを待つ（6）なら release-landed で打ち直す。run のステップが落ちたら、外部の待ち（CI・ネットワーク）の"
    "揺れなら同じステップをもう一度。試用の導入の失敗・タグの重複・権限の不足と、コードや設定に原因があるもの（版数の書き漏れ・文書の形）は stop。"
)


def landed_step(release: dict) -> dict:
    """release と同じコマンドを打ち直すステップ（merge queue のマージか人のマージの後に公開する。#3703 の決定 3・9）。6 は止める。"""
    on_exit = {k: v for k, v in release["on_exit"].items() if k != "10"}
    on_exit["6"] = "stop"
    step = {**release, "id": "release-landed", "on_exit": on_exit, "gate_next": "end"}
    return step


def trial_step(a, repo: str | None, v: str, rts: str) -> dict:
    """承認前の試用（#3703 の決定 7）: 版上げの PR の head（版上げのブランチ）から導入を確かめる。`a.trial_cmd`（fast・auto の
    `.ndf/pace.json` の verify の {ref} を置き換えたもの）があればそれ、無ければ verify-install。失敗すれば承認資料を作らない。"""
    cmd = getattr(a, "trial_cmd", None)
    cmd = cmd.replace(RELEASE_VERSION, v) if cmd else f"{VERIFY_PY} verify-install --ref {RELEASE_PREFIX}{v} --expect {v} --runtimes {rts}"
    return {
        "id": "trial",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 1500,
        **({"cwd": repo} if repo else {}),
        "cmd": cmd,
        "on_fail": "judge",
        "next": "facts",
    }
