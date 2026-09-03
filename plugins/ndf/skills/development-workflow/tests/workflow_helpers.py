"""工程の検知とマージの判定のテストが共有する補助。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突する。直接 import する補助はこの固有名のモジュールへ置く。

**通信は行わない。** `gh` は PATH の差し替えで作り物へ向ける。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
LIB = SCRIPTS / "lib" / "workflow-common.sh"
GUARD = SCRIPTS / "workflow-guard.sh"
STAGE_CHECK = SCRIPTS / "stage-check.sh"

REMOTE = "https://github.com/devbasex/ai-plugins.git"
SLUG = "devbasex/ai-plugins"


def base_env(state_dir: Path, extra: dict | None = None) -> dict:
    """控えの置き場所を試験用へ向けた環境を返す。"""
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C.UTF-8")
    env["CLAUDE_PLUGIN_DATA"] = str(state_dir)
    env["NDF_STAGE_LOCK_TIMEOUT"] = "1"
    if extra:
        env.update(extra)
    return env


def run_lib(snippet: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """共通ライブラリを読み込んだ上で `snippet` を bash で実行する。"""
    script = f'set -uo pipefail\n. "{LIB}"\n{snippet}\n'
    return subprocess.run(
        ["bash", "-c", script], cwd=str(cwd) if cwd else None,
        env=env or os.environ.copy(), capture_output=True, text=True,
    )


def run_stage_check(*args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(STAGE_CHECK), *args], cwd=str(cwd), env=env,
        capture_output=True, text=True,
    )


def run_guard(payload: dict, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD)], cwd=str(cwd), env=env,
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
    )


def pre_tool_use(command: str, cwd: Path) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": command},
    }


def init_repo(path: Path, remote: str | None = REMOTE) -> Path:
    """origin を持つリポジトリを作る。通信はしない（fetch も push もしない）。

    `remote` に `None` を渡すと origin を持たないリポジトリになる。
    """
    path.mkdir(parents=True, exist_ok=True)

    def run(*a: str) -> None:
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    if remote:
        run("remote", "add", "origin", remote)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


def checkout(repo: Path, branch: str) -> None:
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True, capture_output=True)


def stub_gh(bin_dir: Path, responses: dict[str, str]) -> Path:
    """`gh` を作り物へ差し替える。差し替え先のディレクトリを返す。

    `responses` は問い合わせの引数の一部から、返す本文への対応である。値が
    `!<終了コード>:<本文>` の形のときは、その終了コードで本文を標準エラーへ書く。
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    table = "\n".join(
        f'  *{json.dumps(key)}*) __ndf_reply {json.dumps(value)} ;;' for key, value in responses.items()
    )
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env bash\n"
        "__ndf_reply() {\n"
        '  case "$1" in\n'
        '    "!"*) local rest=${1#!}; printf "%s" "${rest#*:}" >&2; exit "${rest%%:*}" ;;\n'
        '  esac\n'
        '  printf "%s" "$1"; exit 0\n'
        "}\n"
        'args="$*"\n'
        'case "$args" in\n'
        f"{table}\n"
        '  *) printf "gh: unexpected call: %s\\n" "$args" >&2; exit 1 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    return bin_dir


def path_with(bin_dir: Path, without: tuple[str, ...] = ()) -> str:
    """`without` に挙げたコマンドだけを見えなくした PATH を返す。

    PATH を空にすると bash の組み込み以外が何も動かず、判定そのものへ入れない。
    実際に隠したいコマンドだけを外した経路を作る。
    """
    shim = bin_dir / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not Path(entry).is_dir():
            continue
        for src in Path(entry).iterdir():
            if src.name in without or (shim / src.name).exists():
                continue
            try:
                (shim / src.name).symlink_to(src)
            except OSError:
                pass
    return str(shim)


def state_file(state_dir: Path, issue: int, slug: str = SLUG) -> Path:
    return state_dir / "stages" / f"{slug.replace('/', '__')}__{issue}.json"
