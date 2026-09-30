#!/usr/bin/env python3
"""upkeep.py: backlog-refinement の手順 1（候補の収集）と手順 3（反映）の決まった手順。

    python3 upkeep.py candidates --since-ref <ref> [--all] [--add 12,34] [--limit N]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py apply --plan plan.json [--max-waits N] [--max-wait 秒]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py report [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py rank [--scores <見積の入力>] [--capacity N] ...（引数と記録は upkeep_rank_cmd.py の冒頭）

区分の決定（手順 2A / 2B）は持たない。LLM が candidates の結果を読んで区分を決め、plan.json に書く。

candidates: 手順 1 の経路のうち機械で集められるものを集め、重複を除いて件数とともに返す。
  経路は diff-path（差分のパス）/ diff-identifier（削除された識別子）/ no-milestone /
  closed-milestone（閉じた課題のマイルストーン）/ sub-issue（閉じた親の子）/
  commit-subject（<ref>..HEAD のコミットの件名が #番号で指す）/ all（--all）/
  manual（--add で担当が足したもの）/ unscored（記録の見積が無いか、付けた後に要約値が変わった課題。rank を打った
  リポジトリだけ。上限で切るときは数えない）。commit-subject の候補は、上限で切るときも先に残す。候補ごとに updated_at と
  課題の要約値（題名・本文・状態・マイルストーン・ラベル）を返す。
  --limit で 1 回に扱う件数に上限を置く。超えた分は items に載せず、metrics.deferred に番号だけを返す（終了コード 20）。
apply: plan.json の変更を反映する。反映の直前に updated_at を照合し、変わっていれば課題の
  要約値を比べ、それも変わっていれば飛ばす（skipped_changed）。途中で止まった課題は書き込んだ後の
  要約値を記録に残し、打ち直しでは自分の書き込みとして扱う。済んだものは記録に記録して
  2 度書かない。上限に当たれば Retry-After / 回復時刻 / 倍々の順で待ち、--max-waits を
  超えたら部分的に終わった状態で止める。
report: candidates・rank・apply の記録から完了報告の値を返す。candidates は前の回の apply と rank の記録を消す。
rank: 順位・切り出しの境界・前倒しと後ろ倒しの候補（算出は upkeep_rank.py）。plan の rank・rejected・reschedule は
  upkeep_rank_cmd.py の RankPlan が確かめ、承認の要る移動は「やらない」と同じく needs_approval へ回す。

plan.json の形:

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
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
import deps  # noqa: E402  外部パッケージの環境（upkeep_gh が投稿キューを読む）
import jsonio  # noqa: E402
from step_result import EXIT_PAUSE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, approval_present, common_parser  # noqa: E402
from step_result import emit, git, git_root, main_with, result  # noqa: E402
import upkeep_rank_cmd as RC  # noqa: E402
import upkeep_report  # noqa: E402
from upkeep_gh import DEFAULT_MAX_WAIT, DEFAULT_MAX_WAITS, Gh, Milestones, Partial, list_issues, target_repo, _sub_issue_kids, with_labels  # noqa: E402

TOOL = "backlog-refinement"

VERDICTS = ("そのまま", "追記が要る", "書き直しが要る", "閉じてよい", "やらない", "重複", "ルートコーズ", "要判断")
# 承認を得てから反映する区分。承認の無いものは needs_approval へ回す。
NEEDS_APPROVAL = ("やらない",)
# 反映しない区分。人へ返す。
RETURNED = ("要判断",)

ROUTES = ("diff-path", "diff-identifier", "no-milestone", "closed-milestone", "sub-issue", "commit-subject", "all", "manual", "unscored")

# 削除された識別子として拾う語の形。短い語や記号を含まない語は、ありふれた単語と区別できない。
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*[A-Za-z0-9_]")
_MIN_TOKEN = 6


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


def _is_identifier(tok: str) -> bool:
    if len(tok) < _MIN_TOKEN:
        return False
    return any(c in tok for c in "_-.") or bool(re.search(r"[a-z][A-Z]", tok))


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


def _ref_date(root, ref: str) -> str:
    p = git(root, "log", "-1", "--format=%cI", ref, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        raise StepError(f"ref を読めない: {ref}", EXIT_UNREADABLE)
    d = datetime.datetime.fromisoformat(p.stdout.strip())
    return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_terms(root, ref: str) -> tuple[list[str], list[str]]:
    """差分のパスと、削除された行にだけ現れる識別子を返す。"""
    paths = [p for p in git(root, "diff", "--name-only", "--no-renames", ref, "HEAD").stdout.splitlines() if p]
    removed, added = set(), set()
    for line in git(root, "diff", "--no-renames", "-U0", ref, "HEAD").stdout.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.update(_TOKEN_RE.findall(line[1:]))
        elif line.startswith("+"):
            added.update(_TOKEN_RE.findall(line[1:]))
    idents = sorted(t for t in removed - added if _is_identifier(t))
    return paths, idents


def _path_needles(root, paths: list[str]) -> dict[str, str]:
    """検索語 → 元のパス。基名はリポジトリの中で 1 つに決まるときだけ使う。"""
    files = git(root, "ls-files", check=False).stdout.splitlines()
    counts: dict[str, int] = {}
    for f in files:
        b = f.rsplit("/", 1)[-1]
        counts[b] = counts.get(b, 0) + 1
    out = {}
    for p in paths:
        out[p] = p
        b = p.rsplit("/", 1)[-1]
        if len(b) >= _MIN_TOKEN and counts.get(b, 0) <= 1:
            out.setdefault(b, p)
    return out


def _mentions(text: str, term: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text) is not None


def _mentions_path(text: str, path: str) -> bool:
    """パスが、より長いパスの一部としてでなく現れるか（`README.md` は `docs/README.md` に当てない）。"""
    return re.search(r"(?<![\w/.-])" + re.escape(path) + r"(?![\w/-])", text) is not None


def _still_present(root, term: str) -> bool:
    return git(root, "grep", "-q", "-F", "-e", term, "HEAD", check=False).returncode == 0


class _Routes:
    """open の課題ごとに、拾った経路と手がかりの語を集める。"""

    def __init__(self, open_issues):
        self.by_num = {i["number"]: i for i in open_issues}
        self.routes: dict[int, set] = {}
        self.terms: dict[int, set] = {}

    def add(self, n, route, term=None):
        if n in self.by_num:
            self.routes.setdefault(n, set()).add(route)
            if term:
                self.terms.setdefault(n, set()).add(term)


def _route_diff(open_issues, root, since_ref, routes):
    paths, idents = diff_terms(root, since_ref)
    needles = _path_needles(root, paths)
    gone: dict[str, bool] = {}
    for i in open_issues:
        text = f"{i.get('title') or ''}\n{i.get('body') or ''}"
        for needle in needles:
            if _mentions_path(text, needle):
                routes.add(i["number"], "diff-path", needle)
        for t in idents:
            if _mentions(text, t):
                if t not in gone:
                    gone[t] = not _still_present(root, t)
                if gone[t]:
                    routes.add(i["number"], "diff-identifier", t)
    return paths, idents


def _route_milestones(gh, repo, open_issues, since, routes, notes):
    milestones = gh.call([f"repos/{repo}/milestones?state=all&per_page=100"], paginate=True) or []
    closed = [c for c in list_issues(gh, f"state=closed&since={since}") if (c.get("closed_at") or "") >= since]
    empty_milestones = []
    if milestones:
        for i in open_issues:
            if not i.get("milestone"):
                routes.add(i["number"], "no-milestone")
        touched = {c["milestone"]["title"] for c in closed if c.get("milestone")}
        for i in open_issues:
            if i.get("milestone") and i["milestone"]["title"] in touched:
                routes.add(i["number"], "closed-milestone", i["milestone"]["title"])
        open_titles = {i["milestone"]["title"] for i in open_issues if i.get("milestone")}
        empty_milestones = sorted(t for t in touched if t not in open_titles)
    else:
        notes.append("マイルストーンが無いため no-milestone と closed-milestone と unscored を飛ばした")
    return closed, empty_milestones, bool(milestones)


def _route_sub_issues(gh, repo, closed, open_issues, routes, notes):
    closed_nums = {c["number"] for c in closed}
    sub_api = True
    for c in closed:
        if not (c.get("sub_issues_summary") or {}).get("total") or not sub_api:
            continue
        kids = _sub_issue_kids(gh, c["number"])
        if kids is None:
            sub_api = False
            notes.append("サブイシューの API が無いため本文の参照だけで子を拾った")
            continue
        for k in kids:
            if k.get("state") == "open":
                routes.add(k["number"], "sub-issue", f"#{c['number']}")
    for i in open_issues:
        for m in re.finditer(r"親[^\n#]{0,20}#(\d+)\b", i.get("body") or ""):
            if int(m.group(1)) in closed_nums:
                routes.add(i["number"], "sub-issue", f"#{m.group(1)}")


def _route_commits(root, since_ref, routes):
    for line in git(root, "log", "--no-merges", "--format=%s", f"{since_ref}..HEAD").stdout.splitlines():
        for m in re.finditer(r"#(\d+)\b", line):
            routes.add(int(m.group(1)), "commit-subject", line)


def _route_manual(a, routes, notes):
    if a.all:
        for n in routes.by_num:
            routes.add(n, "all")
    for n in a.add:
        if n not in routes.by_num:
            notes.append(f"--add の #{n} は open でないため除いた")
        routes.add(n, "manual")


def _candidate_result(a, repo, since, open_issues, closed, paths, idents, routes, empty_milestones, notes, waits):
    found = routes.routes
    # コミットの件名が指す課題は直っている見込みが高いため、上限で切るときも先に残す
    order = sorted(found, key=lambda n: ("commit-subject" not in found[n], -len(found[n] - {"unscored"}), n))
    keep = order if a.limit is None else order[: a.limit]
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
    repo = target_repo(root, a.repo)
    gh = Gh(repo)
    since = _ref_date(root, a.since_ref)
    open_issues = list_issues(gh, "state=open")
    routes = _Routes(open_issues)
    notes = []

    paths, idents = _route_diff(open_issues, root, a.since_ref, routes)
    closed, empty_milestones, has_milestones = _route_milestones(gh, repo, open_issues, since, routes, notes)
    _route_sub_issues(gh, repo, closed, open_issues, routes, notes)
    _route_commits(root, a.since_ref, routes)
    _route_manual(a, routes, notes)
    sd = _state_dir(a.state_dir, repo)
    for n in RC.unscored(sd, _read_state, open_issues, snapshot_digest) if has_milestones else []:
        routes.add(n, "unscored")

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
        "round": {
            "applied": sorted(set(prev.get("applied", [])) | set(buckets["applied"])),
            "closed": sorted(set(prev.get("closed", [])) | set(closed)),
            "waits": prev.get("waits", []) + waits,
            "runs": prev.get("runs", 0) + 1,
        },
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
    plan = _load_plan(a.plan)
    repo = a.repo or plan.get("repo") or target_repo(root, None)
    sd = _state_dir(a.state_dir, repo)
    ledger_path = sd / "ledger.json"
    ledger = _read_state(ledger_path) or {}
    gh = Gh(repo, max_waits=a.max_waits, max_wait=a.max_wait)
    ms = Milestones(gh)
    buckets = {k: [] for k in ("applied", "skipped_changed", "unchanged", "already", "needs_approval", "returned", "failed", "pending")}
    items, partial, why_partial = [], False, ""
    rp = RC.RankPlan(plan, sd, _read_state)
    actions = [] if rp.stale else plan["actions"]  # rank.json と合わない plan は何も書かない
    for idx, act in enumerate(actions):
        n, verdict = act["number"], act["verdict"]
        key = _ledger_key(repo, act)

        def record(value, key=key):
            ledger[key] = value
            jsonio.write_atomic(ledger_path, ledger, indent=1)

        try:
            bucket, reason = _apply_one(gh, ms, repo, act, ledger.get(key), record, rp.hold(act))
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

    # 同じ回（前の candidates 以降）の打ち直しを足し合わせ、承認後・partial 後の報告から前の反映と待ちを落とさない
    prev = ((_read_state(sd / "apply.json") or {}).get("metrics") or {}).get("round") or {}
    status, code, summary, metrics, pres, nxt = _apply_outcome(repo, actions, buckets, partial, why_partial, prev, gh.waits, rp)
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
    c.add_argument("--all", action="store_true")
    c.add_argument("--add", type=_numbers, default=[], help="担当が足す課題の番号（12,34）")
    c.add_argument("--limit", type=int, default=None, help="1 回に扱う候補の上限")
    c.set_defaults(func=cmd_candidates)
    p = sub.add_parser("apply", parents=[common])
    p.add_argument("--plan", required=True)
    p.add_argument("--max-waits", type=int, default=DEFAULT_MAX_WAITS)
    p.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT, help="1 回の待ちの上限（秒）")
    p.set_defaults(func=cmd_apply)
    ctx = argparse.Namespace(tool=TOOL, state_dir=_state_dir, read_state=_read_state)
    r = sub.add_parser("report", parents=[common])
    r.set_defaults(func=lambda a: upkeep_report.cmd_report(a, ctx))
    RC.add_parser(sub, common, _numbers, lambda a: RC.cmd_rank(a, ctx))
    return ap


def main(argv=None):
    deps.require("durable")
    main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
