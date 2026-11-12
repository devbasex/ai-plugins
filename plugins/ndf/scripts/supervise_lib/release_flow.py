"""リリースのプランのうち、ブランチ運用の取り込み方（#3121）で変わる部分。`release_templates` が呼ぶ。

取り込み方の行（`branch_flow.Promotion`）の属性だけを読み、取り込み方の名前で分岐しない。値はスプリントの開始時の
スナップショット（`decl.sprint_flow`）から読む。単位を選ばない行（宣言の無いリポジトリの既定）では何も変えない（I2）。
"""

from __future__ import annotations

import shlex

from supervise_lib.decl import DeclError, sprint_flow
from supervise_lib.paths import STEPS_PY


def release_ref(a, dev: bool) -> str:
    """版を上げる比べ先。本番で行がリリースブランチを本番チャネルから組むなら本番チャネル。導入を確かめる前に作業ツリーへ取り込むのは
    ベースブランチのまま（ベースブランチは本番チャネルから分かれており、本番チャネルは fast-forward で入らない）。"""
    flow = sprint_flow(a)
    return flow.row.release_from(flow) if not dev and flow.row.selects_units else a.base


# changed-plugins の結果 JSON（最後の行）の items を「名前 版」の行にする
PICK_BUMPS = (
    'import json,sys; [print(i[\\"name\\"], i[\\"to\\"]) for i in json.loads(sys.stdin.read().strip().splitlines()[-1])[\\"items\\"]]'
)


def bump_others_cmd(a, plugin: str, material: str) -> str:
    """本番のリリースプランの bump-others（不足 c）。前のタグからの差分のある、宣言の release.plugin 以外の
    プラグインを changed-plugins で列挙し、承認資料（material）の表の上げ幅で 1 つずつ上げる（#1752）。
    表を読めない・どれかが落ちたら止まる（PATCH へ倒さない）。"""
    since = f" --since {shlex.quote(a.prev_tag)}" if getattr(a, "prev_tag", None) else ""
    return (
        f'out=$({STEPS_PY} changed-plugins --plugin {plugin}{since} --decided {shlex.quote(material)}) || {{ printf "%s\\n" "$out"; exit 1; }}; '
        f'printf "%s\\n" "$out"; printf "%s\\n" "$out" | python3 -c "{PICK_BUMPS}" | while read -r n to; do '
        f'{STEPS_PY} bump --plugin "$n" --to "$to" --base {release_ref(a, False)} || exit 1; done'
    )


def release_units(a) -> list[str]:
    """行が単位を選ぶならリリースの単位（スプリントブランチ）。選ばない行は空。単位が要るのに無ければ組まない（I5）。"""
    if not sprint_flow(a).row.selects_units:
        return []
    units = list(getattr(a, "units", None) or [])
    if not units:
        raise DeclError(
            "取り込み方（branch_flow.promotion）がリリースの単位を選ぶが、単位（スプリントブランチ）が無い。"
            "まとめたリリースと pace: fast はこの取り込み方のリリースを組まない"
        )
    return units


def promotion_flag(a) -> str:
    """本番の release に渡す取り込み方（行が単位を選ぶときだけ。本番チャネルへ直接出し、戻すマージを入れる。決定 17）。"""
    row = sprint_flow(a).row
    return f" --promotion {row.name}" if row.selects_units else ""


def release_branch_step(a, repo: str | None, version: str, units: list[str]) -> dict:
    """開発版のデプロイの後・承認資料の前に、選んだ単位だけを本番チャネルから組んだリリースブランチを作る（E10・I5）。
    衝突すれば止まり（judge）、承認資料を出さない。`version` は接尾辞を外した版。"""
    prod = sprint_flow(a).production_branch or a.production_branch
    return {
        "id": "release-branch",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 600,
        **({"cwd": repo} if repo else {}),
        "cmd": f"{STEPS_PY} release-branch --version {version}"
        + (f" --production {shlex.quote(prod)}" if prod else "")
        + f" --units {' '.join(map(shlex.quote, units))}",
        "on_fail": "judge",
        "next": "facts",
    }


def fast_flow_refusal(a, pin) -> str | None:
    """pace: fast はスプリントブランチを作らないため、スプリントブランチを本番に届くまで使う取り込み方と組めない。
    `pin` は目録の開始時の値を `a` へ読む関数（`sprint_waves.pin_flow`。同じスプリントの打ち直しは開始時の値で見る）。"""
    pin(a)
    row = sprint_flow(a).row
    if row.keeps_sprint_branch:
        return f"取り込み方 {row.name}（.ndf/worktree.json の branch_flow.promotion）はスプリントブランチを使うため fast と組めない"
    return None
