"""hook のテストの共通の部品。見本の入力・入口の呼び出し・宛先のリポジトリの作り方。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.domains.hooks import main, tables

SAMPLES = Path(__file__).resolve().parent / "samples"
SRC = tables.PLUGIN_ROOT / "src"
# 中継・オーガナイザー・通知・止め方の変数（テストを流す人の値を読まない）
ENV_NAMES = ("NDF_RELAY_DIR", "NDF_ORGANIZER_DIR", "NDF_ORGANIZER_LANE", "NDF_FACTORY_RUN", "NDF_HOOK_OFF", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")


def sample_paths() -> list[Path]:
    return sorted(SAMPLES.glob("*/*/*.json"))


def _fill(value: Any, places: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _fill(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, places) for v in value]
    return places.get(value, value) if isinstance(value, str) else value


def read_sample(path: Path, cwd: Path, **override: Any) -> dict[str, Any]:
    """見本の入力。作業ディレクトリと会話の記録のパスを置き換え、`override` の鍵を上書きする。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    data = _fill(data, {"<cwd>": str(cwd), "<transcript>": str(cwd / "transcript.jsonl")})
    data.update(override)
    return data


def sample(runtime: str, event: str, cwd: Path, **override: Any) -> dict[str, Any]:
    return read_sample(next(SAMPLES.glob(f"{runtime}/*/{event}.json")), cwd, **override)


def bash(cwd: Path, command: str, runtime: str = "claude", **tool_input: Any) -> dict[str, Any]:
    return sample(runtime, "PreToolUse", cwd, tool_name="Bash", tool_input={"command": command, **tool_input})


def stop(cwd: Path, reply: str, runtime: str = "claude", **override: Any) -> dict[str, Any]:
    return sample(runtime, "Stop", cwd, last_assistant_message=reply, **override)


def call_entry(runtime: str, event: str, payload: Any) -> Any:
    """入口を同じプロセスで呼び、出力の JSON（無ければ None）を返す。終了コードはいつも 0。"""
    out = io.StringIO()
    text = payload if isinstance(payload, str) else json.dumps(payload)
    assert main.run_entry(["entry.py", runtime, event], io.StringIO(text), out) == 0
    got = out.getvalue()
    return json.loads(got) if got.strip() else None


def state_dir() -> Path:
    return identity.load().state_dir


def records() -> list[dict[str, Any]]:
    """計測の記録のすべての行。"""
    lines = []
    for path in sorted((state_dir() / "hooks").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines


def make_repo(root: Path, slug: str = "owner/repo", delivery: Any = None, worktree: bool = False) -> Path:
    """宛先のリポジトリ（.git の設定に origin と、.ndf/project.json の delivery）を作る。`delivery` が None なら宣言を置かない。"""
    root.mkdir(parents=True, exist_ok=True)
    config = f'[core]\n\tbare = false\n[remote "origin"]\n\turl = git@github.com:{slug}.git\n'
    if worktree:
        common = root.parent / f"{root.name}-common.git"
        gitdir = common / "worktrees" / root.name
        gitdir.mkdir(parents=True, exist_ok=True)
        (common / "config").write_text(config, encoding="utf-8")
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        (root / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    else:
        (root / ".git").mkdir(exist_ok=True)
        (root / ".git" / "config").write_text(config, encoding="utf-8")
    if delivery is not None:
        (root / ".ndf").mkdir(exist_ok=True)
        (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "delivery": delivery}), encoding="utf-8")
    return root


AUTO = [{"target": "リリース対象", "kind": "auto", "channel": "production"}]
MANUAL = [{"target": "リリース対象", "kind": "manual", "channel": "production"}]


def deny_reason(out: Any) -> str | None:
    """PreToolUse の拒否の理由（拒否でなければ None）。"""
    spec = (out or {}).get("hookSpecificOutput") or {}
    return spec.get("permissionDecisionReason") if spec.get("permissionDecision") == "deny" else None


def advise_text(out: Any) -> str | None:
    spec = (out or {}).get("hookSpecificOutput") or {}
    return spec.get("additionalContext")
