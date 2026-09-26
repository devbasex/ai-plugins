"""drive.py の再開: sweep / done まで進んだ駆動は `state.py init` を打ち直さない（#1142 の I7・不足 d）。

実機の `state.py init` は、`final` が決まった状態を再開の対象にせず、空の状態で上書きする。
偽物の `call` がその振る舞いを模し、打ち直した駆動が実際のラウンド数と指摘数を返すことを確かめる。
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


def _load():
    spec = importlib.util.spec_from_file_location("cross_review_drive_resume", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load()


class FakeReview:
    """`state.py` の応答を模す。`init` は `final` が決まった状態を空で上書きする（実機と同じ）。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.judges = [2, 0]
        self.calls: list[tuple] = []
        self.state = self.empty()
        self.save()

    def empty(self) -> dict:
        return {"repo": "o/r", "current_pr": 5, "worktree_path": str(self.tmp / "wt"), "rounds": [],
                "pr_history": [{"pr": 5}]}

    def save(self):
        (self.tmp / "cross-review-pr5-state.json").write_text(json.dumps(self.state))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        args = cmd[2:]
        self.calls.append((name, *args))
        if name != "state.py":
            return 0, ""
        sub = args[0]
        if sub == "init":
            if self.state.get("final") is not None:
                self.state = self.empty()
                self.save()
            return 0, f"PR=5\nTMP_DIR={self.tmp}\nWORKTREE={self.tmp / 'wt'}\nREPO=o/r\n"
        if sub == "start-round":
            if not self.judges:
                return 1, ""
            n = len(self.state["rounds"]) + 1
            self.state["rounds"].append({"round": n, "reviewers": ["codex"], "codex": {"comments": 3}})
            self.save()
            return 0, f"ROUND={n}\nREVIEWERS=codex\n"
        if sub == "judge":
            rc = self.judges.pop(0)
            if rc == 0:
                self.state["final"] = "approved"
                self.save()
            return rc, ""
        if sub == "check-oscillation":
            return 2, ""
        if sub == "should-rotate":
            return 2, ""
        if sub == "verify-sweep":
            self.state["sweep"] = {"remaining_open": 0, "verified": True, "commit": None}
            self.save()
            return 0, ""
        return 0, ""

    def inits(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ("state.py", "init"))


def run_main(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cr.main(argv)
    return e.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def drive_to_sweep(fake: FakeReview, argv, capsys) -> dict:
    """fix と sweep の pause まで進め、sweep の結果ファイルを書いた状態で返す。"""
    code, out = run_main(argv, capsys)
    assert code == 20
    Path(out["items"][0]["result_file"]).write_text("{}")
    code, out = run_main(argv, capsys)
    assert code == 21 and out["items"][0]["pause"] == "sweep"
    Path(out["items"][0]["result_file"]).write_text("{}")
    return out


def test_rerun_from_sweep_keeps_rounds_and_findings(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    drive_to_sweep(fake, ["5"], capsys)
    inits = fake.inits()

    code, out = run_main(["5"], capsys)
    assert code == 0 and out["status"] == "ok"
    m = out["metrics"]
    assert (m["rounds"], m["findings"], m["final"], m["review_status"]) == (2, 6, "approved", "approved")
    assert fake.inits() == inits  # sweep から打ち直すと init を打たない

    code, out = run_main(["5"], capsys)  # done からの打ち直しも同じ件数を返す
    assert code == 0 and out["metrics"]["rounds"] == 2 and out["metrics"]["findings"] == 6
    assert fake.inits() == inits


def test_drive_state_keeps_init_vars(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(cr, "call", FakeReview(tmp_path))
    run_main(["5"], capsys)
    ds = json.loads((tmp_path / "drive-pr5.json").read_text())
    assert ds["init_vars"]["TMP_DIR"] == str(tmp_path) and ds["init_vars"]["REPO"] == "o/r"


def test_rerun_finds_drive_state_under_given_worktree(tmp_path, monkeypatch, capsys):
    """`--worktree` を渡した駆動は、`<worktree>/.cross_review/` の駆動の状態を見る。"""
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)
    wt = tmp_path / "wt"
    tmp = wt / ".cross_review"
    tmp.mkdir(parents=True)
    fake = FakeReview(tmp)
    monkeypatch.setattr(cr, "call", fake)
    argv = ["5", "--worktree", str(wt)]
    drive_to_sweep(fake, argv, capsys)
    inits = fake.inits()
    code, out = run_main(argv, capsys)
    assert code == 0 and out["metrics"]["rounds"] == 2 and fake.inits() == inits


def test_rerun_finds_drive_state_under_default_worktree(tmp_path, monkeypatch, capsys):
    """引数も環境変数も無ければ、`init` と同じ既定の worktree の下を見る。"""
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(tmp_path / "base"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
    monkeypatch.chdir(repo)
    tmp = tmp_path / "base" / "o--r" / "pr5" / ".cross_review"
    tmp.mkdir(parents=True)
    fake = FakeReview(tmp)
    monkeypatch.setattr(cr, "call", fake)
    drive_to_sweep(fake, ["5"], capsys)
    inits = fake.inits()
    code, out = run_main(["5"], capsys)
    assert code == 0 and out["metrics"]["rounds"] == 2 and fake.inits() == inits


def test_drive_state_before_sweep_still_runs_init(tmp_path, monkeypatch, capsys):
    """sweep より前の段階では、これまでどおり init を打って再開する。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    run_main(["5"], capsys)
    run_main(["5"], capsys)
    assert fake.inits() == 2


def test_drive_state_of_other_dir_is_not_used(tmp_path, monkeypatch, capsys):
    """駆動の状態の `init_vars` が別の置き場を指すなら使わず、init を打つ。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    (tmp_path / "drive-pr5.json").write_text(json.dumps(
        {"stage": "done", "init_vars": {"TMP_DIR": str(tmp_path / "other")}}))
    run_main(["5"], capsys)
    assert fake.inits() == 1
