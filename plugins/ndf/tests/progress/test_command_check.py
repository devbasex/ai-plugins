"""サブコマンド `ndf progress check`（#3856）と、check と finish が共有する前半の振る舞い。

test_command.py が行数の安全網を超えたため、#3856 の塊をここへ分けた。"""

from __future__ import annotations

import json

import pytest

from ndf.checks import spec_model
from ndf.progress import definition
from ndf.progress.gate import finishing


def _run(capsys, last_result, *argv: str) -> tuple[int, dict]:
    code = finishing.main(list(argv))
    return code, last_result(capsys.readouterr().out)


def _new(capsys, last_result, *extra: str) -> tuple[int, dict]:
    return _run(capsys, last_result, "new", "s1", "--mode", "standard", "--issues", "3369", *extra)


# --- 工程の出力の確かめ（check。#3856） ---------------------------------------------------------------


# 設計の定義のキーを両方持つ出力（mvv_gate の形は workflow/context.schema.json の $defs）
MVV_GATE = {"gate": "design", "skip": False, "verdict": None, "why": ["テストでは判定しない"]}
DESIGN_OUTPUT = {"steps": [], "summary": "設計した", "outputs": {"design_pr": 1, "mvv_gate": MVV_GATE}}


def _check_result_fits_the_schema(out: dict) -> None:
    """結果のスキーマ（progress.result.schema.json）に通る。emit の確かめに加えて、ok と refused の両方を名指しで縛る。"""
    assert list(spec_model.result_validator("progress").iter_errors(out)) == []


def _check(capsys, last_result, tmp_path, tid: str, stage: str, output: dict) -> tuple[int, dict]:
    path = tmp_path / f"check-{stage}.json"
    path.write_text(json.dumps(output, ensure_ascii=False))
    return _run(capsys, last_result, "check", tid, stage, "--output", str(path))


def _shown(capsys, last_result, tid: str) -> tuple[list, list]:
    _, shown = _run(capsys, last_result, "show", tid)
    return shown["items"], shown["metrics"]["history"]


def test_check_returns_ok_and_the_declared_keys_for_a_fitting_output(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _check(capsys, last_result, tmp_path, tid, "設計", DESIGN_OUTPUT)
    assert code == 0 and out["status"] == "ok"
    assert out["metrics"] == {"machine_id": tid, "stage": "設計", "outputs": ["design_pr", "mvv_gate"]}
    assert "warnings" not in out
    _check_result_fits_the_schema(out)


def test_check_takes_an_output_without_outputs_when_the_stage_declares_none(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _check(capsys, last_result, tmp_path, tid, "計画", {"steps": [], "summary": "計画した"})
    assert code == 0 and out["metrics"]["outputs"] == []


def test_check_warns_of_the_context_keys_finish_drops(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _check(capsys, last_result, tmp_path, tid, "振り返り", {"steps": [], "summary": "x", "outputs": {"pr": 5}})
    assert code == 0 and out["status"] == "ok"
    assert any(w.endswith(": pr") for w in out["warnings"])
    _check_result_fits_the_schema(out)


@pytest.mark.parametrize(
    ("stage", "output", "named"),
    [
        (
            "振り返り",
            {"steps": [], "summary": "x", "outputs": {"retrospective_comment": "https://example.invalid"}},
            "retrospective_comment",
        ),
        ("設計", {"steps": [], "summary": "x", "outputs": {"mvv_gate": MVV_GATE}}, "design_pr"),
        ("計画", {"steps": []}, "summary"),
    ],
)
def test_check_refuses_with_the_same_error_as_finish(repo, capsys, tmp_path, last_result, stage, output, named):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _check(capsys, last_result, tmp_path, tid, stage, output)
    assert code == 1 and out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert named in out["cause"]["detail"] and out["cause"]["detail"].startswith(f"{stage} の出力が規約に合わない: ")
    assert out["metrics"]["machine_id"] == tid and out["metrics"]["stage"] == stage
    _check_result_fits_the_schema(out)
    path = tmp_path / f"check-{stage}.json"
    _, finished = _run(capsys, last_result, "finish", tid, stage, "--output", str(path), "--reason", "手で")
    assert finished["cause"] == out["cause"]


@pytest.mark.parametrize(
    ("stage", "output"),
    [
        ("設計", DESIGN_OUTPUT),
        ("振り返り", {"steps": [], "summary": "x", "outputs": {"retrospective_comment": "u"}}),
    ],
)
def test_check_writes_neither_the_rows_nor_the_history(repo, capsys, tmp_path, last_result, stage, output):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    before = _shown(capsys, last_result, tid)
    _check(capsys, last_result, tmp_path, tid, stage, output)
    assert _shown(capsys, last_result, tid) == before


def test_check_of_an_unknown_stage_or_table_stops_like_finish(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    output = {"steps": [], "summary": "x"}
    _, out = _check(capsys, last_result, tmp_path, tid, "存在しない", output)
    assert out["cause"]["kind"] == "refused" and "存在しない" in out["cause"]["detail"]
    _, out = _check(capsys, last_result, tmp_path, "無い", "実装", output)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"


def test_check_of_an_unreadable_output_stops_like_finish(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    _, checked = _run(capsys, last_result, "check", tid, "実装", "--output", str(bad))
    _, finished = _run(capsys, last_result, "finish", tid, "実装", "--output", str(bad), "--reason", "手で")
    assert checked["status"] == "stopped" and checked["cause"] == finished["cause"]


def test_check_lists_the_missing_keys_with_where_to_take_them(repo, capsys, tmp_path, last_result):
    """受け入れ条件 4（#3908）。欠けた mvv_gate には取り場所が、取り場所の無い design_pr には null が載る。"""
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _check(capsys, last_result, tmp_path, tid, "設計", {"steps": [], "summary": "x", "outputs": {}})
    assert code == 1 and out["cause"]["kind"] == "refused"
    assert out["items"] == [
        {"kind": "missing", "key": "design_pr", "source": None},
        {"kind": "missing", "key": "mvv_gate", "source": definition.context_source("mvv_gate")},
    ]
    assert f"（取り場所: {definition.context_source('mvv_gate')}）" in out["summary"]
    _check_result_fits_the_schema(out)


def test_check_without_missing_keys_has_no_items(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    _, refused = _check(capsys, last_result, tmp_path, tid, "設計", {"steps": [], "outputs": DESIGN_OUTPUT["outputs"]})
    _, fitting = _check(capsys, last_result, tmp_path, tid, "設計", DESIGN_OUTPUT)
    assert refused["status"] == "stopped" and refused["items"] == []
    assert fitting["status"] == "ok" and fitting["items"] == []


# --- check と finish が共有する前半の振る舞い（I-018 の切り出しの前に固定する） ----------------------


def _finish(capsys, last_result, tmp_path, tid: str, stage: str, output: dict) -> tuple[int, dict]:
    path = tmp_path / f"finish-{stage}.json"
    path.write_text(json.dumps(output, ensure_ascii=False))
    return _run(capsys, last_result, "finish", tid, stage, "--output", str(path), "--reason", "手で")


def _tail(capsys, last_result, tid: str, stage: str) -> list[tuple[str, dict]]:
    _, history = _shown(capsys, last_result, tid)
    return [(h["kind"], h["payload"]) for h in history if h["row_key"] == stage and h["kind"] != "created"]


def test_finish_writes_the_declared_outputs_after_the_marker(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _finish(capsys, last_result, tmp_path, tid, "設計", DESIGN_OUTPUT)
    assert code == 0 and out["status"] == "ok" and "warnings" not in out
    assert len(out["metrics"]["seqs"]) == 2
    tail = _tail(capsys, last_result, tid, "設計")
    assert [k for k, _ in tail] == ["manual_finished", "outputs_recorded"]
    assert tail[1][1] == {"outputs": DESIGN_OUTPUT["outputs"]}


def test_finish_without_outputs_writes_only_the_marker(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    code, out = _finish(capsys, last_result, tmp_path, tid, "計画", {"steps": [], "summary": "計画した"})
    assert code == 0 and len(out["metrics"]["seqs"]) == 1 and "warnings" not in out
    assert [k for k, _ in _tail(capsys, last_result, tid, "計画")] == ["manual_finished"]


def test_finish_drops_the_keys_check_warns_of(repo, capsys, tmp_path, last_result):
    """check が警告するキーと finish が捨てるキーは同じ集合（文言は check が「捨てる」、finish が「捨てた」）。"""
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    output = {**DESIGN_OUTPUT, "outputs": {**DESIGN_OUTPUT["outputs"], "pr": 5}}
    _, checked = _check(capsys, last_result, tmp_path, tid, "設計", output)
    code, finished = _finish(capsys, last_result, tmp_path, tid, "設計", output)
    assert code == 0 and finished["status"] == "ok", finished
    assert checked["warnings"] == ["工程 設計 の定義の outputs に無いキーを finish が捨てる: pr"]
    assert finished["warnings"] == ["工程 設計 の定義の outputs に無いキーを捨てた: pr"]
    tail = _tail(capsys, last_result, tid, "設計")
    assert tail[-1] == ("outputs_recorded", {"outputs": DESIGN_OUTPUT["outputs"]})


def test_finish_refusal_writes_nothing_and_check_keeps_the_declared_keys(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    output = {"steps": [], "summary": "x", "outputs": {"mvv_gate": MVV_GATE}}
    before = _shown(capsys, last_result, tid)
    code, finished = _finish(capsys, last_result, tmp_path, tid, "設計", output)
    assert code == 1 and finished["cause"]["kind"] == "refused" and finished["metrics"] == {}
    assert _shown(capsys, last_result, tid) == before
    _, checked = _check(capsys, last_result, tmp_path, tid, "設計", output)
    assert checked["metrics"]["outputs"] == ["design_pr", "mvv_gate"]
