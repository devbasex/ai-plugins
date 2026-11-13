"""ブランチ運用の測定（`project-decl.py measure` の `branch_flow`）と、宣言とのずれ（`project-decl.py drift`）。#3121。

測るのは宣言の 4 項目（`start`・`integrate`・`doc_prs`・`promotion`）で、項目ごとに推した値・確からしさ・根拠（打った
コマンドと結果の要約）を出す。根拠が足りない項目は値を出さず `unknown` と理由を出す。

- 標本は直近のマージ済みの Pull Request（最大 100 件）と、origin のスプリントブランチである
- 確からしさ: 推した値に合う標本の割合が 0.9 以上で `高`、0.6 以上で `中`。標本が 3 件未満か 0.6 未満なら `unknown`
- 守らせている仕組み（ルールセット）は根拠の行としてだけ出す

推す規則と判定は純粋な関数（`infer`）にし、git と gh の呼び出し（`collect`）と分ける。

レビューの単位（`branch_flow.reviews`。#3163 の決定 8）は、PR の種類ごとに直近の PR を最大 `REVIEW_SAMPLES` 件読み、
レビューごとの印の有無から推す（`infer_reviews`）。推した値は項目の `reviews` に置き、`write` の自動の書き出し（4 項目の
`proposal`）へは渡さない。
"""

from __future__ import annotations

from collections import Counter

import branch_flow

from .measure_repo import measured, question, unknown
from .secret import mask

MIN_SAMPLES = 3
HIGH, MID = 0.9, 0.6
PR_LIMIT = 100
ITEM = "branch_flow"


def confidence(hits: int, total: int) -> str | None:
    """標本 `total` 件のうち `hits` 件が合うときの確からしさ。値を出さないなら None。"""
    if total < MIN_SAMPLES:
        return None
    r = hits / total
    return "高" if r >= HIGH else "中" if r >= MID else None


def infer_field(counts: Counter, command: str, what: str) -> dict:
    """数えた標本から 1 項目を推す。"""
    total = sum(counts.values())
    if not total:
        return {"unknown": f"{what}の標本が無い", "evidence": [{"command": command, "text": "0 件"}]}
    value, hits = counts.most_common(1)[0]
    text = "・".join(f"{k} {n}" for k, n in counts.most_common())
    ev = [{"command": command, "text": f"{what} {total} 件: {text}"}]
    level = confidence(hits, total)
    if level is None:
        why = f"標本が {total} 件で {MIN_SAMPLES} 件に満たない" if total < MIN_SAMPLES else f"最も多い値 {value} の割合が {hits}/{total}"
        return {"unknown": why, "evidence": ev}
    return {"value": value, "confidence": level, "evidence": ev}


PR_COMMAND = "gh api repos/<R>/pulls?state=closed&per_page=100（マージ済みだけを数える）"


def infer(prs: list[dict], base: str, production: str, release_kinds: list[str], sprint_starts: list[str]) -> dict:
    """4 項目を推す。

    - `prs`: マージ済みの Pull Request の `{"base", "head"}`
    - `release_kinds`: 本番チャネル宛ての Pull Request の head ごとの取り込み方（`whole` / `selected`。`classify_release`）
    - `sprint_starts`: 本番チャネルに含まれていない origin のスプリントブランチごとの起点のチャネル（`base` / `production`）
    """
    sprint = branch_flow.SPRINT_PREFIX
    integrate = Counter("check" for p in prs if p["head"].startswith(sprint) and p["base"] == base)
    docs = Counter(
        "sprint" if p["base"].startswith(sprint) else "base" if p["base"] == base else "other"
        for p in prs
        if p["head"].startswith("design/") or p["head"].startswith("spec/")
    )
    out = {
        "integrate": infer_field(integrate, PR_COMMAND, f"head が {sprint}* で宛先が {base} の Pull Request"),
        "doc_prs": infer_field(docs, PR_COMMAND, "設計 PR・確定仕様化の PR の宛先"),
        "promotion": infer_field(
            Counter(release_kinds), PR_COMMAND + " と git rev-list --first-parent", f"{production} 宛ての Pull Request の head"
        ),
    }
    starts = infer_field(
        Counter(sprint_starts),
        "git merge-base <スプリントブランチ> origin/<ベースブランチ>・origin/<本番チャネル> と git rev-list --first-parent",
        "本番に入っていないスプリントブランチの起点",
    )
    row = branch_flow.PROMOTIONS.get(out["promotion"].get("value") or "")
    if "unknown" in starts and row is not None and len(row.allowed_start) == 1:
        # 動いているスプリントブランチが少なければ、取り込み方の表が許す起点を、取り込み方の確からしさのまま出す
        (only,) = row.allowed_start
        starts = {
            "value": only,
            "confidence": out["promotion"]["confidence"],
            "evidence": [
                *starts["evidence"],
                {"command": "lib/branch_flow.py の取り込み方の表", "text": f"promotion = {row.name} が許す start は {only} だけ"},
            ],
        }
    return {"start": starts, **out}


def as_item(fields: dict, extra_evidence: list[dict]) -> dict:
    """測定の結果の 1 項目。4 項目すべてが `高` で組み合わせが取り込み方の表に合えば測った値、ほかは問い（候補は推した値）。
    表に合わない組（スプリントの起点の推し違いなど）は `write` の自動の書き出しへ渡さない。"""
    fields = {k: {**v, "evidence": [*v.get("evidence", []), *([] if k != "promotion" else extra_evidence)]} for k, v in fields.items()}
    proposal = {k: v["value"] for k, v in fields.items() if "value" in v}
    unsure = any(v.get("confidence") != "高" for v in fields.values())
    problem = None if unsure else answer_problem(ITEM, proposal)
    evidence = [{"command": "lib/branch_flow.py の取り込み方の表", "text": f"測った組み合わせが表に合わない: {problem}"}] if problem else []
    item = question({"branch_flow": proposal}, evidence) if unsure or problem else measured(proposal)
    item["fields"] = mask(fields)
    return item


def classify_release(root, merge_commit: str, head: str, base: str) -> str | None:
    """本番チャネル宛ての Pull Request 1 件の取り込み方。head がベースブランチそのものなら丸ごと。リリースブランチなら、
    その first-parent の列がマージの前の本番チャネル（マージのコミットの第 1 親）へ届けば選んで集める、届かなければ丸ごと。"""
    if head == base:
        return "whole"
    if not head.startswith(branch_flow.RELEASE_PREFIX):
        return None
    tip, before = f"{merge_commit}^2", f"{merge_commit}^1"
    if not branch_flow.git_succeeds(root, "rev-parse", "--verify", "--quiet", tip):
        return None
    chain = branch_flow.git_run(root, "rev-list", "--first-parent", "--max-count=200", tip).stdout.split()
    first = branch_flow.git_run(root, "rev-parse", before).stdout.strip()
    return "selected" if first and first in chain else "whole"


def sprint_start(root, ref: str, base: str, production: str) -> str | None:
    """本番チャネルに含まれていないスプリントブランチの起点のチャネル（`base` / `production`）。

    1. 本番チャネルとの分岐点がベースブランチに無い（本番チャネルにだけあるコミットから切った）なら `production`
    2. ベースブランチとの分岐点が本番チャネルに無い（ベースブランチにだけあるコミットから切った）なら `base`
    3. 分岐点が両方にある（丸ごとのリリースで後から本番に入った、または本番の直しをベースブランチへ戻した）なら、
       分岐点が本番チャネルの first-parent の列にあれば `production`（本番で作ったコミット）、無ければ（リリースの
       マージの第 2 親の側から本番に入ったなら）`base`
    """
    prod, origin_base = f"origin/{production}", f"origin/{base}"
    if branch_flow.ancestor_of(root, ref, prod):
        return None
    mb_prod = branch_flow.git_run(root, "merge-base", ref, prod).stdout.strip()
    if mb_prod and not branch_flow.ancestor_of(root, mb_prod, origin_base):
        return "production"
    mb = branch_flow.git_run(root, "merge-base", ref, origin_base).stdout.strip()
    if not mb:
        return None
    if not branch_flow.ancestor_of(root, mb, prod):
        return "base"
    chain = branch_flow.git_run(root, "rev-list", "--first-parent", f"^{mb}^@", prod).stdout.split()
    return "production" if mb in chain else "base"


def collect_flow(root, gh, base: str, production: str) -> dict:
    """git と gh（`measure_ci.Gh`）で標本を集めて推す。gh が使えなければ例外（呼ぶ側が項目を不明にする）。"""
    pulls = gh.get(f"repos/{gh.repo}/pulls?state=closed&per_page={PR_LIMIT}") or []
    merged = [p for p in pulls if isinstance(p, dict) and p.get("merged_at")]
    prs = [{"base": p["base"]["ref"], "head": p["head"]["ref"], "merge": p.get("merge_commit_sha") or ""} for p in merged]
    kinds = [classify_release(root, p["merge"], p["head"], base) for p in prs if p["base"] == production and p["merge"]]
    refs = branch_flow.git_run(
        root, "for-each-ref", "--format=%(refname:short)", f"refs/remotes/origin/{branch_flow.SPRINT_PREFIX}"
    ).stdout.split()
    starts = [sprint_start(root, r, base, production) for r in refs]
    extra = []
    try:
        rules = gh.get(f"repos/{gh.repo}/rulesets") or []
        extra.append(
            {
                "command": f"gh api repos/{gh.repo}/rulesets",
                "text": "ルールセット: " + ("・".join(str(r.get("name")) for r in rules) or "無し"),
            }
        )
    except Exception as e:  # noqa: BLE001  守らせている仕組みは根拠の行だけで、測定を止めない
        extra.append({"command": f"gh api repos/{gh.repo}/rulesets", "text": f"読めない: {str(e)[:120]}"})
    fields = infer(prs, base, production, [k for k in kinds if k], [s for s in starts if s])
    item = as_item(fields, extra)
    try:
        item[branch_flow.REVIEWS_KEY] = mask(collect_reviews(gh, [p for p in pulls if isinstance(p, dict)], base, production))
    except Exception as e:  # noqa: BLE001  レビューの単位は 4 項目の測定を止めない
        item[branch_flow.REVIEWS_KEY] = {k: {"unknown": f"読めない: {str(e)[:120]}", "evidence": []} for k in branch_flow.REVIEW_KINDS}
    return item


def measure_flow(root, gh_repo: str | None, deadline: float) -> dict:
    """`measure` の 1 項目。gh・ベースブランチ・本番チャネルが無ければ不明。"""
    import repo

    base, production = repo.declared_base(root) or repo.default_branch(root), repo.production_branch(root)[0]
    if not base or not production:
        return unknown("ベースブランチか本番チャネルを決められない")
    if not gh_repo:
        return unknown("origin が GitHub でない（gh で Pull Request を読めない）")
    from .measure_ci import Gh, GhUnavailable

    try:
        return collect_flow(root, Gh(root, gh_repo, deadline), base, production)
    except GhUnavailable as e:
        return unknown(f"gh を使えない: {e}")


REVIEW_SAMPLES = 10
# 掛けたレビューの印（書き手: cross-review の投稿の見出し `lib/result_posts.REVIEW_HEAD`・cross-refactoring の計画の
# コメント `refactor_lib/plan.py`・振る舞いの確認の記録 `behavior-check.py` の `MARK`）。書き手はこのモジュールから
# import できない（外部パッケージの環境・ハイフンの名前のスクリプト）ため、印の頭だけを置く
REVIEW_MARKS = {
    "cross-review": ("reviews", "## 🤖 cross-review"),
    "cross-refactoring": ("comments", "<!-- cross-refactoring plan"),
    "behavior": ("comments", "<!-- ndf:behavior-check"),
}
REVIEWS_COMMAND = "gh api repos/<R>/pulls/<N>/reviews と repos/<R>/issues/<N>/comments（印の有無だけを数える）"
IMPL_PREFIX = "feat/issue-"
DOC_PREFIXES = {"design/": "design", "spec/": "spec"}


def pr_kind(head: str, base_ref: str, base: str, production: str) -> str | None:
    """PR の head と宛先から PR の種類。当たらなければ None。"""
    if head.startswith(IMPL_PREFIX):
        return "impl"
    if head.startswith(branch_flow.SPRINT_PREFIX) and base_ref == base:
        return "sprint"
    if head.startswith(branch_flow.RELEASE_PREFIX) and base_ref == production:
        return "release"
    if head.startswith(branch_flow.REVIEW_PREFIX) and not head.endswith(branch_flow.REVIEW_BASE_SUFFIX):
        return "release"
    return next((k for pre, k in DOC_PREFIXES.items() if head.startswith(pre)), None)


def marks_of(gh, number: int) -> frozenset:
    """PR のレビューとコメントから、掛けたレビューの印の組。読めなければ例外（呼ぶ側が標本から外す）。"""
    texts = {
        "reviews": [str(r.get("body") or "") for r in gh.get(f"repos/{gh.repo}/pulls/{number}/reviews?per_page=100") or []],
        "comments": [str(c.get("body") or "") for c in gh.get(f"repos/{gh.repo}/issues/{number}/comments?per_page=100") or []],
    }
    return frozenset(
        check
        for check, (where, mark) in REVIEW_MARKS.items()
        if any(t.lstrip().startswith(mark) if where == "reviews" else mark in t for t in texts[where])
    )


def _checks_key(checks) -> str:
    return "+".join(c for c in branch_flow.CHECKS if c in checks) or "(無し)"


def infer_reviews(samples: dict[str, list[frozenset]], release_marks: dict[str, int], unreadable: dict[str, int] | None = None) -> dict:
    """種類ごとの印の組から、掛けるレビュー・対象の差分・確からしさを推す。標本が足りない種類は `unknown` と理由。

    `release_marks` はリリースブランチの PR の cross-review の印が、衝突の解消のレビューの PR（`resolution`）と本番チャネル宛の
    PR（`pr`）のどちらに何件あったか。"""
    return {
        kind: _infer_kind(kind, samples.get(kind) or [], release_marks, (unreadable or {}).get(kind)) for kind in branch_flow.REVIEW_KINDS
    }


def _infer_kind(kind: str, got: list[frozenset], release_marks: dict[str, int], unreadable: int | None) -> dict:
    """1 つの種類の印の組から推す。`unreadable` は読めなかった PR の件数。"""
    allowed = branch_flow.REVIEW_ALLOWED[kind]
    counts = Counter(_checks_key(m & frozenset(allowed["checks"])) for m in got)
    est = infer_field(counts, REVIEWS_COMMAND, f"{kind} の PR の印")
    if unreadable:
        est["evidence"].append({"command": REVIEWS_COMMAND, "text": f"読めない PR {unreadable} 件"})
    if "value" not in est:
        return est
    checks = [] if est["value"] == "(無し)" else est["value"].split("+")
    est["value"] = {"checks": checks, "diff": _review_diff(kind, checks, allowed, release_marks)}
    return est


def _review_diff(kind: str, checks: list[str], allowed: dict, release_marks: dict[str, int]) -> str:
    """推したレビューの対象の差分。レビューを掛けなければ none、リリースは cross-review の印の多い側。"""
    if not checks:
        return "none"
    if kind == "release":
        return "resolution" if release_marks.get("resolution", 0) >= max(release_marks.get("pr", 0), 1) else "pr"
    return next(d for d in allowed["diff"] if d != "none")


def _pick_samples(pulls: list[dict], base: str, production: str) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """(種類ごとの直近のマージ済みの PR を最大 `REVIEW_SAMPLES` 件, 版ごとの衝突の解消のレビューの PR)。"""
    chosen: dict[str, list[dict]] = {k: [] for k in branch_flow.REVIEW_KINDS}
    reviews: dict[str, dict] = {}
    for p in pulls:
        head, base_ref = p["head"]["ref"], p["base"]["ref"]
        kind = pr_kind(head, base_ref, base, production)
        if kind is None:
            continue
        if head.startswith(branch_flow.REVIEW_PREFIX):
            reviews.setdefault(head[len(branch_flow.REVIEW_PREFIX) :], p)
        elif p.get("merged_at") and len(chosen[kind]) < REVIEW_SAMPLES:
            chosen[kind].append(p)
    return chosen, reviews


def collect_reviews(gh, pulls: list[dict], base: str, production: str) -> dict:
    """種類ごとに直近の PR を最大 `REVIEW_SAMPLES` 件選び、印を集めて推す。衝突の解消のレビューの PR（閉じてマージしない）は
    同じ版の本番チャネル宛の PR の標本へ寄せる。"""
    chosen, reviews = _pick_samples(pulls, base, production)
    samples: dict[str, list[frozenset]] = {k: [] for k in branch_flow.REVIEW_KINDS}
    unreadable: Counter = Counter()
    release_marks: Counter = Counter()
    for kind, prs in chosen.items():
        for p in prs:
            try:
                marks = marks_of(gh, p["number"])
                extra = frozenset()
                rv = reviews.get(p["head"]["ref"][len(branch_flow.RELEASE_PREFIX) :].split("-")[0]) if kind == "release" else None
                if rv is not None:
                    extra = marks_of(gh, rv["number"])
            except Exception:  # noqa: BLE001  読めない PR は標本から外し、根拠に件数を出す
                unreadable[kind] += 1
                continue
            release_marks["pr"] += "cross-review" in marks and kind == "release"
            release_marks["resolution"] += "cross-review" in extra
            samples[kind].append(marks | extra)
    return infer_reviews(samples, dict(release_marks), dict(unreadable))


def drift(declared: branch_flow.BranchFlow, item: dict | None) -> list[dict]:
    """宣言（無ければ既定値の行）と測った値のずれ。測れなかった項目は比べない。ずれが無ければ空。
    レビューの単位は、書いていない種類を既定値の 2 列（課題のまとまりの記録の有無）のどちらかに合えばずれとしない。"""
    fields = (item or {}).get("fields") or {}
    rows = []
    want = declared.as_dict()
    for key in branch_flow.KEYS:
        f = fields.get(key) or {}
        if "value" not in f or f["value"] == want[key]:
            continue
        rows.append(
            {
                "kind": "drift",
                "name": f"{branch_flow.DECL_KEY}.{key}",
                "declared": want[key],
                "measured": f["value"],
                "confidence": f.get("confidence"),
                "evidence": f.get("evidence", []),
                "proposal": f"{branch_flow.DECL_KEY}.{key} を {f['value']} にする（同じ promotion の行が許す組み合わせに揃えてから write で書く）",
            }
        )
    return rows + _review_drift(declared, (item or {}).get(branch_flow.REVIEWS_KEY) or {})


def _review_drift(declared: branch_flow.BranchFlow, est: dict) -> list[dict]:
    rows = []
    for kind in branch_flow.REVIEW_KINDS:
        e = est.get(kind) or {}
        if "value" not in e:
            continue
        got = e["value"]
        row = declared.reviews.declared.get(kind)
        wants = [row.as_dict()] if row else [branch_flow.REVIEW_DEFAULTS[kind][b].as_dict() for b in (True, False)]
        if got in wants:
            continue
        key = f"{branch_flow.DECL_KEY}.{branch_flow.REVIEWS_KEY}.{kind}"
        rows.append(
            {
                "kind": "drift",
                "name": key,
                "declared": wants[0] if row else {"既定値": wants},
                "measured": got,
                "confidence": e.get("confidence"),
                "evidence": e.get("evidence", []),
                "proposal": f"{key} を {got} にする（成り立たない組み合わせは check が直すキーと理由を出す）",
            }
        )
    return rows


def print_drift(rows: list[dict]) -> str:
    """ずれの行を人が読む形で出し、結果の要約を返す。"""
    for r in rows:
        print(f"ずれ: {r['name']} 宣言 {r['declared']} / 測った {r['measured']}（{r['confidence']}）— {r['proposal']}")
    return f"ブランチ運用の宣言と使い方のずれ {len(rows)} 件" if rows else "ブランチ運用の宣言は使い方と合う"


def answer_problem(key: str, value) -> str | None:
    """`write` の答えの形（取り込み方の表）。合えば None（`merge.resolve_item` の `check` と同じ形）。"""
    try:
        branch_flow.parse_flow(value)
    except branch_flow.FlowError as e:
        return str(e)
    return None


def bad_answer(ans) -> str | None:
    """`write` の答え（`{"value": ...}`）が取り込み方の表に合わなければその理由。答えが無い・不明なら None。"""
    return answer_problem(ITEM, ans["value"]) if isinstance(ans, dict) and "value" in ans else None
