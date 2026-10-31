"""承認の材料の読み取り（#1488）。`release-steps.py notes --approval` が承認資料の `## 検査の結果` と
`## 未検証・残る危険` に書く行を組む。

承認の材料は 5 つ: レビュー（cross-review のラウンド数と見送った指摘）・リファクタリング（採用と取り消しの件数）・
全体テスト（コマンド・合否・件数）・設計文書の未確認・未実施のリリース後テスト。検査の 3 つは検査のプランの状態
ディレクトリの `NN-<ステップ>.out` の最後の結果 JSON から、設計文書の未確認はベースブランチの
`issues/issue-<番号>-design.md` の `## 未確認のまま残ること` から、未実施のリリース後テストは閉じる課題の
`## 受け入れ条件` と `## リリース後テスト` の表（`lib/acceptance.py`）から読む。

終了コードも出力も持たない。gh・git が読めなければ StepError を投げ（止めるのは呼ぶ側）、検査の状態の置き場が
無い・結果 JSON が無いときは、その材料を None（無い材料）にして返す。使う側は `lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import acceptance
import closing
import gh_sections
import proc
import mdtable
from release_lib import included, others
from release_lib.names import h2_lines, next_h2
from step_result import EXIT_PRECONDITION, StepError

CHECK_HEADING = "## 検査の結果"
RISKS_HEADING = included.RISKS_HEADING
RUNTIME_NAMES = {"claude": "Claude Code", "codex": "Codex", "kiro": "Kiro", "agy": "Antigravity"}
UNCONFIRMED_HEADING = "## 未確認のまま残ること"
NAMES = {
    "review": "レビュー",
    "refactor": "リファクタリング",
    "whole_test": "全体テスト",
    "unconfirmed": "設計文書の未確認",
    "unverified": "未実施のリリース後テスト",
}
PR_RISKS = "PR の本文の未検証・残る危険"
UNCONFIRMED_LINE = "- 設計の未確認 #"
UNVERIFIED_LINE = "- 未実施のリリース後テスト #"
MISSING_LINE = "- 無い材料: "
NOTHING_LINE = "- 記載が無い"
# `## 未検証・残る危険` のうち機械が書く行の頭。書くたびに消してから入れ直す（手で足した行と PR の本文の行は残す。#1834）
MACHINE = (UNCONFIRMED_LINE, UNVERIFIED_LINE[:-1], MISSING_LINE.rstrip(), NOTHING_LINE, "- PR の本文に記載が無い")
STEPS = {
    "review": ("review", "cross-review-drive"),
    "refactor": ("refactor", "cross-refactoring-drive"),
    "whole_test": ("test-all", "test-run"),
}
JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Deferred:
    path: str | None
    line: int | None
    summary: str


@dataclass(frozen=True)
class Review:
    rounds: int
    deferred: tuple[Deferred, ...]


@dataclass(frozen=True)
class Refactor:
    adopted: int
    reverted: int


@dataclass(frozen=True)
class WholeTest:
    commands: tuple[str, ...]
    on_ci: bool
    passed: bool
    failed: int
    caused: int


@dataclass(frozen=True)
class Unconfirmed:
    issue: int
    item: str


@dataclass(frozen=True)
class Unverified:
    issue: int
    number: int
    text: str
    repo: str | None = None  # 今のリポジトリでない課題のときだけ owner/name


@dataclass(frozen=True)
class Materials:
    """各欄の None は「材料が無い」（読む元が無い・読めない）、空の列は「材料はあり、項目が 0 件」。"""

    review: Review | None = None
    refactor: Refactor | None = None
    whole_test: WholeTest | None = None
    unconfirmed: tuple[Unconfirmed, ...] | None = None
    unverified: tuple[Unverified, ...] | None = None

    def missing(self) -> list[str]:
        return [name for key, name in NAMES.items() if getattr(self, key) is None]


# --- 検査の結果（状態ディレクトリ） -------------------------------------------------


def last_result(state_dir: Path, step: str, tool: str) -> dict | None:
    """`<state_dir>/NN-<step>.out`（NN の最も大きいもの。再開で増える）の最後の結果 JSON（`tool` が一致するもの）。"""
    outs = [(int(m.group(1)), f) for f in state_dir.glob(f"*-{step}.out") if (m := re.match(rf"(\d+)-{re.escape(step)}\.out$", f.name))]
    if not outs:
        return None
    try:
        lines = max(outs)[1].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.lstrip().startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("tool") == tool:
            return d
    return None


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _review(d: dict) -> Review:
    items = [i for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "deferred"]
    deferred = tuple(
        Deferred(i.get("path") or None, i.get("line") if isinstance(i.get("line"), int) else None, str(i.get("summary") or ""))
        for i in items
    )
    return Review(_int((d.get("metrics") or {}).get("rounds")), deferred)


def _refactor(d: dict) -> Refactor:
    m = d.get("metrics") or {}
    return Refactor(_int(m.get("adopted")), _int(m.get("reverted")))


def declared_whole(root: Path) -> tuple[tuple[str, ...], bool]:
    """宣言（`.ndf/project.json` の `test.suites[].command`）から `test-run.py whole` が走らせる全体テストのコマンドと
    CI に任せるか。結果 JSON に `ran` の無い出力のときだけ使う。解けなければ ((), False)。"""
    try:
        import test_strategy as ts

        sv = root / ".ndf" / "supervise.json"
        supervise = json.loads(sv.read_text(encoding="utf-8")) if sv.is_file() else {}
        strategy = ts.resolve(ts.decl_of(root, supervise if isinstance(supervise, dict) else {})[0])
        return tuple(strategy.whole_commands(ts.TEST)), bool(strategy.whole_on_ci)
    except Exception:  # noqa: BLE001  コマンドは補いで、解けなければ合否だけ書く
        return (), False


def _whole(d: dict, root: Path) -> WholeTest:
    items = [i for i in d.get("items") or [] if isinstance(i, dict)]
    ran = next((i for i in items if "ran" in i), None)
    triage = next((i for i in items if "failed_tests" in i), {})
    commands, on_ci = (tuple(ran.get("ran") or ()), bool(ran.get("on_ci"))) if ran else declared_whole(root)
    caused = _int((d.get("metrics") or {}).get("caused"))
    return WholeTest(commands, on_ci, d.get("status") == "ok", len(triage.get("failed_tests") or []), caused)


def read_check(state_dir, root: Path) -> tuple[Review | None, Refactor | None, WholeTest | None]:
    """検査のプランの状態ディレクトリから 3 つの材料。渡されない（None）ときと、結果 JSON が無いときはその材料を None。
    渡されたのに空文字か無いディレクトリなら、材料が無いと書かずに StepError（3）で止める（置き場が無い。#2079）。"""
    if state_dir is None:
        return None, None, None
    if not str(state_dir).strip() or not Path(state_dir).is_dir():
        raise StepError(f"--check-state の検査の状態ディレクトリが無い: {state_dir!r}", EXIT_PRECONDITION)
    d = Path(state_dir)
    got = {key: last_result(d, step, tool) for key, (step, tool) in STEPS.items()}
    return (
        _review(got["review"]) if got["review"] else None,
        _refactor(got["refactor"]) if got["refactor"] else None,
        _whole(got["whole_test"], root) if got["whole_test"] else None,
    )


# --- 設計文書の未確認・未実施のリリース後テスト ----------------------------------------


def unconfirmed_items(text: str) -> list[str]:
    """設計文書の `## 未確認のまま残ること` の項目（表なら「項目: 内容」、箇条ならその文）。「無し」だけなら空。"""
    import md

    section = gh_sections.get_section(text or "", UNCONFIRMED_HEADING)
    if not section:
        return []
    found = md.tables(section)
    if found:
        rows = [[c.strip() for c in r] for r in found[0].rows]
        out = [f"{r[0]}: {r[1]}" if len(r) > 1 and r[1] else r[0] for r in rows if r and r[0]]
    else:
        out = [re.sub(r"^[-*]\s+", "", t.strip()) for t in section.splitlines() if re.match(r"^[-*]\s+", t.strip())]
    return [o for o in out if o.rstrip("。") not in gh_sections.NO_ITEMS]


def read_unconfirmed(root: Path, base: str, issues) -> tuple[Unconfirmed, ...] | None:
    """閉じる課題の設計文書（`origin/<base>:issues/issue-<番号>-design.md`。origin が無ければ `<base>`、それも無ければ
    HEAD）の未確認。どの課題にも設計文書が無ければ None。どの ref も読めなければ StepError（2）。"""
    if not issues:
        return None
    ref = next((r for r in (f"origin/{base}", base, "HEAD") if proc.git_out(root, "rev-parse", "--verify", "-q", r) is not None), None)
    if ref is None:
        raise StepError(f"設計文書を読むベースブランチ {base} が無い", 2)
    out, found = [], False
    for n in issues:
        text = proc.git_out(root, "show", f"{ref}:issues/issue-{n}-design.md")
        if text is None:
            continue
        found = True
        out += [Unconfirmed(n, item) for item in unconfirmed_items(text)]
    return tuple(out) if found else None


def read_unverified(bodies: dict[tuple[str, int], str], verdicts: dict, repo: str) -> tuple[Unverified, ...] | None:
    """閉じる課題（(owner/name, 番号)）の受け入れ条件のうち、印が無く、リリース後テストの表（verdicts）にその
    リポジトリの課題として合格の行が無いもの。どの課題にも受け入れ条件が無ければ None。"""
    out, found = [], False
    for (owner, n), body in bodies.items():
        rows = acceptance.criteria_lines(body or "")
        if not rows:
            continue
        found = True
        v = verdicts.get((owner, n))
        if v is not None and v.all_pass and not v.passed:  # 条件の番号を書かずに全行が合格
            continue
        passed = v.passed if v is not None else frozenset()
        other = None if same_repo(owner, repo) else owner
        out += [Unverified(n, c.number, text, other) for c, text in rows if not c.checked and c.number not in passed]
    return tuple(out) if found else None


# --- 承認資料の行 ----------------------------------------------------------------


def _deferred_place(d: Deferred) -> str:
    if d.path and d.line is not None:
        return f"`{d.path}:{d.line}` "
    return f"`{d.path}` " if d.path else ""


def check_lines(m: Materials) -> list[str]:
    """`## 検査の結果` の行。検査の 3 つの材料がどれも無ければ、無いことを 1 行で書く。"""
    out = []
    if m.review is not None:
        out.append(f"- レビュー: cross-review {m.review.rounds} ラウンド、見送った指摘 {len(m.review.deferred)} 件")
        out += [f"  - {_deferred_place(d)}{d.summary}".rstrip() for d in m.review.deferred]
    if m.refactor is not None:
        out.append(f"- リファクタリング: 採用 {m.refactor.adopted} 件 / 取り消し {m.refactor.reverted} 件")
    if (w := m.whole_test) is not None:
        how = "CI に任せた " if w.on_ci else (" / ".join(f"`{c}`" for c in w.commands) + " " if w.commands else "")
        out.append(f"- 全体テスト: {how}{'合格' if w.passed else '不合格'}（落ちたテスト {w.failed} 件・変更起因 {w.caused} 件）")
    return out or [f"- 無し（{'・'.join(NAMES[k] for k in STEPS)}の結果が検査の状態に無い）"]


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def risk_lines(m: Materials, pr_risks: bool, at: str) -> list[str]:
    """`## 未検証・残る危険` の機械の行。5 つの材料と PR の本文の危険がすべて無いときだけ「記載が無い」の 1 行。"""
    out = [f"{UNCONFIRMED_LINE}{u.issue}: {u.item}" for u in m.unconfirmed or ()]
    out += [f"{UNVERIFIED_LINE[:-1]}{u.repo or ''}#{u.issue} の条件 {u.number}: {u.text}（{at} 時点）" for u in m.unverified or ()]
    missing = m.missing() + ([] if pr_risks else [PR_RISKS])
    if len(missing) == len(NAMES) + 1:
        return [f"{NOTHING_LINE}（無い材料: {'・'.join(missing)}）"]
    return out + ([f"{MISSING_LINE}{'・'.join(missing)}"] if missing else [])


def items_of(m: Materials) -> list[dict]:
    """結果 JSON の items の材料の行（5 行）。"""
    lines = {
        "review": m.review and 1 + len(m.review.deferred),
        "refactor": m.refactor and 1,
        "whole_test": m.whole_test and 1,
        "unconfirmed": m.unconfirmed and len(m.unconfirmed),
        "unverified": m.unverified and len(m.unverified),
    }
    return [
        {"kind": "material", "name": name, "result": "missing" if getattr(m, key) is None else "written", "lines": lines[key] or 0}
        for key, name in NAMES.items()
    ]


def write_sections(lines: list[str], risks: list[str], machine: list[str], results: list[str] | None) -> None:
    """承認資料の `## 未検証・残る危険` と `## 検査の結果`（results が None なら書かない）を書く。危険の節は機械の行
    （MACHINE で始まる行）を消してから machine で入れ直し、手で足した行と既にある PR の本文の行は残して、無い PR の本文の
    行だけを足す（#1834・I8）。検査の結果の節は無ければ危険の節の直前へ置く。"""
    at = next((i for i in h2_lines(lines) if lines[i] == RISKS_HEADING), None)
    kept = [x for x in lines[at + 1 : next_h2(lines, at)] if not x.startswith(MACHINE)] if at is not None else []
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    have = set(kept)
    others.put_section(lines, RISKS_HEADING, [*kept, *(r for r in risks if r.split("\n", 1)[0] not in have), *machine] or ["- 無し"])
    if results is None:
        return
    if any(lines[i] == CHECK_HEADING for i in h2_lines(lines)):
        others.put_section(lines, CHECK_HEADING, results)
        return
    at = next(i for i in h2_lines(lines) if lines[i] == RISKS_HEADING)
    lines[at:at] = [CHECK_HEADING, "", *results, ""]


def same_repo(a: str | None, b: str | None) -> bool:
    """owner/name が同じリポジトリか（GitHub は大文字と小文字を区別しない）。"""
    return (a or "").lower() == (b or "").lower()


def _reference_of(node: dict, repo: str) -> tuple[str, int] | None:
    """closingIssuesReferences の 1 件を (owner/name, 番号) にする。URL が無ければ今のリポジトリの課題。"""
    if not isinstance(node.get("number"), int):
        return None
    got = closing.issue_ref(node.get("url"), repo) if node.get("url") else None
    return got if got is not None and got[1] == node["number"] else (repo, node["number"])


def issues_closed_by(prs, data: dict, repo: str) -> list[tuple[str, int]]:
    """含む PR が閉じる課題を (owner/name, 番号) で（GitHub の closingIssuesReferences と本文の closing keywords の和。
    昇順）。今のリポジトリの課題は owner/name を `repo` に揃え、別のリポジトリの課題は番号だけにしない。"""
    found: set[tuple[str, int]] = set()
    for p in prs:
        d = data.get(p.number) or {}
        refs = [_reference_of(i, repo) for i in d.get("closingIssuesReferences") or [] if isinstance(i, dict)]
        refs += closing.closing_refs(d.get("body") or "", repo)
        found |= {(repo if same_repo(r, repo) else r, n) for r, n in (x for x in refs if x is not None)}
    return sorted(found)


def approval_materials(root: Path, decl: tuple, check_state, prs, data: dict, read_issue) -> Materials:
    """承認の材料。decl は (ベースブランチ, owner/name, 版, --prs)。prs は「含む PR」、data はその gh pr view の出力
    （comments つき）、read_issue(repo, n) は課題の本文（読めなければ StepError）。設計文書は今のリポジトリの課題だけを
    読む。リリース後テストの表は --prs の PR の本文とコメントから読む。"""
    base, repo, version, sprint_prs = decl
    review, refactor, whole = read_check(check_state, root)
    issues = issues_closed_by(prs, data, repo)
    texts = [(data.get(n) or {}).get("body") or "" for n in sprint_prs]
    texts += [(c or {}).get("body") or "" for n in sprint_prs for c in (data.get(n) or {}).get("comments") or []]
    verdicts = acceptance.version_verdicts("\n".join(texts), version, repo)
    unverified = read_unverified({ref: read_issue(*ref) for ref in issues}, verdicts, repo)
    own = [n for r, n in issues if same_repo(r, repo)]
    return Materials(review, refactor, whole, read_unconfirmed(root, base, own), unverified)


def approval_cell(text: str) -> str:
    """提示物の表の 1 セル（改行は <br>）。"""
    return mdtable.cell_text(text.replace("\n", "<br>"))


def write_approval(path: Path, version, bullets, risks, verified, ref, migration=(), results=None, machine=()) -> list[dict]:
    """notes --approval が承認資料の「配る中身」「検証への配布で確かめたこと」の欄と、`## 検査の結果`・
    `## 未検証・残る危険`・`## 移行の手順` の節を書く。資料か欄が無ければ StepError（3）。"""
    if not path.is_file():
        raise StepError(f"提示物 {path} が無い（先に approval-facts を走らせる）", EXIT_PRECONDITION)
    names = "・".join(RUNTIME_NAMES.get(r, r) for r in verified)
    check = (
        f"{names} の {len(verified)} 経路で {ref} から ndf {version} を導入し、版と中身が一致した"
        f"（release-verification-steps.py verify-install）"
        if verified
        else "—"
    )
    rows = {"配る中身": "\n".join(bullets) or "—", "検証への配布で確かめたこと": check}
    lines = path.read_text(encoding="utf-8").split("\n")
    done = []
    for i, line in enumerate(lines):
        for key, value in rows.items():
            if line.startswith(f"| {key} |"):
                lines[i] = f"| {key} | {approval_cell(value)} |"
                done.append(key)
    missing = [k for k in rows if k not in done]
    if missing:
        raise StepError(f"提示物に欄が無い: {', '.join(missing)}", EXIT_PRECONDITION)
    write_sections(lines, list(risks), list(machine), results)
    others.put_section(lines, others.MIGRATION_HEADING, list(migration) or ["- 無し"])
    path.write_text("\n".join(lines), encoding="utf-8")
    return [{"kind": "cell", "name": k, "result": "written"} for k in rows] + [
        {"kind": "section", "name": RISKS_HEADING, "result": "written", "lines": len(risks) + len(machine)},
        {"kind": "section", "name": others.MIGRATION_HEADING, "result": "written", "lines": len(migration)},
    ]
