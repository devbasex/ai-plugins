"""upkeep_routes.py: upkeep.py candidates の経路（候補を拾う決まった手順）。

差分の棚卸し（`--depth diff`）は diff-path・diff-identifier・sub-issue・commit-subject・manual・unscored を、見直し
（`--depth review`）はそれに no-milestone・closed-milestone と後回しを除く open の全件（all）を足して拾う（#2315）。
"""

from __future__ import annotations

import re

from step_result import git
from upkeep_gh import _sub_issue_kids, list_issues

# 削除された識別子として拾う語の形。短い語や記号を含まない語は、ありふれた単語と区別できない。
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*[A-Za-z0-9_]")
_MIN_TOKEN = 6



def _is_identifier(tok: str) -> bool:
    if len(tok) < _MIN_TOKEN:
        return False
    return any(c in tok for c in "_-.") or bool(re.search(r"[a-z][A-Z]", tok))


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


def _route_milestones(gh, repo, open_issues, since, routes, notes, depth="review"):
    """閉じた課題と、空になったマイルストーン。no-milestone と closed-milestone の経路は見直しだけが拾う（#2315 の決定 3）。"""
    milestones = gh.call([f"repos/{repo}/milestones?state=all&per_page=100"], paginate=True) or []
    closed = [c for c in list_issues(gh, f"state=closed&since={since}") if (c.get("closed_at") or "") >= since]
    empty_milestones = []
    if milestones and depth == "diff":
        open_titles = {i["milestone"]["title"] for i in open_issues if i.get("milestone")}
        touched = {c["milestone"]["title"] for c in closed if c.get("milestone")}
        empty_milestones = sorted(t for t in touched if t not in open_titles)
    elif milestones:
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


def _route_review(open_issues, deferred, routes):
    """見直しの候補: 後回しのマイルストーン（材料の宣言の deferred_milestones）を除く open の全件（I10）。"""
    for i in open_issues:
        if (i.get("milestone") or {}).get("title") in deferred:
            routes.routes.pop(i["number"], None)
        else:
            routes.add(i["number"], "all")


def _route_manual(a, routes, notes):
    if a.all:
        for n in routes.by_num:
            routes.add(n, "all")
    for n in a.add:
        if n not in routes.by_num:
            notes.append(f"--add の #{n} は open でないため除いた")
        routes.add(n, "manual")
