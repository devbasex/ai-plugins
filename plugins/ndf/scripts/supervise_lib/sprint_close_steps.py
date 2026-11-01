"""new close のまとめのステップ（確定仕様化 → Pull Request・振り返り → 課題を閉じる → 棚卸し）。`sprint.close_plan` が組む。

閉じる時点の決まりの正本は `skills/progress-tracking/SKILL.md` の「スプリントを閉じる」。閉じてよいかは sprint-close.py が
判定し、プランは並び（振り返りの後に close）と `--final-stage` の値だけを持つ（#2228 の I6）。"""

from __future__ import annotations

import shlex

from supervise_lib.paths import HERE


def _spec_pr_steps(a, refs: str) -> list[dict]:
    """確定仕様化のコミットと、その Pull Request を出して ready にするまで。"""
    return [
        {
            "id": "spec",
            "type": "work",
            "full": True,
            "kind": "確定仕様化",
            "stage": "確定仕様化",
            "timeout": 3600,
            "prompt": f"/ndf:plan-to-spec {refs}。課題の issues/ の計画と設計を docs/ へ移し、コミットする"
            "（push しない）。移すものが無ければ何もしない。",
            "next": "pr",
        },
        {
            "id": "pr",
            "type": "pr",
            "stage": "Pull Request",
            "base": a.base,
            "title": f"確定仕様化: スプリント {a.name}",
            "summary": f"スプリント {a.name}（{refs}）の計画と設計を docs/ へ移す。issues/ と docs/ だけを触る",
            "changes": "無し（文書の置き場所だけ）",
            "next": "ready",
        },
        {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "next": "merge-gate"},
    ]


def _close_step(a, repo: str, record_pr: str, issues: str) -> dict:
    """課題を閉じる（sprint-close.py）。プランはどのモードでも retro を通すため、最終工程は振り返りを明示して渡す。"""
    return {
        "id": "close",
        "type": "run",
        "stage": "後片付け",
        "cwd": repo,
        "timeout": 900,
        "cmd": f"python3 {HERE / 'sprint-close.py'} --record-pr {record_pr} --issues {issues} "
        f"--final-stage 振り返り --with-verification --label {shlex.quote(f'スプリント {a.name}の後片付け')}",
        "next": "refine",
    }


def carry_text(a) -> str:
    """棚卸しの指示へ足す、前の周回の持ち越しの 1 文（--carry が無ければ空）。"""
    carry = getattr(a, "carry", None) or []
    if not carry:
        return ""
    nums = " ".join(f"#{n}" for n in carry)
    return f"前の周回の持ち越し {nums} を `upkeep.py` の `--add` で候補へ渡す（{' '.join(map(str, carry))}）。"


def _retro_step(a, refs: str, stats: str) -> dict:
    """振り返り。課題は閉じない（次の close が閉じる）。"""
    return {
        "id": "retro",
        "type": "work",
        "full": True,
        "kind": "振り返り",
        "stage": "振り返り",
        "timeout": 3600,
        "prompt": f"/ndf:retrospective スプリント {a.name}（{refs}）。材料に検査の記録の集計（`{stats}` の出力: "
        "トリガーが立った回数・検査ごとの指摘の件数・検査の後に逃げた不具合の件数）を使い、閾値の"
        "見直しが要るかを書く。課題は閉じない（次の close が閉じる）。",
        "next": "close",
    }


def _refine_step(a, refs: str) -> dict:
    """棚卸し。"""
    return {
        "id": "refine",
        "type": "work",
        "full": True,
        "kind": "棚卸し",
        "stage": "棚卸し",
        "timeout": 3600,
        "prompt": f"/ndf:backlog-refinement スプリント {a.name}（{refs}）。棚卸しの工程として無人で通す。{carry_text(a)}"
        "候補が上限を超えて持ち越し（`metrics.deferred`）が出ても、`--limit` を上げて打ち直さず、番号と件数を"
        "報告に載せる。`upkeep.py apply` が終了コード 10 を返したら、承認を求めず、承認資料のパス"
        "（`presentation_path`）と対象の番号を報告に載せて終える。`--repo` を渡さない（記録のリポジトリの外へ"
        "書かない）。親 issue の起票と子 issue の結び付けは行わず、クラスタと提案する親 issue の内容（題・子の"
        "番号）を報告の「人の判断待ち」に載せて終える。",
        "next": "end",
    }
