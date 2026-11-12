"""並列の計画の 1 スプリントのレーンの目録へ、本番の後のまとめのステージを足す（#3058 の決定 7・8。`sprint.cmd_new_sprint` が呼ぶ）。

| レーンの本番の経路 | まとめのステージのプラン | 版の要るまとめのプラン |
| --- | --- | --- |
| リリースの依頼（同じリポジトリでテンプレートで出す） | 確定仕様化 → Pull Request → 起動の判定 → 振り返り → 棚卸し → マージ → 終わりの記録（`lane_close_plan`） | リリース後テスト → 課題を閉じる（`verify_plan`）。`released` の後に一時レーンが流す |
| レーンの中で本番を流し終える（リリースの Pull Request・マージで届く・別のリポジトリ） | 今のまとめ → 終わりの記録 | 作らない |

依頼の経路では、依頼のプランの承認ゲート（10）を完了へ向け（`on_exit`）、レーンが答えを待たずにまとめへ進む（#3060）。
課題を閉じるステップは版の要るまとめにだけ置く（I8）。開発ループのレーンと外のオーガナイザーのレーンと計画の外のスプリントには足さない。
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from supervise_lib import limits
from supervise_lib.decl import with_decls
from supervise_lib.paths import CHECK_PY, PARALLEL_PY
from supervise_lib.plan import sprint_state_path
from supervise_lib.procedures import sprint_out, with_record
from supervise_lib.release_templates import release_verify_steps
from supervise_lib.sprint_close_steps import _close_step, _retro_step, _spec_pr_steps, deep_due_step, refine_steps
from supervise_lib.sprint_routes import RECORD_FOUND
from supervise_lib.verify_steps import merge_steps

STAGE = "まとめ"
CLOSE_KEY = "lane-close"  # まとめのステージのプランの名（版の要るまとめの close が進行の記録を `{queue_state:lane-close}` で読む）
VERIFY_FILE = "lane-verify.json"  # 版の要るまとめのプラン（目録と同じ置き場。ステージには入れない）
REQUEST_KEY = "release-request"  # 依頼のプランの名（`sprint.prod_plans`）


def sprint_lane(repo: str) -> tuple[str, str] | None:
    """動いている並列の計画の 1 スプリントのレーンなら (計画名, レーン名)。別のリポジトリのレーンも含む。開発ループのレーンと
    外のオーガナイザーのレーン（依頼と答えを置き場のファイルで受け渡す）は None。"""
    import decl
    import parallel_external as pe
    import parallel_lane as pl

    got = pl.lane_of(decl.main_dir(repo) or Path(repo))
    if not got or got[0] == pe.EXTERNAL or getattr(got[1].input, "kind", None) != "sprint":
        return None
    return got[0], got[1].name


def lane_end_step(plan: str, lane: str, request: bool) -> dict:
    """終わりの記録（`parallel.py end`）。依頼の経路では依頼の記録を渡し、答えが差し戻しか失敗なら承認ゲート（10）で止まる（決定 6）。"""
    cmd = f"{PARALLEL_PY} end {shlex.quote(plan)} --lane {shlex.quote(lane)}"
    if request:
        cmd += f" --request {{queue_state:{REQUEST_KEY}}}/work/request.txt"
    return {"id": "lane-end", "type": "run", "timeout": 120, "cmd": cmd, "next": "end"}


def _close_head(a, repo: str) -> dict:
    branch = f"spec/{a.name}"
    return {"フェーズ": "まとめ", "課題": a.issue, "モード": a.mode, "作業場所": f"{repo}/.worktrees/{branch}", "branch": branch,
            "起点": f"origin/{a.base}", "リポジトリ": repo, "規則": "", "進め方": "fast"}  # fmt: skip


def lane_close_plan(a, repo: str, lane: tuple[str, str]) -> dict:
    """版の要らないまとめ: 確定仕様化 → Pull Request → 起動の判定 →（起動する回だけ）振り返り → 棚卸し → マージ → 終わりの記録。"""
    issues, refs = ",".join(map(str, a.issue)), " ".join(f"#{i}" for i in a.issue)
    steps = [
        *_spec_pr_steps(a, refs, "deep-due"),
        deep_due_step(a, repo, issues, after="merge-gate"),
        _retro_step(a, refs, f"{CHECK_PY} stats --root {shlex.quote(repo)}", repo),
        *refine_steps(a, refs, repo, issues, after="merge-gate"),
        *merge_steps(a, next="lane-end"),
        lane_end_step(*lane, request=True),
    ]
    return with_record(with_decls({**_close_head(a, repo), "steps": steps}, a))


def verify_plan(a, repo: str) -> dict:
    """版の要るまとめ: リリース後テスト（控えを取る・比べるを含む）→ 課題を閉じる。最終工程はまとめのプランの進行の記録で選ぶ。"""
    issues, refs = ",".join(map(str, a.issue)), " ".join(f"#{i}" for i in a.issue)
    close = _close_step(a, repo, RECORD_FOUND, issues)
    close["cmd"] = close["cmd"].replace("{state_dir}/progress.jsonl", f"{{queue_state:{CLOSE_KEY}}}/progress.jsonl")
    steps = [*release_verify_steps(a.name, refs, RECORD_FOUND, after="close"), close]
    plan = {
        "フェーズ": "リリース後テスト",
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": repo,
        "リポジトリ": repo,
        "規則": "",
        "steps": steps,
    }
    return with_record(with_decls(plan, a))


def closes_issues(plan: dict) -> bool:
    """課題を閉じるステップ（`close`・sprint-close.py）を持つか（I8 の検査）。"""
    return any(s.get("id") == "close" or "sprint-close.py" in str(s.get("cmd") or "") for s in plan.get("steps") or [])


def _write_verify(a, repo: str) -> Path:
    plan = verify_plan(a, repo)
    if getattr(a, "state", None):
        plan["スプリント状態"] = sprint_state_path(a)
    if bad := limits.fill_all([plan]):
        raise ValueError(f"版の要るまとめのプランを組めない: {bad}")
    out = sprint_out(a).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / VERIFY_FILE).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return out / VERIFY_FILE


def with_lane_close(a, waves: list[dict]) -> list[dict]:
    """1 スプリントのレーンの目録なら、本番の後にまとめのステージを足す（A7）。まとめは最後のプランのステージの queue へ
    `--then` でつなぐ。最後のステージが承認ゲートか手で流すステージなら、まとめを自分の command で流す。"""
    lane = sprint_lane(a.worktree)
    if lane is None:
        return waves
    repo = str(Path(a.worktree).resolve())
    prod = next((w for w in waves if w.get("name") == "本番"), {})
    request = (prod.get("plans") or {}).get(REQUEST_KEY)
    if request:
        close = lane_close_plan(a, repo, lane)
        step = request["steps"][0]
        step["cmd"] += f" --verify-plan {shlex.quote(str(_write_verify(a, repo)))}"
        step["on_exit"] = {**step.get("on_exit", {}), "10": "end"}  # 依頼を送ったら答えを待たずにまとめへ進む（決定 7）
    else:
        from supervise_lib.sprint import close_plan

        close = close_plan(a, repo)
        close["steps"] = [{**s, "next": "lane-end"} if s.get("id") == "close" else s for s in close["steps"]]
        close["steps"].append(lane_end_step(*lane, request=False))
    if request and closes_issues(close):
        raise ValueError("版の要らないまとめのプランに課題を閉じるステップがある（I8）")
    last = waves[-1] if waves else {}
    then = {"then_of": last.get("then_of") or last["name"]} if last.get("plans") else {}
    return [*waves, {"name": STAGE, "plans": {CLOSE_KEY: close}, **then}]
