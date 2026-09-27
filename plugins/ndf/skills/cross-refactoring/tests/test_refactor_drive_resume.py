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
            return 0, (
                f"ID=7\nTMP_DIR={self.tmp}\nPHASE={self.state['phase']}\nIMPL=codex\nRUNTIMES=codex\nRUNTIMES_CSV=codex\nWORK={self.tmp}\n"
            )
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


class FakeFrom(FakeRefactor):
    """`init` が途中の手順（`phase`）を返し、子のスクリプトの終了コードを `fail` で決める。"""

    def __init__(self, tmp: Path, phase: str, fail: dict | None = None, monitor_out: str = ""):
        super().__init__(tmp)
        self.state["phase"] = phase
        self.save()
        self.fail, self.monitor_out = fail or {}, monitor_out

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name in self.fail:
            self.calls.append((name, *cmd[2:]))
            return self.fail[name], ""
        if name == "monitor.py":
            self.calls.append((name, *cmd[2:]))
            return 0, self.monitor_out
        if name == "refactor.py" and cmd[2] == "verify":
            self.calls.append((name, *cmd[2:]))
            return 0, "VERIFY=done\n"
        if name == "refactor.py" and cmd[2] in ("merge-plan", "merge-implement"):
            self.calls.append((name, *cmd[2:]))
            return 0, ""
        return super().__call__(cmd, env, cwd)

    def launched(self) -> list[str]:
        return [c[2] for c in self.calls if c[0] == "launch-cli.sh"]


def test_rerun_from_fix_does_not_relaunch_earlier_phases(tmp_path, monkeypatch, capsys):
    """修正の手順で止まった実行は検証から再開し、提案・改修計画・実装の CLI を起動し直さない。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "fix")
    monkeypatch.setattr(rf, "call", fake)
    code, _ = run_main(ARGV, capsys)
    assert code == 0 and fake.launched() == []
    assert not any(c[:2] == ("refactor.py", "measure") for c in fake.calls)
    assert any(c[:2] == ("refactor.py", "verify") for c in fake.calls)


def test_prepare_worktrees_failure_stops_before_clis(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "propose", fail={"prepare-worktrees.sh": 1})
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code != 0 and "prepare-worktrees.sh" in out["summary"]
    assert fake.launched() == []


def test_monitor_failure_in_implement_stops_before_merge(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "implement", fail={"monitor.py": 2})
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code != 0 and "monitor.py" in out["summary"]
    assert not any(c[:2] == ("refactor.py", "merge-implement") for c in fake.calls)


@pytest.mark.parametrize("phase", ["fix", "final-fix"])
def test_monitor_failure_in_fix_goes_on_to_merge(tmp_path, monkeypatch, capsys, phase):
    """修正の工程は監視の非ゼロ終了で止めず、結果なしの取り込み（`merge-fix` / `merge-final-fix`）へ渡す。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "fix" if phase == "fix" else "final", fail={"monitor.py": 2})
    verified, gates = [], []
    real = fake.__call__

    def call(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py" and cmd[2] == "verify":
            verified.append(1)
            fake.calls.append(("refactor.py", *cmd[2:]))
            return 0, "VERIFY=fix\n" if phase == "fix" and len(verified) == 1 else "VERIFY=done\n"
        if Path(cmd[1]).name == "refactor.py" and cmd[2] == "final-gate":
            gates.append(1)
            fake.calls.append(("refactor.py", *cmd[2:]))
            return (2, "") if phase == "final-fix" and len(gates) == 1 else (0, "FINAL_GATE=passed\n")
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    code, _ = run_main(ARGV, capsys)
    merge = "merge-fix" if phase == "fix" else "merge-final-fix"
    assert code == 0 and any(c[:2] == ("refactor.py", merge) for c in fake.calls)


def test_propose_stops_when_no_participant_finished(tmp_path, monkeypatch, capsys):
    """全員が欠けた提案を候補 0 件の完了として扱わない。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "propose", fail={"monitor.py": 2})
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code != 0 and "提案" in out["summary"]
    assert not any(c[:2] == ("refactor.py", "merge-proposals") for c in fake.calls)


def test_propose_continues_when_one_participant_finished(tmp_path, monkeypatch, capsys):
    """1 者が欠けても、提案を終えた参加者がいれば続ける（`merge-proposals` が欠けた者を除く）。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    rows = '{"agent": "codex", "exit_code": 0}\n{"agent": "kiro", "exit_code": 2}\n'
    fake = FakeFrom(tmp_path, "propose", monitor_out=rows)
    real = fake.__call__

    def call(cmd, env=None, cwd=None):
        rc, out = real(cmd, env, cwd)
        return (2, out) if Path(cmd[1]).name == "monitor.py" else (rc, out)

    monkeypatch.setattr(rf, "call", call)
    code, _ = run_main(ARGV, capsys)
    assert code == 0 and any(c[:2] == ("refactor.py", "merge-proposals") for c in fake.calls)
