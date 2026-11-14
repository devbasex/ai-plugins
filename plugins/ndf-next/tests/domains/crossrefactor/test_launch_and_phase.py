"""担当の起動と工程の記録。

- #750: 提案の担当が結果ファイルを提出先に書けず結果が無い（提出先が読み取り用ディレクトリの外）
- #2057: drive が完了した回を返す案内に、持たない --reopen を書く
- #3313: テストを足す担当が結果を出さずに終わると、起点が記録されておらず範囲を確定できずに止まる
"""

from __future__ import annotations

from pathlib import PurePosixPath as P

import pytest

from ndf.contracts import facts
from ndf.domains.crossrefactor import finished_decide, launch_decide, phase_decide

DIRS = (P("/rf/read"), P("/rf/work"), P("/tmp/rf"))


def test_propose_result_is_inside_the_read_only_dir():
    got = launch_decide.result_place("propose", "codex", "rf199-r2", DIRS)
    assert got.workdir == P("/rf/read/codex")
    assert got.submit == P("/rf/read/codex/.cross_refactoring/codex-propose-rf199-r2-result.json")
    assert got.collect == P("/tmp/rf/codex-propose-rf199-r2-result.json")


@pytest.mark.parametrize("phase", facts.ids("loop-phases", skill="cross-refactoring", launch=True))
def test_every_launched_phase_submits_inside_its_own_workdir(phase):
    got = launch_decide.result_place(phase, "claude", "rf1-r1", DIRS)
    assert got.submit.is_relative_to(got.workdir)


def test_coding_phases_run_in_the_writable_work_dir():
    assert launch_decide.workdir_of("implement", "claude", *DIRS[:2]) == P("/rf/work")


def test_finished_guidance_without_a_reopen_flag_does_not_name_one():
    got = finished_decide.finished((None, None), ("abc", ["--scope", "lib"]), reopen_flag=None, discard_hint="状態ファイルを消して init から")
    assert got.keep
    assert got.notes and all("--reopen" not in n for n in got.notes)
    assert all("状態ファイルを消して init から" in n for n in got.notes)


def test_finished_guidance_names_the_flag_the_drive_has():
    got = finished_decide.finished((None, ["a"]), ("abc", ["a"]), reopen_flag="--reopen", discard_hint="x")
    assert got.keep and got.notes == ("ℹ head を比べられないため前回の結果を返す（打ち直すなら --reopen）",)


def test_finished_reruns_when_the_head_or_the_args_differ():
    assert not finished_decide.finished(("a", ["x"]), ("b", ["x"]), reopen_flag=None, discard_hint="x").keep
    assert not finished_decide.finished(("a", ["x"]), ("a", ["y"]), reopen_flag=None, discard_hint="x").keep
    assert finished_decide.finished(("a", ["x"]), ("a", ["x"]), reopen_flag=None, discard_hint="x") == finished_decide.Finished(True)


def test_phase_start_records_the_origin_before_launch():
    phases = phase_decide.start_phase({"add-tests": {"result": "old"}}, "add-tests", "c0ffee", "2026-11-14T12:00:00+09:00")
    assert phases["add-tests"] == {"base_sha": "c0ffee", "started_at": "2026-11-14T12:00:00+09:00"}


def test_no_result_without_an_origin_skips_the_item_instead_of_stopping():
    # #3313 の形: 記録 phases.add-tests に base_sha が無い
    got = phase_decide.on_no_result({}, "beef", spare=True)
    assert got.kind == phase_decide.SKIP and got.undo is None


def test_no_result_with_an_origin_undoes_and_reassigns():
    got = phase_decide.on_no_result({"base_sha": "c0ffee"}, "beef", spare=True)
    assert (got.kind, got.undo) == (phase_decide.REASSIGN, ("c0ffee", "beef"))
    nothing_written = phase_decide.on_no_result({"base_sha": "c0ffee"}, "c0ffee", spare=False)
    assert (nothing_written.kind, nothing_written.undo) == (phase_decide.SKIP, None)
