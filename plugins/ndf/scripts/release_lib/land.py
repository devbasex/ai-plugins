"""land.py: 承認した head だけを、ブランチ戦略の宣言の主体と方式で本番ブランチへ入れる（#3672 の I5・I7・I10）。

- `ruleset_mismatch`: セクションの値と本番ブランチの ruleset の食い違い（I10）
- `check_production_pr`: 本番の PR のマージの方式ごとの確かめ（I7）
- `fast_forward_land`: ruleset の無い `strict` で、確かめた先端から本番ブランチが進んでいないときだけ入れる
- `land_approved`: 上を組み、マージかキューへの投入をする。承認した head と今の head が違えば止まる（I5・E10）

GitHub との出入りは `Port` に閉じる（テストは差し替える）。NDF はロックを取らない（決定 7）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

TRAILER_PR = "NDF-Landed-PR"
TRAILER_HEAD = "NDF-Approved-Head"
TRAILER_TIP = "NDF-Tip"
BYPASS_OK = ("always", "pull_requests_only")
REAPPROVE = "承認を取り直してください"


def ruleset_mismatch(strategy, facts) -> list[dict]:
    """セクションの値と ruleset の食い違いを I10 の比べ方で並べる。空なら一致。"""
    sec = strategy.section
    method, via, admin = sec["merge"]["method"], sec["approval"]["via"], sec["merge"]["admin"]
    out = []
    if not facts.present:
        if method == "merge-queue":
            out.append({"key": "merge.method", "declared": method, "ruleset": "ruleset が無い（merge queue が無い）"})
        return out
    if (method == "merge-queue") != facts.queue:
        out.append({"key": "merge.method", "declared": method, "ruleset": "merge queue あり" if facts.queue else "merge queue なし"})
    queue_commit = str(facts.queue_merge_method or "").lower()
    if method == "merge-queue" and facts.queue and queue_commit and queue_commit != sec["merge"]["commit"]:
        # キューは自分の merge_method で取り込む。宣言の履歴の作り方（merge.commit）と違えば投入前に止める
        out.append({"key": "merge.commit", "declared": sec["merge"]["commit"], "ruleset": f"merge queue の取り込み方 {queue_commit}"})
    if method == "strict" and not facts.up_to_date:
        out.append({"key": "merge.method", "declared": method, "ruleset": "最新の取り込みを求めない"})
    if admin:
        if facts.bypass not in BYPASS_OK:
            out.append({"key": "merge.admin", "declared": True, "ruleset": f"迂回の可否 {facts.bypass}"})
    elif via == "gate" and facts.approvals > 0:
        out.append({"key": "approval.via", "declared": via, "ruleset": f"承認の数 {facts.approvals}"})
    elif via == "review" and facts.approvals < 1:
        out.append({"key": "approval.via", "declared": via, "ruleset": "承認の数 0"})
    return out


def may_bump(row: dict) -> bool:
    """版上げの PR でありうるか。`bump` が None（版数を比べられない）は版上げとみなす（承認の免除と衝突の検査から外さない）。"""
    return row.get("bump", False) is not False


def check_production_pr(strategy, pr: dict, prod: dict, queue: list[dict]) -> str | None:
    """本番の PR がマージの方式ごとの確かめを通るか。通れば None、落ちれば理由の文。

    `pr` は `head`・`bump`（版上げの PR か。版数を比べられなければ None で、版上げとして確かめる）・`cut_version`（切った時点の本番の版数）・`contains_tip`（head が本番の先端を含むか）、
    `prod` は `tip`・`version`、`queue` はキューの PR（各 `number`・`bump`）と、承認したがまだキューへ入れていない版上げの PR
    （`approved_release` が真の行）。`merge-queue` では、後者がある間は版上げでない本番の PR を入れない（決定 18）。
    """
    method = strategy.section["merge"]["method"]
    if method == "direct":
        return None
    held = [q for q in queue if q.get("approved_release") and q.get("number") != pr.get("number")]
    if method == "merge-queue" and pr.get("bump") is not True and held:
        # 承認の範囲を版上げの PR の head に固定する（利用者の判断 3。決定 18）。版上げの PR がキューへ入るまで待つ
        return f"版上げの PR #{held[0]['number']} を承認してからキューへ入れるまで、スプリントの PR はキューへ入れない。入った後に打ち直してください"
    if method == "strict":
        if not pr.get("contains_tip"):
            return f"head が本番ブランチの先端 {prod.get('tip', '')[:12]} を含まない。本番の PR を作り直し、{REAPPROVE}"
        return None
    if not may_bump(pr):
        return None
    if pr.get("cut_version") != prod.get("version"):
        return (
            f"本番ブランチの版数 {prod.get('version')} が、版上げの PR を切った時点の {pr.get('cut_version')} と違う。作り直し、{REAPPROVE}"
        )
    others = [q for q in queue if may_bump(q) and not q.get("approved_release") and q.get("number") != pr.get("number")]
    if others:
        return f"キューに別の版上げの PR（#{others[0]['number']}）がある。作り直し、{REAPPROVE}"
    return None


class Port(Protocol):
    """GitHub との出入り（`land_approved` が使う分だけ）。"""

    def pull(self, number: int) -> dict: ...  # {"state", "merged", "head", "base", "bump", "cut_version", "contains_tip"}
    def production(self, branch: str) -> dict: ...  # {"tip", "version"}
    def queue(self, branch: str) -> list[dict]: ...
    def rule_facts(self, branch: str) -> Any: ...
    def merge(self, number: int, sha: str, commit: str, admin: bool) -> tuple[bool, str]: ...
    def enqueue(self, number: int, head: str, jump: bool) -> tuple[bool, str]: ...
    def create_commit(self, message: str, tree_of: str, parents: list[str]) -> str | None: ...
    def update_ref(self, branch: str, sha: str) -> bool: ...
    def close(self, number: int, comment: str) -> bool: ...
    def comment(self, number: int, body: str) -> bool: ...
    def landed_record(self, branch: str, number: int, since: str | None) -> dict | None: ...
    def has_human_record(self, number: int) -> bool: ...
    def checks_failing(
        self, sha: str, pull: dict | None = None
    ) -> str | None: ...  # head の検査がすべて成功なら None、そろわなければ理由の文


def trailers(pr: int, head: str, tip: str) -> str:
    return f"{TRAILER_PR}: {pr}\n{TRAILER_HEAD}: {head}\n{TRAILER_TIP}: {tip}"


def fast_forward_land(port: Port, production: str, pr: int, tip: str, head: str, commit: str, title: str = "") -> dict:
    """確かめた先端 `tip` から本番ブランチが進んでいないときだけ承認した head を入れる（早送りだけの参照の更新）。"""
    if commit == "rebase":
        sha = head
    else:
        parents = [tip, head] if commit == "merge" else [tip]
        message = f"{title or f'Land #{pr}'}\n\n{trailers(pr, head, tip)}\n"
        sha = port.create_commit(message, head, parents)
        if not sha:
            return {"result": "stopped", "commit": None, "reason": "取り込みのコミットを作れない"}
    if not port.update_ref(production, sha):
        return {
            "result": "stopped",
            "commit": sha,
            "reason": f"本番ブランチ {production} が確かめた先端から進んだ（早送りでない）。本番の PR を作り直し、{REAPPROVE}",
        }
    if commit == "squash" and not port.close(pr, f"{sha} で本番ブランチへ入れた（squash）"):
        return {"result": "stopped", "commit": sha, "reason": f"#{pr} を閉じられない。打ち直すと記録から閉じるところへ進む"}
    return {"result": "merged", "commit": sha, "reason": ""}


@dataclass
class Approval:
    """承認の証跡が持つ値。"""

    head: str
    at: str | None = None


def land_approved(port: Port, strategy, pr: int, approval: Approval, pull: dict | None = None, verify_checks: bool = False) -> dict:
    """承認した head を、セクションの主体と方式で本番ブランチへ入れる。

    `pull` は読んだ PR（省けば読む）。`verify_checks` が真なら、入れる前に head の検査の成功を確かめる
    （承認ゲートを通らずに入れるスプリントの PR。ruleset の無い `strict` では常に確かめる）。
    """
    sec = strategy.section
    prod_branch = sec["branches"]["production"]
    merge = sec["merge"]
    pull = pull if pull is not None else port.pull(pr)
    out = {"result": "stopped", "head": pull.get("head"), "reason": ""}
    if pull.get("merged"):
        if not port.has_human_record(pr):
            port.comment(pr, f"<!-- ndf:human-merge -->\n人がマージした先端: {pull.get('head')}")
        return {**out, "result": "human"}
    if merge["actor"] == "human":
        return {**out, "result": "human", "reason": "人のマージを待つ"}
    if pull.get("base") != prod_branch:
        # 早送りは本番ブランチの参照を動かすため、宛先が本番ブランチでない PR を入れない
        return {**out, "reason": f"#{pr} の宛先 {pull.get('base')} が本番ブランチ {prod_branch} でない"}
    if pull.get("head") != approval.head:
        return {**out, "reason": f"承認した head {approval.head[:12]} と今の head {str(pull.get('head'))[:12]} が違う。{REAPPROVE}"}
    facts = port.rule_facts(prod_branch)
    if facts is None:
        return {**out, "reason": f"本番ブランチ {prod_branch} の ruleset を読めない"}
    if merge["method"] == "strict" and not facts.present:
        rec = port.landed_record(prod_branch, pr, approval.at)
        if rec and rec.get("head") == approval.head and rec.get("tree_ok") and rec.get("first_parent_is_tip"):
            if pull.get("state") == "closed" or port.close(pr, f"{rec.get('commit')} で本番ブランチへ入れた"):
                return {**out, "result": "merged"}
    if pull.get("state") == "closed":
        # 取り込みの記録の無い閉じた PR は、承認の後に取り消したものとみなす（参照の更新は PR のマージ API を通らない）
        return {**out, "reason": f"#{pr} はマージされずに閉じている。入れるなら開き直し、{REAPPROVE}"}
    mism = ruleset_mismatch(strategy, facts)
    if mism:
        return {**out, "reason": "セクションと ruleset が食い違う: " + json.dumps(mism, ensure_ascii=False)}
    if merge["method"] != "direct":
        # キューを読むのは merge-queue の確かめに要るときだけ（キューの照会は本番宛ての PR の数だけ git と API を打つ）
        queue = port.queue(prod_branch) if merge["method"] == "merge-queue" else []
        why = check_production_pr(strategy, pull, port.production(prod_branch), queue)
        if why:
            return {**out, "reason": why}
    if verify_checks or (merge["method"] == "strict" and not facts.present):
        # ruleset の無い strict の参照の更新と承認なしの取り込みは、GitHub の必須の検査を通らない
        failing = port.checks_failing(approval.head, pull)
        if failing:
            return {**out, "reason": f"head {approval.head[:12]} の検査がそろっていない: {failing}"}
    if merge["method"] == "merge-queue":
        # 版数を比べられない承認済みの PR（bump が None）も版上げとして先頭へ入れる。後ろへ並ぶと先に並んだ変更が承認の外で入る
        ok, why = port.enqueue(pr, approval.head, may_bump(pull))
        return {**out, "result": "queued" if ok else "stopped", "reason": why}
    if merge["method"] == "strict" and not facts.present:
        # 親は head が含むと確かめた先端に固定する。取り直すと、確かめた後に進んだ先端を親にして間の変更を消す
        tip = pull.get("tip")
        if not tip:
            return {**out, "reason": f"head が含むと確かめた本番ブランチ {prod_branch} の先端を読めない"}
        got = fast_forward_land(port, prod_branch, pr, tip, approval.head, merge["commit"], pull.get("title", ""))
        return {**out, "result": got["result"], "reason": got["reason"]}
    ok, why = port.merge(pr, approval.head, merge["commit"], bool(merge["admin"] and merge["method"] == "direct"))
    return {**out, "result": "merged" if ok else "stopped", "reason": why}


# --- GitHub と手元の git での実装（`release-steps.py land` が使う） ---------------------------------------------

HUMAN_MARK = "<!-- ndf:human-merge -->"
_QUEUE_QUERY = (
    "query($owner: String!, $name: String!, $branch: String!, $after: String) { repository(owner: $owner, name: $name) {"
    " mergeQueue(branch: $branch) { entries(first: 100, after: $after) { pageInfo { hasNextPage endCursor }"
    " nodes { pullRequest { number headRefOid } } } } } }"
)


def manifest_paths(strategy) -> list[str]:
    """版数の置き場が `plugin` のとき、版数を持つ `plugin.json` の候補（測る部品と同じ 2 つの置き場）。"""
    name = strategy.section["release"].get("plugin")
    return [f"plugins/{name}/.claude-plugin/plugin.json", ".claude-plugin/plugin.json"] if name else [".claude-plugin/plugin.json"]


def store_version(strategy, show) -> str | None:
    """`show(path)` で読めるファイルから版数を引く。置き場が `none`・読めないときは None。"""
    import json_pick

    store = strategy.section["release"]["version_store"]
    if store["kind"] == "none":
        return None
    rows = [(p, "/version") for p in manifest_paths(strategy)] if store["kind"] == "plugin" else [(store["path"], store["pointer"])]
    for path, pointer in rows:
        text = show(path)
        if text is None:
            continue
        try:
            value, found = json_pick.resolve_pointer(json.loads(text), pointer)
        except ValueError:
            return None
        return value if found and isinstance(value, str) and value else None
    return None


def parse_landed(log: str, pr: int) -> dict | None:
    """`git log --format=%H%x00%T%x00%P%x00<trailers>%x1e` の出力から、PR の取り込みの記録の最新の 1 件を引く。"""
    for row in log.split("\x1e"):
        parts = row.strip("\n").split("\x00")
        if len(parts) < 4:
            continue
        sha, tree, parents, trail = parts[0].strip(), parts[1], parts[2].split(), parts[3]
        values = dict(line.split(": ", 1) for line in trail.splitlines() if ": " in line)
        if values.get(TRAILER_PR, "").strip() == str(pr):
            return {
                "commit": sha,
                "tree": tree,
                "head": values.get(TRAILER_HEAD, "").strip(),
                "tip": values.get(TRAILER_TIP, "").strip(),
                "first_parent": parents[0] if parents else "",
            }
    return None


class LandError(Exception):
    """GitHub から land に要る事実を読めない。"""


class GhPort:
    """`Port` の GitHub 版。版数と先端の比べは手元の git（origin から取り直す）で、書き込みは gh で行う。"""

    def __init__(self, root, repo: str, strategy) -> None:
        self.root, self.repo, self.strategy = root, repo, strategy

    def _git(self, *args: str):
        import subprocess

        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)

    def _fetch(self, *refspecs: str) -> None:
        self._git("fetch", "--quiet", "origin", *refspecs)

    def _show(self, rev: str):
        return lambda path: (lambda r: r.stdout if r.returncode == 0 else None)(self._git("show", f"{rev}:{path}"))

    def _rest(self, path: str, method: str = "GET", payload: Any = None):
        import gh_call

        resp = gh_call.request(path, method, payload)
        return resp.body if resp.ok and not resp.error else None

    def _bump_facts(self, number: int, head: str, branch: str) -> dict:
        self._fetch(f"+refs/pull/{int(number)}/head:refs/ndf/land/{int(number)}", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        base = self._git("merge-base", head, f"origin/{branch}").stdout.strip()
        cut = store_version(self.strategy, self._show(base)) if base else None
        now = store_version(self.strategy, self._show(head))
        tip = self._git("rev-parse", f"origin/{branch}").stdout.strip()
        tip_in = bool(tip) and self._git("merge-base", "--is-ancestor", tip, head).returncode == 0
        if self.strategy.section["release"]["version_store"]["kind"] == "none":
            bump: bool | None = False
        else:
            # 片側を読めない（版数のファイルの初回の追加・読み取りの失敗）は None。版上げでないとは決めない
            bump = cut != now if cut and now else None
        return {"bump": bump, "cut_version": cut, "contains_tip": tip_in, "tip": tip}

    def pull(self, number: int) -> dict:
        d = self._rest(f"repos/{self.repo}/pulls/{int(number)}")
        if not isinstance(d, dict):
            raise LandError(f"PR #{number} を読めない")
        head, base = str(d["head"]["sha"]).lower(), d["base"]["ref"]
        row = {"number": int(number), "state": d["state"], "merged": bool(d.get("merged")), "head": head, "base": base}
        row["title"] = d.get("title") or ""
        row["head_ref"] = str((d.get("head") or {}).get("ref") or "") or None
        if not row["merged"]:
            row.update(self._bump_facts(number, head, base))
        return row

    def production(self, branch: str) -> dict:
        self._fetch(f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        tip = self._git("rev-parse", f"origin/{branch}").stdout.strip()
        return {"tip": tip, "version": store_version(self.strategy, self._show(tip)) if tip else None}

    def _queue_nodes(self, branch: str) -> list[dict]:
        """merge queue の entries を pageInfo と after で最後まで読む（先頭の 1 ページで切ると、後ろの版上げを衝突の検査から外す）。"""
        import gh_graphql

        owner, _, name = self.repo.partition("/")
        nodes: list[dict] = []
        after = None
        while True:
            a = gh_graphql.graphql(_QUEUE_QUERY, {"owner": owner, "name": name, "branch": branch, "after": after})
            if not a.ok or not isinstance(a.value, dict):
                # 空のキューとみなすと、別の版上げとの衝突を調べずに入れる
                raise LandError(f"本番ブランチ {branch} の merge queue を読めない: {str(a.error)[:300]}")
            entries = ((a.value.get("repository") or {}).get("mergeQueue") or {}).get("entries") or {}
            nodes += entries.get("nodes") or []
            page = entries.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return nodes
            if not page.get("endCursor") or page.get("endCursor") == after:
                raise LandError(f"本番ブランチ {branch} の merge queue を最後まで読めない（次のページの位置が無い）")
            after = page["endCursor"]

    def queue(self, branch: str) -> list[dict]:
        from release_lib import approval_trail as trail

        nodes = self._queue_nodes(branch)
        rows = [{"number": int(n["pullRequest"]["number"]), "head": str(n["pullRequest"]["headRefOid"]).lower()} for n in nodes]
        for r in rows:
            r["bump"] = self._bump_facts(r["number"], r["head"], branch)["bump"]
        queued = {r["number"] for r in rows}
        for n in trail.open_prs_at_base(self.repo, branch):
            if n["number"] in queued or self._bump_facts(n["number"], n["head"], branch)["bump"] is False:
                continue
            got = approval_of(self.repo, self.strategy, n["number"])  # 宣言の承認の受け方（approval.via）で読む
            if got is not None and got.head == str(n["head"]).lower():
                rows.append({"number": n["number"], "bump": True, "approved_release": True})
        return rows

    def rule_facts(self, branch: str) -> Any:
        import release_case

        return release_case.read_rule_facts(self.root, branch)

    def merge(self, number: int, sha: str, commit: str, admin: bool) -> tuple[bool, str]:
        import gh_call

        args = ["pr", "merge", str(int(number)), "--repo", self.repo, f"--{commit}", "--match-head-commit", sha]
        r = gh_call.gh(args + (["--admin"] if admin else []))
        return (True, "") if r.returncode == 0 else (False, (r.stderr or r.stdout).strip()[:300] or "マージできない")

    def enqueue(self, number: int, head: str, jump: bool) -> tuple[bool, str]:
        import gh_graphql

        a = gh_graphql.enqueue_pr(self.repo, number, head, jump)
        return (True, "") if a.ok else (False, f"キューへ入れられない: {str(a.error)[:300]}")

    def create_commit(self, message: str, tree_of: str, parents: list[str]) -> str | None:
        tree = self._git("rev-parse", f"{tree_of}^{{tree}}").stdout.strip()
        d = self._rest(f"repos/{self.repo}/git/commits", "POST", {"message": message, "tree": tree, "parents": parents}) if tree else None
        return d.get("sha") if isinstance(d, dict) else None

    def update_ref(self, branch: str, sha: str) -> bool:
        return self._rest(f"repos/{self.repo}/git/refs/heads/{branch}", "PATCH", {"sha": sha, "force": False}) is not None

    def close(self, number: int, comment: str) -> bool:
        return (
            self.comment(number, comment) and self._rest(f"repos/{self.repo}/pulls/{int(number)}", "PATCH", {"state": "closed"}) is not None
        )

    def comment(self, number: int, body: str) -> bool:
        import gh_rest

        return gh_rest.comment(self.repo, number, body).ok

    def landed_record(self, branch: str, number: int, since: str | None) -> dict | None:
        self._fetch(f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        fmt = f"%H%x00%T%x00%P%x00%(trailers:key={TRAILER_PR},key={TRAILER_HEAD},key={TRAILER_TIP})%x1e"
        r = self._git("log", f"origin/{branch}", f"--format={fmt}", *([f"--since={since}"] if since else ["-n", "200"]))
        rec = parse_landed(r.stdout, number) if r.returncode == 0 else None
        if not rec or not rec["head"]:
            return None
        head_tree = self._git("rev-parse", f"{rec['head']}^{{tree}}").stdout.strip()
        return {**rec, "tree_ok": bool(head_tree) and head_tree == rec["tree"], "first_parent_is_tip": rec["first_parent"] == rec["tip"]}

    def has_human_record(self, number: int) -> bool:
        from release_lib import approval_trail as trail

        return any(HUMAN_MARK in str(c.get("body") or "") for c in trail.read_comments(self.repo, number))

    def checks_failing(self, sha: str, pull: dict | None = None) -> str | None:
        from release_lib import land_checks

        return land_checks.checks_failing(self, sha, pull)


def approval_of(repo: str, strategy, pr: int) -> Approval | None:
    """証跡の書き手（`approval_trail.trail_writer`）に合わせて、承認した head と時刻を読む。無ければ None。"""
    from release_lib import approval_trail as trail

    if strategy.section["approval"]["via"] == "review":
        sha = trail.read_review_commit(repo, pr)
        return Approval(sha) if sha else None
    trails = trail.trails_of(trail.read_comments(repo, pr))
    last = trail.approved_trail(trails, trail.read_roles(repo, [t.login for t in trails if not t.edited])) if trails else None
    return Approval(last.sha, last.created_at) if last else None


def exempt_from_gate(strategy, pull: dict) -> bool:
    """承認の証跡なしで入れてよいスプリントの PR か。trunk（スプリントの PR が本番を宛先にする）で、承認の対象が版上げの PR の
    ときの版上げでない PR だけ。ほかの取り込み方の本番宛ての PR（版数の置き場が無い版上げ・ホットフィックス）は承認ゲートを通す。"""
    sec = strategy.section
    # 版数を比べられない PR（bump が None）は版上げでありうるため免除しない
    return sec["release_strategy"] == "trunk" and sec["approval"]["target"] == "release_pr" and pull.get("bump") is False


def land_pr(root, repo: str, strategy, pr: int) -> dict:
    """`release-steps.py land` の本体。承認の証跡を読み、`land_approved` を GitHub 版の Port で呼ぶ。"""
    from release_lib.approval_trail import TrailError

    port = GhPort(root, repo, strategy)
    try:
        approval = approval_of(repo, strategy, pr)
        if approval is None and strategy.section["merge"]["actor"] != "human":
            pull = port.pull(pr)
            if not pull.get("merged"):
                if exempt_from_gate(strategy, pull):
                    # trunk で承認の対象が版上げの PR なら、スプリントの PR は承認ゲートを通らずに検査の後で入る（決定 8）
                    return land_approved(port, strategy, pr, Approval(str(pull.get("head") or "")), pull=pull, verify_checks=True)
                return {"result": "stopped", "head": pull.get("head"), "reason": f"#{pr} に承認の証跡が無い。{REAPPROVE}"}
        return land_approved(port, strategy, pr, approval or Approval(""))
    except (LandError, TrailError) as e:
        return {"result": "stopped", "head": None, "reason": str(e)}


def land_cli(root, repo: str | None, pr: int) -> dict:
    """`release-steps.py land` の入口。宣言を読めなければ StepError（`branch_strategy` の終了コード）で止める。"""
    import branch_strategy
    import gh_call
    import repo as repo_lib
    from step_result import StepError

    try:
        # メインディレクトリの宣言を読む。作業ツリーの宣言は PR の変更でマージの主体・方式を差し替えられる
        strategy = repo_lib.branch_strategy_of(root)
    except branch_strategy.StrategyError as e:
        raise StepError(e.reason, e.exit_code) from e
    # owner/repo は --root の origin から決める。今のディレクトリから決めると、別のリポジトリの同じ番号の PR を操作し得る
    return land_pr(root, gh_call.resolve_repo(repo, cwd=root) or "", strategy, pr)
