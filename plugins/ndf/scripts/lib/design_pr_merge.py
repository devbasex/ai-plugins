"""承認した設計のマージ（#2507）。設計の承認の答えと周回の境が、承認した設計 PR を宛先へ待たずに 1 回だけマージしてみる。

呼ぶのは `parallel.py answer --approved`（並列の計画）・`sprint-state.py loop answer --approved`（1 本のループ）・周回の境
（`sprint_loop_select.settle_approved_designs`）の 3 か所で、どれもこの関数だけを使う（設計 #2507 の決定 2）。

- 承認した先端（問いの時点の設計 PR の先頭のコミット）のままのときだけマージする（`merge-when-green --expect-head`）
- 宛先が本番チャネルに当たるか決められないときはマージしない（リリース承認ゲートを設計承認ゲートの答えで通さない。決定 3）
- 必須の検査が緑のときだけマージする。検査の完了は待たない（`probe` で分類し、`passed` と `infra` のときだけ呼ぶ。決定 1）
- 後片付けは持ち主の周回の境が打つ（`--no-cleanup`。決定 5）
- 例外を投げず、終了コードを持たない。結果は「マージの結果」の 1 つ（`RESULTS`）にして返す
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import ci_workflows
import clock
import delivery

MERGED_STEPS = Path(__file__).resolve().parents[1] / "merged-steps.py"
RESULTS = ("merged", "already_merged", "head_moved", "no_stamp", "not_green", "conflict", "production", "failed")
MERGED_RESULTS = ("merged", "already_merged")  # マージ済みに当たる結果
# 答えの待ちの上限（非機能の実現方式）。緑なら 1 回目の読みから 5 秒後の確かめ直しでマージし、10 秒を過ぎた最初の読みで切れる
WAIT_ARGS = ["--timeout", "10", "--interval", "5", "--recheck", "5", "--no-checks-after", "5"]
INFRA_RERUNS = 3  # 同じ承認した先端で基盤待ちのために再実行する回数の上限（merge-when-green の --infra-reruns の既定と同じ）
UNREGISTERED = "unregistered"  # 必須の検査がまだ検査の一覧に載っていない・検査が無いと確かめられない

Run = Callable[..., tuple]


def run_cmd(cmd: list[str], cwd: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    """コマンドを起動して (終了コード, 標準出力, 標準エラー) を返す。周回のコマンド（`sprint_loop_record`）もこれを使う。"""
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        try:
            got = json.loads(line)
        except ValueError:
            continue
        if isinstance(got, dict):
            return got
    return {}


def _res(result: str, pr: int, head: str = "", reason: str = "", cls: str | None = None, **extra) -> dict:
    return {"result": result, "class": cls, "reason": reason, "pr": pr, "head": head, "at": clock.now_iso("utc"), **extra}


def _check_names(rollup: list) -> set[str]:
    return {str(c.get("name") or c.get("context") or "") for c in rollup or [] if isinstance(c, dict)} - {""}


def _checks_registered(root: str, base: str, stamp: str, rollup: list, run: Run) -> str | None:
    """必須の検査が検査の一覧に載っているか（検査が無いなら、無いこと）を確かめる。確かめられなければ理由を返す（手順 3）。"""
    code, so, se = run(["gh", "api", f"repos/{{owner}}/{{repo}}/rules/branches/{base}"], cwd=root)
    try:
        required = ci_workflows.required_contexts(json.loads(so)) if code == 0 else None
    except ValueError:
        code = 1
    if code != 0:
        return f"宛先 {base} の必須の検査の規則を読めない: {(se or so).strip()[:200]}"
    missing = sorted(set(required or []) - _check_names(rollup))
    if missing:
        return f"必須の検査がまだ検査の一覧に無い: {', '.join(missing)}"
    if rollup:
        return None
    if required is not None:
        return "検査の一覧が空（必須の検査がある）"
    code, so, se = run(["gh", "api", f"repos/{{owner}}/{{repo}}/contents/{ci_workflows.WORKFLOW_DIR}?ref={stamp}"], cwd=root)
    if code == 0:
        return "検査の一覧が空（ワークフローがある）"
    if "404" in (se or so) or "Not Found" in (se or so):
        return None  # 必須の検査もワークフローのディレクトリも無い: 検査が無いと確かめた
    return f"ワークフローの有無を読めない: {(se or so).strip()[:200]}"


def merge_approved(root, pr: int, stamp: str | None, run: Run | None = None, infra_budget: int = INFRA_RERUNS) -> dict:
    """設計 PR `pr` を、承認した先端 `stamp` のままで検査が緑なら宛先へマージする。結果は `RESULTS` の値の 1 つ。

    出力: {result, class, reason, pr, head, at, branch, infra_rerun}。`infra_rerun` は基盤待ちのジョブを実際に再実行した
    回数（merge-when-green の items の `infra_rerun` の数）。周回の境が同じ承認した先端での累計に足す。
    `infra_budget` は再実行の残りの回数で、merge-when-green の `--infra-reruns` に渡す。0 以下で基盤待ちなら
    merge-when-green を呼ばずに not_green（infra）を返す。"""
    run = run or run_cmd
    root = str(root)
    try:
        return _try_merge(root, int(pr), (stamp or "").strip(), run, int(infra_budget))
    except Exception as e:  # noqa: BLE001 — 例外を投げない（I4）。読めない出力も failed に写す
        return _res("failed", int(pr), reason=f"マージを試せない: {e}"[:300])


def _try_merge(root: str, pr: int, stamp: str, run: Run, budget: int) -> dict:
    code, so, se = run(["gh", "pr", "view", str(pr), "--json", "state,headRefOid,baseRefName,headRefName,statusCheckRollup"], cwd=root)
    try:
        info = json.loads(so) if code == 0 else None
    except ValueError:
        info = None
    if not isinstance(info, dict) or not info.get("state"):
        return _res("failed", pr, reason=f"設計 PR を読めない: {(se or so).strip()[:200]}")
    head, base, branch = info.get("headRefOid") or "", info.get("baseRefName") or "", info.get("headRefName") or ""
    ctx = {"branch": branch, "infra_rerun": 0}
    if info["state"] == "MERGED":
        return _res("already_merged", pr, head, **ctx)
    if info["state"] != "OPEN":
        return _res("failed", pr, head, f"設計 PR が開いていない（{info['state']}）", **ctx)
    if not stamp.strip("0"):
        return _res("no_stamp", pr, head, "承認した先端が記録に無い", **ctx)
    if head != stamp:
        return _res("head_moved", pr, head, f"承認の後に設計 PR の先端が動いた（{stamp[:12]} → {head[:12]}）", **ctx)
    verdict = delivery.judge_target(delivery.load_delivery(root), base)
    if verdict.stops:
        return _res("production", pr, head, f"設計 PR の宛先 {base} が本番チャネルに当たるか決められない（{verdict.reason}）", **ctx)
    if why := _checks_registered(root, base, stamp, info.get("statusCheckRollup") or [], run):
        return _res("not_green", pr, head, why, UNREGISTERED, **ctx)
    code, so, se = run([sys.executable, str(MERGED_STEPS), "probe", "--pr", str(pr), "--act"], cwd=root)
    cls = (_last_json(so).get("metrics") or {}).get("class")
    if code != 0 or not cls:
        return _res("failed", pr, head, f"検査を分類できない（probe {code}）: {(se or so).strip()[:200]}", **ctx)
    if cls not in ("passed", "infra"):
        return _res("not_green", pr, head, f"検査が緑でない（{cls}）", cls, **ctx)
    argv = [sys.executable, str(MERGED_STEPS), "merge-when-green", str(pr), "--expect-head", stamp, "--no-cleanup", *WAIT_ARGS]
    if cls == "infra":
        if budget <= 0:
            return _res("not_green", pr, head, f"CI の基盤待ちが再実行 {INFRA_RERUNS} 回で解けない", cls, **ctx)
        argv += ["--infra-gap", "0", "--infra-reruns", str(budget)]
    code, so, se = run(argv, cwd=root)
    got = _last_json(so)
    ctx["infra_rerun"] = sum(1 for i in got.get("items") or [] if isinstance(i, dict) and i.get("result") == "infra_rerun")
    return _from_merge(pr, head, cls, code, got, se, ctx)


def _from_merge(pr: int, head: str, cls: str, code: int, got: dict, se: str, ctx: dict) -> dict:
    """merge-when-green の終了コードと出力を、マージの結果へ写す。"""
    summary = str(got.get("summary") or se or "").strip()[:200]
    items = got.get("items") or []
    if code == 0:
        return _res("merged", pr, head, cls=cls, **ctx)
    if any(isinstance(i, dict) and i.get("result") == "head_moved" for i in items):
        return _res("head_moved", pr, head, f"マージの直前に先端が動いた: {summary}", cls, **ctx)
    if code == 4:
        return _res("conflict", pr, head, f"宛先と衝突している: {summary}", cls, **ctx)
    if code == 10:
        return _res("production", pr, head, f"宛先が本番チャネルに当たる（リリース承認ゲートで止まった）: {summary}", cls, **ctx)
    if code == 75 or (code == 1 and not any(isinstance(i, dict) and i.get("result") == "stopped" for i in items)):
        return _res("not_green", pr, head, f"検査が待ちの上限までに緑にならない（{code}）: {summary}", cls, **ctx)
    return _res("failed", pr, head, f"マージできない（{code}）: {summary}", cls, **ctx)
