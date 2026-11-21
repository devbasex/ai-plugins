"""外部 CLI の下限（`cli_floor.require`）と、起動の箇所がすべて比べを持つこと（#3691 の受け入れ条件 3・I12）。

偽の CLI は `PATH` の先に置く。`--version` で決めたバージョンを出し、受けた引数を 1 行ずつ記録する
（`--version` を何回打ったかを記録の行で数える）。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf import identity
from ndf.adapters import agents, claude, cli, cli_floor, github, launch_slot
from ndf.contracts import facts
from ndf.domains.project import measure_ci
from ndf.domains.relay import account_login, wrapper
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay import cli as relay_cli

SRC = identity.ROOT / "src" / "ndf"
BIN = identity.ROOT / "bin"
FLOOR = {name: facts.value("cli-floors", name, "min_version") for name in facts.ids("cli-floors")}
VERSION_LINE = {"gh": "gh version {v} (2026-01-01)", "claude": "{v} (Claude Code)", "codex": "codex-cli {v}"}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """比べの結果の再利用をテストごとに空から始める。"""
    monkeypatch.setattr(cli_floor, "_SEEN", {})


def _older(version: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    return f"{major}.{minor}.{patch - 1}" if patch else f"{major}.{minor - 1}.99" if minor else f"{major - 1}.99.99"


def fake_cli(bin_dir: Path, name: str, version_out: str, log: Path, body: str = "exit 0") -> Path:
    """`--version` で `version_out` を出し、受けた引数を `log` へ 1 行ずつ足す偽の CLI。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / name
    exe.write_text(
        f'#!/bin/sh\necho "$*" >> "{log}"\nif [ "$1" = "--version" ]; then\n  echo "{version_out}"\n  exit 0\nfi\n{body}\n',
        encoding="utf-8",
    )
    exe.chmod(0o755)
    return exe


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.fixture
def old(tmp_path, monkeypatch):
    """下限より 1 つ古いバージョンを返す偽の CLI（gh・claude・codex）を `PATH` の先に置く。記録のパスを返す。"""
    log = tmp_path / "calls.log"
    for name, floor in FLOOR.items():
        fake_cli(tmp_path / "old-bin", name, VERSION_LINE[name].format(v=_older(floor)), log)
    monkeypatch.setenv("PATH", f"{tmp_path / 'old-bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return log


def assert_reason(text: str, name: str) -> None:
    """理由が CLI の名前・見つけたバージョン・下限を持つ。"""
    assert name in text and _older(FLOOR[name]) in text and FLOOR[name] in text


# ---------------------------------------------------------------- 表


def test_table_has_the_three_clis_and_runtime_executables_match():
    assert set(FLOOR) == {"gh", "claude", "codex"}
    for name in ("claude", "codex"):
        assert facts.value("cli-floors", name, "executable") == facts.value("runtimes", name, "executable")
    for name, line in VERSION_LINE.items():
        m = re.search(facts.value("cli-floors", name, "version_pattern"), line.format(v="1.2.3-alpha.1"))
        assert m is not None and m.group(1) == "1.2.3-alpha.1"


# ---------------------------------------------------------------- require（I12）


@pytest.mark.parametrize("name", ["gh", "claude", "codex"])
def test_i12_newer_and_equal_versions_pass_and_one_older_is_refused(tmp_path, name):
    """I12: 下限と同じか新しければ通し、1 つ古ければ名前・見つけたバージョン・下限を持つ `TooOld`。"""
    log = tmp_path / "log"
    for i, (version, refused) in enumerate([("99.0.0", False), (FLOOR[name], False), (_older(FLOOR[name]), True)]):
        exe = fake_cli(tmp_path / f"b{i}", name, VERSION_LINE[name].format(v=version), log)
        env = {"PATH": str(exe.parent)}
        if not refused:
            cli_floor.require(name, name, env)
            continue
        with pytest.raises(cli_floor.TooOld) as got:
            cli_floor.require(name, name, env)
        assert_reason(str(got.value), name)
        assert (got.value.name, got.value.found, got.value.floor, got.value.path) == (name, version, FLOOR[name], str(exe))


def test_i12_a_prerelease_of_the_floor_is_refused(tmp_path):
    """I12: 下限と同じ数字のプレリリースは下限に満たない。"""
    exe = fake_cli(tmp_path / "b", "codex", VERSION_LINE["codex"].format(v=FLOOR["codex"] + "-alpha.1"), tmp_path / "log")
    with pytest.raises(cli_floor.TooOld):
        cli_floor.require("codex", "codex", {"PATH": str(exe.parent)})


def test_unreadable_output_warns_once_and_passes(tmp_path, capsys):
    exe = fake_cli(tmp_path / "b", "claude", "something else", tmp_path / "log")
    env = {"PATH": str(exe.parent)}
    cli_floor.require("claude", "claude", env)
    cli_floor.require("claude", "claude", env)
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and "claude" in err[0] and FLOOR["claude"] in err[0]
    assert calls(tmp_path / "log") == ["--version"]


def test_a_version_that_does_not_answer_in_time_warns_and_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_floor, "VERSION_SECONDS", 0.5)
    exe = tmp_path / "b" / "gh"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\nsleep 30 &\nwait\n", encoding="utf-8")
    exe.chmod(0o755)
    started = time.monotonic()
    cli_floor.require("gh", "gh", {"PATH": str(exe.parent)})
    assert time.monotonic() - started < 15
    assert "gh" in capsys.readouterr().err


def test_the_second_call_on_the_same_executable_does_not_run_version(tmp_path):
    log = tmp_path / "log"
    exe = fake_cli(tmp_path / "b", "claude", VERSION_LINE["claude"].format(v="99.0.0"), log)
    for _ in range(3):
        cli_floor.require("claude", "claude", {"PATH": str(exe.parent)})
        cli_floor.require("claude", str(exe))
    assert calls(log) == ["--version"]


def test_a_path_that_resolves_to_another_executable_is_compared_again(tmp_path):
    log = tmp_path / "log"
    new = fake_cli(tmp_path / "new", "gh", VERSION_LINE["gh"].format(v="99.0.0"), log)
    old = fake_cli(tmp_path / "old", "gh", VERSION_LINE["gh"].format(v=_older(FLOOR["gh"])), log)
    cli_floor.require("gh", "gh", {"PATH": str(new.parent)})
    with pytest.raises(cli_floor.TooOld):
        cli_floor.require("gh", "gh", {"PATH": f"{old.parent}{os.pathsep}{new.parent}"})
    assert calls(log) == ["--version", "--version"]


def test_a_relative_path_resolves_from_the_launch_cwd(tmp_path):
    fake_cli(tmp_path / "work" / "bin", "codex", VERSION_LINE["codex"].format(v=_older(FLOOR["codex"])), tmp_path / "log")
    with pytest.raises(cli_floor.TooOld):
        cli_floor.require("codex", "./bin/codex", None, tmp_path / "work")
    with pytest.raises(cli_floor.TooOld):
        cli_floor.require("codex", "codex", {"PATH": "bin"}, tmp_path / "work")


@pytest.mark.parametrize("name", ["kiro", "agy"])
def test_names_without_a_floor_run_nothing(tmp_path, name):
    log = tmp_path / "log"
    exe = fake_cli(tmp_path / "b", name, "0.0.1", log)
    cli_floor.require(name, name, {"PATH": str(exe.parent)})
    assert calls(log) == []


def test_a_missing_executable_passes():
    cli_floor.require("claude", "claude", {"PATH": "/nonexistent"})


# ---------------------------------------------------------------- 起動の箇所（古い CLI で起動せずに理由を返す）


def test_run_agent_refuses_an_old_codex_without_launching(old, tmp_path):
    spec = agents.Launch("codex", ("codex", "exec"), tmp_path, agents.AgentPaths.at(tmp_path / "run" / "codex"), 30)
    got = agents.run_agent(spec, poll=0.05)
    assert got.state == agents.AgentState.REFUSED
    assert_reason(got.detail, "codex")
    assert calls(old) == ["--version"]


def test_run_agent_refused_by_training_does_not_run_version(old, tmp_path, monkeypatch):
    """学習の設定で拒む経路では `--version` も打たない。"""
    monkeypatch.setattr(launch_slot.training_cache, "refusal", lambda agent, env=None: "学習の設定を確かめていない")
    spec = agents.Launch("codex", ("codex", "exec"), tmp_path, agents.AgentPaths.at(tmp_path / "run" / "codex"), 30)
    assert agents.run_agent(spec, poll=0.05).state == agents.AgentState.REFUSED
    assert calls(old) == []


def test_call_claude_refuses_an_old_claude(old):
    got = claude.call_claude(["claude", "-p"], "質問", 30)
    assert not got.ok and got.failure is claude.Failure.REFUSED
    assert_reason(got.error or "", "claude")
    assert calls(old) == ["--version"]


def test_claude_plugin_refuses_an_old_claude(old, tmp_path):
    got = claude.run_plugin(["update", "x"], 30, dict(os.environ), tmp_path)
    assert got.code is None
    assert_reason(got.error, "claude")
    assert claude.list_plugins(cwd=tmp_path) is None
    assert calls(old) == ["--version"]


def test_github_token_refuses_an_old_gh(old):
    with pytest.raises(github.GitHubError) as got:
        github.token()
    assert_reason(str(got.value), "gh")
    assert calls(old) == ["--version"]


def test_measure_ci_refuses_an_old_gh(old, tmp_path):
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 30).raw("repos/o/r")
    assert_reason(str(got.value), "gh")
    assert calls(old) == ["--version"]


def test_launch_args_stops_with_an_old_claude(old, capsys, last_result):
    assert cli.main([]) != 0
    out = last_result(capsys.readouterr().out)
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert_reason(out["summary"], "claude")
    assert calls(old) == ["--version"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が要る（CI は setup-uv で入れる）")
def test_ndf_claude_does_not_exec_an_old_claude(old, tmp_path):
    uv_cache = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")
    done = subprocess.run(
        [str(BIN / "ndf-claude"), "-p", "x"],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "UV_CACHE_DIR": uv_cache},
        check=False,
    )
    assert done.returncode != 0
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["status"] == "stopped"
    assert_reason(report["summary"], "claude")
    assert calls(old) == ["--version"]


def test_relay_wrapper_does_not_start_an_old_claude(old, tmp_path):
    from relay_children import make_settings

    spawned = []
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=lambda *a: spawned.append(a), clock=lambda: 0.0)
    ended = w._start(wrapper.Launch(1, "s", None, "", resume=False))
    assert ended is not None and ended.cause == "precondition"
    assert_reason(ended.reason, "claude")
    assert spawned == [] and calls(old) == ["--version"]


def test_relay_passthrough_does_not_exec_an_old_claude(old, capsys, monkeypatch):
    monkeypatch.setattr(relay_cli.os, "execvp", lambda *a: pytest.fail("古い claude を exec した"))
    assert relay_cli._passthrough(["-p", "x"], SimpleNamespace(notice=False, passthrough="")) != 0
    assert_reason(capsys.readouterr().err, "claude")


def test_account_login_refuses_an_old_claude(old, tmp_path):
    with pytest.raises(account_login.LoginError) as got:
        account_login.add(tmp_path / "store", "work1", None, dict(os.environ))
    assert got.value.kind == "precondition"
    assert_reason(str(got.value), "claude")
    with pytest.raises(account_login.LoginError) as status:
        account_login._identity(str(Path(shutil.which("claude"))), tmp_path / "cfg", dict(os.environ))
    assert status.value.kind == "precondition"
    assert [c for c in calls(old) if c != "--version"] == []


def test_account_remove_with_an_old_claude_keeps_the_registration(old, tmp_path, monkeypatch, capsys, last_result):
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path / "store"))
    d = tmp_path / "store" / "work1"
    d.mkdir(parents=True)
    (d / reg.ACCOUNT_FILE).write_text(json.dumps({"name": "work1", "email": "w@example.com", "needs_relogin": False}))
    shared = tmp_path / "shared.json"
    shared.write_text("{}")
    (d / "settings.json").symlink_to(shared)

    assert relay_cli.main(["account", "remove", "work1"]) != 0

    out = last_result(capsys.readouterr().out)
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert_reason(out["summary"], "claude")
    assert (d / reg.ACCOUNT_FILE).is_file() and (d / "settings.json").is_symlink()
    assert calls(old) == ["--version"]


# ---------------------------------------------------------------- 起動の箇所を探す（I12）

TABLE_EXECUTABLES = {facts.value("cli-floors", n, "executable") for n in FLOOR}
# 実行ファイルの名前を持つ変数・定数の名前（`CLAUDE = facts.value("runtimes", "claude", "executable")` と、解いたパスの `claude`）
EXECUTABLE_NAMES = {n.upper() for n in TABLE_EXECUTABLES} | TABLE_EXECUTABLES
# 起動を担う呼び出しの名前（`subprocess.*`・`os.exec*` のほか、差し替えられる起動の関数）
LAUNCHER_NAMES = {"run", "spawn", "Popen"}
# argv を引数で受けて起動する箇所（静的には実行ファイルの名前が見えない）。比べを持つ関数か、比べを通った後にだけ呼ばれる関数
DYNAMIC_SITES = {
    ("adapters/agents.py", "_guarded_run"),
    ("adapters/claude.py", "call_claude"),
    # 登録の解除は logout の中で claude を打つ。detach の前に比べ、古ければ登録も symlink も残す
    ("domains/relay/cli_account.py", "_acc_remove"),
    # bin/ndf-claude が exec claude の前に打つ launch-args
    ("adapters/cli.py", "main"),
}
# 比べを持つ呼び出し: `require` と、`require` を呼ぶ確かめの関数（名前で見るので、汎用の名前の関数は足さない）
GUARDS = {
    "require",
    "require_claude",  # relay/account_login.py: 古い claude を LoginError("precondition") に直す
    "_claude",  # relay/account_login.py: PATH から claude を解いて require_claude を呼ぶ
}
# 表の実行ファイルを起動するが比べを持たない関数と、その理由
EXEMPT = {
    # 試験の基盤: 実物の claude の振る舞いを確かめるテストが使う語の並び（起動はしない）
    ("testing/behavior.py", "claude_check_command"): "起動の語の並びを作るだけ",
}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _launches(fn: ast.AST) -> bool:
    """関数の中で起動を担う呼び出しがあるか。"""
    for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
        names = _names(call.func)
        if "subprocess" in names or any(n.startswith("exec") for n in names if n != "executable") or names & LAUNCHER_NAMES:
            return True
    return False


def _executable_lists(fn: ast.AST) -> bool:
    """関数の中に、表の実行ファイルを頭に置く語の並び（リストの字面）があるか。"""
    for lst in (n for n in ast.walk(fn) if isinstance(n, ast.List) and n.elts):
        head = lst.elts[0]
        if isinstance(head, ast.Constant) and head.value in TABLE_EXECUTABLES:
            return True
        if isinstance(head, (ast.Name, ast.Attribute)) and (head.id if isinstance(head, ast.Name) else head.attr) in EXECUTABLE_NAMES:
            return True
    return False


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _calls(fn: ast.AST) -> set[str]:
    return {name for call in ast.walk(fn) if isinstance(call, ast.Call) for name in [_call_name(call)] if name}


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def unguarded_sites(files: dict[str, str]) -> list[tuple[str, str]]:
    """表の実行ファイルを起動するのに、比べ（`GUARDS`）を呼ばない関数の (ファイル, 関数名)。"""
    trees = {rel: ast.parse(text) for rel, text in files.items()}
    bad = []
    for rel, tree in trees.items():
        for fn in _functions(tree):
            if (rel, fn.name) in EXEMPT or not _executable_lists(fn):
                continue
            if (_launches(fn) or fn.name.startswith("_start")) and not _calls(fn) & GUARDS:
                bad.append((rel, fn.name))
    return bad


def _sources() -> dict[str, str]:
    return {str(p.relative_to(SRC)): p.read_text(encoding="utf-8") for p in sorted(SRC.rglob("*.py"))}


def test_i12_every_launch_site_in_src_compares_with_the_floor():
    """I12: `src/ndf` の中で表の実行ファイルを起動する関数は、すべて比べを持つ。"""
    assert unguarded_sites(_sources()) == []


def test_i12_dynamic_launch_sites_compare_with_the_floor():
    """I12: argv を受けて起動する箇所（実行ファイルの名前が字面に無い）も比べを持つ。"""
    sources = _sources()
    for rel, name in DYNAMIC_SITES:
        fn = next(f for f in _functions(ast.parse(sources[rel])) if f.name == name)
        assert _calls(fn) & GUARDS, (rel, name)


def test_i12_adding_a_launch_site_without_the_floor_fails():
    """I12: 比べを通らない起動の箇所を足すと、探すテストが落ちる。"""
    added = {
        **_sources(),
        "x/new_gh.py": 'import subprocess\n\ndef list_prs():\n    return subprocess.run(["gh", "pr", "list"])\n',
        "x/new_claude.py": "import os\nfrom ndf.adapters.claude import CLAUDE\n\ndef go():\n    os.execvp(CLAUDE, [CLAUDE])\n",
    }
    assert unguarded_sites(added) == [("x/new_gh.py", "list_prs"), ("x/new_claude.py", "go")]


def test_i12_bin_scripts_exec_a_table_cli_only_after_launch_args():
    """I12: `bin/` の中で表の実行ファイルを exec するシェルは、先に `ndf launch-args` を打つ。"""
    exec_line = re.compile(r"^\s*exec\s+(" + "|".join(sorted(TABLE_EXECUTABLES)) + r")\b", re.M)
    bad = []
    for p in sorted(BIN.iterdir()):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = exec_line.search(text)
        if m and "launch-args" not in text[: m.start()]:
            bad.append(p.name)
    assert bad == []


def test_version_runs_are_counted_once_per_executable_for_launch_args(old, capsys, monkeypatch):
    """`ndf launch-args` 相当の 1 回の起動で、`--version` は実行ファイルごとに 1 回（一覧の読み取りは比べを再利用する）。"""
    log = old
    exe = Path(shutil.which("claude"))
    exe.write_text(exe.read_text().replace(_older(FLOOR["claude"]), "99.0.0"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["ndf", "launch-args"])
    assert cli.main([]) == 0
    assert calls(log).count("--version") == 1
