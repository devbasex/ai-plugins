"""`ndf delegate-run`（委譲の起動）の引数の組み立てと、起動の前の確かめ（#1959 の受け入れ条件 13）。

`os.execvpe` と名前空間の確認は差し替え、実の子プロセスを起動しない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli, identity
from ndf.adapters import agents
from ndf.adapters import launch_settings as ls
from ndf.adapters import model_choice
from ndf.contracts import causes, internal
from ndf.domains.settings import delegate_run_cli as drc

PRECONDITION = causes.exit_code("precondition")
# 同梱の表の委譲（工程 delegate）の codex の基準の組
EFFORT = ["-c", "model_reasoning_effort=medium"]
# 値の制約に外れる（スキーマには合う）選択肢。値を選ぶ起動だけが止まる
BROKEN_EXPERIMENTS = {"stages": {"delegate": {"arms": {"codex": []}}}}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(ls.launch_approval, "repo_key", lambda r: str(r))
    monkeypatch.chdir(root)
    monkeypatch.delenv("NDF_AI_CHILD", raising=False)
    for key in ("launch-codex-model", "launch-codex-effort"):
        monkeypatch.delenv(identity.env_name(key), raising=False)
    return root


@pytest.fixture
def execs(monkeypatch) -> list[tuple[str, list[str], dict[str, str]]]:
    """`os.execvpe` の代わりに呼び出しを記録する（置き換わらずに戻る）。"""
    seen: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(drc.os, "execvpe", lambda file, argv, env: seen.append((file, list(argv), dict(env))))
    return seen


def _approve_unsandboxed(root: Path, runtimes: list[str], experiments: dict | None = None) -> str:
    path = root / ".ndf" / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {"version": 1, "permissions": {"unsandboxed": runtimes}, **({"experiments": experiments} if experiments else {})}
    path.write_text(json.dumps(raw), encoding="utf-8")
    sha = ls.approval_target(ls.load(root)).sha256
    ls.launch_approval.append(str(root), sha, ls.launch_approval.Action.APPROVE, approver="人")
    return sha


# ---------------------------------------------------------------- build_command


@pytest.mark.parametrize("args", [["-s", "workspace-write"], []])
def test_the_place_holder_is_spread_into_the_safety_arguments(args: list[str]) -> None:
    got = drc.build_command("codex", ["codex", "exec", "{args}", "--model", "m"], args)
    assert got == ["codex", "exec", *args, "--model", "m"]


@pytest.mark.parametrize(
    "command",
    [
        ["codex", "exec", "--model", "m"],  # {args} が無い
        ["codex", "exec", "{args}", "{args}"],  # 2 つある
        ["codex", "exec", "x{args}"],  # 単独の引数でない
        ["codex", "{args}", "exec"],  # サブコマンドの前（codex の最上位のオプションとして読まれる）
        ["codex", "exec", "--config", "{args}"],  # 別のオプションの値の位置
        ["codex", "exec", "--", "{args}"],  # プロンプトの位置
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
    head = drc._prefix(runtime, drc.facts.row("runtimes", runtime))
    with pytest.raises(internal.InternalError) as got:
        drc.build_command(runtime, [*head, "{args}", word, "x"], [])
    assert got.value.kind == "precondition" and word.split("=")[0][:2] in str(got.value)


def test_a_prompt_after_the_options_end_is_not_taken_for_a_safety_argument() -> None:
    assert drc.build_command("codex", ["codex", "exec", "{args}", "--", "-s を説明する"], []) == ["codex", "exec", "--", "-s を説明する"]


# ---------------------------------------------------------------- main


def test_the_delegate_replaces_itself_with_the_cli_marked_as_an_ai_child(repo: Path, monkeypatch, execs, capsys) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}", "-"])
    (file, argv, env) = execs[0]
    assert file == "codex" and argv == ["codex", "exec", *EFFORT, "-s", "workspace-write", "-C", str(repo), "-"]
    assert env["NDF_AI_CHILD"] == "1"
    assert capsys.readouterr().out == ""


def test_a_read_only_delegate_takes_the_review_row(repo: Path, monkeypatch, execs) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    drc.main(["--runtime", "codex", "--use", "review", "--", "codex", "exec", "{args}"])
    assert execs[0][1] == ["codex", "exec", *EFFORT, "-s", "read-only", "-C", str(repo)]


def test_without_namespaces_and_an_approval_it_stops_before_the_cli(repo: Path, monkeypatch, execs, capsys) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"]) == PRECONDITION
    out = capsys.readouterr()
    assert execs == [] and out.out == "" and "launch-config" in out.err


def test_an_approved_delegate_without_namespaces_takes_the_unsandboxed_arguments(repo: Path, monkeypatch, execs) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    _approve_unsandboxed(repo, ["codex"])
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"])
    assert execs[0][1] == ["codex", "exec", *EFFORT, "--dangerously-bypass-approvals-and-sandbox", "-C", str(repo)]


def test_a_revoke_between_the_arguments_and_the_launch_stops_it(repo: Path, monkeypatch, execs, capsys) -> None:
    """読み直しが理由を返せば、CLI に置き換わらない。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: False)
    sha = _approve_unsandboxed(repo, ["codex"])
    real = ls.safety_args

    def then_revoke(*a, **kw):
        got = real(*a, **kw)
        ls.launch_approval.append(str(repo), sha, ls.launch_approval.Action.REVOKE)
        return got

    monkeypatch.setattr(drc.ls, "safety_args", then_revoke)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"]) == PRECONDITION
    assert execs == [] and "launch-config" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flag", [["-C", "."], ["-C", "/elsewhere"], ["-C/elsewhere"], ["-C=/elsewhere"], ["--cd", "/elsewhere"], ["--cd=/elsewhere"]]
)
def test_a_working_directory_given_by_the_command_itself_is_refused(repo: Path, monkeypatch, execs, capsys, flag) -> None:
    """承認と名前空間は今のディレクトリで確かめるので、作業場所は NDF が今のディレクトリで入れ、コマンドの指定は形によらず拒む。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}", *flag]) == PRECONDITION
    assert execs == [] and "-C" in capsys.readouterr().err


def test_kiro_takes_the_safety_arguments_after_its_non_interactive_chat(repo: Path, execs) -> None:
    _approve_unsandboxed(repo, ["kiro"])
    drc.main(["--runtime", "kiro", "--", "kiro-cli", "chat", "--no-interactive", "{args}", "--model", "m"])
    assert execs[0][1] == ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "--model", "m"]


@pytest.mark.parametrize(("use", "sandbox"), [(["--use", "review"], "read-only"), ([], "workspace-write")])
def test_the_use_given_is_passed_to_the_safety_arguments(repo: Path, monkeypatch, execs, use: list[str], sandbox: str) -> None:
    """用途は `--use` で受けた値を安全装置へ渡す（review を delegate に決め打ちすると、読むだけの委譲が書き込める）。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    seen: list[str] = []
    real = ls.safety_args

    def spy(runtime, given_use, cwd, **kw):
        seen.append(given_use)
        return real(runtime, given_use, cwd, **kw)

    monkeypatch.setattr(drc.ls, "safety_args", spy)
    drc.main(["--runtime", "codex", *use, "--", "codex", "exec", "{args}"])
    assert seen == [use[1] if use else "delegate"] and execs[0][1][4:6] == ["-s", sandbox]


@pytest.mark.parametrize(("can", "reads"), [(True, 1), (False, 2)])
def test_codex_reads_the_launch_settings_once_to_build_the_arguments(repo: Path, monkeypatch, execs, can: bool, reads: int) -> None:
    """引数の組み立てで読むのは 1 回。サンドボックスなしの経路だけ、起動の直前の照らし直しがもう 1 回読む。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: can)
    _approve_unsandboxed(repo, ["codex"])
    real = ls.load
    seen: list[Path] = []

    def counted(*a, **kw):
        seen.append(a[0])
        return real(*a, **kw)

    monkeypatch.setattr(ls, "load", counted)
    drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"])
    assert len(execs) == 1 and len(seen) == reads


@pytest.mark.parametrize("can", [True, False])
def test_a_broken_choice_stops_the_codex_delegate_before_the_cli(repo: Path, monkeypatch, execs, capsys, can: bool) -> None:
    monkeypatch.setattr(agents, "user_namespaces", lambda: can)
    _approve_unsandboxed(repo, ["codex"], BROKEN_EXPERIMENTS)
    assert drc.main(["--runtime", "codex", "--", "codex", "exec", "{args}"]) == PRECONDITION
    out = capsys.readouterr()
    assert execs == [] and out.out == "" and "experiments" in out.err


@pytest.mark.parametrize(
    ("runtime", "head", "args"),
    [("kiro", ["kiro-cli", "chat", "--no-interactive"], ["--trust-all-tools"]), ("agy", ["agy"], ["--dangerously-skip-permissions"])],
)
def test_kiro_and_agy_take_only_the_safety_arguments_even_with_a_broken_choice(repo: Path, monkeypatch, execs, runtime, head, args) -> None:
    """kiro と agy は値を選ばない（モデルは手順書が `--model` で書く）。選択肢の誤りでは止まらない。"""
    _approve_unsandboxed(repo, [runtime], BROKEN_EXPERIMENTS)

    def never(*a, **kw):
        raise AssertionError("launch_choice を呼んだ")

    monkeypatch.setattr(model_choice, "launch_choice", never)
    drc.main(["--runtime", runtime, "--", *head, "{args}", "x"])
    assert execs[0][1] == [*head, *args, "x"]


@pytest.mark.parametrize("argv", [["--runtime", "codex"], ["--runtime", "nobody", "--", "x", "{args}"], ["--", "codex", "{args}"]])
def test_a_usage_error_goes_only_to_standard_error(repo: Path, execs, capsys, argv) -> None:
    assert cli.main(["delegate-run", *argv]) == PRECONDITION
    out = capsys.readouterr()
    assert execs == [] and out.out == "" and out.err
