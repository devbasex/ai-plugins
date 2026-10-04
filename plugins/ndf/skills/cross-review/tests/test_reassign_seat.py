"""結果を残さなかった席の振り替え（#919 の AC6〜AC10・AC18、I2・I6・I8）。

| ラウンドの結果なし | 利用可能な参加者 | 出口 |
| --- | --- | --- |
| 1 席が `usage_limit` | 3 者 | 残りの 1 者へ振り替えて 7（`REASSIGNED`） |
| 1 席が `stalled`（1 度目） | 3 者 | 同じ席で起動し直して 7（`RELAUNCH_AGENTS`） |
| 1 席が `stalled`（起動し直した後） | 3 者 | 振り替えて 7 |
| 1 席が `usage_limit` | 2 者・`--only` | `final = error`・1 |

駆動（`drive.py`）を通す結合テストは、judge を本物の `cmd_judge` で打ち、起動と結果の取り込みだけを偽にする。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib

import pytest
import review_lib.commands.judge
import review_lib.commands.start_round
import review_lib.participants

PR = 5
THREE = ["claude", "codex", "kiro"]


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _participants(available: list[str], **over) -> dict:
    p = {
        "pool": list(THREE),
        "included": [],
        "excluded": [],
        "ignored_exclude": [],
        "available": list(available),
        "unavailable": {},
        "probe_skipped": True,
        "require_all": False,
        "policy": None,
        "fallback": [],
    }
    p.update(over)
    return p


def _state(rounds: list[dict], available=THREE, **over) -> dict:
    st = {
        "current_pr": PR,
        "repo": "o/r",
        "host": "claude",
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": rounds,
        "pr_history": [{"pr": PR, "rounds": len(rounds)}],
        "deferred_nits": [],
        "final": None,
        "participants": _participants(available),
    }
    st.update(over)
    return st


def _round(reviewers: list[str], no: int = 1, **seats) -> dict:
    entry = {
        "round": no,
        "pr": PR,
        "started_at": "2026-10-04T00:00:00+00:00",
        "reviewers": list(reviewers),
        "seats": review_lib.participants.seat_records(list(reviewers)),
    }
    entry.update(seats)
    return entry


def _approve() -> dict:
    return {"intent": "APPROVE", "posted_as": "APPROVE", "comments": 0, "by_severity": {"critical": 0, "major": 0}}


def _no_result(reason: str) -> dict:
    return {"intent": "NO_RESULT", "no_result_reason": reason, "posted_as": None, "comments": None, "review_url": None, "by_severity": {}}


def _write(tmp: pathlib.Path, st: dict) -> None:
    (tmp / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st))


def _read(tmp: pathlib.Path) -> dict:
    return json.loads((tmp / f"cross-review-pr{PR}-state.json").read_text())


def _judge() -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            review_lib.commands.judge.cmd_judge(argparse.Namespace(pr=PR))
            code = 0
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


# ---------------- judge ----------------


def test_a_usage_limit_seat_moves_to_the_remaining_participant(tmp_dir):
    """AC6・AC10・I6: 3 者で 1 席が利用上限なら、残りの 1 者へ振り替えて 7。席が重ならない。"""
    _write(tmp_dir, _state([_round(["codex", "kiro"], codex=_approve(), kiro=_no_result("usage_limit"))]))

    code, out, _ = _judge()

    assert code == 7
    assert "REASSIGNED='kiro=claude:usage_limit'" in out
    assert "RELAUNCH_AGENTS" not in out
    st = _read(tmp_dir)
    last = st["rounds"][-1]
    assert last["reviewers"] == ["codex", "claude"]
    assert [s["seat"] for s in last["seats"]] == ["codex", "claude"]
    assert last["kiro"]["intent"] == "NO_RESULT"  # 元の席の欄は残す
    assert last["reassigned"] == [{"from": "kiro", "to": "claude", "to_account": "", "reason": "usage_limit"}]
    assert st["final"] is None
    assert [(e["seat"], e["reason"], e["decision"], e["to"]) for e in st["no_results"]] == [("kiro", "usage_limit", "reassign", "claude")]


def test_a_first_relaunchable_no_result_relaunches_the_same_seat(tmp_dir):
    """AC18: `usage_limit` 以外の 1 度目は同じ席で 1 度だけ起動し直す（出力の形は今のまま）。"""
    _write(tmp_dir, _state([_round(["codex", "kiro"], codex=_approve(), kiro=_no_result("stalled"))]))

    code, out, _ = _judge()

    assert code == 7
    assert "RELAUNCH_AGENTS='kiro'" in out
    assert "RELAUNCH_AGENTS_CSV=kiro" in out
    assert "RELAUNCH_TARGET=kiro" in out
    assert "REASSIGNED" not in out
    assert _read(tmp_dir)["rounds"][-1]["relaunched"] == ["kiro"]


def test_a_second_no_result_after_the_relaunch_moves_the_seat(tmp_dir):
    """AC7: 起動し直した後も結果が無い席は振り替える。"""
    _write(tmp_dir, _state([_round(["codex", "kiro"], codex=_approve(), kiro=_no_result("stalled"))]))
    assert _judge()[0] == 7
    st = _read(tmp_dir)
    st["rounds"][-1]["kiro"] = _no_result("stalled")  # 起動し直した後の read-result
    _write(tmp_dir, st)

    code, out, _ = _judge()

    assert code == 7
    assert "REASSIGNED='kiro=claude:stalled'" in out
    assert "RELAUNCH_AGENTS" not in out
    assert [e["decision"] for e in _read(tmp_dir)["no_results"]] == ["relaunch", "reassign"]


@pytest.mark.parametrize(
    ("available", "only"),
    [(["codex", "kiro"], None), (["kiro"], "kiro")],
    ids=["two-participants", "only"],
)
def test_without_a_candidate_the_round_stops_with_the_tried_agents(tmp_dir, available, only):
    """AC9・I8: 振り替え先が無い（2 者・1 者指定）ときは `final = error`・1 で、試した担当と理由が並ぶ。"""
    reviewers = ["codex", "kiro"] if only is None else ["kiro"]
    seats = {"codex": _approve()} if only is None else {}
    _write(tmp_dir, _state([_round(reviewers, kiro=_no_result("usage_limit"), **seats)], available=available, only=only))

    code, out, err = _judge()

    assert code == 1
    assert "REASSIGNED" not in out
    st = _read(tmp_dir)
    assert st["final"] == "error"
    assert "kiro=usage_limit→abort" in err
    assert st["no_results"][-1]["decision"] == "abort"


def test_two_failing_seats_never_share_a_runtime(tmp_dir):
    """I6: 2 席とも利用上限なら、1 席目の振り替え先は 2 席目の候補から外れ、2 席目は中断になる。"""
    _write(tmp_dir, _state([_round(["codex", "kiro"], codex=_no_result("usage_limit"), kiro=_no_result("usage_limit"))]))

    code, _, err = _judge()

    assert code == 1
    assert "codex=usage_limit→reassign" in err
    assert "kiro=usage_limit→abort" in err


def test_claude_moves_to_another_account_on_the_same_seat(tmp_dir, monkeypatch):
    """AC4: claude の利用上限で使えるアカウントがあれば、同じ席のままそのアカウントで起動する。"""
    monkeypatch.setattr(review_lib.commands.judge.assignee_env, "account_picker", lambda base=None: lambda tried: "work2")
    _write(tmp_dir, _state([_round(["claude", "codex"], codex=_approve(), claude=_no_result("usage_limit"))]))

    code, out, _ = _judge()

    assert code == 7
    assert "REASSIGNED='claude=claude@work2:usage_limit'" in out
    last = _read(tmp_dir)["rounds"][-1]
    assert last["reviewers"] == ["claude", "codex"]
    assert last["seats"][0]["account"] == "work2"


# ---------------- 以後のラウンドの席（AC8・I2） ----------------


def test_a_dropped_runtime_is_left_out_of_later_rounds(tmp_dir):
    """AC8・AC2: 利用上限で外したランタイムは、以後のラウンドの席に出ない（-2 の席も）。"""
    log = [
        {
            "step": "review",
            "attempt": 1,
            "seat": "kiro",
            "account": "",
            "reason": "usage_limit",
            "decision": "reassign",
            "to": "claude",
            "to_account": "",
            "at": "t",
        }
    ]
    st = _state([], no_results=log)
    for round_no in range(1, 6):
        seats = review_lib.participants._round_reviewers(st, round_no)
        assert all(s.split("-")[0] != "kiro" for s in seats), seats
    st = _state([], available=["claude", "kiro"], no_results=log)
    assert review_lib.participants._round_reviewers(st, 2) == ["claude", "claude-2"]


def test_pinned_seats_are_not_used_once_one_side_was_replaced(tmp_dir):
    """前提 6: 固定の組の片方が振り替えの元になったら、表の規則へ戻る。"""
    policy = {"allowed": THREE, "review_seats": ["claude", "kiro"], "path": ".ndf/runtimes.json"}
    st = _state([], participants=_participants(THREE, policy=policy))
    assert review_lib.participants._round_reviewers(st, 1) == ["claude", "kiro"]
    st["no_results"] = [
        {
            "step": "review",
            "attempt": 1,
            "seat": "kiro",
            "account": "",
            "reason": "usage_limit",
            "decision": "reassign",
            "to": "codex",
            "to_account": "",
            "at": "t",
        }
    ]
    assert review_lib.participants._round_reviewers(st, 2) == ["claude", "codex"]


# ---------------- drive.py を通す（AC6・AC7） ----------------


class FakeLaunches:
    """起動・監視・結果の取り込みを偽にし、judge は本物を打つ。`reads` は席ごとの結果の並び。"""

    def __init__(self, tmp: pathlib.Path, reads: dict[str, list[dict]]):
        self.tmp = tmp
        self.reads = reads
        self.calls: list[tuple] = []

    def __call__(self, cmd, env=None, cwd=None):
        name = pathlib.Path(cmd[1]).name if len(cmd) > 1 else cmd[0]
        args = list(cmd[2:])
        self.calls.append((name, *args))
        if name != "state.py":
            return 0, ""
        sub = args[0]
        if sub == "init":
            return 0, f"PR={PR}\nTMP_DIR={self.tmp}\nWORKTREE={self.tmp / 'wt'}\nREPO=o/r\n"
        if sub == "read-result":
            seat = args[2]
            st = _read(self.tmp)
            got = self.reads[seat].pop(0)
            st["rounds"][-1][seat] = got
            _write(self.tmp, st)
            return (0 if got.get("intent") == "APPROVE" else 1), ""
        if sub == "judge":
            code, out, _ = _judge()
            return code, out
        return 0, ""


@pytest.mark.parametrize(
    ("kiro_reads", "decisions"),
    [([_no_result("usage_limit")], ["reassign"]), ([_no_result("stalled"), _no_result("stalled")], ["relaunch", "reassign"])],
    ids=["usage-limit", "stalled-twice"],
)
def test_drive_runs_the_reassigned_seat_and_moves_on(tmp_dir, monkeypatch, kiro_reads, decisions):
    """AC6・AC7: drive は振り替え先の席を起動して judge し直し、`final = error` にならずに先へ進む。"""
    import test_review_drive_resume as resume

    _write(tmp_dir, _state([_round(["codex", "kiro"])]))
    fake = FakeLaunches(tmp_dir, {"codex": [_approve()], "kiro": list(kiro_reads), "claude": [_approve()]})
    d = resume.cr.Drive(PR, "light", [])
    monkeypatch.setattr(resume.cr, "call", fake)
    d.adopt(d.init())

    rc, _ = d.collect_reviews({"ROUND": "1", "REVIEWERS": "codex kiro"})

    assert rc == 0
    st = _read(tmp_dir)
    assert st["final"] == "approved"
    assert [e["decision"] for e in st["no_results"]] == decisions
    launched = [c[1] for c in fake.calls if c[0] == "launch-reviewer.sh"]
    assert launched.count("claude") == 1
    # 反証は振り替えた後の席で 1 度だけ通す
    critiques = [c for c in fake.calls if c[0] == "critique-round.sh"]
    assert len(critiques) == 1 and critiques[0][3:] == ("codex", "claude")


def test_the_report_lists_reassignments_and_dropped_agents(tmp_dir, capsys):
    """F7: 完了報告に振り替えの行と外した担当の行が出る。"""
    import review_lib.commands.report

    log = [
        {
            "step": "review",
            "attempt": 2,
            "seat": "kiro",
            "account": "",
            "reason": "usage_limit",
            "decision": "reassign",
            "to": "claude",
            "to_account": "",
            "at": "t",
        }
    ]
    _write(
        tmp_dir,
        _state([_round(["codex", "claude"], codex=_approve(), claude=_approve(), verdict="approved")], final="approved", no_results=log),
    )

    review_lib.commands.report.cmd_report(argparse.Namespace(pr=PR))

    out = capsys.readouterr().out
    assert "round 2: kiro → claude（usage_limit）" in out
    assert "- 外した担当: kiro" in out


@pytest.mark.parametrize(
    ("src", "to", "seat"),
    [
        ("kiro", "claude@work1", "claude"),
        ("claude-2", "claude@work2", "claude-2"),
        ("claude", "claude@work2", "claude"),
        ("kiro", "codex", "codex"),
    ],
    ids=["other-runtime-to-claude-account", "claude-seat-keeps-its-seat", "claude-account", "other-runtime"],
)
def test_drive_launches_the_seat_of_the_reassignment(src, to, seat):
    """`claude@<名前>` は元の席が claude のときだけ元の席を起動し直し、別のランタイムからなら宛先の席を起動する。"""
    import test_review_drive_resume as resume

    assert resume.cr._reassigned_seat(src, to) == seat
