"""まとめの課題から GitHub の参照をたどり、リリース記録（`## 配布の記録`）を置いた PR を 1 本決める（#2230）。

queue の差し込み `{queue_record}`（`supervise_lib/queue_fill.py`）だけが呼ぶ。決め方（設計 `issues/issue-2230-design.md`
の `find_record` の規則）:

1. まとめの課題ごとに、課題の履歴の参照（`cross-referenced`）のうち同じリポジトリの Pull Request を集める
2. そのうち本文の closing keywords か閉じる予定の参照（`closing.closing_refs`）がその課題を指すものを、スプリントの PR とする。
   閉じる PR が見つかった課題が 0 件なら見つからない
3. 候補はスプリントの PR と、スプリントの PR の履歴の参照のうち Pull Request のものの和
4. 候補ごとに最後のリリース記録を読み、段階が本番か配布なし（`dist_record.released`）で、`スプリント:` の PR が
   閉じる PR の見つかった課題の 1 件ごとにその閉じる PR を 1 本以上含むものだけを残す
5. 残った記録の `スプリント:` の並び（番号の集合）が 2 通り以上なら特定できない（見つからない）
6. 残った中で記録のコメントの識別子（本文は 0）が最も大きいもの、同じなら PR の番号の大きいものを採る

GitHub は `gh`（`lib/gh_call.py`）で読むだけで、書かない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import gh_call
from closing import closing_refs
from dist_record import parse_record, released
from step_result import StepError

from sprint_close_lib.collect import read_record


@dataclass(frozen=True)
class RecordHit:
    """記録の PR を探した結果。`pr` が None なら見つからず、`reason()` が理由の文を返す。"""

    pr: int | None = None
    issues: tuple[int, ...] = ()
    closers: tuple[int, ...] = ()  # まとめの課題を閉じる PR（スプリントの PR）
    searched: tuple[int, ...] = ()  # 記録を読んだ PR
    pre_release: tuple[int, ...] = ()  # 記録はあるが段階が本番の前だった PR
    error: str = ""  # GitHub を読めなかったときの理由
    ambiguous: tuple[tuple[int, tuple[int, ...]], ...] = ()  # (記録の PR, その `スプリント:` の PR の集合) の並び

    def reason(self) -> str:
        if self.pr is not None:
            return ""
        issues = " ".join(f"#{n}" for n in self.issues) or "（無し）"
        if self.error:
            return f"GitHub を読めない: {self.error}"
        if self.ambiguous:
            sets = "・".join(f"PR #{pr}（スプリント: {' '.join(f'#{n}' for n in prs)}）" for pr, prs in self.ambiguous)
            return f"課題 {issues} を閉じる記録が 2 つ以上のスプリントにある: {sets}。`--record-pr <番号>` を明示して組み直す"
        if not self.closers:
            return f"課題 {issues} を閉じる PR が見つからない（課題の履歴の参照を読んだ）"
        closers = " ".join(f"#{n}" for n in self.closers)
        others = [n for n in self.searched if n not in self.closers]
        refs = f"と、それを参照する PR {' '.join(f'#{n}' for n in others)} " if others else ""
        tail = f"（本番の前の記録だけ: PR {' '.join(f'#{n}' for n in self.pre_release)}）" if self.pre_release else ""
        return f"課題 {issues} を閉じる PR {closers} {refs}を読んだが、本番か配布なしの段階のリリース記録が無い{tail}"


class _Unreadable(Exception):
    pass


def _repo_of(root) -> str:
    p = gh_call.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=root)
    name = p.stdout.strip()
    if p.returncode != 0 or not name:
        raise _Unreadable((p.stderr or "記録のリポジトリを決められない").strip()[:300])
    return name


def _referencing_prs(root, repo: str, n: int) -> list[dict]:
    """課題か PR `n` の履歴の参照のうち、同じリポジトリの Pull Request（番号と本文）。"""
    p = gh_call.gh(["api", "--paginate", "--slurp", f"repos/{repo}/issues/{n}/timeline"], cwd=root)
    if p.returncode != 0:
        raise _Unreadable((p.stderr or "").strip()[:300] or f"#{n} の履歴の参照を読めない")
    try:
        pages = json.loads(p.stdout or "[]")
    except ValueError:
        raise _Unreadable(f"#{n} の履歴の参照の出力を読めない")
    events = [e for page in pages for e in (page if isinstance(page, list) else [page])]
    out: dict[int, dict] = {}
    for e in events:
        src = (e or {}).get("source", {}).get("issue") or {}
        if e.get("event") != "cross-referenced" or src.get("pull_request") is None:
            continue
        if ((src.get("repository") or {}).get("full_name") or repo).lower() != repo.lower():
            continue
        out.setdefault(int(src["number"]), {"number": int(src["number"]), "body": src.get("body") or ""})
    return list(out.values())


def _closers_of(root, repo: str, issues: tuple[int, ...]) -> dict[int, set[int]]:
    """まとめの課題ごとに、その課題を閉じる PR の集合。閉じる PR が見つからない課題は含めない。"""
    closers_of: dict[int, set[int]] = {}
    for n in issues:
        hit = {pr["number"] for pr in _referencing_prs(root, repo, n) if (repo, n) in closing_refs(pr["body"], repo)}
        if hit:
            closers_of[n] = hit
    return closers_of


def _record_candidates(root, repo: str, closers: tuple[int, ...]) -> set[int]:
    """閉じる PR と、それを参照する PR の和。"""
    cands = set(closers)
    for pr in closers:
        cands |= {r["number"] for r in _referencing_prs(root, repo, pr)}
    return cands


def _released_records(root, repo: str, cands: set[int], closers_of: dict[int, set[int]]) -> tuple[list, list[int]]:
    """候補ごとに最後のリリース記録を読み、(本番か配布なしで閉じる PR を含む記録, 本番の前の記録の PR) に振り分ける。"""
    kept, pre = [], []
    for pr in sorted(cands):
        try:
            rec = parse_record(*read_record(root, repo, pr))
        except StepError as e:
            raise _Unreadable(str(e))
        if not rec["found"]:
            continue
        if not released(rec["stage"]):
            pre.append(pr)
            continue
        sprint = set(rec["sprint_prs"])
        if all(sprint & want for want in closers_of.values()):
            kept.append((rec["dist_comment_id"] or 0, pr, tuple(sorted(sprint))))
    return kept, pre


def find_record(root, repo: str | None, issues) -> RecordHit:
    """まとめの課題 `issues` のリリース記録を置いた PR を決める。`root` は `gh` を打つ作業場所、`repo` は owner/name（None なら `root` から決める）。"""
    issues = tuple(int(n) for n in issues)
    if not issues:
        return RecordHit(error="まとめの課題が無い")
    try:
        repo = repo or _repo_of(root)
        closers_of = _closers_of(root, repo, issues)
        closers = tuple(sorted(set().union(*closers_of.values()))) if closers_of else ()
        if not closers:
            return RecordHit(issues=issues)
        cands = _record_candidates(root, repo, closers)
        kept, pre = _released_records(root, repo, cands, closers_of)
    except _Unreadable as e:
        return RecordHit(issues=issues, error=str(e))
    base = {"issues": issues, "closers": closers, "searched": tuple(sorted(cands)), "pre_release": tuple(pre)}
    if len({k[2] for k in kept}) > 1:
        return RecordHit(**base, ambiguous=tuple((pr, prs) for _, pr, prs in sorted(kept, key=lambda k: k[1])))
    if not kept:
        return RecordHit(**base)
    return RecordHit(pr=max(kept)[1], **base)
