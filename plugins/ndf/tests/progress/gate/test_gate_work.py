"""`ndf answer work`: 起動指示を組んで書き、作業名を `ndf worker run` の必須の引数へ合わせる（#3295・#3908）。"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

import pytest

from ndf import cli, contracts, identity
from ndf.contracts import causes, result, worker_paths
from ndf.core import route, worker
from ndf.progress import gate, invoke, model, worker_kinds
from ndf.progress.gate import entry

KIND_KEYS = {"work", "summary", "skills", "excerpt", "inputs", "cwd", "outputs"}
# `agent-layers.md` の「supervisor → worker」が定める起動指示の 7 項目（見出しでそろう）
PROMPT_ITEMS = ("作業", "入力", "手順", "読む Skill", "返す形", "置く場所", "作業の結果ファイル")


def _saved(plan: str, values: dict, inputs: dict) -> dict:
    """`invoke.read_values` が返す形（プランの名前・差し込みの値・工程の入力）。"""
    return {"plan": plan, "values": values, "inputs": inputs, "local_tests": True, "at": "2026-11-23T00:00:00+00:00"}


def _prompt(kind: str, saved: dict, tmp_path: Path, workdir: str = "/w") -> str:
    return entry.work_prompt(kind, saved, Path(workdir), worker_paths.report_paths(tmp_path, kind, resolve=False))


def test_the_prompt_holds_the_seven_items_and_the_seven_rules(tmp_path):
    body = _prompt("requirements", _saved("requirements", {"repo": "/r"}, {"issues": [12, 34]}), tmp_path, "/r")
    assert [h for h in PROMPT_ITEMS if f"\n## {h}\n" in body] == list(PROMPT_ITEMS)
    assert re.findall(r"^(\d+)\. ", body, re.M) == [str(n) for n in range(1, 8)]


def test_the_prompt_takes_the_stage_input_the_template_params_drop(tmp_path):
    # `requirements.json` の params に issues が無くても、工程の入力の課題 2 件が入力の欄に載る（決定 2）
    body = _prompt("requirements", _saved("requirements", {"repo": "/r"}, {"issues": [12, 34]}), tmp_path, "/r")
    assert re.search(r"^- issues: 12, 34$", body, re.M)
    assert "$" not in body


def test_the_work_name_becomes_the_required_arguments_of_worker_run(tmp_path):
    argv = gate.work_argv("impl", tmp_path, Path("/w"))
    a = worker._parser().parse_args(argv)
    assert (a.kind, a.cwd, a.state_dir) == ("impl", "/w", str(tmp_path))
    paths = worker_paths.report_paths(tmp_path, "impl", resolve=False)
    assert (a.prompt_file, a.result_file) == (str(paths.prompt), str(paths.result))


def test_the_argv_requires_the_outputs_the_work_has_to_return(tmp_path):
    # 工程「作業場所の用意」は worktree・branch を結果の outputs で返す（`kinds.json` の outputs）
    argv = gate.work_argv("worktree", tmp_path, Path("/r"))
    assert argv[-4:] == ["--require-output", "worktree", "--require-output", "branch"]
    assert "--require-output" not in gate.work_argv("requirements", tmp_path, Path("/r"))
    # 受け側（`ndf worker run`）のパーサーが必須の出力の名前を受ける（拒まれて worker の起動前に止まらない）
    assert worker._parser().parse_args(argv).require_output == ["worktree", "branch"]
    assert worker._parser().parse_args(gate.work_argv("requirements", tmp_path, Path("/r"))).require_output == []


def test_ndf_answer_work_resets_the_report_and_the_done_marker_before_each_launch(tmp_path):
    # 起動指示が指す `ndf worker append --done` は報告が要り、前の完了マーカーが残ると今回の報告より先に見える
    invoke.write_values(tmp_path, "fix", {"worktree": "/w", "branch": "b"}, {}, True)
    paths = worker_paths.report_paths(tmp_path, "fix", resolve=False)
    paths.report.write_text("前の報告", encoding="utf-8")
    paths.done.write_text("", encoding="utf-8")
    paths.result.write_text("{}", encoding="utf-8")
    assert gate._cmd_work(["--state-dir", str(tmp_path), "fix"], lambda exe, argv: None) == 0
    assert paths.report.read_text(encoding="utf-8") == ""
    assert not paths.done.exists() and not paths.result.exists()


def test_a_result_file_that_is_a_link_stops_before_the_worker_and_keeps_its_target(tmp_path, capsys):
    invoke.write_values(tmp_path, "fix", {"worktree": "/w", "branch": "b"}, {}, True)
    target = tmp_path / "利用者のファイル"
    target.write_text("消さない", encoding="utf-8")
    worker_paths.report_paths(tmp_path, "fix", resolve=False).result.symlink_to(target)
    got: list[list[str]] = []
    code = gate._cmd_work(["--state-dir", str(tmp_path), "fix"], lambda exe, argv: got.append(argv))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == causes.exit_code("precondition") and got == [] and out["cause"]["kind"] == "precondition"
    assert target.read_text(encoding="utf-8") == "消さない"


def test_a_relative_cwd_reaches_the_prompt_and_the_argv_as_the_same_absolute_path(tmp_path, monkeypatch):
    invoke.write_values(tmp_path, "fix", {"worktree": "/w", "branch": "b"}, {}, True)
    (tmp_path / "rel").mkdir()
    monkeypatch.chdir(tmp_path)
    got: list[list[str]] = []
    assert gate._cmd_work(["--state-dir", str(tmp_path), "--cwd", "rel", "fix"], lambda exe, argv: got.append(argv)) == 0
    want = str((tmp_path / "rel").resolve())
    assert got[0][got[0].index("--cwd") + 1] == want
    prompt = worker_paths.report_paths(tmp_path, "fix", resolve=False).prompt
    assert f"\n- 作業場所: {want}\n" in prompt.read_text(encoding="utf-8")


def test_the_command_samples_quote_a_state_dir_with_a_space(tmp_path):
    state_dir = tmp_path / "保存 先"
    paths = worker_paths.report_paths(state_dir, "worktree", resolve=False)
    body = _prompt("worktree", _saved("worktree", {"repo": "/r"}, {"issues": [7]}), state_dir, "/r")
    assert f"--report {shlex.quote(str(paths.report))} --done" in body
    assert body.count(f"--out {shlex.quote(str(paths.result))}") == 2


def test_an_unknown_work_name_stops_as_invalid(tmp_path, capsys):
    with pytest.raises(model.Refused, match="作業名"):
        gate.work_argv("deploy", tmp_path, tmp_path)
    assert gate.main(["work", "--state-dir", str(tmp_path), "deploy"]) == causes.exit_code("invalid")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"


def test_ndf_answer_work_writes_the_prompt_then_hands_the_arguments_to_worker_run(tmp_path):
    invoke.write_values(tmp_path, "fix", {"worktree": str(tmp_path), "branch": "b"}, {}, True)
    got: list[list[str]] = []
    code = gate._cmd_work(["--state-dir", str(tmp_path), "--cwd", "/w", "fix"], lambda exe, argv: got.append(argv))
    # #4029: src は置き換えた先の argv（sys.path）で渡す。PYTHONPATH には入れない。標準出力の文字コードは今のものを写す
    want = cli.ndf_command("worker", *gate.work_argv("fix", tmp_path, Path("/w")), keep_streams=True)
    assert code == 0 and got[0] == want
    prompt = worker_paths.report_paths(tmp_path, "fix", resolve=False).prompt
    assert "\n- 作業場所: /w\n" in prompt.read_text(encoding="utf-8")


def test_ndf_answer_work_rewrites_the_prompt_when_it_is_run_again(tmp_path):
    invoke.write_values(tmp_path, "fix", {"worktree": "/w1", "branch": "b"}, {}, True)
    assert gate._cmd_work(["--state-dir", str(tmp_path), "fix"], lambda exe, argv: None) == 0
    invoke.write_values(tmp_path, "fix", {"worktree": "/w2", "branch": "b"}, {}, True)
    assert gate._cmd_work(["--state-dir", str(tmp_path), "fix"], lambda exe, argv: None) == 0
    prompt = worker_paths.report_paths(tmp_path, "fix", resolve=False).prompt
    assert "/w2" in prompt.read_text(encoding="utf-8") and "/w1" not in prompt.read_text(encoding="utf-8")


def test_the_work_runs_in_the_worktree_or_the_repo_by_its_definition(tmp_path):
    values = {"repo": "/r", "worktree": "/r/.worktrees/b", "branch": "b", "issue": "5", "acceptance": "a.json", "issues": [5]}
    invoke.write_values(tmp_path, "impl", values, {"issues": [5]}, True)
    got: list[list[str]] = []
    for kind in ("impl", "worktree", "requirements"):
        assert gate._cmd_work(["--state-dir", str(tmp_path), kind], lambda exe, argv: got.append(argv)) == 0
    assert [a[a.index("--cwd") + 1] for a in got] == ["/r/.worktrees/b", "/r", "/r"]


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
    for kind in worker_kinds.work_kinds():
        argv = gate.work_argv(kind, tmp_path, tmp_path)
        assert argv[:4] == ["run", "--kind", kind, "--prompt-file"], kind
    assert worker._parser().parse_args(gate.work_argv("requirements", tmp_path, tmp_path)).kind == "requirements"


def test_a_work_without_skills_or_an_excerpt_leaves_the_sections_empty(tmp_path):
    body = _prompt("fix-push", _saved("fix-push", {"branch": "b", "remote": "origin"}, {}), tmp_path)
    assert re.search(r"^## 読む Skill\n+無し$", body, re.M)
    assert str(identity.ROOT / "skills") not in body


def test_the_prompt_carries_the_excerpt_and_the_skill_paths_of_the_work(tmp_path):
    saved = _saved("plan-to-spec", {"branch": "b", "pr": "1"}, {"issues": [7]})
    body = _prompt("plan-to-spec", saved, tmp_path)
    spec = worker_kinds.work_kinds()["plan-to-spec"]
    assert (identity.ROOT / spec["excerpt"]).read_text(encoding="utf-8").strip() in body
    assert all(str(identity.ROOT / "skills" / n / "SKILL.md") in body for n in spec["skills"])


def test_the_prompt_of_the_worktree_work_shows_the_outputs_it_must_return(tmp_path):
    body = _prompt("worktree", _saved("worktree", {"repo": "/r"}, {"issues": [7]}), tmp_path, "/r")
    assert "worktree・branch" in body and '--outputs \'{"worktree": "<値>", "branch": "<値>"}\'' in body
    assert str(worker_paths.report_paths(tmp_path, "worktree", resolve=False).result) in body


@pytest.fixture
def contracts_copy(tmp_path):
    """コントラクトの写しを保存先にする（起動指示のテンプレートを差し替えて確かめる）。"""
    root = tmp_path / "contracts"
    shutil.copytree(contracts.DEFAULT_DIR, root)
    contracts.use_dir(root)
    yield root
    contracts.use_dir(None)


def test_the_template_escapes_a_dollar_and_refuses_an_unknown_placeholder(contracts_copy, tmp_path):
    saved = _saved("fix", {"branch": "b"}, {})
    (contracts_copy / entry.PROMPT_FILE).write_text("## 作業\n$work\n見本 $$HOME\n", encoding="utf-8")
    assert "見本 $HOME" in _prompt("fix", saved, tmp_path)
    (contracts_copy / entry.PROMPT_FILE).write_text("$work $nowhere\n", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="nowhere"):
        _prompt("fix", saved, tmp_path)


def test_a_state_dir_without_the_values_stops_before_the_worker(tmp_path, capsys):
    got: list[list[str]] = []
    code = gate._cmd_work(["--state-dir", str(tmp_path), "fix"], lambda exe, argv: got.append(argv))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == causes.exit_code("precondition") and got == []
    assert out["cause"]["kind"] == "precondition" and str(tmp_path) in out["summary"]
    assert not worker_paths.report_paths(tmp_path, "fix", resolve=False).prompt.exists()


def test_a_missing_stage_input_stops_with_the_name_and_its_source(tmp_path, capsys):
    invoke.write_values(tmp_path, "requirements", {"repo": "/r"}, {}, True)
    got: list[list[str]] = []
    code = gate._cmd_work(["--state-dir", str(tmp_path), "requirements"], lambda exe, argv: got.append(argv))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == causes.exit_code("precondition") and got == []
    assert [i["key"] for i in out["items"]] == ["issues"] and "metadata.ndf.inputs" in out["items"][0]["source"]
    assert not worker_paths.report_paths(tmp_path, "requirements", resolve=False).prompt.exists()


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
