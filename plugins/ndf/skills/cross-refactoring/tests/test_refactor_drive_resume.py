"""drive.py の再開: done まで進んだ駆動は `refactor.py init` を打ち直さない（#1142 の I7・不足 d）。

実機の `refactor.py init` は、`phase` が `done` の状態を再開の対象にせず、新しい状態で作り直す。
偽物の `call` がその振る舞いを模し、打ち直した駆動が実際の件数を返すことを確かめる。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PY = sys.executable
ITEMS = [{"status": "verified", "fix_count": 2}, {"status": "reverted"}]


def _load():
    spec = importlib.util.spec_from_file_location("cross_refactoring_drive_resume", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rf = _load()


class FakeRefactor:
    """`refactor.py` の応答を模す。`init` は `phase` が `done` の状態を作り直す（実機と同じ）。"""

    def __init__(self, tmp: Path, gate: str = "passed"):
        self.tmp, self.gate = tmp, gate
        self.calls: list[tuple] = []
        self.state = {"items": list(ITEMS), "phase": "final"}
        self.save()

    def save(self):
        (self.tmp / "cross-refactoring-rf7-state.json").write_text(json.dumps(self.state))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        args = cmd[2:]
        self.calls.append((name, *args))
        if name != "refactor.py":
            return 0, "abc\n"
        sub = args[0]
        if sub == "init":
            if self.state.get("phase") == "done":
                self.state = {"items": [], "phase": "propose"}
                self.save()
            return 0, (f"ID=7\nTMP_DIR={self.tmp}\nPHASE={self.state['phase']}\nIMPL=codex\n"
                       f"RUNTIMES=codex\nRUNTIMES_CSV=codex\nWORK={self.tmp}\n")
        if sub == "merge-proposals":
            return 2, ""
        if sub == "final-gate":
            return 0, f"FINAL_GATE={self.gate}\n"
        if sub == "finalize":
            self.state["phase"] = "done"
            self.save()
        return 0, ""

    def inits(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ("refactor.py", "init"))


def run_main(argv, capsys):
    with pytest.raises(SystemExit) as e:
        rf.main(argv)
    return e.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


ARGV = ["7", "--scope", "src", "--baseline-test", "pytest"]


def test_rerun_from_done_keeps_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeRefactor(tmp_path)
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["items"] == 2
    code, out = run_main(ARGV, capsys)
    m = out["metrics"]
    assert code == 0 and (m["items"], m["adopted"], m["reverted"], m["fix_rounds"]) == (2, 1, 1, 2)
    assert fake.inits() == 1  # done から打ち直すと init を打たない


def test_rerun_after_cross_review_keeps_review_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeRefactor(tmp_path, gate="cross-review")
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code == 23
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["review_status"] == "approved"
    inits = fake.inits()
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["items"] == 2 and out["metrics"]["review_status"] == "approved"
    assert fake.inits() == inits


def test_drive_state_keeps_init_vars(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(rf, "call", FakeRefactor(tmp_path))
    run_main(ARGV, capsys)
    ds = json.loads((tmp_path / "drive-rf7.json").read_text())
    assert ds["init_vars"]["ID"] == "7" and ds["init_vars"]["TMP_DIR"] == str(tmp_path)


def test_rerun_finds_drive_state_under_given_root(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    tmp = tmp_path / "root" / "work" / ".cross_refactoring"
    tmp.mkdir(parents=True)
    fake = FakeRefactor(tmp)
    monkeypatch.setattr(rf, "call", fake)
    argv = [*ARGV, "--worktree-root", str(tmp_path / "root")]
    run_main(argv, capsys)
    code, out = run_main(argv, capsys)
    assert code == 0 and out["metrics"]["items"] == 2 and fake.inits() == 1


def test_rerun_finds_drive_state_under_default_root(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(tmp_path / "base"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:o/r.git"], check=True)
    monkeypatch.chdir(repo)
    tmp = tmp_path / "base" / "o--r" / "rf7" / "work" / ".cross_refactoring"
    tmp.mkdir(parents=True)
    fake = FakeRefactor(tmp)
    monkeypatch.setattr(rf, "call", fake)
    run_main(ARGV, capsys)
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["items"] == 2 and fake.inits() == 1
