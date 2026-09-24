"""check: 導入の検査（AC13・AC14・AC15）と、対応表だけで言語を足せること。"""
import json
from pathlib import Path

import pytest

from serena_lsp_testlib import run_json
from serena_lsp import check, table


def _project(root: Path, languages):
    (root / ".serena").mkdir(parents=True, exist_ok=True)
    body = "language_servers:\n" + "".join(f"- {l}\n" for l in languages) if languages else "language_servers: []\n"
    (root / ".serena/project.yml").write_text(body + "mcp_serena_excluded: []\n")
    return root


def _bin(tmp_path, *names):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for n in names:
        p = d / n
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    return d


def _home(tmp_path, plugins=None, raw=None):
    home = tmp_path / "home"
    (home / ".claude/plugins").mkdir(parents=True, exist_ok=True)
    path = home / ".claude/plugins/installed_plugins.json"
    if raw is not None:
        path.write_text(raw)
    elif plugins is not None:
        path.write_text(json.dumps({"version": 2, "plugins": {p: [{"scope": "user"}] for p in plugins}}))
    return home


def _check(root, home, bindir, *extra, env=None):
    e = {"HOME": str(home), "PATH": str(bindir)}
    e.update(env or {})
    e.pop("CLAUDE_CONFIG_DIR", None)
    return run_json("check", "--root", str(root), "--json", *extra, env=e)


def _items(out):
    return sorted((m["language"], m["item"]) for m in out["missing"])


def test_all_present_exits_0(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    home = _home(tmp_path, ["pyright-lsp@claude-plugins-official"])
    code, out, _ = _check(root, home, _bin(tmp_path, "pyright-langserver"))
    assert (code, out["missing"]) == (0, [])


def test_missing_plugin_and_binary_exit_1_with_install_commands(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    code, out, _ = _check(root, _home(tmp_path, []), _bin(tmp_path))
    assert code == 1
    assert _items(out) == [("python", "binary"), ("python", "plugin")]
    plugin = next(m for m in out["missing"] if m["item"] == "plugin")
    assert plugin["install"] == "claude plugin install pyright-lsp@claude-plugins-official"


def test_no_installed_plugins_json_counts_as_missing(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    code, out, _ = _check(root, _home(tmp_path), _bin(tmp_path, "pyright-langserver"))
    assert code == 1
    assert _items(out) == [("python", "plugin")]


def test_broken_installed_plugins_json_exits_2_but_not_for_codex(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    home = _home(tmp_path, raw="{broken")
    code, _, _ = _check(root, home, _bin(tmp_path, "pyright-langserver"))
    assert code == 2
    code, out, _ = _check(root, home, _bin(tmp_path), "--runtime", "codex")
    assert (code, out["missing"]) == (0, [])


def test_missing_project_yml_exits_2(tmp_path):
    (tmp_path / "r").mkdir()
    code, _, _ = _check(tmp_path / "r", _home(tmp_path, []), _bin(tmp_path))
    assert code == 2


def test_local_override_languages_are_checked(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    (root / ".serena/project.local.yml").write_text("language_servers:\n- php\n")
    code, out, _ = _check(root, _home(tmp_path, []), _bin(tmp_path))
    assert {m["language"] for m in out["missing"]} == {"php"}


def test_missing_items_skips_unknown_language(tmp_path, monkeypatch):
    root = _project(tmp_path / "r", ["nosuchlang", "python"])
    monkeypatch.setenv("HOME", str(_home(tmp_path, [])))
    monkeypatch.setenv("PATH", str(_bin(tmp_path)))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    missing = check.missing_items(root, "claude-code")

    assert sorted((item["language"], item["item"]) for item in missing) == [
        ("python", "binary"),
        ("python", "plugin"),
    ]


def test_typescript_check_is_skipped_when_language_server_is_missing(tmp_path, monkeypatch):
    root = _project(tmp_path / "r", ["typescript"])
    monkeypatch.setenv("HOME", str(_home(tmp_path, ["typescript-lsp@claude-plugins-official"])))
    monkeypatch.setenv("PATH", str(_bin(tmp_path)))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    missing = check.missing_items(root, "claude-code")

    assert [(item["language"], item["item"]) for item in missing] == [("typescript", "binary")]


def test_typescript_is_missing_when_package_cannot_be_found(tmp_path, monkeypatch):
    root = _project(tmp_path / "r", ["typescript"])
    monkeypatch.setenv("HOME", str(_home(tmp_path, ["typescript-lsp@claude-plugins-official"])))
    monkeypatch.setenv("PATH", str(_bin(tmp_path, "typescript-language-server")))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    missing = check.missing_items(root, "claude-code")

    assert [item["item"] for item in missing] == ["typescript_major_5"]


def test_typescript_is_missing_when_package_json_is_broken(tmp_path, monkeypatch):
    root = _project(tmp_path / "r", ["typescript"])
    bindir = _bin(tmp_path, "typescript-language-server")
    pkg = bindir / "node_modules/typescript"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text("{broken")
    monkeypatch.setenv("HOME", str(_home(tmp_path, ["typescript-lsp@claude-plugins-official"])))
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    missing = check.missing_items(root, "claude-code")

    assert [item["item"] for item in missing] == ["typescript_major_5"]


@pytest.mark.parametrize("version,flagged", [("7.0.2", True), ("5.9.3", False)])
def test_typescript_major_5(tmp_path, version, flagged):
    root = _project(tmp_path / "r", ["typescript"])
    bindir = _bin(tmp_path, "typescript-language-server")
    pkg = bindir / "node_modules/typescript"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "typescript", "version": version}))
    code, out, _ = _check(root, _home(tmp_path, ["typescript-lsp@claude-plugins-official"]), bindir)
    assert (("typescript", "typescript_major_5") in _items(out)) is flagged
    assert code == (1 if flagged else 0)


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_shellcheck(tmp_path, runtime):
    root = _project(tmp_path / "r", ["bash"])
    home = _home(tmp_path, [])
    code, out, _ = _check(root, home, _bin(tmp_path), "--runtime", runtime)
    assert (code, _items(out)) == (1, [("bash", "shellcheck")])
    code, out, _ = _check(root, home, _bin(tmp_path, "shellcheck"), "--runtime", runtime)
    assert (code, out["missing"]) == (0, [])


def test_codex_skips_claude_code_items(tmp_path):
    root = _project(tmp_path / "r", ["python", "typescript"])
    code, out, _ = _check(root, _home(tmp_path, []), _bin(tmp_path), "--runtime", "codex")
    assert (code, out["missing"]) == (0, [])


def _custom_table(tmp_path, extra):
    data = table.load()
    data["languages"].append({"serena": "zig", "extensions": [".zig"], "claude_plugin": None,
                              "binaries": [], "extra_checks": extra})
    path = tmp_path / "languages.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_new_language_with_known_check_needs_no_code_change(tmp_path):
    root = _project(tmp_path / "r", ["zig"])
    env = {"SERENA_LSP_TABLE": _custom_table(tmp_path, ["shellcheck"])}
    code, out, _ = _check(root, _home(tmp_path, []), _bin(tmp_path), env=env)
    assert (code, _items(out)) == (1, [("zig", "shellcheck")])


def test_unknown_extra_check_exits_2(tmp_path):
    root = _project(tmp_path / "r", ["python"])
    env = {"SERENA_LSP_TABLE": _custom_table(tmp_path, ["no_such_check"])}
    code, _, _ = _check(root, _home(tmp_path, []), _bin(tmp_path), env=env)
    assert code == 2


def test_installed_plugins_top_level_array_is_unreadable(tmp_path, monkeypatch):
    from serena_lsp import check
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins/installed_plugins.json").write_text("[]")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with pytest.raises(check.Unreadable):
        check.installed_plugins()
