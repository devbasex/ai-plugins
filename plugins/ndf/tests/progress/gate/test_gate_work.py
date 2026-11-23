"""`ndf answer work`: 作業名を `ndf worker run` の必須の引数へ合わせる（#3295）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.contracts import causes, result
from ndf.core import route, worker
from ndf.progress import gate, invoke, model, worker_kinds
from ndf.progress.gate import entry

KIND_KEYS = {"work", "summary", "skills", "excerpt", "inputs", "cwd", "outputs"}


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
    # #4029: src は置き換えた先の argv（sys.path）で渡す。PYTHONPATH には入れない。標準出力の文字コードは今のものを写す
    want = cli.ndf_command("worker", *gate.work_argv("fix", Path("/s"), Path("/w")), keep_streams=True)
    assert code == 0 and got[0] == want


def test_the_template_worker_has_no_default_until_prompts_are_written():
    # PR 3496 の指摘: `<保存先>/<作業名>-prompt.md` を書く処理が無いまま既定にすると worker のステップが必ず落ちる。
    # 既定を持たず、決まらなければ実行の前に TemplateValuesMissing で止まる
    assert route.params()["worker"].default is None
    assert set(gate.work_kinds()) >= {"impl", "fix", "design", "plan-to-spec"}


def test_ndf_progress_work_runs_the_stage_work_and_not_ndf_answer_work(repo, st, new_table, monkeypatch):
    # next が起動する `ndf progress work <ステートマシン>` は工程を流す作業で、`ndf answer work`（--state-dir が要る）へ入らない
    tid = new_table()
    seen: list[tuple[str, str]] = []

    def fake(a, cwd, st_):
        seen.append((a.action, a.machine_id))
        return result.result("progress", result.OK, "流した")

    monkeypatch.setattr(entry, "_progress", fake)
    assert gate.progress_main(["work", tid]) == 0
    assert seen == [("work", tid)]


def _worker_names(value) -> set[str]:
    """プランの中で `{{worker}}` の後ろに置かれた作業の名前。"""
    if isinstance(value, dict):
        return set().union(*map(_worker_names, value.values()))
    if isinstance(value, list):
        named = {value[i + 1] for i, v in enumerate(value[:-1]) if v == "{{worker}}"}
        return named.union(*map(_worker_names, value))
    return set()


def _kinds_in_templates() -> dict[str, set[str]]:
    """作業の名前 → それを使うテンプレートの名前（手元のテストのステップを含めても外しても）。"""
    out: dict[str, set[str]] = {}
    for name, t in route.templates().items():
        for local_tests in (True, False):
            for kind in _worker_names(route.expanded(t, local_tests=local_tests)):
                out.setdefault(kind, set()).add(name)
    return out


def test_work_kinds_are_the_names_the_templates_hand_to_the_worker():
    kinds = gate.work_kinds()
    assert set(kinds) == set(_kinds_in_templates())
    assert len(kinds) == 18 and all(set(k) == KIND_KEYS for k in kinds.values())


def test_templates_using_a_work_in_the_worktree_take_the_worktree():
    templates = route.templates()
    kinds = worker_kinds.work_kinds()
    for kind, names in _kinds_in_templates().items():
        if kinds[kind]["cwd"] == "worktree":
            assert all("worktree" in templates[n].params for n in names), kind


def test_every_work_name_in_the_definition_becomes_worker_run_arguments(tmp_path):
    argv = gate.work_argv("requirements", tmp_path, tmp_path)
    assert worker._parser().parse_args(argv).kind == "requirements"


def test_kind_gaps_is_empty_when_the_stage_input_holds_the_value():
    values = {"repo": "/r", "worker": ["ndf-worker"]}
    assert worker_kinds.kind_gaps("requirements", values, {"issues": [12, 34]}) == {}
    assert worker_kinds.work_dir_of("requirements", values) == "/r"


def test_kind_values_take_the_template_value_over_the_stage_input():
    got = worker_kinds.kind_values("requirements", {"issues": ["12"]}, {"issues": [34], "mode": "standard"})
    assert got == {"issues": ["12"]}


def test_kind_gaps_names_the_stage_input_that_is_missing():
    got = worker_kinds.kind_gaps("requirements", {"repo": "/r"}, {})
    assert got == {"issues": "工程の入力 issues（工程の定義の metadata.ndf.inputs）"}


def test_kind_gaps_names_the_template_value_by_its_source():
    values = {"worktree": "/w", "issue": "5", "acceptance": "a.json"}
    got = worker_kinds.kind_gaps("impl", values, {}, sources=invoke.FILL_SOURCES)
    assert got == {"branch": invoke.FILL_SOURCES["branch"]}


def test_kind_gaps_spares_the_issue_when_the_acceptance_is_given_for_several_issues():
    values = {"worktree": "/w", "branch": "b", "acceptance": ["--issue", "5", "--issue", "6"]}
    assert worker_kinds.kind_gaps("impl", values, {}, covers=route.LAUNCH_SOURCES) == {}
    assert set(worker_kinds.kind_gaps("impl", {**values, "acceptance": []}, {}, covers=route.LAUNCH_SOURCES)) == {
        "issue",
        "acceptance",
    }


def test_kind_gaps_names_the_empty_work_dir():
    values = {"worktree": "", "branch": "b", "issue": "5", "acceptance": "a.json"}
    assert worker_kinds.kind_gaps("impl", values, {}) == {"worktree": "作業 impl の作業場所（差し込み worktree）"}
    assert worker_kinds.work_dir_of("impl", values) is None
