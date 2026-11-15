"""`ndf answer work`: 作業名を `ndf worker run` の必須の引数へ合わせる（#3295）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts import causes, result
from ndf.core import route, worker
from ndf.progress import gate, model
from ndf.progress.gate import entry


def test_the_work_name_becomes_the_required_arguments_of_worker_run(tmp_path):
    argv = gate.work_argv("impl", tmp_path, Path("/w"))
    a = worker._parser().parse_args(argv)
    assert (a.kind, a.cwd, a.state_dir) == ("impl", "/w", str(tmp_path))
    assert a.prompt_file == str(tmp_path / "impl-prompt.md") and a.result_file == str(tmp_path / "impl-result.json")


def test_an_unknown_work_name_stops_as_invalid(tmp_path, capsys):
    with pytest.raises(model.Refused, match="作業名"):
        gate.work_argv("deploy", tmp_path, tmp_path)
    assert gate.main(["work", "--state-dir", str(tmp_path), "deploy"]) == causes.exit_code("invalid")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"


def test_ndf_answer_work_hands_the_arguments_to_worker_run():
    got: list[list[str]] = []
    code = gate._cmd_work(["--state-dir", "/s", "--cwd", "/w", "fix"], lambda exe, argv: got.append(argv))
    assert code == 0 and got[0][1:5] == ["-P", "-m", "ndf", "worker"] and got[0][5:] == gate.work_argv("fix", Path("/s"), Path("/w"))


def test_the_template_worker_has_no_default_until_prompts_are_written():
    # PR 3496 の指摘: `<置き場>/<作業名>-prompt.md` を書く処理が無いまま既定にすると worker のステップが必ず落ちる。
    # 既定を持たず、決まらなければ実行の前に TemplateValuesMissing で止まる
    assert route.params()["worker"].default is None
    assert set(gate.WORK_KINDS) >= {"impl", "fix", "design", "plan-to-spec"}


def test_ndf_progress_work_runs_the_stage_work_and_not_ndf_answer_work(repo, st, new_table, monkeypatch):
    # next が起こす `ndf progress work <進行表>` は工程を流す作業で、`ndf answer work`（--state-dir が要る）へ入らない
    tid = new_table()
    seen: list[tuple[str, str]] = []

    def fake(a, cwd, st_):
        seen.append((a.action, a.machine_id))
        return result.result("progress", result.OK, "流した")

    monkeypatch.setattr(entry, "_progress", fake)
    assert gate.progress_main(["work", tid]) == 0
    assert seen == [("work", tid)]
