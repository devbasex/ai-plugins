"""承認したコミット（本番への配布の承認ゲート 2 が対象にしたベースブランチの先端）の扱い（#815）。

- 承認資料（`release-steps.py approval-facts` が書く Markdown）の欄の名前・SHA の形の検査・読み取り・承認の記録の書き込み
- 承認したコミットから比べた先端までに、許したもの（配布の PR）の外のコミットや中身が無いかの比較（`compare_approved`）

`compare_approved` は git を読むだけで書かず、終了コードも出力も持たずに判定（`Verdict`）を値で返す。読み替えは呼び手が行う。
ブランチ名は受け取らず SHA だけを受け取る。`allowed` を空にすると、先端の木が承認したコミットの木と等しいかの判定になる
（昇格の経路 `merged-steps.py promote` から使う形）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import proc

ROW = "承認したコミット"
RECORD = "承認の記録"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RECORD_RE = re.compile(r"^([0-9a-f]{40}) を (\S+) が (\S+) に承認$")

MATCH = "match"
NOT_ANCESTOR = "not_ancestor"
UNKNOWN_COMMIT = "unknown_commit"
OUTSIDE_COMMITS = "outside_commits"
TREE_DIFFERS = "tree_differs"
UNDECIDABLE = "undecidable"


class Unapproved(ValueError):
    """承認の記録が無い（recorded が空）か、記録の SHA が承認したコミット（sha）と違う。"""

    def __init__(self, sha: str, recorded: str | None):
        self.sha, self.recorded = sha, recorded
        what = f"承認の記録 {recorded[:8]} と違う" if recorded else "承認の記録が無い"
        super().__init__(f"承認したコミット {sha[:8]} に{what}")


@dataclass
class Outside:
    sha: str
    subject: str = ""
    pr: int | None = None


@dataclass
class AllowedPR:
    """承認したコミットの後に入ってよい PR（本番の配布では、この版の配布の PR）。"""

    number: int
    head: str
    commits: list[str] = field(default_factory=list)
    merge_commit: str | None = None


@dataclass
class Verdict:
    ok: bool
    approved: str
    tip: str
    reason: str
    outside: list[Outside] = field(default_factory=list)
    expected_tree: str | None = None
    actual_tree: str | None = None


def parse_sha(text) -> str:
    """40 桁の 16 進（小文字へ揃える）。形が違えば ValueError。"""
    s = str(text or "").strip().lower()
    if not SHA_RE.match(s):
        raise ValueError(f"承認したコミットが 40 桁の 16 進でない: {text!r}")
    return s


def _cells(path) -> tuple[list[str], dict[str, tuple[int, str]]]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"承認資料 {p} が無い")
    lines = p.read_text(encoding="utf-8").split("\n")
    rows = {}
    for i, line in enumerate(lines):
        m = re.match(r"^\| ([^|]+?) \| (.*?) \|$", line)
        if m and m.group(1) not in rows:
            rows[m.group(1)] = (i, m.group(2).strip())
    return lines, rows


def material_sha(path) -> str:
    """承認資料の「承認したコミット」の欄の SHA。資料・欄が無い・形が違えば ValueError（承認の記録は見ない）。"""
    _, rows = _cells(path)
    if ROW not in rows:
        raise ValueError(f"承認資料 {path} に「{ROW}」の欄が無い")
    return parse_sha(rows[ROW][1])


def recorded_sha(path) -> str | None:
    """承認資料の「承認の記録」の SHA。無ければ None。"""
    _, rows = _cells(path)
    m = _RECORD_RE.match(rows.get(RECORD, (0, ""))[1])
    return m.group(1) if m else None


def from_material(path) -> str:
    """承認資料から承認したコミットを読む。承認の記録が無いか SHA が違えば Unapproved（I10）。"""
    sha = material_sha(path)
    rec = recorded_sha(path)
    if rec != sha:
        raise Unapproved(sha, rec)
    return sha


def record_approval(path, sha: str, by: str, at: str) -> None:
    """承認資料の「承認したコミット」の行の直後へ承認の記録を書く（あれば置き換える）。
    sha が資料の承認したコミットと違えば書かずに Unapproved（提示の後に資料が書き直された）。"""
    sha = parse_sha(sha)
    now = material_sha(path)
    if now != sha:
        raise Unapproved(now, sha)
    lines, rows = _cells(path)
    row = f"| {RECORD} | {sha} を {by} が {at} に承認 |"
    if RECORD in rows:
        lines[rows[RECORD][0]] = row
    else:
        lines.insert(rows[ROW][0] + 1, row)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def material_sha_or_none(path) -> str | None:
    """material_sha と同じ。読めなければ None（承認資料でない材料を許す呼び手のため）。"""
    try:
        return material_sha(path)
    except ValueError:
        return None


def record_all(approved: dict[str, str], by: str, at: str) -> list[dict]:
    """{承認資料: 承認したコミット} のそれぞれへ承認の記録を書き、結果の items を返す（mvv-gate.py の関門 2。I10）。
    判定の後に資料が書き直されていれば書かず result: not_recorded（本番の配布は記録が無いとして承認ゲートで止まる）。"""
    items = []
    for path, sha in approved.items():
        try:
            record_approval(path, sha, by, at)
            items.append({"kind": "approval", "name": path, "result": "recorded", "approved_sha": sha})
        except ValueError as e:
            items.append({"kind": "approval", "name": path, "result": "not_recorded", "approved_sha": sha, "reason": str(e)})
    return items


def material_row(sha: str) -> tuple[str, str]:
    """承認資料の「2. 承認の判断に使うもの」の表へ足す (項目, 内容)。"""
    return (ROW, parse_sha(sha))


# --- 比較 ---------------------------------------------------------------------


def _ok(root, *args) -> bool:
    return proc.git(root, *args, check=False).returncode == 0


def _out(root, *args) -> str | None:
    p = proc.git(root, *args, check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def _is_commit(root, sha) -> bool:
    return bool(sha) and _ok(root, "cat-file", "-e", f"{sha}^{{commit}}")


def _patch_id(root, sha) -> str | None:
    show = proc.git(root, "diff-tree", "-p", "--no-commit-id", sha, check=False)  # マージのコミットは差分を出さない
    if show.returncode != 0 or not show.stdout.strip():
        return None
    p = proc.run(["git", "-C", str(root), "patch-id", "--stable"], check=False, input=show.stdout)
    return p.stdout.split()[0] if p.returncode == 0 and p.stdout.strip() else None


def _subject(root, sha) -> str:
    return _out(root, "log", "-1", "--format=%s", sha) or ""


def _rev_list(root, *args) -> list[str] | None:
    out = _out(root, "rev-list", *args)
    return None if out is None else [s for s in out.split() if s]


def _expected_tree(root, approved, allowed) -> str | None:
    if not allowed:
        return _out(root, "rev-parse", f"{approved}^{{tree}}")
    if len(allowed) > 1:
        return None
    out = _out(root, "merge-tree", "--write-tree", approved, allowed[0].head)
    return out.split()[0] if out else None


def compare_approved(root, approved: str, tip: str, allowed: list[AllowedPR] = ()) -> Verdict:
    """承認したコミット approved から比べた先端 tip までを比べる（#815 の設計の「compare の中の順序」）。

    1. 読めない approved は unknown_commit、読めない tip・配布の PR の先端は undecidable
    2. approved が tip の祖先でなければ not_ancestor
    3. approved..配布の PR の先端に配布の PR のコミットでないものがあれば outside_commits（I3）
    4. approved..tip の（マージでない）コミットのうち、配布の PR の先端から届かず、配布の PR の mergeCommit でも
       同じ patch-id でもないものがあれば outside_commits（I9。木の結果によらない）
    5. tip の木が approved に配布の PR を merge-tree で足した木と違えば tree_differs、merge-tree が失敗すれば undecidable
    """
    allowed = list(allowed)
    v = Verdict(False, approved, tip, UNDECIDABLE)
    if not _is_commit(root, approved):
        v.reason = UNKNOWN_COMMIT
        return v
    if not _is_commit(root, tip) or not all(_is_commit(root, pr.head) for pr in allowed):
        return v
    if not _ok(root, "merge-base", "--is-ancestor", approved, tip):
        v.reason = NOT_ANCESTOR
        return v
    outside: list[str] = []
    for pr in allowed:
        listed = _rev_list(root, f"{approved}..{pr.head}")
        if listed is None:
            return v
        outside += [c for c in listed if c not in pr.commits and c not in outside]
    if not outside:
        listed = _rev_list(root, "--no-merges", f"{approved}..{tip}", *(f"^{pr.head}" for pr in allowed))
        if listed is None:
            return v
        merges = {pr.merge_commit for pr in allowed if pr.merge_commit}
        patches = {pid for pr in allowed for c in pr.commits if (pid := _patch_id(root, c))}
        outside = [c for c in listed if c not in merges and _patch_id(root, c) not in patches]
    if outside:
        v.reason = OUTSIDE_COMMITS
        v.outside = [Outside(c, _subject(root, c)) for c in outside]
        return v
    v.expected_tree = _expected_tree(root, approved, allowed)
    v.actual_tree = _out(root, "rev-parse", f"{tip}^{{tree}}")
    if not v.expected_tree or not v.actual_tree:
        return v
    v.ok = v.expected_tree == v.actual_tree
    v.reason = MATCH if v.ok else TREE_DIFFERS
    return v
