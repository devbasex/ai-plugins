"""巻き直しの後の番号の規則と、修正の結果の取り込み（#2770）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.crossreview import cli, fixgate_decide, store

from ._repos import last_result

ORIGIN, CURRENT = 2767, 2769


def _state(state_dir: Path, **data) -> Path:
    path = state_dir / store.STATE_NAME.format(pr=ORIGIN)
    store.write_object(path, {store.ORIGIN_KEY: ORIGIN, **data})
    return path


def _fix_result(state_dir: Path, name_pr: int, pr: int) -> Path:
    path = state_dir / store.FIX_RESULT_NAME.format(pr=name_pr)
    path.write_text(json.dumps({"pr": pr, "commit": "abc1234", "fixed": ["F1"]}), encoding="utf-8")
    return path


def test_issue_2770_state_file_is_found_from_the_number_after_reopening(tmp_path: Path) -> None:
    """#2770 の形 2: 巻き直しの後の番号 2769 から、元の番号 2767 の状態ファイルを引く。"""
    path = _state(tmp_path, current_pr=CURRENT)
    assert store.state_pr(tmp_path, CURRENT) == store.StateRef(ORIGIN, CURRENT, path)
    assert store.state_pr(tmp_path, ORIGIN) == store.StateRef(ORIGIN, CURRENT, path)
    assert store.state_pr(tmp_path, 1).origin_pr == 1


def test_record_current_writes_the_new_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _state(tmp_path)
    assert cli.main(["state-file", "--state-dir", str(tmp_path), "--pr", str(ORIGIN), "--current", str(CURRENT)]) == 0
    items = last_result(capsys.readouterr().out)["items"]
    assert items[0]["current_pr"] == CURRENT
    # 修正担当へ渡す結果ファイルの名前は元の番号にそろえる
    assert items[1]["path"].endswith(store.FIX_RESULT_NAME.format(pr=ORIGIN))
    assert store.state_pr(tmp_path, CURRENT).origin_pr == ORIGIN


def test_broken_state_file_is_not_replaced_by_a_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / store.STATE_NAME.format(pr=9)).write_text("{", encoding="utf-8")
    with pytest.raises(store.StateBroken):
        store.state_pr(tmp_path, CURRENT)
    assert cli.main(["state-file", "--state-dir", str(tmp_path), "--pr", str(CURRENT)]) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"


@pytest.mark.parametrize(("name_pr", "pr"), [(ORIGIN, ORIGIN), (CURRENT, CURRENT), (ORIGIN, CURRENT)])
def test_issue_2770_fix_result_written_with_either_number_is_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name_pr: int, pr: int
) -> None:
    """#2770 の形 3: 修正担当が今の番号で書いた結果ファイル（名前・中の pr）も、元の番号の状態へ取り込む。"""
    _state(tmp_path, current_pr=CURRENT, rounds=[{"round": 1, "verdict": fixgate_decide.CHANGES_REQUIRED}])
    pending = store.pending_fix_result(tmp_path, store.state_pr(tmp_path, CURRENT))
    assert pending is None
    path = _fix_result(tmp_path, name_pr, pr)
    ref = store.state_pr(tmp_path, CURRENT)
    assert store.pending_fix_result(tmp_path, ref) == path
    assert cli.main(["merge-fix", "--state-dir", str(tmp_path), "--pr", str(CURRENT), "--file", str(path)]) == 0
    capsys.readouterr()
    assert store.read_object(ref.path)["rounds"][-1]["fix"]["pr"] == pr


def test_fix_result_for_another_pr_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _state(tmp_path, current_pr=CURRENT)
    path = _fix_result(tmp_path, ORIGIN, 1)
    assert cli.main(["merge-fix", "--state-dir", str(tmp_path), "--pr", str(ORIGIN), "--file", str(path)]) != 0
    got = last_result(capsys.readouterr().out)
    assert got["cause"]["kind"] == "invalid" and "合わない" in got["summary"]


def test_issue_2770_start_after_commits_outside_the_driver_takes_in_the_remaining_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#2770 の形 1: 修正必須の判定に修正の記録が無くても、結果ファイルが残っていれば止めずに取り込む 1 行を返す。"""
    _state(tmp_path, current_pr=CURRENT, rounds=[{"round": 2, "verdict": fixgate_decide.CHANGES_REQUIRED}])
    args = ["fix-gate", "--state-dir", str(tmp_path), "--pr", str(CURRENT)]
    assert cli.main(args) != 0
    stop = last_result(capsys.readouterr().out)
    assert stop["cause"]["kind"] == "work_stopped" and "round 2" in stop["summary"]
    path = _fix_result(tmp_path, CURRENT, CURRENT)
    assert cli.main(args) == 0
    got = last_result(capsys.readouterr().out)
    assert got["next"]["argv"][1:3] == ["cross-review", "merge-fix"] and str(path) in got["next"]["argv"]
    assert cli.main(got["next"]["argv"][2:]) == 0
    capsys.readouterr()
    assert cli.main(args) == 0
    assert last_result(capsys.readouterr().out)["summary"] == "次のラウンドへ進める"


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
