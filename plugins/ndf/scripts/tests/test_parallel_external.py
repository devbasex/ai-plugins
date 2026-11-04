"""外のオーガナイザーとファイルでやり取りする切り替え口（#2439。`contracts/forms/parallel-external.json`）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_external as pe  # noqa: E402
import parallel_lane as pl  # noqa: E402

FORM = json.loads((ROOT / "contracts" / "forms" / "parallel-external.json").read_text(encoding="utf-8"))
PARALLEL = SCRIPTS / "parallel.py"


@pytest.mark.parametrize("case", FORM["cases"], ids=lambda c: f"{c['message']['file']}-{c['valid']}")
def test_form_cases(case):
    assert (pe.file_errors(case["message"]) == []) is case["valid"]


@pytest.fixture
def org(tmp_path, monkeypatch):
    d = tmp_path / "organizer"
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(d))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "loop-l20261102")
    monkeypatch.delenv("NDF_RELAY_NAME", raising=False)
    return d


def test_lane_of_is_external_only_with_organizer_dir(tmp_path, org, monkeypatch):
    got = pl.lane_of(tmp_path)
    assert got is not None and got[0] == pe.EXTERNAL and got[1].name == "loop-l20261102"
    monkeypatch.delenv("NDF_ORGANIZER_DIR")
    assert pl.lane_of(tmp_path) is None


def test_external_lane_does_not_send_questions(tmp_path, org):
    # 設計の承認と承認ゲートの外の問いは依頼にしない（今どおり止まって人へ戻す）
    assert pl.sender_of(tmp_path, "loop-l20261102", None) is None
    with pytest.raises(ValueError):
        pl.request_gate1(tmp_path, pe.EXTERNAL, "loop-l20261102", 1, 2, "r", "s", "0" * 12)


def _run(args, cwd, env_extra=None):
    import os

    env = {**os.environ, **(env_extra or {})}
    got = subprocess.run([sys.executable, str(PARALLEL), *args], cwd=cwd, capture_output=True, text=True, env=env)
    return got.returncode, json.loads(got.stdout.strip().splitlines()[-1])


def test_request_and_await_through_files(tmp_path, org):
    state = tmp_path / "plan-state"
    code, out = _run(
        ["request", "--external", "--lane", "loop-l20261102", "--state", str(state), "--prs", "2440", "--issues", "2439"], tmp_path
    )
    assert code == 10, out
    rid = out["metrics"]["id"]
    body = json.loads((org / "requests" / f"{rid}.json").read_text())
    assert pe.file_errors({"file": f"requests/{rid}.json", "body": body}) == []
    # 打ち直しても同じ id
    code, again = _run(
        ["request", "--external", "--lane", "loop-l20261102", "--state", str(state), "--prs", "2440", "--issues", "2439"], tmp_path
    )
    assert again["metrics"]["id"] == rid
    sent = state / "work" / "request.txt"
    code, out = _run(["await", "--external", "--from", str(sent)], tmp_path)
    assert code == 3
    pe.put_json(
        org / "answers" / f"{rid}.json",
        {"result": "released", "version": "10.28.0", "release": "https://x/pull/1", "reason": None, "at": "t"},
    )
    code, out = _run(["await", "--external", "--from", str(sent)], tmp_path)
    assert code == 0 and out["metrics"]["version"] == "10.28.0"
    # ループの周回の境は計画名 --external で答えを読む
    assert pl.answers_of(tmp_path, pe.EXTERNAL, [rid])[rid]["answer"]["result"] == "released"


@pytest.mark.parametrize("cmd", [["init", "p1"], ["run", "p1"], ["start", "p1"]])
def test_factory_run_refuses_local_organizer(tmp_path, cmd):
    code, out = _run(cmd, tmp_path, {"NDF_FACTORY_RUN": "1"})
    assert code == 1 and out["metrics"]["factory_run"] is True


BATCH = {
    "seq": 3,
    "step": "present",
    "lanes": [
        {"lane": "loop-l1", "requests": [{"id": "loop-l1-r000000000001", "prs": [11]}], "issues": [1]},
        {"lane": "run-ab", "requests": [], "issues": [2]},
    ],
    "presentations": [],
    "choices": ["continue", "reject"],
}


def _builder(main, name, d, version):
    return {"steps": [], "version": version, "name": name}, {"steps": [], "dir": str(d)}


def test_compose_present_represent_and_confirm(tmp_path):
    d = tmp_path / "org"
    task = pe.compose(tmp_path, d, BATCH, "SV", "PAR", lambda _m: "10.28.0", builder=_builder)
    assert task["k"] == 1 and (Path(task["dir"]) / "present.json").is_file()
    assert "present.json --then" in task["pipeline"] and task["settle"].startswith("PAR settle --external 3")
    task2 = pe.compose(tmp_path, d, {**BATCH, "step": "represent"}, "SV", "PAR", lambda _m: "10.28.0", builder=_builder)
    assert task2["k"] == 2 and Path(task2["dir"]).name == "2"
    # 確定した提示 1 の本番のプランだけを流す（提示 2 の本番のプランを流さない）
    task3 = pe.compose(tmp_path, d, {**BATCH, "step": "confirm:1"}, "SV", "PAR", lambda _m: "10.28.0", builder=_builder)
    assert str(pe.batch_release_dir(d, 3, 1) / "release.json") in task3["pipeline"]
    assert str(pe.batch_release_dir(d, 3, 2)) not in task3["pipeline"]
    with pytest.raises(ValueError):
        pe.compose(tmp_path, d, BATCH, "SV", "PAR", lambda _m: "", builder=_builder)


def _fake_run(merged: bool):
    def run(args, **_kw):
        if args[:3] == ["gh", "pr", "list"]:
            if "--state" in args and args[args.index("--state") + 1] == "merged":
                return SimpleNamespace(returncode=0, stdout="https://github.com/o/r/pull/900\n")
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"state": "MERGED" if merged else "OPEN", "url": "u"}]))
        return SimpleNamespace(returncode=0, stdout="")

    return run


def _presented(d: Path, k: int = 1) -> None:
    rd = pe.batch_release_dir(d, 3, k)
    pe.put_json(rd / "version.json", {"version": "10.28.0"})
    pe.put_json(
        rd / "presented.json",
        {
            "sha": "0123abcd",
            "prs": [11, 12],
            "lanes": {"loop-l1": {"prs": [11], "requested": True}, "run-ab": {"prs": [12], "requested": False}},
            "orphans": [],
            "added": None,
        },
    )


def test_settle_writes_main_merged_and_auto_presented(tmp_path):
    d = tmp_path / "org"
    _presented(d)
    got = pe.settle_batch(tmp_path, d, BATCH, 3, {"items": [{"result": "完了"}]}, run=_fake_run(True))
    assert got["result"] == "released" and got["prod_pr_url"] == "https://github.com/o/r/pull/900"
    assert "main_merged" in pe.read_json(d / "progress" / "3.json")["stages"]
    # MVV 判定が通して承認ゲート 2 で止まらなかった提示は auto 真で書き返す
    assert pe.read_json(d / "presented" / "3-1.json")["auto"] is True
    for f in ("progress/3.json", "results/3.json", "presented/3-1.json"):
        assert pe.file_errors({"file": f, "body": pe.read_json(d / f)}) == []


def test_settle_refuses_when_release_plan_stopped_at_gate(tmp_path):
    d = tmp_path / "org"
    _presented(d)
    with pytest.raises(ValueError):
        pe.settle_batch(tmp_path, d, BATCH, 3, {"items": [{"result": "関門"}]}, run=_fake_run(False))
    assert not (d / "results" / "3.json").exists()


def test_record_presented_on_gate_stop(tmp_path):
    d = tmp_path / "org"
    _presented(d)
    got = pe.record_presented(d, 3, 1, auto=False)
    assert got["auto"] is False and got["lanes"] == [{"lane": "loop-l1", "requested": True}, {"lane": "run-ab", "requested": False}]


def test_released_version_reads_ranges(tmp_path):
    pe.put_json(tmp_path / "ranges" / "1.json", {"sha": "a", "version": "10.27.0", "prs": [5], "lanes": []})
    pe.put_json(tmp_path / "ranges" / "2.json", {"sha": "b", "version": "10.28.0", "prs": [11], "lanes": []})
    assert pe.range_version(tmp_path, [11]) == "10.28.0"
    assert pe.range_version(tmp_path, [99]) is None
