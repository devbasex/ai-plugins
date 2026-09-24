"""configure: 設定の生成と 1 言語ずつの起動の検証（AC6・AC7・AC9・AC10）。"""
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from serena_lsp_testlib import CLI, files_of, make_repo, run_json
from serena_lsp import project_yml as py
from serena_lsp import verify

FAKE = Path(__file__).parent / "fixtures/fake_serena.py"
TEMPLATE = (Path(__file__).parent / "fixtures/serena-1.7.0-project.yml").read_text()


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo", files_of(py=30, sh=12, js=2))


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "serena.jsonl"
    monkeypatch.setenv("FAKE_SERENA_LOG", str(log))
    return {"cmd": f"{sys.executable} {FAKE}", "log": log}


def _configure(repo, fake, *extra, result=""):
    return run_json("configure", "--root", str(repo), "--json", "--serena", fake["cmd"], *extra,
                    env={"FAKE_SERENA_RESULT": result})


def _yml(repo):
    return (repo / ".serena/project.yml").read_text()


def _calls(fake):
    return [json.loads(l) for l in fake["log"].read_text().splitlines()]


def test_creates_project_yml_when_missing_and_verifies_each_language(repo, fake):
    code, out, _ = _configure(repo, fake)
    assert code == 0, out
    assert out["verified"] == ["python", "bash"]
    assert out["written"]["created"] is True
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == ["python", "bash"]
    assert py.read_list(text, "mcp_serena_excluded") == []
    calls = [c for c in _calls(fake) if "args" in c]
    assert calls[0]["args"][:2] == ["project", "create"]
    assert calls[0]["args"].count("--ls") == 2
    # 1 言語ずつ検証する
    assert [c["checked"] for c in _calls(fake) if "checked" in c] == [["python"], ["bash"]]
    # すべての起動に SERENA_HOME=.serena と cwd=根
    for call in calls:
        assert call["serena_home"] == ".serena"
        assert Path(call["cwd"]).resolve() == repo.resolve()


def test_serena_dir_is_ignored_before_verification(repo, fake):
    # 言語サーバのキャッシュ（.serena/language_servers/）を解析対象に選ばせない
    _configure(repo, fake)
    checks = [c for c in _calls(fake) if "checked" in c]
    assert checks and all(c["ignores_serena"] for c in checks)
    assert ".serena/**" in py.read_list(_yml(repo), "ignored_paths")


def test_existing_yml_keeps_other_keys(repo, fake):
    (repo / ".serena").mkdir()
    (repo / ".serena/project.yml").write_text(TEMPLATE)
    code, out, _ = _configure(repo, fake)
    assert code == 0
    assert out["written"]["created"] is False
    keys = ("language_servers", "ignored_paths", "mcp_serena_excluded")
    assert py.strip_blocks(_yml(repo), keys) == py.strip_blocks(TEMPLATE, keys)
    assert not any("create" in c.get("args", []) for c in _calls(fake))


def test_failed_language_is_excluded_with_reason(repo, fake):
    code, out, _ = _configure(repo, fake, result="bash=1")
    assert code == 0
    assert out["verified"] == ["python"]
    assert out["failed"][0]["language"] == "bash"
    assert out["failed"][0]["reason"] == "health_check_exit_1"
    assert out["failed"][0]["log"].endswith(".log")
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == ["python"]
    assert py.read_list(text, "mcp_serena_excluded") == ["bash health_check_exit_1"]


def test_only_marks_the_rest_not_selected(repo, fake):
    code, out, _ = _configure(repo, fake, "--only", "python")
    assert code == 0
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == ["python"]
    assert py.read_list(text, "mcp_serena_excluded") == ["bash not_selected"]


def test_all_failed_writes_empty_and_exits_1(repo, fake):
    code, out, _ = _configure(repo, fake, result="python=1,bash=2")
    assert code == 1
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == []
    assert py.read_list(text, "mcp_serena_excluded") == ["python health_check_exit_1",
                                                         "bash health_check_exit_2"]


def test_timeout_is_a_failure(repo, fake, monkeypatch):
    code, out, _ = run_json("configure", "--root", str(repo), "--json", "--serena", fake["cmd"],
                            env={"FAKE_SERENA_RESULT": "bash=hang", "SERENA_LSP_VERIFY_TIMEOUT": "1"})
    assert code == 0
    assert [(f["language"], f["reason"]) for f in out["failed"]] == [("bash", "timeout")]


def test_exception_midway_still_writes_verified_only(repo, fake, monkeypatch):
    real = verify.health_check
    calls = []

    def flaky(root, cmd, timeout):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(root, cmd, timeout)

    monkeypatch.setattr(verify, "health_check", flaky)
    with pytest.raises(RuntimeError):
        verify.configure(repo.resolve(), serena_cmd=fake["cmd"])
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == ["python"]


def test_sigterm_midway_still_writes_verified_only(repo, fake):
    env = dict(os.environ, FAKE_SERENA_RESULT="bash=hang")
    proc = subprocess.Popen([sys.executable, str(CLI), "configure", "--root", str(repo), "--json",
                             "--serena", fake["cmd"]], env=env, stdout=subprocess.PIPE, text=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        if fake["log"].exists() and '["bash"]' in fake["log"].read_text():
            break
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 128 + signal.SIGTERM
    text = _yml(repo)
    assert py.read_list(text, "language_servers") == ["python"]


def _hash_tree(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git/" not in str(p.relative_to(root)) + "/":
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_dry_run_writes_nothing(repo, fake):
    (repo / ".serena").mkdir()
    (repo / ".serena/project.yml").write_text(TEMPLATE)
    before = _hash_tree(repo)
    code, out, _ = _configure(repo, fake, "--dry-run", "--gitignore", "--serena-gitignore")
    assert code == 0
    assert _hash_tree(repo) == before
    assert not fake["log"].exists()
    assert "bash" in out["diff"]


def test_dry_run_reports_planned_exclusions(repo, fake):
    code, out, _ = _configure(repo, fake, "--dry-run", "--only", "python")
    assert code == 0
    assert out["written"]["language_servers"] == ["python"]
    assert out["written"]["excluded"] == ["bash not_selected"]
    assert not (repo / ".serena").exists()


def test_gitignore_only_with_flag(repo, fake):
    (repo / ".gitignore").write_text("node_modules/\n")
    _configure(repo, fake)
    assert (repo / ".gitignore").read_text() == "node_modules/\n"
    _configure(repo, fake, "--gitignore")
    _configure(repo, fake, "--gitignore")
    assert (repo / ".gitignore").read_text() == "node_modules/\n.serena/project.yml\n"


def test_serena_gitignore_only_with_flag(repo, fake):
    code, out, _ = _configure(repo, fake)
    assert (repo / ".serena/.gitignore").read_text() == "/cache\n/project.local.yml\n"
    assert out["written"]["serena_gitignore_added"] == ["/serena_config.yml", "/logs", "/language_servers"]
    for name in ("serena_config.yml", "logs/x.log", "language_servers/a", "cache/b"):
        p = repo / ".serena" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    _configure(repo, fake, "--serena-gitignore")
    code, out, _ = _configure(repo, fake, "--serena-gitignore")
    assert out["written"]["serena_gitignore_added"] == []
    assert (repo / ".serena/.gitignore").read_text().count("/logs") == 1
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", ".serena"],
                            capture_output=True, text=True).stdout
    for name in ("serena_config.yml", "logs/", "language_servers/", "cache/"):
        assert name not in status


def test_worktrees_ignored_path_added_only_when_dir_exists(repo, fake):
    _configure(repo, fake)
    assert py.read_list(_yml(repo), "ignored_paths") == [".serena/**"]
    (repo / ".worktrees").mkdir()
    _configure(repo, fake)
    _configure(repo, fake)
    assert py.read_list(_yml(repo), "ignored_paths") == [".serena/**", ".worktrees/**"]


@pytest.mark.parametrize("setup", ["flow", "local"])
def test_unreadable_or_local_override_exits_3_without_writing(repo, fake, setup):
    (repo / ".serena").mkdir()
    if setup == "flow":
        text = TEMPLATE.replace("language_servers:\n- python\n", "language_servers: [python]\n")
    else:
        text = TEMPLATE
        (repo / ".serena/project.local.yml").write_text("language_servers:\n- php\n")
    (repo / ".serena/project.yml").write_text(text)
    code, out, _ = _configure(repo, fake)
    assert code == 3
    assert out["error"]
    assert _yml(repo) == text
    assert not fake["log"].exists()


def test_missing_serena_command_exits_2(repo):
    code, out, _ = run_json("configure", "--root", str(repo), "--json", "--serena", "no-such-uvx-cmd serena")
    assert code == 2


def test_outside_git_exits_2(tmp_path, fake):
    code, _, _ = run_json("configure", "--root", str(tmp_path), "--json", "--serena", fake["cmd"])
    assert code == 2
