"""sprint_loop_table.py: 工程の進行表で流した開発ループの 1 回の締めと、答え待ちの扱い（#3092）。`loop lap`・`loop finish` が呼ぶ。LLM を呼ばない。

`progress.py next` の出力（done）の `gate` の項目の種類で、今までの承認ゲートの扱い（目録とキューの done の「関門」）を引き継ぐ:

- `gate`（ドキュメントレビューの承認の待ち）: 設計承認ゲート。課題を人の判断待ちへ移し、進行表を `progress.py cancel` で中止して
  「人へ戻した」で締める。課題ごとの子の行で止まり、残る課題があれば `progress.py scope --drop` で外して続ける。モードの決め直しで
  下がったときは、今の進行表を中止して、下げたモードで `progress.py new` し直す（今の作り直しと同じ振る舞い）
- `release` で依頼を送った（`next_step` が `parallel.py await`）: レーンのリリースの依頼。「リリース待ち」で締め、答えはループの 1 回の境で読む
- `release`（依頼なし）とデプロイの工程の `plan-gate`: リリース承認ゲート。本番を出さずに人へ戻す（`gate2_returned`）
- ほか（答え待ちの停止・ほかの工程のプランの中の承認）: 締めずに同じ 1 行を返す
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import jsonio
import mode_judgment
from sprint_loop_argv import new_sprint_argv, next_pipeline, table_of
from sprint_loop_gate import (
    after_close,
    close_lap,
    failure_key,
    gate_copy,
    last_verdict,
    loop_ok,
    mode_decision,
    pr_numbers,
    refused_stop,
    sync_mode,
    wait_issues,
)
from sprint_loop_record import (
    DESIGN_GATE,
    DONE,
    FAILED,
    PROGRESS,
    RELEASE_GATE,
    RELEASE_PENDING,
    RETURNED,
    Env,
    LoopError,
    NOT_RUN,
    mvv_change_stop,
    resume_command,
    selected_issues,
    stop_and_report,
    update_handoff,
    write_record,
)

DESIGN_GATE_STAGE = "ドキュメントレビュー"  # 設計承認ゲートの承認の待ちを持つ工程（工程の定義 document-review.json）
TABLE_CONTINUATION_STEP = "進行表の続き"  # 設計承認ゲートの後の続き（scope か作り直し）を組めなかったときの refused の step


def progress_cmd(*args: str) -> list[str]:
    return [sys.executable, str(PROGRESS), *args]


def table_output(env: Env, table: str, key: str) -> dict:
    """進行表の行（`<工程>/<アクティビティ>`）の出力。読めなければ空（締めを止めない）。"""
    try:
        import progress_db as pdb
        import progress_model as pm

        store = pdb.connect(pdb.default_url(env.main_dir()))
        with pdb.transaction(store) as conn:
            rev, _ = pdb.definition(conn, table)
            rows = pm.fold(pdb.read_progress_events(conn, table), rev)
    except Exception:  # noqa: BLE001 — 版の写しが読めなくても締めは止めない（版はループの 1 回の値のまま）
        return {}
    r = rows.get(key)
    return ((r.payload or {}).get("output") or {}) if r is not None else {}


def table_versions(env: Env, lap: dict) -> None:
    """締める前に、進行表のデプロイの工程の開発版のプランをループの 1 回へ写す（`closed_versions` が版の記録を読む）。"""
    if not lap.get("table"):
        return
    plan = ((table_output(env, lap["table"], "デプロイ/dev").get("metrics")) or {}).get("plan")
    if plan:
        lap["dev_plan"] = plan


def cancel_table(env: Env, lap: dict, reason: str) -> str | None:
    """ループの 1 回の進行表を中止する。断られたら理由を返す（締めは止めない）。"""
    code, so, se = env.run(progress_cmd("cancel", lap["table"], "--reason", reason), cwd=str(env.main_dir()))
    return None if code == 0 else f"progress.py cancel が {code} で終わった: {(se or so).strip()[:200]}"


def gate_item(rep: dict) -> dict:
    return next((i for i in rep.get("items") or [] if i.get("row")), {})


def lap_by_table(env: Env, rec: dict, before: dict, lap: dict, failed: bool, done: str | None, deferred_issues: list[int]) -> dict:
    """工程の進行表で流したループの 1 回を、`progress.py next` の出力（done）で締める（#3092）。

    完了なら完了、工程の失敗・中止なら失敗で締める。答え待ち（`gate`）は項目の種類で扱いを分ける（このモジュールの説明）。"""
    rep = jsonio.read(done, missing={}, broken={}, want=dict) if done else {}
    status = rep.get("status")
    if status == "gate" and not failed:
        item = gate_item(rep)
        kind = item.get("kind")
        if kind == "gate" and item.get("row", "").split("/")[0].split("#")[0] == DESIGN_GATE_STAGE:
            return table_design_gate(env, rec, before, lap, item)
        if kind == "release" and item.get("next_step"):
            return table_release_pending(env, rec, before, lap, item)
        if kind == "release" or (kind == "plan-gate" and item.get("stage") == "デプロイ"):
            return table_gate2(env, rec, before, lap, item)
        return table_waiting(env, rec, before, lap, rep, Path(done))
    state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
    ok = status == "ok" if done else failure_key(state) is None  # done を渡さない締めは、今までどおりスプリント状態の行で決める
    outcome = DONE if ok and not failed else FAILED
    table_versions(env, lap)
    close_lap(env, rec, lap, outcome, state, deferred_issues)
    rows = [i.get("row") for i in rep.get("items") or [] if i.get("kind") == "failed" and i.get("row")]
    if outcome == FAILED and rows:  # 同じ失敗の判定（failure_streak）が読む鍵: 課題・失敗した工程（理由の文は含めない）
        lap["failure"] = {"issues": selected_issues(lap), "kind": rows[0], "step": "失敗"}
    return after_close(env, rec, before, f"ループの {lap['n']} 回目 を {outcome} で締めた")


def table_waiting(env: Env, rec: dict, before: dict, lap: dict, rep: dict, done: Path) -> dict:
    """締めずに答えを待つ（答え待ちの停止・ほかの工程のプランの中の承認）。答えの後に同じ 1 行を打ち直す。"""
    pipeline = next_pipeline(rec, lap["table"], done)
    write_record(env, rec, before)
    update_handoff(env, rec, f"ループの {lap['n']} 回目 の進行表が答えを待っている", resume_command(rec), pipeline)
    return loop_ok(
        f"ループの {lap['n']} 回目 の進行表が答えを待っている: {rep.get('summary', '')}",
        rec,
        rep.get("items") or [],
        status="gate",
        next_=f"progress.py answer で答えてから: {pipeline}",
        table=lap["table"],
        pipeline=pipeline,
    )


# ---------------------------------------------------------------- 設計承認ゲート


def table_design_gate(env: Env, rec: dict, before: dict, lap: dict, item: dict) -> dict:
    """設計承認ゲート（MVV 判定が従わなかった承認の待ち）: 課題を人の判断待ちへ移す。答えは `loop answer` で受け、承認した課題は
    次のループの 1 回で設計を通さずに選ばれる（今の振る舞い）。"""
    if stop := mvv_change_stop(env, rec):
        return stop_and_report(env, rec, before, stop, "プロジェクト MVV が改訂された（承認ゲートの機械のチェック）")
    issues = lap.get("issues_kept") or selected_issues(lap)
    row = item.get("row", "")
    child = row.split("/")[0].split("#")
    moved = [int(child[1])] if len(child) == 2 and child[1].isdigit() else list(issues)
    verdict = last_verdict(lap["state_path"], "design")
    prs = pr_numbers(verdict.get("pr"))
    notes: list[str] = []
    wait_issues(env, rec, lap, {"report": ""}, verdict, prs[0] if prs else None, moved, notes)
    kept = [n for n in issues if n not in moved]
    lap["issues_kept"] = kept
    if not kept:
        if err := cancel_table(env, lap, f"設計承認ゲートで全課題を人の判断待ちへ移した（{row}）"):
            notes.append(err)
        table_versions(env, lap)
        close_lap(env, rec, lap, RETURNED)
        lap["notes"] = notes
        return after_close(env, rec, before, f"設計承認ゲートで全課題を人の判断待ちへ移した（{' '.join(f'#{n}' for n in moved)}）")
    try:
        pipeline = table_continuation(env, rec, lap, moved, kept, notes)
    except (LoopError, OSError, ValueError) as e:
        return refused_stop(env, rec, before, TABLE_CONTINUATION_STEP, 1, str(e))
    write_record(env, rec, before)
    moved_s, kept_s = " ".join(f"#{n}" for n in moved), " ".join(f"#{n}" for n in kept)
    update_handoff(env, rec, f"設計承認ゲートで {moved_s} を人の判断待ちへ移し、残りで続ける", resume_command(rec), pipeline)
    return loop_ok(
        f"設計承認ゲートで {moved_s} を人の判断待ちへ移した。残り {kept_s} で続ける",
        rec,
        [{"waiting": moved}, *({"note": x} for x in notes)],
        status="continue",
        next_=pipeline,
        gate=1,
        waiting=moved,
        table=lap["table"],
        pipeline=pipeline,
    )


def table_continuation(env: Env, rec: dict, lap: dict, moved: list[int], kept: list[int], notes: list[str]) -> str:
    """設計承認ゲートの後の続き。判定の根拠を持つループの 1 回は残った課題でモードを決め直す（#2923 の I4）。

    同じモードなら `progress.py scope --drop` で外した課題を除いて流し直す。下がったときは、進行表はモードを下げられない（I5）ため、
    今の進行表を `progress.py cancel` で中止し、残った課題と下げたモードで `progress.py new` し直す（今の作り直しの振る舞い）。
    記録のモードと決め直しの記録は、組み直しが通ってから書く。断れば LoopError。"""
    current = lap.get("mode") or "standard"
    mode = current
    if lap.get("judgments") is not None:
        try:
            mode = mode_judgment.redecide(lap["judgments"], kept, current)
        except mode_judgment.JudgmentError as e:
            raise LoopError(f"モードを決め直せない: {e}") from e
    root = str(env.main_dir())
    k = len(lap.get("dones", [])) + 1
    reason = f"設計承認ゲートで {' '.join(f'#{n}' for n in moved)} を人の判断待ちへ移した"
    if mode == current:
        code, so, se = env.run(progress_cmd("scope", lap["table"], "--drop", *map(str, moved), "--reason", reason), cwd=root)
        if code != 0:
            raise LoopError(f"progress.py scope が {code} で断った: {(se or so).strip()[:300]}")
    else:
        if err := cancel_table(env, lap, f"{reason}。モードを {current} から {mode} へ下げて作り直す"):
            raise LoopError(err)
        redo = {**lap, "mode": mode, "sprint": f"{lap['sprint']}-r{k}", "issues_kept": kept, "designed": True}
        cmd = new_sprint_argv(root, rec, redo, [], issues=kept)
        code, so, se = env.run(cmd, cwd=root)
        table = table_of(so) if code == 0 else None
        if table is None:
            raise LoopError(f"progress.py new（モード {mode} での作り直し）が断った（{code}）: {(se or so).strip()[:300]}")
        lap.update(table=table, sprint_command=shlex.join(cmd))
        lap.setdefault("tables", []).append(table)
    if lap.get("judgments") is not None:
        lap["mode"] = mode
        lap.setdefault("mode_decisions", []).append(mode_decision(env, DESIGN_GATE, kept, current, mode))
        if mode != current:  # 同じモードは loop sprint が本文とボードへ書き済み。変わったときだけ書き直す
            notes.extend(sync_mode(env, kept, mode))
    done = Path(lap["dir"]) / f"done-{k}.json"
    lap.setdefault("dones", []).append(str(done))
    return next_pipeline(rec, lap["table"], done)


# ---------------------------------------------------------------- リリース


def table_release_pending(env: Env, rec: dict, before: dict, lap: dict, item: dict) -> dict:
    """レーンのリリースの依頼を送ったループの 1 回を「リリース待ち」で締める。答えはループの 1 回の境（`pending_step`）で読み、
    `parallel.py await` が答えを進行表の待ちの行へ届けてから、同じ進行表の残り（まとめ）を `progress.py next` で流す。"""
    record = Path(item["next_step"].rsplit("--from", 1)[-1].strip().strip("'\""))
    sent = jsonio.read(record, missing={}, broken={}, want=dict)
    lap["release"] = {
        "out": str(record),
        "plan_name": sent.get("plan"),
        "request": sent.get("id"),
        "row": item.get("row"),
        "await": item["next_step"],
    }
    table_versions(env, lap)
    close_lap(env, rec, lap, RELEASE_PENDING)
    lap["held_at"] = lap["ended_at"]
    return after_close(
        env, rec, before, f"ループの {lap['n']} 回目 のリリースの依頼（{sent.get('id') or '識別子なし'}）を送り、リリース待ちで締めた"
    )


def table_gate2(env: Env, rec: dict, before: dict, lap: dict, item: dict) -> dict:
    """リリース承認ゲートを人へ戻す。本番を出さずに止まる。承認の後は待ちの行へ答えを書いて同じ進行表を流す（`gate2_pipeline`）。"""
    verdict = last_verdict(lap["state_path"], "release")
    lap.setdefault("gates", []).append(gate_copy(RELEASE_GATE, verdict))
    lap["gate2_row"] = item.get("row")
    prs = pr_numbers(verdict.get("pr"))
    values = {
        "pr": prs[0] if len(prs) == 1 else prs or None,
        "verdict": verdict.get("verdict", "unknown"),
        "reasons": verdict.get("reasons") or [],
    }
    stop = {"condition": "gate2_returned", "values": values}
    return stop_and_report(env, rec, before, stop, "リリース承認ゲートを人へ戻した。本番を出さずに止まる（人の承認を求める）", {"gate": 2})


def table_gate2_pipeline(rec: dict, lap: dict) -> str:
    """リリース承認ゲートの承認の後の続き: 待ちの行へ承認を書き（`progress.py answer`）、同じ進行表を次の工程から流して締める。"""
    done = Path(lap["dir"]) / f"done-{len(lap.get('dones', [])) + 1}.json"
    lap.setdefault("dones", []).append(str(done))
    q = shlex.quote
    answer = f"python3 {q(str(PROGRESS))} answer {q(lap['table'])} {q(lap.get('gate2_row') or '')} --approved --by conductor"
    return f"{answer} && {next_pipeline(rec, lap['table'], done)}"


def table_finish_pipeline(rec: dict, lap: dict, done: Path) -> str:
    """リリース待ちのループの 1 回の残り: 答えを進行表の待ちの行へ届け（`parallel.py await`）、同じ進行表の残り（まとめ）を流して
    `loop finish` で締め直す。質問待ちは、答えの後に同じ進行表を次の工程から流す。"""
    q = shlex.quote
    from sprint_loop_record import SPRINT_STATE

    head = f"{lap['release']['await']} > /dev/null; " if lap.get("release", {}).get("await") else ""
    part = done.with_suffix(".part")
    return (
        f"{head}python3 {q(str(PROGRESS))} next {q(lap['table'])} > {q(str(part))}; mv {q(str(part))} {q(str(done))}; "
        f"python3 {q(str(SPRINT_STATE))} loop finish {rec['name']} {lap['n']} --done {q(str(done))}"
    )


def table_finish_ok(done: str) -> bool:
    """`loop finish` の done が完了か。進行表の出力（`progress.py next`）は status で、進行表を持たないループの 1 回（この版より前に
    組んだもの）の done は計画ごとの結果で決める。"""
    rep = jsonio.read(done, missing={}, broken={}, want=dict)
    if "status" in rep:
        return rep["status"] == "ok"
    items = rep.get("items") or []
    return bool(items) and all(not i.get("unfilled") and i.get("result") in ("完了", NOT_RUN) for i in items)
