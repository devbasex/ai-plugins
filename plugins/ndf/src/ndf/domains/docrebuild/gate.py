"""`ndf document-rebuild gate` の 1 回の実行（#4446 の決定 2・3・4・5・8・10。設計書の「処理の流れ」の手順 1〜9）。

承認ゲートの 1 つ前に打つ。PR の head を固定し、決まった形の質問・承認資料の作業用のファイル・素材のファイルを作り、前に読ませた記録と
比べて変わったものだけを、`ndf worker run` で起動した worker に読み手役で読ませる。結果を `reading-docs.jsonl` と
`reading-checks.jsonl` へ書く。PR の題と本文は書き換えない。直しが要るときは記録を書かずに変更要求で止め、合格した承認資料は
PR のコメントとして足す。判断の部品は `gate_decide` にある。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters import git
from ndf.adapters.github import GitHub, GitHubError
from ndf.adapters.publish import Publisher, redacted_pattern
from ndf.contracts import result
from ndf.domains.docrebuild import drive
from ndf.domains.docrebuild import gate_decide as gd
from ndf.domains.docrebuild.drive import Precondition, Stop
from ndf.domains.records import mvv, pull_view, reading
from ndf.domains.records.pull_view import PullView
from ndf.domains.release import approval_decide

log = logging.getLogger(__name__)

TOOL = drive.TOOL
WORK_KIND = f"{drive.WORK_KIND}-gate"
PROMPT_TEMPLATE = "contracts/worker/document-rebuild-gate.md"
SKILL_SECTION = f"{drive.SKILL} の「承認の前の確認」"
FACTS_FILE, MATERIAL_FILE, TITLE_FILE, QUESTIONS_FILE, QUESTION_FILE, PREVIOUS_FILE = (
    "approval-facts.json", "material.md", "title.txt", "questions.json", "question.md", "previous-material.md",
)  # fmt: skip
PARTS_DIR = "parts"
DESIGN, RELEASE = "design", "release"
# 設計 PR の文書のうち読み手役に読ませるもの（要求仕様のコピー・設計書・詳細設計書。実装計画は書いた担当が読み直す）
GATE_DOCS = re.compile(r"^issues/issue-\d+(?:-\d+)*-(?:requirements|design|design-detail)\.md$")
TITLE, BODY = reading.SENT_BACK_TARGETS
TARGET_NAMES = {TITLE: "題", BODY: "本文"}
MATERIAL_SHA, MATERIAL_FILE_SHA, BODY_SHA = reading.MATERIAL_SHA, reading.MATERIAL_FILE_SHA, reading.BODY_SHA

HubOf = Callable[[str], GitHub]
CiWatch = Callable[[int, Path], Mapping[str, Any]]


class Incomplete(Stop):
    """worker の結果の欠け（`stopped`（incomplete））。`gaps` を持つ。"""

    kind = "incomplete"

    def __init__(self, gaps: list[dict[str, str]]) -> None:
        super().__init__(f"確かめの記録か直したものに欠けが {len(gaps)} 件ある")
        self.gaps = gaps


class CheckFailed(Stop):
    """直した文書の CI が通らない（`stopped`（check_failed））。"""

    kind = "check_failed"


@dataclass(frozen=True)
class GateArgs:
    """1 回の実行の引数。`state_dir` と `cwd` は絶対パス。`inputs` は工程の入力（無ければ空）。"""

    gate: str
    pr: Optional[int]
    material: Optional[Path]
    reviewed: Optional[str]
    no_doc_fix: bool
    state_dir: Path
    cwd: Path
    inputs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Read:
    """読ませる対象 1 件（名前・読ませるファイル・立場・一部だけ読ませるなら真）。"""

    name: str
    file: Path
    reader: str
    partial: bool = False


@dataclass
class Plan:
    """読ませるもの・worker が直してよいファイル・比べる元（設計書の手順 2〜3）。"""

    questions: list[dict[str, Any]]
    key: str
    material: Path
    expected: str
    start: str
    docs: dict[str, str]
    targets: list[Read] = field(default_factory=list)
    fixable: list[str] = field(default_factory=list)
    title_file: Optional[Path] = None
    previous: Optional[Path] = None
    # 変更要求を 1 回したので、PR の本文から写した行を直させない（そのときの写した本文）
    frozen_body: Optional[str] = None


@dataclass
class Run:
    """1 回の実行の文脈（引数・git・GitHub・CI の待ち・確認の id と、手順 1 で固定した PR）。"""

    args: GateArgs
    g: git.Git
    hub: GitHub
    ci_watch: CiWatch
    check: str = ""
    repo: str = ""
    remote: str = ""
    view: Optional[PullView] = None
    _publisher: Optional[Publisher] = None
    _published: dict[str, str] = field(default_factory=dict)

    def exposed_before(self) -> bool:
        """この実行で書き先を公開（か読めない）と確かめたか。確かめていなければ偽（照会しない）。"""
        return self._publisher is not None and self._publisher.exposed()

    def publishable(self, text: str) -> str:
        """`text` を PR へ書くときの形（秘密などを伏せた後）。読み手役に読ませる本文とハッシュはこの形にする
        （提示のコメントと違う本文への承認を数えない）。"""
        return self.publish_all([text])[0]

    def publish_all(self, texts: Sequence[str]) -> list[str]:
        """`publishable` をまとめて行う。まだ調べていない文を 1 回の検査に通し、同じ文は 1 回だけ調べる。"""
        new = [t for t in dict.fromkeys(texts) if t not in self._published]
        if new:
            if self._publisher is None:
                self._publisher = Publisher(self.hub)
            try:
                safe, _ = self._publisher.guarded(*new)
            except GitHubError as exc:
                raise Precondition(f"承認資料を公開先に合わせて整えられない: {exc}") from None
            for text, got in zip(new, safe, strict=True):
                self._published[text] = self._published[got] = got
        return [self._published[t] for t in texts]

    @property
    def pr(self) -> PullView:
        if self.view is None:
            raise Precondition("承認する PR を固定していない")
        return self.view

    @property
    def body_route(self) -> bool:
        return self.args.material is None

    def base(self) -> dict[str, Any]:
        return {"repo": self.repo, "check": self.check, "branch": self.pr.head_ref, "gate": self.args.gate, "pr": self.pr.number}

    def state(self, name: str) -> Path:
        return self.args.state_dir / name


# --- ファイル ----------------------------------------------------------------------------------------------


def _put_file(path: Path, text: str) -> Path:
    """所有者だけが読めるファイルに書く。置き先がシンボリックリンクなら先へ書かずに置き換え、親がリンクなら止める。"""
    if path.parent.is_symlink():
        raise Precondition(f"{path.parent} がシンボリックリンクなので書かない")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        mvv.write_private(path, text)
    except OSError as exc:
        raise Precondition(f"{path} への書き込みに失敗した（{exc.strerror or exc}）") from None
    return path


def _text_or_none(path: Path) -> Optional[str]:
    """ファイルの中身。無ければ None、読めなければ `Precondition`。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無いコピー・題の案は「無い」として扱い、呼び手が分ける
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise Precondition(f"{path} を読めない: {exc}") from None


def _file_text(path: Path) -> str:
    """ファイルの中身。無い・読めなければ `Precondition`。"""
    text = _text_or_none(path)
    if text is None:
        raise Precondition(f"{path} が無い")
    return text


# --- 手順 1: head を固定する ---------------------------------------------------------------------------


def fix_head(run: Run) -> None:
    """PR を 1 回取得して head・題・本文を固定する。PR が決まらない・読めなければ `Precondition`。"""
    args = run.args
    number = args.pr if args.pr is not None else _input_pr(args)
    if number is None and args.material is not None:
        raise Precondition("--material の経路は PR の番号が要る。--pr か工程の入力の pr で渡す")
    branch = None if number is not None else drive.checked_out(run.g)
    try:
        run.view = pull_view.pull_view(run.hub, number, branch)
    except GitHubError as exc:
        raise Precondition(f"承認する PR を読めない: {exc}") from None
    if run.view is None:
        raise Precondition("承認する PR が無い（" + (f"#{number}" if number is not None else f"ブランチ {branch} の開いた PR") + "）")


def _input_pr(args: GateArgs) -> Optional[int]:
    """リリースのゲートは工程の入力の `pr`（スプリントの PR）。設計は作業場所のブランチの PR（None を返す）。"""
    if args.gate != RELEASE:
        return None
    pr = args.inputs["pr"] if "pr" in args.inputs else None
    if not isinstance(pr, int):
        raise Precondition("工程の入力にスプリントの PR の番号（pr）が無い。--pr で渡す")
    return pr


def same_tree(run: Run) -> None:
    """設計のゲートでは、作業場所の HEAD が固定した head と同じで、変更が無いことを確かめる。違えば `Precondition`。"""
    if run.args.gate != DESIGN:
        return
    try:
        here, dirty = run.g.rev_parse("HEAD"), run.g.status_paths()
    except git.GitError as exc:
        raise Precondition(f"作業場所を読めない: {exc}") from None
    if here != run.pr.head_sha or dirty:
        raise Precondition(f"作業場所の HEAD {here} が PR の head {run.pr.head_sha} と違うか、変更が残っている（{len(dirty)} 件）")


def open_review_again(run: Run) -> Optional[dict[str, Any]]:
    """解消していない再レビュー待ちの行を扱う（設計書の「設計のレビューへ戻す」）。進めてよければ None、終えるなら結果 JSON。"""
    pending = reading.pending_review(reading.doc_rows(), run.repo, run.pr.head_ref)
    if pending is None:
        return None
    commit = pending["commit"]
    row = {"repo": run.repo, "branch": run.pr.head_ref, "commit": commit}
    if run.args.reviewed == commit:
        reading.append_doc({"kind": "review_resolved", **row})
        return None
    if not _contains(run.g, commit, run.pr.head_sha):
        if not _contains(run.g, commit, "HEAD"):
            reading.append_doc({"kind": "review_cancelled", **row})
            return None
        _push_head(run)
    _wait_ci(run)
    return _review_again(pending["check"], commit, list(pending["docs"]))


def _contains(g: git.Git, commit: str, ref: str) -> bool:
    """`ref` が `commit` を含むか。コミットが手元に無ければ偽。"""
    try:
        return g.commit_or_none(commit) is not None and g.is_ancestor(commit, ref)
    except git.GitError as exc:
        raise Precondition(f"コミット {commit} を確かめられない: {exc}") from None


def _push_head(run: Run) -> None:
    done = run.g.attempt("push", run.remote, "HEAD:" + git.HEADS + run.pr.head_ref)
    if done.returncode != 0:
        raise Precondition(f"直した文書を push できない（git の終了コード {done.returncode}）: {done.stderr.strip()}")


def _wait_ci(run: Run) -> None:
    got = run.ci_watch(run.pr.number, run.args.cwd)
    if got.get("status") != result.OK:
        cause = got.get("cause")
        detail = cause.get("detail") if isinstance(cause, Mapping) else got.get("summary")
        raise CheckFailed(f"直した文書の CI が通らない: {detail}")


def _review_again(check: str, commit: str, docs: list[str]) -> dict[str, Any]:
    outputs = {"review_again": {"commit": commit, "docs": docs}, "check": {"id": check, "fingerprint": None}}
    summary = f"読み手役の指摘で直した文書（{len(docs)} 件）を設計のレビューへ戻す"
    return result.result(TOOL, result.OK, summary, outputs=outputs)


# --- 手順 2〜3: 承認資料の作業用のファイルと、読ませるもの ----------------------------------------------------


def working_material(run: Run, rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, Optional[str], Optional[Mapping[str, Any]]]:
    """承認資料の始めの本文を決める。（始めの本文, 基準のセクション, 前に読ませた本文, 基準の完了の行）を返す。

    前の確認のコピーは、2 つのハッシュが最後の完了の行とどちらも同じときだけ使う（設計書の「確認の id と完了の行」）。
    PR の本文の経路で本文の代わりにコピーから始めるのは、head と本文が前の確認と同じときだけ。
    """
    args, pr = run.args, run.pr
    done = reading.last_done(rows, run.repo, args.gate, pr.number)
    expected = reading.remained_section(reading.remained_of(rows, run.repo, args.gate, pr.number))
    copy = _text_or_none(reading.materials_path(run.repo, args.gate, pr.number)) if done is not None else None
    previous = None
    if copy is not None and done is not None and reading.material_matches(copy, expected, done):
        previous = reading.split_section(copy, expected)[0]
    if args.material is not None:
        source = _file_text(args.material)
        if approval_decide.material_head(source) != pr.head_sha:
            raise Precondition(
                f"承認資料の {approval_decide.HEAD_LINE} が PR の head {pr.head_sha} と違う。materials を組み直してから打ち直す"
            )
        return gd.strip_marks(source, expected), expected, previous, done
    if previous is not None and done is not None and done["head"] == pr.head_sha and done[BODY_SHA] == reading.sha256(pr.body):
        return previous, expected, previous, done
    return gd.strip_marks(pr.body, expected), expected, previous, done


def gate_docs(run: Run) -> dict[str, str]:
    """設計 PR の文書と、固定した head の blob の SHA。head か作業場所で通常ファイルでない文書があれば `Precondition`。"""
    _, changed = drive.start_and_changes(run.g, run.pr.base_ref, run.remote)
    paths = sorted(p for p in changed if GATE_DOCS.match(p))
    if not paths:  # パスを渡さない ls-tree は根の全件を返す
        return {}
    try:
        listed = run.g.run("ls-tree", "-z", run.pr.head_sha, "--", *paths)
    except git.GitError as exc:
        raise Precondition(f"文書の blob を読めない: {exc}") from None
    entries = {path: meta.split() for meta, _, path in (e.partition("\t") for e in listed.split("\0") if e)}
    for path in paths:
        if path not in entries or entries[path][0] not in git.REGULAR_MODES:
            raise Precondition(f"文書 {path} が PR の head で通常ファイルでないので読ませない")
        doc_file(run, path)
    return {path: entries[path][2] for path in paths}


def doc_file(run: Run, path: str) -> Path:
    """作業場所の文書 `path` の、開いてよいパス。通常ファイルでないかシンボリックリンクを経由すれば `Precondition`。"""
    if not drive.regular_file(run.args.cwd, path):
        raise Precondition(f"文書 {path} が作業場所で通常ファイルでない（無いか、シンボリックリンクを経由する）ので開かない")
    return run.args.cwd / path


def plan_reads(run: Run, rows: Sequence[Mapping[str, Any]], form: Optional[str]) -> Plan:
    """読ませるもの・差分のファイル・直してよいファイルを決める（設計書の「読ませるものの決め方」）。"""
    args, pr = run.args, run.pr
    try:
        questions = reading.gate_questions(args.gate, pr.number, pr.title, form)
    except reading.FormMissing as exc:
        raise Precondition(str(exc)) from None
    _put_file(run.state(QUESTIONS_FILE), json.dumps({"questions": questions}, ensure_ascii=False, indent=2) + "\n")
    start, expected, previous, done = working_material(run, rows)
    start = run.publishable(start)
    material = _put_file(run.state(MATERIAL_FILE), start)
    docs = gate_docs(run) if args.gate == DESIGN else {}
    plan = Plan(questions, reading.rd.question_key(questions), material, expected, start, docs)
    run.state(TITLE_FILE).unlink(missing_ok=True)
    if run.body_route:
        if reading.sent_back(rows, run.repo, args.gate, pr.number):
            plan.frozen_body = run.publishable(gd.strip_marks(pr.body, expected))
        else:
            plan.title_file = run.state(TITLE_FILE)
            plan.fixable.append(str(plan.title_file))
    _plan_question(run, plan, rows)
    if done is None or reading.sha256(start) != done[MATERIAL_SHA]:
        plan.targets.append(Read(reading.MATERIAL, material, reading.APPROVER))
        plan.fixable.append(str(material))
        plan.previous = _put_file(run.state(PREVIOUS_FILE), previous) if previous is not None else None
    for path, blob in docs.items():
        _plan_doc(run, plan, rows, path, blob)
    return plan


def _plan_question(run: Run, plan: Plan, rows: Sequence[Mapping[str, Any]]) -> None:
    """質問の文は、前に読ませたキーと違えば全体を読ませる（1 文で短いため）。"""
    prev = reading.latest_read(rows, (run.repo, run.args.gate, run.pr.number, reading.QUESTION))
    if prev is None or prev["hash"] != plan.key:
        plan.targets.append(Read(reading.QUESTION, _put_file(run.state(QUESTION_FILE), gd.question_text(plan.questions)), reading.APPROVER))


def _plan_doc(run: Run, plan: Plan, rows: Sequence[Mapping[str, Any]], path: str, blob: str) -> None:
    """文書は、前の blob と同じなら読ませず、違えば変わった段落とその前後だけ（前の blob が手元に無ければ全体）を読ませる。"""
    prev = reading.latest_read(rows, (run.repo, run.pr.head_ref, path))
    if prev is not None and prev["hash"] == blob:
        return
    old, now = run.g.blobs(prev["hash"], blob) if prev is not None else (None, None)
    if old is None or now is None:
        plan.targets.append(Read(path, run.args.cwd / path, reading.ENGINEER))
    else:
        part = gd.changed_part(old.decode("utf-8", "replace"), now.decode("utf-8", "replace"))
        plan.targets.append(Read(path, _put_file(run.state(PARTS_DIR) / path.replace("/", "__"), part), reading.ENGINEER, partial=True))
    if not run.args.no_doc_fix:
        plan.fixable.append(path)


def write_facts(run: Run) -> Path:
    """素材のファイル（PR の URL・題・ベースと head・変更量・直前の MVV 判定）を書く（設計書の「承認資料を書く」）。"""
    pr = run.pr
    mvv = run.args.inputs["mvv_gate"] if "mvv_gate" in run.args.inputs else None
    facts = {
        "url": pr.url,
        "title": pr.title,
        "base": pr.base_ref,
        "head": pr.head_sha,
        "changes": {"commits": pr.commits, "files": pr.files, "additions": pr.additions, "deletions": pr.deletions},
        "mvv_gate": mvv if isinstance(mvv, Mapping) else "MVV なし",
    }
    return _put_file(run.state(FACTS_FILE), json.dumps(facts, ensure_ascii=False, indent=2) + "\n")


def gate_prompt(run: Run, plan: Plan, facts: Path) -> str:
    """起動指示のテンプレートへ、読ませるファイルと直してよいファイルを差し込む。"""
    root = identity.load().root
    try:
        template = string.Template((root / PROMPT_TEMPLATE).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Precondition(f"起動指示のテンプレート {root / PROMPT_TEMPLATE} を読めない: {exc.strerror or exc}") from None
    lines = [
        f"- `{t.name}`: `{t.file}`（{t.reader}" + (f"・ハッシュ `{plan.key}`" if t.name == reading.QUESTION else "") + "）"
        for t in plan.targets
    ]
    fixable = [f"- `{p}`" for p in plan.fixable] or ["- 無し"]
    if plan.frozen_body is not None:
        fixable.append("- 承認資料のうち PR の本文から写した行は直さない（変更要求は 1 回まで。残った文として返す）")
    if run.args.no_doc_fix and plan.docs:
        fixable.append("- 文書は直さない（設計のレビューへ戻すのは 1 回まで。残った文として返す）")
    values = {
        "skill": str(root / SKILL_SECTION),
        "gate": run.args.gate,
        "facts": str(facts),
        reading.MATERIAL: str(plan.material),
        "previous": f"`{plan.previous}`" if plan.previous is not None else "無し",
        "targets": "\n".join(lines),
        "fixable": "\n".join(fixable),
    }
    return template.substitute(values)


# --- 手順 4〜5: worker に読ませ、直したものを確かめる ----------------------------------------------------------


def current_hash(run: Run, plan: Plan, name: str) -> str:
    """対象の今の中身のハッシュ（文書は作業場所の blob、承認資料はファイル全体の SHA-256、質問の文は質問のキー）。"""
    if name == reading.QUESTION:
        return plan.key
    if name == reading.MATERIAL:
        return reading.sha256(_file_text(plan.material))
    doc_file(run, name)
    try:
        return run.g.run("hash-object", "--", name).strip()
    except git.GitError as exc:
        raise Precondition(f"{name} の blob を読めない: {exc}") from None


def dirty_files(run: Run) -> dict[str, Optional[str]]:
    """作業場所の未コミットの変更のパスと、その中身の SHA-256（ファイルとして読めないパスは None）。"""
    try:
        paths = run.g.status_paths()
    except git.GitError as exc:
        raise Precondition(f"作業場所の変更を読めない: {exc}") from None
    return {p: _digest(run.args.cwd / p) for p in paths}


def _digest(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # noqa: NDF401 ファイル: 消したファイルとディレクトリは「中身なし」として比べる
        return None


def verify_worker(run: Run, plan: Plan, report: Mapping[str, Any], before: Mapping[str, Optional[str]]) -> dict[str, Mapping[str, Any]]:
    """設計書の手順 5 の確かめ。対象の名前から記録を返す。`not_run` は `Precondition`、欠けは `Incomplete`。"""
    metrics = report["metrics"] if isinstance(report.get("metrics"), Mapping) else {}
    raw = metrics["readers"] if isinstance(metrics.get("readers"), list) else []
    found = {r["name"]: r for r in raw if isinstance(r, Mapping) and isinstance(r.get("name"), str)}
    not_run = [t.name for t in plan.targets if t.name in found and found[t.name].get("verdict") == gd.NOT_RUN]
    if not_run:
        raise Precondition(drive.not_run_detail("対象", not_run))
    gaps = [g for g in (_target_gap(run, plan, t, found) for t in plan.targets) if g is not None]
    gaps.extend(_outside_fixable(run, plan, before))
    final = _file_text(plan.material)
    if run.publishable(final) != final:
        gaps.append({"kind": reading.MATERIAL, "name": reading.MATERIAL, "reason": "PR へ書く前に伏せる値（秘密など）がある"})
    if not run.body_route and approval_decide.material_head(final) != run.pr.head_sha:
        gaps.append({"kind": reading.MATERIAL, "name": reading.MATERIAL, "reason": f"行 {approval_decide.HEAD_LINE} が変わった"})
    if plan.frozen_body is not None and gd.body_edited(plan.frozen_body, final):
        gaps.append({"kind": reading.MATERIAL, "name": reading.MATERIAL, "reason": "PR の本文から写した行を変えた（変更要求は 1 回まで）"})
    if plan.title_file is None and run.state(TITLE_FILE).exists():
        gaps.append({"kind": "title", "name": TITLE_FILE, "reason": "題の案を書いてよいと決めていない"})
    if gaps:
        raise Incomplete(gaps)
    return {t.name: found[t.name] for t in plan.targets}


def _target_gap(run: Run, plan: Plan, t: Read, found: Mapping[str, Mapping[str, Any]]) -> Optional[dict[str, str]]:
    rec = found[t.name] if t.name in found else None
    why = "記録が無い" if rec is None else gd.record_gap(rec)
    if why is None and rec is not None and rec["checks"][-1]["hash"] != current_hash(run, plan, t.name):
        why = "最後に読ませた後に中身が変わった"
    return None if why is None else {"kind": "reader", "name": t.name, "reason": why}


def _outside_fixable(run: Run, plan: Plan, before: Mapping[str, Optional[str]]) -> list[dict[str, str]]:
    """worker が、直してよいファイルの外を変えていれば、その欠け。起動の前からある変更（`before`）は、中身が同じなら数えない。"""
    after = dirty_files(run)
    changed = sorted(p for p in {*before, *after} if p not in before or p not in after or before[p] != after[p])
    return [{"kind": "outside", "name": p, "reason": "直してよいファイルの外を変えた"} for p in changed if p not in plan.fixable]


# --- 手順 6〜8 --------------------------------------------------------------------------------------------


def push_fixed(run: Run, docs: Sequence[str]) -> str:
    """再レビュー待ちの行を書いてから、直した文書をコミットして push し、CI を待つ。push したコミットを返す。"""
    try:
        run.g.run("add", "--", *docs)
        run.g.run("commit", "-m", f"Docs: 読み手役の指摘で設計 PR の文書を直す（#{run.pr.number}）", "--", *docs)
        commit = run.g.rev_parse("HEAD")
    except git.GitError as exc:
        raise Precondition(f"直した文書をコミットできない: {exc}") from None
    row = {"kind": "review_pending", "repo": run.repo, "check": run.check, "branch": run.pr.head_ref, "commit": commit, "docs": list(docs)}
    reading.append_doc(row)
    _push_head(run)
    _wait_ci(run)
    return commit


def refetch_check(run: Run, head: str) -> PullView:
    """PR を 1 回取得し直し、head・題・本文が動いていないかを確かめる。動いていれば `Precondition`。"""
    try:
        now = pull_view.pull_view(run.hub, run.pr.number, None)
    except GitHubError as exc:
        raise Precondition(f"PR を取得し直せない: {exc}") from None
    if now is None:
        raise Precondition(f"PR #{run.pr.number} を取得し直せない")
    if now.head_sha != head:
        raise Precondition(f"読ませている間に PR の head が {head} から {now.head_sha} へ動いた。打ち直すと足された部分だけを読ませる")
    if now.title != run.pr.title or now.body != run.pr.body:
        raise Precondition("読ませている間に PR の題か本文が変わった。打ち直す")
    return now


def post_material(run: Run, now: PullView, final: str) -> None:
    """承認資料の最終版を PR のコメントとして 1 件足す。同じ中身のコメントがあれば足さない（設計書の手順 8）。

    足す直前に、参照先の公開範囲を照会し直す（読み手役の実行の前に照会した結果を使わない）。伏せる所ができていれば足さずに止める。"""
    if reading.comment_has(now.comments, reading.sha256(final)):
        return
    try:
        posted = Publisher(run.hub).comment_unchanged(run.pr.number, final, exposed_before=run.exposed_before())
    except GitHubError as exc:
        raise Precondition(f"承認資料を PR のコメントで足せない: {exc}") from None
    if posted is None:
        raise Precondition(
            "承認資料に、PR へ書く前に伏せる値がある（確認の間に参照先が非公開へ変わった場合を含む）。打ち直すと伏せた本文を読ませる"
        )


def send_back(run: Run, plan: Plan, targets: Sequence[str], raised: Mapping[str, Sequence[Mapping[str, str]]]) -> dict[str, Any]:
    """変更要求の行を書き、記録を書かずに `stopped`（precondition）で止める（変更要求。設計書の決定 10）。"""
    reading.append_doc({"kind": "sent_back", **run.base(), "targets": list(targets)})
    said = "・".join(f"{reading.target_label(n)}: {q['quote']}（{q['why']}）" for n, qs in raised.items() for q in qs) or "無し"
    where = [*([str(plan.title_file)] if TITLE in targets else []), str(plan.material)]
    names = "と".join(TARGET_NAMES[t] for t in targets)
    return result.stopped(
        TOOL, "precondition", f"PR の{names}を直さないと通じない（変更要求）。読み手役の指摘: {said}。直しの案: {'・'.join(where)}"
    )


# --- 手順 9: 記録する ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Final:
    """承認資料の最終版と、今回の確認で書く読ませた行。"""

    text: str
    section: str
    rows: list[dict[str, Any]]

    @property
    def sha256(self) -> str:
        return reading.sha256(self.text[: len(self.text) - len(self.section)])

    @property
    def file_sha256(self) -> str:
        return reading.sha256(self.text)


def write_records(run: Run, plan: Plan, final: Final, head: str, pushed: bool) -> dict[str, Any]:
    """設計書の手順 9 の 1〜5 を順に書き、`outputs.check` の値を返す。"""
    base = run.base()
    body_sha = reading.sha256(run.pr.body) if run.body_route else None
    hashes = {MATERIAL_SHA: final.sha256, MATERIAL_FILE_SHA: final.file_sha256, BODY_SHA: body_sha}
    for row in final.rows:
        reading.append_doc({"kind": "read", **base, **row})
    _put_file(reading.materials_path(run.repo, run.args.gate, run.pr.number), final.text)
    if pushed:
        reading.append_doc(reading.done_row(base, **hashes))
        return {"id": run.check, "fingerprint": None}
    now = {reading.QUESTION: plan.key, reading.MATERIAL: final.sha256, **plan.docs}
    carried = {r["name"] for r in final.rows if r["verdict"] == reading.REMAINED}
    current = [{"kind": "read", **base, **r} for r in final.rows]
    reads = reading.select_reads(current, reading.doc_rows(), now, carried, base)
    entry = {"questions": plan.questions, "prs": [{"number": run.pr.number, "branch": run.pr.head_ref, "head": head}], "reads": reads}
    fingerprint = reading.row_fingerprint(reading.add(entry, run.args.cwd, run.repo))
    reading.append_doc(reading.done_row(base, question_key=plan.key, head=head, fingerprint=fingerprint, **hashes))
    return {"id": run.check, "fingerprint": fingerprint}


# --- 全体 --------------------------------------------------------------------------------------------------


def drive_gate(
    args: GateArgs,
    *,
    hub_of: HubOf,
    ci_watch: CiWatch,
    form_of: Callable[[Path], Optional[str]],
    git_of: Callable[[Path], git.Git] = git.Git,
    runner: drive.Runner = drive._run,
) -> dict[str, Any]:
    """設計書の「処理の流れ」の手順 1〜9 を順に呼び、結果 JSON を返す。"""
    g = git_of(args.cwd)
    try:
        remote = g.remote()
        repo = g.remote_repo(remote)
        run = Run(args, g, hub_of(repo), ci_watch, drive.new_check_id(), repo, remote)
        fix_head(run)
        again = open_review_again(run)
        if again is not None:
            return again
        same_tree(run)
        plan = plan_reads(run, reading.doc_rows(), form_of(args.cwd) if args.gate == RELEASE else None)
        log.info("承認の前の確認で読ませるものを決めた", extra={"head": run.pr.head_sha, "targets": len(plan.targets)})
        found: dict[str, Mapping[str, Any]] = {}
        if plan.targets:
            before = dirty_files(run)
            out, result_file = drive.run_worker_at(args.state_dir, args.cwd, WORK_KIND, gate_prompt(run, plan, write_facts(run)), runner)
            log.info("読み手役の worker が終えた", extra={"worker_status": out.get("status")})
            stopped, report = drive.worker_report(out, result_file)
            if stopped is not None:
                return stopped
            found = verify_worker(run, plan, report, before)
        return complete_check(run, plan, found)
    except Incomplete as exc:
        return result.stopped(TOOL, exc.kind, exc.detail, gaps=exc.gaps)
    except Stop as exc:
        return result.stopped(TOOL, exc.kind, exc.detail)
    except (git.GitError, reading.ReadingInvalid, OSError) as exc:
        return result.stopped(TOOL, "precondition", f"記録か作業場所を読み書きできない: {exc}")


def complete_check(run: Run, plan: Plan, found: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """手順 5 の残り（残った文のセクション・変更要求の見分け）〜9。"""
    draft = gd.strip_marks(_file_text(plan.material), plan.expected)
    raised = {name: gd.quotes_of(rec["checks"][-1]) for name, rec in found.items() if rec["verdict"] == gd.FAIL}
    hashes = {reading.QUESTION: plan.key, reading.MATERIAL: reading.sha256(draft), **_doc_hashes(run, plan)}
    contents = {reading.QUESTION: gd.question_text(plan.questions), reading.MATERIAL: draft}
    contents.update({path: _file_text(doc_file(run, path)) for path in plan.docs})
    parts = {t.name: _file_text(t.file) if t.partial else None for t in plan.targets}
    if reading.MATERIAL in parts and plan.previous is not None:
        # 承認資料は worker が書き足した後に変わった段落とその前後だけを読ませるので、読ませた部分は今の本文から切り出す
        parts[reading.MATERIAL] = gd.changed_part(_file_text(plan.previous), draft)
    prev = reading.remained_of(reading.doc_rows(), run.repo, run.args.gate, run.pr.number)
    rows = gd.doc_rows_of(found, gd.carried_quotes(prev, contents, parts, raised, redacted_pattern), hashes)
    _publish_quotes(run, rows)
    remained = [r for r in rows if r["verdict"] == reading.REMAINED]
    section = reading.remained_section(remained)
    final = Final(draft + section, section, rows)
    fixed_docs = [p for p in plan.docs if p in plan.fixable and hashes[p] != plan.docs[p]]
    targets = _send_back_targets(run, plan, draft)
    head = push_fixed(run, fixed_docs) if fixed_docs else run.pr.head_sha
    # 読ませず、push も投稿もしないなら初回の取得を使う（最新の状態は提示の直前の突き合わせが確かめる）
    posting = run.body_route and not reading.comment_has(run.pr.comments, reading.sha256(final.text))
    now = run.pr if not (plan.targets or fixed_docs or posting) else refetch_check(run, head)
    if targets:
        return send_back(run, plan, targets, raised)
    if run.body_route and not fixed_docs:
        post_material(run, now, final.text)
    check = write_records(run, plan, final, head, bool(fixed_docs))
    if fixed_docs:
        return _review_again(run.check, head, fixed_docs)
    _put_file(plan.material, final.text)
    material = {"path": str(plan.material), "sha256": final.sha256, "file_sha256": final.file_sha256}
    outputs = {"questions": str(run.state(QUESTIONS_FILE)), "head": head, reading.MATERIAL: material, "check": check}
    items = gd.remained_items(drive.READER_PREFIX, remained)
    reads = [{k: r[k] for k in ("name", "reader", "round", "verdict")} for r in rows]
    summary = f"承認の前の確認を記録した（読ませた対象 {len(plan.targets)} 件・残った文 {len(items)} 件）"
    return result.result(TOOL, result.OK, summary, items, {"reads": reads}, outputs=outputs)


def _publish_quotes(run: Run, rows: Sequence[dict[str, Any]]) -> None:
    """残った文の引用と理由を PR へ書くときの形にする。伏せて変わった引用は元の文を残さず、`redacted` を持つ
    （次の確認は、固定の文の所をその種類の値の形として探す）。"""
    remained = [r for r in rows if r["verdict"] == reading.REMAINED]
    run.publish_all([q[k] for r in remained for q in r["quotes"] for k in ("quote", "why")])
    for row in remained:
        quotes = []
        for q in row["quotes"]:
            out = {**q, "quote": run.publishable(q["quote"]), "why": run.publishable(q["why"])}
            quotes.append(gd.marked_redacted(out, q))
        row["quotes"] = quotes


def _send_back_targets(run: Run, plan: Plan, draft: str) -> list[str]:
    """PR の題と本文のうち、直さないと通じないもの（題の案がある・PR の本文から写した行を消すか変えた）。"""
    if not run.body_route or plan.frozen_body is not None:
        return []
    title = _text_or_none(plan.title_file) if plan.title_file is not None else None
    out = [TITLE] if title is not None and title.strip() else []
    if gd.body_edited(run.publishable(gd.strip_marks(run.pr.body, plan.expected)), draft):
        out.append(BODY)
    return out


def _doc_hashes(run: Run, plan: Plan) -> dict[str, str]:
    """文書の今の blob（直してよい文書は作業場所の中身、ほかは固定した head の blob）。"""
    return {path: current_hash(run, plan, path) if path in plan.fixable else blob for path, blob in plan.docs.items()}
