"""drive.py の再開: 打ち直した駆動は耐久の記録から続け、`refactor.py init` を打ち直さない（#1142 の I19・不足 d）。

実機の `refactor.py init` は、`phase` が `done` の状態を再開の対象にせず、新しい状態で作り直す。
偽物の `call` がその振る舞いを模し、打ち直した駆動が実際の件数を返すことを確かめる。
"""

from __future__ import annotations

import importlib.util
import json
import shlex
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


@pytest.fixture(autouse=True)
def durable_in_process(monkeypatch):
    """止まりで抜けるところを SystemExit に替え、同じプロセスの打ち直しが開いたままの耐久の記録を続ける。"""

    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(rf.durable, "exit_leaving_pending", leave)
    yield
    rf.durable.close()


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
            # 実機と同じく、通った最終ゲートは状態の `final_gate.status` を `passed` にする（採用を数える条件）
            self.state["final_gate"] = {"status": "passed" if self.gate in ("passed", "cross-review") else self.gate}
            self.save()
            return (1 if self.gate == "failed" else 0), f"FINAL_GATE={self.gate}\n"
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


def test_a_run_whose_final_gate_did_not_pass_stops_without_adopting(tmp_path, monkeypatch, capsys):
    """AC-1399-6: 最終ゲートが `passed` でないまま終わった実行は完了にせず、採用を 0 と数える。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(rf, "call", FakeRefactor(tmp_path, gate="failed"))
    code, out = run_main(ARGV, capsys)
    assert (code, out["status"]) == (1, "stopped")
    assert out["metrics"]["exit"] == 1
    assert (out["metrics"]["adopted"], out["metrics"]["unconfirmed"]) == (0, 1)


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


def test_rerun_without_the_result_pauses_again_with_a_larger_number(tmp_path, monkeypatch, capsys):
    """結果ファイルを書かずに打ち直すと同じ止まりを番号を増やして返し、書いてから打ち直すと finalize へ進む。

    進みは耐久の記録だけが持ち、drive の状態ファイル（`drive-rf<ID>.json`）は書かない（#1142 の決定 32・I19）。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeRefactor(tmp_path, gate="cross-review")
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code == 23
    (wid,) = rf.durable.workflow_ids("refactor-")
    assert rf.durable.event(wid)["seq"] == 1
    code, out = run_main(ARGV, capsys)
    assert code == 23 and out["items"][0]["pause"] == "cross-review"
    assert rf.durable.event(wid)["seq"] == 2
    assert not (tmp_path / "drive-rf7.json").exists()
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["review_status"] == "approved"
    assert fake.inits() == 1 and not (tmp_path / "drive-rf7.json").exists()


def _repo_with_subdir(tmp_path: Path) -> tuple[Path, Path]:
    """ai-plugins の外の git リポジトリと、その下位のディレクトリ（Skill のディレクトリにあたる）。"""
    repo = tmp_path / "sample"
    sub = repo / "skills" / "cross-refactoring"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo.resolve(), sub


def test_the_final_gate_names_the_target_repository_and_the_answer_file(tmp_path, monkeypatch, capsys):
    """AC5・AC12（#1655）: 最終ゲートの止まりは cross-review の駆動を打つ場所（対象のリポジトリの根）と回答ファイルの引数を示す。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    repo, sub = _repo_with_subdir(tmp_path)
    monkeypatch.chdir(sub)
    monkeypatch.setattr(rf, "call", FakeRefactor(tmp_path, gate="cross-review"))
    code, out = run_main(ARGV, capsys)
    item = out["items"][0]
    assert code == 23 and item["cwd"] == str(repo)
    words = shlex.split(item["command"])
    assert words[words.index("--result-file") + 1] == item["result_file"]
    prompt = Path(item["prompt_file"]).read_text()
    assert str(repo) in prompt and item["command"] in prompt


def test_an_unverified_answer_is_finalized_as_unverified(tmp_path, monkeypatch, capsys):
    """AC8（#1656）: 回答が unverified なら、finalize へそのまま渡し、結果の review_status も unverified。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeRefactor(tmp_path, gate="cross-review")
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code == 23
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "unverified"}')
    code, out = run_main(ARGV, capsys)
    assert code == 0 and out["metrics"]["review_status"] == "unverified"
    assert ("refactor.py", "finalize", "7", "--review-status", "unverified") in fake.calls


def test_outside_a_repository_the_drive_stops_before_anything_runs(tmp_path, monkeypatch, capsys):
    """AC4（#1655）: git の作業ツリーでない場所で打つと、子を打たずに中断し（metrics.exit 2）、耐久の記録を開かない。"""
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    monkeypatch.chdir(nogit)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeRefactor(tmp_path)
    monkeypatch.setattr(rf, "call", fake)
    rf.durable.close()
    code, out = run_main(ARGV, capsys)
    assert (code, out["status"], out["metrics"]["exit"]) == (1, "stopped", 2)
    assert "対象のリポジトリを決められない" in out["summary"]
    assert fake.calls == [] and rf.durable.launched() is None


# 別のプロセスで drive.py の main を打つ。偽物の refactor.py は状態ファイルを読み直し、呼び出しをファイルへ残す
RUNNER = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("resume_test", {test!r})
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
tmp, log = Path(sys.argv[1]), Path(sys.argv[2])
fake = t.FakeRefactor.__new__(t.FakeRefactor)
fake.tmp, fake.gate, fake.calls = tmp, "cross-review", []
fake.state = json.loads((tmp / "cross-refactoring-rf7-state.json").read_text())

def call(cmd, env=None, cwd=None):
    out = fake(cmd, env, cwd)
    with log.open("a") as f:
        f.write(json.dumps(fake.calls[-1]) + "\\n")
    return out

t.rf.call = call
t.rf.main(sys.argv[3:])
"""


def test_a_pause_survives_the_process_and_the_next_process_does_not_init_again(tmp_path, monkeypatch):
    """止まりのまま抜けたプロセスの後、別のプロセスの打ち直しが耐久の記録から続け、init を打たない（I19）。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    FakeRefactor(tmp_path)  # 状態ファイルを置く
    runner = tmp_path / "runner.py"
    runner.write_text(RUNNER.format(test=str(Path(__file__).resolve())))
    log = tmp_path / "calls.jsonl"

    def drive():
        p = subprocess.run([PY, str(runner), str(tmp_path), str(log), *ARGV], capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])

    code, out = drive()
    assert code == 23 and out["items"][0]["pause"] == "cross-review"
    code, _ = drive()
    assert code == 23
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    code, out = drive()
    assert code == 0 and out["metrics"]["review_status"] == "approved"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [c[1] for c in calls if c[0] == "refactor.py"].count("init") == 1
    assert ["refactor.py", "finalize", "7", "--review-status", "approved"] in calls


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
        self.reassigns: list[tuple[int, str]] = []  # `refactor.py reassign` の応答の並び（#919）

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name == "refactor.py" and cmd[2] == "reassign" and self.reassigns:
            self.calls.append((name, *cmd[2:]))
            return self.reassigns.pop(0)
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


@pytest.mark.parametrize("rc", [3, 4, 6])
def test_monitor_failure_in_implement_stops_before_merge(tmp_path, monkeypatch, capsys, rc):
    """結果が無いまま異常に終わった（結果なし・早期の異常・起動失敗）ときは取り込みへ進まない。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "implement", fail={"monitor.py": rc})
    fake.reassigns = [(3, "REASSIGN=abort\n")]  # 振り替え先が無い（#919）
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code != 0 and "monitor.py" in out["summary"]
    assert any(c[:3] == ("refactor.py", "reassign", "7") for c in fake.calls)
    assert not any(c[:2] == ("refactor.py", "merge-implement") for c in fake.calls)


def test_a_reassigned_implementer_runs_the_same_phase_and_merges(tmp_path, monkeypatch, capsys):
    """#919 の AC11・AC16: 実装担当が結果を残さなければ、`reassign` が返した担当で同じ工程を起動し、取り込みへ進む。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "implement")
    fake.reassigns = [(7, "IMPL=claude\nREASSIGNED='codex=claude:usage_limit'\n")]
    real = fake.__call__
    monitors = [6, 0]

    def call(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "monitor.py" and monitors:
            fake.calls.append(("monitor.py", *cmd[2:]))
            return monitors.pop(0), ""
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    code, out = run_main(ARGV, capsys)
    assert code == 0, out
    assert [c[1:3] for c in fake.calls if c[0] == "launch-cli.sh"] == [("codex", "implement"), ("claude", "implement")]
    # 起動のたびに start-phase を打ち、工程の終わりの時刻から残りの上限を出し直す（締め切りは延ばさない）
    assert [c[:4] for c in fake.calls if c[:2] == ("refactor.py", "start-phase")] == [("refactor.py", "start-phase", "7", "implement")] * 2
    assert sum(1 for c in fake.calls if c[:2] == ("refactor.py", "merge-implement")) == 1


@pytest.mark.parametrize("rc", [2, 5])
@pytest.mark.parametrize("phase", ["plan", "implement"])
def test_monitor_stop_at_limit_goes_on_to_merge(tmp_path, monkeypatch, capsys, phase, rc):
    """監視が上限で止めた（timeout / stalled）ときは止めずに `merge-*` へ渡す（決定 23）。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, phase, fail={"monitor.py": rc})
    monkeypatch.setattr(rf, "call", fake)
    code, _ = run_main(ARGV, capsys)
    assert code == 0 and any(c[:2] == ("refactor.py", f"merge-{phase}") for c in fake.calls)


def _final_fix_resume(tmp_path, monkeypatch, gate: dict, merge_rc: int):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "final-fix")
    fake.state["final_gate"] = gate
    fake.save()
    real = fake.__call__

    def call(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py" and cmd[2] == "merge-final-fix":
            fake.calls.append(("refactor.py", *cmd[2:]))
            return merge_rc, ""
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    return fake


@pytest.mark.parametrize("merge_rc", [0, 2])
def test_rerun_from_final_fix_merges_before_final_gate(tmp_path, monkeypatch, capsys, merge_rc):
    """最終ゲートの修正の途中で止まった駆動は、`final-gate` の前に `merge-final-fix` を通す（#674）。

    結果なしで閉じ済みの試行（`merge-final-fix` が 2）でも最終ゲートへ進む。
    """
    fake = _final_fix_resume(tmp_path, monkeypatch, {"status": "failing", "impl": "codex", "fix_base_sha": "abc"}, merge_rc)
    code, _ = run_main(ARGV, capsys)
    subs = [c[1] for c in fake.calls if c[0] == "refactor.py" and c[1] in ("merge-final-fix", "final-gate")]
    assert code == 0 and subs[:2] == ["merge-final-fix", "final-gate"]
    assert fake.launched() == []


def test_final_gate_recheck_reruns_the_gate_without_a_fix_cli(tmp_path, monkeypatch, capsys):
    """寄せた危険フラグの項目の取り消し（`FINAL_GATE=recheck`）は修正の依頼ではない。

    修正の CLI も `merge-final-fix` も通さずに `final-gate` を打ち直す。
    """
    fake = _final_fix_resume(tmp_path, monkeypatch, {"status": "passed"}, 0)
    real = fake.__call__
    gates: list = []

    def call(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py" and cmd[2] == "final-gate":
            gates.append(1)
            fake.calls.append(("refactor.py", *cmd[2:]))
            return (2, "FINAL_GATE=recheck\n") if len(gates) == 1 else (0, "FINAL_GATE=passed\n")
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    code, _ = run_main(ARGV, capsys)
    assert code == 0 and len(gates) == 2
    assert "final-fix" not in fake.launched()
    assert not any(c[:2] == ("refactor.py", "merge-final-fix") for c in fake.calls)


def test_final_gate_without_open_fix_does_not_merge(tmp_path, monkeypatch, capsys):
    """開いた修正の試行が無ければ（最終ゲートが通った後など）`merge-final-fix` を打たない。"""
    fake = _final_fix_resume(tmp_path, monkeypatch, {"status": "passed", "impl": "codex", "fix_base_sha": "abc"}, 0)
    code, _ = run_main(ARGV, capsys)
    assert code == 0 and not any(c[:2] == ("refactor.py", "merge-final-fix") for c in fake.calls)


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
            if phase == "final-fix" and len(gates) == 1:
                return 2, ""
            fake.state["final_gate"] = {"status": "passed"}
            fake.save()
            return 0, "FINAL_GATE=passed\n"
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    code, _ = run_main(ARGV, capsys)
    merge = "merge-fix" if phase == "fix" else "merge-final-fix"
    assert code == 0 and any(c[:2] == ("refactor.py", merge) for c in fake.calls)


def test_propose_stops_when_no_participant_finished(tmp_path, monkeypatch, capsys):
    """全員が欠けた提案を候補 0 件の完了として扱わない。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "propose", fail={"monitor.py": 2})
    fake.reassigns = [(3, "REASSIGN=abort\n")]  # 振り替え先が無い（#919）
    monkeypatch.setattr(rf, "call", fake)
    code, out = run_main(ARGV, capsys)
    assert code != 0 and "提案" in out["summary"]
    assert any(c[:4] == ("refactor.py", "reassign", "7", "propose") for c in fake.calls)
    assert not any(c[:2] == ("refactor.py", "merge-proposals") for c in fake.calls)


def test_propose_gathers_again_from_the_reassigned_proposer(tmp_path, monkeypatch, capsys):
    """#919 の AC14: 提案担当の全員が結果なしなら、`reassign` が返した担当で提案を集め直す。"""
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    fake = FakeFrom(tmp_path, "propose")
    fake.reassigns = [(7, "PROPOSERS=kiro\nREASSIGNED='codex=kiro:usage_limit'\n")]
    real = fake.__call__
    monitors = [6]

    def call(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "monitor.py" and monitors:
            fake.calls.append(("monitor.py", *cmd[2:]))
            return monitors.pop(0), ""
        return real(cmd, env, cwd)

    monkeypatch.setattr(rf, "call", call)
    code, _ = run_main(ARGV, capsys)
    assert code == 0
    assert [c[1] for c in fake.calls if c[0] == "launch-cli.sh" and c[2] == "propose"] == ["codex", "kiro"]
    assert any(c[:2] == ("refactor.py", "merge-proposals") for c in fake.calls)


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


@pytest.mark.parametrize(
    ("seat", "phase", "account"),
    [("claude", "propose", "work2"), ("claude", "implement", "work1"), ("codex", "propose", "")],
    ids=["propose-reads-proposers", "implement-reads-implementer", "other-seat"],
)
def test_launch_reads_the_account_of_the_phase(monkeypatch, seat, phase, account):
    """提案は席が実装担当と同じでも提案担当の記録からアカウントを引く（実装担当の記録は提案の振り替えで変わらない）。"""
    d = rf.Drive(1, [])
    d.v = {"ID": "7"}
    state = {"implementer": "claude", "implementer_account": "work1", "proposer_accounts": {"claude": "work2"}}
    monkeypatch.setattr(rf.Drive, "state", lambda self: state)
    seen = []
    monkeypatch.setattr(rf, "_call_step", lambda cmd, env, s="", a="": seen.append((s, a)) or (0, ""))
    assert d.launch(seat, phase) == 0
    assert seen == [(seat, account)]
