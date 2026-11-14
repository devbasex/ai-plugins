"""雛形の `{{worker}}` の既定（`ndf answer work`）: 作業名を `ndf worker run` の必須の引数へ合わせる（#3295）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts import causes
from ndf.core import route, worker
from ndf.progress import gate, model


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
    assert code == 0 and got[0][1:4] == ["-m", "ndf", "worker"] and got[0][4:] == gate.work_argv("fix", Path("/s"), Path("/w"))


def test_the_template_default_of_worker_is_ndf_answer_work():
    default = route.params()["worker"].default
    assert default[:3] == ["ndf", "answer", "work"] and "{state_dir}" in default
    assert set(gate.WORK_KINDS) >= {"impl", "fix", "design", "plan-to-spec"}
