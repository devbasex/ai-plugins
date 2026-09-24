"""提案ラウンドの開始・再開・収束判定のテスト。"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def round_of(round_no, **over):
    base = {
        "round": round_no, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": 1, "adopted": 1, "deferred": 0,
        "items": [], "apply": {"applied": [], "failed": []},
        "fix_rounds": 0, "durations": {}, "reviews": [],
        "proposal_keys": [["src/a.py", "A", "long_method"]],
    }
    base.update(over)
    return base


# ---------- start-round ----------

def test_start_round_opens_and_records_assignment(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, runtimes=["claude", "codex", "kiro"])
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    state = read_state(state_path)
    assert len(state["rounds"]) == 1
    entry = state["rounds"][0]
    # ラウンド 1 は参加者の 2 番目から始まり、ホストが最初に適用しない（#727 の決定 7）
    assert entry["impl"] == "codex"
    assert "reviewers" not in entry
    assert state["phase"] == "propose"


def test_start_round_is_idempotent_on_resume(refactor, tmp_path, env_tmp_dir):
    """同一ラウンドを開き直しても担当が変わらないこと。"""
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())
    first = read_state(state_path)["rounds"][0]

    # ラウンドが未完了のまま再実行しても新しいラウンドを開かない
    state = read_state(state_path)
    state["rounds"] = [first]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    refactor.cmd_start_round(_args())

    state = read_state(state_path)
    assert len(state["rounds"]) == 2, "2 回目は次のラウンドを開く"
    assert state["rounds"][0] == first, "既に開いたラウンドの割り当ては変わらない"


def test_start_round_stops_at_max_outer_rounds(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, max_outer_rounds=2,
                            rounds=[round_of(1), round_of(2)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        refactor.cmd_start_round(_args())
    assert e.value.code == 1
    assert read_state(state_path)["final"] == "max_outer_rounds"


def test_start_round_stops_when_already_final(refactor, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, final="no_more_proposals")
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        refactor.cmd_start_round(_args())
    assert e.value.code == 1


# ---------- advance（収束判定） ----------

def test_advance_with_no_rounds_leaves_the_state_unchanged(cmd_report, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, rounds=[], final=None)
    env_tmp_dir(state_path)
    before = read_state(state_path)

    cmd_report.cmd_advance(_args())

    assert read_state(state_path) == before


def test_advance_stops_when_already_final(cmd_report, tmp_path, env_tmp_dir):
    """終了済みなら状態を書き換えずに終了コード 1 で止まる（R2-002 の現状固定）。"""
    state_path = make_state(tmp_path, final="no_more_proposals", rounds=[round_of(1)])
    env_tmp_dir(state_path)
    before = read_state(state_path)

    with pytest.raises(SystemExit) as e:
        cmd_report.cmd_advance(_args())

    assert e.value.code == 1
    assert read_state(state_path) == before


def test_advance_continues_when_progress_is_made(cmd_report, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, rounds=[round_of(1)])
    env_tmp_dir(state_path)
    cmd_report.cmd_advance(_args())
    assert read_state(state_path)["final"] is None


def test_advance_stops_when_nothing_adopted(cmd_report, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, rounds=[round_of(1, adopted=0)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit) as e:
        cmd_report.cmd_advance(_args())
    assert e.value.code == 1
    assert read_state(state_path)["final"] == "no_more_proposals"


def test_advance_stops_at_max_outer_rounds(cmd_report, tmp_path, env_tmp_dir):
    state_path = make_state(tmp_path, max_outer_rounds=1, rounds=[round_of(1)])
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        cmd_report.cmd_advance(_args())
    assert read_state(state_path)["final"] == "max_outer_rounds"


def test_advance_stops_on_duplicate_proposals(cmd_report, tmp_path, env_tmp_dir):
    """同じ提案が毎ラウンド出続けて終わらない状態を収束と判定する。"""
    keys = [["src/a.py", "A", "long_method"], ["src/b.py", "B", "duplication"]]
    state_path = make_state(
        tmp_path, max_outer_rounds=5,
        rounds=[round_of(1, proposal_keys=keys), round_of(2, proposal_keys=keys)],
    )
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        cmd_report.cmd_advance(_args())
    assert read_state(state_path)["final"] == "duplicate_proposals"


def test_advance_allows_partially_overlapping_proposals(cmd_report, tmp_path, env_tmp_dir):
    """重複率がしきい値未満なら続ける。"""
    prev = [["src/a.py", "A", "long_method"], ["src/b.py", "B", "duplication"]]
    cur = [["src/a.py", "A", "long_method"], ["src/c.py", "C", "deep_nesting"],
           ["src/d.py", "D", "dead_code"]]
    state_path = make_state(
        tmp_path, max_outer_rounds=5,
        rounds=[round_of(1, proposal_keys=prev), round_of(2, proposal_keys=cur)],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_advance(_args())
    assert read_state(state_path)["final"] is None


# ---------- 報告 ----------

def test_report_renders_tables(cmd_report, tmp_path, env_tmp_dir, capsys):
    state_path = make_state(
        tmp_path,
        rounds=[round_of(1, items=["R1-001"], apply={"applied": ["R1-001"], "failed": []},
                       reviews=[{"round": 1, "agy": "APPROVE", "kiro": "APPROVE",
                                 "findings": []}])],
        items=[{"item_id": "R1-001", "round": 1, "path": "src/a.py", "symbol": "A",
                "smell": "long_method", "technique": "extract_method",
                "severity": "major", "proposed_by": ["codex", "agy"],
                "status": "done", "commits": ["abc"]}],
        deferred_items=[{"path": "src/z.py", "symbol": "Z", "smell": "duplication",
                         "round": 1, "defer_reason": "しきい値未満"}],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(type("A", (), {"id": 130, "metrics": True})())

    out = capsys.readouterr().out
    assert "## ラウンド" in out
    assert "## 改善項目" in out
    assert "## 見送った提案" in out
    assert "比較として読むときの限界" in out
    assert "R1-001" in out


def test_status_reports_one_cohort(cmd_report, tmp_path, env_tmp_dir, capsys):
    """AC37 — 母集合は 1 行で出す。提案と適用で分けない（#727 の決定 5）。"""
    state_path = make_state(tmp_path, runtimes=["claude", "codex", "kiro"])
    env_tmp_dir(state_path)
    cmd_report.cmd_status(_args())
    out = capsys.readouterr().out
    assert "参加者（提案と適用）: claude / codex / kiro" in out
    assert "提案・レビュー" not in out
    assert "適用の母集合" not in out


def test_status_reads_an_older_state_with_the_implementation_cohort(
        cmd_report, tmp_path, env_tmp_dir, capsys):
    """AC41 — 適用専用の母集合を持つ古い状態ファイルも読める。表示は参加者の一覧だけ。"""
    state_path = make_state(tmp_path, impl_capable=["claude", "codex", "agy", "kiro"])
    env_tmp_dir(state_path)
    cmd_report.cmd_status(_args())
    assert "参加者（提案と適用）: codex / agy / kiro" in capsys.readouterr().out


def _report_args():
    return type("A", (), {"id": 130, "metrics": False})()


def test_report_has_no_reviewer_column(cmd_report, tmp_path, env_tmp_dir, capsys):
    """AC37 — ラウンド表にレビュー担当の列が無い。古い記録にあっても出さない。"""
    state_path = make_state(tmp_path, rounds=[{
        "round": 1, "kind": "structure", "impl": "codex",
        "impl_model": {"requested": None, "observed": None},
        "reviewers": ["agy", "kiro"], "reviewer_models": {}, "adopted": 1,
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0, "reviews": [],
    }])
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if line.startswith("| R |"))
    assert "レビュー担当" not in header
    assert "| 1 | 構造改善 | codex |" in out
    assert "agy / kiro" not in out


def test_report_prints_the_participants(cmd_report, tmp_path, env_tmp_dir, capsys):
    """F6 — 参加者・外した者・足した者・確認を通らなかった者・再開で変えた値を出す。"""
    state_path = make_state(
        tmp_path, runtimes=["claude", "codex"],
        participants={
            "pool": ["claude", "codex", "kiro"], "included": ["agy"],
            "excluded": ["kiro"], "available": ["claude", "codex"],
            "unavailable": {"agy": "Not logged in"},
            "probe_skipped": False, "require_all": False,
        },
        resume_changes=[{"at": "2026-09-22T00:00:00", "field": "max_outer_rounds",
                         "from": 3, "to": 5}],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    out = capsys.readouterr().out
    assert "## 参加した者" in out
    assert "- 母集合: claude / codex / kiro" in out
    assert "- 使える者: claude / codex" in out
    assert "- --exclude で外した者: kiro" in out
    assert "- --include で足した者: agy" in out
    assert "- 確認を通らなかった者: agy（Not logged in）" in out
    assert "max_outer_rounds: 3 → 5" in out


def test_report_prints_an_ignored_exclusion(cmd_report, tmp_path, env_tmp_dir, capsys):
    """#786 の AC4d — `--exclude` で指定したが既定の母集合に無かった者を 1 行で出す。"""
    state_path = make_state(
        tmp_path, runtimes=["claude", "codex", "kiro"],
        participants={
            "pool": ["claude", "codex", "kiro"], "included": [], "excluded": [],
            "ignored_exclude": ["agy"], "available": ["claude", "codex", "kiro"],
            "unavailable": {}, "probe_skipped": False, "require_all": False,
        },
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    assert "- --exclude で指定したが既定の母集合に無かった者: agy" in capsys.readouterr().out


def test_report_says_no_record_for_an_older_state(cmd_report, tmp_path, env_tmp_dir, capsys):
    """AC41 — 参加者の記録を持たない古い状態ファイルでは「記録なし」と出す。"""
    state_path = make_state(tmp_path, impl_capable=["claude", "codex", "agy", "kiro"])
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    assert "- 使える者: 記録なし" in capsys.readouterr().out


# ---------- 報告の未到達分岐の固定（R2-004） ----------
#
# 現状固定テスト。完了報告の既存テストが通っていなかった分岐を固定する。
# 行全体の完全一致は避け、分岐の判定に関わる値の部分だけを比較する。


def _participants(**over):
    """参加者の記録を組み立てるヘルパ。"""
    base = {
        "pool": ["claude", "codex", "kiro"], "included": [],
        "excluded": [], "available": ["claude", "codex", "kiro"],
        "unavailable": {}, "probe_skipped": False, "require_all": False,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("probe_skipped, expected_text", [
    (True, "確認を飛ばした（NDF_SKIP_AUTH_CHECK）"),
    (False, "確認を通らなかった者: なし"),
])
def test_report_probe_skipped_vs_no_unavailable(
    cmd_report, tmp_path, env_tmp_dir, capsys, probe_skipped, expected_text
):
    """R2-004(a)(b) — unavailable が空のとき probe_skipped で出力が変わる。"""
    state_path = make_state(
        tmp_path,
        participants=_participants(probe_skipped=probe_skipped),
        resume_changes=[],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    assert expected_text in capsys.readouterr().out


def test_report_empty_resume_changes_says_none(
    cmd_report, tmp_path, env_tmp_dir, capsys
):
    """R2-004(c) — 再開で変えた値が空なら「なし」と出る。"""
    state_path = make_state(
        tmp_path,
        participants=_participants(),
        resume_changes=[],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    assert "再開で変えた値: なし" in capsys.readouterr().out


def test_report_resume_change_with_participants_shows_available(
    cmd_report, tmp_path, env_tmp_dir, capsys
):
    """R2-004(d) — 再開で変えた値に参加者（available を持つ dict）があれば、使える者だけを出す。"""
    state_path = make_state(
        tmp_path,
        participants=_participants(),
        resume_changes=[{
            "at": "2026-09-22T00:00:00", "field": "participants",
            "from": {"available": ["claude", "codex"]},
            "to": {"available": ["claude", "codex", "kiro"]},
        }],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    out = capsys.readouterr().out
    assert "claude / codex → claude / codex / kiro" in out


def test_report_shows_test_rounds_final_when_present(
    cmd_report, tmp_path, env_tmp_dir, capsys
):
    """R2-004(e) — test_rounds_final があれば「テスト整備の終わり方」を出す。"""
    state_path = make_state(
        tmp_path,
        test_rounds_final="max_test_rounds",
        participants=_participants(),
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    out = capsys.readouterr().out
    assert "テスト整備の終わり方" in out
    assert "max_test_rounds" in out


def test_report_item_table_shows_case_and_level_for_test_items(
    cmd_report, tmp_path, env_tmp_dir, capsys
):
    """R2-004(f) — テスト整備の項目（kind=test）は case と level を項目表に出す。"""
    test_item = {
        "item_id": "R2-001", "round": 1, "kind": "test",
        "path": "tests/test_init.py",
        "target": "scripts/refactor_lib/commands/setup.py#cmd_init",
        "case": "error", "level": "unit",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 0, "proposed_by": ["claude"],
        "status": "applied", "commits": ["abc"],
    }
    structure_item = {
        "item_id": "R1-001", "round": 1,
        "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 10, "proposed_by": ["codex"],
        "status": "done", "commits": ["def"],
    }
    state_path = make_state(
        tmp_path,
        items=[structure_item, test_item],
        rounds=[{
            "round": 1, "kind": "structure", "impl": "codex",
            "impl_model": {"requested": None, "observed": None},
            "adopted": 2,
            "apply": {"applied": ["R1-001", "R2-001"], "failed": []},
            "fix_rounds": 0, "reviews": [],
        }],
    )
    env_tmp_dir(state_path)
    cmd_report.cmd_report(_report_args())
    lines = capsys.readouterr().out.splitlines()
    # テスト項目の行で case と level が出ること
    test_row = next(line for line in lines if "R2-001" in line)
    assert "error" in test_row
    assert "unit" in test_row
    # 構造改善項目の行は smell と technique が出ること（既存動作の確認）
    struct_row = next(line for line in lines if "R1-001" in line)
    assert "long_method" in struct_row
    assert "extract_method" in struct_row
