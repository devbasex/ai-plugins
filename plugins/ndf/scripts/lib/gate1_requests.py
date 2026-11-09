"""設計の承認の依頼の部品（#2510）: 設計 PR の先頭の読み取り・取り込みのコミットだけかの判定・前のループの待ちの読み出しと依頼の形。

レーンの問い直し・レーンの答えの当て方・耐久ワークフローの前のループへの当て方が、同じ関数を呼ぶ。
前のループの記録へ書くのは `loop answer` だけで、ここは記録を直接書かない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import design_pr_merge
import jsonio
import step_read
from sprint_loop_names import LOOP_DIR

SPRINT_STATE = Path(__file__).resolve().parents[1] / "sprint-state.py"
HEAD_LEN = 12  # 依頼の id に入れる SHA の桁


def design_pr_head(run, main: str | Path, pr: int) -> str:
    """設計 PR の先頭の SHA。読めなければ空。"""
    code, so, _ = run(["gh", "pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid"], cwd=str(main))
    return so.strip() if code == 0 else ""


def design_head(env, w: dict) -> str:
    """人の判断待ちの行の設計 PR の先頭の SHA。"""
    return design_pr_head(env.run, env.main_dir(), w["pr"])


def carried_over(env, w: dict, head: str | None = None) -> bool:
    """今の先端が行の承認した先端と同じか、取り込みのコミットだけの違いなら、行の先端を今の先端へ書き換えて True（承認を引き継ぐ）。"""
    head, stamp = head if head is not None else design_head(env, w), w.get("stamp") or ""
    if head and head == stamp:
        return True
    if not head or merge_only(env.run, env.main_dir(), int(w["pr"]), w["number"], stamp, head) is not True:
        return False
    w["stamp"] = head
    return True


def lane_unmatched(env, rid: str, reason: str) -> None:
    """当たらなかった答えをオーガナイザーへ知らせる。レーンでない・送れなければ何もしない（答えは耐久の記録に残る）。"""
    try:
        import parallel_lane

        got = parallel_lane.lane_of(env.main_dir())
        if got:
            parallel_lane.send_unmatched(parallel_lane.home_of(got, env.main_dir()), got[0], got[1].name, rid, reason)
    except Exception:  # noqa: BLE001 — 知らせを送れなくてもループの 1 回は止めない
        return


def answer_before(env, w: dict, answers: dict) -> dict | None:
    """問い直した行の、前の依頼の id への答えを判定する。その印から今の先端まで取り込みのコミットだけなら答えを返し、
    違えば当たらなかった答えとして知らせる。どちらでも要素を判定済みにする。先端を読めなければ次の境で読み直す。"""
    for b in w.get("requests_before") or []:
        a = (answers.get(b["id"]) or {}).get("answer")
        if b.get("settled") or not isinstance(a, dict):
            continue
        head = design_head(env, w)
        if not head:
            return None
        b["settled"] = True
        if merge_only(env.run, env.main_dir(), int(w["pr"]), w["number"], b.get("stamp") or "", head) is True:
            return a
        lane_unmatched(env, b["id"], f"前の依頼の先端から設計 PR #{w['pr']} が取り込み以外で動いたため、答えを当てない")
    return None


def merge_only(run, main: str | Path, pr: int, issue: int, old: str, new: str) -> bool | None:
    """`old` から `new` までが起点ブランチの取り込みのコミットだけで、設計文書と要求のコピーの中身が同じか。
    True = 取り込みだけ、False = 違う、None = 判定できない。"""
    import repo

    if not (old and new and old.strip("0")):
        return None
    base = repo.base_branch(main)
    if not base:
        return None
    cwd = str(main)
    if run(["git", "fetch", "-q", "origin", base, f"refs/pull/{pr}/head"], cwd=cwd)[0] != 0:
        return None
    code, so, _ = run(["git", "rev-list", "--first-parent", "--parents", f"{old}..{new}"], cwd=cwd)
    if code != 0:
        return None
    rows = [line.split() for line in so.splitlines() if line.strip()]
    if not rows or any(len(r) < 3 for r in rows) or rows[-1][1] != old:  # 問いの時点の先頭から続かない履歴は取り込みでない
        return False
    for parent in (p for r in rows for p in r[2:]):
        code = run(["git", "merge-base", "--is-ancestor", parent, f"origin/{base}"], cwd=cwd)[0]
        if code != 0:
            return False if code == 1 else None
    code = run(["git", "diff", "--quiet", old, new, "--", f"issues/issue-{issue}-*.md"], cwd=cwd)[0]
    return {0: True, 1: False}.get(code)


# ---------------------------------------------------------------- 前のループの待ち


def prior_id(loop: str, issue: int, lap: int, head: str = "", scope: str = "") -> str:
    """前のループの待ちの依頼の id。行から決まり、別のリポジトリのループは `scope`（`repo_scope`）を、問い直すときだけ新しい先頭の
    SHA を足す（別のリポジトリの同じ名前のループ・課題・回数と id を分ける。#2733）。"""
    return f"{loop}-w{issue}-l{lap}" + (f"-r{scope}" if scope else "") + (f"-{head[:HEAD_LEN]}" if head else "")


def repo_scope(main: str | Path, home: str | Path | None) -> str:
    """ループの記録を読むリポジトリ `main` の識別。計画の置き場 `home`（省けば `main`）と同じなら空（従来の id のまま）。"""
    import hashlib

    from parallel_files import resolved_path

    got = resolved_path(Path(main))
    return "" if home is None or got == resolved_path(Path(home)) else hashlib.sha1(got.encode()).hexdigest()[:8]


def _other_plan_running(main: Path, w: dict) -> bool:
    import parallel_lane as pl

    if not (w.get("request") and w.get("plan")):
        return False
    try:
        return pl.run_status(main, w["plan"]) in pl.RUNNING
    except Exception:  # noqa: BLE001 — 計画の状態を読めなければ動いていないものとして問いへ出す（埋もれさせない）
        return False


def prior_rows(main: str | Path, name: str, home: str | Path | None = None) -> tuple[list[tuple[str, dict]], list[dict]]:
    """計画のレーンでないループの、答えがまだで設計 PR を持つ行の (ループの名前, 行) と、読めなかったループ。
    ループの記録は `main`（レーンのリポジトリ）から、計画とその状態は `home`（計画の置き場。省けば `main`）から読む（#2733）。"""
    import parallel_plan as pp

    main = Path(main)
    home = Path(home) if home is not None else main
    try:
        plan = pp.load_valid_plan(home, name)
    except (OSError, ValueError) as e:
        return [], [{"loop": name, "reason": f"計画を読めない: {e}"}]
    here = pp.resolved_path(main)  # 現役のループは、レーンのリポジトリが探す先 `main` と同じレーンだけ（別のリポジトリの同名は旧ループ）
    lanes = {ln.input.loop for ln in plan.lanes if ln.input.kind == "loop" and pp.resolved_path(ln.repo_dir(home)) == here}
    rows, skipped = [], []
    for p in sorted((main / LOOP_DIR).glob("*/loop.json")):
        loop = p.parent.name
        if loop in lanes:
            continue
        try:
            rec = jsonio.read(p, want=dict)
        except (OSError, ValueError, jsonio.JsonReadError) as e:
            skipped.append({"loop": loop, "reason": str(e)[:200]})
            continue
        rows += [(loop, w) for w in rec.get("waiting") or [] if not w.get("answer") and w.get("pr") and not _other_plan_running(home, w)]
    return rows, skipped


def prior_request(loop: str, w: dict, stamp: str, at: str, via: str | None = None, scope: str = "") -> dict:
    """問いの一覧へ送る前のループの待ち（`kind: gate1`・`loop`。`lane` を持たない）。`via` は送ったレーンの名前で、別のリポジトリの
    ループの記録・設計 PR をそのレーンのリポジトリから引くために持たせる（#2733。計画の置き場で始めるときに送る分は持たない）。
    `scope` はループのリポジトリの識別（`repo_scope`）で、id と同じ行の置き換え（`replace_same`）をリポジトリごとに分ける。"""
    issue, pr, lap = w["number"], w["pr"], w.get("lap") or 0
    return ({"via": via} if via else {}) | ({"scope": scope} if scope else {}) | {
        "kind": "gate1", "id": prior_id(loop, issue, lap, scope=scope), "loop": loop, "issue": issue, "pr": pr, "lap": lap, "stamp": stamp,
        "verdict": w.get("verdict"), "reasons": [w["reason"]] if w.get("reason") else [], "report": w.get("report") or "",
        "question": f"開発ループ {loop} の #{issue} の設計 PR #{pr} が承認待ちのまま残っている。承認 / 直して出し直す / やらない を答える",
        "at": at,
    }  # fmt: skip


def prior_requests(
    main: str | Path, name: str, at: str, run=design_pr_merge.run_cmd, home: str | Path | None = None, via: str | None = None
) -> tuple[list[dict], list[dict]]:
    """前のループの待ちの依頼と、読めなかったループ。印は送る時点の設計 PR の先頭（読めなければ行の印）。`home` は `prior_rows`、
    `via` は `prior_request`。"""
    rows, skipped = prior_rows(main, name, home)
    scope = repo_scope(main, home)
    return [prior_request(loop, w, design_pr_head(run, main, w["pr"]) or w.get("stamp") or "", at, via, scope) for loop, w in rows], skipped


def lane_plan(env) -> str | None:
    """自分が属する動いている並列の計画の名前。レーンでない・外のオーガナイザーのレーンなら None（問いを依頼にしない）。"""
    try:
        import parallel_external as pe
        import parallel_lane

        got = parallel_lane.lane_of(env.main_dir())
    except Exception:  # noqa: BLE001 — 計画を読めないときはレーンでないものとして扱う
        return None
    return got[0] if got and not pe.is_external(got[0]) else None


def lane_sends(env, rec: dict, send_gate1) -> list[dict]:
    """レーンのループの 1 回の境: 自分の行で依頼の id を持たないもの（送れなかった依頼）を `send_gate1` で送り直し、前のループの待ちを
    送る（#2510 の受け入れ条件 12・13）。読めずに飛ばしたループを結果の項目の形で返す。"""
    plan = lane_plan(env)
    if plan is None:
        return []
    for w in rec.get("waiting", []):
        if w.get("pr") and not w.get("answer") and not w.get("request"):
            if sent := send_gate1(env, w, w.get("report") or "", w.get("state") or ""):
                w.update(sent)
    import parallel_lane

    main = env.main_dir()
    home = parallel_lane.plan_home(main, plan)
    msgs, skipped = prior_requests(
        main, plan, env.now(), env.run, home, os.environ.get("NDF_RELAY_NAME")
    )  # レーンの名前（`lane_of` と同じ）
    try:
        for msg in msgs:
            parallel_lane.send_prior(home, plan, msg)
    except Exception as e:  # noqa: BLE001 — 送れなければ次の境で送り直す（ループの 1 回は止めない）
        skipped = [*skipped, {"loop": msg["loop"], "reason": f"依頼を送れない: {e}"[:200]}]
    return [{"skipped_loop": s["loop"], "reason": s["reason"]} for s in skipped]


def prior_issues(env) -> dict[int, str]:
    """レーンなら、前のループの答えがまだの行を持つ課題（課題 → ループの名前）。レーンでなければ空。"""
    plan = lane_plan(env)
    if not plan:
        return {}
    import parallel_lane

    main = env.main_dir()
    return {w["number"]: loop for loop, w in prior_rows(main, plan, parallel_lane.plan_home(main, plan))[0]}


def _loop_answer(run, main: Path, q: dict, args: list[str], expect: str | None = None) -> str | None:
    """そのループの `loop answer` を打つ。当たらなかった理由を返す（当たれば None）。先頭は呼び出し側が確かめたので、行の印が古く
    答え直しを求められたら（`status: regated`。印は今の先頭へ書き換わる）1 回だけ打ち直す。`expect` は呼び出し側が確かめた先頭で、
    打ち直す前に今の先頭がそれと同じかを確かめ直す（確かめた後に動いた先頭を、人の答えなしに承認しないため）。"""
    cmd = [sys.executable, str(SPRINT_STATE), "loop", "answer", q["loop"], str(q["issue"]), *args]
    for attempt in range(2):
        if attempt and expect and (now := design_pr_head(run, main, q["pr"])) != expect:
            return f"設計 PR #{q['pr']} の先頭が確かめた {expect[:HEAD_LEN]} から {now[:HEAD_LEN] or '（読めない）'} へ動いた。新しい先頭で答え直す"
        code, so, se = run(cmd, cwd=str(main))
        if code != 0:
            return f"loop answer が断った（{code}）: {(se or so).strip()[:200]}"
        status = ((step_read.result_line(so) or {}).get("metrics") or {}).get("status")
        if status != "regated":
            return None
    return "loop answer が新しい先頭での答え直しを求めた"


def answer_prior(main: str | Path, plan_dir: Path, q: dict, answer: dict, stamp: str | None = None, run=design_pr_merge.run_cmd) -> dict:
    """前のループの待ちの問いへの答えを、そのループの記録へ当てる。`stamp` は置き換えた問いへ答えたときのその問いの印。
    当たれば {}、問い直すなら {"reask": 新しい問い}、当てられなければ {"unmatched": 理由} を返す。"""
    main, result = Path(main), answer.get("result")
    moved = head = None
    if stamp is not None or result == "approved":
        head, old = design_pr_head(run, main, q["pr"]), stamp if stamp is not None else q.get("stamp") or ""
        if not head:
            return {"unmatched": f"設計 PR #{q['pr']} の先頭を読めない"}
        same = head == old or merge_only(run, main, q["pr"], q["issue"], old, head) is True
        moved = None if same else head
    if moved and stamp is not None:
        return {"unmatched": f"置き換えた問いの先頭 {stamp[:HEAD_LEN]} から設計 PR が取り込み以外で動いた"}
    if moved:
        return {"reask": {**q, "id": prior_id(q["loop"], q["issue"], q["lap"], moved, q.get("scope") or ""), "stamp": moved}}
    if result == "approved":
        args = ["--approved", "--pr", str(q["pr"])]
    elif result == "revise":
        (Path(plan_dir) / "prior").mkdir(parents=True, exist_ok=True)
        (Path(plan_dir) / "prior" / f"revise-{q['id']}.md").write_text(answer.get("note") or "", encoding="utf-8")
        args = ["--revise", "--note", str(Path(plan_dir) / "prior" / f"revise-{q['id']}.md")]
    elif result == "rejected":
        args = ["--rejected"]
    else:
        return {"unmatched": f"答え {result} は前のループの待ちへ当てられない"}
    why = _loop_answer(run, main, q, args, head)
    return {"unmatched": why} if why else {}
