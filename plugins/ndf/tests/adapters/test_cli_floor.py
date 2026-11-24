"""外部 CLI の下限の表と、起動の箇所がすべて比べ（`cli_floor.require`）を持つこと（#3691 の受け入れ条件 3・I12）。"""

from __future__ import annotations

import ast
import re

import pytest

from ndf import identity
from ndf.adapters import cli_floor
from ndf.contracts import facts

SRC = identity.ROOT / "src" / "ndf"
BIN = identity.ROOT / "bin"
FLOOR = {name: facts.value("cli-floors", name, "min_version") for name in facts.ids("cli-floors")}
VERSION_LINE = {"gh": "gh version {v} (2026-01-01)", "claude": "{v} (Claude Code)", "codex": "codex-cli {v}"}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """比べの結果の再利用をテストごとに空から始める。"""
    monkeypatch.setattr(cli_floor, "_SEEN", {})


# ---------------------------------------------------------------- 表


def test_table_has_the_three_clis_and_runtime_executables_match():
    assert set(FLOOR) == {"gh", "claude", "codex"}
    for name in ("claude", "codex"):
        assert facts.value("cli-floors", name, "executable") == facts.value("runtimes", name, "executable")
    for name, line in VERSION_LINE.items():
        m = re.search(facts.value("cli-floors", name, "version_pattern"), line.format(v="1.2.3-alpha.1"))
        assert m is not None and m.group(1) == "1.2.3-alpha.1"


# ---------------------------------------------------------------- require（I12）


def test_a_missing_executable_passes():
    cli_floor.require("claude", "claude", {"PATH": "/nonexistent"})


# ---------------------------------------------------------------- 起動の箇所を探す（I12）

TABLE_EXECUTABLES = {facts.value("cli-floors", n, "executable") for n in FLOOR}
# 実行ファイルの名前を持つ変数・定数の名前（`CLAUDE = facts.value("runtimes", "claude", "executable")` と、解いたパスの `claude`）
EXECUTABLE_NAMES = {n.upper() for n in TABLE_EXECUTABLES} | TABLE_EXECUTABLES
# 起動を担う呼び出しの名前（`subprocess.*`・`os.exec*` のほか、差し替えられる起動の関数）
LAUNCHER_NAMES = {"run", "spawn", "Popen"}
# argv を引数で受けて起動する箇所（静的には実行ファイルの名前が見えない）。比べを持つ関数か、比べを通った後にだけ呼ばれる関数
DYNAMIC_SITES = {
    ("adapters/agents.py", "_guarded_run"),
    # call_claude の起動はどれも _prefaced を通り、確かめが確定した起動ごとの環境と実行ファイルで比べる
    ("adapters/claude.py", "_prefaced"),
    # 登録の解除は logout の中で claude を打つ。detach の前に比べ、古ければ登録も symlink も残す
    ("domains/relay/cli_account.py", "_acc_remove"),
    # bin/ndf-claude が exec claude の前に打つ launch-args
    ("adapters/cli.py", "main"),
}
# 比べを持つ呼び出し: `require` と、`require` を呼ぶ確かめの関数（名前で見るので、汎用の名前の関数は足さない）
GUARDS = {
    "require",
    "refuse_old",  # adapters/cli_floor.py: 古ければ起動の箇所の例外へ直す
    "require_claude",  # relay/account_login.py: 古い claude を LoginError("precondition") に直す
    "_precheck",  # adapters/claude.py: 起動の直前の確かめ（recheck の後に require_claude）
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


def test_record_key_reports_the_key_and_whether_it_is_recorded(tmp_path, monkeypatch):
    # #4068: バージョンの記録のキー（実パス・更新時刻・大きさ）と、記録に在るかを `--version` を打たずに返す
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho '2.1.330 (Claude Code)'\n", encoding="utf-8")
    exe.chmod(0o755)
    ran = []
    real = cli_floor._read_version
    monkeypatch.setattr(cli_floor, "_read_version", lambda *a: ran.append(a) or real(*a))
    st = exe.stat()
    assert cli_floor.record_key(str(exe)) == ((str(exe), st.st_mtime_ns, st.st_size), False)
    assert ran == []
    assert cli_floor.version_at("claude", str(exe)) == "2.1.330"
    assert cli_floor.record_key(str(exe))[1] is True
    exe.write_text("#!/bin/sh\necho '2.1.331 (Claude Code)'  \n", encoding="utf-8")
    assert cli_floor.record_key(str(exe))[1] is False
    assert cli_floor.record_key(str(tmp_path / "none")) is None and cli_floor.record_key(None) is None
    assert len(ran) == 1
