"""agents の起動（置き場の名前・ファイルへの書き出し・新しいセッション・子へ渡す環境）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ndf.adapters import agents, run_log


def test_agent_paths_at_names_files_next_to_stem():
    paths = agents.AgentPaths.at(Path("/w/kiro"))

    names = (paths.out.name, paths.err.name, paths.launch.name, paths.progress.name)
    assert names == ("kiro.out", "kiro.err.log", "kiro-launch.json", "kiro-progress.log")


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境（I-011・I-012）


START_DUMP = (
    "import json, os, sys\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'X_SPEC', 'PIN_PARENT')}\n"
    "own = os.getsid(0) == os.getpid()\n"
    "print(json.dumps({'env': seen, 'cwd': os.getcwd(), 'stdin': sys.stdin.read(), 'own_session': own}))\n"
    "sys.stderr.write('err')\n"
)


def _start_spec(tmp_path: Path) -> agents.Launch:
    paths = agents.AgentPaths.at(tmp_path / "deep" / "run" / "codex")
    env = {"X_SPEC": "s", "NDF_RUN_ID": "from-spec"}
    return agents.Launch("codex", (sys.executable, "-c", START_DUMP), tmp_path, paths, 30, env=env)


def test_start_writes_files_in_new_session_with_spec_env(tmp_path, no_run_env):
    spec = _start_spec(tmp_path)

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": "from-spec", "NDF_RUN_LOG": None, "X_SPEC": "s", "PIN_PARENT": "kept"}
    assert (got["cwd"], got["stdin"], got["own_session"]) == (str(tmp_path), "", True)
    assert spec.paths.err.read_text() == "err"


def test_start_run_log_env_overrides_spec_env(tmp_path, no_run_env):
    spec = _start_spec(tmp_path)
    log = run_log.open_run("entry", run_id="r2", location="repo")

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": "r2", "NDF_RUN_LOG": str(log.path), "X_SPEC": "s", "PIN_PARENT": "kept"}


def test_start_without_spec_env_keeps_parent_env(tmp_path, no_run_env):
    spec = agents.Launch("codex", (sys.executable, "-c", START_DUMP), tmp_path, agents.AgentPaths.at(tmp_path / "codex"), 30)

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "X_SPEC": None, "PIN_PARENT": "kept"}
