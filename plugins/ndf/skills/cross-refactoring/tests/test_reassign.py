"""副命令 `reassign`: 結果を残さなかった担当を規則どおりに起動し直すか振り替える（#919 の AC11〜AC15、I1・I7・I9）。

| 工程 | 答え | 終了コード | 担当の書き換え |
| --- | --- | ---: | --- |
| `implement` | 振り替え | 7 | 実装担当を振り替え先へ（範囲を取り消してから） |
| `implement` | 中断 | 3 | なし |
| `fix` | 中断 | 3 | `fix_no_relaunch` だけを立てる |
| `propose` | 一部だけ結果なし | 0 | なし |
| `propose` | 全員が結果なし | 7 / 3 | 振り替え先で集め直す / 中断 |
"""

from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state_v2, read_state, write_result

ID = 130


def _args(phase, seats=None):
    return type("A", (), {"id": ID, "phase": phase, "seats": seats})()


def _run(cmd_reassign, phase, seats=None) -> int:
    with pytest.raises(SystemExit) as e:
        cmd_reassign.cmd_reassign(_args(phase, seats))
    return int(e.value.code or 0)


def _monitor(state_path, stem, reason, ended_at="2000-01-01T00:00:00"):
    (state_path.parent / f"{stem}-monitor.json").write_text(
        json.dumps({"reason": reason, "detail": "d", "ended_at": ended_at}), encoding="utf-8"
    )


@pytest.fixture
def undo_spy(patch_lib):
    """範囲の取り消しが触る git を差し替える。範囲は起点から 1 コミット。"""
    seen: dict = {"reverted": []}
    patch_lib("git_out", lambda work, args, **k: "HEADSHA")
    patch_lib("commits_in_range", lambda work, base, head: None if not base else ["C1"])
    patch_lib("discard_range", lambda path, state, reason: seen["reverted"].append(reason) or {"mode": "item"})
    return seen


def _implement_state(tmp_path, **over):
    items = [
        {"id": "R1", "status": "adopted", "commits": {"implement": ["OLD"], "fix": []}},
        {"id": "R2", "status": "planned", "commits": {"implement": [], "fix": []}},
    ]
    over.setdefault("items", items)
    over.setdefault("plan", {"reserve": {}})
    over.setdefault("phase", "implement")
    return make_state_v2(tmp_path, tmp_path / "work", phases={"implement": {"base_sha": "BASE"}}, **over)


def test_a_usage_limit_in_implement_reverts_the_range_and_moves_the_implementer(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    """AC11・AC12・I7: 範囲を取り消してから、計画の後でも止めずに実装担当を振り替えて 7。採用済みの項目は触らない。"""
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "implement") == 7

    out = capsys.readouterr().out
    assert "IMPL=codex" in out
    assert "REASSIGNED=claude=codex:usage_limit" in out
    st = read_state(path)
    assert (st["implementer"], st["implementer_reason"]) == ("codex", "reassigned")
    assert undo_spy["reverted"] == ["implement-no-result"]
    assert st["phases"]["implement"]["base_sha"] == "HEADSHA"
    assert st["phases"]["implement"]["failed_attempts"][-1]["impl"] == "claude"
    assert [i["status"] for i in st["items"]] == ["adopted", "planned"]
    assert st["no_results"] == [
        {
            "step": "implement",
            "attempt": 1,
            "seat": "claude",
            "account": "",
            "reason": "usage_limit",
            "decision": "reassign",
            "to": "codex",
            "to_account": "",
            "at": st["no_results"][0]["at"],
        }
    ]


def test_a_first_relaunchable_no_result_relaunches_the_same_implementer(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    """AC18: `usage_limit` 以外の 1 度目は同じ担当で同じ工程を起動し直す。"""
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "early_error")

    assert _run(cmd_reassign, "implement") == 7

    assert "IMPL=claude" in capsys.readouterr().out
    assert read_state(path)["implementer"] == "claude"


def test_without_a_candidate_implement_stops(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    path = _implement_state(tmp_path, participants={"pool": ["claude"], "available": ["claude"]})
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "implement") == 3
    assert "REASSIGN=abort" in capsys.readouterr().out
    assert read_state(path)["implementer"] == "claude"


def test_a_result_or_a_stop_at_the_limit_is_not_a_no_result(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    """決定 8: 結果がある・上限での打ち切りは 0（振り替えない）。"""
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "timeout")
    assert _run(cmd_reassign, "implement") == 0
    write_result(path, f"claude-implement-rf{ID}", {"ok": True})
    _monitor(path, f"claude-implement-rf{ID}", "usage_limit")
    assert _run(cmd_reassign, "implement") == 0
    assert "no_results" not in read_state(path)
    assert undo_spy["reverted"] == []


def test_a_rerun_replays_the_recorded_answer(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    """I1: 記録の後に打ち直しても、同じ件を重ねず、記録した答えを出し直す。"""
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "early_error")
    assert _run(cmd_reassign, "implement") == 7
    before = read_state(path)["no_results"]

    assert _run(cmd_reassign, "implement") == 7

    assert read_state(path)["no_results"] == before
    # 起動し直した後の結果なし（終わりが記録より後）は新しい件として振り替える
    _monitor(path, f"claude-implement-rf{ID}", "early_error", ended_at="2999-01-01T00:00:00")
    assert _run(cmd_reassign, "implement") == 7
    assert [e["decision"] for e in read_state(path)["no_results"]] == ["relaunch", "reassign"]


def test_claude_moves_to_another_account_and_keeps_only_the_name(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, monkeypatch, capsys):
    """AC4・I9: 別のアカウントへ振り替えても、状態ファイルと出力にはアカウントの名前だけが残る。"""
    monkeypatch.setattr(cmd_reassign.assignee_env, "account_picker", lambda base=None: lambda tried: "work2")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-token-value")
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "implement") == 7

    out = capsys.readouterr().out
    assert "REASSIGNED=claude=claude@work2:usage_limit" in out
    text = path.read_text()
    st = json.loads(text)
    assert (st["implementer"], st["implementer_account"]) == ("claude", "work2")
    assert "secret-token-value" not in text and "secret-token-value" not in out
    assert "CLAUDE_CONFIG_DIR" not in text


def test_a_fix_without_a_candidate_raises_only_the_fix_flag(cmd_reassign, cmd_converge, tmp_path, env_tmp_dir, undo_spy):
    """決定 14: `fix` の中断は `fix_no_relaunch` だけを立て、最終ゲートの旗は立てない。範囲は取り消さない。"""
    path = _implement_state(
        tmp_path,
        phase="verify",
        fix={"items": ["R2"], "base_sha": "BASE", "attempt": 2},
        participants={"pool": ["claude"], "available": ["claude"]},
    )
    env_tmp_dir(path)
    _monitor(path, f"claude-fix-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "fix") == 3

    st = read_state(path)
    assert st["fix_no_relaunch"] is True
    assert "no_relaunch" not in st["final_gate"]
    assert st["no_results"][-1]["attempt"] == 2
    assert undo_spy["reverted"] == []
    # 時計に余裕があっても修正を求めない
    assert cmd_converge._fix_stop(st) is True
    st.pop("fix_no_relaunch")
    assert cmd_converge._fix_stop(st) is False


def test_a_fix_with_a_candidate_switches_the_next_fix(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, capsys):
    path = _implement_state(tmp_path, phase="verify", fix={"items": ["R2"], "base_sha": "BASE", "attempt": 1})
    env_tmp_dir(path)
    _monitor(path, f"claude-fix-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "fix") == 2
    assert read_state(path)["implementer"] == "codex"
    assert undo_spy["reverted"] == []


# ---------------- 提案（AC14） ----------------


def test_a_partial_no_result_in_propose_goes_on(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    write_result(path, f"codex-propose-rf{ID}", {"proposals": []})
    _monitor(path, f"claude-propose-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "propose", ["claude", "codex"]) == 0
    assert "no_results" not in read_state(path)


def test_all_no_result_in_propose_gathers_from_a_participant_that_did_not_propose(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    """全員が結果なしなら、提案を出していない担当（ここでは起動しなかった kiro）へ振り替える。"""
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    _monitor(path, f"claude-propose-rf{ID}", "usage_limit")
    _monitor(path, f"codex-propose-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "propose", ["claude", "codex"]) == 7

    out = capsys.readouterr().out
    assert "PROPOSERS=kiro" in out
    assert [e["decision"] for e in read_state(path)["no_results"]] == ["reassign", "abort"]


def test_all_no_result_in_propose_without_a_candidate_stops(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    for r in ("claude", "codex", "kiro"):
        _monitor(path, f"{r}-propose-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "propose", ["claude", "codex", "kiro"]) == 3
    assert "REASSIGN=abort" in capsys.readouterr().out


def test_all_proposers_stopped_at_the_limit_abort_without_relaunch(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    """全員を監視が上限で止めたら、起動し直さず振り替えずに中断する（提案の期限を過ぎて CLI を動かさない）。"""
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    _monitor(path, f"claude-propose-rf{ID}", "timeout")
    _monitor(path, f"codex-propose-rf{ID}", "stalled")

    assert _run(cmd_reassign, "propose", ["claude", "codex"]) == 3
    assert "REASSIGN=abort" in capsys.readouterr().out
    assert "no_results" not in read_state(path)


def test_a_proposer_reassignment_that_drops_the_implementer_picks_it_again(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    """提案の振り替えで実装担当のランタイムが外れたら、外した後の参加者から実装担当を選び直して `IMPL` で返す。"""
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    _monitor(path, f"claude-propose-rf{ID}", "usage_limit")
    _monitor(path, f"codex-propose-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "propose", ["claude", "codex"]) == 7

    out = capsys.readouterr().out
    assert "PROPOSERS=kiro" in out and "IMPL=kiro" in out
    st = read_state(path)
    assert (st["implementer"], st["implementer_reason"]) == ("kiro", "reassigned")


def test_a_proposer_relaunch_keeps_the_implementer(cmd_reassign, tmp_path, env_tmp_dir, capsys):
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    _monitor(path, f"claude-propose-rf{ID}", "early_error")

    assert _run(cmd_reassign, "propose", ["claude"]) == 7

    assert "IMPL=" not in capsys.readouterr().out
    assert read_state(path)["implementer"] == "claude"


def test_a_no_result_in_implement_discards_uncommitted_changes_first(cmd_reassign, tmp_path, env_tmp_dir, undo_spy, monkeypatch):
    """コミットせずに止まった担当の未コミットの変更を、次の担当へ渡す前に捨てる。"""
    discarded = []
    monkeypatch.setattr(cmd_reassign, "discard_impl_leftovers", lambda state, work: discarded.append(work))
    path = _implement_state(tmp_path)
    env_tmp_dir(path)
    _monitor(path, f"claude-implement-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "implement") == 7
    assert discarded == [str(tmp_path / "work")]


def test_a_claude_proposer_moves_to_another_account(cmd_reassign, tmp_path, env_tmp_dir, monkeypatch, capsys):
    monkeypatch.setattr(cmd_reassign.assignee_env, "account_picker", lambda base=None: lambda tried: "work2")
    path = _implement_state(tmp_path, phase="propose")
    env_tmp_dir(path)
    _monitor(path, f"claude-propose-rf{ID}", "usage_limit")

    assert _run(cmd_reassign, "propose", ["claude"]) == 7

    out = capsys.readouterr().out
    assert "PROPOSERS=claude" in out
    st = read_state(path)
    assert st["proposer_accounts"] == {"claude": "work2"}
    # 実装担当の claude も同じアカウントへ合わせ、利用上限に当たった元のアカウントで plan を起動しない
    assert "IMPL=claude" in out
    assert (st["implementer"], st["implementer_account"]) == ("claude", "work2")


def test_the_report_lists_each_reassignment(cmd_report, tmp_path, env_tmp_dir, capsys):
    """AC15・F7: 報告に振り替えの行（工程・試行・元の担当 → 振り替え先・理由）が出る。"""
    log = [
        {
            "step": "implement",
            "attempt": 1,
            "seat": "claude",
            "account": "",
            "reason": "usage_limit",
            "decision": "reassign",
            "to": "codex",
            "to_account": "",
            "at": "t",
        },
        {
            "step": "fix",
            "attempt": 2,
            "seat": "codex",
            "account": "",
            "reason": "stalled",
            "decision": "relaunch",
            "to": "",
            "to_account": "",
            "at": "t",
        },
    ]
    lines = cmd_report._reassigned_lines({"no_results": log})
    assert lines == ["- 振り替え: implement（試行 1）: claude → codex（usage_limit）"]
    assert cmd_report._reassigned_lines({}) == []
