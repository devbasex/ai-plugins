"""起動スクリプトは head のコミットを状態ファイルから読む（#271）。

`launch-codex.sh` と `launch-agy.sh` は同じ値を別々に `gh pr view` で取っており、
ラウンドごとに GraphQL を 2 点使っていた。値は `start-round` が
`rounds[-1].head_sha` へ記録するので、そこから読む。

**前の版の状態ファイルから再開したときは記録が無い。** そのときだけ従来の
`gh pr view` へ落ちる。

CLI そのものは起動しない。`gh` / `codex` / `agy` を、呼ばれた事実を書き出すだけの
実行ファイルへ置き換える。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
COMMANDS = ("bash", "jq")
pytestmark = pytest.mark.skipif(
    any(shutil.which(c) is None for c in COMMANDS),
    reason=f"起動スクリプトが使う外部コマンドが無い（{' / '.join(COMMANDS)}）",
)

PR = 4242
STATE_SHA = "1111111111111111111111111111111111111111"
FALLBACK_SHA = "2222222222222222222222222222222222222222"

GH_STUB = f"""#!/bin/sh
printf '%s\\n' "$*" >> "$NDF_TEST_GH_LOG"
printf '{FALLBACK_SHA}\\n'
"""

CLI_STUB = """#!/bin/sh
exit 0
"""


def _setup(tmp_path: pathlib.Path, *, head_sha: str | None) -> tuple[pathlib.Path, dict]:
    worktree = tmp_path / "worktree"
    (worktree / ".git").mkdir(parents=True)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()

    round_ = {"round": 1, "pr": PR, "started_at": "2026-09-04T00:00:00+00:00"}
    if head_sha is not None:
        round_["head_sha"] = head_sha
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "worktree_path": str(worktree),
        "event_downgrade": False,
        "review_instructions": "",
        "rounds": [round_],
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    (tmp_dir / f"cross-review-pr{PR}-existing-comments.txt").write_text("(なし)\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gh", GH_STUB), ("codex", CLI_STUB), ("agy", CLI_STUB)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)

    gh_log = tmp_path / "gh.log"
    gh_log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CROSS_REVIEW_TMP_DIR": str(tmp_dir),
        "NDF_TEST_GH_LOG": str(gh_log),
    }
    return tmp_dir, {"env": env, "gh_log": gh_log}


def _launch(script: str, tmp_path: pathlib.Path, *, head_sha: str | None) -> tuple[str, str]:
    tmp_dir, ctx = _setup(tmp_path, head_sha=head_sha)
    subprocess.run(
        [str(SCRIPTS / script), str(PR), "1"],
        env=ctx["env"], check=True, capture_output=True, text=True,
    )
    stem = "codex" if "codex" in script else "agy"
    prompt = tmp_dir / f"{stem}-review-pr{PR}-prompt.md"
    for _ in range(100):
        if prompt.is_file():
            break
        time.sleep(0.05)
    assert prompt.is_file(), f"プロンプトが書かれていない: {prompt}"
    return prompt.read_text(encoding="utf-8"), ctx["gh_log"].read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ["launch-codex.sh", "launch-agy.sh"])
def test_the_launcher_reads_the_head_commit_from_the_state_file(script, tmp_path):
    prompt, gh_log = _launch(script, tmp_path, head_sha=STATE_SHA)

    assert f"commit_id (headRefOid): {STATE_SHA}" in prompt
    assert "pr view" not in gh_log


@pytest.mark.parametrize("script", ["launch-codex.sh", "launch-agy.sh"])
def test_the_launcher_falls_back_when_the_state_has_no_head_commit(script, tmp_path):
    prompt, gh_log = _launch(script, tmp_path, head_sha=None)

    assert f"commit_id (headRefOid): {FALLBACK_SHA}" in prompt
    assert "pr view" in gh_log
