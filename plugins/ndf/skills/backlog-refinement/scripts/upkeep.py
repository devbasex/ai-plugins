#!/usr/bin/env python3
"""upkeep.py: backlog-refinement の手順 1（候補の収集）と手順 3（反映）の決まった手順。

    python3 upkeep.py candidates --since-ref <ref> [--all] [--add 12,34] [--limit N]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py apply --plan plan.json [--max-waits N] [--max-wait 秒]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py report [--depth diff|review|overhaul [--run R] [--order prune,rank,...]]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py rank [--scores <見積の入力>] [--capacity N] ...（引数と記録は upkeep_rank_cmd.py の冒頭）
    python3 upkeep.py depth due|scope|record|compare ... / causes record ...（棚卸しの深さ。upkeep_depth.py の冒頭）
    python3 upkeep.py owner claim|touch|release|status ...（見直しの持ち主。upkeep_owner.py の冒頭）
    python3 upkeep.py research plan|run ...（ニーズとシーズの調査。upkeep_research.py の冒頭）
    python3 upkeep.py overhaul snapshot|count|measure ...（大整理。upkeep_overhaul.py の冒頭）
    python3 upkeep.py revert --run <実行>（付け直しを戻す。upkeep_actions.py の冒頭）

区分の決定（手順 2A / 2B）は持たない。LLM が candidates の結果を読んで区分を決め、plan.json に書く。

candidates: 手順 1 の経路のうち機械で集められるものを集め、重複を除いて件数とともに返す。`--depth diff`（既定）は差分の
  棚卸しの経路だけを、`--depth review` は no-milestone・closed-milestone と後回しを除く open の全件（all）も拾い、上限で切らない。
  経路は diff-path（差分のパス）/ diff-identifier（削除された識別子）/ no-milestone /
  closed-milestone（閉じた課題のマイルストーン）/ sub-issue（閉じた親の子）/
  commit-subject（<ref>..HEAD のコミットの件名が #番号で指す）/ all（--all）/
  manual（--add で担当が足したもの）/ unscored（記録の見積が無いか、付けた後に要約値が変わった課題。rank を打った
  リポジトリだけ。`--depth diff` では <ref> のコミットの時刻（metrics.since）より後に作られた課題だけ。上限で切るときは数えない）。commit-subject の候補は、上限で切るときも先に残す。候補ごとに updated_at と
  課題の要約値（題名・本文・状態・マイルストーン・ラベル）を返す。
  上限（--limit → .ndf/backlog.json の candidate_limit → 既定 10）を超えた分は items に載せず、metrics.deferred に番号だけを返す（終了コード 20）。
apply: plan.json の変更を反映する。反映の直前に updated_at を照合し、変わっていれば課題の
  要約値を比べ、それも変わっていれば飛ばす（skipped_changed）。途中で止まった課題は書き込んだ後の
  要約値を記録に残し、打ち直しでは自分の書き込みとして扱う。済んだものは記録に記録して
  2 度書かない。上限に当たれば Retry-After / 回復時刻 / 倍々の順で待ち、--max-waits を
  超えたら部分的に終わった状態で止める。
report: candidates・rank・apply の記録から完了報告の値を返す。candidates は前の回の apply と rank の記録を消す。
  `--depth` を渡すと深さの値（起動した条件・open の前後・閉じた・起票した・飛ばした側面・調べた材料 `research`・
  根本原因・戻すコマンド）を足す（upkeep_report.depth_metrics）。
rank: 順位・切り出しの境界・前倒しと後ろ倒しの候補（算出は upkeep_rank.py）。plan の rank・rejected・reschedule は
  upkeep_rank_cmd.py の RankPlan が確かめ、承認の要る移動は「やらない」と同じく needs_approval へ回す。

plan.json の形（起票・付け直しと深さのキーは upkeep_actions.py の冒頭）:

    {"repo": "owner/name",
     "actions": [{"number": 12, "verdict": "追記が要る",
                  "updated_at": "<candidates の値>", "digest": "<candidates の値>",
                  "changes": {"body": "...", "title": "...", "milestone": "<題名>" | null,
                              "add_labels": ["..."], "remove_labels": ["..."],
                              "state": "closed", "state_reason": "completed" | "not_planned"},
                  "reschedule": "前倒し" | "後ろ倒し",  # rank の候補を反映するときだけ
                  "approved": false}],
     "rank": "<rank.json の metrics.digest>",  # 書くと各マイルストーンの説明の ### 順位 を書く
     "rejected": [{"number": 34, "direction": "前倒し" | "後ろ倒し"}]}  # 人が退けた移動

結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 10 = 「やらない」に承認が
要る / 20 = LLM の判断待ち（上限を超えた候補・照合で飛ばした課題・部分的に終わった反映）/
1 = 反映の失敗 / 2 = 読めない / 3 = 前提が無い。
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
import deps  # noqa: E402  外部パッケージの環境（upkeep_gh が投稿キューを読む）

deps.require("locks")  # 深さの記録と見直しの持ち主の錠（upkeep_depth・upkeep_owner が最上位で読む。#2315）
import jsonio  # noqa: E402
from step_result import EXIT_PAUSE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, approval_present, common_parser  # noqa: E402
from step_result import emit, git_root, main_with, result  # noqa: E402
import upkeep_rank_cmd as RC  # noqa: E402
import upkeep_report  # noqa: E402
from upkeep_gh import DEFAULT_MAX_WAIT, DEFAULT_MAX_WAITS, Gh, Milestones, Partial, list_issues, target_repo, with_labels  # noqa: E402
from upkeep_routes import _Routes, _route_commits, _route_diff, _route_manual, _route_milestones, _route_review, _route_sub_issues  # noqa: E402
import upkeep_actions as A  # noqa: E402
import upkeep_depth as D  # noqa: E402
import upkeep_overhaul as V  # noqa: E402
import upkeep_owner as O  # noqa: E402
import upkeep_research as R  # noqa: E402

TOOL = "backlog-refinement"

VERDICTS = ("そのまま", "追記が要る", "書き直しが要る", "閉じてよい", "やらない", "重複", "ルートコーズ", "要判断")
# 承認を得てから反映する区分。承認の無いものは needs_approval へ回す。
NEEDS_APPROVAL = ("やらない",)
# 反映しない区分。人へ返す。
RETURNED = ("要判断",)

ROUTES = ("diff-path", "diff-identifier", "no-milestone", "closed-milestone", "sub-issue", "commit-subject", "all", "manual", "unscored")

# ---------------- 小関数 ----------------


def digest(body) -> str:
    """本文の要約値。改行の違いと行末の空白は同じとみなす。"""
    text = (body or "").replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def snapshot_digest(issue: dict) -> str:
    """課題の要約値。apply が書き換えうる欄（題名・本文・状態・マイルストーン・ラベル）をまとめる。

    本文だけを見ると、題名・マイルストーン・ラベルの並行した更新を見落として上書きする。
    updated_at はコメントでも動くため、updated_at だけでは飛ばさない。
    """
    snap = {
        "title": issue.get("title") or "",
        "body": digest(issue.get("body")),
        "state": issue.get("state") or "",
        "milestone": (issue.get("milestone") or {}).get("title"),
        "labels": sorted(lb.get("name", "") for lb in issue.get("labels") or []),
    }
    return hashlib.sha256(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _state_dir(arg, repo: str) -> Path:
    base = arg or os.environ.get("NDF_UPKEEP_STATE_DIR") or str(Path(tempfile.gettempdir()) / "ndf" / "backlog-refinement")
    d = Path(base) / repo.replace("/", "--")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_state(path: Path):
    """状態の JSON。無ければ None、読めなければ終了コード 2（読みは `jsonio`）。"""
    try:
        return jsonio.read(path, missing=None)
    except jsonio.JsonReadError as e:
        raise StepError(f"{path} を読めない: {e.detail or e}", EXIT_UNREADABLE)


# ---------------- candidates ----------------


def _candidate_result(a, repo, since, open_issues, closed, paths, idents, routes, empty_milestones, notes, waits):
    found = routes.routes
    # コミットの件名が指す課題は直っている見込みが高いため、上限で切るときも先に残す
    order = sorted(found, key=lambda n: ("commit-subject" not in found[n], -len(found[n] - {"unscored"}), n))
    if a.depth == "review":  # 見直しは上限で切らず、費用と秒の上限で止めたときに古い順で持ち越す（#2315 の決定 11）
        order = sorted(found, key=lambda n: (routes.by_num[n].get("updated_at") or "", n))
    keep = order[: a.limit]
    deferred = [n for n in order if n not in set(keep)]
    items = []
    # 上限を超えた候補は items に載せない（metrics.deferred にだけ並べる）。載せると
    # 区分を決める対象として求められ、上限が効かない。
    for n in keep:
        i = routes.by_num[n]
        items.append(
            {
                "kind": "issue",
                "name": f"#{n}",
                "result": "candidate",
                "number": n,
                "title": i.get("title") or "",
                "routes": sorted(found[n], key=ROUTES.index),
                "terms": sorted(routes.terms.get(n, ())),
                "updated_at": i.get("updated_at"),
                "digest": snapshot_digest(i),
            }
        )
    for t in empty_milestones:
        items.append({"kind": "milestone", "name": t, "result": "no-open-issue"})
    by_route = {r: sum(1 for n in keep if r in found[n]) for r in ROUTES}
    metrics = {
        "since_ref": a.since_ref,
        "since": since,
        "repo": repo,
        "open": len(open_issues),
        "candidates": len(keep),
        "deferred": deferred,
        "limit": a.limit,
        "depth": a.depth,
        "by_route": by_route,
        "closed_since": len(closed),
        "paths": len(paths),
        "identifiers": len(idents),
        "notes": notes,
        "waits": waits,
    }
    summary = (
        f"候補 {len(keep)} 件（open {len(open_issues)} 件中）: "
        + "・".join(f"{r} {c}" for r, c in by_route.items() if c)
        + (f"。上限 {a.limit} 件を超えた {len(deferred)} 件は次の回へ" if deferred else "")
    )
    out = result(
        TOOL,
        "gate" if deferred else "ok",
        summary,
        items,
        metrics,
        next=(
            f"上限 {a.limit} 件を超えた {len(deferred)} 件（deferred）は、次の回に --add で渡すか --limit を上げる" if deferred else None
        ),
    )
    return out, deferred


def cmd_candidates(a):
    root = git_root(a.root)
    a.limit = RC.candidate_limit(a.limit, RC.read_backlog_decl(root))  # 候補を集める前に決める（不正なら集めない）
    a.limit = None if a.depth == "review" else a.limit
    repo = target_repo(root, a.repo)
    gh = Gh(repo)
    since = RC.ref_date(root, a.since_ref)
    open_issues = list_issues(gh, "state=open")
    routes = _Routes(open_issues)
    notes = []

    paths, idents = _route_diff(open_issues, root, a.since_ref, routes)
    closed, empty_milestones, has_milestones = _route_milestones(gh, repo, open_issues, since, routes, notes, a.depth)
    _route_sub_issues(gh, repo, closed, open_issues, routes, notes)
    _route_commits(root, a.since_ref, routes)
    _route_manual(a, routes, notes)
    sd = _state_dir(a.state_dir, repo)
    # 差分の棚卸しが見積の段階を付けるのは起点より後に作られた課題だけ（#2315 の決定 1・決定 6）。見直しは絞らない
    pool = open_issues if a.depth == "review" else [i for i in open_issues if (i.get("created_at") or "") > since]
    for n in RC.unscored(sd, _read_state, pool, snapshot_digest) if has_milestones else []:
        routes.add(n, "unscored")
    if a.depth == "review":
        _route_review(open_issues, set((D.materials(root) or {}).get("deferred_milestones") or []), routes)

    out, deferred = _candidate_result(a, repo, since, open_issues, closed, paths, idents, routes, empty_milestones, notes, gh.waits)
    for f in ("apply.json", "rank.json"):  # 前の回の apply と rank を今回の報告へ混ぜない
        (sd / f).unlink(missing_ok=True)
    jsonio.write_atomic(sd / "candidates.json", out, indent=1)
    emit(out, EXIT_PAUSE if deferred else None)


# ---------------- apply ----------------


def _load_plan(path: str) -> dict:
    plan = _read_state(Path(path))
    if plan is None:
        raise StepError(f"plan が無い: {path}", EXIT_PRECONDITION)
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise StepError('plan は {"actions": [...]} の形で書く', EXIT_UNREADABLE)
    errs = []
    for k, act in enumerate(plan["actions"]):
        if not isinstance(act, dict) or not isinstance(act.get("number"), int):
            errs.append(f"actions[{k}] に number が無い")
            continue
        if act.get("verdict") not in VERDICTS:
            errs.append(f"#{act['number']} の verdict は {' / '.join(VERDICTS)} のどれか")
        if not act.get("updated_at") or not act.get("digest"):
            errs.append(f"#{act['number']} に updated_at と digest が無い（照合できない）")
        if "approved" in act and not isinstance(act["approved"], bool):  # "false" などで承認を通さない
            errs.append(f"#{act['number']} の approved は true / false で書く")
        ch = act.get("changes", {})
        if not isinstance(ch, dict):
            errs.append(f"#{act['number']} の changes はオブジェクトで書く")
    if errs:
        raise StepError("plan の誤り: " + " / ".join(errs), EXIT_UNREADABLE)
    return plan


def _ledger_key(repo: str, act: dict) -> str:
    """記録のキー。plan の updated_at と要約値も含め、後の回の同じ変更を「済み」にしない。"""
    ch = json.dumps(
        {"changes": act.get("changes") or {}, "updated_at": act.get("updated_at"), "digest": act.get("digest")},
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"{repo}#{act['number']}:{hashlib.sha256(ch.encode('utf-8')).hexdigest()[:12]}"


def _diff(cur: dict, ch: dict, ms: Milestones, n: int) -> tuple[dict, list, list]:
    """いまの状態と比べて、書き込みが要るものだけを返す。"""
    patch = {}
    for k in ("title", "body"):
        if k in ch and ch[k] != (cur.get(k) or ""):
            patch[k] = ch[k]
    if "state" in ch and ch["state"] != cur.get("state"):
        patch["state"] = ch["state"]
        if ch.get("state_reason"):
            patch["state_reason"] = ch["state_reason"]
    if "milestone" in ch:
        now = (cur.get("milestone") or {}).get("title")
        if ch["milestone"] != now:
            patch["milestone"] = None if ch["milestone"] is None else ms.number(ch["milestone"], n)
    have = {lb["name"] for lb in cur.get("labels") or []}
    add = [lb for lb in ch.get("add_labels") or [] if lb not in have]
    remove = [lb for lb in ch.get("remove_labels") or [] if lb in have]
    return patch, add, remove


def _apply_one(gh, ms, repo, act, rec, record, hold=None):
    """1 件を反映し (区分, 理由) を返す。hold は承認を待つ移動の理由。Partial は書き込み後の要約値を記録してから投げ直す。"""
    n, verdict, ch = act["number"], act["verdict"], act.get("changes") or {}
    if verdict in RETURNED:
        return "returned", "要判断は反映しない"
    if rec is not None and rec.get("result") != "partial":
        return "already", "記録にある"
    if verdict in NEEDS_APPROVAL and act.get("approved") is not True:
        return "needs_approval", "やらないは承認を得てから反映する"
    if hold:  # 承認の要る前倒し・後ろ倒しは approved によらず待つ（rank --approved で承認済みになる）
        return "needs_approval", hold
    cur, wrote = None, False
    try:
        cur = gh.call([f"repos/{repo}/issues/{n}"], target=n)
        now = snapshot_digest(cur)
        own = rec is not None and now == rec.get("digest")  # 前の打ち直しで自分が書いた状態
        if cur.get("updated_at") != act["updated_at"] and now != act["digest"] and not own:
            return "skipped_changed", f"updated_at {act['updated_at']} → {cur.get('updated_at')}・課題の要約値も変わった"
        patch, add, remove = _diff(cur, ch, ms, n)
        if not (patch or add or remove):
            record({"result": "unchanged"})
            return "unchanged", "変える内容が無い"
        if patch:
            got = gh.call(
                [f"repos/{repo}/issues/{n}", "-X", "PATCH", "--input", "-"], stdin=json.dumps(patch, ensure_ascii=False), target=n
            )
            cur, wrote = (got if isinstance(got, dict) else {**cur, **patch}), True
        if add:
            got = gh.call(
                [f"repos/{repo}/issues/{n}/labels", "-X", "POST", "--input", "-"],
                stdin=json.dumps({"labels": add}, ensure_ascii=False),
                target=n,
            )
            cur, wrote = with_labels(cur, got, add=add), True
        for lb in remove:
            seg = urllib.parse.quote(lb, safe="")  # `status/blocked` の `/` を別のパスにしない
            got = gh.call([f"repos/{repo}/issues/{n}/labels/{seg}", "-X", "DELETE"], target=n)
            cur, wrote = with_labels(cur, got, drop=lb), True
        record({"result": "applied", "fields": sorted(patch), "add_labels": add, "remove_labels": remove})
        return "applied", ", ".join(sorted(patch) + [f"+{x}" for x in add] + [f"-{x}" for x in remove])
    except (Partial, StepError) as e:
        if wrote:  # 書き込んだ後の要約値を残し、打ち直しで自分の書き込みを並行の更新と取り違えない
            record({"result": "partial", "digest": snapshot_digest(cur)})
        if isinstance(e, StepError):
            return "failed", str(e)
        raise


def _apply_outcome(repo, actions, buckets, partial, why_partial, prev, waits, rp):
    """区分ごとの課題番号から metrics・要約・終了の状態・次の手を作る。"""
    closed = [
        act["number"] for act in actions if act["number"] in buckets["applied"] and (act.get("changes") or {}).get("state") == "closed"
    ]
    metrics = {
        **buckets,
        "closed": closed,
        "waits": waits,
        "round": upkeep_report.next_round(prev, actions, buckets, closed, waits),
        "partial": partial,
        "verdicts": {v: sum(1 for act in actions if act["verdict"] == v) for v in VERDICTS},
        "tables": rp.tables,
        "tables_withheld": rp.withheld,
    }
    summary = (
        f"反映 {len(buckets['applied'])} 件（閉じた {len(closed)} 件）・照合で飛ばした "
        f"{len(buckets['skipped_changed'])} 件・変更なし {len(buckets['unchanged'])} 件・済み "
        f"{len(buckets['already'])} 件・承認待ち {len(buckets['needs_approval'])} 件・返した "
        f"{len(buckets['returned'])} 件・失敗 {len(buckets['failed'])} 件・待ち {len(waits)} 回"
        + (f"。部分的に終えた（{why_partial}）" if partial else "")
    )
    status, code, nxt, pres = "ok", None, None, None
    if buckets["failed"]:
        status, code = "stopped", 1
    elif buckets["needs_approval"]:
        status, code = "gate", 10
        pres = RC.approval_presentation(TOOL, repo, actions, buckets["needs_approval"], rp.holds, approval_present)
        nxt = '承認を得た課題に "approved": true を付けて同じ plan で apply を打ち直す（済んだものは記録で飛ぶ）'
        if rp.holds:
            nxt += "。前倒し・後ろ倒しは承認を得た番号を rank --approved で渡して打ち直し、その結果で plan を作り直す"
    elif rp.stale:
        status, code, nxt = "gate", EXIT_PAUSE, "plan の rank が rank.json と合わない。rank を打ち直して plan を作り直す"
    elif partial or buckets["skipped_changed"] or rp.tables["failed"] or rp.withheld:
        status, code = "gate", EXIT_PAUSE
        parts = []
        if buckets["skipped_changed"]:
            parts.append("照合で飛ばした " + " ".join(f"#{x}" for x in buckets["skipped_changed"]) + " を手順 2A へ戻す")
        parts += rp.withheld_notes()
        if partial or rp.tables["failed"]:
            parts.append("時間を置いて同じ plan で apply を打ち直す（済んだものは記録で飛ぶ）")
        nxt = "。".join(parts)
    return status, code, summary, metrics, pres, nxt


def cmd_apply(a):
    root = git_root(a.root)
    plan, wrong = A.precheck_depth(root, _load_plan(a.plan))  # 深さと持ち主（#2315 の I3・I12）
    repo = target_repo(root, a.repo)
    sd = _state_dir(a.state_dir, repo)
    # 記録のリポジトリ（保存した candidates.json の metrics.repo）の外へ書かない（#842 の決定 7）。
    # 書き込み先と plan の repo の双方を記録と照合する（同じ呼び出しの --repo どうしを比べると、両方を別のリポジトリにすれば通る）
    recorded = ((_read_state(sd / "candidates.json") or {}).get("metrics") or {}).get("repo")
    why = None
    if recorded is None:
        why = f"書き込み先 {repo} の記録（candidates.json）が無い。candidates を打ってから plan を作る"
    elif repo != recorded:
        why = f"書き込み先 {repo} は記録のリポジトリ {recorded} と違う"
    elif plan.get("repo") not in (None, recorded):
        why = f"plan の repo {plan['repo']} は記録のリポジトリ {recorded} と違う"
    if why:
        emit(
            result(TOOL, "stopped", f"{why}。1 件も書かない", [{"kind": "plan", "name": a.plan, "result": "stopped", "reason": why}], {}),
            EXIT_PRECONDITION,
        )
    ledger_path = sd / "ledger.json"
    ledger = _read_state(ledger_path) or {}
    gh = Gh(repo, max_waits=a.max_waits, max_wait=a.max_wait)
    ms = Milestones(gh)
    buckets = {k: [] for k in ("applied", "skipped_changed", "unchanged", "already", "needs_approval", "returned", "failed", "pending")}
    items, partial, why_partial = [], False, ""
    # 同じ回（前の candidates 以降）の打ち直しを足し合わせ、承認後・partial 後の報告から前の反映・区分・待ちを落とさない。
    # 書き込む前に読み、前の記録が読めないときは 1 件も書かずに止める
    prev_out = _read_state(sd / "apply.json")
    prev = (((prev_out or {}).get("metrics") or {}).get("round") or {"runs": 1}) if prev_out is not None else {}
    rp = RC.RankPlan(plan, sd, _read_state)
    actions = [] if rp.stale else plan["actions"]  # rank.json と合わない plan は何も書かない

    def record(value=None, key=None):
        """済んだ反映を記録へ書く。key が無ければ今の記録をそのまま書く（起票・付け直しの記録）。"""
        if key is not None:
            ledger[key] = value
        jsonio.write_atomic(ledger_path, ledger, indent=1)

    for idx, act in enumerate(actions):
        n, verdict = act["number"], act["verdict"]
        key = _ledger_key(repo, act)
        try:
            bucket, reason = _apply_one(gh, ms, repo, act, ledger.get(key), functools.partial(record, key=key), rp.hold(act))
        except Partial as e:
            partial, why_partial = True, str(e)
            for rest in actions[idx:]:
                buckets["pending"].append(rest["number"])
                items.append({"kind": "issue", "name": f"#{rest['number']}", "result": "pending", "verdict": rest["verdict"]})
            break
        buckets[bucket].append(n)
        it = {"kind": "issue", "name": f"#{n}", "result": bucket, "verdict": verdict}
        if reason:
            it["reason"] = reason
        items.append(it)
    rp.withheld = [] if partial else rp.unmoved(actions, buckets)
    try:
        rp.tables = rp.write_tables(None if partial else ms)
    except Partial as e:
        partial, why_partial = True, str(e)
    rp.update_rejected()
    extra = A.write_extra(root, plan, gh, ms, repo, ledger, record) if not partial else {}

    status, code, summary, metrics, pres, nxt = _apply_outcome(repo, actions, buckets, partial, why_partial, prev, gh.waits, rp)
    status, code, nxt = A.merge_outcome(extra, wrong, status, code, nxt, metrics, items)
    out = result(TOOL, status, summary, items, metrics, presentation_path=pres, next=nxt)
    jsonio.write_atomic(sd / "apply.json", out, indent=1)
    emit(out, code)


# ---------------- CLI ----------------


def _numbers(s: str) -> list[int]:
    try:
        return [int(x.lstrip("#")) for x in s.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"番号の並びでない: {s}")


def build_parser():
    common = common_parser()
    common.add_argument("--repo", default=None, help="owner/name")
    common.add_argument("--state-dir", default=None, help="記録の置き場所")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates", parents=[common])
    c.add_argument("--since-ref", required=True)
    c.add_argument("--depth", choices=("diff", "review"), default="diff", help="棚卸しの深さ（省くと差分の棚卸し。#2315 の決定 2）")
    c.add_argument("--all", action="store_true")
    c.add_argument("--add", type=_numbers, default=[], help="担当が足す課題の番号（12,34）")
    c.add_argument("--limit", type=int, default=None, help="1 回に扱う候補の上限（省くと宣言の candidate_limit、無ければ既定 10）")
    c.set_defaults(func=cmd_candidates)
    p = sub.add_parser("apply", parents=[common])
    p.add_argument("--plan", required=True)
    p.add_argument("--max-waits", type=int, default=DEFAULT_MAX_WAITS)
    p.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT, help="1 回の待ちの上限（秒）")
    p.set_defaults(func=cmd_apply)
    ctx = argparse.Namespace(tool=TOOL, state_dir=_state_dir, read_state=_read_state)
    r = sub.add_parser("report", parents=[common])
    r.add_argument("--depth", choices=("diff", "review", "overhaul"), default=None, help="棚卸しの深さの値を足す（#2315）")
    r.add_argument("--run", default=None, help="見直し・大整理の実行（省くと持ち主の宣言の実行）")
    r.add_argument("--order", default=None, help="通した手順の並び（prune,rank,... 省くと null）")
    r.set_defaults(func=lambda a: upkeep_report.cmd_report(a, ctx))
    RC.add_parser(sub, common, _numbers, lambda a: RC.cmd_rank(a, ctx))
    D.add_depth_parsers(sub, common, _state_dir, _numbers)  # 深さ・持ち主・調査・大整理・戻し（#2315）
    for add in (O.add_owner_parsers, R.add_research_parsers, V.add_overhaul_parsers, A.add_revert_parser):
        add(sub, common)
    return ap


def main(argv=None):
    # schema は見積もりの記録の形（rank が読む。#1856）。procs は調査の claude -p（upkeep_research.call_llm）が読む。
    # 起動し直し（exec）は見直しの持ち主を取る前のここで 1 回に済ませる（取った後だと自分の宣言に阻まれる）
    deps.require("durable", "schema", "waits", "procs")
    main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
