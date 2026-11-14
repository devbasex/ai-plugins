"""drive のテストの補助: origin（bare）と主の clone、担当の代わり（`FakeAgents`）、GitHub と CI の代わり。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import agents, ci, github
from ndf.domains.crossrefactor import drive, final_gate, launch_decide

BRANCH = "feat/x"
PR = 7
SOURCE = "def area(w, h):\n    return w * h\n\n\ndef untouched():\n    return 0\n"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit_in(cwd: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (cwd / rel).parent.mkdir(parents=True, exist_ok=True)
        (cwd / rel).write_text(text, encoding="utf-8")
    sh(cwd, "add", "-A")
    sh(cwd, "commit", "-q", "-m", message)
    return sh(cwd, "rev-parse", "HEAD")


@dataclass
class World:
    origin: Path
    main: Path
    base: str

    def config(self, **over: Any) -> drive.RunConfig:
        kw: dict[str, Any] = {"scope": ("src",), "required": ("pytest",), "tests": ("pytest",)}
        kw.update(over)
        return drive.RunConfig(self.main, PR, BRANCH, self.base, **kw)

    def root(self) -> Path:
        return self.main.resolve() / ".worktrees" / ".ndf" / f"rf{PR}"


def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    main = tmp_path / "main"
    sh(tmp_path, "clone", "-q", str(origin), str(main))
    commit_in(main, {"src/geo.py": "def area(w, h):\n    return w*h\n\n\ndef untouched():\n    return 0\n", "docs/a.md": "# a\n"}, "初め")
    base = sh(main, "rev-parse", "HEAD")
    sh(main, "push", "-q", "origin", "HEAD:refs/heads/main")
    commit_in(main, {"src/geo.py": SOURCE}, "PR の変更")
    sh(main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(main, "fetch", "-q", "origin")
    return World(origin, main, base)


Act = Callable[[agents.Launch, Path], Optional[dict[str, Any]]]


def _done(spec: agents.Launch) -> agents.AgentStatus:
    return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())


@dataclass
class FakeAgents:
    """担当の代わり。工程ごとの振る舞い（`acts`）を呼び、読み取りの工程は標準出力へ、コードを書く工程は結果ファイルへ書く。"""

    acts: dict[str, Act]
    seen: list[tuple[str, str, Path, Path]] = field(default_factory=list)

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        stem = spec.paths.out.name.removesuffix(".out")
        phase = next(p for p in sorted(self.acts, key=len, reverse=True) if stem.startswith(f"{spec.agent}-{p}-"))
        submit = spec.cwd / launch_decide.RESULT_DIR / f"{stem}-result.json"
        self.seen.append((spec.agent, phase, spec.cwd, submit))
        got = self.acts[phase](spec, submit)
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        if phase in launch_decide.read_only_phases():
            spec.paths.out.write_text("ログ\n" + (json.dumps(got, ensure_ascii=False) if got is not None else "") + "\n", encoding="utf-8")
        else:
            spec.paths.out.write_text("ok\n", encoding="utf-8")
            if got is not None:
                submit.parent.mkdir(parents=True, exist_ok=True)
                submit.write_text(json.dumps(got), encoding="utf-8")
        return _done(spec)


def propose_area(spec: agents.Launch, submit: Path) -> dict[str, Any]:
    return {"proposals": [
        {"path": "src/geo.py", "symbol": "area", "title": "式に名前を付ける", "reason": "読みやすさ"},
        {"path": "src/geo.py", "symbol": "untouched", "title": "課題が触らない関数", "reason": "範囲外"},
    ]}  # fmt: skip


def no_plan(spec: agents.Launch, submit: Path) -> None:
    return None


def commit_at(files: dict[str, str], message: str, out: Optional[dict[str, Any]] = None) -> Act:
    def act(spec: agents.Launch, submit: Path) -> Optional[dict[str, Any]]:
        commit_in(spec.cwd, files, message)
        return out if out is not None else {"commit": sh(spec.cwd, "rev-parse", "HEAD")}

    return act


class FakeHub:
    """`pull` だけを持つ GitHub の代わり。`states` を順に返す（尽きたら最後のもの）。"""

    def __init__(self, *states: str) -> None:
        self.states = list(states)
        self.reads = 0

    def pull(self, number: int) -> github.Pull:
        state = self.states[min(self.reads, len(self.states) - 1)]
        self.reads += 1
        return github.Pull(number, "open", False, "", BRANCH, "main", "", state, "a", "MEMBER")


class Watched:
    """`ci.WatchResult` の代わり（`cause` と `reading` だけ）。"""

    def __init__(self, kind: Optional[str], failed: tuple[str, ...] = ()) -> None:
        self.kind = kind
        self.reading = ci.Reading([ci.Check(n, ci.State.DONE, "failure", 1) for n in failed], [], [], [], [], [], True)

    def cause(self, tests: Any) -> Optional[str]:
        return self.kind


def fake_ci(monkeypatch: pytest.MonkeyPatch, *results: Watched) -> list[str]:
    """最終ゲートの CI の待ちを差し替え、待った head を返す並びを渡す。"""
    waited: list[str] = []
    queue = list(results)

    def wait(hub: Any, sha: str, cfg: final_gate.GateConfig) -> Watched:
        waited.append(sha)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(final_gate, "wait_ci", wait)
    monkeypatch.setattr(final_gate, "_failed_tests", lambda hub, watched: ["tests/test_geo.py::test_area"])
    return waited


def ports(fake: FakeAgents, hub: FakeHub) -> drive.Ports:
    return drive.Ports(launcher=fake, hub=lambda work: hub, sleep=lambda s: None)  # type: ignore[arg-type,return-value]
