"""`launch-cli.sh` のランタイム別引数を固定する。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import pytest


LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "launch-cli.sh"
STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for arg in "$@"; do printf '%s\\0' "$arg" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""


def _launch(
    tmp_path: pathlib.Path,
    runtime: str,
    *,
    allowed_tools: str | None = None,
    model: str = "test-model",
    interpreter: str | None = None,
    extra_env: dict | None = None,
) -> list[str]:
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("現状を確認してください\n", encoding="utf-8")
    stem = tmp_path / "output" / runtime
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = "kiro-cli" if runtime == "kiro" else runtime
    stub = bin_dir / executable
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    args_file = tmp_path / "args.bin"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "NDF_TEST_ARGS_FILE": str(args_file),
    }
    # 従量の接続の区間の中でテストを回しても、宣言を足さない既定の形を見る（足す形は extra_env で渡す）。
    env.pop("NDF_CLAUDE_ACCOUNT", None)
    env.update(extra_env or {})
    if allowed_tools is not None:
        env["NDF_CLAUDE_ALLOWED_TOOLS"] = allowed_tools

    # 上限は数字で渡す（`limits.py` を引かず、python3 の無い bash 3.2 の環境でも起動まで進む）。
    command = [str(LAUNCH), runtime, str(workdir), str(prompt), str(stem), model, "", "60"]
    subprocess.run(
        [interpreter, *command] if interpreter else command,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.01)
    assert args_file.is_file(), f"{runtime} が起動されていない"
    return args_file.read_bytes().decode().split("\0")[:-1]


def test_codex_launch_arguments(tmp_path):
    args = _launch(tmp_path, "codex")

    assert args[:2] == ["exec", "--dangerously-bypass-approvals-and-sandbox"]
    assert args[args.index("--config") + 1] == "reasoning.effort=medium"
    assert args[args.index("-C") + 1] == str(tmp_path / "work")
    assert args[args.index("--model") + 1] == "test-model"


def test_claude_launch_arguments_and_allowed_tools_override(tmp_path):
    args = _launch(tmp_path, "claude", allowed_tools="Read,Grep")

    assert args[0] == "-p"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--allowed-tools") + 1] == "Read,Grep"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "test-model"


def test_claude_launch_passes_metered_declaration_as_settings(tmp_path):
    """従量の接続の区間から起動する claude にも、宣言を `--settings` の env で渡す（#1543）。資格情報は載せない。"""
    decl = "AWS_REGION=ap-northeast-1 AWS_BEARER_TOKEN_BEDROCK=b-SECRET"
    args = _launch(tmp_path, "claude", extra_env={"NDF_CLAUDE_ACCOUNT": "metered", "NDF_SUPERVISE_CLAUDE_FALLBACK": decl})

    assert args[0] == "-p" and "SECRET" not in " ".join(args)
    assert json.loads(args[args.index("--settings") + 1]) == {"env": {"AWS_REGION": "ap-northeast-1"}}
    assert args[args.index("--allowed-tools") + 1] == "Bash,Read,Write,Edit,Glob,Grep"


@pytest.mark.parametrize("env", [{}, {"NDF_CLAUDE_ACCOUNT": "a"}, {"NDF_CLAUDE_ACCOUNT": "metered", "NDF_SUPERVISE_CLAUDE_FALLBACK": ""}])
def test_claude_launch_without_metered_declaration_passes_no_settings(tmp_path, env):
    """アカウントの区間・宣言の無い従量の接続では `--settings` を足さない。"""
    assert "--settings" not in _launch(tmp_path, "claude", extra_env={"NDF_SUPERVISE_CLAUDE_FALLBACK": "AWS_REGION=r", **env})


def test_kiro_launch_arguments(tmp_path):
    args = _launch(tmp_path, "kiro")

    assert args[:3] == ["chat", "--no-interactive", "--trust-all-tools"]
    assert args[args.index("--model") + 1] == "test-model"


# 空の配列を `set -u` の下で展開しても起動まで進むこと（#476）。bash 4.4 未満（macOS の既定の
# 3.2）は空配列の `"${arr[@]}"` を unbound variable として扱い、`nohup` の行を実行しない。
# bash 3.2 の実体は `NDF_TEST_BASH32` で渡す（無ければ手元の bash で同じ経路を通す）。
@pytest.mark.parametrize("runtime", ["codex", "agy", "claude", "kiro"])
def test_launch_without_model_passes_no_model_flag(tmp_path, runtime):
    args = _launch(tmp_path, runtime, model="")

    assert "--model" not in args


@pytest.mark.skipif(not os.environ.get("NDF_TEST_BASH32"), reason="bash 3.2 の実体が無い")
@pytest.mark.parametrize("runtime", ["codex", "agy", "claude", "kiro"])
def test_launch_without_model_under_bash32(tmp_path, runtime):
    args = _launch(tmp_path, runtime, model="", interpreter=os.environ["NDF_TEST_BASH32"])

    assert "--model" not in args
