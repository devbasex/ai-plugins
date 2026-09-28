"""upkeep_rank_cmd.py: `upkeep.py rank` と、candidates・apply・report の順位付けの部分（#1429）。

    python3 upkeep.py rank [--scores <見積の入力>] [--approved 12,34] [--capacity N] [--scale 1,2,3,5,8,13,20]
                           [--margin-steps N] [--auto-threshold N] [--dependents-steps 0:1,1:5,2:8,3:13,5:20]
                           [--size-exponent X] [--large-slot-ratio X] [--large-min-size N]
                           [--label-floor "<ラベル>=8"]... [--repo owner/name] [--state-dir <dir>] [--root <dir>]

rank: GitHub から open の課題・マイルストーン（説明）・サブイシューを読み、見積（--scores → 記録の estimates.json →
  説明の `### 順位` の表の順に採る）から、マイルストーンごとの順位・大きい課題の枠・切り出しの境界・前倒しと後ろ倒しの
  候補・前回の表からの変化を 1 行の JSON で返す。算出は upkeep_rank.py が持つ。設定は 引数 → `.ndf/backlog.json` →
  既定値 の順に採る。終了コード: 0 = 算出した（マイルストーンが無ければ飛ばす）/ 20 = 段階の欠けた課題がある /
  2 = 入力・宣言が読めない・依存が循環した / 3 = gh が使えない。

記録（$NDF_UPKEEP_STATE_DIR/<repo>/）: estimates.json（課題ごとの最新の見積。rank が書く）・rejected.json（却下した移動。
apply だけが書く）・rank.json（その回の rank の出力。candidates が消す）。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonio
import repo as repo_lib
import upkeep_rank as R
from step_result import EXIT_PAUSE, EXIT_UNREADABLE, StepError, emit, git_root, result
from upkeep_gh import Gh, Milestones, list_issues, target_repo, sub_issues

DECL = Path(".ndf") / "backlog.json"
NO_WORK_ROLLBACK = "閉じた課題を reopen し、wontfix を外す（本文は GitHub の編集履歴から戻せる）"


# ---------------- 設定 ----------------


def read_backlog_decl(root) -> dict | None:
    """バックログの宣言。メインディレクトリの `.ndf/` を先に、無ければ root の `.ndf/` を読む。壊れていれば終了コード 2。"""
    main = repo_lib.main_dir(root) if root else None
    for base in dict.fromkeys(p for p in (main, Path(root) if root else None) if p):
        f = Path(base) / DECL
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise StepError(f"{DECL} を読めない: {e}", EXIT_UNREADABLE)
    return None


def _label_floor(values):
    if not values:
        return None
    out = {}
    for v in values:
        name, sep, step = v.rpartition("=")
        if not sep or not name or not step.strip().isdigit():
            raise StepError(f"--label-floor は ラベル=段階 で書く: {v}", EXIT_UNREADABLE)
        out[name] = int(step)
    return out


def config_args(a) -> dict:
    return {
        "scale": a.scale,
        "margin_steps": a.margin_steps,
        "auto_threshold": a.auto_threshold,
        "capacity": a.capacity,
        "dependents_steps": a.dependents_steps,
        "size_exponent": a.size_exponent,
        "large_slot_ratio": a.large_slot_ratio,
        "large_min_size": a.large_min_size,
        "label_floor": _label_floor(a.label_floor),
    }


def add_parser(sub, common, numbers, func):
    k = sub.add_parser("rank", parents=[common])
    k.add_argument("--scores", default=None, help="手順 2B が書いた見積の入力（JSON）")
    k.add_argument("--approved", type=numbers, default=[], help="承認を得た移動の課題の番号（12,34）")
    k.add_argument("--capacity", type=int, default=None, help="1 つのスプリントへ切り出す Size の和の上限")
    k.add_argument("--scale", default=None, help="尺度（1,2,3,5,8,13,20）")
    k.add_argument("--margin-steps", type=int, default=None)
    k.add_argument("--auto-threshold", type=int, default=None)
    k.add_argument("--dependents-steps", default=None, help="被依存の数の下限:段階 の並び（0:1,1:5,2:8,3:13,5:20）")
    k.add_argument("--size-exponent", type=float, default=None)
    k.add_argument("--large-slot-ratio", type=float, default=None)
    k.add_argument("--large-min-size", type=int, default=None)
    k.add_argument("--label-floor", action="append", default=[], help="ラベル=段階（何度でも）")
    k.set_defaults(func=func)


# ---------------- rank ----------------


def _read_scores(path, read_state) -> dict:
    if not path:
        return {}
    got = read_state(Path(path))
    if got is None:
        raise StepError(f"見積の入力が無い: {path}", EXIT_UNREADABLE)
    if not isinstance(got, dict) or not isinstance(got.get("issues", []), list):
        raise StepError('見積の入力は {"version": 1, "issues": [...]} の形で書く', EXIT_UNREADABLE)
    ms = got.get("milestones")
    if ms is not None and (not isinstance(ms, list) or not all(isinstance(t, str) for t in ms)):
        raise StepError("見積の入力の milestones は題名の列で書く", EXIT_UNREADABLE)
    return got


def estimates(issues, scores, stored, previous, cfg):
    """open の課題ごとの見積。--scores → 記録 → 前回の表 の順に採る。どれにも無い・検証に落ちた課題は外す（I1・I10）。"""
    given = {i.get("number"): i for i in scores.get("issues", []) if isinstance(i, dict)}
    prev_rows = {}
    for tab in previous.values():
        for r in tab["rows"]:
            prev_rows.setdefault(r["number"], r)
    ests, excluded = {}, []
    for n in sorted(issues):
        if n in given:
            raw, band = given[n], True
            if not raw.get("digest"):
                excluded.append({"number": n, "reason": "digest が無い（candidates の値を写す）"})
                continue
        elif str(n) in stored:
            raw = stored[str(n)]
            band = not raw.get("restored") and (raw.get("ubv") or {}).get("impact") is not None
        elif n in prev_rows:
            raw, band = R.restore_from_row(prev_rows[n], cfg), False
        else:
            excluded.append({"number": n, "reason": "段階が無い（手順 2B で付ける）"})
            continue
        est, why = R.validate_estimate(raw, cfg, check_band=band)
        if est is None:
            excluded.append({"number": n, "reason": why})
        else:
            ests[n] = est
    return ests, excluded


def _items(issues, got, excluded):
    fwd = {f["number"] for f in got["forward"]}
    back = {b["number"] for b in got["backward"]}
    out = []
    for n in sorted(issues):
        if n in fwd:
            res = "forward"
        elif n in back:
            res = "backward"
        elif n in got["placement"]:
            res = "ranked"
        else:
            continue
        out.append({"kind": "issue", "name": f"#{n}", "result": res})
    out += [{"kind": "issue", "name": f"#{e['number']}", "result": "excluded", "reason": e["reason"]} for e in excluded]
    return out


def _rank_summary(got, excluded, cfg) -> str:
    ranked = sum(len(m["rows"]) for m in got["milestones"])
    auto = sum(1 for f in got["forward"] if f["decision"] == R.AUTO)
    s = f"順位 {ranked} 件（マイルストーン {len(got['milestones'])} 本）"
    if cfg.capacity is None:
        s += "。容量が無いため境界と候補は出していない"
    else:
        s += f"・前倒しの候補 {len(got['forward'])} 件（自動 {auto}）・後ろ倒しの候補 {len(got['backward'])} 件"
    return s + (f"・外した {len(excluded)} 件" if excluded else "")


def _write_rank_result(sd, out, code=None):
    jsonio.write_atomic(sd / "rank.json", out, indent=1)
    return emit(out, code)


def _build_board(a, gh, rows, order, scores, cfg, sd, ctx, notes):
    """open の課題・前回の表・見積・依存の辺から Board を作る。戻り値は (board, issues, ests, excluded)。"""
    open_issues = list_issues(gh, "state=open")
    issues = {
        i["number"]: {"milestone": (i.get("milestone") or {}).get("title"), "labels": [lb.get("name") for lb in i.get("labels") or []]}
        for i in open_issues
    }
    previous = {r["title"]: tab for r in rows if (tab := R.parse_rank_table(r.get("description") or "")) is not None}
    ests, excluded = estimates(issues, scores, ctx.read_state(sd / "estimates.json") or {}, previous, cfg)
    jsonio.write_atomic(sd / "estimates.json", {str(n): e.to_json() for n, e in ests.items()}, indent=1)
    notes.extend(
        f"--scores の #{n} は open でないため除いた" for n in (i.get("number") for i in scores.get("issues", [])) if n not in issues
    )
    subs = sub_issues(gh, [i["number"] for i in open_issues if (i.get("sub_issues_summary") or {}).get("total")], notes)
    edges = R.merge_edges(
        [
            R.sub_issue_edges(subs),
            [(n, d) for n, e in ests.items() for d in e.depends_on],
            [e for r in rows for e in R.parallel_group_edges(r.get("description") or "")],
        ],
        set(issues),
    )
    rejected = ctx.read_state(sd / "rejected.json") or []
    board = R.Board(issues, ests, cfg, order, subs, edges, previous, rejected, tuple(a.approved), excluded)
    return board, issues, ests, excluded


def _rank_metrics(repo, order, cfg, got, ests, excluded) -> dict:
    return {
        "repo": repo,
        "order": order,
        "capacity": cfg.capacity,
        **{k: got[k] for k in ("milestones", "forward", "backward", "changes", "rejected", "rejected_stale", "cross_milestone_deps")},
        "excluded": excluded,
        "waiting_decision": sorted(n for n, e in ests.items() if e.waiting_decision),
        "needs_detail": {str(n): list(e.needs_detail) for n, e in sorted(ests.items()) if e.needs_detail},
        "restored": sorted(n for n, e in ests.items() if e.restored),
    }


def cmd_rank(a, ctx):
    root = git_root(a.root)
    repo = target_repo(root, a.repo)
    gh = Gh(repo)
    sd = ctx.state_dir(a.state_dir, repo)
    try:
        cfg = R.build_config(config_args(a), read_backlog_decl(root))
    except R.RankError as e:
        raise StepError(f"宣言か引数の誤り: {e}", EXIT_UNREADABLE)
    scores = _read_scores(a.scores, ctx.read_state)
    rows = Milestones(gh).open_rows()
    if not rows:
        return _write_rank_result(
            sd, result(ctx.tool, "ok", "マイルストーンが無いため順位の算出を飛ばした", [], {"repo": repo, "skipped": True})
        )
    order = R.milestone_order([r["title"] for r in rows], scores.get("milestones"))
    notes = []
    board, issues, ests, excluded = _build_board(a, gh, rows, order, scores, cfg, sd, ctx, notes)
    try:
        got = R.compute_ranking(board)
    except R.CycleError as e:
        items = [{"kind": "issue", "name": f"#{n}", "result": "cycle"} for n in e.numbers]
        out = result(
            ctx.tool,
            "stopped",
            str(e),
            items,
            {"repo": repo, "cycle": e.numbers},
            next="循環する依存の記述を直す（どちらを先にするかは人が決める）",
        )
        return _write_rank_result(sd, out, EXIT_UNREADABLE)
    metrics = _rank_metrics(repo, order, cfg, got, ests, excluded)
    metrics["digest"] = R.stable_digest(metrics)
    metrics.update(notes=notes, waits=gh.waits)
    nxt = None
    if excluded:
        nxt = f"外した {len(excluded)} 件（excluded）に手順 2B で段階を付け、--scores で rank を打ち直す"
    elif cfg.capacity is None:
        nxt = "容量を --capacity N か .ndf/backlog.json の capacity で渡すと、切り出しの境界と前倒し・後ろ倒しの候補が出る"
    out = result(
        ctx.tool, "gate" if excluded else "ok", _rank_summary(got, excluded, cfg), _items(issues, got, excluded), metrics, next=nxt
    )
    _write_rank_result(sd, out, EXIT_PAUSE if excluded else None)


# ---------------- candidates ----------------


def unscored(sd, read_state, open_issues, snapshot_digest) -> list[int]:
    """記録の見積が無い課題と、見積を付けたときから要約値が変わった課題（決定 13）。記録が無い（rank を打っていない）なら空。"""
    stored = read_state(sd / "estimates.json")
    if stored is None:
        return []
    return [i["number"] for i in open_issues if (stored.get(str(i["number"])) or {}).get("digest") != snapshot_digest(i)]


# ---------------- apply ----------------


def _empty_tables() -> dict:
    """表の書き込み結果の空の形。呼ぶたびに新しい dict を返す。"""
    return {"written": [], "unchanged": [], "truncated": {}, "failed": []}


def _rejected_by_length(e: StepError) -> bool:
    return "422" in str(e) or "Validation Failed" in str(e)


def _write_one(ms, cur, m, desc):
    """1 本のマイルストーンの節を書く。戻り値は (written / unchanged / failed, 切り詰めた行数か None)。"""
    limit = None
    while True:
        new = R.replace_rank_section(desc, R.render_rank_table(m, limit))
        if new == desc:
            return "unchanged", limit
        if not R.outside_unchanged(desc, new):
            return "failed", limit  # 節の外が変わる書き込みはしない
        try:
            ms.set_description(cur["number"], new, target=m["title"])
            return "written", limit
        except StepError as e:
            if not _rejected_by_length(e) or limit == 0:  # 長さで拒まれたときだけ減らす
                return "failed", limit
            limit = len(m["rows"]) // 2 if limit is None else limit // 2


class RankPlan:
    """plan の順位付けの部分（`rank`・`rejected`・actions の `reschedule`）。"""

    def __init__(self, plan: dict, sd: Path, read_state):
        self.plan, self.sd, self.read_state = plan, sd, read_state
        self.used = bool(plan.get("rank") or plan.get("rejected") or any(act.get("reschedule") for act in plan["actions"]))
        self.out = read_state(sd / "rank.json") if self.used else None
        self.metrics = (self.out or {}).get("metrics") or {}
        self.stale = bool(plan.get("rank")) and plan.get("rank") != self.metrics.get("digest")
        self.holds: dict = {}
        self.withheld: list[int] = []
        self.tables = _empty_tables()
        if self.used:
            self._check()

    def candidate(self, n, direction):
        if direction == R.FORWARD:
            return next((f for f in self.metrics.get("forward", []) if f["number"] == n or n in f["group"]), None)
        return next((b for b in self.metrics.get("backward", []) if b["number"] == n), None)

    def _check(self):
        errs = []
        rej = self.plan.get("rejected", [])
        if not isinstance(rej, list):
            errs.append("rejected は [{number, direction}] の列で書く")
            rej = []
        wants = [(r.get("number"), r.get("direction"), "rejected") for r in rej if isinstance(r, dict)]
        wants += [(act["number"], act["reschedule"], act) for act in self.plan["actions"] if act.get("reschedule")]
        if (wants or self.plan.get("rank")) and self.out is None:
            raise StepError("rank.json が無い（rank を打ってから plan を作る）", EXIT_UNREADABLE)
        forward_acts = {(n, (act.get("changes") or {}).get("milestone")) for n, d, act in wants if act != "rejected" and d == R.FORWARD}
        for n, d, act in wants:
            if d not in R.DIRECTIONS:
                errs.append(f"#{n} の向きは {' / '.join(R.DIRECTIONS)} のどれか: {d!r}")
                continue
            c = self.candidate(n, d)
            if c is None:
                errs.append(f"#{n} の{d}は rank.json の候補に無い")
            elif act != "rejected":
                if (act.get("changes") or {}).get("milestone") != c["to"]:
                    errs.append(f"#{n} の changes.milestone は候補の移す先 {c['to']!r} と合わせる")
                    continue
                missing = [g for g in c.get("group", []) if d == R.FORWARD and c["number"] == n and (g, c["to"]) not in forward_acts]
                if missing:
                    errs.append(f"#{n} の前倒しは依存先 {', '.join(f'#{g}' for g in missing)} も同じ移す先で前倒しする")
                elif c["decision"] == R.APPROVAL:
                    self.holds[n] = {**c, "direction": d, "number": n}
        if errs:
            raise StepError("plan の誤り: " + " / ".join(errs), EXIT_UNREADABLE)
        self._hold_leads_of_held_groups({n for n, d, act in wants if act != "rejected" and d == R.FORWARD})

    def _hold_leads_of_held_groups(self, leads):
        """依存先が保留に入った前倒しの先頭も保留にする（先頭だけが動いて依存先を置いていかない。AC6）。"""
        changed = True
        while changed:
            changed = False
            for n in sorted(leads - set(self.holds)):
                c = self.candidate(n, R.FORWARD)
                if c["number"] == n and any(g in self.holds for g in c.get("group", [])):
                    self.holds[n] = {**c, "direction": R.FORWARD, "number": n}
                    changed = True

    def unmoved(self, actions, buckets) -> list[int]:
        """順位の表が移したものとして置くのに、この apply で移せなかった課題（表を書かない理由になる）。"""
        placed = {n for f in self.metrics.get("forward", []) if f["decision"] in (R.AUTO, R.APPROVED) for n in [f["number"], *f["group"]]}
        placed |= {b["number"] for b in self.metrics.get("backward", []) if b["decision"] == R.APPROVED}
        done = set(buckets["applied"]) | set(buckets["unchanged"]) | set(buckets["already"])
        return sorted({act["number"] for act in actions if act.get("reschedule") and act["number"] in placed} - done)

    def hold(self, act) -> str | None:
        if act["number"] in self.holds:
            return f"{act['reschedule']}は承認を得て rank --approved で打ち直してから反映する"
        return None

    def update_rejected(self):
        """却下の一覧へ plan の却下を足し、rank.json の rejected_stale を消す（apply だけが書く）。"""
        if self.out is None or self.stale:
            return
        path = self.sd / "rejected.json"
        cur = self.read_state(path) or []
        stale = {r["move_digest"] for r in self.metrics.get("rejected_stale", [])}
        new = [r for r in cur if r.get("move_digest") not in stale]
        have = {r.get("move_digest") for r in new}
        for r in self.plan.get("rejected", []):
            c = self.candidate(r["number"], r["direction"])
            if c["move_digest"] not in have:
                new.append({"number": r["number"], "direction": r["direction"], "move_digest": c["move_digest"]})
                have.add(c["move_digest"])
        if new != cur:
            jsonio.write_atomic(path, new, indent=1)

    def write_tables(self, ms: Milestones) -> dict:
        """各マイルストーンの説明の `### 順位` の節を書く（I5・I6・AC11）。拒まれたら行を半分ずつ減らす（決定 15）。"""
        got = _empty_tables()
        if ms is None or not self.plan.get("rank") or self.stale:
            return got
        current = {r["title"]: r for r in ms.open_rows()}
        for m in self.metrics.get("milestones", []):
            cur = current.get(m["title"])
            desc = (cur or {}).get("description") or ""
            if cur is None or (not m["rows"] and R.parse_rank_table(desc) is None):
                continue
            kind, limit = _write_one(ms, cur, m, desc)
            if limit is not None:
                got["truncated"][m["title"]] = limit
            got[kind].append(m["title"])
        return got


def approval_presentation(tool, repo, actions, needs, holds, approval_present):
    """承認待ちの提示。「やらない」と前倒し・後ろ倒しを別の節（判断の項目）に並べる。"""
    no_work = [n for n in needs if n not in holds]
    moves = [n for n in needs if n in holds]
    body = {act["number"]: (act.get("changes") or {}).get("body", "")[:300] for act in actions}
    judge = [(f"#{n}", body.get(n, "")) for n in no_work]
    for n in moves:
        c = holds[n]
        detail = f"CoD {c['cod']}" + (
            f"・境界との差 {c['diff']}・境界より高い列 {'・'.join(c['higher_columns']) or 'なし'}" if "diff" in c else ""
        )
        judge.append((f"#{n}（{c['direction']}: {c['from'] or '未設定'} → {c['to']}）", detail))
    consent = [f"#{n} を「やらない」で閉じる" for n in no_work] + [
        f"#{n} を {holds[n]['to']} へ移す（{holds[n]['direction']}）" for n in moves
    ]
    if moves:
        title = "棚卸しの承認（やらない・前倒し・後ろ倒し）"
        change = f"やらない {len(no_work)} 件を wontfix で閉じる・{len(moves)} 件のマイルストーンを移す"
        rollback = NO_WORK_ROLLBACK + "。移した課題はマイルストーンを元へ戻す"
    else:
        title, change, rollback = "「やらない」で閉じる課題の承認", f"{len(no_work)} 件を wontfix で閉じる", NO_WORK_ROLLBACK
    return approval_present(
        tool,
        repo.replace("/", "--") + "-no-work",
        title=title,
        targets=[{"url": f"https://github.com/{repo}/issues/{x}"} for x in needs],
        change=change,
        judge=judge,
        consent=consent,
        rollback=rollback,
    )
