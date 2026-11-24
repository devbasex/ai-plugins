"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017・#3959）。LLM のアダプターと GitHub はスタブにし、git は本物を使う。

#2213・#2704 の再現: どちらもレッドライン（#2213 は C2・C4、#2704 は C3・C4）に当たる設計で、判定の指示文の変更の後に
boundary 空の「従う」になって承認ゲートを省けた（#3017）。ここでは、判定の材料にレッドラインの一覧（共通原則の C1〜C8 と
プロジェクト MVV の P の行）が載り、判定がそれを boundary に挙げれば承認ゲートを省かないことを確かめる。
"""

from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import git_sandbox
import pytest
import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters import git, github, llm
from ndf.contracts import causes
from ndf.contracts.internal import InternalError
from ndf.domains.records import cli, jsonl, mvv, mvv_gate
from ndf.progress import command, definition, invoke, model, stage_inputs, store
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress.gate.checks import MvvFacts

from ._mvv_gate_records import HOST
from ._mvv_gate_records import failures as _failures

BODY = "# MVV\n\n## 必ず人の承認が要る操作\n\n| # | 操作 | 理由 |\n| --- | --- | --- |\n| P1 | リリース対象の取得元・バージョンの規則を変える | 利用者全員に効く |\n"
SHA = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
FOLLOW = {"verdict": "follow", "reasons": ["Value 1 に沿う"], "boundary": [], "basis": ["Value 1"]}
# #2213: 承認ゲートの証跡と、本番・マージへ進める認可の経路を変える設計
DESIGN_2213 = {
    "body": "承認ゲートの後と止まった後を、キューと待ちだけで流す設計",
    "files": [
        {"filename": "issues/issue-1426-design.md", "status": "added", "patch": "+承認ゲートの記録を書かずにマージへ進める経路を足す"}
    ],
    "verdict": {
        "verdict": "follow",
        "reasons": ["Value 3 に沿う"],
        "boundary": ["C2: マージへ進める認可の経路を変える", "C4: 証跡を消す"],
        "basis": ["C2", "C4"],
    },
}
# #2704: 耐久の記録を退避し 30 日で削除する設計
DESIGN_2704 = {
    "body": "耐久ワークフローや耐久の記録を失っても、計画のジャーナルから start 1 回で続ける設計",
    "files": [{"filename": "issues/issue-2647-design.md", "status": "added", "patch": "+耐久の記録を退避し、30 日で削除する"}],
    "verdict": {
        "verdict": "follow",
        "reasons": ["Value 6 に沿う"],
        "boundary": ["C3: 利用者の記録を移す", "C4: 30 日で削除する"],
        "basis": ["C3", "C4"],
    },
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """設計のブランチ design/x の作業場所。マージ先 main は別のリポジトリ（リモート origin）にあり、承認済みの MVV を持つ。"""
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    origin = tmp_path / "origin"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(origin))
    _commit(origin, {".ndf/mvv.md": BODY, ".ndf/mvv.json": json.dumps({"body": "mvv.md", "versions": [{"version": 1, "sha256": SHA}]})})
    root = tmp_path / "main"
    git_sandbox.sh(tmp_path, "clone", "-q", str(origin), str(root))
    git_sandbox.sh(root, "checkout", "-q", "-b", "design/x")
    return root


def _commit(where: Path, changes: dict[str, Optional[str]]) -> str:
    """`where` に変更（パス → 中身。None なら消す）をコミットし、その SHA を返す。"""
    for rel, text in changes.items():
        path = where / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git_sandbox.sh(where, "add", "-A")
    git_sandbox.sh(where, "commit", "-q", "-m", "変更")
    return git_sandbox.sh(where, "rev-parse", "HEAD", strip=True)


def _base(repo: Path, changes: dict[str, Optional[str]]) -> str:
    """マージ先（リモートの main）に変更をコミットする。"""
    return _commit(repo.parent / "origin", changes)


def _base_sha(repo: Path) -> str:
    return git_sandbox.sh(repo.parent / "origin", "rev-parse", "main", strip=True)


@pytest.fixture(autouse=True)
def notes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """PR のコメントへ書く判定の記録（GitHub へは書かず、番号と本文を残す）。"""
    posted: list[tuple[int, str]] = []

    def post(hub: Any, number: int, text: str) -> SimpleNamespace:
        posted.append((number, text))
        return SimpleNamespace(url=f"https://github.com/o/r/pull/{number}#issuecomment-{len(posted)}")

    monkeypatch.setattr(mvv_gate, "post_note", post)
    return posted


class Hub:
    """GitHub の偽物。設計のブランチ design/x の開いた PR と、その本文・マージ先 main・head と、比較の変えたファイルを返す。

    `case["reads"]` は 2 回目以降の `pull` の値（head・マージ先）か投げる例外、`case["compare"]` は比較の応答か投げる例外。
    """

    repo = "o/r"

    def __init__(self, number: int, case: dict[str, Any]) -> None:
        self.number, self.case = number, case
        self.paths: list[str] = []
        self.reads = 0

    def open_pulls_of_head(self, branch: str, enough: int) -> list[SimpleNamespace]:
        # 本物と同じく、開いた同じリポジトリの PR を `enough` 件まで返す
        opened = [SimpleNamespace(number=self.number, state="open", cross_repository=False)]
        pulls = list(self.case.get("pulls", opened)) if branch == "design/x" else []
        return [p for p in pulls if p.state == "open" and not p.cross_repository][:enough]

    def pull(self, number: int) -> SimpleNamespace:
        assert number == self.number
        self.reads += 1
        later = self.case.get("reads", [])
        moved = later[self.reads - 2] if self.reads >= 2 and len(later) >= self.reads - 1 else {}
        if isinstance(moved, Exception):
            raise moved
        head = moved.get("head", self.case.get("head", HEAD))
        return SimpleNamespace(body=moved.get("body", self.case["body"]), head_sha=head, base_ref=moved.get("base", "main"))

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> dict:
        self.paths.append(path)
        assert method == "GET" and path.startswith("/repos/o/r/compare/")
        got = self.case.get("compare", {"files": self.case["files"]})
        if isinstance(got, Exception):
            raise got
        return got


HEAD = "h" * 40


class Ask:
    """LLM のアダプターのスタブ。質問・材料・スキーマを残し、渡した回答を返す。

    `retry` なら、1 回目の回答が形に合わなかったものとして、打ち直しの前の確かめ `before_retry` を呼んでから回答する
    （`launches` は起動した回数。確かめが例外を投げれば打ち直さない）。
    """

    def __init__(self, answer: Any, ok: bool = True, retry: bool = False) -> None:
        self.answer, self.ok, self.retry = answer, ok, retry
        self.calls: list[tuple[str, str, dict]] = []
        self.launches = 0

    def __call__(self, question: str, material: str, schema: dict, timeout: float, before_retry: Any = None) -> llm.JudgeAnswer:
        self.calls.append((question, material, schema))
        self.launches += 1
        if self.retry and before_retry is not None:
            before_retry()
            self.launches += 1
        if not self.ok:
            return llm.JudgeAnswer(False, None, causes.cause("needs_human", "形が合わない"), 2, 0.2)
        assert not llm.check_answer(self.answer, schema)
        return llm.JudgeAnswer(True, self.answer, None, 1, 0.1)


def _inputs(**extra: Any) -> dict[str, Any]:
    return {"mode": "standard", "pace": "fast", "mvv_ref": SHA, **extra}


def _hub(number: int, case: dict[str, Any]):
    return lambda root, remote=None: Hub(number, case)


def _no_hub(root: Path, remote: Optional[str] = None) -> Hub:
    raise AssertionError("判定を打たないときは GitHub を読まない")


def _stopped(out: dict[str, Any]) -> str:
    return out["stopped_by"]["kind"] if "stopped_by" in out else ""


DOCS = {"body": "文書の誤字を直す", "files": [{"filename": "docs/a.md", "status": "modified", "patch": "-誤字\n+正字"}]}
NORMAL = "進め方 normal は承認ゲートを MVV 判定で省かない"


# --- #3959 Task 4: 進め方によらず判定を打ち、進め方と区分は省くかだけを決める -----------------------------------


@pytest.mark.parametrize("gate_name", ["design", "release"])
def test_normal_pace_judges_and_keeps_the_gate(repo: Path, gate_name: str, notes: list[tuple[int, str]]) -> None:
    # 受け入れ条件 1: normal でも判定が verdict に載り、skip は偽のまま、判定の記録が PR のコメントに載る
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, gate_name, _inputs(pace="normal", pr=5), _hub(5, DOCS), ask)
    assert len(ask.calls) == 1 and out["verdict"] == FOLLOW and out["skip"] is False
    assert out["why"][0] == NORMAL and out["head"] == HEAD and out["holds"] is None and "stopped_by" not in out
    assert (out["noted"], out["note_url"]) == (True, "https://github.com/o/r/pull/5#issuecomment-1")
    ((number, text),) = notes
    assert number == 5 and "Value 1 に沿う" in text and NORMAL in text
    # 判定した head をコメントに載せ、読む人が PR の今の HEAD と見比べられる
    assert f"判定したコミット（PR の head）: {HEAD}" in text


def test_fast_does_not_skip_while_the_holds_are_unchecked(repo: Path) -> None:
    # 保留を読まない判定（holds が null）は、進行の側（model.mvv_skip）と同じく省けると書かず、理由を why に載せる
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW))
    assert (out["skip"], out["why"], out["holds"]) == (False, [mm.HOLDS_UNCHECKED], None)
    assert not model.mvv_skip({"step": {"outputs": {model.MVV_KEY: out}}}, "step", "design")
    # 決定 3: リリース承認ゲートは fast でも省かず、#4105 の理由を why に載せる
    out = mvv_gate.gate(repo, "release", _inputs(pr=5), _hub(5, DOCS), Ask(FOLLOW))
    assert (out["skip"], out["why"], out["verdict"]) == (False, [mvv_gate.RELEASE_KEPT], FOLLOW)


@pytest.mark.parametrize(
    ("gate_name", "inputs", "case", "word"),
    [
        ("design", _inputs(mvv_ref="0" * 64), DOCS, "スプリントの状態の参照"),
        ("design", _inputs(mvv_ref=None), DOCS, "スプリントの状態に承認済み MVV の参照が無い"),
        ("release", _inputs(mode="operation", pr=5), DOCS, "モード operation は承認ゲートを省かない"),
        ("design", _inputs(), {"body": "設定", "files": [{"filename": ".ndf/mvv.json", "status": "modified", "patch": "+x"}]}, "C7"),
    ],
)
def test_machine_checks_after_the_first_do_not_stop_the_verdict(
    repo: Path, gate_name: str, inputs: dict[str, Any], case: dict[str, Any], word: str, notes: list[tuple[int, str]]
) -> None:
    # 受け入れ条件 2: 検査 2〜6 が外れても判定は打たれ、skip は偽で、外れた理由が why と PR のコメントに載る
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, gate_name, inputs, _hub(5, case), ask)
    assert len(ask.calls) == 1 and out["verdict"] == FOLLOW and out["skip"] is False
    assert any(word in w for w in out["why"]) and any(word in text for _, text in notes)


def test_the_boundary_paths_come_from_the_base_and_not_the_worktree(repo: Path, notes: list[tuple[int, str]]) -> None:
    # 検査 4: ベースの規則が効く。worktree の .ndf/pace.json からパスを消しても外れる
    _base(repo, {".ndf/pace.json": json.dumps({"version": 1, "boundary_paths": ["docs/**"]})})
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "boundary_paths": []}), encoding="utf-8")
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert len(ask.calls) == 1 and out["skip"] is False and out["why"] == ["レッドラインのパスに当たる: docs/a.md"]


def test_a_broken_boundary_setting_on_the_base_is_a_base_failure(repo: Path, notes: list[tuple[int, str]]) -> None:
    _base(repo, {".ndf/pace.json": "{壊れた"})
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert (out["verdict"], _stopped(out), out["head"], ask.calls) == (None, "base_unreadable", HEAD, [])
    assert out["why"][0].startswith("ベースの MVV を読めない: " + mm.STOP_REASONS["base_unreadable"]) and len(notes) == 1


@pytest.mark.parametrize("change", ["unapproved", "mismatch"])
def test_without_an_approved_mvv_on_the_base_no_verdict_and_no_note(repo: Path, change: str, notes: list[tuple[int, str]]) -> None:
    # 検査 1 だけが判定を止める。PR へも書かない（承認済みの MVV が無いリポジトリで今と同じ）
    if change == "unapproved":
        _base(repo, {".ndf/mvv.json": json.dumps({"body": "mvv.md", "versions": []})})
    else:
        _base(repo, {".ndf/mvv.md": BODY + "承認の後に足した行\n"})
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert (out["verdict"], out["skip"], out["noted"], _stopped(out), out["head"]) == (None, False, False, "mvv_unapproved", HEAD)
    assert "note_url" not in out and ask.calls == [] and notes == []


def test_the_worktree_mvv_is_not_read(repo: Path) -> None:
    # PR が MVV の設定を消しても、ベースが承認済みなら判定を打ち、検査 5 が外れる
    git_sandbox.sh(repo, "rm", "-q", ".ndf/mvv.json")
    case = {"body": "設定を消す", "files": [{"filename": ".ndf/mvv.json", "status": "removed", "patch": "-x"}]}
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), ask)
    assert len(ask.calls) == 1 and out["skip"] is False and any("C7" in w for w in out["why"])


def test_the_base_is_fetched_once_and_the_diff_is_read_from_the_base_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[tuple[str, ...]] = []
    real = git.Git.fetch

    def fetch(self: git.Git, remote: str, *refspecs: str) -> git.Completed:
        fetched.append((remote, *refspecs))
        return real(self, remote, *refspecs)

    monkeypatch.setattr(git.Git, "fetch", fetch)
    base = _base_sha(repo)
    hub = Hub(5, DOCS)

    def advance_then_answer(method: str, path: str, payload: Any = None, params: Any = None) -> dict:
        # 比較を返す前にマージ先が進んでも、比較の基点は判定の MVV を読んだコミットのまま
        hub.paths.append(path)
        _base(repo, {"docs/later.md": "後"})
        return {"files": DOCS["files"]}

    monkeypatch.setattr(hub, "rest", advance_then_answer)
    out = mvv_gate.gate(repo, "design", _inputs(), lambda root, remote: hub, Ask(FOLLOW))
    assert fetched == [("origin", "+refs/heads/main:refs/remotes/origin/main")]
    assert hub.paths == [f"/repos/o/r/compare/{base}...{HEAD}"] and out["verdict"] == FOLLOW


MOVES = [
    ({"head": "b" * 40}, "pr_moved"),
    ({"base": "develop"}, "pr_moved"),
    ({"body": "書き換えた本文"}, "pr_moved"),
    (github.GitHubError("読めない", 502), "pr_reread_failed"),
]


@pytest.fixture
def no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """PR の読み直しの分岐だけを確かめる判定の場所。ベースと差分の取得・リモート名・記録のキーをスタブにし、Git を起動しない。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    monkeypatch.setattr(git.Git, "remote", lambda self: "origin")
    monkeypatch.setattr(mvv, "repo_key", lambda root: "o__r")
    monkeypatch.setattr(mvv_gate, "pr_number", lambda root, gate, inputs, hub: 5)
    facts = MvvFacts(md.State.APPROVED, SHA, SHA, SHA, "standard")
    monkeypatch.setattr(mvv_gate, "_base_snapshot", lambda j, remote, inputs: (SimpleNamespace(commit="b" * 40), facts, BODY))
    monkeypatch.setattr(mvv_gate, "_diff", lambda j, remote, snap: (DOCS["files"], None))
    return tmp_path


def test_an_unchanged_pr_is_read_once_more_only_before_the_note(no_git: Path, notes: list[tuple[int, str]]) -> None:
    # LLM の 1 回目の起動の前には読み直さない。読み直しはコメントを書く直前の 1 回だけ
    hub, ask = Hub(5, DOCS), Ask(FOLLOW)
    out = mvv_gate.gate(no_git, "design", _inputs(), lambda root, remote: hub, ask)
    assert (hub.reads, ask.launches, out["verdict"], [n for n, _ in notes]) == (2, 1, FOLLOW, [5])


def test_an_unchanged_pr_is_read_again_before_the_retry(no_git: Path, notes: list[tuple[int, str]]) -> None:
    # 打ち直してから投稿する場合は、打ち直しの直前の 1 回を含めて計 3 回読む
    hub, ask = Hub(5, DOCS), Ask(FOLLOW, retry=True)
    out = mvv_gate.gate(no_git, "design", _inputs(), lambda root, remote: hub, ask)
    assert (hub.reads, ask.launches, out["verdict"], [n for n, _ in notes]) == (3, 2, FOLLOW, [5])


@pytest.mark.parametrize(("change", "kind"), MOVES)
def test_a_pr_that_moved_before_the_retry_does_not_relaunch(no_git: Path, change: Any, kind: str, notes: list[tuple[int, str]]) -> None:
    # 1 回目の回答が形に合わず打ち直す直前の読み直しで、head・マージ先・本文が変わった・読めない → 打ち直さず、コメントも書かない
    ask = Ask(FOLLOW, retry=True)
    out = mvv_gate.gate(no_git, "design", _inputs(), _hub(5, {**DOCS, "reads": [change]}), ask)
    assert (out["verdict"], out["skip"], out["noted"], _stopped(out), out["head"]) == (None, False, False, kind, HEAD)
    assert ask.launches == 1 and notes == [] and mm.STOP_REASONS[kind] in out["why"][-1]


@pytest.mark.parametrize(("change", "kind"), MOVES)
def test_a_pr_that_moves_while_the_llm_answers_is_not_noted(no_git: Path, change: Any, kind: str, notes: list[tuple[int, str]]) -> None:
    # コメントを書く直前の読み直しで変わった → 判定を捨ててコメントを書かない
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(no_git, "design", _inputs(), _hub(5, {**DOCS, "reads": [change]}), ask)
    assert (out["verdict"], out["skip"], out["noted"], _stopped(out), out["head"]) == (None, False, False, kind, HEAD)
    assert ask.launches == 1 and notes == [] and mm.STOP_REASONS[kind] in out["why"][-1]


@pytest.mark.parametrize("failure", [github.GitHubError("比較を読めない", 502), {"no_files": True}])
def test_a_diff_that_cannot_be_read_is_noted_without_a_verdict(repo: Path, failure: Any, notes: list[tuple[int, str]]) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, {**DOCS, "compare": failure}), ask)
    assert (out["verdict"], _stopped(out), out["head"], ask.calls, len(notes)) == (None, "diff_unreadable", HEAD, [], 1)
    assert out["why"][0].startswith("変えたファイルを読めない: ")


def test_a_verdict_the_llm_does_not_give_keeps_the_head_and_notes(repo: Path, notes: list[tuple[int, str]]) -> None:
    out = mvv_gate.gate(repo, "design", _inputs(mvv_ref=None), _hub(5, DOCS), Ask(None, ok=False))
    assert (out["verdict"], out["skip"], _stopped(out), out["head"], out["holds"]) == (None, False, "llm_no_answer", HEAD, None)
    assert out["why"][0].startswith("MVV 判定を打てない: " + mm.STOP_REASONS["llm_no_answer"])
    assert out["why"][1] == "スプリントの状態に承認済み MVV の参照が無い" and len(notes) == 1
    assert "形が合わない" not in json.dumps(out, ensure_ascii=False)


def test_a_verdict_that_cannot_be_recorded_is_kept_but_does_not_skip(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 記録は読めるが判定の行の追記だけが OSError: verdict は保ち、skip は偽で、may_skip の結果で上書きしない
    real = jsonl.append_locked

    def append(path: Path, row: Any, *, kind: str) -> dict:
        if kind == "mvv-validation":
            raise OSError(f"/home/someone/{HOST} に書けない")
        return real(path, row, kind=kind)

    monkeypatch.setattr(jsonl, "append_locked", append)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW))
    assert (out["verdict"], out["skip"]) == (FOLLOW, False) and len(out["why"]) == 1
    assert out["why"][0].startswith("判定を記録（mvv-validation.jsonl）に書けない: " + mm.STOP_REASONS["records_unwritable"])
    assert HOST not in out["why"][0]


def test_an_unreadable_record_judges_again_without_reuse(repo: Path) -> None:
    ask = Ask(FOLLOW)
    first = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    path = mvv.record_path("mvv-validation")
    with path.open("ab") as f:
        f.write(b'{"\xff\xfe"}\n')
    again = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert len(ask.calls) == 2 and again["verdict"] == first["verdict"]


def test_broken_rows_of_the_record_are_warned_once(repo: Path) -> None:
    path = mvv.record_path("mvv-validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れた\n", encoding="utf-8")
    warnings: list[dict[str, Any]] = []
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW), warnings)
    assert out["verdict"] == FOLLOW and warnings == [{"kind": "broken_rows", "path": str(path), "lines": [1]}]


def test_a_note_that_cannot_be_posted_keeps_the_verdict_and_does_not_skip(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(hub: Any, number: int, text: str) -> None:
        raise github.GitHubError(f"https://x-access-token:zz-fake@{HOST}/o/r に書けない", 403)

    monkeypatch.setattr(mvv_gate, "post_note", fail)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW))
    assert (out["verdict"], out["skip"], out["noted"], out["head"]) == (FOLLOW, False, False, HEAD) and "note_url" not in out
    assert out["why"][-1].startswith("判定の記録を PR に書けない: " + mm.STOP_REASONS["note_unwritten"])
    assert HOST not in json.dumps(out, ensure_ascii=False)


def test_the_cause_of_a_failure_stays_in_the_local_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch, notes: list[tuple[int, str]]
) -> None:
    # 例外の文（社内のホスト名・ホームのパス・秘密・別のリポジトリ）は出力にも PR のコメントにも入らず、診断の記録にだけ残る
    monkeypatch.setenv("NDF_TEST_TOKEN", "zz-fake-secret-88")
    cause = f"fetch https://x-access-token:zz-fake-secret-88@github.com/other-owner/other-repo.git と {HOST} と /home/someone/x に失敗した"

    def fail(self: git.Git, remote: str, name: str) -> None:
        raise git.GitError(cause)

    monkeypatch.setattr(git.Git, "fetch_branch_or_raise", fail)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW))
    shown = json.dumps(out, ensure_ascii=False) + "".join(text for _, text in notes)
    for leak in (HOST, "/home/someone", "zz-fake-secret-88", "other-owner", "other-repo"):
        assert leak not in shown
    (row,) = _failures()
    assert out["why"] == [f"ベースの MVV を読めない: {mm.STOP_REASONS['base_unreadable']}（診断の記録 {row['id']}）"]
    assert HOST in row["cause"] and "zz-fake-secret-88" not in row["cause"] and row["head"] == HEAD and row["pr"] == 5


@pytest.mark.parametrize("mode", ["light", "operation"])
def test_a_mode_without_the_design_gate_is_not_judged(repo: Path, mode: str) -> None:
    # 設計承認ゲートを持たないモードでも判定のステップは流れ、出力をそろえるが判定は打たない
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(mode=mode), _no_hub, ask)
    assert out == mm.stop_outcome("design", "no_gate", [f"モード {mode} は承認ゲート design を持たない"])
    assert ask.calls == [] and out["holds"] is None and out["noted"] is False
    assert mvv_gate.has_gate(mode, "release") and mvv_gate.has_gate("standard", "design")


def test_follow_without_redlines_is_noted_and_the_record_is_reused(repo: Path, notes: list[tuple[int, str]]) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert (out["skip"], out["why"], out["verdict"]["boundary"], out["head"]) == (False, [mm.HOLDS_UNCHECKED], [], HEAD)
    # 省かない理由（保留を確かめていない）を、判定の理由と一緒に PR のコメントへ残す
    ((number, text),) = notes
    assert number == 5 and "## MVV 判定" in text and "Value 1 に沿う" in text and mm.HOLDS_UNCHECKED in text
    # 同じ材料の「従う」は記録を再利用し、理由とレッドラインも引き継ぐ
    again = mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), ask)
    assert {**again, "note_url": ""} == {**out, "note_url": ""} and len(ask.calls) == 1
    rows, _ = jsonl.read_rows(mvv.record_path("mvv-validation"), kind="mvv-validation")
    assert [r["boundary"] for r in rows] == [[], []] and "reused_from" in rows[1]


@pytest.mark.parametrize(("number", "gate", "case"), [(2213, "design", DESIGN_2213), (2704, "release", DESIGN_2704)])
def test_redlines_in_the_verdict_keep_the_gate_2213_2704(
    repo: Path, number: int, gate: str, case: dict[str, Any], notes: list[tuple[int, str]]
) -> None:
    ask = Ask(case["verdict"])
    inputs = _inputs(pr=number) if gate == "release" else _inputs()
    out = mvv_gate.gate(repo, gate, inputs, _hub(number, case), ask)
    redline = "レッドラインに当たる: " + " / ".join(case["verdict"]["boundary"])
    assert out["skip"] is False and out["why"][0] == redline
    # 省かなかった判定も、レッドラインと省かない理由を PR のコメントへ残す
    ((posted, text),) = notes
    assert posted == number and case["verdict"]["boundary"][0] in text and "承認ゲートを省かない理由" in text
    ((question, material, schema),) = ask.calls
    # 判定の材料はレッドラインの一覧（共通原則の C1〜C8 とプロジェクト MVV の P1）を先に置き、PR の本文と差分を載せる
    assert question == mm.SYSTEM
    redlines = [f"- C{i}:" for i in range(1, 9)] + ["- P1: リリース対象の取得元・バージョンの規則を変える"]
    assert all(r in material for r in redlines) and material.index("- P1:") < material.index("## NDF の共通原則")
    assert case["body"] in material and case["files"][0]["patch"] in material
    assert schema == contracts.load_json(contracts.schema_path(mvv_gate.VERDICT_SCHEMA))


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": "src/a.py", "status": "modified", "patch": "\n".join(f"+行 {i}" for i in range(mm.PATCH_LINES + 1))}],
        [{"filename": "src/a.py", "status": "modified", "changes": 3}],
    ],
)
def test_a_diff_that_cannot_be_shown_whole_keeps_the_gate_without_judging(
    repo: Path, files: list[dict[str, Any]], notes: list[tuple[int, str]]
) -> None:
    # 差分を省いた部分（認可やデータの削除があり得る）は判定できないため、LLM を呼ばずに人へ問う
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, {"body": "変更", "files": files}), ask)
    assert out["skip"] is False and out["why"] == ["差分の全体を材料に載せられない: src/a.py"] and ask.calls == []
    assert (_stopped(out), [n for n, _ in notes]) == ("incomplete_patches", [5])
    # 中身を変えない改名は patch を持たないが、省いた部分が無い
    assert mm.incomplete_patches([{"filename": "b.py", "status": "renamed", "changes": 0}]) == []


def test_a_record_of_a_newer_version_stops_instead_of_asking_a_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def newer(*args: Any) -> dict:
        raise InternalError("precondition", "新しいバージョンの記録")

    monkeypatch.setattr(mvv_gate, "_judged", newer)
    with pytest.raises(InternalError):
        mvv_gate.gate(tmp_path, "design", _inputs(), _no_hub, Ask(FOLLOW))


def _raising(exc: Exception) -> Any:
    def open_hub(root: Path, remote: str) -> Hub:
        raise exc

    return open_hub


@pytest.mark.parametrize(
    "exc",
    [mvv_gate.GateError("理由"), git.GitError("理由"), github.GitHubError("理由", 500), identity.IdentityError("理由"), OSError("理由")],
)
def test_a_failure_before_reading_the_pr_is_the_whole_outcome_without_the_head(
    repo: Path, exc: Exception, notes: list[tuple[int, str]]
) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "release", _inputs(pr=5), _raising(exc), ask)
    (row,) = _failures()
    why = [f"MVV 判定を打てない: {mm.STOP_REASONS['no_pr']}（診断の記録 {row['id']}）"]
    assert out == mm.stop_outcome("release", "no_pr", why) and "head" not in out
    assert ask.calls == [] and notes == [] and row["cause"] == "理由"


def test_an_unlisted_failure_is_not_turned_into_an_outcome(repo: Path) -> None:
    with pytest.raises(KeyError):
        mvv_gate.gate(repo, "design", _inputs(), _raising(KeyError("x")), Ask(FOLLOW))


def test_the_mode_without_the_gate_is_told_first(tmp_path: Path) -> None:
    out = mvv_gate.gate(tmp_path, "design", {"pace": "normal"}, _no_hub, Ask(FOLLOW))
    assert (out["verdict"], out["why"], _stopped(out)) == (None, ["モード None は承認ゲート design を持たない"], "no_gate")


def test_the_release_gate_needs_the_pr_in_the_context(repo: Path) -> None:
    out = mvv_gate.gate(repo, "release", _inputs(), lambda root, remote: SimpleNamespace(), Ask(FOLLOW))
    assert out["skip"] is False and _stopped(out) == "no_pr"
    assert "pr" in _failures()[0]["cause"]


def test_cli_writes_the_outcome_as_the_step_output(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_hub", _hub(5, DOCS))
    monkeypatch.setattr(mvv_gate, "gate", partial(mvv_gate.gate, ask=Ask(FOLLOW)))
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_plan_inputs(repo, pace="normal")))
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["outputs"]["mvv_gate"]["skip"] is False
    assert out["outputs"]["mvv_gate"]["verdict"]["verdict"] == "follow"
    monkeypatch.delenv("NDF_STAGE_INPUT")
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) != 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"


def test_the_outcome_fits_the_context_schema(repo: Path) -> None:
    # 文脈のスキーマ（$defs.mvv_gate）が判定・止まった判定・判定しない出力のどれも受ける
    outs = [
        mvv_gate.gate(repo, "design", _inputs(), _hub(5, DOCS), Ask(FOLLOW)),
        mvv_gate.gate(repo, "design", _inputs(), _hub(5, {**DOCS, "reads": [{"head": "b" * 40}]}), Ask(FOLLOW)),
        mvv_gate.gate(repo, "design", _inputs(mode="light"), _no_hub, Ask(FOLLOW)),
    ]
    for out in outs:
        assert contracts.schema_errors({"mvv_gate": out}, model.CONTEXT) == []


# --- 手元の git の差分を、GitHub の比較の files の行の形にする（diff_rows・unquote_path） ---


def test_diff_rows_reads_added_removed_modified_and_renamed_files() -> None:
    text = (
        "diff --git a/new b/new\nnew file mode 100644\nindex 0000000..8ba3a16\n--- /dev/null\n+++ b/new\n@@ -0,0 +1 @@\n+n\n"
        "diff --git a/keep b/keep\ndeleted file mode 100644\nindex b68fde2..0000000\n--- a/keep\n+++ /dev/null\n@@ -1 +0,0 @@\n-k\n"
        "diff --git a/m b/m\nindex 1..2 100644\n--- a/m\n+++ b/m\n@@ -1 +1,2 @@\n q\n+r\n"
        "diff --git a/a/f b/b/g\nsimilarity index 100%\nrename from a/f\nrename to b/g\n"
        "diff --git a/bin b/bin\nindex bdc955b..8835708 100644\nBinary files a/bin and b/bin differ\n"
        "diff --git a/s b/s\nindex 1111111..2222222 160000\n--- a/s\n+++ b/s\n@@ -1 +1 @@\n-Subproject commit 1111111\n"
        "+Subproject commit 2222222\n"
    )
    rows = mm.diff_rows(text)
    assert rows == [
        {"filename": "new", "status": "added", "changes": 1, "patch": "@@ -0,0 +1,1 @@\n+n"},
        {"filename": "keep", "status": "removed", "changes": 1, "patch": "@@ -1,1 +0,0 @@\n-k"},
        {"filename": "m", "status": "modified", "changes": 1, "patch": "@@ -1,1 +1,2 @@\n q\n+r"},
        {"filename": "b/g", "previous_filename": "a/f", "status": "renamed", "changes": 0},
        {"filename": "bin", "status": "modified", "changes": 1},
        {
            "filename": "s",
            "status": "modified",
            "changes": 2,
            "patch": "@@ -1,1 +1,1 @@\n-Subproject commit 1111111\n+Subproject commit 2222222",
        },
    ]
    # 中身を変えない改名は数えず、バイナリは差分の全体が載らないものとして数える
    assert mm.incomplete_patches(rows) == ["差分の全体を材料に載せられない: bin"]


def test_diff_rows_keeps_mode_changes_in_the_patch() -> None:
    # 実行権限の変更はハンクの外にあるので、patch の先頭へ写す。モードだけの変更も同じ形にする
    text = (
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/do.sh b/do.sh\nold mode 100644\nnew mode 100755\nindex 1..2\n--- a/do.sh\n+++ b/do.sh\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert mm.diff_rows(text) == [
        {"filename": "run.sh", "status": "modified", "changes": 0, "patch": "old mode 100644\nnew mode 100755"},
        {"filename": "do.sh", "status": "modified", "changes": 2, "patch": "old mode 100644\nnew mode 100755\n@@ -1,1 +1,1 @@\n-a\n+b"},
    ]


def test_diff_rows_restores_quoted_paths() -> None:
    text = (
        'diff --git "a/\\346\\227\\245.md" "b/\\346\\227\\245.md"\nindex 1..2 100644\n'
        '--- "a/\\346\\227\\245.md"\n+++ "b/\\346\\227\\245.md"\n@@ -1 +1 @@\n-x\n+y\n'
        'diff --git "a/\\346\\227\\245" "b/q\\"t\\\\\\t"\nsimilarity index 100%\n'
        'rename from "\\346\\227\\245"\nrename to "q\\"t\\\\\\t"\n'
    )
    rows = mm.diff_rows(text)
    assert [(r["filename"], r.get("previous_filename")) for r in rows] == [("日.md", None), ('q"t\\\t', "日")]
    assert mm.unquote_path("plain/path") == "plain/path"
    with pytest.raises(mm.DiffError):
        mm.unquote_path('"\\377"')
    with pytest.raises(mm.DiffError):
        mm.diff_rows("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n-x\n")


# --- #3959 単位 1: 今の判定の振る舞いを縛る（Task 1）・工程の入力の形（Task 2）・プランの外のエントリポイント（Task 3） ---


def _plan_inputs(repo: Path, **context: Any) -> dict[str, Any]:
    """プランが NDF_STAGE_INPUT に入れる入力（文脈から `resolve_inputs` で組んだ平らな object）。"""
    full = {"issues": [3959], "worktree": str(repo), "mode": "standard", "pace": "fast", "mvv_ref": SHA, "design_pr": 9, **context}
    return invoke.resolve_inputs(definition.load_definition(str(full["mode"])).stage("設計"), full)


def test_the_flat_plan_input_keeps_todays_outcome(repo: Path, notes: list[tuple[int, str]]) -> None:
    # Task 1: プランが組む平らな入力で、今の形の mvv_gate を返す（fast で検査を通り「従う」でも、保留を読むまでは省かない）
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo), _hub(5, DOCS), Ask(FOLLOW))
    expected = ("design", False, [mm.HOLDS_UNCHECKED], "follow", HEAD)
    assert (out["gate"], out["skip"], out["why"], out["verdict"]["verdict"], out["head"]) == expected
    assert [n for n, _ in notes] == [5]


def test_a_repository_without_an_approved_mvv_keeps_a_null_verdict(repo: Path, notes: list[tuple[int, str]]) -> None:
    # Task 1・前提 2: 承認済みの MVV が無いリポジトリでは verdict: null のまま人へ質問し、PR へも書かない
    _base(repo, {".ndf/mvv.json": None, ".ndf/mvv.md": None})
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo, mvv_ref=None), _hub(5, DOCS), ask)
    assert out["skip"] is False and out["verdict"] is None and ask.calls == [] and notes == []


def test_the_gate_steps_are_steps_of_the_named_stage_documents() -> None:
    for gate_name, step_id in mvv_gate.GATE_STEPS.items():
        doc = contracts.load_json(f"workflow/stages/{mvv_gate.GATE_STAGES[gate_name]}.json")
        assert step_id in [name for task in doc["do"] for name in task]


def test_env_inputs_returns_the_plan_input_unchanged(repo: Path) -> None:
    given = _plan_inputs(repo)
    assert cli._env_inputs("design", given) == given


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"unknown": 1}, "invalid"),
        ({"design_pr": 4}, "invalid"),
        ({"pace": "slow"}, "invalid"),
        ({"mode": None}, "input_missing"),
    ],
)
def test_env_inputs_refuses_inputs_of_another_shape(repo: Path, change: dict[str, Any], kind: str) -> None:
    given = {**_plan_inputs(repo), **change}
    if change.get("mode", "") is None:
        given.pop("mode")
    with pytest.raises(cli._InputError) as caught:
        cli._env_inputs("design", given)
    assert caught.value.kind == kind


@pytest.mark.parametrize("given", [{"context": {"issues": [3959], "mode": "standard", "pace": "normal"}}, [1, 2]])
def test_env_inputs_refuses_a_nested_context_and_a_non_object_as_invalid(given: Any) -> None:
    # {"context": {...}} は最上位に mode が無く、規約の誤りと足りないキーの両方に当たるが、規約の誤りが先で invalid
    with pytest.raises(cli._InputError) as caught:
        cli._env_inputs("design", given)
    assert caught.value.kind == "invalid"


def _cli_gate(repo: Path, capsys: pytest.CaptureFixture[str], gate_name: str, *extra: str) -> dict[str, Any]:
    cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", gate_name, *extra])
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("gate_name", ["design", "release"])
def test_the_cli_takes_the_input_from_exactly_one_source(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], gate_name: str
) -> None:
    # 受け入れ条件 8・9: 環境変数だけ・--machine だけ・両方・どちらも無しの 4 行を、区分ごとに確かめる
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        cli.mvv_gate,
        "gate",
        lambda root, g, inputs, hub, warnings: seen.append((g, dict(inputs))) or mm.gate_outcome(g, None, False, ["x"]),
    )
    from_machine = (
        {"pr": 5, "mode": "standard", "pace": "normal", "mvv_ref": SHA} if gate_name == "release" else _plan_inputs(repo, pace="normal")
    )
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", lambda root, mid, step: asked.append((mid, step)) or from_machine)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    # どちらも無し
    none = _cli_gate(repo, capsys, gate_name)
    assert (none["status"], none["cause"]["kind"]) == ("stopped", "invalid") and "--machine" in none["summary"]
    # --machine だけ
    assert _cli_gate(repo, capsys, gate_name, "--machine", "sprint:x")["status"] == "ok"
    assert asked == [("sprint:x", mvv_gate.GATE_STEPS[gate_name])] and seen[-1] == (gate_name, from_machine)
    # 環境変数だけ
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(from_machine))
    assert _cli_gate(repo, capsys, gate_name)["status"] == "ok" and seen[-1] == (gate_name, from_machine)
    # 両方
    both = _cli_gate(repo, capsys, gate_name, "--machine", "sprint:x")
    assert (both["status"], both["cause"]["kind"]) == ("stopped", "invalid") and len(seen) == 2


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (lambda: model.Refused("ステートマシン sprint:x は無い"), "refused"),
        (lambda: invoke.StageInputMissing(["pr"]), "input_missing"),
        (lambda: invoke.StageInputInvalid(["pace: 形が違う"]), "invalid"),
    ],
)
def test_the_cli_stops_without_judging_when_the_machine_gives_no_input(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: Any, kind: str
) -> None:
    def fail(*_a: Any) -> dict[str, Any]:
        raise exc()

    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", fail)
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("入力を組めないのに判定を打った"))
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    out = _cli_gate(repo, capsys, "release", "--machine", "sprint:x")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", kind)


def test_a_db_error_while_reading_the_machine_shows_only_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 共有 DB への照会の失敗は、SQL 文・バインド値・接続先を結果へ載せず、例外の種類だけを出す（ndf progress と同じ）。
    # 故障は stage_inputs_of で発生させるので、リポジトリは Git で作らず、CLI のリポジトリの解決も差し替える
    def fail(*_a: Any) -> dict[str, Any]:
        raise sa.exc.ProgrammingError("SELECT * FROM ndf_progress_machines WHERE id = ?", ("secret-bind",), Exception("db.internal"))

    monkeypatch.setattr(cli, "_root", lambda args: tmp_path)
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", fail)
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("入力を組めないのに判定を打った"))
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    out = _cli_gate(tmp_path, capsys, "release", "--machine", "sprint:x")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", store.StoreUnreachable.kind)
    shown = json.dumps(out, ensure_ascii=False)
    assert "SELECT" not in shown and "secret-bind" not in shown and "db.internal" not in shown


def test_the_cli_refuses_a_nested_context_in_the_environment_without_judging(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 受け入れ条件 9 の例（11.0.22 で status: ok・判定なしを返した入力）
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("形の違う入力で判定を打った"))
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps({"context": {"pace": "normal", "mode": "standard", "issues": [3959]}}))
    out = _cli_gate(repo, capsys, "design")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "invalid")
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps({k: v for k, v in _plan_inputs(repo).items() if k != "mode"}))
    out = _cli_gate(repo, capsys, "design")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "input_missing")


def test_both_entrypoints_judge_the_same_pr_with_the_same_input(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], notes: list[tuple[int, str]]
) -> None:
    # 受け入れ条件 8: 同じステートマシンの文脈で、NDF_STAGE_INPUT と --machine が同じ入力・同じ PR で同じ mvv_gate を返す
    monkeypatch.chdir(repo)
    assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
    tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
    out_file = repo.parent / "outputs.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "用意した", "outputs": {"worktree": str(repo), "branch": "design/x"}}))
    assert command.main(["finish", tid, "作業場所の用意", "--output", str(out_file), "--reason", "手で"]) == 0
    # 文脈の design_pr は判定する PR と違っても読まない（両方とも worktree のブランチの PR を判定する）
    out_file.write_text(
        json.dumps({"steps": [], "summary": "設計", "outputs": {"design_pr": 99, "mvv_gate": mm.gate_outcome("design", None, False, [])}})
    )
    assert command.main(["finish", tid, "設計", "--output", str(out_file), "--reason", "手で"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "_hub", _hub(5, DOCS))
    seen: list[dict[str, Any]] = []
    real = mvv_gate.gate

    def judged(root: Path, gate_name: str, inputs: Any, hub: Any, warnings: Any) -> dict[str, Any]:
        seen.append(dict(inputs))
        return real(root, gate_name, inputs, hub, Ask(FOLLOW), warnings)

    monkeypatch.setattr(cli.mvv_gate, "gate", judged)
    by_machine = _cli_gate(repo, capsys, "design", "--machine", tid)
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(seen[0]))
    by_env = _cli_gate(repo, capsys, "design")
    assert seen[0] == seen[1] and "design_pr" not in seen[0]
    # 今回のコメントの URL だけが違う（どちらの起動もコメントを 1 件書く）
    same = [{**out["outputs"]["mvv_gate"], "note_url": None} for out in (by_machine, by_env)]
    assert same[0] == same[1] and by_env["outputs"]["mvv_gate"]["verdict"]["verdict"] == "follow"
    assert [n for n, _ in notes] == [5, 5]


def test_the_design_pr_is_the_open_pr_of_the_worktree_branch_in_this_repository(repo: Path, tmp_path: Path) -> None:
    # 作業場所が別のリポジトリなら、そのブランチ名で PR を探さない
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "design/x", str(other))
    git_sandbox.sh(other, "commit", "-q", "--allow-empty", "-m", "x")
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo, worktree=str(other)), _hub(5, {"body": "変更", "files": []}), ask)
    assert out["verdict"] is None and _stopped(out) == "no_pr" and ask.calls == []
    assert "worktree でない" in _failures()[0]["cause"] and "worktree でない" not in out["why"][0]
    # フォークの同名ブランチは数えず、同じリポジトリの開いた PR が 2 件なら決めない
    fork = SimpleNamespace(number=7, state="open", cross_repository=True)
    mine = SimpleNamespace(number=5, state="open", cross_repository=False)
    case = {"body": "変更", "files": [], "pulls": [fork, mine]}
    assert mvv_gate.pr_number(repo, "design", _plan_inputs(repo), Hub(5, case)) == 5
    twice = {**case, "pulls": [mine, SimpleNamespace(number=6, state="open", cross_repository=False)]}
    with pytest.raises(mvv_gate.GateError, match="複数"):
        mvv_gate.pr_number(repo, "design", _plan_inputs(repo), Hub(5, twice))


class _BranchOnlyGit:
    """ブランチ名だけを返す Git の偽物（子プロセスを起動しない）。ほかの問い合わせはテストを落とす。"""

    def __init__(self, where: Path) -> None:
        self.where = where

    def run(self, *args: str) -> str:
        assert args == ("rev-parse", "--abbrev-ref", "HEAD"), f"想定しない Git の呼び出し: {args}"
        return "design/x\n"


def test_a_worktree_at_the_same_path_is_not_checked_with_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 作業場所とリポジトリの根（root）が同じパスなら、共通ディレクトリを Git に問わない
    def no_git(where: Path) -> str:
        raise AssertionError("同じパスで共通ディレクトリを問うた")

    monkeypatch.setattr(mvv_gate, "_common_dir", no_git)
    monkeypatch.setattr(mvv_gate, "Git", _BranchOnlyGit)
    assert mvv_gate.pr_number(tmp_path, "design", _plan_inputs(tmp_path, worktree=str(tmp_path)), Hub(5, {})) == 5


# --- #3959 決定 5: --expect-pr（承認する PR と、判定に使う入力の PR を判定の前に比べる） ---


def _release_machine(repo: Path, capsys: pytest.CaptureFixture[str], pr: int) -> str:
    """文脈の pr を持つステートマシンを作り、ID を返す（リリース承認ゲートの判定の入力を --machine で組める）。"""
    monkey = pytest.MonkeyPatch()
    monkey.chdir(repo)
    try:
        assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
        tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
        _set_pr(repo, capsys, tid, pr)
    finally:
        monkey.undo()
    return tid


def _set_pr(repo: Path, capsys: pytest.CaptureFixture[str], tid: str, pr: int) -> None:
    monkey = pytest.MonkeyPatch()
    monkey.chdir(repo)
    try:
        out_file = repo.parent / f"pr-{pr}.json"
        out_file.write_text(json.dumps({"steps": [], "summary": "PR を作った", "outputs": {"pr": pr}}))
        assert command.main(["finish", tid, "Pull Request", "--output", str(out_file), "--reason", "手で"]) == 0
        capsys.readouterr()
    finally:
        monkey.undo()


class _UnreadHub(Hub):
    def pull(self, number: int) -> SimpleNamespace:
        raise AssertionError("判定しないのに PR を読んだ")


def _no_judging(monkeypatch: pytest.MonkeyPatch) -> None:
    # 承認する PR と比べるだけなら GitHub へつながない（認証の無い環境でも not_applicable を返す）
    monkeypatch.setattr(cli, "_hub", lambda root: pytest.fail("PR を比べる前に GitHub へつないだ"))
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("判定する PR が承認する PR と違うのに判定した"))
    monkeypatch.setattr(mvv_gate.llm, "ask", lambda *a, **k: pytest.fail("LLM を呼んだ"))


def test_expect_pr_judges_when_the_input_pr_is_the_pr_to_approve(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tid = _release_machine(repo, capsys, 12)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    monkeypatch.setattr(cli, "_hub", lambda root: _UnreadHub(12, {}))
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli.mvv_gate, "gate", lambda root, g, inputs, hub, warnings: seen.append(dict(inputs)) or mm.gate_outcome(g, None, False, ["x"])
    )
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert out["status"] == "ok" and [s["pr"] for s in seen] == [12]


def test_expect_pr_of_another_pr_stops_without_reading_judging_or_commenting(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], notes: list[tuple[int, str]]
) -> None:
    tid = _release_machine(repo, capsys, 12)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    _no_judging(monkeypatch)
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "34")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "not_applicable") and notes == []
    shown = json.dumps([out["summary"], out.get("warnings", []), out["cause"]], ensure_ascii=False)
    assert "12" not in shown and "34" not in shown
    # 承認する PR を 12 と決めた後、判定の前に文脈の pr が 56 に変わった → 打った時点の入力と比べるので判定しない
    # （文脈の pr を書く工程は 1 回しか終えられないので、打った時点で文脈から組んだ入力をスタブで 56 にする）
    built = {**stage_inputs.stage_inputs_of(repo, tid, mvv_gate.GATE_STEPS["release"]), "pr": 56}
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", lambda root, mid, step: dict(built))
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "not_applicable") and notes == []
    assert "56" not in out["summary"] and "12" not in out["summary"]


def test_expect_pr_is_invalid_for_the_design_gate(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_judging(monkeypatch)
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_plan_inputs(repo)))
    out = _cli_gate(repo, capsys, "design", "--expect-pr", "5")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "invalid")


def test_expect_pr_without_the_pr_in_the_context_is_input_missing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
    tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    _no_judging(monkeypatch)
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "input_missing")
