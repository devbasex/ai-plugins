"""branch_strategy_measure.py: 既存のリポジトリの事例と文書を測り、セクションの値・値ごとの根拠・問いを出す（#3672 の決定 6・I11）。

キーの分け方は既定の表（`data/branch-strategy-presets.json`）の `measured_keys` が持つ。

- 履歴から推すキー: 事例が `min_samples` 件以上・`min_ratio` 以上で合うときだけ値にする（`count_key`）
- 今の正本で決まるキー: 既定ブランチ・版数のファイル・`plugin.json`・ワークフロー・本番ブランチの ruleset の 1 件で決める。
  正本が無いことは値の根拠にし、読めないときだけ問う
- 種類の既定から埋めるキー: 測った値がすべて合う種類の行（`pick_kind`）から埋める

食い違いと根拠不足は書かずに問う（`questions`）。GitHub は `api`（`path → (値, 読めない理由)`）で読み、テストは差し替える。
取り込み方 `release_branch` は事例（PR の宛先）から見分けられないため、測った値は `merge_base_branch` か `trunk` になる。
`release_branch` のリポジトリは、起点（`sprint.start`）と組み合わせの規則が合わずに問いになり、答えで書く。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import branch_strategy as bs
import proc
from strategy_rows import RELEASE_PREFIX
from project_lib import branch_strategy_conflicts as conflicts_of
from project_lib import branch_strategy_write as bw
from release_lib.land import TRAILER_HEAD, TRAILER_PR, TRAILER_TIP

Api = Callable[[str], tuple[Any, str]]
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")
CD_MARKS = ("gh release create", "action-gh-release", "git push origin --tags", "git tag ")


@dataclass
class Samples:
    """集めた事例。各一覧は `(値, 事例の参照)` か事実の行。`unreadable` は読めなかった種類。"""

    production: str | None = None
    prs: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    rules: Any = None
    workflows: dict[str, str] = field(default_factory=dict)
    version_files: dict[str, Any] = field(default_factory=dict)
    plugins: dict[str, list[str]] = field(default_factory=dict)
    unreadable: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Measured:
    """1 つのキーの測った結果。`why` が None なら `value` と `evidence` が決まっている。"""

    value: Any = None
    evidence: list[dict] = field(default_factory=list)
    why: str | None = None
    candidates: list = field(default_factory=list)


def default_api(root: Path) -> Api:
    import release_case

    return lambda path: release_case._gh_read(root, path)


def _default_branch(root: Path, api: Api) -> str | None:
    repo, _ = api("repos/{owner}/{repo}")
    if isinstance(repo, dict) and repo.get("default_branch"):
        return repo["default_branch"]
    head = proc.git_out(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return head.split("/", 1)[-1] if head else None


def local_facts(root: Path, s: Samples) -> None:
    wf = Path(root) / ".github" / "workflows"
    for p in sorted(wf.glob("*.y*ml")) if wf.is_dir() else []:
        try:
            s.workflows[p.name] = p.read_text(encoding="utf-8")
        except OSError:
            s.warnings.append(f"読めないワークフロー: {p}")
    pkg = Path(root) / "package.json"
    if pkg.is_file():
        try:
            if "version" in json.loads(pkg.read_text(encoding="utf-8")):
                s.version_files["package.json"] = "/version"
        except (OSError, ValueError):
            s.warnings.append("読めない: package.json")
    for pj in sorted(Path(root).glob("plugins/*/.claude-plugin/plugin.json")) + sorted(Path(root).glob(".claude-plugin/plugin.json")):
        try:
            name = json.loads(pj.read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError):
            continue
        if name:
            runtimes = ["claude"] + (["codex"] if (pj.parent.parent / ".codex-plugin" / "plugin.json").is_file() else [])
            s.plugins[name] = runtimes


def cd_workflows(s: Samples) -> list[str]:
    """CD の印（Release・タグを作るコマンド）を含むワークフローの名前（昇順）。"""
    return sorted(n for n, text in s.workflows.items() if any(m in text for m in CD_MARKS))


def single_plugin(s: Samples) -> tuple[str, list[str]] | None:
    """プラグインが 1 つだけのときの名前とランタイム。0 個か 2 個以上なら None。"""
    return next(iter(s.plugins.items())) if len(s.plugins) == 1 else None


def _merged_prs(got: list) -> list[dict]:
    return [
        {"number": p["number"], "base": (p.get("base") or {}).get("ref"), "head": (p.get("head") or {}).get("ref")}
        for p in got
        if isinstance(p, dict) and p.get("merged_at")
    ]


def _values_of(key: str) -> Callable[[list], list[str]]:
    """一覧の各要素から `key` の値（空でないもの）を取り出す関数。"""
    return lambda got: [x[key] for x in got if isinstance(x, dict) and x.get(key)]


# 集める一覧: (Samples の属性名, API のパス（{limit} を入れる）, 取り出し方)。属性名は打ち切り・読めないの記録の名前も兼ねる
_LISTINGS = (
    ("prs", "repos/{{owner}}/{{repo}}/pulls?state=closed&per_page={limit}", _merged_prs),
    ("tags", "repos/{{owner}}/{{repo}}/tags?per_page={limit}", _values_of("name")),
    ("releases", "repos/{{owner}}/{{repo}}/releases?per_page={limit}", _values_of("tag_name")),
)


def collect_samples(root: Path, api: Api, deadline: float, limit: int, clock: Callable[[], float] | None = None) -> Samples:
    """直近のマージ済み PR・タグ・Release・本番ブランチの履歴・ruleset・ワークフロー・版数のファイルを集める。`clock` は締切の時計（既定 time.monotonic）。"""
    clock = clock or time.monotonic
    s = Samples()
    s.production = _default_branch(root, api)
    if s.production is None:
        s.unreadable.append("default_branch")
    for attr, path, extract in _LISTINGS:
        if clock() > deadline:
            s.warnings.append(f"時間切れで打ち切った: {attr}")
            continue
        got, why = api(path.format(limit=limit))
        if not isinstance(got, list):
            s.unreadable.append(attr)
            continue
        setattr(s, attr, extract(got)[:limit])
    if s.production:
        _collect_production_commits(root, s, limit)
        _collect_rules(api, s)
    local_facts(root, s)
    return s


def _collect_rules(api: Api, s: Samples) -> None:
    """本番ブランチに効く ruleset を読み、承認の数・merge queue・迂回の可否の事実にする。"""
    import release_case

    rules, why = api(f"repos/{{owner}}/{{repo}}/rules/branches/{s.production}")
    if why == "missing":
        s.rules = release_case.RuleFacts(False, 0, False, False, None)
    elif isinstance(rules, list):
        byp = []
        for rid in sorted({r.get("ruleset_id") for r in rules if isinstance(r, dict) and r.get("ruleset_id")}):
            got, _ = api(f"repos/{{owner}}/{{repo}}/rulesets/{rid}")
            byp.append(got.get("current_user_can_bypass") if isinstance(got, dict) else None)
        s.rules = release_case.rule_facts_of(rules, byp)
    else:
        s.unreadable.append("ruleset")


def _collect_production_commits(root: Path, s: Samples, limit: int) -> None:
    """本番ブランチの最初の親の履歴を 1 回の `git log` で読む。NDF が入れた取り込みの記録（trailer）の PR も事例にする。"""
    ref = next((r for r in (f"origin/{s.production}", s.production) if proc.git_out(root, "rev-parse", "--verify", "--quiet", r)), None)
    if ref is None:
        s.unreadable.append("history")
        return
    fmt = f"%H%x00%P%x00%s%x00%(trailers:key={TRAILER_PR},key={TRAILER_HEAD},key={TRAILER_TIP},separator=%x1f)%x1e"
    out = proc.git_out(root, "log", "--first-parent", f"-n{limit}", f"--format={fmt}", ref) or ""
    known = {p["number"] for p in s.prs}
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x00")
        if len(parts) < 4:
            continue
        sha, parents, subject, trailers = parts[0], parts[1].split(), parts[2], parts[3]
        tr = dict(t.split(": ", 1) for t in trailers.split("\x1f") if ": " in t)
        s.commits.append({"sha": sha, "parents": len(parents), "subject": subject, "landed": tr.get(TRAILER_PR)})
        if tr.get(TRAILER_PR) and parents and parents[0] == tr.get(TRAILER_TIP):
            n = int(tr[TRAILER_PR]) if tr[TRAILER_PR].isdigit() else None
            if n and n not in known:
                s.prs.append({"number": n, "base": s.production, "head": None, "landed": sha})
                known.add(n)


def count_key(pointer: str, observed: list[tuple[Any, str]], rule: dict) -> Measured:
    """履歴から推すキー 1 つについて、値ごとの事例を数えて値と根拠を決める。"""
    total = len(observed)
    if total < rule["min_samples"]:
        return Measured(why="insufficient", evidence=[{"source": "pr", "ref": r, "value": str(v)} for v, r in observed])
    counts: dict[str, list] = {}
    for v, r in observed:
        counts.setdefault(bs.canonical_json(v), []).append((v, r))
    best = max(counts.values(), key=len)
    if len(best) / total >= rule["min_ratio"]:
        return Measured(best[0][0], [{"source": "pr", "ref": r, "value": str(v)} for v, r in best])
    return Measured(
        why="conflict",
        candidates=[json.loads(k) for k in sorted(counts, key=lambda k: -len(counts[k]))],
        evidence=[{"source": "pr", "ref": r, "value": str(v)} for v, r in observed],
    )


def decide_merge_method(facts, release_strategy: str) -> Measured:
    """本番ブランチの ruleset と履歴から推した取り込み方の組で `merge.method` を決める。"""
    if facts is None:
        return Measured(why="insufficient", evidence=[{"source": "ruleset", "ref": "unreadable", "value": ""}])
    trunk = release_strategy == "trunk"
    if facts.present and facts.queue:
        v = "merge-queue"
    elif facts.present and facts.up_to_date:
        v = "strict"
    elif facts.present:
        if trunk:
            return Measured(
                why="conflict",
                candidates=["merge-queue", "strict"],
                evidence=[{"source": "ruleset", "ref": "present", "value": "no queue, no up-to-date"}],
            )
        v = "direct"
    else:
        v = "strict" if trunk else "direct"
    return Measured(
        v,
        [
            {"source": "ruleset", "ref": "present" if facts.present else "absent", "value": v},
            {"source": "pr", "ref": "release_strategy", "value": release_strategy},
        ],
    )


def _tag_shape(tag: str, plugins: dict) -> str | None:
    if not VERSION.search(tag):
        return None
    shape = VERSION.sub("{version}", tag, count=1)
    for name in plugins:
        if shape.startswith(f"{name}--"):
            return "{plugin}" + shape[len(name) :]
    return shape


def _history_keys(s: Samples, rule: dict) -> dict[str, Measured]:
    prod = s.production
    # 長く残るブランチ（ほかの PR の宛先）からの PR と版上げの PR は、スプリントの事例に数えない
    long_lived = {p.get("base") for p in s.prs}
    units = [p for p in s.prs if p.get("base") and not (p.get("head") or "").startswith(RELEASE_PREFIX) and p.get("head") not in long_lived]
    strat = [("trunk" if p["base"] == prod else "merge_base_branch", f"#{p['number']}") for p in units]
    out = {"/release_strategy": count_key("/release_strategy", strat, rule)}
    out["/branches/base"] = count_key("/branches/base", [(p["base"], f"#{p['number']}") for p in units], rule)
    out["/sprint/start"] = count_key(
        "/sprint/start", [("production" if p["base"] == prod else "base", f"#{p['number']}") for p in units], rule
    )
    if not s.tags and not s.releases and "tags" not in s.unreadable and "releases" not in s.unreadable:
        zero = [{"source": "tag", "ref": "0 件", "value": "0"}, {"source": "release", "ref": "0 件", "value": "0"}]
        out["/release/tag"] = Measured(None, zero)
        out["/release/github_release"] = Measured(False, zero)
    else:
        shapes = [(sh, t) for t in s.tags if (sh := _tag_shape(t, s.plugins))]
        out["/release/tag"] = _relabel(count_key("/release/tag", shapes, rule), "tag")
        rel = set(s.releases)
        out["/release/github_release"] = _relabel(count_key("/release/github_release", [(t in rel, t) for t in s.tags], rule), "release")
    merges = [
        ("merge" if c["parents"] >= 2 else "squash", c["sha"][:12])
        for c in s.commits
        if c["parents"] >= 2 or re.search(r"\(#\d+\)$", c["subject"]) or c.get("landed")
    ]
    out["/merge/commit"] = _relabel(count_key("/merge/commit", merges, rule), "pr")
    return out


def _relabel(m: Measured, source: str) -> Measured:
    for e in m.evidence:
        e["source"] = source
    return m


def _source_keys(s: Samples, release_strategy: str | None) -> dict[str, Measured]:
    out: dict[str, Measured] = {}
    if s.production:
        out["/branches/production"] = Measured(s.production, [{"source": "repo", "ref": "default_branch", "value": s.production}])
    else:
        out["/branches/production"] = Measured(why="insufficient")
    plugin = single_plugin(s)
    if plugin:
        name, runtimes = plugin
        out["/release/plugin"] = Measured(name, [{"source": "repo", "ref": "plugin.json", "value": name}])
        out["/release/runtimes"] = Measured(runtimes, [{"source": "repo", "ref": "plugin.json", "value": ",".join(runtimes)}])
        out["/release/version_store"] = Measured({"kind": "plugin"}, [{"source": "repo", "ref": "plugin.json", "value": "plugin"}])
    elif s.version_files:
        ((path, pointer),) = list(s.version_files.items())[:1]
        out["/release/version_store"] = Measured(
            {"kind": "json", "path": path, "pointer": pointer}, [{"source": "repo", "ref": path, "value": pointer}]
        )
    else:
        out["/release/version_store"] = Measured({"kind": "none"}, [{"source": "repo", "ref": "版数のファイルが無い", "value": "none"}])
    cd = cd_workflows(s)
    out["/release/cd"] = Measured(
        {"workflow": cd[0]} if cd else None,
        [{"source": "workflow", "ref": cd[0] if cd else "CD のワークフローが無い", "value": cd[0] if cd else "null"}],
    )
    channels = [{"name": "stable", "ref": s.production}] if s.production else []
    out["/channels"] = Measured(channels, [{"source": "workflow", "ref": "stable", "value": s.production or ""}])
    out["/merge/method"] = decide_merge_method(s.rules, release_strategy) if release_strategy else Measured(why="insufficient")
    return out


def _preset_matches(row: dict, pointer: str, value: Any, presets_row: dict) -> bool:
    got = bw.get_pointer(row, pointer)
    if pointer == "/merge/method" and got == "merge-queue":
        return value in ("merge-queue", presets_row.get("merge_method_without_queue"))
    if pointer == "/release/version_store":
        return got.get("kind") == (value or {}).get("kind")
    return bs.canonical_json(got) == bs.canonical_json(value)


COMPARED = ("/release_strategy", "/sprint/start", "/release/version_store", "/merge/commit", "/merge/method")


def pick_kind(measured: dict[str, Measured], presets: dict) -> tuple[str | None, list[str]]:
    """測った値がすべて合う種類の既定の行を 1 つ選ぶ。形・CD とその産物・チャネルは比べない。"""
    hits = [
        k
        for k, row in presets["kinds"].items()
        if all(_preset_matches(row["section"], p, measured[p].value, row) for p in COMPARED if p in measured)
    ]
    if len(hits) == 1:
        return hits[0], []
    return None, hits or list(presets["kinds"])


def measure_strategy(
    root: Path, api: Api | None = None, deadline: float | None = None, presets: dict | None = None, prefer: str | None = None
) -> dict:
    """既存のリポジトリを測り、作成の結果（値・根拠・問い）を組む。書かない。"""
    presets = presets or bw.load_presets()
    rule = presets["evidence"]
    if deadline is None:
        import limits

        deadline = time.monotonic() + limits.seconds("branch-strategy.measure")
    s = collect_samples(root, api or default_api(root), deadline, rule["limit"])
    hist = _history_keys(s, rule)
    rs = hist["/release_strategy"].value if hist["/release_strategy"].why is None else None
    src = _source_keys(s, rs)
    measured = {**hist, **src}
    evidence = {p: m.evidence for p, m in measured.items()}
    questions = [{"pointer": p, "why": m.why, "candidates": m.candidates, "evidence": m.evidence} for p, m in measured.items() if m.why]
    for u in s.unreadable:
        for q in questions:
            q.setdefault("unreadable", []).append(u)
    # 問いを返すときも、確定した値と根拠を持つ（答えを足した後に `complete_answers` が種類の既定から全体を組む）
    values: dict = {}
    for p, m in measured.items():
        if not m.why:
            bw.set_pointer(values, p, m.value)
    if rs == "trunk":
        bw.set_pointer(values, "/branches/base", s.production)
    kept = [
        {"source": e["source"], "ref": e["ref"], "value": e["value"], "pointer": p}
        for p, m in measured.items()
        if not m.why
        for e in m.evidence
    ]
    base = {"status": "ask", "kind": None, "values": values, "evidence": kept, "questions": questions, "warnings": s.warnings}
    if questions:
        return base
    conflicts = conflicts_of.rule_conflicts(values, evidence) + conflicts_of.doc_conflicts(root, values, evidence)
    if conflicts:
        return {**base, "questions": conflicts}
    kind, candidates = pick_kind(measured, presets)
    if kind is None:
        if prefer in candidates:
            candidates = [prefer] + [c for c in candidates if c != prefer]
        return {**base, "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": candidates, "evidence": []}]}
    section = _section_from_kind(presets, kind, measured, values)
    out = {
        "status": "ok",
        "kind": kind,
        "values": section,
        "evidence": _flat_evidence(evidence, presets, kind),
        "questions": [],
        "warnings": s.warnings,
    }
    approval = _approval_question(section, s.rules)
    if approval:
        out["status"] = "ask"
        out["questions"] = approval
    return out


def complete_answers(prior: dict, answers: dict, presets: dict | None = None) -> dict:
    """問いを返した測った結果（確定した値と根拠だけを持つ）に答えを入れ、種類の既定の行から完全なセクションを組む。

    種類は答えの `/kind`、無ければ確定した値と答えに合う 1 つ。決まらなければ種類の問いを返す。答えが既定から変えた値は
    `apply_answers` が種類を `custom` にする。"""
    presets = presets or bw.load_presets()
    rest = {p: v for p, v in answers.items() if p != "/kind"}
    known = json.loads(json.dumps(prior.get("values") or {}))
    for p, v in rest.items():
        bw.set_pointer(known, p, v)
    asked = {
        "status": "ask",
        "kind": None,
        "values": known,
        "evidence": prior.get("evidence") or [],
        "warnings": prior.get("warnings") or [],
    }
    if not bw.get_pointer(known, "/branches/base"):
        return {**asked, "questions": [{"pointer": "/branches/base", "why": "missing", "candidates": [], "evidence": []}]}
    pointers = {e["pointer"] for e in prior.get("evidence") or [] if e.get("pointer") and e.get("source") != "preset"}
    kind = answers.get("/kind")
    if kind is None:
        kind, candidates = pick_kind({p: Measured(bw.get_pointer(known, p)) for p in pointers | set(rest)}, presets)
        if kind is None:
            return {**asked, "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": candidates, "evidence": []}]}
    measured = {p: Measured(bw.get_pointer(known, p)) for p in pointers - set(rest) - {"/branches/base"}}
    section = _section_from_kind(presets, kind, measured, known)
    evidence = [
        *asked["evidence"],
        *({"source": "preset", "ref": kind, "value": p, "pointer": p} for p in presets["measured_keys"]["preset"]),
    ]
    return bw.apply_answers({**asked, "kind": kind, "values": section, "evidence": evidence, "questions": []}, rest)


def _section_from_kind(presets: dict, kind: str, measured: dict[str, Measured], values: dict) -> dict:
    """種類の既定の行に測った値を入れたセクション。プラグインの無いリポジトリは plugin・runtimes を落とす。"""
    section = json.loads(json.dumps(presets["kinds"][kind]["section"]))
    for p, m in measured.items():
        bw.set_pointer(section, p, m.value)
    section["branches"]["base"] = values["branches"]["base"]
    if section["release"].get("plugin") is None:
        section["release"].pop("plugin", None)
        section["release"].pop("runtimes", None)
    return section


def _flat_evidence(evidence: dict, presets: dict, kind: str) -> list[dict]:
    """キーごとの根拠と、種類の既定から埋めたキーの根拠を 1 本の並びにする。"""
    flat = [{"source": e["source"], "ref": e["ref"], "value": e["value"], "pointer": p} for p, evs in evidence.items() for e in evs]
    flat += [{"source": "preset", "ref": kind, "value": p, "pointer": p} for p in presets["measured_keys"]["preset"]]
    return flat


def _approval_question(section: dict, rules) -> list[dict]:
    """承認ゲートで受ける行なのに本番ブランチの ruleset が承認を求めるときの問い（無ければ空）。"""
    if section["approval"]["via"] == "gate" and rules is not None and rules.approvals > 0:
        return [
            {
                "pointer": "/approval/via",
                "why": "conflict",
                "candidates": ["gate", "review"],
                "evidence": [{"source": "ruleset", "ref": "required_approving_review_count", "value": str(rules.approvals)}],
            }
        ]
    return []


def classify_repo(root: Path, api: Api | None = None, presets: dict | None = None) -> tuple[str, list[dict]]:
    """新規（4 つとも無い）か既存かを分ける。読めないものは既存とみなす（新規へ倒さない）。"""
    presets = presets or bw.load_presets()
    api = api or default_api(root)
    ev = []
    n = proc.git_out(root, "rev-list", "--count", "HEAD")
    commits = int(n) if n and n.isdigit() else None
    ev.append({"source": "repo", "ref": "commits", "value": str(commits)})
    existing = commits is None or commits > presets["evidence"]["new_repo_max_commits"]
    for kind, path in (
        ("pr", "repos/{owner}/{repo}/pulls?state=closed&per_page=1"),
        ("tag", "repos/{owner}/{repo}/tags?per_page=1"),
        ("release", "repos/{owner}/{repo}/releases?per_page=1"),
    ):
        got, why = api(path)
        hit = not isinstance(got, list) or bool(got)
        ev.append({"source": kind, "ref": path, "value": "unreadable" if not isinstance(got, list) else str(len(got))})
        existing = existing or hit
    wf = Path(root) / ".github" / "workflows"
    has_wf = wf.is_dir() and any(wf.glob("*.y*ml"))
    rules, why = api("repos/{owner}/{repo}/rulesets")
    has_rules = not isinstance(rules, list) or bool(rules)
    ev.append({"source": "workflow", "ref": ".github/workflows", "value": str(has_wf)})
    ev.append({"source": "ruleset", "ref": "rulesets", "value": "unreadable" if not isinstance(rules, list) else str(len(rules))})
    existing = existing or has_wf or has_rules
    return ("existing" if existing else "new"), ev
