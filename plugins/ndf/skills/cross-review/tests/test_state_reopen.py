"""`final` が確定したレビューにラウンドを足す（#1340 の AC1・AC2・AC4〜AC7、I1〜I6・I9）。

入口は `state.py init` そのものである（決定 1）。`final` が確定していれば `final` を外して `reopens` に 1 件積み、
履歴を残したまま次の番号のラウンドから続ける。上限・巻き直し・振動検知は最後に足した時点より後だけで数える。
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest
import review_lib
import review_lib.commands.init
import review_lib.commands.loop
import review_lib.commands.start_round
import review_lib.findings
import review_lib.github
import review_lib.matching
import review_lib.participants
import review_lib.posts
import review_lib.workspace

PR = 6200
REPO = "acme/demo"
CLOSED_SWEEP = {"verified": True, "remaining_open": 0, "commit": None}


def _round(no: int, verdict: str = "approved", **over) -> dict:
    entry = {
        "round": no,
        "pr": PR,
        "started_at": "2026-09-19T00:00:00+09:00",
        "reviewers": ["codex"],
        "codex": {"intent": "APPROVE" if verdict == "approved" else "REQUEST_CHANGES", "by_severity": {}},
        "verdict": verdict,
    }
    entry.update(over)
    return entry


def _state(tmp_dir: pathlib.Path, **over) -> pathlib.Path:
    st = {
        "started_at": "2026-09-19T00:00:00+09:00",
        "host": "claude",
        "host_source": "explicit",
        "max_rounds": 3,
        "rotate_after": 8,
        "only": None,
        "participants": {
            "pool": ["codex"],
            "included": [],
            "excluded": [],
            "available": ["codex"],
            "unavailable": {},
            "probe_skipped": False,
            "require_all": False,
            "fallback": [],
        },
        "resume_changes": [],
        "current_pr": PR,
        "worktree_path": str(tmp_dir),
        "tmp_dir": str(tmp_dir),
        "repo": REPO,
        "head_branch": "feat/x",
        "base_branch": "develop",
        "auto_review_instructions": "",
        "review_instructions": "",
        "verify_commands": [],
        "verify_exit_codes": [],
        "pr_history": [{"pr": PR, "opened_at": "x", "closed_at": None, "rounds": 0}],
        "rounds": [_round(1, "changes_requested", fix={"commit": "a1"}), _round(2), _round(3)],
        "deferred_nits": [],
        "carried_over": None,
        "final": "approved",
        "ended_at": "2026-09-19T01:00:00+09:00",
        "sweep": dict(CLOSED_SWEEP),
    }
    st.update(over)
    path = tmp_dir / f"cross-review-pr{PR}-state.json"
    path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return path


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))


@pytest.fixture()
def env(state_mod, monkeypatch, tmp_path):
    """init と start-round を、GitHub にも git にも触れずに通す。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: REPO)
    monkeypatch.setattr(review_lib.posts, "_auto_flush", lambda pr: None)
    monkeypatch.setattr(review_lib.findings, "_record_carried_over", lambda *a, **k: False)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda path: False)
    monkeypatch.setattr(review_lib.github, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(review_lib.github, "_fetch_existing_comments", lambda *a, **k: None)
    monkeypatch.setattr(review_lib.github, "_fetch_unresolved_threads", lambda repo, pr: [])
    monkeypatch.setattr(review_lib.commands.start_round, "_sync_before_round", lambda st, pr: None)
    monkeypatch.setattr(
        review_lib.participants.auth,
        "probe_auth",
        lambda runtimes, *, info, env=None: ({r: {"command": r, "ok": True, "detail": ""} for r in runtimes}, False),
    )

    class Env:
        tmp = tmp_path

        @staticmethod
        def init(*argv: str) -> dict:
            args = state_mod.build_parser().parse_args(["init", str(PR), "--worktree", str(tmp_path), *argv])
            review_lib.commands.init.cmd_init(args)
            return _read(tmp_path)

        @staticmethod
        def start_round() -> None:
            review_lib.commands.start_round.cmd_start_round(argparse.Namespace(pr=PR))

    return Env


# ---------------- AC1・AC2: どの final からでも次の番号のラウンドが始まる ----------------


@pytest.mark.parametrize(
    "final, last_verdict",
    [("approved", "approved"), ("error", "changes_requested"), ("oscillation", "changes_requested"), ("max_rounds", "changes_requested")],
)
def test_a_settled_review_gets_the_next_round(env, capsys, final, last_verdict):
    """足す前の rounds はそのまま残り、次のラウンドは前の最後 + 1。後始末の確認で止まらない（I1・I4）。"""
    path = _state(env.tmp, final=final, rounds=[_round(1, "changes_requested", fix={"commit": "a1"}), _round(2), _round(3, last_verdict)])
    before = json.loads(path.read_text(encoding="utf-8"))["rounds"]

    st = env.init()
    assert st["final"] is None
    assert st["rounds"] == before
    err = capsys.readouterr().err
    assert f"final={final}" in err and "round 4 から" in err

    env.start_round()
    out = capsys.readouterr().out
    assert "ROUND=4" in out
    after = _read(env.tmp)
    assert after["rounds"][:3] == before
    assert after["rounds"][3]["round"] == 4


def test_the_reopen_record_keeps_the_previous_end(env):
    """I2: 足した記録は at・from_final・base_round・ended_at・sweep を持ち、状態から ended_at と sweep が消える。"""
    _state(env.tmp)
    st = env.init()
    assert len(st["reopens"]) == 1
    rec = st["reopens"][0]
    assert rec["at"]
    assert rec["from_final"] == "approved"
    assert rec["base_round"] == 3
    assert rec["ended_at"] == "2026-09-19T01:00:00+09:00"
    assert rec["sweep"] == CLOSED_SWEEP
    assert "ended_at" not in st and "sweep" not in st


def test_reopening_twice_keeps_both_records(env):
    """I2: 2 回足すと 2 件。前の記録を置き換えない。"""
    _state(env.tmp)
    env.init()
    st = _read(env.tmp)
    st["final"] = "approved"
    (env.tmp / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st), encoding="utf-8")
    st = env.init()
    assert [r["from_final"] for r in st["reopens"]] == ["approved", "approved"]


# ---------------- AC4・I3: 上限は足した時点から数え直す ----------------


def test_the_round_limit_counts_from_the_reopen(env, capsys):
    _state(env.tmp, final="max_rounds", max_rounds=3, rounds=[_round(1), _round(2), _round(3)])
    env.init()
    for no in (4, 5, 6):
        env.start_round()
        assert f"ROUND={no}" in capsys.readouterr().out
        st = _read(env.tmp)
        st["rounds"][-1]["verdict"] = "approved"
        (env.tmp / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        env.start_round()
    assert e.value.code == 1
    assert _read(env.tmp)["final"] == "max_rounds"


def test_max_rounds_passed_on_reopen_sets_the_new_limit(env, capsys):
    _state(env.tmp, final="max_rounds", max_rounds=3, rounds=[_round(1), _round(2), _round(3)])
    env.init("--max-rounds", "1")
    env.start_round()
    assert "ROUND=4" in capsys.readouterr().out
    st = _read(env.tmp)
    st["rounds"][-1]["verdict"] = "approved"
    (env.tmp / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        env.start_round()
    assert e.value.code == 1


def test_should_rotate_counts_only_rounds_after_the_reopen(env, capsys):
    """I3: 巻き直しの数も境より後だけで数える。"""
    _state(env.tmp, rotate_after=2, rounds=[_round(1), _round(2), _round(3)])
    env.init()
    with pytest.raises(SystemExit) as e:
        review_lib.commands.loop.cmd_should_rotate(argparse.Namespace(pr=PR))
    assert e.value.code == 2


# ---------------- I4: 足す前のラウンドに確認を当てない条件 ----------------


@pytest.mark.parametrize(
    "sweep",
    [None, {"verified": False, "remaining_open": 0}, {"verified": True, "remaining_open": 2}],
    ids=["no-sweep", "unverified", "open-left"],
)
def test_the_previous_round_is_checked_unless_the_sweep_closed(env, sweep):
    over = {"final": "oscillation", "rounds": [_round(1), _round(2, "changes_requested")]}
    if sweep is None:
        _state(env.tmp, **over)
        st = json.loads((env.tmp / f"cross-review-pr{PR}-state.json").read_text())
        st.pop("sweep")
        (env.tmp / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st), encoding="utf-8")
    else:
        _state(env.tmp, sweep=sweep, **over)
    env.init()
    with pytest.raises(SystemExit) as e:
        env.start_round()
    assert e.value.code == 5
    assert len(_read(env.tmp)["rounds"]) == 2


# ---------------- AC5・AC6・I9 ----------------


def test_an_unsettled_state_resumes_as_before(env, capsys):
    """AC5: final=null は今の再開。reopens が増えない。"""
    _state(env.tmp, final=None)
    st = env.init()
    assert "reopens" not in st
    err = capsys.readouterr().err
    assert "↻ 前回中断 state から再開（round=3）" in err
    assert "ラウンドを足す" not in err


def test_a_broken_state_file_stops_without_overwriting(env, capsys):
    """AC6・I9: 読めない状態ファイルはパスと理由を出して終了コード 1。ファイルは変わらない。"""
    path = env.tmp / f"cross-review-pr{PR}-state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        env.init()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert str(path) in err
    assert path.read_text(encoding="utf-8") == "{broken"


def test_a_failed_refresh_leaves_the_settled_state_untouched(env, monkeypatch):
    """E4: 再開の反映が止まれば、状態ファイルは足す前のまま残る。"""
    path = _state(env.tmp)
    before = path.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise SystemExit(1)

    monkeypatch.setattr(review_lib.participants, "_apply_resume_args_block", boom)
    with pytest.raises(SystemExit):
        env.init()
    assert path.read_text(encoding="utf-8") == before


# ---------------- AC7・I5・I6: 振動検知 ----------------


def _oscillate(monkeypatch, keys_by_round: dict[int, set]) -> None:
    monkeypatch.setattr(review_lib.matching, "_finding_keys", lambda st, pr, rnd: keys_by_round.get(rnd, set()))


def test_check_oscillation_keeps_a_settled_final(env, monkeypatch, capsys):
    """AC7: final=approved なら判定せず、final を変えず、終了コード 2。"""
    _state(env.tmp)
    _oscillate(monkeypatch, {2: {("a.py", 1, "x")}, 3: {("a.py", 1, "x")}})
    with pytest.raises(SystemExit) as e:
        review_lib.commands.loop.cmd_check_oscillation(argparse.Namespace(pr=PR))
    assert e.value.code == 2
    assert _read(env.tmp)["final"] == "approved"
    err = capsys.readouterr().err
    assert "振動検知 —" not in err
    assert "⏭ final=approved" in err


def test_oscillation_does_not_compare_across_the_reopen(env, monkeypatch, capsys):
    """I6: oscillation から足した最初のラウンドは、境の前のラウンドと同じ指摘でも中断しない。"""
    _state(env.tmp, final="oscillation", rounds=[_round(1), _round(2)])
    env.init()
    env.start_round()
    same = {("a.py", 1, "x")}
    _oscillate(monkeypatch, {2: same, 3: same})
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        review_lib.commands.loop.cmd_check_oscillation(argparse.Namespace(pr=PR))
    assert e.value.code == 2
    assert _read(env.tmp)["final"] is None
    assert "round_in_pr<2" in capsys.readouterr().err
