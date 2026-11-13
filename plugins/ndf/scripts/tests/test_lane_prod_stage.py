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
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import parallel_release  # noqa: E402
import progress_activities as acts  # noqa: E402
from supervise_lib import queue  # noqa: E402

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


def prod_stage(waves) -> list[dict]:
    return [load(p) for w in waves if w["name"] == "本番" for p in w.get("plans", [])]


def run_request(tmp_path, org, issues) -> tuple[dict, dict]:
    """リリースの依頼のプランを組んで queue で流す。(依頼のファイル, 依頼のプラン) を返す。"""
    a = argparse.Namespace(issue=list(issues), mode="standard")
    plan = parallel_release.plan_release_request(a, str(tmp_path), "--external", LANE)
    path = tmp_path / "prod.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    res = queue.cmd_queue([str(path)], 1, poll=0.05)
    assert res["items"][0]["result"] == "関門", res
    sent = json.loads((tmp_path / "prod-state" / "work" / "request.txt").read_text())
    return load(org / "requests" / f"{sent['id']}.json"), load(path)


def test_request_without_dropping_keeps_every_issue(tmp_path, org):
    """受け入れ条件 8 / I6: 課題を外さなかったループの 1 回の依頼は、組んだときの全件を載せる。"""
    body, plan = run_request(tmp_path, org, (153, 191, 117))
    assert plan["課題"] == [153, 191, 117] and body["issues"] == [153, 191, 117]


@pytest.mark.parametrize("issues", [[153], [153, 191, 117], []])
def test_the_release_request_of_the_table_carries_the_issues_of_its_range(tmp_path, monkeypatch, issues):
    """受け入れ条件 4・5 / I3（#3092）: 進行表のデプロイの工程の依頼は、その時点の進行表の範囲の課題だけを載せる。設計承認ゲートで
    外した課題は `progress.py scope --drop` で範囲から外れ、工程の入力の issues に入らない。課題が無ければ --issues を付けない。"""
    seen = []

    def run(cmd, capture_output=True, text=True, cwd=None):
        seen.append(cmd)
        state = Path(cmd[cmd.index("--state") + 1])
        (state / "work" / "request.txt").write_text(json.dumps({"id": "la-r1", "plan": "p1"}))
        return type("P", (), {"returncode": 10, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(acts, "_lane_of", lambda store, table_id: ("p1", "la"))
    monkeypatch.setattr(acts.subprocess, "run", run)
    out = acts.act_request(None, "t1", {"work": str(tmp_path), "issues": issues, "previous": {}}, "デプロイ/release")
    cmd = seen[0]
    assert out["metrics"]["requested"] is True and "await p1 --from" in out["metrics"]["next_step"]
    if issues:
        assert cmd[cmd.index("--issues") + 1 :] == [str(n) for n in issues]
    else:
        assert "--issues" not in cmd
    record = json.loads((tmp_path / "release-request" / "work" / "request.txt").read_text())
    assert (record["table"], record["row"]) == ("t1", "デプロイ/release")  # parallel.py await が答えを届ける先


def test_request_with_no_issues_is_not_refused(tmp_path, org):
    """差し込みの境界: `課題` が空なら `--issues` は空で、request は断らずに依頼を書く。"""
    body, _ = run_request(tmp_path, org, ())
    assert body["issues"] == []
