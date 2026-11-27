"""`ndf delegate-run`（委譲の起動）の引数の組み立てと、起動の前の確かめ（#1959 の受け入れ条件 13）。

`os.execvpe` と名前空間の確認は差し替え、実の子プロセスを起動しない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.adapters import agents
from ndf.adapters import launch_settings as ls
from ndf.contracts import causes, internal
from ndf.domains.settings import delegate_run_cli as drc

PRECONDITION = causes.exit_code("precondition")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(ls.launch_approval, "repo_key", lambda r: str(r))
    monkeypatch.chdir(root)
    monkeypatch.delenv("NDF_AI_CHILD", raising=False)
    return root


@pytest.fixture
def execs(monkeypatch) -> list[tuple[str, list[str], dict[str, str]]]:
    """`os.execvpe` の代わりに呼び出しを記録する（置き換わらずに戻る）。"""
    seen: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(drc.os, "execvpe", lambda file, argv, env: seen.append((file, list(argv), dict(env))))
    return seen


def _approve_unsandboxed(root: Path, runtimes: list[str]) -> str:
    path = root / ".ndf" / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "permissions": {"unsandboxed": runtimes}}), encoding="utf-8")
    sha = ls.approval_target(ls.load(root)).sha256
    ls.launch_approval.append(str(root), sha, ls.launch_approval.Action.APPROVE, approver="人")
    return sha


# ---------------------------------------------------------------- build_command


@pytest.mark.parametrize("args", [["-s", "workspace-write"], []])
def test_the_place_holder_is_spread_into_the_safety_arguments(args: list[str]) -> None:
    got = drc.build_command("codex", ["codex", "exec", "{args}", "-C", "/w"], args)
    assert got == ["codex", "exec", *args, "-C", "/w"]


@pytest.mark.parametrize(
    "command",
    [
        ["codex", "exec", "-C", "/w"],  # {args} が無い
        ["codex", "exec", "{args}", "{args}"],  # 2 つある
        ["codex", "exec", "x{args}"],  # 単独の引数でない
        ["kiro-cli", "chat", "{args}"],  # 別のランタイムの CLI
        ["sh", "-c", "codex exec {args}"],
        [],
    ],
)
def test_a_command_without_one_place_holder_or_of_another_cli_is_refused(command: list[str]) -> None:
    with pytest.raises(internal.InternalError) as got:
        drc.build_command("codex", command, [])
    assert got.value.kind == "precondition"


@pytest.mark.parametrize(
    ("runtime", "word"),
    [
        ("codex", "-s"),
        ("codex", "--sandbox"),
        ("codex", "--sandbox=danger-full-access"),
        ("codex", "-sdanger-full-access"),
        ("codex", "--dangerously-bypass-approvals-and-sandbox"),
        ("kiro", "--trust-all-tools"),
        ("kiro", "-a"),
        ("kiro", "--trust-tools=fs_write"),
        ("agy", "--dangerously-skip-permissions"),
    ],
)
def test_a_safety_argument_given_by_the_command_itself_is_refused(runtime: str, word: str) -> None:
    """安全装置の引数は NDF だけが入れる。利用者のコマンドが自分で持てば、判定を迂回するので拒む。"""
    exe = drc.facts.value("runtimes", runtime, "executable")
    with pytest.raises(internal.InternalError) as got:
        drc.build_command(runtime, [exe, "{args}", word, "x"], [])
    assert got.value.kind == "precondition" and word.split("=")[0][:2] in str(got.value)


def test_a_prompt_after_the_options_end_is_not_taken_for_a_safety_argument() -> None:
    assert drc.build_command("codex", ["codex", "exec", "{args}", "--", "-s を説明する"], []) == ["codex", "exec", "--", "-s を説明する"]


# ---------------------------------------------------------------- main


def test_the_delegate_replaces_itself_with_the_cli_marked_as_an_ai_child(repo: Path, monkeypatch, execs, capsys) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}", "-C", str(repo)])
    (file, argv, env) = execs[0]
    assert file == "codex" and argv == ["codex", "exec", "-s", "workspace-write", "-C", str(repo)]
    assert env["NDF_AI_CHILD"] == "1"
    assert capsys.readouterr().out == ""


def test_a_read_only_delegate_takes_the_review_row(repo: Path, monkeypatch, execs) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    drc.main(["--runtime", "codex", "--use", "review", "--", "codex", "exec", "{args}"])
    assert execs[0][1] == ["codex", "exec", "-s", "read-only"]


def test_without_namespaces_and_an_approval_it_stops_before_the_cli(repo: Path, monkeypatch, execs, capsys) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"]) == PRECONDITION
    out = capsys.readouterr()
    assert execs == [] and out.out == "" and "launch-config" in out.err


def test_an_approved_delegate_without_namespaces_takes_the_unsandboxed_arguments(repo: Path, monkeypatch, execs) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    _approve_unsandboxed(repo, ["codex"])
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"])
    assert execs[0][1] == ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"]


def test_a_revoke_between_the_arguments_and_the_launch_stops_it(repo: Path, monkeypatch, execs, capsys) -> None:
    """読み直しが理由を返せば、CLI に置き換わらない。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    sha = _approve_unsandboxed(repo, ["codex"])
    real = ls.safety_args

    def then_revoke(*a):
        got = real(*a)
        ls.launch_approval.append(str(repo), sha, ls.launch_approval.Action.REVOKE)
        return got

    monkeypatch.setattr(drc.ls, "safety_args", then_revoke)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"]) == PRECONDITION
    assert execs == [] and "launch-config" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["-C", "/elsewhere"], ["--cd", "/elsewhere"], ["--cd=/elsewhere"]])
def test_a_working_directory_other_than_the_current_one_is_refused(repo: Path, monkeypatch, execs, capsys, flag) -> None:
    """承認と名前空間は今のディレクトリで確かめるので、別の作業場所を指す起動は拒む。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}", *flag]) == PRECONDITION
    assert execs == [] and "-C" in capsys.readouterr().err


def test_a_relative_working_directory_that_is_the_current_one_passes(repo: Path, monkeypatch, execs) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}", "-C", "."])
    assert execs


@pytest.mark.parametrize("argv", [["--runtime", "codex"], ["--runtime", "nobody", "--", "x", "{args}"], ["--", "codex", "{args}"]])
def test_a_usage_error_goes_only_to_standard_error(repo: Path, execs, capsys, argv) -> None:
    assert cli.main(["delegate-run", *argv]) == PRECONDITION
    out = capsys.readouterr()
    assert execs == [] and out.out == "" and out.err
