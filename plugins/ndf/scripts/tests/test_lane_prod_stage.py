"""並列の計画のレーンの本番のステージと、設計承認ゲートで外した課題（#2717）。

リリースの Pull Request で届く経路のレーンは本番チャネルへ入れるリリースプランをそのまま流し（I1）、テンプレートの経路のレーンはリリースの依頼を送る（I2）。
リリースの依頼のコマンドは課題の番号を持たず、流す時点のプランの `課題` を `{plan_issues}` で埋める（I3）。
レーンの判定は外のオーガナイザーの置き場（`NDF_ORGANIZER_DIR`）で真にする。実機の gh と claude は呼ばない。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import parallel_release  # noqa: E402
import sprint_loop_gate as sl_gate  # noqa: E402
from pace_state import sprint_state  # noqa: E402
from supervise_lib import queue, sprint  # noqa: E402
from test_supervise_pace_verify import PRD, STG, make_repo  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
LANE = "loop-l20261107"

# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


@pytest.fixture
def org(tmp_path, monkeypatch):
    """外のオーガナイザーのレーン。lane_of が (`--external`, レーン) を返す。"""
    d = tmp_path / "organizer"
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(d))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", LANE)
    monkeypatch.delenv("NDF_RELAY_NAME", raising=False)
    return d


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def promote_sprint(tmp_path, pace: str, issues=("11",)) -> list[dict]:
    repo = make_repo(tmp_path, [STG, PRD])
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", *issues, "--out", str(out)]
    if pace != "normal":
        args += ["--pace", pace, "--state", str(sprint_state(tmp_path, pace))]
    p = inproc.run(SUPERVISE, *args, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out / "sprint.json")["ステージ"]


def prod_stage(waves) -> list[dict]:
    return [load(p) for w in waves if w["name"] == "本番" for p in w.get("plans", [])]


@pytest.mark.parametrize("pace", ["auto", "normal"])
def test_promote_route_lane_keeps_one_promote_plan(tmp_path, org, pace):
    """受け入れ条件 1 / I1: リリースの Pull Request で届く経路のレーンの本番のステージは本番チャネルへ入れるリリースプラン 1 本で、リリースの依頼を組まない。"""
    plans = prod_stage(promote_sprint(tmp_path, pace))
    assert len(plans) == 1 and plans[0]["フェーズ"] != parallel_release.RELEASE_REQUEST_PHASE
    ids = [s["id"] for s in plans[0]["steps"]]
    assert "promote" in ids and "parallel-request" not in ids
    if pace == "normal":  # mvv を持たない promote はリリース承認ゲートで終わり、承認の後に promote-approved で続ける
        promote = next(s for s in plans[0]["steps"] if s["id"] == "promote")
        assert "--gate-approved" not in promote["cmd"] and "promote-approved" in ids


def test_template_route_lane_requests_the_release_with_the_issue_placeholder(org):
    """受け入れ条件 2 / I2・I3: テンプレートの経路のレーンの本番はリリースの依頼で、コマンドは課題の番号を持たない。"""
    a = argparse.Namespace(issue=[11, 12, 13], mode="standard")
    got = sprint.prod_plans(a, "/r", lambda: pytest.fail("レーンでは本番のプランを組まない"))
    plan = got["release-request"]
    cmd = plan["steps"][0]["cmd"]
    assert plan["課題"] == [11, 12, 13] and "--issues {plan_issues} " in cmd
    assert not any(f" {n}" in cmd.split("--issues")[1] for n in (11, 12, 13))


def test_dropped_issues_leave_the_promote_plan(tmp_path, org):
    """受け入れ条件 3 / I4: リリースの Pull Request で届く経路で 3 件中 2 件を外すと、本番チャネルへ入れるリリースプランの `課題` は残った 1 件になる。"""
    waves = promote_sprint(tmp_path, "auto", ("11", "12", "13"))
    path = next(p for w in waves if w["name"] == "本番" for p in w["plans"])
    assert load(path)["課題"] == [11, 12, 13]
    sl_gate.drop_issues(path, [12, 13])
    assert load(path)["課題"] == [11]


def run_request(tmp_path, org, issues, moved) -> tuple[dict, dict]:
    """リリースの依頼のプランを組み、moved を外してから queue で流す。(依頼のファイル, 依頼の控え) を返す。"""
    a = argparse.Namespace(issue=list(issues), mode="standard")
    plan = parallel_release.plan_release_request(a, str(tmp_path), "--external", LANE)
    path = tmp_path / "prod.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    if moved:
        sl_gate.drop_issues(str(path), moved)
    res = queue.cmd_queue([str(path)], 1, poll=0.05)
    assert res["items"][0]["result"] == "関門", res
    sent = json.loads((tmp_path / "prod-state" / "work" / "request.txt").read_text())
    return load(org / "requests" / f"{sent['id']}.json"), load(path)


def test_request_after_dropping_issues_carries_only_the_kept_issue(tmp_path, org):
    """受け入れ条件 4・5 / I3: 2 件を外した後に流した依頼の `issues` は残った 1 件だけである。"""
    body, plan = run_request(tmp_path, org, (153, 191, 117), [191, 117])
    assert plan["課題"] == [153] and body["issues"] == [153]


def test_request_without_dropping_keeps_every_issue(tmp_path, org):
    """受け入れ条件 8 / I6: 課題を外さなかったループの 1 回の依頼は、組んだときの全件を載せる。"""
    body, plan = run_request(tmp_path, org, (153, 191, 117), [])
    assert plan["課題"] == [153, 191, 117] and body["issues"] == [153, 191, 117]


def test_request_with_no_issues_is_not_refused(tmp_path, org):
    """差し込みの境界: `課題` が空なら `--issues` は空で、request は断らずに依頼を書く。"""
    body, _ = run_request(tmp_path, org, (), [])
    assert body["issues"] == []


def lane_close_waves(monkeypatch) -> dict:
    """テンプレートの経路のレーンで close_waves を組み、ステージの名前ごとのプランを返す（#3083）。部品のプランは中身を省いて差し替える。"""
    monkeypatch.setattr(sprint, "has_release_template", lambda a: True)
    monkeypatch.setattr(sprint, "plan_fast_check", lambda *a, **k: {"steps": []})
    monkeypatch.setattr(sprint, "close_plan", lambda *a, **k: {"steps": []})
    monkeypatch.setattr(sprint, "plan_mvv_release", lambda a, repo, v, ch, condition=None, **k: {"実行の条件": condition})
    a = argparse.Namespace(issue=[11], mode="standard", name="lgate-1", worktree="/r", pace=None, state=None, version=None)
    return {w["name"]: w.get("plans") for w in sprint.close_waves(a)}


def test_lane_close_request_carries_the_dev_condition(org, monkeypatch):
    """#3083 の受け入れ条件 1 / I1: レーンのまとめのリリースの依頼のプランは、開発版のプランと同じ実行条件を持つ。"""
    waves = lane_close_waves(monkeypatch)
    cond = waves["本番"]["release-request"]["実行の条件"]
    assert cond == waves["開発版"]["release"]["実行の条件"]
    assert "changed --id lgate-1-final" in cond["cmd"] and cond["skip_code"] == 3


def test_prod_plans_without_condition_keeps_the_request_unconditional(org):
    """#3083 の受け入れ条件 4 / I2: 条件を省いた呼び出し（new sprint・pace: fast の本番）のリリースの依頼は実行条件を持たない。"""
    a = argparse.Namespace(issue=[11], mode="standard")
    plan = sprint.prod_plans(a, "/r", lambda: pytest.fail("レーンでは本番のプランを組まない"))["release-request"]
    assert "実行の条件" not in plan


def run_close_request(tmp_path, org, code: int, prs: list[str]) -> dict:
    """実行条件の終了コードを code にしたまとめのリリースの依頼のプランを、前のステージの Pull Request prs で埋めてから queue で流す。"""
    a = argparse.Namespace(issue=[11], mode="standard")
    cond = {"cmd": f"exit {code}", "skip_code": 3}
    plan = sprint.prod_plans(a, str(tmp_path), lambda: pytest.fail("レーンでは本番のプランを組まない"), condition=cond)
    path = tmp_path / "prod.json"
    path.write_text(json.dumps(plan["release-request"], ensure_ascii=False), encoding="utf-8")
    assert queue.fill_queue_prs(str(path), prs) is None  # Pull Request が無くても実行条件に判断を任せて止めない
    return queue.cmd_queue([str(path)], 1, poll=0.05)["items"][0]


@pytest.mark.parametrize("prs", [["3079"], []], ids=["closed-pr", "no-pr"])
def test_lane_close_request_is_skipped_when_the_final_check_has_no_change(tmp_path, org, prs):
    """#3083 の受け入れ条件 2: 最終の検査が変更なし（終了コード 3）なら依頼のプランは完了で飛び、依頼を送らない。"""
    item = run_close_request(tmp_path, org, 3, prs)
    assert item["result"] == "完了", item
    assert not (org / "requests").exists() or not list((org / "requests").glob("*.json"))


def test_lane_close_request_is_sent_when_the_final_check_changed(tmp_path, org):
    """#3083 の受け入れ条件 3: 最終の検査で変更があれば（終了コード 0）今どおり依頼を送って承認ゲートで終わる。"""
    item = run_close_request(tmp_path, org, 0, ["3079"])
    assert item["result"] == "関門", item
    assert list((org / "requests").glob("*.json"))
