"""merged-steps.py の merge-gate・merge-when-green・promote（承認ゲート 2 の判定を持つマージ。#1336）。

判定は `lib/delivery.py` の `judge_target`。自動反映の本番チャネルか、決められない宛先へのマージは、
`--gate-approved user|mvv` が無ければ `gh pr merge` を打たずに承認ゲート 2（status: gate・終了コード 10）で止める。
merge-gate は、PR 本文の手動確認（マージ前）に印の無い行があっても承認ゲートで止める（`lib/manual_checks.py`）。
後片付け（`cleanup`）は merged-steps.py が持ち、呼び出しの引数で受ける。
"""

from __future__ import annotations

import json

import delivery
import gh_parts
import manual_checks
from merged_lib.checks import TOOL, GreenWatch, pr_state
from step_result import StepError, approval_present, emit, git_root, result


def add_wait_args(m):
    """merge-when-green と promote が受ける、待ちと承認の引数。"""
    m.add_argument("--method", choices=("merge", "squash", "rebase"), default="merge")
    m.add_argument("--interval", type=float, default=10.0, help="CI を読み直す間隔（秒）")
    m.add_argument("--recheck", type=float, default=5.0, help="pending を見ずに通っていたとき、確かめ直すまでの間隔（秒）")
    m.add_argument(
        "--no-checks-after", type=float, default=60.0, help="rollup が空のままこの秒数を過ぎたら、CI の無いリポジトリとしてマージする"
    )
    m.add_argument("--timeout", type=float, default=3600.0, help="CI を待つ上限（秒）")
    m.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="実行が終わったのにチェックが pending のまま続けば、ジョブを 1 度だけ再実行するまでの秒数",
    )
    m.add_argument(
        "--gate-approved",
        choices=("user", "mvv"),
        help="承認ゲート 2 を通した担い手（利用者 / MVV 判定）。自動反映の本番チャネルへのマージは、これが無ければ止まる",
    )


# --- 承認ゲート 2（自動反映の本番チャネルへのマージ。#1336） ------------------------

GATE = "production-merge"
GATE_ROLLBACK = (
    "マージのコミットを revert する Pull Request を同じ宛先へマージする。本番系には revert が反映されるまで変更が残り、"
    "その間に利用者が触れた結果（データの書き込みなど）は revert で戻らない"
)


def _change_text(info):
    """承認資料の差分の量の文言。"""
    return f"{info.get('changedFiles', '?')} ファイル・+{info.get('additions', '?')} / -{info.get('deletions', '?')} 行"


def _judge_rows(verdict):
    """承認資料の判定の行（判定・理由・宣言の各項目）。"""
    return [("判定", verdict.value), ("理由", verdict.reason), *[(i["name"], i["result"]) for i in verdict.items]]


def gate_stop(n, verdict, info=None, next_cmd=None, plan_step="merge-approved"):
    """承認の無い本番系へのマージを止める結果（status: gate・終了コード 10）を出して終える。承認資料を書く。"""
    info = info or {}
    target = verdict.target or "（不明）"
    what = f"#{n}" if n else f"宛先 {target} へのマージ"
    lead = (
        f"{what} の宛先 {target} は自動反映の本番チャネルである"
        if verdict.value == delivery.PRODUCTION
        else f"{what} が本番系へ出るかを決められない（{verdict.reason}）"
    )
    change = _change_text(info) if info else "（Pull Request を渡していないため読んでいない）"
    target_row = {"url": info.get("url") or what}
    if info.get("title"):
        target_row["title"] = info["title"]
    if info.get("headRefName"):
        target_row["base_head"] = f"{target} ← {info['headRefName']}"
    path = approval_present(
        TOOL,
        f"merge-{n or target}",
        title=f"{what} のマージ（本番系への反映。承認ゲート 2）",
        targets=[target_row],
        change=change,
        judge=[*_judge_rows(verdict), ("宛先", target)],
        consent=[f"{what} を {target} へマージし、本番系へ反映する"],
        rollback=GATE_ROLLBACK,
    )
    cmd = next_cmd or (f"merge-when-green {n} --gate-approved user" if n else "merge-when-green <PR番号> --gate-approved user")
    items = verdict.items + [{"kind": "pr", "name": what, "result": "gate", "base": target}]
    emit(
        result(
            TOOL,
            "gate",
            f"{lead}。承認ゲート 2 の承認が要る",
            items,
            {"gate": GATE, "verdict": verdict.value, "target": target},
            path,
            f"承認を得たら {cmd}（プランなら run <プラン> --from {plan_step}）",
        )
    )


def manual_gate(n, info) -> None:
    """PR 本文の「手動確認」の節に印の無いマージ前の行があれば、承認ゲート（終了コード 10）で止める（#1485 の決定 5）。
    本文は merge_gate が宛先と 1 回で読んだもの。読めなければ何もしない（承認ゲート 2 の判定だけを行う）。"""
    rows = manual_checks.unchecked_before_merge((info or {}).get("body") or "")
    if not rows:
        return
    emit(
        result(
            TOOL,
            "gate",
            f"#{n} の手動確認（マージ前）に確認の済んでいない行が {len(rows)} 行ある: " + " / ".join(rows),
            [{"kind": "manual", "name": r, "result": "unchecked"} for r in rows],
            {"gate": "manual-check", "unchecked": len(rows)},
            None,
            f"確かめたら #{n} の本文の「手動確認」の行に印（[x]）を付け、run <プラン> --from merge-gate で続ける",
        )
    )


def merge_gate(a):
    """宛先へのマージが承認ゲート 2 に当たるかを判定する（gh を呼ぶのは --pr を渡したときの 1 回だけ）。
    その前に、--pr の本文に未確認のマージ前の手動確認の行があれば承認ゲートで止める。"""
    root = git_root(a.root)
    if not a.base and not a.pr:
        raise StepError("merge-gate には --base か --pr が要る", 2)
    info = None
    if a.pr:  # 本文（手動確認）と宛先を 1 回の gh pr view で読む
        try:
            info = pr_state(root, a.pr, extra=("body",))
        except StepError:
            if not a.base:
                raise
        manual_gate(a.pr, info)
    base = a.base or (info or {}).get("baseRefName") or ""
    verdict = delivery.judge_target(delivery.load_delivery(root), base)
    if verdict.stops:
        info = info or (pr_state(root, a.pr) if a.pr else None)
        gate_stop(a.pr, verdict, info)
    emit(
        result(
            TOOL,
            "ok",
            f"宛先 {base} へのマージは承認ゲート 2 に当たらない（{verdict.reason}）",
            verdict.items,
            {"verdict": verdict.value, "target": base},
        )
    )


# --- merge-when-green ---------------------------------------------------------


def merge_when_green(a, cleanup, next_cmd=None, plan_step="merge-approved"):
    root = git_root(a.root)
    n = a.pr
    decl = delivery.load_delivery(root)  # 判定は保存しない。呼ぶたびに宣言から作り直す
    watch = GreenWatch(root, a)
    judged = {}

    def gate(info):
        # CI を待つ前・draft を外す前に、最初の読みの宛先で判定する（gh の呼び出しを足さない）
        verdict = delivery.judge_target(decl, info.get("baseRefName") or "")
        judged["verdict"] = verdict
        if verdict.stops and not a.gate_approved:
            gate_stop(n, verdict, info, next_cmd, plan_step)

    watch.on_open = gate
    watch.wait()
    items, waits, queued_runs = watch.items, watch.waits, watch.queued_runs
    verdict = judged.get("verdict")
    gate_metrics = {"verdict": verdict.value, "target": verdict.target} if verdict else {}

    if not any(i["kind"] == "pr" and i["result"] == "already_merged" for i in items):
        pin = ["--match-head-commit", watch.last_sha] if watch.last_sha else []  # 緑を確かめた先頭だけをマージする
        p = gh_parts.gh(["pr", "merge", str(n), "--admin", f"--{a.method}", *pin], cwd=root)
        if p.returncode != 0:
            emit(
                result(
                    TOOL,
                    "stopped",
                    f"gh pr merge --admin が失敗: {p.stderr.strip()[:300]}",
                    items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": p.stderr.strip()[:300]}],
                    {"waits": waits},
                )
            )
        merged = {"kind": "pr", "name": f"#{n}", "result": "merged", "method": a.method, "head": watch.last_sha}
        if a.gate_approved:
            merged["gate_approved"] = a.gate_approved  # 誰が承認ゲート 2 を通したか（監査で辿る。#1336 の I8）
        items.append(merged)

    if a.no_cleanup:
        emit(
            result(
                TOOL,
                "ok",
                f"#{n} をマージした（後片付けは行わない）",
                items,
                {"waits": waits, "queued_runs": queued_runs, **gate_metrics},
            )
        )
    status, summary, citems, metrics, path, nxt = cleanup(root, [n])
    metrics = {**metrics, "waits": waits, "queued_runs": queued_runs, **gate_metrics}
    emit(result(TOOL, status, f"#{n} をマージした。{summary}", items + citems, metrics, path, nxt))


# --- promote（昇格の Pull Request。#1336） ------------------------------------


def _open_promote_pr(root, head, base):
    """開いた昇格の Pull Request（head → base）の番号。無ければ None。"""
    p = gh_parts.gh(["pr", "list", "--head", head, "--base", base, "--state", "open", "--json", "number"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"gh pr list が失敗: {p.stderr.strip()[:300]}")
    try:
        prs = json.loads(p.stdout or "[]")
    except ValueError:
        prs = []
    return prs[0]["number"] if prs else None


def _find_or_create_promote_pr(root, head, base):
    """昇格の Pull Request を探し、無ければ作る。(番号, 作ったか) を返す。昇格する変更が無ければ ok で終える。"""
    n = _open_promote_pr(root, head, base)
    if n is not None:
        return n, False
    title = f"昇格: {head} → {base}"
    body = f"{head} の変更を本番チャネル {base} へ入れる（merged-steps.py promote が作った）。マージは承認ゲート 2 の後に行う。"
    p = gh_parts.gh(["pr", "create", "--head", head, "--base", base, "--title", title, "--body", body], cwd=root)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        if "No commits between" in err:
            emit(result(TOOL, "ok", f"{head} から {base} へ昇格する変更が無い", [], {"pr": None, "target": base}))
        raise StepError(f"gh pr create が失敗: {err[:300]}")
    return int((p.stdout or "").strip().rstrip("/").rsplit("/", 1)[-1]), True


def _prepare_promote(root, a, n, item):
    """--prepare: 判定と承認資料を書き、昇格の Pull Request を用意した結果を出して終える。"""
    info = pr_state(root, n)
    verdict = delivery.judge_target(delivery.load_delivery(root), info.get("baseRefName") or a.base)
    path = approval_present(
        TOOL,
        f"promote-{n}",
        title=f"#{n} の昇格（{a.head} → {a.base}。承認ゲート 2）",
        targets=[{"url": info.get("url") or f"#{n}", "title": info.get("title"), "base_head": f"{a.base} ← {a.head}"}],
        change=_change_text(info),
        judge=_judge_rows(verdict),
        consent=[f"#{n} を {a.base} へマージし、本番系へ反映する"],
        rollback=GATE_ROLLBACK,
        path=a.out,
    )
    emit(
        result(
            TOOL,
            "ok",
            f"昇格の Pull Request #{n} を用意した（{a.head} → {a.base}）",
            verdict.items + [item],
            {"pr": n, "verdict": verdict.value, "target": a.base},
            path,
        )
    )


def promote(a, cleanup):
    """ベースブランチ（--head）から本番チャネル（--base）への昇格の Pull Request を作り（あれば使い）、承認ゲート 2 の後にマージする。
    head はベースブランチなので、マージの後に後片付けをしない（#1336 の I7）。"""
    root = git_root(a.root)
    n, created = _find_or_create_promote_pr(root, a.head, a.base)
    item = {"kind": "pr", "name": f"#{n}", "result": "created" if created else "found", "base": a.base, "head_branch": a.head}
    if a.prepare:
        _prepare_promote(root, a, n, item)
    a.pr, a.no_cleanup = n, True
    merge_when_green(a, cleanup, next_cmd=f"promote --head {a.head} --base {a.base} --gate-approved user", plan_step="promote-approved")
