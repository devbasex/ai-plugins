"""触る場所の見込みと、見込みが重ならない課題を先に選ぶ並べ替え（#2997）。

見込みの出し方（`from_*`・`estimate`）と並べ替え（`spread`）は、渡した文字列と追跡しているファイルの一覧（`git ls-files`）から
値を返すだけにする。外を読むのは集める関数（`issue_estimate`・`lane_estimates`・`shared_list`・`git_tracked`）だけで、
`gh` と `git` は引数の `source`（`run`・`issue_body`・`milestones` を持つもの。開発ループの `Env` か `GhSource`）を通す。

    files = ["src/app/models/user.rb", "docs/a.md"]
    est = estimate([(DESIGN, lambda: from_design(text, 12)), (BODY, lambda: from_body(body, files))])
    est.to_dict()   # {"paths": ["src/app/models/user.rb"], "source": "課題の本文", "note": ""}
    got = spread([1, 2, 3], set(), {1: e1, 2: e2, 3: e3}, slots=2, width=1)
    got.take, got.overlap, got.reorders

- 出所は 設計の結果 → 課題グループの表 → 課題の本文 の順に見て、見込みが 1 件以上出た最初の 1 つだけを使う（決定 1）
- 本文からはファイルだけを取る（追跡しているファイルに一致・glob が当たる・末尾のパスの要素が 1 つのファイルだけに一致）
- 重なりの判定は実装のキューと同じ `admission.overlap_of` と共有の一覧を使う（I10）
- 並列の計画の検査の見込みの重なり（I21）の組と直し方の案は `lane_overlaps` が返す
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping, Sequence

import md
from supervise_lib import admission

DESIGN = "設計の結果"
GROUP = "課題グループの表"
BODY = "課題の本文"
NONE = "見込みなし"
SOURCES = (DESIGN, GROUP, BODY, NONE)

YES, NO, UNKNOWN = "あり", "なし", "不明"

GROUP_HEADING = "並列の組（見込み）"
BODY_HEADINGS = ("目的", "対象範囲", "事象", "原因の見当")
BODY_STOP = "含まない"  # 対象範囲の「含まない」より後は読まない
_GLOB = re.compile(r"[*?\[]")
_ISSUE = re.compile(r"#(\d+)")


@dataclass(frozen=True)
class Estimate:
    paths: list[str] = field(default_factory=list)
    source: str = NONE
    note: str = ""

    def to_dict(self) -> dict:
        return {"paths": list(self.paths), "source": self.source, "note": self.note}


@dataclass
class Spread:
    take: list = field(default_factory=list)  # 取った課題（取った順）
    overlap: dict = field(default_factory=dict)  # 課題 → {"overlap": あり/なし/不明, "overlap_with": [課題]}
    reorders: list[dict] = field(default_factory=list)  # [{"later", "earlier", "with"}]


def _path_token(token: str) -> str:
    t = token.strip()
    return t[2:] if t.startswith("./") else t


def _match_file(token: str, files: Sequence[str]) -> list[str]:
    """本文の語に当たるファイル: 完全に一致 → glob が当たる → 末尾のパスの要素が 1 つのファイルだけに一致。ディレクトリは取らない。"""
    t = _path_token(token)
    if not t or " " in t or t.endswith("/"):
        return []
    if t in files:
        return [t]
    if _GLOB.search(t):
        return fnmatch.filter(files, t)
    tail = PurePosixPath(t).parts
    hits = [f for f in files if PurePosixPath(f).parts[-len(tail) :] == tail]
    return hits if len(hits) == 1 else []


def _match_place(token: str, files: Sequence[str]) -> list[str]:
    """課題グループの表の語: ファイルかディレクトリ（追跡しているファイルを含むもの）か glob。当たらない語（Skill の名前など）は捨てる。"""
    t = _path_token(token).rstrip("/")
    if not t or " " in t:
        return []
    if _GLOB.search(t):
        return fnmatch.filter(files, t)
    return [t] if any(admission.contains(t, f) for f in files) else []


def _uniq(paths: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def from_design(text: str, issue: int) -> list[str]:
    """設計文書の「設計の結果」の表の、課題が「実装する」の行の触るファイル。表が無い・読めなければ空。"""
    import design_results

    try:
        rows = design_results.parse_results(text or "") or []
    except Exception:  # noqa: BLE001 — 書式の外れた表は見込みの出所にしない（次の出所へ進む）
        return []
    return _uniq(f for r in rows if r.issue == issue and r.kind == design_results.IMPLEMENT for f in r.files)


def _group_rows(description: str) -> list[tuple[list[int], str]]:
    """`### 並列の組（見込み）` の表の行（課題の番号, 「触る場所の見込み」のセル）。表が無ければ空。"""
    text = description or ""
    sec = md.section_named(text, GROUP_HEADING)
    if sec is None:
        return []
    out = []
    for table in md.tables(text):
        head = [h.strip() for h in table.header]
        if not sec.start <= table.start < sec.end or "課題" not in head or "触る場所の見込み" not in head:
            continue
        ci, pi = head.index("課題"), head.index("触る場所の見込み")
        out += [([int(n) for n in _ISSUE.findall(c[ci])], c[pi]) for c in table.rows if len(c) > max(ci, pi)]
    return out


def group_issues(description: str) -> list[int]:
    """課題グループの表に載る課題の番号（載った順）。"""
    return _uniq(n for nums, _ in _group_rows(description) for n in nums)


def from_group_table(description: str, issue: int, files: Sequence[str]) -> list[str]:
    """マイルストーンの説明の `### 並列の組（見込み）` の表で、課題の行の「触る場所の見込み」のバッククォートの語。"""
    out: list[str] = []
    for nums, cell in _group_rows(description):
        if issue in nums:
            for token in re.findall(r"`([^`]+)`", cell):
                out += _match_place(token, files)
    return _uniq(out)


def _body_lines(text: str) -> list[tuple[int, int]] | None:
    """本文のうち読む行の範囲 [(始め, 終わり)]。見出しが 1 つも無ければ None（全体を読む）。"""
    lines = text.splitlines()
    spans = []
    for s in md.md_sections(text):
        if s.heading.title not in BODY_HEADINGS:
            continue
        end = s.end
        if s.heading.title == "対象範囲":
            end = next((i for i in range(s.start, s.end) if i < len(lines) and BODY_STOP in lines[i]), s.end)
        spans.append((s.start, end))
    return spans or None


def from_body(body: str, files: Sequence[str]) -> list[str]:
    """課題の本文の見出し「目的」「対象範囲」（含まないより前）「事象」「原因の見当」のバッククォートの語に当たるファイル。"""
    text = body or ""
    spans = _body_lines(text)
    out: list[str] = []
    for span in md.code_spans(text):
        if spans is None or any(a <= span.line < b for a, b in spans):
            out += _match_file(span.content, files)
    return _uniq(out)


def estimate(sources: Iterable[tuple[str, Callable[[], list[str]]]], note: str = "") -> Estimate:
    """出所を順に見て、見込みが 1 件以上出た最初の出所の見込み。どれも出なければ見込みなし（note は理由）。

    出所は (出所の名前, 見込みを返す関数) で渡す。先の出所で出れば後の関数を呼ばない（本文の取得を省く）。"""
    for source, get in sources:
        paths = _uniq(get() or [])
        if paths:
            return Estimate(paths, source, "")
    return Estimate([], NONE, note)


def _relation(mine: Estimate, others: Sequence[tuple[object, Estimate]], shared: Sequence[Mapping]) -> tuple[str, list]:
    """既に選んだ課題との重なり。どれかと重なれば あり、重ならずに見込みなしの相手がいれば 不明、ほかは なし。"""
    if not others:
        return NO, []
    if not mine.paths:
        return UNKNOWN, [n for n, _ in others]
    hit = [n for n, e in others if e.paths and admission.overlap_of(mine.paths, e.paths, shared)]
    if hit:
        return YES, hit
    unknown = [n for n, e in others if not e.paths]
    return (UNKNOWN, unknown) if unknown else (NO, [])


def spread(
    order: Sequence,
    fixed: Iterable,
    estimates: Mapping[object, Estimate],
    slots: int,
    width: int = 0,
    shared: Iterable[Mapping] = (),
) -> Spread:
    """枠の順 `order` から `slots` 件を取る。`fixed`（先頭に選ぶ課題・品質の枠）は重なりを見ずに先に取る（I3）。

    残りは先頭から「残りの枠の数＋width」件の窓の中で、既に取った課題と重ならない課題を先に取り（I4）、枠が残れば
    取らなかった候補を枠の順に戻して埋める（I5）。width が 0 なら取る組は枠の順の先頭 `slots` 件と同じ（I6）。"""
    shared, fixed = list(shared), set(fixed)
    blank = Estimate()
    est = {n: estimates.get(n, blank) for n in order}
    take = [n for n in order if n in fixed][: max(slots, 0)]
    rest = [n for n in order if n not in fixed]
    k = max(slots - len(take), 0)
    window = rest[: k + max(width, 0)]
    picked: list = []
    for n in window:
        if len(picked) >= k:
            break
        rel, _ = _relation(est[n], [(m, est[m]) for m in take + picked], shared)
        if rel != YES:
            picked.append(n)
    for n in rest:
        if len(picked) >= k:
            break
        if n not in picked:
            picked.append(n)
    take += picked
    out = Spread(take=take)
    for i, n in enumerate(take):
        rel, with_ = _relation(est[n], [(m, est[m]) for m in take[:i]], shared)
        out.overlap[n] = {"overlap": rel, "overlap_with": with_}
    chosen = set(take)
    for j, n in enumerate(window):
        if n in chosen:
            continue
        for later in window[j + 1 :]:
            if later in chosen and later in picked:
                _, with_ = _relation(est[n], [(m, est[m]) for m in take if m != n], shared)
                out.reorders.append({"later": n, "earlier": later, "with": with_})
    return out


# ---------------------------------------------------------------- 集める（外を読む）

OUTSIDE_NOTE = "切り出しの境界の外"
UNREADABLE_NOTE = "本文を読めない"


@dataclass
class TouchPick:
    """選定 1 回の触る場所の見込み。estimates は 課題 → 見込み、reorders は逆転の組を書き足す先。"""

    estimates: dict = field(default_factory=dict)
    width: int = 0
    shared: list = field(default_factory=list)
    reorders: list = field(default_factory=list)


class GhSource:
    """`gh` と `git` を root で打つ入口（並列の計画の検査が使う。開発ループは `Env` を渡す）。"""

    def __init__(self, root) -> None:
        self.root = Path(root)

    def main_dir(self) -> Path:
        return self.root

    def run(self, cmd: list[str], cwd: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
        p = subprocess.run(cmd, cwd=cwd or str(self.root), input=stdin, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def milestones(self, repo: str = "") -> dict:
        api = f"repos/{repo or '{owner}/{repo}'}/milestones?state=open&per_page=100"
        code, out, err = self.run(["gh", "api", api, "--jq", "map({title, description})"])
        if code != 0:
            raise OSError(f"マイルストーンを読めない: {err.strip()[:300]}")
        return {m["title"]: m.get("description") or "" for m in json.loads(out or "[]")}

    def issue_body(self, n: int) -> str:
        code, out, err = self.run(["gh", "issue", "view", str(n), "--json", "body", "--jq", ".body"])
        if code != 0:
            raise OSError(f"課題 #{n} を読めない: {err.strip()[:300]}")
        return out


def reorder_width(rec: Mapping, root) -> int:
    """記録の逆転の幅。無い古い記録は進め方の宣言の値（読めなければ既定の値）で読む（I7）。"""
    import pace

    width = rec.get("reorder_width")
    if isinstance(width, int) and not isinstance(width, bool):
        return width
    try:
        return pace.read_loop(root)["reorder_width"]
    except pace.PaceError:
        return pace.LOOP_DEFAULTS["reorder_width"]


def shared_list(root) -> list:
    """実装のキューと同じ共有の一覧（`.ndf/supervise.json` の `queue.shared`）。読めなければ空（I10）。"""
    from supervise_lib import decl

    try:
        queue = decl.queue_decl(decl.read_decl([root], decl.SUPERVISE_DECL))
    except decl.DeclError:
        return []
    return queue["shared"]


def git_tracked(source) -> list[str]:
    """追跡しているファイル（`git ls-files`。選定 1 回に 1 回）。読めなければ空。"""
    code, out, _ = source.run(["git", "ls-files"], cwd=str(source.main_dir()))
    return [line for line in (out or "").splitlines() if line.strip()] if code == 0 else []


def design_paths(root, n: int) -> list[str]:
    """マージ済みの設計文書（`issues/issue-<n>-design*.md`）の設計の結果の触るファイル。"""
    out: list[str] = []
    for p in sorted((Path(root) / "issues").glob(f"issue-{n}-design*.md")):
        try:
            out += from_design(p.read_text(encoding="utf-8"), n)
        except OSError:
            continue
    return out


def issue_estimate(source, n: int, description: str, files: Sequence[str], outside: bool = False) -> Estimate:
    """課題 n の見込み。設計の結果 → 課題グループの表 → 課題の本文（切り出しの境界の外は読まない）の順に見る。"""
    notes: list[str] = []

    def body() -> list[str]:
        if outside:
            notes.append(OUTSIDE_NOTE)
            return []
        try:
            return from_body(source.issue_body(n), files)
        except OSError:
            notes.append(UNREADABLE_NOTE)
            return []

    got = estimate(
        [(DESIGN, lambda: design_paths(source.main_dir(), n)), (GROUP, lambda: from_group_table(description, n, files)), (BODY, body)]
    )
    return Estimate([], NONE, notes[0]) if got.source == NONE and notes else got


def annotate_rows(rows: list[dict], numbers: Sequence[int], picked: Iterable[int], got: Spread, est: Mapping, touch: TouchPick) -> None:
    """選定の行に見込み（すべての行）と重なり（選んだ行 `picked`）を書き、逆転の組を touch.reorders へ足す。
    `got` と `est` の鍵は候補の添字、`numbers` は 添字 → 課題の番号。"""
    at, picked = {n: i for i, n in enumerate(numbers)}, set(picked)
    for row in rows:
        i = at[row["number"]]
        row["touch"] = est[i].to_dict()
        if row["number"] in picked:
            o = got.overlap[i]
            row.update(overlap=o["overlap"], overlap_with=[numbers[j] for j in o["overlap_with"]])
    touch.reorders += [
        {"later": numbers[r["later"]], "earlier": numbers[r["earlier"]], "with": [numbers[j] for j in r["with"]]} for r in got.reorders
    ]


def lane_estimates(main, lanes, source_of=GhSource) -> tuple[dict, list, str | None]:
    """並列の計画のレーン → 課題 → 見込みのパス（I21）。出し方は開発ループの選定と同じ `issue_estimate`。
    スプリントのレーンは `input.issues`、ループのレーンは `--milestones` のマイルストーンの課題グループの表の課題を見る。
    マイルストーンか課題の本文を読めなければ理由を返して続ける（その課題は見込みなし）。source_of はテストが差し替える（レーンのリポジトリ → 入口）。"""
    repos: dict[str, tuple] = {}
    out: dict[str, dict] = {}
    errors: list[str] = []
    for ln in lanes:
        root = ln.repo_dir(Path(main))
        if str(root) not in repos:
            src = source_of(root)
            try:
                descs = src.milestones("")
            except OSError as exc:
                errors.append(f"{root}: {exc}")
                descs = {}
            repos[str(root)] = (src, descs, git_tracked(src))
        src, descs, files = repos[str(root)]
        if ln.input.kind == "sprint":
            issues = list(ln.input.issues)
        else:
            issues = [n for t in sorted(ln.milestones() or ()) for n in group_issues(descs.get(t, ""))]
        got = {n: issue_estimate(src, n, next((d for d in descs.values() if n in group_issues(d)), ""), files) for n in issues}
        errors += [f"課題 #{n} の{UNREADABLE_NOTE}" for n, e in got.items() if e.note == UNREADABLE_NOTE]
        out[ln.name] = {n: e.paths for n, e in got.items()}
    return out, shared_list(main), "; ".join(errors) or None


def _ancestors(lanes) -> dict[str, set[str]]:
    """レーン → `after` を推移的にたどって届くレーン。"""
    after = {ln.name: set(ln.after) for ln in lanes}
    out: dict[str, set[str]] = {}
    for name in after:
        seen, todo = set(), list(after[name])
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo += after.get(n, ())
        out[name] = seen
    return out


def lane_overlaps(lanes, estimates: Mapping[str, Mapping[int, list[str]]], shared: Iterable[Mapping], same_repo) -> list[dict]:
    """同じ時間帯に動く（`after` で順序の付かない）同じリポジトリの 2 レーンの、課題の見込みの重なりの組と直し方の案（I21）。
    見込みの無い課題は重なりを「不明」として出さない。same_repo(a, b) はレーンが同じリポジトリか。"""
    shared, anc, out = list(shared), _ancestors(lanes), []
    for i, a in enumerate(lanes):
        for b in lanes[i + 1 :]:
            if not same_repo(a, b) or a.name in anc[b.name] or b.name in anc[a.name]:
                continue
            for ia, pa in (estimates.get(a.name) or {}).items():
                for ib, pb in (estimates.get(b.name) or {}).items():
                    paths = admission.overlap_of(pa, pb, shared) if pa and pb and ia != ib else None
                    if paths:
                        out.append(_lane_item(a, b, ia, ib, paths))
    return out


def _lane_item(a, b, ia: int, ib: int, paths: list[str]) -> dict:
    proposals: list[dict] = [{"kind": "after", "lane": b.name, "after": a.name}]
    if a.input.kind == "sprint" and b.input.kind == "sprint":  # ループのレーンの課題はループが選ぶため移さない
        proposals.append({"kind": "move", "issue": ib, "from": b.name, "to": a.name})
    shown = "・".join(dict.fromkeys(paths))
    return {
        "message": f"同じ時間帯に動くレーンの課題の見込みが重なる: {a.name} #{ia}・{b.name} #{ib}（{shown}）",
        "lanes": [a.name, b.name],
        "issues": [ia, ib],
        "paths": paths,
        "proposals": proposals,
    }
