"""`ndf document-rebuild gate`（#4446 の受け入れ条件 2・4・5・6・9、設計書の「処理の流れ」とテスト設計）。

git・GitHub・`ndf worker run`・CI の待ちは偽物に替え、子のプロセスを起動しない。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import git
from ndf.adapters import publish, run_log
from ndf.domains.docrebuild import cli, gate, gate_decide
from ndf.domains.records import reading
from ndf.domains.records import reading_decide as rd

REPO = "o/r"
BRANCH = "sprint/1-x"
DESIGN = "issues/issue-1-design.md"
PLAN = "issues/issue-1-plan.md"
BODY = "## 目的\n\n設計を直す。\n\n## 決めたこと\n\n- A にする\n"
H1, H2 = "1" * 40, "2" * 40


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    return tmp_path / "mvv-state"


def blob(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGit:
    """git の偽物。作業場所のファイルと、コミットごとの文書の中身を持つ。"""

    def __init__(self, cwd: Path, docs: Mapping[str, str], head: str = H1) -> None:
        self.cwd = cwd
        self.head = head
        self.trees: dict[str, dict[str, str]] = {head: dict(docs)}
        self.objects: dict[str, bytes] = {}
        self.pushed: list[str] = []
        self.push_code = 0
        self.local: set[str] = {head}
        self.modes: dict[str, str] = {}
        for path, text in docs.items():
            self._put(path, text)

    def _put(self, path: str, text: str) -> None:
        (self.cwd / path).parent.mkdir(parents=True, exist_ok=True)
        (self.cwd / path).write_text(text, encoding="utf-8")
        self.objects[blob(text)] = text.encode()

    def remote(self) -> str:
        return "origin"

    def remote_repo(self, remote: Optional[str] = None) -> str:
        return REPO

    def rev_parse(self, ref: str) -> str:
        if ref == "HEAD":
            return self.head
        return ref

    def commit_or_none(self, ref: str) -> Optional[str]:
        if ref.startswith("refs/remotes/origin/"):
            return "b" * 40
        return ref if ref in self.local else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return a == (self.head if b == "HEAD" else b) or (b == "HEAD" and a in self.local)

    def status_paths(self) -> list[str]:
        tree = self.trees[self.head]
        return sorted(p for p in tree if (self.cwd / p).read_text(encoding="utf-8") != tree[p])

    def fetch(self, remote: str, *refspecs: str) -> git.Completed:
        return git.Completed(0, "", "")

    def blobs(self, *names: str) -> list[Optional[bytes]]:
        return [self.objects.get(n) for n in names]

    def attempt(self, *args: str) -> git.Completed:
        if args[0] == "diff":
            return git.Completed(0, "".join(f"{p}\0" for p in self.trees[self.head]), "")
        if args[0] == "push":
            self.pushed.append(self.head)
            return git.Completed(self.push_code, "", "rejected\n" if self.push_code else "")
        raise AssertionError(f"想定していない git: {args}")

    def run(self, *args: str) -> str:
        if args[0] == "remote":
            return "origin\n"
        if args[:2] == ("branch", "--show-current"):
            return BRANCH + "\n"
        if args[0] == "ls-tree":
            tree, paths = self.trees[args[2]], args[args.index("--") + 1 :]
            return "".join(f"{self.modes.get(p, '100644')} blob {blob(tree[p])}\t{p}\0" for p in paths)
        if args[0] == "hash-object":
            return "".join(blob((self.cwd / p).read_text(encoding="utf-8")) + "\n" for p in args[args.index("--") + 1 :])
        if args[0] == "add":
            return ""
        if args[0] == "commit":
            new = hashlib.sha1(self.head.encode()).hexdigest()
            self.trees[new] = {p: (self.cwd / p).read_text(encoding="utf-8") for p in self.trees[self.head]}
            for text in self.trees[new].values():
                self.objects[blob(text)] = text.encode()
            self.head = new
            self.local.add(new)
            return ""
        raise AssertionError(f"想定していない git: {args}")


@dataclass
class FakeHub:
    """GitHub の偽物。PR の値・取得の回数・足したコメント・取得のたびに PR を変える関数を持つ。"""

    title: str = "読み手役の確認を工程へ移す"
    body: str = BODY
    head: str = H1
    comments: list[str] = field(default_factory=list)
    fetches: int = 0
    posted: list[str] = field(default_factory=list)
    on_fetch: Optional[Callable[[FakeHub], None]] = None
    repo: str = REPO
    visibility: dict[str, str] = field(default_factory=dict)

    def repository(self, query: str, variables: Optional[dict] = None) -> dict[str, Any]:
        self.fetches += 1
        if self.on_fetch is not None and self.fetches > 1:
            self.on_fetch(self)
        node = {
            "number": 7, "url": "https://github.com/o/r/pull/7", "title": self.title, "body": self.body, "headRefOid": self.head,
            "headRefName": BRANCH, "baseRefName": "main", "changedFiles": 2, "additions": 10, "deletions": 1,
            "commits": {"totalCount": 1}, "comments": {"nodes": [{"body": c} for c in self.comments]},
        }  # fmt: skip
        return {"found": {"nodes": [node]} if "pullRequests(" in query else node}

    def visibilities(self, repos: Any) -> dict[str, str]:
        return {r: self.visibility.get(r, "PRIVATE") for r in repos}

    def rest(self, method: str, path: str, payload: Any = None) -> dict[str, str]:
        assert (method, path) == ("POST", "/repos/o/r/issues/7/comments")
        self.posted.append(payload["body"])
        self.comments.append(payload["body"])
        return {"html_url": "https://example.invalid/comment"}


Behave = Callable[[Path, list[tuple[str, Path]]], list[dict[str, Any]]]


def passing(state_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    """読ませた対象をすべて 1 回で合格にする worker（ハッシュは読ませた時点の全体のもの）。"""
    return [
        {"name": n, "verdict": "pass", "checks": [{"verdict": "pass", "hash": HASH(n, state_dir), "restatements": ["要点"]}]}
        for n, _ in targets
    ]


def HASH(name: str, state_dir: Path) -> str:  # noqa: N802 テストの道具: 対象の今の全体のハッシュ
    if name == reading.QUESTION:
        return rd.question_key(json.loads((state_dir / gate.QUESTIONS_FILE).read_text(encoding="utf-8"))["questions"])
    if name == reading.MATERIAL:
        return reading.sha256((state_dir / gate.MATERIAL_FILE).read_text(encoding="utf-8"))
    return blob((state_dir.parent / name).read_text(encoding="utf-8"))


def failing(name: str, quote: str) -> Behave:
    """`name` を 2 回とも不合格（引用 `quote` が残る）にし、ほかを合格にする worker。"""

    def behave(state_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, Any]]:
        out = passing(state_dir, targets)
        for rec in out:
            if rec["name"] == name:
                h = HASH(name, state_dir)
                bad = {
                    "verdict": "fail",
                    "hash": h,
                    "restatements": ["要点"],
                    "reason": "通じない",
                    "quotes": [{"quote": quote, "why": "語が通じない"}],
                }
                rec.update(verdict="fail", checks=[dict(bad), dict(bad)])
        return out

    return behave


class FakeWorker:
    """`ndf worker run` の偽物。起動指示から読ませる対象を読み、`behave` の記録を結果ファイルへ書く。"""

    def __init__(self, behave: Behave = passing, edit: Optional[Callable[[Path], None]] = None, status: str = "ok") -> None:
        self.behave, self.edit, self.status = behave, edit, status
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        prompt = Path(argv[argv.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        self.prompts.append(prompt)
        state_dir = Path(argv[argv.index("--state-dir") + 1])
        if self.edit is not None:
            self.edit(state_dir)
        targets = [
            (line.split("`")[1], Path(line.split("`")[3])) for line in prompt.splitlines() if line.startswith("- `") and "`: `" in line
        ]
        report = {
            "tool": "worker",
            "status": "ok",
            "summary": "読ませた",
            "items": [],
            "metrics": {"readers": self.behave(state_dir, targets)},
        }
        Path(argv[argv.index("--result-file") + 1]).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        out: dict[str, Any] = {"tool": "worker", "status": self.status, "summary": "終えた", "items": [], "metrics": {}}
        if self.status != "ok":
            out["cause"] = {"kind": "work_stopped", "detail": "止まった"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")


@dataclass
class World:
    cwd: Path
    git: FakeGit
    hub: FakeHub
    ci: list[int] = field(default_factory=list)
    ci_status: str = "ok"

    def watch(self, number: int, cwd: Path) -> dict[str, Any]:
        self.ci.append(number)
        return {"status": self.ci_status, "summary": "CI", "cause": {"kind": "check_failed", "detail": "test が落ちた"}}

    def run(self, worker: FakeWorker, gate_name: str = "design", **kw: Any) -> dict[str, Any]:
        args = gate.GateArgs(
            gate_name,
            kw.pop("pr", None),
            kw.pop("material", None),
            kw.pop("reviewed", None),
            kw.pop("no_doc_fix", False),
            self.cwd / "state",
            self.cwd,
            kw.pop("inputs", {}),
        )
        return gate.drive_gate(
            args,
            hub_of=lambda repo: self.hub,
            ci_watch=self.watch,
            form_of=lambda cwd: "mobile",
            git_of=lambda cwd: self.git,
            runner=worker,
        )  # type: ignore[arg-type]


def world(tmp_path: Path, docs: Optional[Mapping[str, str]] = None) -> World:
    docs = {DESIGN: "# 設計\n\n第 1 の段落。\n\n第 2 の段落。\n\n第 3 の段落。\n", PLAN: "# 計画\n"} if docs is None else docs
    return World(tmp_path, FakeGit(tmp_path, docs), FakeHub())


def checks_rows() -> list[dict[str, Any]]:
    return reading.rows()


# --- 読ませて記録する（条件 1・2・9） ---------------------------------------------------------------------


def test_design_gate_reads_records_and_comments_once(tmp_path: Path) -> None:
    """設計の経路: 質問・承認資料・文書を読ませ、PR を書き換えずにコメントを 1 件足し、記録の指紋を outputs に返す。打ち直しは worker を起動しない。"""
    w = world(tmp_path)
    worker = FakeWorker()
    got = w.run(worker)
    assert got["status"] == "ok", got
    # 結果は結果の規約（outputs の閉じた語彙）を通り、CLI が stopped（invalid）に差し替えない
    assert cli.adopt(got)[0] == got
    assert w.hub.fetches == 2 and len(w.hub.posted) == 1 and worker.calls == 1
    names = {line.split("`")[1] for line in worker.prompts[0].splitlines() if line.startswith("- `") and "`: `" in line}
    assert names == {reading.QUESTION, reading.MATERIAL, DESIGN}
    row = checks_rows()[-1]
    assert got["outputs"]["check"]["fingerprint"] == reading.row_fingerprint(row)
    assert row["question_key"] == rd.question_key(json.loads(Path(got["outputs"]["questions"]).read_text(encoding="utf-8"))["questions"])
    assert {r["name"] for r in row["reads"]} == {reading.QUESTION, reading.MATERIAL, DESIGN} and row["prs"][0]["head"] == H1
    assert reading.comment_has(w.hub.comments, got["outputs"]["material"]["file_sha256"])
    facts = json.loads((tmp_path / "state" / gate.FACTS_FILE).read_text(encoding="utf-8"))
    assert facts["head"] == H1 and facts["mvv_gate"] == "MVV なし"
    again = w.run(worker)
    assert again["status"] == "ok" and worker.calls == 1 and len(w.hub.posted) == 1 and w.hub.fetches == 3
    assert len(checks_rows()) == 2 and checks_rows()[-1]["reads"] == row["reads"]


def test_run_rows_are_used_by_branch_and_only_the_changed_part_is_read(tmp_path: Path) -> None:
    """条件 1・6: 組み直しが同じ blob で読ませた文書は読ませずに reads へ入り、変わった文書は変わった段落と前後だけを読ませる。"""
    old = "# 設計\n\n第 1 の段落。\n\n第 2 の段落。\n\n第 3 の段落。\n\n第 4 の段落。\n\n第 5 の段落。\n"
    w = world(tmp_path, {DESIGN: old})
    base = {"repo": REPO, "check": "run1", "branch": BRANCH, "gate": None, "pr": None}
    reading.append_doc(
        {"kind": "read", **base, "name": DESIGN, "hash": blob(old), "reader": reading.ENGINEER, "round": 1, "verdict": "passed"}
    )
    reading.append_doc(reading.done_row(base))
    worker = FakeWorker()
    assert w.run(worker)["status"] == "ok"
    assert f"`{DESIGN}`" not in worker.prompts[0]
    assert {"name": DESIGN, "reader": reading.ENGINEER, "round": 1, "result": "passed"} in checks_rows()[-1]["reads"]
    new = "# 設計\n\n第 1 の段落。\n\n第 2 の段落を直した。\n\n第 3 の段落。\n\n第 4 の段落。\n\n第 5 の段落。\n"
    objects = w.git.objects
    w.git = FakeGit(tmp_path, {DESIGN: new}, head=H2)
    w.git.objects.update(objects)
    w.hub.head = H2
    worker = FakeWorker()
    assert w.run(worker)["status"] == "ok"
    part = next(line for line in worker.prompts[0].splitlines() if line.startswith(f"- `{DESIGN}`")).split("`")[3]
    assert Path(part).read_text(encoding="utf-8") == "第 1 の段落。\n\n第 2 の段落を直した。\n\n第 3 の段落。\n"


# --- 止まる経路（条件 2・5） ---------------------------------------------------------------------------------


def _not_run(state_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    return [{"name": n, "verdict": "not_run", "checks": [], "reason": "Tool が無い"} for n, _ in targets]


def _edit_doc_after_read(state_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    out = passing(state_dir, targets)
    (state_dir.parent / DESIGN).write_text("# 設計\n\n読ませた後に直した。\n", encoding="utf-8")
    return out


def _touch_plan(state_dir: Path) -> None:
    (state_dir.parent / PLAN).write_text("# 計画を直した\n", encoding="utf-8")


def _move_head(hub: FakeHub) -> None:
    hub.head = H2


def _edit_title(hub: FakeHub) -> None:
    hub.title = "ほかの人が変えた題"


@pytest.mark.parametrize(
    ("worker", "on_fetch", "kind"),
    [
        (FakeWorker(_not_run), None, "precondition"),
        (FakeWorker(_edit_doc_after_read), None, "incomplete"),
        (FakeWorker(edit=_touch_plan), None, "incomplete"),
        (FakeWorker(), _move_head, "precondition"),
        (FakeWorker(), _edit_title, "precondition"),
        (FakeWorker(status="stopped"), None, "work_stopped"),
    ],
    ids=["not_run", "changed-after-read", "outside-file", "head-moved", "title-edited", "worker-stopped"],
)
def test_stops_without_records(tmp_path: Path, worker: FakeWorker, on_fetch: Any, kind: str) -> None:
    """読み手役を立てられない・読ませた後に変えた・決めたファイルの外を変えた・読ませている間に PR が動いた・worker が止まった: 記録を書かずに止める。"""
    w = world(tmp_path)
    w.hub.on_fetch = on_fetch
    got = w.run(worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == kind, got
    assert checks_rows() == [] and not any(r["kind"] == "done" for r in reading.doc_rows()) and w.hub.posted == []


def test_not_run_stop_names_every_target_not_run(tmp_path: Path) -> None:
    """読み手役を立てられなかった対象は読ませる順に並べて、停止の文にする。"""
    w = world(tmp_path)
    worker = FakeWorker(_not_run)
    got = w.run(worker)
    names = [line.split("`")[1] for line in worker.prompts[0].splitlines() if line.startswith("- `") and "`: `" in line]
    assert len(names) > 1
    detail = f"読み手役を立てられなかった対象がある: {'・'.join(names)}。読み手役を立てられる環境に直してから打ち直す"
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition", got
    assert got["cause"]["detail"] == got["summary"] == detail


def test_stops_when_the_pr_or_the_tree_does_not_match(tmp_path: Path) -> None:
    """PR が無い・作業場所の HEAD が PR の head と違う・--material の経路で --pr が無い: 読ませる前に止める。"""
    w = world(tmp_path)
    w.git.head = H2
    w.git.trees[H2] = w.git.trees[H1]
    assert w.run(FakeWorker())["cause"]["kind"] == "precondition"
    got = w.run(FakeWorker(), "release", material=tmp_path / "m.md")
    assert got["cause"]["kind"] == "precondition" and "--pr" in got["cause"]["detail"]
    assert w.run(FakeWorker(), "release")["cause"]["detail"].startswith("工程の入力にスプリントの PR")
    # material があって PR の番号が決まらなければ、どのゲートでもチェックアウト中のブランチから引かない
    w.hub.fetches = 0
    got = w.run(FakeWorker(), "design", material=tmp_path / "m.md")
    assert got["cause"]["kind"] == "precondition" and "--pr" in got["cause"]["detail"] and w.hub.fetches == 0


def test_a_reference_made_private_while_reading_is_not_posted(tmp_path: Path) -> None:
    """読み手役の実行中に参照先が非公開へ変わったら、投稿の直前の再照会で止め、コメントも記録も残さない。"""
    w = world(tmp_path)
    w.hub.body = BODY + "\n- 参考: https://github.com/acme/tool/issues/1\n"
    w.hub.visibility = {REPO: "PUBLIC", "acme/tool": "PUBLIC"}
    got = w.run(FakeWorker(edit=lambda state_dir: w.hub.visibility.update({"acme/tool": "PRIVATE"})))
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition", got
    assert w.hub.posted == [] and checks_rows() == []


# --- 変更要求（決定 10） ------------------------------------------------------------------------------------


def _title_proposal(state_dir: Path) -> None:
    (state_dir / gate.TITLE_FILE).write_text("通じる題\n", encoding="utf-8")


def _body_edit(state_dir: Path) -> None:
    path = state_dir / gate.MATERIAL_FILE
    path.write_text(path.read_text(encoding="utf-8").replace("- A にする", "- A にする（理由つき）"), encoding="utf-8")


def _add_only(state_dir: Path) -> None:
    path = state_dir / gate.MATERIAL_FILE
    path.write_text(path.read_text(encoding="utf-8") + "\n## 判断に使うもの\n\n- 目的は承認を速くすること\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("edit", "sent"), [(_title_proposal, True), (_body_edit, True), (_add_only, False)], ids=["title", "body", "added-lines"]
)
def test_sends_back_when_title_or_body_must_change(tmp_path: Path, edit: Any, sent: bool) -> None:
    """題の案・PR の本文の行の直しは、PR を書き換えずに変更要求で止める。行を足しただけならコメントで足して記録する。2 回目は変更要求しない。"""
    w = world(tmp_path)
    got = w.run(FakeWorker(edit=edit))
    if not sent:
        assert got["status"] == "ok" and "目的は承認を速くすること" in w.hub.posted[0]
        return
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition" and "変更要求" in got["cause"]["detail"]
    assert checks_rows() == [] and w.hub.posted == [] and [r["kind"] for r in reading.doc_rows()].count("sent_back") == 1
    w.hub.title, w.hub.body = "通じる題", BODY + "\n補足を足した。\n"
    worker = FakeWorker(failing(reading.MATERIAL, "A にする"))
    again = w.run(worker)
    assert again["status"] == "ok" and worker.calls == 1, again
    assert gate.TITLE_FILE not in worker.prompts[0] and "変更要求は 1 回まで" in worker.prompts[0]
    assert again["items"][0]["doc"] == reading.MATERIAL


# --- 残った文（条件 2） ---------------------------------------------------------------------------------------


def test_remained_quotes_are_sectioned_and_carried(tmp_path: Path) -> None:
    """2 回目にも残った文は items・承認資料のセクション・reads の remained になり、読ませた本文のハッシュはセクションを除く。
    次の確認で承認資料の外だけが変わっても引き継がれ、引用した文が無くなれば外れる。"""
    w = world(tmp_path)
    got = w.run(FakeWorker(failing(reading.MATERIAL, "A にする")))
    assert got["status"] == "ok" and got["items"][0]["text"].startswith("承認資料: A にする")
    final = Path(got["outputs"]["material"]["path"]).read_text(encoding="utf-8")
    assert reading.BEGIN in final and got["outputs"]["material"]["sha256"] == reading.sha256(final.split("\n\n" + reading.BEGIN)[0])
    assert {"name": reading.MATERIAL, "reader": reading.APPROVER, "round": 2, "result": "remained"} in checks_rows()[-1]["reads"]
    w.git = FakeGit(tmp_path, {DESIGN: "# 設計\n\n別の段落を直した。\n", PLAN: "# 計画\n"})
    w.hub.head = H1
    worker = FakeWorker()
    again = w.run(worker)
    assert again["status"] == "ok" and reading.MATERIAL not in {
        l.split("`")[1] for l in worker.prompts[0].splitlines() if l.startswith("- `") and "`: `" in l
    }
    assert again["items"][0]["doc"] == reading.MATERIAL
    w.hub.body = BODY.replace("- A にする", "- B にする")
    third = w.run(FakeWorker())
    assert third["status"] == "ok" and third["items"] == []


# --- --material の経路（条件 4・9） -------------------------------------------------------------------------


def _change_head_line(state_dir: Path) -> None:
    path = state_dir / gate.MATERIAL_FILE
    path.write_text(path.read_text(encoding="utf-8").replace(H1, H2), encoding="utf-8")


@pytest.mark.parametrize(
    ("line_head", "edit", "kind"),
    [(H1, None, None), (H2, None, "precondition"), (H1, _change_head_line, "incomplete")],
    ids=["ok", "stale", "changed-line"],
)
def test_release_material_route(tmp_path: Path, line_head: str, edit: Any, kind: Optional[str]) -> None:
    """`--material` の経路: そのファイルと質問を読ませ、PR の本文もコメントも触らない。対象のコミットの行が古い・変えられたら止める。"""
    w = world(tmp_path, {})
    material = tmp_path / "materials.md"
    material.write_text(f"# リリース\n\n提示したコミット: `{line_head}`\n\n- 中身\n", encoding="utf-8")
    worker = FakeWorker(edit=edit)
    # PR の番号は引数でなく工程の入力でも渡せ、直前の MVV 判定は素材のファイルに載る
    mvv = {"gate": "release", "skip": False, "verdict": None, "why": ["進め方 normal"]}
    got = w.run(worker, "release", material=material, inputs={"pr": 7, "mvv_gate": mvv})
    if kind is not None:
        assert got["status"] == "stopped" and got["cause"]["kind"] == kind
        return
    assert got["status"] == "ok" and w.hub.posted == [] and w.hub.fetches == 2
    assert json.loads((w.cwd / "state" / gate.FACTS_FILE).read_text(encoding="utf-8"))["mvv_gate"] == mvv
    questions = json.loads(Path(got["outputs"]["questions"]).read_text(encoding="utf-8"))["questions"]
    assert questions[0]["question"].endswith("アプリをストアの審査に提出してよいですか")
    assert reading.rows()[-1]["question_key"] == rd.question_key(questions)


@pytest.mark.parametrize(("edit", "status"), [(None, "ok"), (_touch_plan, "stopped")], ids=["left-as-is", "changed-by-worker"])
def test_a_change_left_before_the_worker_is_not_counted(tmp_path: Path, edit: Any, status: str) -> None:
    """メインディレクトリから打つリリースでは、起動の前からある未コミットの変更で止めない。worker がその中身を変えたら止める。"""
    w = world(tmp_path)
    (tmp_path / PLAN).write_text("# 起動の前からの変更\n", encoding="utf-8")
    material = tmp_path / "materials.md"
    material.write_text(f"# リリース\n\n提示したコミット: `{H1}`\n", encoding="utf-8")
    got = w.run(FakeWorker(edit=edit), "release", pr=7, material=material)
    assert got["status"] == status, got


# --- 通常ファイルだけを開く -----------------------------------------------------------------------------------


def _linked_file(root: Path) -> None:
    """文書を、同じ中身の作業場所の外のファイルを指すシンボリックリンクに替える。"""
    outside = root / "outside.md"
    (root / DESIGN).rename(outside)
    (root / DESIGN).symlink_to(outside)


def _linked_dir(root: Path) -> None:
    """途中のディレクトリをシンボリックリンクに替える。"""
    (root / "issues").rename(root / "moved")
    (root / "issues").symlink_to(root / "moved")


@pytest.mark.parametrize(
    ("mode", "link", "after_read"),
    [("120000", None, False), (None, _linked_file, False), (None, _linked_dir, False), (None, _linked_file, True)],
    ids=["git-symlink", "worktree-symlink", "symlinked-parent", "replaced-by-the-worker"],
)
def test_a_document_that_is_not_a_plain_file_is_not_opened(tmp_path: Path, mode: Optional[str], link: Any, after_read: bool) -> None:
    """Git 上で通常ファイルでない文書と、作業場所でシンボリックリンクを経由する文書は開かずに止める。"""
    w = world(tmp_path)
    if mode is not None:
        w.git.modes[DESIGN] = mode
    if link is not None and not after_read:
        link(tmp_path)
    worker = FakeWorker(edit=(lambda state_dir: link(state_dir.parent)) if after_read else None)
    got = w.run(worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition" and "通常ファイル" in got["cause"]["detail"], got
    assert worker.calls == (1 if after_read else 0) and checks_rows() == [] and w.hub.posted == []


# --- 設計のレビューへ戻す（条件 6、Task 5） -------------------------------------------------------------------


def _fix_doc(state_dir: Path) -> None:
    (state_dir.parent / DESIGN).write_text("# 設計\n\n読み手役の指摘で直した。\n", encoding="utf-8")


def _fixing(state_dir: Path, targets: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    out = passing(state_dir, targets)
    for rec in out:
        if rec["name"] == DESIGN:
            rec.update(
                verdict="pass",
                checks=[
                    {"verdict": "fail", "hash": "x", "restatements": ["要点"], "reason": "通じない"},
                    {"verdict": "pass", "hash": HASH(DESIGN, state_dir), "restatements": ["要点"]},
                ],
            )
    return out


def test_fixed_docs_go_back_to_the_design_review(tmp_path: Path) -> None:
    """文書を直したら、再レビュー待ちの行を書いてから push し、CI を待ち、reading-checks に書かずに review_again を返す。
    レビューの前に打ち直すと worker を起動せずに review_again を返し、--reviewed で解消してから読ませる。"""
    w = world(tmp_path)
    w.hub.on_fetch = lambda hub: setattr(hub, "head", w.git.head)
    got = w.run(FakeWorker(_fixing, edit=_fix_doc))
    assert got["status"] == "ok" and got["outputs"]["review_again"]["docs"] == [DESIGN], got
    assert cli.adopt(got)[0] == got
    commit = got["outputs"]["review_again"]["commit"]
    assert w.git.pushed == [commit] and w.ci == [7] and checks_rows() == [] and w.hub.posted == []
    kinds = [r["kind"] for r in reading.doc_rows()]
    assert kinds.index("review_pending") < kinds.index("done")
    w.hub.head = commit
    worker = FakeWorker()
    again = w.run(worker)
    assert again["outputs"]["review_again"]["commit"] == commit and worker.calls == 0
    resolved = w.run(worker, reviewed=commit)
    assert resolved["status"] == "ok" and worker.calls == 0 and "review_again" not in resolved["outputs"], resolved
    assert len(w.hub.posted) == 1 and checks_rows()[-1]["prs"][0]["head"] == commit


@pytest.mark.parametrize(("in_pr", "local", "push_code", "ci", "expect"), [
    (True, True, 0, "stopped", "check_failed"),
    (False, True, 0, "ok", "review_again"),
    (False, True, 1, "ok", "precondition"),
    (False, False, 0, "ok", "cancelled"),
], ids=["ci-failed", "repush", "repush-failed", "gone"])  # fmt: skip
def test_pending_review_rows(tmp_path: Path, in_pr: bool, local: bool, push_code: int, ci: str, expect: str) -> None:
    """再レビュー待ちの行: CI が落ちたままなら返さない。PR に届いていなければ push し直す。手元にも無ければ取り消して読ませる。"""
    w = world(tmp_path)
    pending = "9" * 40
    reading.append_doc({"kind": "review_pending", "repo": REPO, "check": "c0", "branch": BRANCH, "commit": pending, "docs": [DESIGN]})
    if in_pr:
        w.hub.head = w.git.head = pending
        w.git.trees[pending] = w.git.trees[H1]
    if local:
        w.git.local.add(pending)
    w.git.push_code, w.ci_status = push_code, ci
    worker = FakeWorker()
    got = w.run(worker)
    if expect == "cancelled":
        assert got["status"] == "ok" and worker.calls == 1 and reading.doc_rows()[1]["kind"] == "review_cancelled"
    elif expect == "review_again":
        assert got["outputs"]["review_again"]["commit"] == pending and w.git.pushed and worker.calls == 0
    else:
        assert got["cause"]["kind"] == expect and worker.calls == 0
        assert reading.pending_review(reading.doc_rows(), REPO, BRANCH) is not None


def test_no_doc_fix_keeps_docs_out_of_the_fixable_files(tmp_path: Path) -> None:
    """往復の上限: --no-doc-fix では文書を直してよいファイルに入れず、直すと決めたファイルの外として止める。"""
    w = world(tmp_path)
    worker = FakeWorker(_fixing, edit=_fix_doc)
    got = w.run(worker, no_doc_fix=True)
    assert got["cause"]["kind"] == "incomplete" and "文書は直さない" in worker.prompts[0]


# --- ndf records reading check の突き合わせ（Task 7、条件 6・7） ---------------------------------------------------


def _check(w: World, material: Optional[Path] = None, gate_name: str = "design") -> list[str]:
    before = w.hub.fetches
    missing, questions = reading.check(gate_name, 7, material, w.cwd, lambda repo: w.hub)  # type: ignore[arg-type,return-value]
    assert w.hub.fetches == before + 1 and rd.question_key(questions) == checks_rows()[-1]["question_key"]
    return missing


def _patched(w: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """check が作業場所の git とリリースの形の設定を偽物から読むようにする。"""
    monkeypatch.setattr(reading, "Git", lambda root: w.git)
    monkeypatch.setattr(reading, "release_form", lambda root: "mobile")
    return w


def _drop_last_done(w: World) -> None:
    path = reading.docs_path()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        (lambda w: None, []),
        (lambda w: setattr(w.hub, "body", BODY + "\n足した。\n"), ["material_changed"]),
        (lambda w: w.hub.comments.__setitem__(-1, w.hub.comments[-1].replace("A にする", "A")), ["material_changed"]),
        (lambda w: setattr(w.hub, "head", H2), [rd.HEAD_MOVED]),
        (_drop_last_done, [rd.NO_RECORD]),
    ],
    ids=["same", "body-edited", "comment-edited", "head-moved", "done-row-missing"],
)
def test_check_matches_the_gate_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any, missing: list[str]) -> None:
    """gate が記録した後に、check は同じキーの質問を 1 回の取得で作り直す。本文・コメントの変更は material_changed、完了の行が無ければ no_record。"""
    w = _patched(world(tmp_path), monkeypatch)
    assert w.run(FakeWorker())["status"] == "ok"
    change(w)
    assert _check(w) == missing


def test_check_on_the_material_route_and_a_renamed_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--material の経路は、渡したファイルの 2 つのハッシュと突き合わせる（残った文のセクションを消すと material_changed）。題が変われば no_record。"""
    w = _patched(world(tmp_path, {}), monkeypatch)
    material = tmp_path / "materials.md"
    material.write_text(f"# リリース\n\n提示したコミット: `{H1}`\n\n- 中身\n", encoding="utf-8")
    got = w.run(FakeWorker(failing(reading.MATERIAL, "中身")), "release", pr=7, material=material)
    final = Path(got["outputs"]["material"]["path"])
    assert _check(w, final, "release") == []
    trimmed = tmp_path / "trimmed.md"
    trimmed.write_text(final.read_text(encoding="utf-8").split("\n\n" + reading.BEGIN)[0], encoding="utf-8")
    assert _check(w, trimmed, "release") == ["material_changed"]
    w.hub.title = "別の題"
    missing, _ = reading.check("release", 7, final, w.cwd, lambda repo: w.hub)  # type: ignore[arg-type]
    assert missing == [rd.NO_RECORD]


def test_a_redacted_remained_quote_is_carried_without_keeping_the_original():
    """記録の引用は伏せた後の文と `redacted` を持ち、次の確認は固定の文の所をその種類の値の形として、空白の数の違いを除いて探す。"""
    original = {"quote": "連絡は  山田@example.co.jp へ送る", "why": "w"}
    hidden = f"連絡は  {publish.EMAIL_TEXT} へ送る"
    stored = gate_decide.marked_redacted({"quote": hidden, "why": "w"}, original)
    assert stored == {"quote": hidden, "why": "w", "redacted": True}
    prev = [{"name": "docs/a.md", "quotes": [stored]}]

    def carried(now: str, part: Optional[str] = "", raised: tuple[dict[str, str], ...] = ()) -> dict[str, Any]:
        parts = {} if part == "" else {"docs/a.md": part}
        return gate_decide.carried_quotes(prev, {"docs/a.md": now}, parts, {"docs/a.md": raised}, publish.redacted_pattern)

    first, other = "連絡は\n山田@example.co.jp へ送る", "連絡は b@example.co.jp へ送る"
    assert carried(f"前\n{first}\n後") == {"docs/a.md": [stored]}
    assert carried("書き直した") == {}
    assert carried(first, first) == {} and carried(first, None) == {}
    # 値だけが違う別の文を読ませて同じ理由が挙がっても、読ませていない文の指摘は引き継ぐ
    raised = ({"quote": other, "why": "w"},)
    assert carried(f"{first}\n\n{other}", other, raised) == {"docs/a.md": [stored]}
    assert carried(f"{first}\n\n{other}", f"{first}\n\n{other}", raised) == {}


@pytest.mark.parametrize(
    ("quote", "document"),
    [
        # 前の確認のアカウント名・秘密の値は、今の実行の環境からは分からない
        (f"担当は {publish.ACCOUNT_TEXT} が承認する", "担当は demoaccount が承認する"),
        (f"鍵は {run_log.MASK} を使う", "鍵は old-rotated-value を使う"),
        (f"Authorization: {run_log.MASK}", "Authorization: Bearer old rotated value"),
        (
            f"置き場は {publish.HOME_TEXT}/work と {publish.PRIVATE_REF_TEXT} の {publish.EMAIL_TEXT}",
            "置き場は /home/someone/work と acme/secret#3 の a@example.org",
        ),
        # 引用の外の文字（認証ヘッダー）まで伏せる範囲が広がる文書
        (f"承認先は {publish.EMAIL_TEXT} にする", "Authorization: Bearer demo # 承認先は a@example.org にする"),
    ],
)
def test_a_redacted_quote_is_found_without_masking_the_document_again(quote: str, document: str) -> None:
    stored = {"quote": quote, "why": "w", "redacted": True}
    prev = [{"name": "docs/a.md", "quotes": [stored]}]
    got = gate_decide.carried_quotes(prev, {"docs/a.md": document}, {}, {}, publish.redacted_pattern)
    assert got == {"docs/a.md": [stored]}


@pytest.mark.parametrize(
    ("quote", "document"),
    [
        (f"連絡先: {publish.EMAIL_TEXT}", "連絡先: 未定"),
        (f"担当は {publish.ACCOUNT_TEXT} が承認する", "担当は 未定 が承認する"),
        (f"鍵は {run_log.MASK} を使う", "鍵は 使わないことにした値 を使う"),
    ],
)
def test_a_redacted_quote_is_dropped_when_the_value_was_replaced_by_other_words(quote: str, document: str) -> None:
    prev = [{"name": "docs/a.md", "quotes": [{"quote": quote, "why": "w", "redacted": True}]}]
    assert gate_decide.carried_quotes(prev, {"docs/a.md": document}, {}, {}, publish.redacted_pattern) == {}
