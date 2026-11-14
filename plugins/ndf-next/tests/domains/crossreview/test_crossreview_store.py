"""巻き直しの後の番号の規則と、修正の結果の取り込み（#2770）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.crossreview import cli, fixgate_decide, store

from ._repos import run_cli

ORIGIN, CURRENT = 2767, 2769


def _state(state_dir: Path, **data) -> Path:
    path = state_dir / store.STATE_NAME.format(pr=ORIGIN)
    store.write_object(path, {store.ORIGIN_KEY: ORIGIN, **data})
    return path


def _fix_result(state_dir: Path, name_pr: int, pr: int) -> Path:
    path = state_dir / store.FIX_RESULT_NAME.format(pr=name_pr)
    body = {"pr": pr, "commit": "abc1234", "findings": [{"id": "F1", "outcome": "fixed"}]}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_issue_2770_state_file_is_found_from_the_number_after_reopening(tmp_path: Path) -> None:
    """#2770 の形 2: 巻き直しの後の番号 2769 から、元の番号 2767 の状態ファイルを引く。"""
    path = _state(tmp_path, current_pr=CURRENT)
    assert store.state_pr(tmp_path, CURRENT) == store.StateRef(ORIGIN, CURRENT, path)
    assert store.state_pr(tmp_path, ORIGIN) == store.StateRef(ORIGIN, CURRENT, path)
    assert store.state_pr(tmp_path, 1).origin_pr == 1


def test_record_current_writes_the_new_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _state(tmp_path)
    code, got = run_cli(capsys, "state-file", "--state-dir", str(tmp_path), "--pr", str(ORIGIN), "--current", str(CURRENT))
    assert code == 0
    items = got["items"]
    assert items[0]["current_pr"] == CURRENT
    # 修正担当へ渡す結果ファイルの名前は元の番号にそろえる
    assert items[1]["path"].endswith(store.FIX_RESULT_NAME.format(pr=ORIGIN))
    assert store.state_pr(tmp_path, CURRENT).origin_pr == ORIGIN


def test_broken_state_file_is_not_replaced_by_a_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / store.STATE_NAME.format(pr=9)).write_text("{", encoding="utf-8")
    with pytest.raises(store.StateBroken):
        store.state_pr(tmp_path, CURRENT)
    code, got = run_cli(capsys, "state-file", "--state-dir", str(tmp_path), "--pr", str(CURRENT))
    assert code != 0
    assert got["cause"]["kind"] == "invalid"


@pytest.mark.parametrize(("name_pr", "pr"), [(ORIGIN, ORIGIN), (CURRENT, CURRENT), (ORIGIN, CURRENT)])
def test_issue_2770_fix_result_written_with_either_number_is_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name_pr: int, pr: int
) -> None:
    """#2770 の形 3: 修正担当が今の番号で書いた結果ファイル（名前・中の pr）も、元の番号の状態へ取り込む。
    取り込みは `run` と同じ形（台帳へ反映し、修正の記録は commit・reported・fixed、結果ファイルは移す。PR #3466 の指摘）。"""
    finding = {"id": "F1", "key": "k", "text": "直す", "round": 1, "outcome": "unresolved", "reason": None}
    _state(tmp_path, current_pr=CURRENT, rounds=[{"round": 1, "verdict": fixgate_decide.CHANGES_REQUIRED}], findings=[finding])
    pending = store.pending_fix_result(tmp_path, store.state_pr(tmp_path, CURRENT))
    assert pending is None
    path = _fix_result(tmp_path, name_pr, pr)
    ref = store.state_pr(tmp_path, CURRENT)
    assert store.pending_fix_result(tmp_path, ref) == path
    assert cli.main(["merge-fix", "--state-dir", str(tmp_path), "--pr", str(CURRENT), "--file", str(path)]) == 0
    capsys.readouterr()
    state = store.read_object(ref.path)
    assert state["rounds"][-1]["fix"] == {"commit": "abc1234", "reported": 1, "fixed": ["F1"]}
    assert state["findings"][0]["outcome"] == "fixed"
    assert store.pending_fix_result(tmp_path, ref) is None


def test_merge_fix_takes_in_decisions_on_handed_items(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PR #3466 の指摘: fix-gate と merge-fix は run と同じ足すべき内容（--findings）を受け、その id の決定も台帳の行にする。"""
    _state(tmp_path, current_pr=CURRENT, rounds=[{"round": 1, "verdict": fixgate_decide.CHANGES_REQUIRED}])
    handed = tmp_path / "rebuild.json"
    handed.write_text(json.dumps({"items": [{"id": "R1", "text": "出すコミット"}]}), encoding="utf-8")
    ref = store.state_pr(tmp_path, CURRENT)
    path = store.fix_result_path(tmp_path, ref)
    path.write_text(json.dumps({"pr": CURRENT, "commit": "abc1234", "findings": [{"id": "R1", "outcome": "fixed"}]}), encoding="utf-8")
    code, gate = run_cli(capsys, "fix-gate", "--state-dir", str(tmp_path), "--pr", str(CURRENT), "--findings", str(handed))
    assert code == 0 and gate["next"]["argv"][-2:] == ["--findings", str(handed)]
    assert cli.main(gate["next"]["argv"][2:]) == 0
    capsys.readouterr()
    state = store.read_object(ref.path)
    assert state["rounds"][-1]["fix"]["fixed"] == ["R1"]
    assert [(f["id"], f["outcome"]) for f in state["findings"]] == [("R1", "fixed")]


def test_fix_result_for_another_pr_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _state(tmp_path, current_pr=CURRENT)
    path = _fix_result(tmp_path, ORIGIN, 1)
    code, got = run_cli(capsys, "merge-fix", "--state-dir", str(tmp_path), "--pr", str(ORIGIN), "--file", str(path))
    assert code != 0
    assert got["cause"]["kind"] == "invalid" and "合わない" in got["summary"]
    assert path.is_file() and "fix" not in store.read_object(store.state_pr(tmp_path, ORIGIN).path)


def test_issue_2770_start_after_commits_outside_the_driver_takes_in_the_remaining_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#2770 の形 1: 修正必須の判定に修正の記録が無くても、結果ファイルが残っていれば止めずに取り込む 1 行を返す。"""
    _state(tmp_path, current_pr=CURRENT, rounds=[{"round": 2, "verdict": fixgate_decide.CHANGES_REQUIRED}])
    args = ["fix-gate", "--state-dir", str(tmp_path), "--pr", str(CURRENT)]
    code, stop = run_cli(capsys, *args)
    assert code != 0
    assert stop["cause"]["kind"] == "work_stopped" and "round 2" in stop["summary"]
    path = _fix_result(tmp_path, CURRENT, CURRENT)
    code, got = run_cli(capsys, *args)
    assert code == 0
    assert got["next"]["argv"][1:3] == ["cross-review", "merge-fix"] and str(path) in got["next"]["argv"]
    assert cli.main(got["next"]["argv"][2:]) == 0
    capsys.readouterr()
    code, got = run_cli(capsys, *args)
    assert code == 0
    assert got["summary"] == "次のラウンドへ進める"


@pytest.mark.parametrize(
    ("verdict", "fix", "pending", "kind"),
    [
        ("approved", None, None, fixgate_decide.OK),
        (fixgate_decide.CHANGES_REQUIRED, {"commit": "x"}, None, fixgate_decide.OK),
        (fixgate_decide.CHANGES_REQUIRED, None, "/p.json", fixgate_decide.INGEST),
        (fixgate_decide.CHANGES_REQUIRED, None, None, fixgate_decide.STOP),
    ],
)
def test_fix_gate_decision(verdict: str, fix, pending, kind: str) -> None:
    assert fixgate_decide.require_fix_for_changes(1, verdict, fix, pending).kind == kind
