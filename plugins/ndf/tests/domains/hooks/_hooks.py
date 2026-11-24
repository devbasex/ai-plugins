"""hook のテストの共通の部品。見本の入力・エントリポイントの呼び出し・マージ先のリポジトリの作り方。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from ndf import identity
from ndf.domains.hooks import context, main, tables
from ndf.domains.relay import signals

SAMPLES = Path(__file__).resolve().parent / "samples"
SRC = tables.PLUGIN_ROOT / "src"
# 中継・オーガナイザー・通知・止め方・文脈の運用上限の変数と、stop-wait が親の Claude Code を探す変数（テストを流す人の値を
# 読まない。親の起動の変数が無ければ、stop-wait は実の `/proc` を読まず、判定の子も起動しない）
ENV_NAMES = (
    "NDF_RELAY_DIR",
    "NDF_RELAY_LAUNCH",
    "NDF_RELAY_SESSION",
    "NDF_ORGANIZER_DIR",
    "NDF_ORGANIZER_LANE",
    "NDF_FACTORY_RUN",
    "NDF_HOOK_OFF",
    "NDF_CONTEXT_LIMIT",
    "NDF_WORKER_KIND",
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNEL_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_CONFIG_DIR",
)


def sample_paths() -> list[Path]:
    return sorted(SAMPLES.glob("*/*/*.json"))


def _fill(value: Any, locations: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _fill(v, locations) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, locations) for v in value]
    return locations.get(value, value) if isinstance(value, str) else value


def read_sample(path: Path, cwd: Path, **override: Any) -> dict[str, Any]:
    """見本の入力。作業ディレクトリと会話の記録のパスを置き換え、`override` のキーを上書きする。"""
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
    """エントリポイントを同じプロセスで呼び、出力の JSON（無ければ None）を返す。終了コードはいつも 0。"""
    out = io.StringIO()
    text = payload if isinstance(payload, str) else json.dumps(payload)
    assert main.run_entry(["entry.py", runtime, event], io.StringIO(text), out) == 0
    got = out.getvalue()
    return json.loads(got) if got.strip() else None


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """中継の作業ディレクトリ。今の起動は L1 で、hook の環境も L1。"""
    directory = tmp_path / "relay"
    directory.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(directory))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    signals.set_launch(directory, "L1")
    return directory


@pytest.fixture
def counted_tail(monkeypatch):
    """会話の記録の末尾を読んだ回数。"""
    got = {"n": 0}
    real = context.read_tail

    def wrapped(*args, **kw):
        got["n"] += 1
        return real(*args, **kw)

    monkeypatch.setattr(context, "read_tail", wrapped)
    return got


def ndf_repo(root: Path) -> None:
    """`root` を NDF の引継ぎの規約を使うリポジトリ（メインディレクトリに .ndf）にする。"""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".ndf").mkdir(exist_ok=True)


def usage_transcript(root: Path, tokens: int) -> None:
    """`root` を NDF のリポジトリにし、最後の応答の文脈の量が `tokens` の会話の記録を置く（#3995 の 3995:15）。"""
    ndf_repo(root)
    line = {"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}
    (root / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def state_dir() -> Path:
    return identity.load().state_dir


def records() -> list[dict[str, Any]]:
    """計測の記録のすべての行。"""
    lines = []
    for path in sorted((state_dir() / "hooks").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines


# ブランチ戦略の設定のテンプレート（代表の 5 種の fixture の web-develop。届け先の行だけを差し替える）
_STRATEGY = Path(__file__).resolve().parents[2] / "fixtures" / "branch_strategy" / "web-develop.json"


def make_repo(root: Path, slug: str = "owner/repo", deliveries: Any = None, worktree: bool = False) -> Path:
    """マージ先のリポジトリ（.git の設定に origin と、.ndf/project.json の `branch_strategy.release.deliveries`）を作る。
    `deliveries` が None なら設定を置かない。"""
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
    if deliveries is not None:
        section = json.loads(_STRATEGY.read_text(encoding="utf-8"))
        section["release"]["deliveries"] = deliveries
        (root / ".ndf").mkdir(exist_ok=True)
        (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "branch_strategy": section}), encoding="utf-8")
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
