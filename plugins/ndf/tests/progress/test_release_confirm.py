"""手で終えるデプロイの工程の、リリース記録と CD の確かめ・追認・リリースしないで終える扱い（#4520 の不変条件 I1 のデプロイ）。

ステートマシンの DB は本物で、作業（プラン）と GitHub は偽物にする。
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import merge_hub
import pytest
from result_json import last_result

from ndf.adapters import ci
from ndf.adapters.github import Authored
from ndf.core import release_record
from ndf.progress import delivery_decide, gate, invoke, run
from ndf.progress.gate import answering, delivery_check, finishing


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manual = _load("test_manual_gate")
env = manual.env  # 作業と GitHub の偽物の fixture を共有する

DEPLOY = manual.DEPLOY
VERIFY = "リリース後テスト"
VERSION = "11.0.0"
SPRINT = 8
MERGE_SHA = "c" * 40
NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)


def _body(stage: str = "本番（承認済み）", version: str | None = VERSION, sprint: int = SPRINT, cd: str | None = None) -> str:
    lines = ["## リリース記録", "", f"段階: {stage}"]
    if version is not None:
        lines.append(f"バージョン: 10.9.0 → {version}（MINOR: 機能追加）")
    lines.append(f"スプリント: PR #{sprint} / #9")
    if cd is not None:
        lines.append(f"CD の結果: {cd}")
    return "\n".join(lines) + "\n"


def _workflow(unit: str | None = None, version: str = VERSION, release: int = 30) -> str:
    made = {"version": version, "release": release, "included": [SPRINT], **({"unit": unit} if unit else {})}
    return f"{release_record.RECORD_MARK}\n## リリース記録\n\n```json\n{json.dumps(made)}\n```\n"


def _judge(bodies, runs=None):
    return delivery_decide.judge_delivery(delivery_decide.pick_records(bodies, sprint_pr=SPRINT, version=VERSION), runs)


# --- 記録と CD の判定 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bodies, ok",
    [
        ([], False),
        ([_body()], True),
        ([_workflow()], True),
        ([_body("検証")], False),
        ([_body("反映失敗（落ちた）")], False),
        ([_body(), _body("反映失敗（落ちた）")], False),
        ([_body("反映失敗（落ちた）"), _body()], True),
        ([_body(), _body("反映失敗（落ちた）", version=None)], False),
        ([_body("反映失敗（落ちた）", version=None), _body()], True),
        ([_body(version="10.9.9")], False),
        ([_body(sprint=99)], False),
        ([_workflow("a"), _workflow("b"), _body("本番", version="1.0.0")], True),
    ],
    ids=[
        "無し",
        "手の本番",
        "workflow",
        "検証",
        "反映失敗",
        "後の失敗",
        "直した後",
        "バージョンの無い失敗",
        "前のバージョンの無い失敗",
        "バージョンが違う",
        "PR が違う",
        "別のバージョンは数えない",
    ],
)
def test_the_records_of_the_sprint_and_version_decide(bodies, ok):
    assert _judge(bodies).ok is ok


def test_each_release_unit_is_judged_by_its_own_latest_record():
    failed = delivery_decide.pick_records([_workflow("a"), _body("反映失敗"), _workflow("b")], sprint_pr=SPRINT, version=VERSION)

    assert not delivery_decide.judge_delivery(failed, None).ok  # 単位の名前の無い組の失敗は、別の単位の成功で隠れない


def _run(conclusion: str | None = "success", event: str = "push", status: str = "completed", at: str = "2026-11-28T00:00:00Z") -> dict:
    return {"event": event, "status": status, "conclusion": conclusion, "created_at": at}


@pytest.mark.parametrize(
    "run_, cd, ok, result_",
    [
        (_run(), None, True, "success"),
        (_run("failure"), "未確定", False, None),
        (None, "未確定（上限）", True, "unknown"),
        (_run(None, status="in_progress"), "未確定", True, "unknown"),
        (None, "成功（…）", False, None),
        (None, None, False, None),
    ],
)
def test_the_cd_run_decides_with_the_record(run_, cd, ok, result_):
    got = _judge([_body(cd=cd)], {None: ci.ended_with(run_) if run_ else None})

    assert got.ok is ok and got.cd_result == result_


def test_the_push_run_is_picked_before_a_dispatched_one():
    pushed, dispatched = _run(at="2026-11-01T00:00:00Z"), _run("failure", "workflow_dispatch", at="2026-11-02T00:00:00Z")

    assert delivery_decide.pick_run([dispatched, pushed, _run("failure", "issue_comment")]) == pushed
    assert delivery_decide.pick_run([dispatched]) == dispatched
    assert delivery_decide.pick_run([_run(event="issue_comment")]) is None


def test_only_a_release_written_after_the_reopen_counts_as_executed():
    before, after = NOW - timedelta(hours=1), NOW + timedelta(hours=1)

    def executed(body, at):
        return delivery_decide.executed([(body, at)], sprint_pr=SPRINT, since=NOW)

    assert executed(_body(), after) and executed(_workflow(), after) and executed(_body("反映失敗", version=None), after)
    assert not executed(_body(), before) and not executed(_body("検証"), after) and not executed(_body("デプロイなし（x）"), after)
    assert delivery_decide.executed([(_body(), before)], sprint_pr=SPRINT, since=None)


def test_no_release_needs_the_latest_record_to_be_no_deploy():
    skipped = _body("デプロイなし（文書だけ）", version=None)

    got = delivery_decide.judge_no_release([_body(), skipped], sprint_pr=SPRINT)

    assert got.ok and got.no_release_reason == "文書だけ"
    assert not delivery_decide.judge_no_release([skipped, _body()], sprint_pr=SPRINT).ok
    assert not delivery_decide.judge_no_release([skipped], sprint_pr=99).ok


# --- GitHub を読む確かめ ------------------------------------------------------------------------


def _strategy(**result_) -> dict:
    row = {"target": "t", "kind": "auto", "channel": "production", **({"result": result_} if result_ else {})}
    return merge_hub.strategy(deliveries=[row])


def _hub(body: str = "") -> merge_hub.MergeHub:
    hub = merge_hub.MergeHub()
    hub.pulls_of[SPRINT] = merge_hub.pull(SPRINT, merged=True)._replace(merge_sha=MERGE_SHA)
    hub.comments_of[SPRINT] = [Authored("octocat", "OWNER", body or _body(), author_id=1)]
    return hub


def _confirm(hub, strategy, target: str = "t"):
    return delivery_check.confirm(hub, delivery_check.Wanted(SPRINT, SPRINT, VERSION, target), strategy)


def test_the_workflow_of_the_delivery_row_is_read_for_the_merge_commit():
    hub = _hub()
    hub.runs_of[("cd.yml", MERGE_SHA)] = [_run("failure")]

    assert not _confirm(hub, _strategy(workflow="cd.yml")).ok
    assert _confirm(hub, _strategy()).ok and _confirm(hub, {}).ok
    assert not _confirm(hub, _strategy(), target="別の届け先").ok
    assert not _confirm(hub, merge_hub.strategy(deliveries=None, cd={"workflow": "cd.yml"})).ok


def test_an_untrusted_record_is_not_read():
    hub = _hub()
    hub.comments_of[SPRINT] = [Authored("someone", "NONE", _body(), author_id=2)]

    assert not _confirm(hub, {}).ok


# --- 手で終える操作と next ----------------------------------------------------------------------


def _record(env, body: str | None = None, number: int = SPRINT, at: datetime | None = None) -> None:
    made = Authored("octocat", "OWNER", body or _body(), author_id=1, created=at)
    env.hub.comments_of.setdefault(number, []).append(made)


def _new(capsys, repo, env, st) -> str:
    """設計を承認してマージし（読むのは次の next）、Pull Request の工程までを手で終える。"""
    tid = manual._held_design(st, capsys, repo, env)
    manual._answer(st, tid, repo, env, manual._next(st, tid, repo, env), key="k-design")
    manual._merge(env)
    assert manual._finish(capsys, repo, tid, "Pull Request", {"pr": SPRINT})["status"] == "ok"
    return tid


def _deploy(capsys, repo, tid: str, *extra: str, outputs: dict | None = None, command: str = "finish") -> dict:
    path = repo.parent / "deploy.json"
    values = manual._released_values(repo) if outputs is None else outputs
    path.write_text(json.dumps({"steps": [], "summary": "デプロイを手で行った", "outputs": values}))
    reason = ["--reason", "手で"] if command == "finish" else []
    finishing.main([command, tid, DEPLOY, "--output", str(path), *reason, *extra])
    return last_result(capsys.readouterr().out)


def _held_payload(st, tid: str, kind: str = "finish_held") -> dict:
    return next(ev.draft.payload for ev in reversed(run.read(st, tid).row_events(DEPLOY)) if ev.draft.kind == kind)


def _answers(st, tid: str) -> list[dict]:
    return [ev.draft.payload for ev in run.read(st, tid).row_events(DEPLOY) if ev.draft.kind == run.ANSWERED]


def _released_port(env, repo):
    return delivery_check.released(lambda: env.hub)


def _reply(st, tid, repo, env, shown: dict, key: str = "k-1", value: str = "approved") -> dict:
    """デプロイの質問へ回答する（`ndf answer` と同じに、実行済みの証拠を読む口を渡し、証跡の投稿を数える）。"""
    argv = shown["routes"][value]["argv"]
    seen = gate.Target(**{manual._FLAGS[f]: v for f, v in zip(argv, argv[1:]) if f in manual._FLAGS})
    a = gate.Answer(shown["metrics"]["gate"]["id"], value, "回答", "human:reviewer", key, None, seen=seen)
    env.reader.forget()
    posted: list = []
    observe = answering.github_observe(repo, st.root, reader=env.reader)
    out = gate.answer(st, tid, a, observe, lambda row: posted.append(row) or True, answering.Outward(released=_released_port(env, repo)))
    return {**out, "posted": posted}


def test_a_deploy_without_a_record_is_not_finished_with_or_without_an_approval(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)

    out = _deploy(capsys, repo, tid)

    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused", out
    assert "リリース記録" in out["cause"]["detail"] and manual._kinds(st, tid, DEPLOY) == ["created"]


def test_the_record_pull_of_a_hand_finish_is_read_again_by_next(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    env.hub.pulls_of[30] = merge_hub.pull(30, merged=True)
    _record(env, number=30)
    assert _deploy(capsys, repo, tid, "--record-pr", "30")["status"] == "ok"
    assert _held_payload(st, tid)["record_pr"] == 30
    shown = manual._next(st, tid, repo, env)
    assert "追認" in Path(shown["presentation_path"]).read_text(encoding="utf-8")
    replied = _reply(st, tid, repo, env, shown)
    assert replied["posted"] == [] and _answers(st, tid)[-1]["after_release"] is True
    assert gate.approved_head(run.read(st, tid), "release") is None
    assert gate.answers(run.read(st, tid))[-1]["note"] == "追認"

    _record(env, _body("反映失敗（落ちた）", version=None), number=30)
    stopped = manual._next(st, tid, repo, env)
    assert stopped["status"] == "stopped" and stopped["cause"]["kind"] == "precondition", stopped
    assert manual._state(st, tid, DEPLOY) == "running"

    _record(env, number=30)
    manual._next(st, tid, repo, env)
    assert manual._state(st, tid, DEPLOY) == "finished"
    assert _held_payload(st, tid, "finished")["record_pr"] == 30


def test_an_unknown_cd_result_is_recorded_as_unknown(repo, st, env, capsys, monkeypatch):
    monkeypatch.setattr(delivery_check, "declared", lambda hub, cwd: merge_hub.strategy(deliveries=None, cd={"workflow": "cd.yml"}))
    tid = _new(capsys, repo, env, st)
    env.hub.pulls_of[SPRINT] = merge_hub.pull(SPRINT, merged=True)._replace(merge_sha=MERGE_SHA)
    _record(env, _body(cd="未確定（上限）"))

    out = _deploy(capsys, repo, tid)

    assert out["status"] == "ok" and any("未確定" in w for w in out["warnings"]), out
    assert _held_payload(st, tid)["cd_result"] == "unknown"


def test_the_record_check_reads_the_pull_once_and_the_comments_once(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    _record(env)
    env.hub.calls.clear()

    assert _deploy(capsys, repo, tid)["status"] == "ok"

    assert env.hub.calls.count("pull") <= 1 and env.hub.calls.count("comments") == 1, env.hub.calls


@pytest.mark.parametrize("stage, flag", [("Pull Request", "--no-release"), ("設計", "--record-pr=8")])
def test_the_release_flags_are_only_for_the_deploy_stage(repo, st, env, capsys, stage, flag):
    tid = manual._new(capsys)
    manual._before_design(capsys, repo, tid)
    path = repo.parent / "o.json"
    path.write_text(json.dumps({"steps": [], "summary": "x", "outputs": {"pr": SPRINT, "design_pr": 7}}))

    finishing.main(["finish", tid, stage, "--output", str(path), "--reason", "手で", flag])

    out = last_result(capsys.readouterr().out)
    assert out["status"] == "stopped" and "デプロイ" in out["cause"]["detail"], out


def _no_release(env) -> None:
    """デプロイなしの記録を置く。デプロイの後の工程は止めて、スプリントを進行中のままにする。"""
    env.script["cleanup"] = [lambda r: invoke.StageResult(run.result.stopped("plan", "test_failed", "止める"), {})]
    _record(env, _body("デプロイなし（文書だけ）", version=None))


def _enabled_state(st, tid) -> str:
    return manual._state(st, tid, VERIFY)


def test_a_no_release_finish_needs_its_own_approval_and_disables_the_verification(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    _no_release(env)
    assert _deploy(capsys, repo, tid, "--no-release")["cause"]["kind"] == "refused"  # バージョンと届け先は受けない
    assert _deploy(capsys, repo, tid, "--no-release", outputs={}, command="check")["status"] == "ok"
    assert _deploy(capsys, repo, tid, "--no-release", outputs={})["status"] == "ok"
    assert _held_payload(st, tid)["no_release"] is True
    shown = manual._next(st, tid, repo, env)
    assert "リリースせずにこの工程を終える" in Path(shown["presentation_path"]).read_text(encoding="utf-8")
    replied = _reply(st, tid, repo, env, shown)
    answered = _answers(st, tid)[-1]
    assert replied["posted"] == [] and answered["no_release"] is True and answered["no_release_reason"] == "文書だけ"
    assert gate.approved_head(run.read(st, tid), "release") is None

    manual._next(st, tid, repo, env)

    assert manual._state(st, tid, DEPLOY) == "finished" and _enabled_state(st, tid) == "disabled"
    finishing.main(["retry", tid, DEPLOY, "--reason", "出す", "--later", "keep"])
    capsys.readouterr()
    assert manual._state(st, tid, DEPLOY) == "pending" and _enabled_state(st, tid) == "pending"


def test_a_stage_disabled_by_a_person_stays_disabled_after_the_reopen(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    finishing.main(["disable", tid, VERIFY, "--reason", "人が止めた"])
    capsys.readouterr()
    _no_release(env)
    assert _deploy(capsys, repo, tid, "--no-release", outputs={})["status"] == "ok"
    _reply(st, tid, repo, env, manual._next(st, tid, repo, env))
    manual._next(st, tid, repo, env)

    finishing.main(["retry", tid, DEPLOY, "--reason", "出す", "--later", "keep"])
    capsys.readouterr()

    assert _enabled_state(st, tid) == "disabled"


def test_a_release_approval_is_not_used_to_close_without_a_release(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    replied = _reply(st, tid, repo, env, _question(capsys, tid))  # 実行の前の承認
    assert replied["status"] == "ok" and "after_release" not in _answers(st, tid)[-1], replied
    _no_release(env)

    assert _deploy(capsys, repo, tid, "--no-release", outputs={})["status"] == "ok"

    assert manual._state(st, tid, DEPLOY) == "running"


def _question(capsys, tid: str) -> dict:
    finishing.main(["question", tid, DEPLOY])
    return last_result(capsys.readouterr().out)


def test_an_answer_after_a_release_without_a_finish_is_a_ratification(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    _record(env)
    shown = _question(capsys, tid)
    assert "追認" in Path(shown["presentation_path"]).read_text(encoding="utf-8")

    replied = _reply(st, tid, repo, env, shown)

    assert replied["posted"] == [] and _answers(st, tid)[-1]["after_release"] is True
    assert gate.approved_head(run.read(st, tid), "release") is None
    assert _deploy(capsys, repo, tid)["status"] == "ok" and manual._state(st, tid, DEPLOY) == "finished"


def test_a_record_before_the_reopen_leaves_the_next_answer_as_an_approval(repo, st, env, capsys):
    tid = _new(capsys, repo, env, st)
    _record(env, at=datetime.now(timezone.utc) - timedelta(days=1))
    assert _deploy(capsys, repo, tid)["status"] == "ok"
    finishing.main(["retry", tid, DEPLOY, "--reason", "出し直す", "--later", "keep"])
    capsys.readouterr()

    replied = _reply(st, tid, repo, env, _question(capsys, tid))

    assert replied["status"] == "ok" and "after_release" not in _answers(st, tid)[-1], replied
    assert gate.approved_head(run.read(st, tid), "release") == merge_hub.HEAD
