"""リリースブランチの PR のレビュー（#3163 の決定 5・6）。`release-steps.py` の review-open と review-close の本体。

- 衝突の解消の差分は、範囲の first-parent のマージの再マージの差分（`git log --remerge-diff`）である。差分を先頭へ逆に当てた
  tree を土台のコミット（親は先頭）にし、その子に先頭と同じ tree の見せるコミットを置く。土台を宛先・見せるコミットを head に
  した一時の PR（衝突の解消のレビューの PR）の差分が、衝突の解消の差分そのものになる
- `diff: pr` は、土台を範囲の始まり・見せるコミットを先頭そのものにした同じ経路を通る
- レビューの直しは見せるブランチに積まれる。review-close がリリースブランチへ移し（I9）、レビュー済みの先頭を PR の本文へ
  書いてから閉じる（I10）。開いているレビューの PR は版ごとに 1 組までで、直しを移し終えた組だけを閉じて消す（I11）

GitHub の操作は `GitHub`（`gh` の CLI）を通し、テストで差し替えられる。出す（emit）のは呼び手である。
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import branch_flow
from step_result import EXIT_PRECONDITION, StepError, gh_json, git, run

# レビューの PR の本文に書く印（見せる先頭・範囲の終わり・レビュー済みの先頭）
SHOWN_MARK = "<!-- ndf:shown-head {sha} -->"
RANGE_END_MARK = "<!-- ndf:range-end {sha} -->"
REVIEWED_MARK = "<!-- ndf:reviewed-head {sha} -->"
_MARK_SHA = r"([0-9a-f]{7,64})"


def _mark_re(mark: str) -> re.Pattern:
    return re.compile(re.escape(mark).replace(re.escape("{sha}"), _MARK_SHA))


def mark_in_body(body: str | None, mark: str) -> str | None:
    """本文の最後の印の SHA。無ければ None。"""
    found = _mark_re(mark).findall(body or "")
    return found[-1] if found else None


def with_mark(body: str | None, mark: str, sha: str) -> str:
    """本文の印を `sha` に書き換える（無ければ末尾へ足す）。"""
    text = _mark_re(mark).sub("", body or "").rstrip()
    return f"{text}\n\n{mark.format(sha=sha)}\n"


def review_names(version: str) -> tuple[str, str, str]:
    """(リリースブランチ, 見せるブランチ, 土台のブランチ)。"""
    show = f"{branch_flow.REVIEW_PREFIX}{version}"
    return f"{branch_flow.RELEASE_PREFIX}{version}", show, show + branch_flow.REVIEW_BASE_SUFFIX


def review_version_of(show: str) -> str | None:
    """見せるブランチの名前から版。レビューの PR のブランチでなければ None。"""
    if not show.startswith(branch_flow.REVIEW_PREFIX) or show.endswith(branch_flow.REVIEW_BASE_SUFFIX):
        return None
    return show[len(branch_flow.REVIEW_PREFIX) :]


@dataclass
class GitHub:
    """レビューの PR の GitHub の操作（`gh`）。`create` は呼び手（`release-steps.create_pr`）から受ける。"""

    create: object = None

    def list_prs(self, root, state: str, head: str | None = None) -> list[dict]:
        """`state` の PR（`number`・`headRefName`・`body`）。`head` を省くと全ページを読む（取得の上限で見落とした
        レビューの PR の直しを、無いと扱って消さない。I11）。"""
        if head:
            args = ["pr", "list", "--state", state, "--head", head, "--json", "number,headRefName,body", "--limit", "100"]
            return gh_json(root, args, "gh pr list") or []
        path = f"repos/{{owner}}/{{repo}}/pulls?state={state}&per_page=100"
        pages = gh_json(root, ["api", "--paginate", "--slurp", path], "gh api pulls") or []
        return [
            {"number": p["number"], "headRefName": (p.get("head") or {}).get("ref") or "", "body": p.get("body")}
            for page in pages
            for p in page
        ]

    def create_pr(self, root, base: str, head: str, title: str, body: str) -> int:
        return self.create(root, base, head, title, body)

    def edit_body(self, root, n: int, body: str) -> None:
        p = run(["gh", "pr", "edit", str(n), "--body-file", "-"], cwd=root, check=False, input=body)
        if p.returncode != 0:
            raise StepError(f"レビューの PR #{n} の本文を書けない: {p.stderr.strip()[-400:]}")

    def close(self, root, n: int) -> None:
        p = run(["gh", "pr", "close", str(n)], cwd=root, check=False)
        if p.returncode != 0:
            raise StepError(f"レビューの PR #{n} を閉じられない: {p.stderr.strip()[-400:]}")


def _fetch_branch(root, branch: str) -> bool:
    ref = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    return git(root, "fetch", "-q", "origin", ref, check=False).returncode == 0


def _sha(root, ref: str) -> str:
    return git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _remote_has(root, branch: str) -> bool:
    return git(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False).returncode == 0


def delete_branches(root, *branches: str) -> None:
    """origin の一時のブランチを消す（無ければ何もしない）。"""
    for b in branches:
        if _remote_has(root, b):
            git(root, "push", "-q", "origin", "--delete", b)


def pending_fixes(root, upstream: str, head: str, limit: str) -> list[str]:
    """`limit..head` のコミットのうち、`upstream` へまだ移していないもの（古い順。`git cherry` の `+` の行）。"""
    out = git(root, "cherry", upstream, head, limit).stdout.split("\n")
    return [line[2:].strip() for line in out if line.startswith("+ ")]


def remerge_patch(root, frm: str, head: str) -> str:
    """`frm..head` の first-parent のマージの再マージの差分を 1 つのパッチにする（新しいマージが先。逆に当てる順）。
    衝突の無いマージだけなら空文字。"""
    p = git(root, "log", "--first-parent", "--merges", "--remerge-diff", "--format=", f"{frm}..{head}", check=False)
    if p.returncode != 0:
        raise StepError(f"再マージの差分を取り出せない（git 2.36 以降が要る）: {p.stderr.strip()[-400:]}")
    return p.stdout if p.stdout.strip() else ""


def build_review_pair(root, head: str, patch: str) -> tuple[str, str]:
    """パッチを `head` へ逆に当てた土台のコミット（親は `head`）と、その子で `head` と同じ tree の見せるコミットを作る。
    一時の index で組み、呼び手の作業ツリーに触れない。逆に当てられなければ止める（推定で縮めない。I8）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ndf-review-range-"))
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp / "index")}
    try:
        run(["git", "-C", str(root), "read-tree", head], env=env)
        p = run(["git", "-C", str(root), "apply", "-R", "--cached", "-"], env=env, check=False, input=patch)
        if p.returncode != 0:
            raise StepError(
                f"衝突の解消の差分を {head[:12]} へ逆に当てられない（後のマージか直しが同じ行を変えた）: {p.stderr.strip()[-400:]}"
            )
        tree = run(["git", "-C", str(root), "write-tree"], env=env).stdout.strip()
    finally:
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()
    base = git(root, "commit-tree", tree, "-p", head, "-m", "衝突の解消のレビューの土台（自動のマージの結果）").stdout.strip()
    show = git(root, "commit-tree", f"{head}^{{tree}}", "-p", base, "-m", "衝突の解消（リリースブランチの先頭）").stdout.strip()
    return base, show


def review_from(root, version: str, head: str, production: str, gh: GitHub) -> tuple[str, list[dict]]:
    """範囲の始まり。同じ版の閉じたレビューの PR のレビュー済みの先頭が `head` の祖先ならそこ、無ければ本番チャネル（I10）。"""
    _, show, _ = review_names(version)
    closed = sorted(gh.list_prs(root, "closed", head=show), key=lambda it: -int(it.get("number") or 0))
    marks = [m for it in closed if (m := mark_in_body(it.get("body"), REVIEWED_MARK))]
    for sha in marks:
        if branch_flow.ancestor_of(root, sha, head):
            return _sha(root, sha), []
    start = _sha(root, f"origin/{production}")
    if not marks:
        return start, []
    note = {
        "kind": "range",
        "result": "widened",
        "reason": f"前回のレビュー済みの先頭 {marks[0][:12]} が祖先でないため {production} から取る",
    }
    return start, [note]


def close_stale(root, version: str, head: str, gh: GitHub) -> tuple[int | None, list[int]]:
    """開いている衝突の解消のレビューの PR を版に依らず調べ、直しを移し終えたものを閉じて一時のブランチを消す（I11）。

    移していない直しを持つ PR が `version` の PR で、本文の範囲の終わりが `head` と同じなら使い直す（番号を返す）。
    それ以外の移していない直しを持つ PR か、比べるリリースブランチ・見せるブランチ・見せる先頭の印を読めない PR があれば、
    何も閉じずに止める（読めないものを直しが無いと扱って消さない）。
    戻り値は（使い直す PR の番号か None, 閉じた PR の番号）。"""
    reuse, closable, blocked = None, [], []
    for it in gh.list_prs(root, "open"):
        ver = review_version_of(it.get("headRefName") or "")
        if ver is None:
            continue
        n = int(it["number"])
        release, show, base = review_names(ver)
        if not _fetch_branch(root, release):
            blocked.append(f"#{n}（{release} を取得できない）")
            continue
        shown = mark_in_body(it.get("body"), SHOWN_MARK)
        if not shown:
            blocked.append(f"#{n}（本文に見せる先頭の印が無い）")
            continue
        if not _fetch_branch(root, show):
            if _remote_has(root, show):
                blocked.append(f"#{n}（{show} を取得できない）")
                continue
            fixes = []  # 見せるブランチがもう無い。移す直しは残っていない
        else:
            fixes = pending_fixes(root, f"origin/{release}", f"origin/{show}", shown)
        if not fixes:
            closable.append((n, show, base))
        elif ver == version and mark_in_body(it.get("body"), RANGE_END_MARK) == head and reuse is None:
            reuse = n
        else:
            blocked.append(f"#{n}（移していない直し {', '.join(c[:12] for c in fixes)}）")
    if blocked:
        raise StepError(
            "移していない直しを持つか、比べるブランチ・本文の印を読めない衝突の解消のレビューの PR がある: "
            + "・".join(blocked)
            + "。review-close から打ち直すか、直しを捨てると決めて PR を閉じてから review-open を打ち直す"
        )
    for n, show, base in closable:
        gh.close(root, n)
        delete_branches(root, show, base)
    return reuse, [n for n, _, _ in closable]


def open_review(root, version: str, diff: str, production: str, gh: GitHub) -> dict:
    """片付け → 範囲 → 差分 → ブランチの送信 → PR の作成。差分が空なら `review_pr` は None。"""
    if diff not in ("pr", "resolution"):
        raise StepError(f"--diff は pr か resolution（{diff}）", 2)
    release, show, base_branch = review_names(version)
    git(root, "fetch", "-q", "origin")
    if not _fetch_branch(root, release):
        raise StepError(f"{release} が origin に無い（release-branch を先に打つ）")
    head = _sha(root, f"origin/{release}")
    reuse, closed = close_stale(root, version, head, gh)
    out = {"head": head, "closed": closed}
    if reuse is not None:
        return {**out, "review_pr": reuse, "reused": True}
    frm, items = review_from(root, version, head, production, gh)
    out.update({"from": frm, "items": items, "review_pr": None})
    if diff == "resolution":
        patch = remerge_patch(root, frm, head)
        if not patch:
            return out
        base, shown = build_review_pair(root, head, patch)
    else:
        if frm == head:
            return out
        base, shown = frm, head
    files = git(root, "diff", "--name-only", base, shown).stdout.split()
    delete_branches(root, show, base_branch)  # 前に送った後で PR を作れずに止まった残り
    git(root, "push", "-q", "origin", f"{base}:refs/heads/{base_branch}", f"{shown}:refs/heads/{show}")
    body = (
        f"{release} の{'衝突の解消の差分' if diff == 'resolution' else '差分'}（{frm[:12]}..{head[:12]}）をレビューする一時の PR。"
        "レビューの直しは review-close がリリースブランチへ移してから閉じる。マージしない。\n\n"
        + SHOWN_MARK.format(sha=shown)
        + "\n"
        + RANGE_END_MARK.format(sha=head)
        + "\n"
    )
    n = gh.create_pr(root, base_branch, show, f"衝突の解消のレビュー: {release}", body)
    return {**out, "review_pr": n, "files": len(files)}


def open_outcome(version: str, got: dict) -> tuple[str, str, list[dict], int]:
    """`open_review` の結果から（status, 要約, items, 終了コード）。差分が空なら stopped と 3（打たずに進む）。結果の欄から items を外す。"""
    items = [*got.pop("items", []), *({"kind": "pr", "number": n, "result": "closed"} for n in got.get("closed") or [])]
    if got.get("review_pr") is None:
        return "stopped", f"{version} のリリースブランチの PR にレビューする差分が無い（打たずに進む）", items, EXIT_PRECONDITION
    how = "使い直す" if got.get("reused") else f"作った（ファイル {got.get('files')} 件）"
    return "ok", f"レビューの PR #{got['review_pr']} を{how}", items, 0


def _only_fixes_since(root, range_end: str, tip: str, show_ref: str) -> bool:
    """`range_end..tip` が直しだけか（マージが無く、どのコミットも見せるブランチの直しと同じ変更）。"""
    if not branch_flow.ancestor_of(root, range_end, tip):
        return False
    if git(root, "rev-list", "--merges", f"{range_end}..{tip}").stdout.strip():
        return False
    return not pending_fixes(root, show_ref, tip, range_end)


def close_review(root, version: str, gh: GitHub) -> dict:
    """直しをリリースブランチへ移して送り（I9）、レビュー済みの先頭を本文へ書き（I10）、PR を閉じて一時のブランチを消す。
    範囲の終わりから先頭までに直し以外のコミットがあれば、範囲の終わりを書いて閉じた後に止める。"""
    release, show, base_branch = review_names(version)
    git(root, "fetch", "-q", "origin")
    pr = next(iter(gh.list_prs(root, "open", head=show)), None)
    if pr is None:
        raise StepError(f"{version} の衝突の解消のレビューの PR が開いていない（review-open から打ち直す）")
    n, body = int(pr["number"]), pr.get("body") or ""
    shown, range_end = mark_in_body(body, SHOWN_MARK), mark_in_body(body, RANGE_END_MARK)
    if not shown or not range_end or not _fetch_branch(root, show) or not _fetch_branch(root, release):
        raise StepError(f"レビューの PR #{n} の本文の印か、{show}・{release} を読めない")
    fixes = pending_fixes(root, f"origin/{release}", f"origin/{show}", shown)
    tip = _sha(root, f"origin/{release}")
    if fixes:
        tip = _move_fixes(root, release, tip, fixes)
    clean = _only_fixes_since(root, range_end, tip, f"origin/{show}")
    reviewed = tip if clean else range_end
    gh.edit_body(root, n, with_mark(body, REVIEWED_MARK, reviewed))
    gh.close(root, n)
    delete_branches(root, show, base_branch)
    out = {"review_pr": n, "moved": len(fixes), "head": tip, "reviewed": reviewed}
    if not clean:
        raise StepError(f"レビューの後に {release} へ足したマージがある（範囲の終わり {range_end[:12]}）。review-open から打ち直す")
    return out


def _move_fixes(root, release: str, tip: str, fixes: list[str]) -> str:
    """直しを origin のリリースブランチの先頭へ順に cherry-pick して送る。移せなければリリースブランチを動かさずに止める。"""
    tmp = Path(tempfile.mkdtemp(prefix="ndf-review-close-"))
    wt = tmp / "wt"
    git(root, "worktree", "add", "-q", "--detach", str(wt), tip)
    try:
        for c in fixes:
            p = git(wt, "cherry-pick", "--allow-empty", c, check=False)
            if p.returncode != 0:
                git(wt, "cherry-pick", "--abort", check=False)
                raise StepError(f"レビューの直し {c[:12]} を {release} へ移せない: {(p.stdout + p.stderr).strip()[-400:]}")
        p = git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{release}", check=False)
        if p.returncode != 0:
            raise StepError(f"{release} へ直しを送れない（リリースブランチが動いた）: {p.stderr.strip()[-400:]}")
        return _sha(wt, "HEAD")
    finally:
        git(root, "worktree", "remove", str(wt), check=False)
        if tmp.exists() and not any(tmp.iterdir()):
            tmp.rmdir()
