#!/usr/bin/env python3
"""upkeep.py: issue-upkeep の手順 1（候補の収集）と手順 3（反映）の決まった手順。

    python3 upkeep.py candidates --since-ref <ref> [--all] [--add 12,34] [--limit N]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py apply --plan plan.json [--max-waits N] [--max-wait 秒]
                      [--repo owner/name] [--state-dir <dir>] [--root <dir>]
    python3 upkeep.py report [--repo owner/name] [--state-dir <dir>] [--root <dir>]

判定（手順 2A / 2B）は持たない。LLM が candidates の結果を読んで判定し、plan.json に書く。

candidates: 手順 1 の経路のうち機械で集められるものを集め、重複を除いて件数とともに返す。
  経路は diff-path（差分のパス）/ diff-identifier（削除された識別子）/ no-milestone /
  closed-milestone（閉じた課題のマイルストーン）/ sub-issue（閉じた親の子）/
  commit-subject（<ref>..HEAD のコミットの件名が #番号で指す）/ all（--all）/
  manual（--add で担当が足したもの）。commit-subject の候補は、上限で切るときも先に残す。候補ごとに updated_at と本文の要約値を返す。
  --limit で 1 回に扱う件数に上限を置く。超えた分は items に載せず、metrics.deferred に番号だけを返す（終了コード 20）。
apply: plan.json の変更を反映する。反映の直前に updated_at を照合し、変わっていれば本文の
  要約値を比べ、それも変わっていれば飛ばす（skipped_changed）。済んだものは記録に記録して
  2 度書かない。上限に当たれば Retry-After / 回復時刻 / 倍々の順で待ち、--max-waits を
  超えたら部分的に終わった状態で止める。
report: candidates と apply の記録から完了報告の値を返す。

plan.json の形:

    {"repo": "owner/name",
     "actions": [{"number": 12, "verdict": "追記が要る",
                  "updated_at": "<candidates の値>", "digest": "<candidates の値>",
                  "changes": {"body": "...", "title": "...", "milestone": "<題名>" | null,
                              "add_labels": ["..."], "remove_labels": ["..."],
                              "state": "closed", "state_reason": "completed" | "not_planned"},
                  "approved": false}]}

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
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "lib"
sys.path.insert(0, str(_LIB))
import post_queue  # noqa: E402
from step_result import (EXIT_PAUSE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError,  # noqa: E402
                         approval_present, common_parser, emit, git, git_root, main_with,
                         result)

TOOL = "issue-upkeep"

VERDICTS = ("そのまま", "追記が要る", "書き直しが要る", "閉じてよい", "やらない", "重複",
            "ルートコーズ", "要判断")
# 承認を得てから反映する判定。承認の無いものは needs_approval へ回す。
NEEDS_APPROVAL = ("やらない",)
# 反映しない判定。人へ返す。
RETURNED = ("要判断",)

ROUTES = ("diff-path", "diff-identifier", "no-milestone", "closed-milestone", "sub-issue",
          "commit-subject", "all", "manual")

# 待ちの既定。倍々の起点は、作成の二次的な制限で実測した 60 秒の間隔に合わせる。
DOUBLING_START = 60.0
DEFAULT_MAX_WAITS = 8
DEFAULT_MAX_WAIT = 900.0

# 削除された識別子として拾う語の形。短い語や記号を含まない語は、ありふれた単語と区別できない。
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*[A-Za-z0-9_]")
_MIN_TOKEN = 6


# ---------------- 小関数 ----------------

def digest(body) -> str:
    """本文の要約値。改行の違いと行末の空白は同じとみなす。"""
    text = (body or "").replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_identifier(tok: str) -> bool:
    if len(tok) < _MIN_TOKEN:
        return False
    return any(c in tok for c in "_-.") or bool(re.search(r"[a-z][A-Z]", tok))


def _decode_concat(text: str) -> list:
    """`gh api --paginate` が出す、連結された JSON 配列を 1 つの列にする。"""
    out, dec, i, text = [], json.JSONDecoder(), 0, text or ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        obj, i = dec.raw_decode(text, i)
        out.extend(obj if isinstance(obj, list) else [obj])


def _split_include(stdout: str) -> tuple[dict, str]:
    """`gh api -i` の出力をヘッダ（小文字のキー）と本文に分ける。"""
    if not stdout.startswith("HTTP/"):
        return {}, stdout
    parts = re.split(r"\r?\n\r?\n", stdout, maxsplit=1)
    headers = {}
    for line in parts[0].splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers, parts[1] if len(parts) > 1 else ""


def _state_dir(arg, repo: str) -> Path:
    base = arg or os.environ.get("NDF_UPKEEP_STATE_DIR") or str(
        Path(tempfile.gettempdir()) / "ndf" / "issue-upkeep")
    d = Path(base) / repo.replace("/", "--")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as e:
        raise StepError(f"{path} を読めない: {e}", EXIT_UNREADABLE)


# ---------------- gh の呼び出しと待ち ----------------

class Partial(Exception):
    """待ちの回数か長さが上限を超えた。"""


class Gh:
    """gh api を呼ぶ。上限に当たれば応答から待つ長さを決めて待ち、再実行する。"""

    def __init__(self, repo: str, max_waits: int = DEFAULT_MAX_WAITS,
                 max_wait: float = DEFAULT_MAX_WAIT, sleep=None, now=None):
        self.repo = repo
        self.max_waits = max_waits
        self.max_wait = max_wait
        self.sleep = sleep or (lambda s: None if os.environ.get("NDF_UPKEEP_NO_SLEEP") else time.sleep(s))
        self.now = now or time.time
        self.waits: list[dict] = []
        self._doubling = 0.0

    def _wait_for(self, headers: dict) -> tuple[float, str]:
        ra = headers.get("retry-after")
        if ra and ra.strip().isdigit():
            return float(ra), "retry-after"
        reset = headers.get("x-ratelimit-reset")
        if headers.get("x-ratelimit-remaining") == "0" and reset and reset.isdigit():
            return max(1.0, float(reset) - self.now()), "reset"
        self._doubling = self._doubling * 2 if self._doubling else DOUBLING_START
        return self._doubling, "doubling"

    def call(self, args: list[str], stdin: str | None = None, target=None, paginate=False):
        """`gh api <args>` を呼び、本文の JSON を返す。失敗は StepError。"""
        cmd = ["gh", "api", *(["--paginate"] if paginate else ["-i"]), *args]
        while True:
            a = post_queue.run(cmd, stdin=stdin)
            headers, body = _split_include(a.stdout)
            att = post_queue.Attempt(a.code, body, a.stderr)
            if att.ok:
                self._doubling = 0.0
                if not body.strip():
                    return None
                try:
                    return _decode_concat(body) if paginate else json.loads(body)
                except ValueError:
                    raise StepError(f"gh api {' '.join(args)} の出力を読めない", EXIT_UNREADABLE)
            if not post_queue.is_rate_limited(att):
                raise StepError(f"gh api {' '.join(args)} が失敗: {att.summary()}")
            seconds, why = self._wait_for(headers)
            if len(self.waits) >= self.max_waits or seconds > self.max_wait:
                raise Partial(f"待ちが上限を超えた（{len(self.waits)} 回・次は {seconds:g} 秒）")
            self.waits.append({"number": target, "seconds": round(seconds, 1), "why": why})
            print(f"⏳ 上限のため {seconds:g} 秒待つ（{why}）: gh api {' '.join(args)}",
                  file=sys.stderr)
            self.sleep(seconds)


def _repo(root, arg) -> str:
    if arg:
        return arg
    p = post_queue.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if not p.ok or not p.stdout.strip():
        raise StepError(f"リポジトリを決められない（--repo を渡す）: {p.summary()}", EXIT_PRECONDITION)
    return p.stdout.strip()


# ---------------- candidates ----------------

def _issues(gh: Gh, query: str) -> list[dict]:
    rows = gh.call([f"repos/{gh.repo}/issues?{query}&per_page=100"], paginate=True) or []
    return [r for r in rows if isinstance(r, dict) and not r.get("pull_request")]


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


def cmd_candidates(a):
    root = git_root(a.root)
    repo = _repo(root, a.repo)
    gh = Gh(repo)
    since = _ref_date(root, a.since_ref)
    open_issues = _issues(gh, "state=open")
    by_num = {i["number"]: i for i in open_issues}
    routes: dict[int, set] = {}
    terms: dict[int, set] = {}
    notes = []

    def hit(n, route, term=None):
        if n in by_num:
            routes.setdefault(n, set()).add(route)
            if term:
                terms.setdefault(n, set()).add(term)

    paths, idents = diff_terms(root, a.since_ref)
    needles = _path_needles(root, paths)
    gone: dict[str, bool] = {}
    for i in open_issues:
        text = f"{i.get('title') or ''}\n{i.get('body') or ''}"
        for needle in needles:
            if _mentions_path(text, needle):
                hit(i["number"], "diff-path", needle)
        for t in idents:
            if _mentions(text, t):
                if t not in gone:
                    gone[t] = not _still_present(root, t)
                if gone[t]:
                    hit(i["number"], "diff-identifier", t)

    milestones = gh.call([f"repos/{repo}/milestones?state=all&per_page=100"], paginate=True) or []
    closed = [c for c in _issues(gh, f"state=closed&since={since}") if (c.get("closed_at") or "") >= since]
    empty_milestones = []
    if milestones:
        for i in open_issues:
            if not i.get("milestone"):
                hit(i["number"], "no-milestone")
        touched = {c["milestone"]["title"] for c in closed if c.get("milestone")}
        for i in open_issues:
            if i.get("milestone") and i["milestone"]["title"] in touched:
                hit(i["number"], "closed-milestone", i["milestone"]["title"])
        open_titles = {i["milestone"]["title"] for i in open_issues if i.get("milestone")}
        empty_milestones = sorted(t for t in touched if t not in open_titles)
    else:
        notes.append("マイルストーンが無いため no-milestone と closed-milestone を飛ばした")

    closed_nums = {c["number"] for c in closed}
    sub_api = True
    for c in closed:
        if not (c.get("sub_issues_summary") or {}).get("total") or not sub_api:
            continue
        try:
            kids = gh.call([f"repos/{repo}/issues/{c['number']}/sub_issues?per_page=100"], paginate=True) or []
        except StepError as e:
            if "404" in str(e):
                sub_api = False
                notes.append("サブイシューの API が無いため本文の参照だけで子を拾った")
                continue
            raise
        for k in kids:
            if k.get("state") == "open":
                hit(k["number"], "sub-issue", f"#{c['number']}")
    for i in open_issues:
        for m in re.finditer(r"親[^\n#]{0,20}#(\d+)\b", i.get("body") or ""):
            if int(m.group(1)) in closed_nums:
                hit(i["number"], "sub-issue", f"#{m.group(1)}")

    for line in git(root, "log", "--no-merges", "--format=%s", f"{a.since_ref}..HEAD").stdout.splitlines():
        for m in re.finditer(r"#(\d+)\b", line):
            hit(int(m.group(1)), "commit-subject", line)

    if a.all:
        for n in by_num:
            hit(n, "all")
    for n in a.add:
        if n not in by_num:
            notes.append(f"--add の #{n} は open でないため除いた")
        hit(n, "manual")

    # コミットの件名が指す課題は直っている見込みが高いため、上限で切るときも先に残す
    order = sorted(routes, key=lambda n: ("commit-subject" not in routes[n], -len(routes[n]), n))
    keep = order if a.limit is None else order[:a.limit]
    deferred = [n for n in order if n not in set(keep)]
    items = []
    # 上限を超えた候補は items に載せない（metrics.deferred にだけ並べる）。載せると
    # 判定の対象として求められ、上限が効かない。
    for n in keep:
        i = by_num[n]
        items.append({"kind": "issue", "name": f"#{n}", "result": "candidate",
                      "number": n, "title": i.get("title") or "",
                      "routes": sorted(routes[n], key=ROUTES.index),
                      "terms": sorted(terms.get(n, ())),
                      "updated_at": i.get("updated_at"), "digest": digest(i.get("body"))})
    for t in empty_milestones:
        items.append({"kind": "milestone", "name": t, "result": "no-open-issue"})
    by_route = {r: sum(1 for n in keep if r in routes[n]) for r in ROUTES}
    metrics = {"since_ref": a.since_ref, "since": since, "repo": repo, "open": len(open_issues),
               "candidates": len(keep), "deferred": deferred, "by_route": by_route,
               "closed_since": len(closed), "paths": len(paths), "identifiers": len(idents),
               "notes": notes, "waits": gh.waits}
    summary = (f"候補 {len(keep)} 件（open {len(open_issues)} 件中）: "
               + "・".join(f"{r} {c}" for r, c in by_route.items() if c)
               + (f"。上限 {a.limit} 件を超えた {len(deferred)} 件は次の回へ" if deferred else ""))
    out = result(TOOL, "gate" if deferred else "ok", summary, items, metrics,
                 next=(f"上限 {a.limit} 件を超えた {len(deferred)} 件（deferred）は、次の回に"
                       " --add で渡すか --limit を上げる" if deferred else None))
    _write_json(_state_dir(a.state_dir, repo) / "candidates.json", out)
    emit(out, EXIT_PAUSE if deferred else None)


# ---------------- apply ----------------

def _load_plan(path: str) -> dict:
    plan = _read_json(Path(path))
    if plan is None:
        raise StepError(f"plan が無い: {path}", EXIT_PRECONDITION)
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise StepError("plan は {\"actions\": [...]} の形で書く", EXIT_UNREADABLE)
    errs = []
    for k, act in enumerate(plan["actions"]):
        if not isinstance(act, dict) or not isinstance(act.get("number"), int):
            errs.append(f"actions[{k}] に number が無い")
            continue
        if act.get("verdict") not in VERDICTS:
            errs.append(f"#{act['number']} の verdict は {' / '.join(VERDICTS)} のどれか")
        if not act.get("updated_at") or not act.get("digest"):
            errs.append(f"#{act['number']} に updated_at と digest が無い（照合できない）")
        ch = act.get("changes", {})
        if not isinstance(ch, dict):
            errs.append(f"#{act['number']} の changes はオブジェクトで書く")
    if errs:
        raise StepError("plan の誤り: " + " / ".join(errs), EXIT_UNREADABLE)
    return plan


def _ledger_key(repo: str, act: dict) -> str:
    ch = json.dumps(act.get("changes") or {}, ensure_ascii=False, sort_keys=True)
    return f"{repo}#{act['number']}:{hashlib.sha256(ch.encode('utf-8')).hexdigest()[:12]}"


class Milestones:
    def __init__(self, gh: Gh):
        self.gh, self._by_title = gh, None

    def number(self, title: str, target) -> int:
        if self._by_title is None:
            rows = self.gh.call([f"repos/{self.gh.repo}/milestones?state=all&per_page=100"],
                                target=target, paginate=True) or []
            self._by_title = {r["title"]: r["number"] for r in rows}
        if title not in self._by_title:
            made = self.gh.call([f"repos/{self.gh.repo}/milestones", "-X", "POST", "--input", "-"],
                                stdin=json.dumps({"title": title}), target=target)
            self._by_title[title] = made["number"]
        return self._by_title[title]


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


def cmd_apply(a):
    root = git_root(a.root)
    plan = _load_plan(a.plan)
    repo = a.repo or plan.get("repo") or _repo(root, None)
    sd = _state_dir(a.state_dir, repo)
    ledger_path = sd / "ledger.json"
    ledger = _read_json(ledger_path) or {}
    gh = Gh(repo, max_waits=a.max_waits, max_wait=a.max_wait)
    ms = Milestones(gh)
    buckets = {k: [] for k in ("applied", "skipped_changed", "unchanged", "already", "needs_approval",
                               "returned", "failed", "pending")}
    items, partial, why_partial = [], False, ""
    actions = plan["actions"]
    for idx, act in enumerate(actions):
        n, verdict, ch = act["number"], act["verdict"], act.get("changes") or {}
        key = _ledger_key(repo, act)

        def put(bucket, reason=""):
            buckets[bucket].append(n)
            it = {"kind": "issue", "name": f"#{n}", "result": bucket, "verdict": verdict}
            if reason:
                it["reason"] = reason
            items.append(it)

        if verdict in RETURNED:
            put("returned", "要判断は反映しない")
            continue
        if key in ledger:
            put("already", "記録にある")
            continue
        if verdict in NEEDS_APPROVAL and not act.get("approved"):
            put("needs_approval", "やらないは承認を得てから反映する")
            continue
        try:
            cur = gh.call([f"repos/{repo}/issues/{n}"], target=n)
            if cur.get("updated_at") != act["updated_at"] and digest(cur.get("body")) != act["digest"]:
                put("skipped_changed", f"updated_at {act['updated_at']} → {cur.get('updated_at')}・本文も変わった")
                continue
            patch, add, remove = _diff(cur, ch, ms, n)
            if not (patch or add or remove):
                ledger[key] = {"result": "unchanged"}
                _write_json(ledger_path, ledger)
                put("unchanged", "変える内容が無い")
                continue
            if patch:
                gh.call([f"repos/{repo}/issues/{n}", "-X", "PATCH", "--input", "-"],
                        stdin=json.dumps(patch, ensure_ascii=False), target=n)
            if add:
                gh.call([f"repos/{repo}/issues/{n}/labels", "-X", "POST", "--input", "-"],
                        stdin=json.dumps({"labels": add}, ensure_ascii=False), target=n)
            for lb in remove:
                gh.call([f"repos/{repo}/issues/{n}/labels/{lb}", "-X", "DELETE"], target=n)
            ledger[key] = {"result": "applied", "fields": sorted(patch),
                           "add_labels": add, "remove_labels": remove}
            _write_json(ledger_path, ledger)
            put("applied", ", ".join(sorted(patch) + [f"+{x}" for x in add] + [f"-{x}" for x in remove]))
        except Partial as e:
            partial, why_partial = True, str(e)
            for rest in actions[idx:]:
                buckets["pending"].append(rest["number"])
                items.append({"kind": "issue", "name": f"#{rest['number']}", "result": "pending",
                              "verdict": rest["verdict"]})
            break
        except StepError as e:
            put("failed", str(e))

    closed = [act["number"] for act in actions
              if act["number"] in buckets["applied"] and (act.get("changes") or {}).get("state") == "closed"]
    metrics = {**buckets, "closed": closed, "waits": gh.waits, "partial": partial,
               "verdicts": {v: sum(1 for act in actions if act["verdict"] == v) for v in VERDICTS}}
    summary = (f"反映 {len(buckets['applied'])} 件（閉じた {len(closed)} 件）・照合で飛ばした "
               f"{len(buckets['skipped_changed'])} 件・変更なし {len(buckets['unchanged'])} 件・済み "
               f"{len(buckets['already'])} 件・承認待ち {len(buckets['needs_approval'])} 件・返した "
               f"{len(buckets['returned'])} 件・失敗 {len(buckets['failed'])} 件・待ち {len(gh.waits)} 回"
               + (f"。部分的に終えた（{why_partial}）" if partial else ""))
    status, code, nxt, pres = "ok", None, None, None
    if buckets["failed"]:
        status, code = "stopped", 1
    elif buckets["needs_approval"]:
        status, code = "gate", 10
        pres = approval_present(
            TOOL, repo.replace("/", "--") + "-no-work",
            title="「やらない」で閉じる課題の承認",
            targets=[{"url": f"https://github.com/{repo}/issues/{x}"} for x in buckets["needs_approval"]],
            change=f"{len(buckets['needs_approval'])} 件を wontfix で閉じる",
            judge=[(f"#{act['number']}", (act.get("changes") or {}).get("body", "")[:300])
                   for act in actions if act["number"] in buckets["needs_approval"]],
            consent=[f"#{x} を「やらない」で閉じる" for x in buckets["needs_approval"]],
            rollback="閉じた課題を reopen し、wontfix を外す（本文は GitHub の編集履歴から戻せる）")
        nxt = "承認を得た課題に \"approved\": true を付けて同じ plan で apply を打ち直す（済んだものは記録で飛ぶ）"
    elif partial or buckets["skipped_changed"]:
        status, code = "gate", EXIT_PAUSE
        parts = []
        if buckets["skipped_changed"]:
            parts.append("照合で飛ばした " + " ".join(f"#{x}" for x in buckets["skipped_changed"])
                         + " を手順 2A へ戻す")
        if partial:
            parts.append("時間を置いて同じ plan で apply を打ち直す（済んだものは記録で飛ぶ）")
        nxt = "。".join(parts)
    out = result(TOOL, status, summary, items, metrics, presentation_path=pres, next=nxt)
    _write_json(sd / "apply.json", out)
    emit(out, code)


# ---------------- report ----------------

def cmd_report(a):
    root = git_root(a.root)
    repo = _repo(root, a.repo)
    sd = _state_dir(a.state_dir, repo)
    cand, app = _read_json(sd / "candidates.json"), _read_json(sd / "apply.json")
    if cand is None and app is None:
        raise StepError(f"記録が無い（candidates も apply もまだ打っていない）: {sd}", EXIT_PRECONDITION)
    items, metrics = [], {"repo": repo}
    if cand:
        cm = cand["metrics"]
        metrics.update({"targets": cm["candidates"], "by_route": cm["by_route"],
                        "deferred": cm["deferred"], "notes": cm.get("notes", []),
                        "empty_milestones": [i["name"] for i in cand["items"] if i["kind"] == "milestone"]})
        items.append({"kind": "section", "name": "対象", "result": "ok",
                      "value": f"{cm['candidates']} 件（" + "・".join(
                          f"{r} {c}" for r, c in cm["by_route"].items() if c) + "）"
                      if cm["candidates"] else "0 件のため飛ばした"})
    if app:
        am = app["metrics"]
        waits = am.get("waits", [])
        metrics.update({"verdicts": am["verdicts"], "applied": len(am["applied"]),
                        "closed": len(am["closed"]), "returned": len(am["returned"]),
                        "skipped_changed": am["skipped_changed"], "needs_approval": am["needs_approval"],
                        "failed": am["failed"], "pending": am["pending"], "partial": am["partial"],
                        "wait_count": len(waits),
                        "wait_seconds": round(sum(w["seconds"] for w in waits), 1)})
        items += [
            {"kind": "section", "name": "判定の内訳", "result": "ok",
             "value": "・".join(f"{v} {c}" for v, c in am["verdicts"].items() if c)},
            {"kind": "section", "name": "反映", "result": "partial" if am["partial"] else "ok",
             "value": f"直した {len(am['applied']) - len(am['closed'])} 件・閉じた {len(am['closed'])} 件・"
                      f"返した {len(am['returned'])} 件"},
            {"kind": "section", "name": "待った回数", "result": "ok",
             "value": f"{len(waits)} 回・計 {metrics['wait_seconds']:g} 秒"}]
    summary = " / ".join(f"{i['name']}: {i['value']}" for i in items)
    emit(result(TOOL, "ok", summary, items, metrics))


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
    r = sub.add_parser("report", parents=[common])
    r.set_defaults(func=cmd_report)
    return ap


def main(argv=None):
    main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
