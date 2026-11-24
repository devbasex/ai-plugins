"""cross-refactoring の提案と計画の担当へ、承認済みのプロジェクト MVV を渡すこと（#4076）。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters import agents, github
from ndf.domains.crossrefactor import drive, prompt_decide, store
from ndf.domains.crossrefactor.symbols_decide import Touched
from ndf.domains.records import mvv

from ._drive_world import (
    BRANCH,
    PR,
    FakeAgents,
    FakeHub,
    Watched,
    World,
    base_acts,
    commit_in,
    fake_ci,
    make_world,
    ports,
    propose_area,
    sh,
)

# 作業場所に作る MVV の本文のパス（このリポジトリ自身の MVV の文書は読まない）
NDF_DIR = ".ndf"
BODY_AT = f"{NDF_DIR}/mvv.md"
BODY = "# 見本の MVV\n\n## Mission\n\n使命\n\n## Value\n\n1. **一つ目**\n"
TOUCHED = Touched({"src/geo.py": frozenset({"area"})})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return make_world(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def mvv_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """MVV のスナップショットの保存先を一時ディレクトリにする。"""
    state = tmp_path / "mvv-state"
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(state))
    return state


def _approval(version: int, approved: str = BODY) -> str:
    sha = hashlib.sha256(approved.encode("utf-8")).hexdigest()
    return json.dumps({"versions": [{"version": version, "sha256": sha}]})


def _push_base(world: World, files: dict[str, str]) -> None:
    """リモートの `main` だけを進める（`world.main` の手元のブランチとリモート追跡参照は古いまま）。"""
    other = world.main.parent / "other"
    if not other.exists():
        sh(world.main.parent, "clone", "-q", "--branch", "main", str(world.origin), str(other))
    commit_in(other, files, "ベースの変更")
    sh(other, "push", "-q", "origin", "HEAD:refs/heads/main")


def _push_mvv(world: World, version: int = 11, body: str = BODY, approved: str = BODY) -> None:
    _push_base(world, {BODY_AT: body, f"{NDF_DIR}/mvv.json": _approval(version, approved)})


def _run(world: World, base: str = "main", hub: FakeHub | None = None) -> tuple[drive.Finish, FakeAgents]:
    fake = FakeAgents(base_acts())  # type: ignore[arg-type]
    hub = hub if hub is not None else FakeHub("clean")
    return drive.drive(world.config(base), ports(fake, hub, ci=fake_ci(Watched(None)))), fake


def _prompts(fake: FakeAgents, *phases: str) -> list[str]:
    return [p for phase, p in fake.prompts if phase in phases]


def _paths(prompt: str, state: Path) -> list[Path]:
    """指示文に書かれたスナップショットのパス。"""
    return [Path(p) for p in re.findall(re.escape(str(state)) + r"\S+\.md", prompt)]


@pytest.mark.parametrize("base", ["main", "origin/main"])
def test_approved_mvv_of_the_base_reaches_the_propose_and_plan_prompts(base: str, world: World, mvv_state: Path) -> None:
    """PR が MVV の本文を変えていても、提案と計画の担当の全員に、リモートの最新のベースブランチで承認された本文と NDF の共通原則の
    スナップショットのパスを渡す。ほかの工程の指示文には載せない。"""
    _push_mvv(world)
    commit_in(world.main, {BODY_AT: BODY + "2. **PR が足した**\n"}, "本文を変える")
    sh(world.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    hub = FakeHub("clean")
    got, fake = _run(world, base, hub)
    assert got.kind == "ok", got.detail
    told = _prompts(fake, "propose", "plan")
    assert {phase for phase, _ in fake.prompts} >= {"propose", "plan", "add-tests", "implement"}
    for prompt in told:
        body, principles = _paths(prompt, mvv_state)
        assert "バージョン 11" in prompt
        assert (body.read_text(encoding="utf-8"), principles.read_bytes()) == (BODY, mvv.principles())
    assert not any("MVV" in p for p in _prompts(fake, "add-tests", "implement"))
    # ブランチ名を渡した起動では、マージ先を GitHub へ読みに行かない（最終ゲートの 1 回だけ）
    assert hub.reads == 1


@pytest.mark.parametrize("files", [{"docs/b.md": "# b\n"}, {BODY_AT: BODY, f"{NDF_DIR}/mvv.json": json.dumps({"versions": []})}])
def test_without_an_approved_mvv_the_prompts_stay_the_same(files: dict[str, str], world: World, mvv_state: Path) -> None:
    _push_base(world, files)
    got, fake = _run(world)
    assert got.kind == "ok", got.detail
    # claude は指示文だけを受け取る（ほかのランタイムは起動の引数の形が違う）
    proposals = propose_area(None, None)["proposals"]  # type: ignore[arg-type]
    assert prompt_decide.propose(PR, TOUCHED) in _prompts(fake, "propose")
    assert _prompts(fake, "plan") == [prompt_decide.plan(PR, proposals)]
    assert not any("MVV" in p for _, p in fake.prompts)
    assert not mvv_state.exists()


def test_mismatched_mvv_of_the_base_stops_before_any_agent(world: World) -> None:
    _push_mvv(world, body=BODY + "勝手に足した\n", approved=BODY)
    got, fake = _run(world)
    assert got.kind == "precondition"
    assert "main の MVV を読めない" in got.detail and "承認の記録と合わない" in got.detail and BODY_AT in got.detail
    assert fake.seen == []


def test_snapshot_that_cannot_be_written_stops_before_any_agent(world: World, mvv_state: Path) -> None:
    _push_mvv(world)
    mvv_state.write_text("ディレクトリでない", encoding="utf-8")
    got, fake = _run(world)
    assert got.kind == "precondition" and "main の MVV を読めない" in got.detail
    assert fake.seen == []


def test_snapshots_are_not_written_into_the_work_root(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """担当が書き換えられる作業場所の中には、スナップショットを保存しない。"""
    _push_mvv(world)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(world.root() / "read" / "mvv-state"))
    got, fake = _run(world)
    assert got.kind == "precondition" and fake.seen == []


def test_base_given_as_a_commit_reads_the_mvv_of_the_merge_target(world: World) -> None:
    """`--base` がコミットなら、そのコミットでなく、PR のマージ先のブランチの最新の MVV を渡す。"""
    _push_mvv(world, 12)
    got, fake = _run(world, world.base)
    assert got.kind == "ok", got.detail
    assert all("バージョン 12" in p for p in _prompts(fake, "propose", "plan"))
    assert store.load(store.path_of(world.root()))["base"] == world.base


def test_unreadable_merge_target_stops_before_any_agent(world: World) -> None:
    class Broken(FakeHub):
        def pull(self, number: int) -> github.Pull:
            raise github.GitHubError("認証が無い", 401)

    got, fake = _run(world, world.base, Broken("clean"))
    assert got.kind == "precondition" and "認証が無い" in got.detail
    assert fake.seen == []


def test_given_merge_target_is_not_read_from_github(world: World) -> None:
    """`--base` のコミットと一緒にマージ先のブランチを渡した起動では、MVV を決めるために GitHub を読まない（最終ゲートの 1 回だけ）。"""
    _push_mvv(world, 12)
    fake, hub = FakeAgents(base_acts()), FakeHub("clean")  # type: ignore[arg-type]
    got = drive.drive(world.config(world.base, target_branch="main"), ports(fake, hub, ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    assert all("バージョン 12" in p for p in _prompts(fake, "propose", "plan"))
    assert hub.reads == 1


class Reached(Exception):
    """項目の着手まで進んだことを知らせる例外。"""


def _reach(spec: agents.Launch, submit: Path) -> None:
    raise Reached


def _stop_after_plan(world: World) -> None:
    fake = FakeAgents({**base_acts(), "add-tests": _reach})  # type: ignore[arg-type,dict-item]
    with pytest.raises(Reached):
        drive.drive(world.config(), ports(fake, FakeHub("clean")))


def _resume(world: World, answer: dict[str, Any] | None) -> tuple[drive.Finish | None, FakeAgents]:
    """止まった実行を再開する。項目の着手まで進めば結果は None。"""
    fake = FakeAgents({**base_acts(), "plan": lambda spec, submit: answer, "add-tests": _reach})  # type: ignore[arg-type,dict-item]
    try:
        return drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=fake_ci(Watched(None)))), fake
    except Reached:
        return None, fake


def _answer(keep: bool) -> dict[str, Any]:
    return {"items": [{"item": "I-001", "keep": keep, "reason": "一つ目に反する"}]}


def test_resume_with_a_changed_mvv_defers_the_open_items_against_it(world: World) -> None:
    """計画の後に MVV が変わった再開は、未着手の項目を今の MVV で確かめ、反する項目を担当へ渡さずに見送る。"""
    _push_mvv(world, 11)
    _stop_after_plan(world)
    _push_mvv(world, 12)
    got, fake = _resume(world, _answer(False))
    assert got is not None and got.kind == "ok", got
    assert [phase for phase, _ in fake.prompts] == ["plan"] and "バージョン 12" in fake.prompts[0][1]
    (item,) = store.load(store.path_of(world.root()))["items"]
    assert (item["status"], item["reason"]) == ("deferred", "今の MVV に反する: 一つ目に反する")


def test_resume_with_a_changed_mvv_goes_on_with_the_kept_items_and_asks_once(world: World) -> None:
    _push_mvv(world, 11)
    _stop_after_plan(world)
    _push_mvv(world, 12)
    for phases in (["plan", "add-tests"], ["add-tests"]):
        got, fake = _resume(world, _answer(True))
        assert got is None and [phase for phase, _ in fake.prompts] == phases


def test_resume_of_a_state_without_the_mvv_of_the_plan_checks_the_open_items(world: World) -> None:
    _push_mvv(world, 11)
    _stop_after_plan(world)
    path = store.path_of(world.root())
    state = store.load(path)
    del state["mvv"]
    store.save(path, state)
    got, fake = _resume(world, _answer(True))
    assert got is None and [phase for phase, _ in fake.prompts] == ["plan", "add-tests"]


@pytest.mark.parametrize("answer", [None, {"items": []}])
def test_resume_stops_when_the_open_items_are_not_checked(answer: dict[str, Any] | None, world: World) -> None:
    _push_mvv(world, 11)
    _stop_after_plan(world)
    _push_mvv(world, 12)
    got, fake = _resume(world, answer)
    assert got is not None and got.kind == "work_stopped"
    assert [phase for phase, _ in fake.prompts] == ["plan"]


@pytest.mark.parametrize("base", ["main", "origin/main", None])
def test_moved_base_does_not_add_functions_changed_only_on_the_base(base: str | None, world: World) -> None:
    """取得で `origin/main` が PR の分岐点より先へ進んでも、`main` だけが変えた関数を PR が触る関数に数えない。手元の `main`
    （ここでは PR の変更も含む）は取得で動かないので比べない。コミット（None の行）を渡せば、そのコミットと比べる。"""
    geo = "def area(w, h):\n    return w*h\n\n\ndef untouched():\n    return 1\n"
    _push_base(world, {"src/geo.py": geo})
    fake = FakeAgents(base_acts())  # type: ignore[arg-type]
    drive.drive(world.config(base if base is not None else world.base), ports(fake, FakeHub("clean"), ci=fake_ci(Watched(None))))
    assert store.load(store.path_of(world.root()))["touched"]["symbols"] == {"src/geo.py": ["area"]}


def test_prompt_text_of_an_approved_mvv_names_both_files_before_the_result_form() -> None:
    snap = mvv.Snapshot("approved", 3, "s", Path("/st/v3.md"), (), {}, "c", "p", Path("/st/principles.md"))
    text = prompt_decide.mvv_text(snap)
    assert "バージョン 3" in text and "- 本文: /st/v3.md" in text and "/st/principles.md" in text and text.endswith("\n")
    plain = prompt_decide.propose(7, TOUCHED)
    *head, last = plain.split("\n")
    assert prompt_decide.propose(7, TOUCHED, mvv=text) == "\n".join(head) + "\n" + text + last
    proposals = [{"path": "src/a.py", "symbol": "f", "title": "t", "reason": "r"}]
    *head, last = prompt_decide.plan(7, proposals).split("\n")
    assert prompt_decide.plan(7, proposals, text) == "\n".join(head) + "\n" + text + last


def test_prompt_text_is_empty_without_an_approved_mvv() -> None:
    absent = mvv.Snapshot("absent", None, None, None, (), {}, "c", "", None)
    assert prompt_decide.mvv_text(absent) == ""
