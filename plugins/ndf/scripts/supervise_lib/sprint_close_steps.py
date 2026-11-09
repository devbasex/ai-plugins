"""new close のまとめのステップ（確定仕様化 → Pull Request・振り返り → 課題を閉じる → 棚卸し）。`sprint.close_plan` が組む。

閉じる時点の決まりの正本は `skills/progress-tracking/SKILL.md` の「スプリントを閉じる」。閉じてよいかは sprint-close.py が
判定し、プランは並び（振り返りの後に close）と `--final-stage` の値だけを持つ（#2228 の I6）。"""

from __future__ import annotations

import shlex

import limits
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
    """棚卸しの指示へ足す、ループの前の 1 回の持ち越しの 1 文（--carry が無ければ空）。"""
    carry = getattr(a, "carry", None) or []
    if not carry:
        return ""
    nums = " ".join(f"#{n}" for n in carry)
    return f"ループの前の 1 回の持ち越し {nums} を `upkeep.py` の `--add` で候補へ渡す（{' '.join(map(str, carry))}）。"


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


UPKEEP = HERE.parent / "skills" / "backlog-refinement" / "scripts" / "upkeep.py"


def _upkeep(repo: str, *args: str) -> str:
    return shlex.join(["python3", str(UPKEEP), *args, "--root", repo])


def _refine_step(a, refs: str) -> dict:
    """差分の棚卸し（#2315 の決定 2）。順位付け・マイルストーンの再編・枝刈りは見直しの回にだけ行う。"""
    return {
        "id": "refine",
        "type": "work",
        "full": True,
        "kind": "棚卸し",
        "stage": "棚卸し",
        "timeout": 3600,
        "prompt": f"/ndf:backlog-refinement --depth diff スプリント {a.name}（{refs}）。差分の棚卸しの工程として無人で通す。{carry_text(a)}"
        "候補が上限を超えて持ち越し（`metrics.deferred`）が出ても、`--limit` を上げて打ち直さず、番号と件数を"
        "報告に載せる。`upkeep.py apply` が終了コード 10 を返したら、承認を求めず、承認資料のパス"
        "（`presentation_path`）と対象の番号を報告に載せて終える。`--repo` を渡さない（記録のリポジトリの外へ"
        "書かない）。親 issue の起票と子 issue の結び付けは行わず、クラスタと提案する親 issue の内容（題・子の"
        "番号）を報告の「人の判断待ち」に載せて終える。",
        "next": "refine-record",
    }


DEPTH_WORK_TIMEOUT = limits.seconds("supervise.depth-work")


def _depth_steps(a, refs: str, repo: str, depth: str, after: str) -> list[dict]:
    """大整理か見直しの起動の判定・持ち主の宣言・本体・持ち主になれなかった記録（#2315 の決定 5）。

    判定（`depth due`）が 3 を返したら `after` へ飛ぶ。持ち主になれない（10）ときは `skipped` の行を残して `after` へ進む。
    本体の後は成否によらず `release` で自分の宣言を手放してから `after` へ進む。"""
    name = {"overhaul": "大整理", "review": "見直し"}[depth]
    return [
        {
            "id": f"{depth}-due",
            "type": "run",
            "cwd": repo,
            "timeout": 300,
            "cmd": _upkeep(repo, "depth", "due", depth),
            "skip_to": after,
            "next": f"{depth}-claim",
        },
        {
            "id": f"{depth}-claim",
            "type": "run",
            "cwd": repo,
            "timeout": 60,
            # 持ち主の pid は次の work ステップまで生きる supervise.py にする。run のステップは sh -c で動くため、
            # 既定（呼び出し元 = 短命なシェル）だとこのステップの終わりで宣言が無効になる。$PPID はシェルの親 = supervise.py
            # work のステップの中には touch を打つ手順が無いため、その打ち切り（DEPTH_WORK_TIMEOUT）まで見張りに touch させる
            "cmd": _upkeep(repo, "owner", "claim", "--what", depth, "--keepalive", str(DEPTH_WORK_TIMEOUT)) + ' --pid "$PPID"',
            "on_exit": {"10": f"{depth}-skipped"},
            "next": depth,
        },
        {
            "id": depth,
            "type": "work",
            "full": True,
            "kind": "棚卸し",
            "stage": "棚卸し",
            "timeout": DEPTH_WORK_TIMEOUT,
            "prompt": f"/ndf:backlog-refinement --depth {depth} スプリント {a.name}（{refs}）の後の{name}を無人で通す。"
            f"持ち主の実行は `upkeep.py owner status` の `owner.run`、起動した条件は `depths.jsonl` の最後の `{depth}` の判定にある。"
            "人へ問わず、MVV 判定が従う以外の起票は needs-decision を付けて人の判断待ちに置き、報告に載せて終える。"
            "最後に `depth record` と `owner release` を打つ。",
            # 成否によらず次の release を通す。打ち切り・打ち忘れで宣言が残ると、見張りが打ち切りの秒数まで touch し続け、
            # 続く見直しの claim と他のループの調査が not_owner で締め出される
            "on_fail": f"{depth}-release",
            "next": f"{depth}-release",
        },
        {
            "id": f"{depth}-release",
            "type": "run",
            "cwd": repo,
            "timeout": 60,
            # claim と同じく持ち主の pid は supervise.py（$PPID）。既に手放されていれば 0、別の持ち主のものなら 10 で、どちらも先へ進む
            "cmd": _upkeep(repo, "owner", "release", "--what", depth) + ' --pid "$PPID"',
            "on_exit": {"10": after},
            "next": after,
        },
        {
            "id": f"{depth}-skipped",
            "type": "run",
            "cwd": repo,
            "timeout": 60,
            "cmd": _upkeep(repo, "depth", "record", "--depth", depth, "--event", "skipped"),
            "next": after,
        },
    ]


def refine_steps(a, refs: str, repo: str) -> list[dict]:
    """棚卸し: 差分の棚卸し → 深さの記録 → 大整理（起動するときだけ）→ 見直し（起動するときだけ）。

    大整理を見直しより先に置く。両方が起動するループの 1 回では、組み直した後の課題で順位を付けるため。"""
    # 秒・ドル・言語モデルは同じプランの進行の記録の refine の行から写す（#2315 の決定 2）。開発ループとループの 1 回は引数で受ける（決定 3）
    scope = [x for k in ("loop", "lap") if getattr(a, k, None) is not None for x in (f"--{k}", str(getattr(a, k)))]
    record = {
        "id": "refine-record",
        "type": "run",
        "cwd": repo,
        "timeout": 120,
        "cmd": _upkeep(repo, "depth", "record", "--depth", "diff", "--sprint", a.name, "--progress", "{state_dir}/progress.jsonl", *scope),
        "next": "overhaul-due",
    }
    return [
        _refine_step(a, refs),
        record,
        *_depth_steps(a, refs, repo, "overhaul", "review-due"),
        *_depth_steps(a, refs, repo, "review", "end"),
    ]
