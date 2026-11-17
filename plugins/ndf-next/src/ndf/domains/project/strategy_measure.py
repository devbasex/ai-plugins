"""既存のリポジトリの事例と文書を測り、ブランチ戦略のセクションの値・値ごとの根拠・問いを出す（#3672 の決定 6・I11。#3681）。

キーの分け方は既定の表（`contracts/data/branch-strategy-presets.json`）の `measured_keys` が持つ。

- 履歴から推すキー: 事例が `min_samples` 件以上・`min_ratio` 以上で合うときだけ値にする（`count_key`）
- 今の正本で決まるキー: 既定ブランチ・版数のファイル・プラグインの定義・ワークフロー・本番ブランチの ruleset の 1 件で決める。
  正本が無いことは値の根拠にし、読めないときだけ問う
- 種類の既定から埋めるキー: 測った値がすべて合う種類の行（`pick_kind`）から埋める

食い違い（組み合わせの規則・README などの文書）と根拠不足は書かずに問う（`questions`）。GitHub は `api`
（`path → (値, 読めない理由)`。404 は理由 `missing`）で読み、テストは差し替える。
取り込み方 `release_branch` は事例（PR の宛先）から見分けられないため、測った値は `merge_base_branch` か `trunk` になる。
10 系の `project_lib/branch_strategy_measure.py`・`branch_strategy_conflicts.py` と同じ責務を 11.0 の置き場で持つ。
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf.adapters.git import Git, GitError
from ndf.contracts import limits
from ndf.core import branch_strategy as bs
from ndf.core import json_pointer
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR, TRAILER_TIP
from ndf.domains.project import check
from ndf.domains.project import strategy_write as bw
from ndf.domains.project.items import obj, seq
from ndf.domains.project.measure_ci import Gh, GhUnavailable
from ndf.domains.release.plugins import CLAUDE_MANIFEST, CODEX_MANIFEST, PLUGINS_DIR

Api = Callable[[str], tuple[Any, str]]
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")
CD_MARKS = ("gh release create", "action-gh-release", "git push origin --tags", "git tag ")
# スプリントブランチ（一時的なブランチで、長く残るブランチに数えない）と版上げのブランチの頭
SPRINT_PREFIX, RELEASE_PREFIX = "sprint/", "release/v"
DEFAULT_BRANCH_CANDIDATES = check.KNOWN_BRANCHES[:2]
DOCS = ("README.md", "CONTRIBUTING.md", "AGENTS.md")
DOC_BASE = re.compile(r"--base\s+`?([A-Za-z0-9._/\-]+)`?")
# GitHub の REST の既定の 1 ページの件数（`per_page` を付けないときの値）
DEFAULT_PER_PAGE = 30
MEASURE_LIMIT = "branch-strategy.measure"
COMPARED = ("/release_strategy", "/sprint/start", "/release/version_store", "/merge/commit", "/merge/method")


class RuleFacts(NamedTuple):
    """本番ブランチに効く ruleset と従来のブランチ保護の事実（有無・承認の数・merge queue・最新の取り込みの要否）。"""

    present: bool
    approvals: int
    queue: bool
    up_to_date: bool


NO_RULES = RuleFacts(False, 0, False, False)


@dataclass
class Samples:
    """集めた事例。各一覧は `(値, 事例の参照)` か事実の行。`unreadable` は読めなかった種類。"""

    production: Optional[str] = None
    prs: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    rules: Optional[RuleFacts] = None
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
    why: Optional[str] = None
    candidates: list = field(default_factory=list)


def default_api(root: Path, deadline: float) -> Api:
    """`gh api` で読む `api`。`{owner}/{repo}` は `gh` が作業場所のリモートから埋める。"""
    gh = Gh(Path(root), "", deadline)

    def read(path: str) -> tuple[Any, str]:
        try:
            return gh.get(path), ""
        except GhUnavailable as exc:
            return None, ("missing" if "404" in str(exc) else str(exc))

    return read


def _git_out(root: Path, *args: str) -> Optional[str]:
    done = Git(cwd=Path(root)).attempt(*args)
    return done.stdout.strip() if done.returncode == 0 else None


def _remote(root: Path) -> Optional[str]:
    """作業場所のリモート名（`.ndf/worktree.json` の `remote`、無ければ既定）。求められなければ None。"""
    try:
        return Git(cwd=Path(root)).remote()
    except GitError:  # noqa: NDF401 外部コマンド: メインディレクトリを求められなければリモートを測らない（解析の項目は不明になる）
        return None


def _remote_head(root: Path) -> Optional[str]:
    """リモートの既定ブランチ（取得済みの `refs/remotes/<リモート>/HEAD` の指す先）。無ければ None。"""
    remote = _remote(root)
    return Git(cwd=Path(root)).remote_default_branch(remote) if remote else None


def default_branch(root: Path) -> Optional[str]:
    """既定ブランチ。リモートの HEAD の指す先 → ローカルの main・master の順。どれも無ければ None。"""
    head = _remote_head(root)
    if head:
        return head
    return next((b for b in DEFAULT_BRANCH_CANDIDATES if _git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}")), None)


def _default_branch(root: Path, api: Api) -> Optional[str]:
    repo, _ = api("repos/{owner}/{repo}")
    if isinstance(repo, dict) and repo.get("default_branch"):
        return repo["default_branch"]
    return _remote_head(root)


def local_facts(root: Path, s: Samples) -> None:
    """ワークフロー・版数のファイル（`package.json`）・プラグインの定義（`CLAUDE_MANIFEST`）を作業場所から読む。"""
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
    manifest = CLAUDE_MANIFEST.as_posix()
    for pj in sorted(Path(root).glob(f"{PLUGINS_DIR}/*/{manifest}")) + sorted(Path(root).glob(manifest)):
        try:
            name = json.loads(pj.read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError):  # noqa: S112 ファイル: 読めないプラグインの定義はプラグインに数えない
            continue
        if name:
            runtimes = ["claude"] + (["codex"] if (pj.parent.parent / CODEX_MANIFEST).is_file() else [])
            s.plugins[name] = runtimes


def cd_workflows(s: Samples) -> list[str]:
    """CD の印（Release・タグを作るコマンド）を含むワークフローの名前（昇順）。"""
    return sorted(n for n, text in s.workflows.items() if any(m in text for m in CD_MARKS))


def single_plugin(s: Samples) -> Optional[tuple[str, list[str]]]:
    """プラグインが 1 つだけのときの名前とランタイム。0 個か 2 個以上なら None。"""
    return next(iter(s.plugins.items())) if len(s.plugins) == 1 else None


def _merged_prs(got: list) -> list[dict]:
    return [
        {"number": p["number"], "base": obj(p.get("base")).get("ref"), "head": obj(p.get("head")).get("ref")}
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


def collect_samples(root: Path, api: Api, deadline: float, limit: int, clock: Optional[Callable[[], float]] = None) -> Samples:
    """直近のマージ済み PR・タグ・Release・本番ブランチの履歴・ruleset・ワークフロー・版数のファイルを集める。"""
    clock = clock or time.monotonic
    s = Samples()
    s.production = _default_branch(root, api)
    if s.production is None:
        s.unreadable.append("default_branch")
    for attr, path, extract in _LISTINGS:
        if clock() > deadline:
            s.warnings.append(f"時間切れで打ち切った: {attr}")
            continue
        got, _ = api(path.format(limit=limit))
        if not isinstance(got, list):
            s.unreadable.append(attr)
            continue
        setattr(s, attr, extract(got)[:limit])
    if s.production:
        _collect_production_commits(root, s, limit)
        s.rules = rule_facts(api, s.production)
        if s.rules is None:
            s.unreadable.append("ruleset")
    local_facts(root, s)
    return s


def _all_rules(api: Api, path: str) -> Optional[list]:
    """`rules/branches/<b>` の規則を全ページ読む。1 ページ目が 404 なら規則なし（空）、途中を読めなければ None。"""
    rules, why = api(path)
    if why == "missing":
        return []
    if not isinstance(rules, list):
        return None
    got, page = rules, 1
    while len(got) >= DEFAULT_PER_PAGE:
        page += 1
        got, _ = api(f"{path}?page={page}")
        if not isinstance(got, list):
            return None
        rules = rules + got
    return rules


def _ruleset_facts(rules: list) -> RuleFacts:
    if not rules:
        return NO_RULES
    kinds = {r.get("type") for r in rules if isinstance(r, dict)}
    approvals = [
        obj(r.get("parameters")).get("required_approving_review_count")
        for r in rules
        if isinstance(r, dict) and r.get("type") == "pull_request"
    ]
    up_to_date = any(
        isinstance(r, dict)
        and r.get("type") == "required_status_checks"
        and obj(r.get("parameters")).get("strict_required_status_checks_policy")
        for r in rules
    )
    count = max((a for a in approvals if isinstance(a, int)), default=0)
    return RuleFacts(True, count, "merge_queue" in kinds, bool(up_to_date))


def _classic_facts(api: Api, branch: str) -> Optional[RuleFacts]:
    """従来のブランチ保護の事実。保護が無ければ `present` が偽、保護があるのに詳細を読めなければ None。"""
    info, _ = api(f"repos/{{owner}}/{{repo}}/branches/{branch}")
    enabled = obj(obj(info).get("protection")).get("enabled")
    if enabled is False:
        return NO_RULES
    protection, _ = api(f"repos/{{owner}}/{{repo}}/branches/{branch}/protection")
    if not isinstance(protection, dict):
        return None
    count = obj(protection.get("required_pull_request_reviews")).get("required_approving_review_count")
    up_to_date = bool(obj(protection.get("required_status_checks")).get("strict"))
    return RuleFacts(True, count if isinstance(count, int) else 0, False, up_to_date)


def rule_facts(api: Api, branch: str) -> Optional[RuleFacts]:
    """本番ブランチの ruleset と従来のブランチ保護を重ねた事実（GitHub は両方を効かせるため厳しいほうを採る）。読めなければ None。"""
    rules = _all_rules(api, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}")
    classic = _classic_facts(api, branch) if rules is not None else None
    if rules is None or classic is None:
        return None
    a = _ruleset_facts(rules)
    if not a.present or not classic.present:
        return a if a.present else classic
    return RuleFacts(True, max(a.approvals, classic.approvals), a.queue or classic.queue, a.up_to_date or classic.up_to_date)


def _collect_production_commits(root: Path, s: Samples, limit: int) -> None:
    """本番ブランチの最初の親の履歴を 1 回の `git log` で読む。NDF が入れた取り込みの記録（trailer）の PR も事例にする。

    PR を経ない早送りの取り込みも事例に数える。"""
    remote = _remote(root)
    refs = ([f"{remote}/{s.production}"] if remote else []) + [s.production]
    ref = next((r for r in refs if _git_out(root, "rev-parse", "--verify", "--quiet", r)), None)
    if ref is None:
        s.unreadable.append("history")
        return
    out = _git_out(root, "log", "--first-parent", f"-n{limit}", f"--format={LOG_FORMAT}", ref)
    known = {p["number"] for p in s.prs}
    for sha, parents, subject, tr in _log_records(out):
        s.commits.append({"sha": sha, "parents": len(parents), "subject": subject, "landed": tr.get(TRAILER_PR)})
        n = _landed_pr(parents, tr)
        if n and n not in known:
            s.prs.append({"number": n, "base": s.production, "head": None, "landed": sha})
            known.add(n)


# 履歴の 1 件の形（SHA・親・件名・trailer を NUL で、trailer どうしを 0x1f で、件どうしを 0x1e で区切る）
LOG_FORMAT = f"%H%x00%P%x00%s%x00%(trailers:key={TRAILER_PR},key={TRAILER_HEAD},key={TRAILER_TIP},separator=%x1f)%x1e"


def _log_records(out: Optional[str]) -> list[tuple[str, list[str], str, dict[str, str]]]:
    """`LOG_FORMAT` の出力を `(SHA, 親の並び, 件名, trailer の名前 → 値)` の並びへ分ける。欠けた件は飛ばす。"""
    got = []
    for rec in out.split("\x1e") if out else []:
        parts = rec.strip("\n").split("\x00")
        if len(parts) < 4:
            continue
        tr = dict(t.split(": ", 1) for t in parts[3].split("\x1f") if ": " in t)
        got.append((parts[0], parts[1].split(), parts[2], tr))
    return got


def _landed_pr(parents: list[str], tr: dict[str, str]) -> Optional[int]:
    """取り込みの記録の PR の番号。記録が無い・最初の親が記録の先端と違う・数字でなければ None。"""
    if tr.get(TRAILER_PR) and parents and parents[0] == tr.get(TRAILER_TIP):
        return int(tr[TRAILER_PR]) if tr[TRAILER_PR].isdigit() else None
    return None


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


def decide_merge_method(facts: Optional[RuleFacts], release_strategy: str) -> Measured:
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


def _tag_shape(tag: str, plugins: dict) -> Optional[str]:
    if not VERSION.search(tag):
        return None
    shape = VERSION.sub("{version}", tag, count=1)
    for name in plugins:
        if shape.startswith(f"{name}--"):
            return "{plugin}" + shape[len(name) :]
    return shape


def _relabel(m: Measured, source: str) -> Measured:
    for e in m.evidence:
        e["source"] = source
    return m


def _sprint_units(s: Samples) -> list[dict]:
    """スプリントの事例にする PR。長く残るブランチ（ほかの PR の宛先）からの PR と版上げの PR は数えない。"""
    # スプリントブランチは課題の PR の宛先でも一時的なブランチで、長く残るブランチに数えない
    # （課題の PR → sprint/s1 → develop では sprint/s1 → develop を標本にする）
    long_lived = {b for p in s.prs if isinstance(b := p.get("base"), str) and b and not b.startswith(SPRINT_PREFIX)}
    units = [p for p in s.prs if p.get("base") in long_lived and p.get("head") not in long_lived]
    return [p for p in units if not str(p.get("head")).startswith(RELEASE_PREFIX)]


def _release_keys(s: Samples, rule: dict) -> dict[str, Measured]:
    """タグの形と GitHub Release の有無。タグも Release も 0 件（読めた上で）なら、0 件を根拠に決める。"""
    if not s.tags and not s.releases and "tags" not in s.unreadable and "releases" not in s.unreadable:
        zero = [{"source": "tag", "ref": "0 件", "value": "0"}, {"source": "release", "ref": "0 件", "value": "0"}]
        return {"/release/tag": Measured(None, zero), "/release/github_release": Measured(False, copy.deepcopy(zero))}
    shapes = [(sh, t) for t in s.tags if (sh := _tag_shape(t, s.plugins))]
    rel = set(s.releases)
    return {
        "/release/tag": _relabel(count_key("/release/tag", shapes, rule), "tag"),
        "/release/github_release": _relabel(count_key("/release/github_release", [(t in rel, t) for t in s.tags], rule), "release"),
    }


def _history_keys(s: Samples, rule: dict) -> dict[str, Measured]:
    prod = s.production
    units = _sprint_units(s)
    strat = [("trunk" if p["base"] == prod else "merge_base_branch", f"#{p['number']}") for p in units]
    out = {"/release_strategy": count_key("/release_strategy", strat, rule)}
    out["/branches/base"] = count_key("/branches/base", [(p["base"], f"#{p['number']}") for p in units], rule)
    out["/sprint/start"] = count_key(
        "/sprint/start", [("production" if p["base"] == prod else "base", f"#{p['number']}") for p in units], rule
    )
    out.update(_release_keys(s, rule))
    merges = [
        ("merge" if c["parents"] >= 2 else "squash", c["sha"][:12])
        for c in s.commits
        if c["parents"] >= 2 or re.search(r"\(#\d+\)$", c["subject"]) or c.get("landed")
    ]
    out["/merge/commit"] = _relabel(count_key("/merge/commit", merges, rule), "pr")
    return out


def _source_keys(s: Samples, release_strategy: Optional[str]) -> dict[str, Measured]:
    out: dict[str, Measured] = {}
    if s.production:
        out["/branches/production"] = Measured(s.production, [{"source": "repo", "ref": "default_branch", "value": s.production}])
    else:
        out["/branches/production"] = Measured(why="insufficient")
    plugin = single_plugin(s)
    if plugin:
        name, runtimes = plugin
        ref = CLAUDE_MANIFEST.name
        out["/release/plugin"] = Measured(name, [{"source": "repo", "ref": ref, "value": name}])
        out["/release/runtimes"] = Measured(runtimes, [{"source": "repo", "ref": ref, "value": ",".join(runtimes)}])
        out["/release/version_store"] = Measured({"kind": "plugin"}, [{"source": "repo", "ref": ref, "value": "plugin"}])
    elif s.version_files:
        path, pointer = next(iter(s.version_files.items()))
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
    out["/channels"] = Measured(channels, [{"source": "workflow", "ref": "stable", "value": s.production if s.production else ""}])
    out["/merge/method"] = decide_merge_method(s.rules, release_strategy) if release_strategy else Measured(why="insufficient")
    return out


def _preset_matches(row: dict, pointer: str, value: Any, presets_row: dict) -> bool:
    got = json_pointer.get(row, pointer)
    if pointer == "/merge/method" and got == "merge-queue":
        return value in ("merge-queue", presets_row.get("merge_method_without_queue"))
    if pointer == "/release/version_store":
        return obj(got).get("kind") == obj(value).get("kind")
    return bs.canonical_json(got) == bs.canonical_json(value)


def pick_kind(measured: dict[str, Measured], presets: dict) -> tuple[Optional[str], list[str]]:
    """測った値がすべて合う種類の既定の行を 1 つ選ぶ。形・CD とその産物・チャネルは比べない。"""
    hits = [
        k
        for k, row in presets["kinds"].items()
        if all(_preset_matches(row["section"], p, measured[p].value, row) for p in COMPARED if p in measured)
    ]
    if len(hits) == 1:
        return hits[0], []
    return None, hits or list(presets["kinds"])


def doc_claims(root: Path) -> list[dict]:
    """README・CONTRIBUTING・AGENTS.md から PR の宛先（`--base <名前>`）を述べる行を拾う。"""
    claims = []
    for name in DOCS:
        p = Path(root) / name
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):  # noqa: S112 ファイル: 読めない文書は宛先を述べていないとみなす
            continue
        for i, line in enumerate(lines, 1):
            for m in DOC_BASE.finditer(line):
                claims.append({"pointer": "/branches/base", "value": m.group(1), "ref": f"{name}:{i}"})
    return claims


def rule_conflicts(values: dict, evidence: dict) -> list[dict]:
    """測った値の組が組み合わせの規則に反するかを、規則に出るキーがすべて測ったキーである規則だけで調べる（例は R10）。"""
    rel = obj(values.get("release"))
    if "cd" in rel and "tag" in rel and rel.get("cd") is None and (rel.get("tag") is not None or rel.get("github_release")):
        return [
            {
                "pointer": "/release/cd",
                "why": "conflict",
                "candidates": [{"release.cd": "CD のワークフローを足す"}, {"release.tag": None, "release.github_release": False}],
                "evidence": seq(evidence.get("/release/tag")) + seq(evidence.get("/release/cd")),
            }
        ]
    return []


def doc_conflicts(root: Path, values: dict, evidence: dict) -> list[dict]:
    """文書（README など）の述べる PR の宛先が、測った値と食い違う箇所を問いにする。"""
    conflicts = []
    for c in doc_claims(root):
        if json_pointer.get(values, c["pointer"]) != c["value"]:
            conflicts.append(
                {
                    "pointer": c["pointer"],
                    "why": "conflict",
                    "candidates": [json_pointer.get(values, c["pointer"]), c["value"]],
                    "evidence": seq(evidence.get(c["pointer"])) + [{"source": "doc", "ref": c["ref"], "value": c["value"]}],
                }
            )
    return conflicts


def _questions_of(measured: dict[str, Measured], unreadable: list[str]) -> list[dict]:
    """根拠の足りない・食い違うキーの問い。読めなかった種類（`unreadable`）を各問いに添える。"""
    questions = [{"pointer": p, "why": m.why, "candidates": m.candidates, "evidence": m.evidence} for p, m in measured.items() if m.why]
    if unreadable:
        for q in questions:
            q["unreadable"] = list(unreadable)
    return questions


def _asked(s: Samples, measured: dict[str, Measured], release_strategy: Optional[str]) -> dict:
    """問いを返すときの結果。確定した値と根拠を持つ（答えを足した後に `complete_answers` が種類の既定から全体を組む）。

    本番の必須レビューの数（`facts.required_approvals`）も持つ（答えを足した後に承認の方法の食い違いを問い直す）。"""
    values: dict = {}
    for p, m in measured.items():
        if not m.why:
            json_pointer.put(values, p, m.value)
    if release_strategy == "trunk":
        json_pointer.put(values, "/branches/base", s.production)
    kept = [
        {"source": e["source"], "ref": e["ref"], "value": e["value"], "pointer": p}
        for p, m in measured.items()
        if not m.why
        for e in m.evidence
    ]
    facts = {"required_approvals": s.rules.approvals if s.rules is not None else None}
    return {"status": "ask", "kind": None, "values": values, "evidence": kept, "questions": [], "warnings": s.warnings, "facts": facts}


def _picked(base: dict, measured: dict[str, Measured], presets: dict, prefer: Optional[str]) -> dict:
    """測った値がすべて合う種類の既定の行から全体を組む。種類が決まらなければ種類の問い、承認の方法が食い違えばその問い。"""
    kind, candidates = pick_kind(measured, presets)
    if kind is None:
        if prefer in candidates:
            candidates = [prefer] + [c for c in candidates if c != prefer]
        return {**base, "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": candidates, "evidence": []}]}
    section = _section_from_kind(presets, kind, measured, base["values"])
    evidence = _flat_evidence({p: m.evidence for p, m in measured.items()}, presets, kind)
    out = {**base, "status": "ok", "kind": kind, "values": section, "evidence": evidence}
    approval = _approval_question(section, base["facts"]["required_approvals"])
    return {**out, "status": "ask", "questions": approval} if approval else out


def measure_strategy(
    root: Path, api: Optional[Api] = None, deadline: Optional[float] = None, presets: Optional[dict] = None, prefer: Optional[str] = None
) -> dict:
    """既存のリポジトリを測り、作成の結果（値・根拠・問い）を組む。書かない。"""
    presets = presets if presets is not None else bw.load_presets()
    rule = presets["evidence"]
    deadline = deadline if deadline is not None else time.monotonic() + limits.seconds(MEASURE_LIMIT)
    s = collect_samples(root, api if api is not None else default_api(root, deadline), deadline, rule["limit"])
    hist = _history_keys(s, rule)
    rs = hist["/release_strategy"].value if hist["/release_strategy"].why is None else None
    measured = {**hist, **_source_keys(s, rs)}
    base = _asked(s, measured, rs)
    questions = _questions_of(measured, s.unreadable)
    if questions:
        return {**base, "questions": questions}
    evidence = {p: m.evidence for p, m in measured.items()}
    conflicts = rule_conflicts(base["values"], evidence) + doc_conflicts(root, base["values"], evidence)
    if conflicts:
        return {**base, "questions": conflicts}
    return _picked(base, measured, presets, prefer)


def complete_answers(prior: dict, answers: dict, presets: Optional[dict] = None) -> dict:
    """問いを返した測った結果（確定した値と根拠だけを持つ）に答えを入れ、種類の既定の行から完全なセクションを組む。

    種類は答えの `/kind`、無ければ確定した値と答えに合う 1 つ。決まらなければ種類の問いを返す。答えが既定から変えた値は
    `apply_answers` が種類を `custom` にする。"""
    presets = presets if presets is not None else bw.load_presets()
    rest = {p: v for p, v in answers.items() if p != "/kind"}
    known = copy.deepcopy(obj(prior.get("values")))
    for p, v in rest.items():
        json_pointer.put(known, p, v)
    asked = {
        "status": "ask",
        "kind": None,
        "values": known,
        "evidence": seq(prior.get("evidence")),
        "warnings": seq(prior.get("warnings")),
        "facts": obj(prior.get("facts")),
    }
    if not json_pointer.get(known, "/branches/base"):
        return {**asked, "questions": [{"pointer": "/branches/base", "why": "missing", "candidates": [], "evidence": []}]}
    pointers = {e["pointer"] for e in seq(prior.get("evidence")) if e.get("pointer") and e.get("source") != "preset"}
    kind = answers.get("/kind")
    if kind is None:
        kind, candidates = pick_kind({p: Measured(json_pointer.get(known, p)) for p in pointers | set(rest)}, presets)
        if kind is None:
            return {**asked, "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": candidates, "evidence": []}]}
    measured = {p: Measured(json_pointer.get(known, p)) for p in pointers - set(rest) - {"/branches/base"}}
    section = _section_from_kind(presets, kind, measured, known)
    evidence = [
        *asked["evidence"],
        *({"source": "preset", "ref": kind, "value": p, "pointer": p} for p in presets["measured_keys"]["preset"]),
    ]
    done = bw.apply_answers({**asked, "kind": kind, "values": section, "evidence": evidence, "questions": []}, rest)
    approval = [] if "/approval/via" in rest else _approval_question(done["values"], asked["facts"].get("required_approvals"))
    return {**done, "status": "ask", "questions": approval} if approval else done


def _section_from_kind(presets: dict, kind: str, measured: dict[str, Measured], values: dict) -> dict:
    """種類の既定の行に測った値を入れたセクション。プラグインの無いリポジトリは plugin・runtimes を落とす。"""
    section = copy.deepcopy(presets["kinds"][kind]["section"])
    for p, m in measured.items():
        json_pointer.put(section, p, copy.deepcopy(m.value))
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


def _approval_question(section: dict, approvals: Optional[int]) -> list[dict]:
    """承認ゲートで受ける行なのに本番ブランチの ruleset が承認を求める（必須レビューの数 `approvals` が 1 以上）ときの問い。"""
    if section["approval"]["via"] == "gate" and isinstance(approvals, int) and approvals > 0:
        return [
            {
                "pointer": "/approval/via",
                "why": "conflict",
                "candidates": ["gate", "review"],
                "evidence": [{"source": "ruleset", "ref": "required_approving_review_count", "value": str(approvals)}],
            }
        ]
    return []


def classify_repo(root: Path, api: Api, presets: Optional[dict] = None) -> tuple[str, list[dict]]:
    """新規（PR・タグ・Release・ワークフロー・ruleset が無く、コミットが閾値以下）か既存かを分ける。読めないものは既存とみなす。"""
    presets = presets if presets is not None else bw.load_presets()
    ev = []
    n = _git_out(root, "rev-list", "--count", "HEAD")
    commits = int(n) if n and n.isdigit() else None
    ev.append({"source": "repo", "ref": "commits", "value": str(commits)})
    existing = commits is None or commits > presets["evidence"]["new_repo_max_commits"]
    for kind, path in (
        ("pr", "repos/{owner}/{repo}/pulls?state=closed&per_page=1"),
        ("tag", "repos/{owner}/{repo}/tags?per_page=1"),
        ("release", "repos/{owner}/{repo}/releases?per_page=1"),
    ):
        got, _ = api(path)
        ev.append({"source": kind, "ref": path, "value": "unreadable" if not isinstance(got, list) else str(len(got))})
        existing = existing or not isinstance(got, list) or bool(got)
    wf = Path(root) / ".github" / "workflows"
    has_wf = wf.is_dir() and any(wf.glob("*.y*ml"))
    rules, _ = api("repos/{owner}/{repo}/rulesets")
    has_rules = not isinstance(rules, list) or bool(rules)
    ev.append({"source": "workflow", "ref": ".github/workflows", "value": str(has_wf)})
    ev.append({"source": "ruleset", "ref": "rulesets", "value": "unreadable" if not isinstance(rules, list) else str(len(rules))})
    existing = existing or has_wf or has_rules
    return ("existing" if existing else "new"), ev


def merge_queue_enabled(api: Api, branch: Optional[str]) -> Optional[bool]:
    """本番ブランチに merge queue の規則があるか。読めなければ None。"""
    if not branch:
        return None
    rules = _all_rules(api, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}")
    return None if rules is None else any(isinstance(r, dict) and r.get("type") == "merge_queue" for r in rules)


def init_facts(root: Path, api: Api) -> dict:
    """`init` が既定の行へ入れる測った名前（既定ブランチ・プラグイン・CD のワークフロー・merge queue）。"""
    s = Samples()
    local_facts(root, s)
    production = default_branch(root) or _git_out(root, "symbolic-ref", "--short", "HEAD")
    facts: dict[str, Any] = {"production": production}
    plugin = single_plugin(s)
    if plugin:
        facts["plugin"], facts["runtimes"] = plugin
    cd = cd_workflows(s)
    facts["workflow"] = cd[0] if cd else None
    facts["merge_queue"] = merge_queue_enabled(api, production)
    return facts
