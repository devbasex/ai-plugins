"""pace: normal の承認ゲートの後のつなぎ（#1426 の AC1〜AC3・AC8・AC9・I1・I9）。"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_supervise import cli  # noqa: E402

pytestmark = pytest.mark.usefixtures("issue_bodies")  # 課題の本文は偽の gh が返す

BASE = ["new", "sprint", "--name", "m1", "--issue", "11", "12", "--version", "10.18.0-dev.1"]


def sprint(tmp_path: Path, *extra: str) -> list[dict]:
    out = tmp_path / "m"
    p = cli(*BASE, "--worktree", str(tmp_path), "--out", str(out), *extra)
    assert p.returncode == 0, p.stderr + p.stdout
    return json.loads((out / "sprint.json").read_text())["ステージ"]


def queue_words(command: str) -> list[list[str]]:
    """queue のコマンドのステージごとのプラン（--then の区切り）。"""
    words = shlex.split(command)
    words = words[words.index("queue") + 1 :]
    stages, cur = [], []
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        if w in ("--max", "--done"):
            skip = True
            continue
        if w == "--then":
            stages.append(cur)
            cur = []
            continue
        cur.append(w)
    return [*stages, cur]


def test_normal_with_design_merges_then_chains_up_to_the_release(tmp_path):
    stages = sprint(tmp_path, "--design", "11", "12")
    names = [s["name"] for s in stages]
    assert names == ["設計", "関門 1", "設計 PR のマージ", "設計の結果", "スプリントブランチ", "実装", "検査", "配布", "関門 2", "本番"]
    by = {s["name"]: s for s in stages}
    merge = by["設計 PR のマージ"]
    for n, path in zip((11, 12), merge["plans"]):
        assert Path(path).name == f"3-design-merge-{n}.json"
        plan = json.loads(Path(path).read_text())
        assert plan["Pull Request"] == f"{{queue_pr:design-{n}}}"
        assert [s["id"] for s in plan["steps"]] == ["approved", "merge-gate", "merge", "merge-approved"]
        assert "design-approved --pr {pr}" in plan["steps"][0]["cmd"] and plan["steps"][0]["gate_next"] == "end"
    # 承認ゲート 1 の後はマージのステージの command 1 本だけで、後続はその --then の順に並ぶ
    chained = names[3:8]
    assert all("command" not in by[n] and by[n]["then_of"] == "設計 PR のマージ" for n in chained)
    assert queue_words(merge["command"]) == [merge["plans"], *(by[n]["plans"] for n in chained)]
    done = str((tmp_path / "m" / "done-3.json").resolve())
    assert f"--done {done}" in merge["command"]
    # 止まったときの手は queue --resume の 1 つ（AC8）
    for n in ["設計 PR のマージ", *chained]:
        assert by[n]["resume"].endswith(f"queue --resume {done}")
    assert by["設計"]["resume"].endswith("queue --resume " + str((tmp_path / "m" / "done-1.json").resolve()))
    assert by["本番"]["resume"].endswith("queue --resume " + str((tmp_path / "m" / "done-10.json").resolve()))
    assert "resume" not in by["関門 1"] and "resume" not in by["関門 2"]
    # 本番のプランの先頭が approve（AC9）
    prod = json.loads(Path(by["本番"]["plans"][0]).read_text())
    assert prod["steps"][0]["id"] == "approve" and "--facts-from '{queue_state?:release}'" in prod["steps"][0]["cmd"]


def test_normal_without_design_chains_from_the_sprint_branch(tmp_path):
    stages = sprint(tmp_path)
    by = {s["name"]: s for s in stages}
    assert [s["name"] for s in stages] == ["スプリントブランチ", "実装", "検査", "配布", "関門 2", "本番"]
    assert "command" in by["スプリントブランチ"] and "then_of" not in by["スプリントブランチ"]
    assert queue_words(by["スプリントブランチ"]["command"]) == [by[n]["plans"] for n in ("スプリントブランチ", "実装", "検査", "配布")]
    assert by["配布"]["then_of"] == "スプリントブランチ"


def test_normal_with_state_passes_the_answer_to_merge_and_release(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"name": "m1", "gates": []}))
    stages = sprint(tmp_path, "--design", "11", "--state", str(state))
    by = {s["name"]: s for s in stages}
    merge = json.loads(Path(by["設計 PR のマージ"]["plans"][0]).read_text())
    assert f"--state {state.resolve()}" in merge["steps"][0]["cmd"]
    prod = json.loads(Path(by["本番"]["plans"][0]).read_text())
    assert f"--answer-from {state.resolve()}" in prod["steps"][0]["cmd"]
    assert "'関門 1'" in by["関門 1"]["gate"] and "--pr <設計 PR>" in by["関門 1"]["gate"]
    assert "'関門 2'" in by["関門 2"]["gate"] and "approved-sha" not in by["関門 2"]["gate"]


def plans_of(stages: list[dict]) -> dict[str, dict]:
    return {Path(p).stem.split("-", 1)[1]: json.loads(Path(p).read_text()) for s in stages for p in s.get("plans", [])}


def test_absorb_folds_the_issue_into_the_host(tmp_path, issue_bodies):
    """AC13〜AC15: 取り込み先の設計のプランの `課題` と requirements / design の prompt が両方を持ち、取り込んだ課題は
    設計のプランを持たず、実装のプランは `実行の条件` で飛ぶ。取り込んだ課題に受け入れ条件の節が無くても止まらない。"""
    issue_bodies.body(13, "受け入れ条件の節が無い本文")
    stages = sprint(tmp_path, "--design", "11", "--absorb", "13:11", "12:11")
    plans = plans_of(stages)
    assert "design-13" not in plans and "design-12" not in plans
    assert plans["design-11"]["課題"] == [11, 13, 12]
    for step in plans["design-11"]["steps"]:
        if step["id"] in ("requirements", "design"):
            assert "#11" in step["prompt"] and "#13" in step["prompt"] and "#12" in step["prompt"]
    assert plans["impl-11"]["課題"] == [11, 13, 12]
    for n in (12, 13):
        assert plans[f"impl-{n}"]["取り込み先"] == 11 and plans[f"impl-{n}"]["実行の条件"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--absorb", "13:99"],
        ["--absorb", "11:11"],
        ["--design", "11", "12", "--absorb", "12:11"],
        ["--design", "21", "--absorb", "12:21"],
    ],
    ids=["no-host", "same", "absorbed-in-design", "host-without-impl"],
)
def test_absorb_with_a_bad_pair_writes_nothing(tmp_path, extra):
    """AC16・I8: 取り込み先が無い・実装のプランが無い・同じ番号・取り込む課題が --design にあるなら、どのファイルも書かない。"""
    out = tmp_path / "m"
    p = cli(*BASE, "--worktree", str(tmp_path), "--out", str(out), *extra)
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "stopped"
    assert not out.exists() or not list(out.iterdir())


def test_design_approved_needs_a_label_or_the_users_answer_for_that_pr(tmp_path):
    """I1: 承認ラベルも承認ゲート 1 の記録も無ければ 10。別の PR の記録は数えない。記録があればラベルを付けて 0。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from supervise_lib import design_merge

    calls = []

    def gh(labels):
        def run(args):
            calls.append(args)
            return (0, json.dumps(labels)) if args[:2] == ["pr", "view"] else (0, "")

        return run

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"gates": [{"name": "関門 1", "what": "x", "at": "2026-10-30T00:00:00Z", "by": "user", "pr": 2}]}))
    assert design_merge.cmd_design_approved(1, str(state), gh([]))[1] == 10
    assert design_merge.cmd_design_approved(0, str(state), gh([]))[1] == 10
    assert design_merge.cmd_design_approved(1, None, gh(["design-approved"]))[1] == 0
    assert design_merge.cmd_design_approved(1, None, gh(["design-approved-pending"]))[1] == 10  # 部分一致を数えない
    assert design_merge.cmd_design_approved(1, None, lambda args: (0, "not json"))[1] == 2
    calls.clear()
    out, code = design_merge.cmd_design_approved(2, str(state), gh([]))
    assert (code, out["items"][0]["result"]) == (0, "added")
    assert ["pr", "edit", "2", "--add-label", "design-approved"] in calls
    assert design_merge.cmd_design_approved(2, str(state), lambda args: (1, "boom"))[1] == 2
