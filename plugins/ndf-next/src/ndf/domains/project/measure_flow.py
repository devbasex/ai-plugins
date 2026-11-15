"""ブランチ運用の測定（`ndf project measure` の `branch_flow`）と、設定とのずれ（`ndf project drift`。#3569。旧い
`project_lib/measure_branch_flow.py` と `lib/branch_flow.py` のリリース方式の検査の移し先）。

測るのは設定の 4 項目（`start`・`integrate`・`doc_prs`・`release_strategy`）で、項目ごとに推した値・確からしさ・根拠（打った
コマンドと結果の要約）を出す。根拠が足りない項目は値を出さず `unknown` と理由を出す。

- 標本は直近のマージ済みの Pull Request（最大 100 件）と、リモート名のリモートのスプリントブランチである
- 確からしさ: 推した値に合う標本の割合が 0.9 以上で `高`、0.6 以上で `中`。標本が 3 件未満か 0.6 未満なら `unknown`
- 守らせている仕組み（ルールセット）は根拠の行としてだけ出す

リリース方式の組み合わせは事実の表 `branch-flows`、値の形は `.ndf/worktree.json` のスキーマの `branch_flow` が SSOT である。
レビューの単位（`branch_flow.reviews`）は、PR の種類ごとに直近の PR を最大 `REVIEW_SAMPLES` 件読み、マーカーの有無から推す。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from ndf import contracts, identity
from ndf.contracts import facts
from ndf.domains.project.measure_ci import Gh, GhUnavailable
from ndf.domains.project.items import measured, obj, question, seq, unknown
from ndf.domains.project.measure_repo import Tree
from ndf.domains.project.secret import mask

MIN_FLOW_SAMPLES = 3
HIGH, MID = 0.9, 0.6
PR_LIMIT = 100
REVIEW_SAMPLES = 10
ITEM = "branch_flow"
REVIEWS_KEY = "reviews"
WORKTREE_SCHEMA = contracts.schema_path("ndf-settings/worktree")
DOC_PREFIXES = (("design/", "design"), ("spec/", "spec"))
FLOW_KEYS = ("start", "integrate", "doc_prs", "release_strategy")
REVIEW_KINDS = ("impl", "sprint", "release", "design", "spec")
# 書いていない種類の値。キーは課題のまとまりの記録が有るか（旧い lib/branch_flow.py の REVIEW_DEFAULTS と同じ）
REVIEW_DEFAULTS: dict[str, dict[bool, dict[str, Any]]] = {
    "impl": {True: {"checks": ["cross-review"], "diff": "pr"}, False: {"checks": [], "diff": "none"}},
    "sprint": {
        True: {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},
        False: {"checks": ["cross-refactoring", "cross-review", "behavior"], "diff": "unchecked"},
    },
    "release": {True: {"checks": [], "diff": "none"}, False: {"checks": [], "diff": "none"}},
    "design": {True: {"checks": ["cross-review"], "diff": "pr"}, False: {"checks": ["cross-review"], "diff": "pr"}},
    "spec": {True: {"checks": [], "diff": "none"}, False: {"checks": [], "diff": "none"}},
}
# 掛けたレビューのマーカー（書き手: cross-review の投稿の見出し・cross-refactoring の計画のコメント・振る舞いの確認の記録）
REVIEW_MARKS = (
    ("cross-review", "reviews", "## 🤖 cross-review"),
    ("cross-refactoring", "comments", "<!-- cross-refactoring plan"),
    ("behavior", "comments", f"<!-- {identity.FINAL_NAME}:behavior-check"),
)
PR_COMMAND = "gh api repos/<R>/pulls?state=closed&per_page=100（マージ済みだけを数える）"
REVIEWS_COMMAND = "gh api repos/<R>/pulls/<N>/reviews と repos/<R>/issues/<N>/comments（マーカーの有無だけを数える）"
TABLE_COMMAND = "事実の表 branch-flows のリリース方式の行"


class Branch:
    """ブランチの名前の頭（旧い lib/branch_flow.py と measure_branch_flow.py の値）。"""

    SPRINT = "sprint/"
    RELEASE = "release/v"
    REVIEW = "review/release/v"
    REVIEW_BASE_SUFFIX = "-base"
    IMPL = "feat/issue-"


def _rows() -> dict[str, dict[str, Any]]:
    return {r["release_strategy"]: r for r in facts.rows("branch-flows")}


def default_flow() -> dict[str, Any]:
    """`branch_flow` の無い設定が使う値（表の既定の行の各列の先頭）。"""
    row = next(r for r in facts.rows("branch-flows") if r["default"])
    return {"start": row["start"][0], "integrate": row["integrate"][0], "doc_prs": row["doc_prs"][0], "release_strategy": row["release_strategy"]}


def review_allowed(kind: str) -> tuple[list[str], list[str]]:
    """種類が許す `checks` と `diff`（設定のスキーマの `branch_flow.reviews` の列挙）。"""
    props = contracts.load_json(WORKTREE_SCHEMA)["properties"]["branch_flow"]["properties"][REVIEWS_KEY]["properties"][kind]["properties"]
    return list(props["checks"]["items"]["enum"]), list(props["diff"]["enum"])


def answer_problem(value: Any) -> Optional[str]:
    """`branch_flow` の値が形（スキーマ）とリリース方式の表に合わなければその理由。合えば None。"""
    errs = contracts.schema_errors({"version": 1, ITEM: value}, WORKTREE_SCHEMA)
    if errs:
        return "; ".join(errs[:3])
    flow = {**default_flow(), **obj(value)}
    row = _rows()[flow["release_strategy"]]
    bad = [
        f"{k} = {flow[k]}（release_strategy = {flow['release_strategy']} が許すのは {'・'.join(row[k])}）"
        for k in ("start", "doc_prs", "integrate")
        if flow[k] not in row[k]
    ]
    bad += [
        f"reviews.{k} は release_strategy = {flow['release_strategy']} では書けない"
        for k in obj(obj(value).get(REVIEWS_KEY))
        if k not in row["review_kinds"]
    ]
    return "；".join(bad) if bad else None


def bad_answer(ans: Any) -> Optional[str]:
    """`write` の回答（`{"value": ...}`）がリリース方式の表に合わなければその理由。回答が無い・不明なら None。"""
    return answer_problem(ans["value"]) if isinstance(ans, dict) and "value" in ans else None


def confidence(hits: int, total: int) -> Optional[str]:
    """標本 `total` 件のうち `hits` 件が合うときの確からしさ。値を出さないなら None。"""
    if total < MIN_FLOW_SAMPLES:
        return None
    r = hits / total
    return "高" if r >= HIGH else "中" if r >= MID else None


def infer_field(counts: Counter[str], command: str, what: str) -> dict[str, Any]:
    """数えた標本から 1 項目を推す。"""
    total = sum(counts.values())
    if not total:
        return {"unknown": f"{what}の標本が無い", "evidence": [{"command": command, "text": "0 件"}]}
    value, hits = counts.most_common(1)[0]
    text = "・".join(f"{k} {n}" for k, n in counts.most_common())
    ev = [{"command": command, "text": f"{what} {total} 件: {text}"}]
    level = confidence(hits, total)
    if level is None:
        why = (
            f"標本が {total} 件で {MIN_FLOW_SAMPLES} 件に満たない"
            if total < MIN_FLOW_SAMPLES
            else f"最も多い値 {value} の割合が {hits}/{total}"
        )
        return {"unknown": why, "evidence": ev}
    return {"value": value, "confidence": level, "evidence": ev}


def _start_from_table(starts: dict[str, Any], release_strategy: dict[str, Any]) -> dict[str, Any]:
    """動いているスプリントブランチが少なければ、リリース方式の表が許す起点を、リリース方式の確からしさのまま出す。"""
    row = _rows().get(str(release_strategy.get("value")))
    if "unknown" not in starts or row is None or len(row["start"]) != 1:
        return starts
    only = row["start"][0]
    text = f"release_strategy = {row['release_strategy']} が許す start は {only} だけ"
    return {
        "value": only,
        "confidence": release_strategy["confidence"],
        "evidence": [*starts["evidence"], {"command": TABLE_COMMAND, "text": text}],
    }


def infer(prs: list[dict[str, str]], base: str, production: str, release_kinds: list[str], sprint_starts: list[str]) -> dict[str, Any]:
    """4 項目を推す。`prs` はマージ済みの PR の `{"base", "head"}`、`release_kinds` は本番チャネル宛ての PR のリリース方式、
    `sprint_starts` は本番チャネルに含まれていないリモートのスプリントブランチごとの起点のチャネル。"""
    integrate = Counter("check" for p in prs if p["head"].startswith(Branch.SPRINT) and p["base"] == base)
    docs = Counter(
        "sprint" if p["base"].startswith(Branch.SPRINT) else "base" if p["base"] == base else "other"
        for p in prs
        if p["head"].startswith(("design/", "spec/"))
    )
    out = {
        "integrate": infer_field(integrate, PR_COMMAND, f"head が {Branch.SPRINT}* でマージ先が {base} の Pull Request"),
        "doc_prs": infer_field(docs, PR_COMMAND, "設計 PR・確定仕様化の PR のマージ先"),
        "release_strategy": infer_field(
            Counter(release_kinds), PR_COMMAND + " と git rev-list --first-parent", f"{production} 宛ての Pull Request の head"
        ),
    }
    starts = infer_field(
        Counter(sprint_starts),
        "git merge-base <スプリントブランチ> <リモート名>/<ベースブランチ>・<リモート名>/<本番チャネル> と git rev-list --first-parent",
        "本番に入っていないスプリントブランチの起点",
    )
    return {"start": _start_from_table(starts, out["release_strategy"]), **out}


def as_item(fields: dict[str, Any], extra_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """測定の結果の 1 項目。4 項目すべてが `高` で組み合わせがリリース方式の表に合えば測った値、ほかは質問（候補は推した値）。"""
    fields = {k: {**v, "evidence": [*seq(v.get("evidence")), *(extra_evidence if k == "release_strategy" else [])]} for k, v in fields.items()}
    proposal = {k: v["value"] for k, v in fields.items() if "value" in v}
    unsure = any(v.get("confidence") != "高" for v in fields.values())
    problem = None if unsure else answer_problem(proposal)
    evidence = [{"command": TABLE_COMMAND, "text": f"測った組み合わせが表に合わない: {problem}"}] if problem else []
    item = question({ITEM: proposal}, evidence) if unsure or problem else measured(proposal)
    item["fields"] = mask(fields)
    return item


def _succeeds(tree: Tree, *args: str) -> bool:
    return tree.git(*args) is not None


def _out(tree: Tree, *args: str) -> str:
    got = tree.git(*args)
    return got if got is not None else ""


def classify_release(tree: Tree, merge_commit: str, head: str, base: str) -> Optional[str]:
    """本番チャネル宛ての PR 1 件のリリース方式。head がベースブランチなら丸ごと。リリースブランチなら、その first-parent の列が
    マージの前の本番チャネル（マージのコミットの第 1 親）へ届けば選んで集める、届かなければ丸ごと。"""
    if head == base:
        return "merge_base_branch"
    tip, before = f"{merge_commit}^2", f"{merge_commit}^1"
    if not head.startswith(Branch.RELEASE) or not _succeeds(tree, "rev-parse", "--verify", "--quiet", tip):
        return None
    chain = _out(tree, "rev-list", "--first-parent", "--max-count=200", tip).split()
    first = _out(tree, "rev-parse", before).strip()
    return "release_branch" if first and first in chain else "merge_base_branch"


def sprint_start(tree: Tree, ref: str, base: str, production: str) -> Optional[str]:
    """本番チャネルに含まれていないスプリントブランチの起点のチャネル（`base` / `production`。旧いと同じ 3 ステップの判定）。"""
    remote_prod, remote_base = f"{tree.remote}/{production}", f"{tree.remote}/{base}"
    if _succeeds(tree, "merge-base", "--is-ancestor", ref, remote_prod):
        return None
    mb_prod = _out(tree, "merge-base", ref, remote_prod).strip()
    if mb_prod and not _succeeds(tree, "merge-base", "--is-ancestor", mb_prod, remote_base):
        return "production"
    mb = _out(tree, "merge-base", ref, remote_base).strip()
    if not mb:
        return None
    if not _succeeds(tree, "merge-base", "--is-ancestor", mb, remote_prod):
        return "base"
    chain = _out(tree, "rev-list", "--first-parent", f"^{mb}^@", remote_prod).split()
    return "production" if mb in chain else "base"


def _rulesets(gh: Gh) -> dict[str, str]:
    command = f"gh api repos/{gh.repo}/rulesets"
    try:
        rules = seq(gh.get(f"repos/{gh.repo}/rulesets"))
    except GhUnavailable as exc:  # 守らせている仕組みは根拠の行だけで、測定を止めない
        return {"command": command, "text": f"読めない: {str(exc)[:120]}"}
    names = "・".join(str(obj(r).get("name")) for r in rules)
    return {"command": command, "text": "ルールセット: " + (names if names else "無し")}


def collect_flow(tree: Tree, gh: Gh, base: str, production: str) -> dict[str, Any]:
    """git と gh で標本を集めて推す。gh が使えなければ `GhUnavailable`（呼ぶ側が項目を不明にする）。"""
    pulls = [p for p in seq(gh.get(f"repos/{gh.repo}/pulls?state=closed&per_page={PR_LIMIT}")) if isinstance(p, dict)]
    prs = _merged_prs(pulls)
    item = as_item(_infer_flow(tree, prs, base, production), [_rulesets(gh)])
    item[REVIEWS_KEY] = _reviews(gh, pulls, base, production)
    return item


def _merged_prs(pulls: list[dict[str, Any]]) -> list[dict[str, str]]:
    """REST の Pull Request のうちマージ済みのものを、推す標本の `{"base", "head", "merge"}` にする。"""
    return [
        {"base": p["base"]["ref"], "head": p["head"]["ref"], "merge": str(p["merge_commit_sha"]) if p.get("merge_commit_sha") else ""}
        for p in pulls
        if p.get("merged_at")
    ]


def _infer_flow(tree: Tree, prs: list[dict[str, str]], base: str, production: str) -> dict[str, Any]:
    """git で本番チャネル宛ての PR のリリース方式とスプリントブランチの起点を採り、4 項目を推す。"""
    kinds = [classify_release(tree, p["merge"], p["head"], base) for p in prs if p["base"] == production and p["merge"]]
    refs = tree.refs(f"refs/remotes/{tree.remote}/{Branch.SPRINT}")
    starts = [sprint_start(tree, r, base, production) for r in refs]
    return infer(prs, base, production, [k for k in kinds if k], [s for s in starts if s])


def _reviews(gh: Gh, pulls: list[dict[str, Any]], base: str, production: str) -> dict[str, Any]:
    """レビューの単位の項目。gh で読めなければ各項目を不明にする（4 項目の測定を止めない）。"""
    try:
        return mask(collect_reviews(gh, pulls, base, production))
    except GhUnavailable as exc:  # レビューの単位は 4 項目の測定を止めない
        return {k: {"unknown": f"読めない: {str(exc)[:120]}", "evidence": []} for k in REVIEW_KINDS}


def measure(tree: Tree, gh_repo: Optional[str], base: Optional[str], production: Optional[str]) -> dict[str, Any]:
    """`measure` の 1 項目。gh・ベースブランチ・本番チャネルが無ければ不明。"""
    if not base or not production:
        return unknown("ベースブランチか本番チャネルを決められない")
    if not gh_repo:
        return unknown(f"{tree.remote} が GitHub でない（gh で Pull Request を読めない）")
    try:
        return collect_flow(tree, Gh(tree.root, gh_repo, tree.deadline), base, production)
    except GhUnavailable as exc:
        return unknown(f"gh を使えない: {exc}")


def pr_kind(head: str, base_ref: str, base: str, production: str) -> Optional[str]:
    """PR の head とマージ先から PR の種類。当たらなければ None。"""
    if head.startswith(Branch.IMPL):
        return "impl"
    if head.startswith(Branch.SPRINT) and base_ref == base:
        return "sprint"
    if head.startswith(Branch.RELEASE) and base_ref == production:
        return "release"
    if head.startswith(Branch.REVIEW) and not head.endswith(Branch.REVIEW_BASE_SUFFIX):
        return "release"
    return next((k for pre, k in DOC_PREFIXES if head.startswith(pre)), None)


def markers_of(gh: Gh, number: int) -> frozenset[str]:
    """PR のレビューとコメントから、掛けたレビューのマーカーの組。読めなければ `GhUnavailable`（呼ぶ側が標本から外す）。"""
    texts = {
        "reviews": [str(obj(r).get("body")) for r in seq(gh.get(f"repos/{gh.repo}/pulls/{number}/reviews?per_page=100"))],
        "comments": [str(obj(c).get("body")) for c in seq(gh.get(f"repos/{gh.repo}/issues/{number}/comments?per_page=100"))],
    }
    return frozenset(
        c for c, where, mark in REVIEW_MARKS if any(t.lstrip().startswith(mark) if where == "reviews" else mark in t for t in texts[where])
    )


def _review_diff(kind: str, checks: list[str], release_markers: dict[str, int]) -> str:
    """推したレビューの対象の差分。レビューを掛けなければ none、リリースは cross-review のマーカーの多い側。"""
    if not checks:
        return "none"
    if kind == "release":
        resolution = release_markers["resolution"] if "resolution" in release_markers else 0
        pr = release_markers["pr"] if "pr" in release_markers else 0
        return "resolution" if resolution >= max(pr, 1) else "pr"
    return next(d for d in review_allowed(kind)[1] if d != "none")


def _infer_kind(kind: str, got: list[frozenset[str]], release_markers: dict[str, int], unreadable: int) -> dict[str, Any]:
    """1 つの種類のマーカーの組から推す。`unreadable` は読めなかった PR の件数。並びはステップを置く順（スキーマの列挙の順）。"""
    order = review_allowed("sprint")[0]
    allowed = frozenset(review_allowed(kind)[0])
    keys = ("+".join(c for c in order if c in m & allowed) for m in got)
    est = infer_field(Counter(k if k else "(無し)" for k in keys), REVIEWS_COMMAND, f"{kind} の PR のマーカー")
    if unreadable:
        est["evidence"].append({"command": REVIEWS_COMMAND, "text": f"読めない PR {unreadable} 件"})
    if "value" in est:
        checks = [] if est["value"] == "(無し)" else est["value"].split("+")
        est["value"] = {"checks": checks, "diff": _review_diff(kind, checks, release_markers)}
    return est


def infer_reviews(samples: dict[str, list[frozenset[str]]], release_markers: dict[str, int], unreadable: dict[str, int]) -> dict[str, Any]:
    """種類ごとのマーカーの組から、掛けるレビュー・対象の差分・確からしさを推す。標本が足りない種類は `unknown` と理由。"""
    return {
        k: _infer_kind(k, samples[k] if k in samples else [], release_markers, unreadable[k] if k in unreadable else 0) for k in REVIEW_KINDS
    }


def _pick_samples(
    pulls: list[dict[str, Any]], base: str, production: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """(種類ごとの直近のマージ済みの PR を最大 `REVIEW_SAMPLES` 件, バージョンごとの衝突の解消のレビューの PR)。"""
    chosen: dict[str, list[dict[str, Any]]] = {k: [] for k in REVIEW_KINDS}
    reviews: dict[str, dict[str, Any]] = {}
    for p in pulls:
        head = p["head"]["ref"]
        kind = pr_kind(head, p["base"]["ref"], base, production)
        if kind is not None and head.startswith(Branch.REVIEW):
            reviews.setdefault(head[len(Branch.REVIEW) :], p)
        elif kind is not None and p.get("merged_at") and len(chosen[kind]) < REVIEW_SAMPLES:
            chosen[kind].append(p)
    return chosen, reviews


def _sample(gh: Gh, kind: str, p: dict[str, Any], reviews: dict[str, dict[str, Any]]) -> tuple[frozenset[str], frozenset[str]]:
    markers = markers_of(gh, p["number"])
    rv = reviews.get(p["head"]["ref"][len(Branch.RELEASE) :].split("-")[0]) if kind == "release" else None
    return markers, (markers_of(gh, rv["number"]) if rv is not None else frozenset())


def collect_reviews(gh: Gh, pulls: list[dict[str, Any]], base: str, production: str) -> dict[str, Any]:
    """種類ごとに直近の PR を最大 `REVIEW_SAMPLES` 件選び、マーカーを集めて推す。衝突の解消のレビューの PR は同じバージョンの本番チャネル宛の
    PR の標本へ寄せる。"""
    chosen, reviews = _pick_samples(pulls, base, production)
    samples: dict[str, list[frozenset[str]]] = {k: [] for k in REVIEW_KINDS}
    unreadable: Counter[str] = Counter()
    release_markers: Counter[str] = Counter()
    for kind, prs in chosen.items():
        for p in prs:
            try:
                markers, extra = _sample(gh, kind, p, reviews)
            except GhUnavailable:  # 読めない PR は標本から外し、根拠に件数を出す
                unreadable[kind] += 1
                continue
            release_markers["pr"] += int("cross-review" in markers and kind == "release")
            release_markers["resolution"] += int("cross-review" in extra)
            samples[kind].append(markers | extra)
    return infer_reviews(samples, dict(release_markers), dict(unreadable))


def declared_flow(worktree: Optional[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """設定（`.ndf/worktree.json`）の `branch_flow` を既定値で埋めた 4 項目・書いたレビューの行・書いたか。表に合わなければ `ValueError`。"""
    raw = obj(worktree).get(ITEM)
    if raw is not None:
        problem = answer_problem(raw)
        if problem:
            raise ValueError(f"branch_flow がリリース方式の表に合わない: {problem}")
    flow = {**default_flow(), **{k: v for k, v in obj(raw).items() if k in FLOW_KEYS}}
    return flow, obj(obj(raw).get(REVIEWS_KEY)), raw is not None


def _drift_row(name: str, declared: Any, estimate: dict[str, Any], proposal: str) -> dict[str, Any]:
    """ずれの結果行 1 つ。`estimate` は測った項目（`value`・`confidence`・`evidence`）。"""
    return {
        "kind": "drift",
        "name": name,
        "declared": declared,
        "measured": estimate["value"],
        "confidence": estimate.get("confidence"),
        "evidence": seq(estimate.get("evidence")),
        "proposal": proposal,
    }


def drift(worktree: Optional[dict[str, Any]], item: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """設定（無ければ既定値の行）と測った値のずれ。測れなかった項目は比べない。ずれが無ければ空。"""
    want, reviews, _ = declared_flow(worktree)
    fields = obj(obj(item).get("fields"))
    rows = []
    for key in FLOW_KEYS:
        f = obj(fields.get(key))
        if "value" not in f or f["value"] == want[key]:
            continue
        proposal = f"{ITEM}.{key} を {f['value']} にする（同じ release_strategy の行が許す組み合わせに揃えてから write で書く）"
        rows.append(_drift_row(f"{ITEM}.{key}", want[key], f, proposal))
    return rows + _review_drift(reviews, obj(obj(item).get(REVIEWS_KEY)))


def _review_drift(declared: dict[str, Any], est: dict[str, Any]) -> list[dict[str, Any]]:
    """レビューの単位は、書いていない種類を既定値の 2 列（課題のまとまりの記録の有無）のどちらかに合えばずれとしない。"""
    rows = []
    for kind in REVIEW_KINDS:
        e = obj(est.get(kind))
        row = declared.get(kind)
        wants = [row] if row else [REVIEW_DEFAULTS[kind][b] for b in (True, False)]
        if "value" not in e or e["value"] in wants:
            continue
        key = f"{ITEM}.{REVIEWS_KEY}.{kind}"
        proposal = f"{key} を {e['value']} にする（成り立たない組み合わせは check が直すキーと理由を出す）"
        declared_value = wants[0] if row else {"既定値": wants}
        rows.append(_drift_row(key, declared_value, e, proposal))
    return rows
