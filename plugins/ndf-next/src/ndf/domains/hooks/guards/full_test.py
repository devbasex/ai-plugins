"""全体テストの guard（#3112）。worker の中で、設定のテストの suite の全体の実行を止める。

worker（`ndf worker run`）は子へ作業種別を環境変数 `identity.WORKER_KIND_ENV` で渡す。その下の Bash の呼び出しの
単純コマンドが、作業場所の設定（`.ndf/project.json` の `test.suites`）のテストの suite の全体の実行に当たれば止める
（判定は `ndf.testing.weight.is_whole`）。CI テストの設定（`test.ci.only`）の有無は問わない。

- 範囲テスト（suite の `scope_command` の `{paths}` へファイルを入れたもの）と静的解析（`kind` が `lint` の suite）は通す
- 全体テストはプランの後段のステップ（NDF のプログラム）が打つ。並列の計画で worker が打つとマシンが過負荷になる
- 止めた記録は hook の計測（guard の名前 `full-test`・判定 `deny`）と、マーカー `full_test`（止めたコマンド）に残る
"""

from __future__ import annotations

from pathlib import Path

from ndf import identity
from ndf.domains.hooks.verdict import Verdict, deny
from ndf.testing import declared, weight

# マーカーに残すコマンドの字数の上限
_NOTE_CHARS = 200


def check(ctx, svc) -> Verdict | None:
    kind = ctx.env.get(identity.WORKER_KIND_ENV)
    if not kind:
        return None
    suites = declared.load_suites(Path(ctx.cwd))
    if not suites:
        return None
    for cmd in ctx.shell.commands:
        if cmd.argv and weight.is_whole(cmd.argv, suites):
            text = " ".join(cmd.argv)
            return deny(
                f"worker（作業 {kind}）の中で全体テスト（{text}）を打つのを止めた。全体テストはプランの後段のステップが打つ。"
                "範囲テストは設定の suite の scope_command の {paths} へファイルを入れて打つ",
                notes={"full_test": text[:_NOTE_CHARS], "worker_kind": kind},
            )
    return None
