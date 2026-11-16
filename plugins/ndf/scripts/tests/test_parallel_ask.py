"""`parallel.py request-ask` の送り分け（`lib/parallel_ask.py`。#3603）: 送り手の種類・問いの対象・同じ問いの答えの引き継ぎ・
設計承認の依頼への振り分け・上限の秒。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parallel_ask as pask  # noqa: E402
import parallel_lane as pl  # noqa: E402
from parallel_lane_world import parallel_cli as _cli, plan_main as _main, run_cli as _run  # noqa: E402


# ---- #3603: request-ask の送り分け（設計承認の依頼・同じ問い・ask の依頼） ----


def _git(repo: Path, *args: str) -> str:
    import subprocess

    got = subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a")
    _git(repo, "branch", "sprint/lr-t2")
    return repo, _git(repo, "rev-parse", "sprint/lr-t2")


def _ask(mod, capsys, monkeypatch, lane: str, ledger_id: str, question: str, cwd: Path, source="ndf-ask:redline:4", **extra):
    import io

    if not getattr(pl.sender_of, "_running", False):  # 既定の引数の run_status は差し替えが効かないため、動いている計画として判定する
        orig = pl.sender_of
        fake = lambda main, name, relay_dir: orig(main, name, relay_dir, status=lambda _m, _n: "PENDING")  # noqa: E731
        fake._running = True
        monkeypatch.setattr(pl, "sender_of", fake)

    ti = {"questions": [{"question": question, "options": [{"label": "消す"}, {"label": "残す"}]}], "metadata": {"source": source}}
    payload = {"tool_input": ti, "cwd": str(cwd), "ledger_id": ledger_id, **extra}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    return _run(mod, capsys, "request-ask", "p1", "--lane", lane)


def _helper(monkeypatch, tmp_path: Path, plan: str = "p1") -> None:
    relay = tmp_path / f"relay-{plan}"
    relay.mkdir(exist_ok=True)
    (relay / "role.json").write_text(json.dumps({"role": "helper", "plan": plan}))
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))


def _rows(plg, main: Path, kind: str) -> list[dict]:
    return [r for r in plg.read_ledger_rows(main, "p1").rows if r["kind"] == kind]


Q = "後片付けで sprint/lr-t2 を git branch -D で消してよいか。\n対象: sprint/lr-t2"


def test_sender_kind_reads_the_plan_lanes(tmp_path):
    main = _main(tmp_path)
    assert pask.sender_kind(main, "p1", "la") == "sprint"
    assert pask.sender_kind(main, "p1", "p1-rel1") == "helper"
    assert pask.sender_kind(tmp_path / "none", "p1", "la") == "helper"


def test_ask_target_adds_the_tip_and_keeps_numbers(tmp_path):
    """受け入れ条件 17・18: ブランチ名は先端の 40 桁を足し、#<番号> はそのまま、形に合わない・無いブランチは空。"""
    repo, sha = _repo(tmp_path)
    t = lambda q: pask.ask_target({"questions": [{"question": q}]}, str(repo)).target  # noqa: E731
    assert t(Q) == f"sprint/lr-t2@{sha}"
    assert t("対象: #3603") == "#3603"
    assert t("対象: no/such") == "" and t("対象: bad..name") == "" and t("対象の行が無い") == ""


def test_record_reused_writes_one_row_and_no_notice(tmp_path, monkeypatch):
    """I8: reused の行は 1 行で、同じ鍵の打ち直しで増えず、耐久ワークフローへ知らせない。"""
    import parallel_ledger as plg
    import parallel_question as pq

    seen = []
    monkeypatch.setattr(pl, "record_exists", lambda main, name: seen.append(name) or False)
    prior = pq.Prior("p1-rel1-qa", None)
    assert pask.record_reused(tmp_path, "p1", "p1-rel1", prior, "ndf-ask:cost", "#1", "L1")
    assert pask.record_reused(tmp_path, "p1", "p1-rel1", prior, "ndf-ask:cost", "#1", "L1")
    rows = _rows(plg, tmp_path, "reused")
    assert len(rows) == 1 and rows[0]["msg"]["answered"] is False and seen == []
    assert "reused" in plg.RECORD


def test_record_reused_returns_false_when_the_ledger_is_unwritable(tmp_path, monkeypatch):
    import parallel_ledger as plg
    import parallel_question as pq

    def broken(*a, **k):
        raise OSError("ro")

    monkeypatch.setattr(plg, "append_ledger_row", broken)
    assert not pask.record_reused(tmp_path, "p1", "p1-rel1", pq.Prior("x", None), "s", "#1", "L1")


def test_a_helper_reuses_the_answer_of_the_same_question(monkeypatch, tmp_path, capsys):
    """受け入れ条件 13・15・17: 同じ一時レーンの同じ区分・対象の問いは、新しい依頼を書かずに前の答え（答え待ちなら前の id）を返す。"""
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    repo, sha = _repo(tmp_path)
    _helper(monkeypatch, tmp_path)
    code, out = _ask(mod, capsys, monkeypatch, "p1-rel1", "L1", Q, repo)
    rid = out["metrics"]["id"]
    assert code == 0 and out["metrics"]["kind"] == "ask" and out["metrics"]["target"] == f"sprint/lr-t2@{sha}"
    assert _rows(plg, main, "ask")[0]["msg"]["target"] == f"sprint/lr-t2@{sha}"
    code, out = _ask(mod, capsys, monkeypatch, "p1-rel1", "L2", Q, repo)  # 答え待ちのまま起き直した
    assert code == 0 and out["metrics"]["kind"] == "reused" and out["metrics"]["id"] == rid and "answer" not in out["metrics"]
    pl.send_answer(main, "p1", rid, "answered", choice=["消す"])
    code, out = _ask(mod, capsys, monkeypatch, "p1-rel1", "L3", Q, repo)
    assert out["metrics"]["kind"] == "reused" and out["metrics"]["answer"] == {"result": "answered", "choice": ["消す"]}
    assert len(_rows(plg, main, "ask")) == 1 and len(_rows(plg, main, "reused")) == 2


def test_a_helper_asks_again_for_a_moved_tip_another_helper_or_no_target(monkeypatch, tmp_path, capsys):
    """受け入れ条件 13・14・16・18: 先端が動いた・別の名前の一時レーン・対象の無い問い・同じレッドラインの区分と先端で中身の違う問いは、
    前の答えを使わずに新しい依頼にする。"""
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    repo, _ = _repo(tmp_path)
    _helper(monkeypatch, tmp_path)
    _, out = _ask(mod, capsys, monkeypatch, "p1-rel1", "L1", Q, repo)
    pl.send_answer(main, "p1", out["metrics"]["id"], "answered", choice=["消す"])
    assert _ask(mod, capsys, monkeypatch, "p1-rel2", "L2", Q, repo)[1]["metrics"]["kind"] == "ask"
    other = "強制 push してよいか。\n" + Q.splitlines()[-1]  # 同じ対象の行・同じ区分 redline:4 で中身の違う問い（PR #3652 の指摘）
    assert _ask(mod, capsys, monkeypatch, "p1-rel1", "L5", other, repo)[1]["metrics"]["kind"] == "ask"
    got = _ask(mod, capsys, monkeypatch, "p1-rel1", "L3", "対象の行の無い問い", repo)[1]["metrics"]
    assert got["kind"] == "ask" and got["target"] is None
    _git(repo, "checkout", "-q", "sprint/lr-t2")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "b")
    assert _ask(mod, capsys, monkeypatch, "p1-rel1", "L4", Q, repo)[1]["metrics"]["kind"] == "ask"
    assert len(_rows(plg, main, "ask")) == 5 and _rows(plg, main, "reused") == []


def test_a_sprint_lane_does_not_reuse_answers(monkeypatch, tmp_path, capsys):
    """I5: 1 スプリントのレーンの対象の行つきの問いは、答え済みの同じ対象の依頼があっても新しい ask にする。"""
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    repo, _ = _repo(tmp_path)
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    _, out = _ask(mod, capsys, monkeypatch, "la", "L1", Q, repo)
    pl.send_answer(main, "p1", out["metrics"]["id"], "answered", choice=["消す"])
    assert _ask(mod, capsys, monkeypatch, "la", "L2", Q, repo)[1]["metrics"]["kind"] == "ask"
    assert len(_rows(plg, main, "ask")) == 2


def _gate(**kw):
    import parallel_design_gate as pdg

    base = dict(issue=7, pr=12, stamp="0123456789ab" + "c" * 28, report="/r.md", state="/w/s.json", verdict="unknown", reasons=["r"])
    return pdg.DesignGate(**{**base, **kw})


def test_a_sprint_lane_design_approval_becomes_a_gate1_request(monkeypatch, tmp_path, capsys):
    """受け入れ条件 1・8: 設計承認の依頼は gate1 の行（issue・pr・stamp）を 1 行書き、ask を書かない。同じ先頭の打ち直しは同じ id で増えない。"""
    import parallel_design_gate as pdg
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    monkeypatch.setattr(pdg, "design_gate", lambda *a, **k: _gate())
    q = "設計 PR #12 をマージしてよいか。\n承認: #12"
    code, out = _ask(mod, capsys, monkeypatch, "la", "L1", q, tmp_path, source="ndf-gate:1")
    assert code == 0 and out["metrics"]["kind"] == "gate1" and out["metrics"]["id"] == "la-g12-0123456789ab"
    assert out["metrics"]["sprint_state"] == "/w/s.json"
    rows = _rows(plg, main, "gate1")
    assert len(rows) == 1 and _rows(plg, main, "ask") == []
    msg = rows[0]["msg"]
    assert (msg["issue"], msg["pr"], msg["stamp"], msg["ledger_id"]) == (7, 12, "0123456789ab" + "c" * 28, "L1")
    code, out = _ask(mod, capsys, monkeypatch, "la", "L2", q, tmp_path, source="ndf-gate:1")
    assert out["metrics"]["already"] is True and len(_rows(plg, main, "gate1")) == 1


def test_a_returned_design_request_carries_the_suffix_and_mark(monkeypatch, tmp_path, capsys):
    """I4: 印 returned の依頼は接尾辞つきの id で、returned: true を持つ。"""
    import parallel_design_gate as pdg
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    monkeypatch.setattr(pdg, "design_gate", lambda *a, **k: _gate(suffix="-r1", returned=True))
    _, out = _ask(mod, capsys, monkeypatch, "la", "L1", "承認: #12", tmp_path, source="ndf-gate:1")
    assert out["metrics"]["id"] == "la-g12-0123456789ab-r1" and _rows(plg, main, "gate1")[0]["msg"]["returned"] is True


@pytest.mark.parametrize("lane, helper", [("la", False), ("p1-rel1", True)])
def test_without_a_design_gate_the_question_stays_an_ask(monkeypatch, tmp_path, capsys, lane, helper):
    """受け入れ条件 9・I5: 設計 PR を特定できない ndf-gate:1 の問いと、一時レーンの問いは ask のまま送る。"""
    import parallel_design_gate as pdg
    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    if helper:
        _helper(monkeypatch, tmp_path)
        monkeypatch.setattr(pdg, "design_gate", lambda *a, **k: pytest.fail("一時レーンで設計承認の依頼を組まない"))
    else:
        monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
        monkeypatch.setattr(pdg, "design_gate", lambda *a, **k: None)
    _, out = _ask(mod, capsys, monkeypatch, lane, "L1", "承認: #12", tmp_path, source="ndf-gate:1")
    assert out["metrics"]["kind"] == "ask" and _rows(plg, main, "gate1") == [] and len(_rows(plg, main, "ask")) == 1


def test_a_passed_deadline_writes_nothing(monkeypatch, tmp_path, capsys):
    """I10: deadline_at を過ぎていれば台帳へ書かずに 2（hook は問いを通す）。"""
    import time

    import parallel_ledger as plg

    main = _main(tmp_path)
    mod, _ = _cli(monkeypatch, tmp_path)
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    code, _ = _ask(mod, capsys, monkeypatch, "la", "L1", "どれ", tmp_path, deadline_at=time.time() - 1)
    assert code == 2 and plg.read_ledger_rows(main, "p1").rows == []


@pytest.mark.parametrize("deadline, now, want", [(None, 5, False), ("9", 5, False), (10, 5, False), (10, 10, True), (10, 11, True)])
def test_expired(deadline, now, want):
    assert pask.expired(deadline, lambda: now) is want
