"""本番の配布が受け取る承認したコミットの読み取り・比較・止め方（`release-steps.py` の release と approve から分けた。#815）。

比べる本体は `lib/approved_commit.py`。ここは引数・承認資料の読み替えと、承認ゲートの結果（`step_result.result` の形）を
作るところまでを持ち、出す（emit）のは呼び手である。
"""

from __future__ import annotations

import json
from pathlib import Path

import approved_commit as ac
import clock
import gh_parts
import repo as repo_lib
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, git, result

TOOL = "release"


class HeadMoved(Exception):
    """--expect-head の SHA と PR の先端が違い、merge-when-green がマージしなかった（#815 の I5）。"""


class Gate(Exception):
    """承認ゲートで止める。out は出す結果（status: gate）。"""

    def __init__(self, out: dict):
        super().__init__(out.get("summary"))
        self.out = out


def _material(root, material) -> Path:
    path = Path(material)
    return path if path.is_absolute() else root / path


def approved_of(root, a, plugin):
    """本番の配布が受け取る承認したコミット（#815 の I1・I10）。dev は None。何もマージする前に呼ぶ。

    --approved-sha の形の誤り・どちらも無い・dev に渡した → 2。--approval の資料・欄が無い・形の誤り → 3。
    --approval の資料に承認の記録が無いか SHA が違う → Gate（承認ゲート 10）。"""
    sha, material = getattr(a, "approved_sha", None), getattr(a, "approval", None)
    if a.channel == "dev":
        if sha or material:
            raise StepError("--channel dev には --approved-sha も --approval も渡さない", EXIT_UNREADABLE)
        return None
    if sha:
        try:
            return ac.parse_sha(sha)
        except ValueError as e:
            raise StepError(str(e), EXIT_UNREADABLE)
    if not material:
        raise StepError(
            "--channel prod には --approved-sha か --approval が要る（承認資料の next のコマンドをそのまま打つ）", EXIT_UNREADABLE
        )
    path = _material(root, material)
    try:
        return ac.from_material(path)
    except ac.Unapproved as e:
        item = {"kind": "commit", "name": e.sha, "result": "not_approved", "recorded": e.recorded}
        metrics = {"channel": a.channel, "version": a.version, "approved_sha": e.sha, "compared_head": None, "reason": "not_approved"}
        raise Gate(
            result(
                TOOL,
                "gate",
                f"{plugin} v{a.version} の本番への配布を止めた: 承認資料の承認したコミット {e.sha[:8]} に"
                + ("承認の記録が無い" if e.recorded is None else f"承認の記録 {e.recorded[:8]} と違う"),
                [item],
                metrics,
                str(path),
                f"承認資料を提示して承認を得てから、release-steps.py approve --approval {path} --approved-sha <提示した承認したコミット> "
                f"--by user で承認の記録を書き、release-steps.py release --version {a.version} --channel prod --approval {path} を打ち直す",
            )
        )
    except ValueError as e:
        raise StepError(str(e), EXIT_PRECONDITION)


def release_pr_allowed(root, n, pr_view):
    """配布の PR の先端・コミット・mergeCommit（承認したコミットの後に入ってよいもの）。読めなければ None。"""
    try:
        d = pr_view(root, n, "headRefOid,commits,mergeCommit") or {}
    except StepError:
        return None
    head = d.get("headRefOid") or ""
    commits = [c.get("oid") for c in d.get("commits") or [] if isinstance(c, dict) and c.get("oid")]
    if not head or not commits:
        return None
    if git(root, "cat-file", "-e", f"{head}^{{commit}}", check=False).returncode != 0:
        git(root, "fetch", "-q", "origin", head, check=False)
    return ac.AllowedPR(n, head, commits, (d.get("mergeCommit") or {}).get("oid"))


def check_approved(root, approved, base, allowed):
    """origin/<base> の先端を読み直し、承認したコミットと比べた判定（Verdict）を返す。"""
    git(root, "fetch", "-q", "origin", base)
    tip = git(root, "rev-parse", f"origin/{base}").stdout.strip()
    if allowed is None:
        return ac.Verdict(False, approved, tip, ac.UNDECIDABLE)
    return ac.compare_approved(root, approved, tip, [allowed])


def commit_pr(root, sha):
    """コミットを入れた PR の番号（止まったときの表示にだけ使う）。読めなければ None。"""
    slug = repo_lib.owner_repo(root)
    p = gh_parts.gh(["api", f"repos/{slug}/commits/{sha}/pulls"], cwd=root) if slug else None
    try:
        found = json.loads(p.stdout) if p is not None and p.returncode == 0 else []
        return found[0].get("number") if found else None
    except (ValueError, AttributeError, IndexError, TypeError):
        return None


GATE_LEADS = {
    ac.UNKNOWN_COMMIT: "承認したコミット {a} を読めない",
    ac.NOT_ANCESTOR: "承認したコミット {a} が {base} の先端 {t} の祖先でない",
    ac.OUTSIDE_COMMITS: "承認したコミット {a} の後に承認の外のコミットが {n} 件ある",
    ac.TREE_DIFFERS: "{base} の先端 {t} の中身が、承認したコミット {a} に配布の PR を足したものと違う",
    ac.UNDECIDABLE: "承認したコミット {a} の後に承認の外の変更が無いかを判定できない",
    "head_moved": "{base} の先端が比べた後に {t} へ進んだ",
}


def gate(root, a, plugin, base, verdict, items, metrics, moved=False) -> dict:
    """承認の外の変更で本番への配布を止める結果（status: gate。出すのは呼び手で終了コード 10。#815 の I2〜I6）。"""
    reason = "head_moved" if moved and verdict.ok else verdict.reason
    out = []
    for o in verdict.outside:
        it = {"kind": "commit", "name": o.sha, "result": "unapproved", "subject": o.subject}
        if (n := commit_pr(root, o.sha)) is not None:
            it["pr"] = n
        out.append(it)
    if reason == ac.TREE_DIFFERS:
        out.append(
            {"kind": "tree", "name": verdict.tip, "result": "differs", "expected": verdict.expected_tree, "actual": verdict.actual_tree}
        )
    elif reason in (ac.NOT_ANCESTOR, ac.UNKNOWN_COMMIT):
        out.append({"kind": "commit", "name": verdict.approved, "result": "not_ancestor" if reason == ac.NOT_ANCESTOR else "unknown"})
    metrics.update({"approved_sha": verdict.approved, "compared_head": verdict.tip, "reason": reason, "outside": len(verdict.outside)})
    lead = GATE_LEADS.get(reason, GATE_LEADS[ac.UNDECIDABLE]).format(
        a=verdict.approved[:8], t=(verdict.tip or "?")[:8], base=base, n=len(verdict.outside)
    )
    return result(
        TOOL,
        "gate",
        f"{plugin} v{a.version} の本番への配布を止めた: {lead}",
        items + out,
        metrics,
        None,
        f"承認資料を作り直して（release-steps.py approval-facts --version {a.version} --prs <PR番号>...）承認を取り直し、"
        f"release-steps.py release --version {a.version} --channel prod --approved-sha <新しい承認したコミット> を打ち直す",
    )


def approve(root, a) -> dict:
    """ゲート 2 の承認を承認資料へ記録する（#815 の I10）。--approved-sha は提示した資料を書いた approval-facts の
    metrics.approved_sha（資料を読み直した値ではない）。資料がその後に書き直されていれば書かずに止まる。"""
    path = _material(root, a.approval)
    try:
        sha = ac.parse_sha(a.approved_sha)
    except ValueError as e:
        raise StepError(str(e), EXIT_UNREADABLE)
    at = clock.now_iso("utc")
    try:
        ac.record_approval(path, sha, a.by, at)
    except ac.Unapproved as e:
        raise StepError(
            f"承認資料の承認したコミットが {e.sha[:8]} で、提示した {sha[:8]} と違う（提示の後に書き直された）。"
            "資料を提示し直して承認を取り直す"
        )
    except ValueError as e:
        raise StepError(str(e), EXIT_PRECONDITION)
    return result(
        TOOL,
        "ok",
        f"承認資料に承認の記録（{sha[:8]}・{a.by}）を書いた",
        [{"kind": "cell", "name": ac.RECORD, "result": "written"}],
        {"approved_sha": sha, "by": a.by, "at": at},
        str(path),
    )
