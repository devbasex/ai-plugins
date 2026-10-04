"""mcp-serena のテストが共有する道具。スクリプトの置き場所を import の経路へ足す。"""

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
CLI = SCRIPTS / "serena-lsp.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
# SessionStart の hook に環境（~/.cache/mcp-serena）を用意させない（#1142 の決定 25。テストは根の環境の ruamel.yaml を使う）
os.environ.setdefault("MCP_SERENA_ENV", "0")


def make_repo(root: Path, files: dict) -> Path:
    """files の {相対パス: 中身} を書き、すべてを追跡した git リポジトリを作る。"""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def files_of(**counts) -> dict:
    """files_of(py=3, sh=1) → {"f0.py": "", ..., "f0.sh": ""}。"""
    out = {}
    for ext, n in counts.items():
        for i in range(n):
            out[f"src/f{i}.{ext}"] = "x\n"
    return out


# 利用者のシェルから子プロセスへ渡さない変数。テストは HOME で設定の置き場所を隔てる（#1689）
INHERIT_DROP = ("CLAUDE_CONFIG_DIR",)


def child_env(env=None, drop=()) -> dict:
    """os.environ の写しから INHERIT_DROP と drop を外し、env を重ねる。env に書いた値は外さない。"""
    full = {k: v for k, v in os.environ.items() if k not in INHERIT_DROP and k not in drop}
    full.update(env or {})
    return full


def run_cli(*args, env=None, cwd=None, stdin=None):
    full_env = child_env(env)
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=full_env, cwd=cwd, input=stdin)


def run_json(*args, **kw):
    proc = run_cli(*args, **kw)
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc
