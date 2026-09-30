"""drive.py の再開: 打ち直した駆動は耐久の記録から続き、`state.py init` を打ち直さない（#1142 の I19・不足 d）。

実機の `state.py init` は、`final` が決まった状態を再開の対象にせず、空の状態で上書きする。
偽物の `call` がその振る舞いを模し、打ち直した駆動が実際のラウンド数と指摘数を返すことを確かめる。
駆動は 1 回ずつ fork した子で打つ（`test_drive_review.fork_main` と同じ形。止まりのまま `os._exit` で抜ける）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import pickle
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PY = sys.executable


def _load():
    """同じ名前で 1 度だけ読む（耐久ワークフローの登録を 1 つのモジュールに保つ）。"""
    if "cross_review_drive" in sys.modules:
        return sys.modules["cross_review_drive"]
    spec = importlib.util.spec_from_file_location("cross_review_drive", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cross_review_drive"] = mod
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
        return {"repo": "o/r", "current_pr": 5, "worktree_path": str(self.tmp / "wt"), "rounds": [], "pr_history": [{"pr": 5}]}

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
    """駆動を fork した子で 1 回打つ。差し替えた `call` の中身は子が終わる前に書き出して親へ戻す。"""
    fake = cr.call
    fd, box = tempfile.mkstemp(suffix=".pickle")
    os.close(fd)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # 子
        os.close(r)
        try:
            sys.stdout = os.fdopen(w, "w")

            def leave(code):
                sys.stdout.flush()
                with open(box, "wb") as f:
                    pickle.dump(dict(getattr(fake, "__dict__", {})), f)
                os._exit(code if isinstance(code, int) else 1)

            cr.durable.exit_leaving_pending = leave
            try:
                cr.main(argv)
            except SystemExit as e:
                leave(e.code)
        except BaseException:  # noqa: BLE001  子の失敗は親で終了コード 99 として見る
            traceback.print_exc()
        os._exit(99)
    os.close(w)
    with os.fdopen(r) as f:
        text = f.read()
    _, status = os.waitpid(pid, 0)
    with open(box, "rb") as f:
        saved = f.read()
    os.unlink(box)
    if saved:
        fake.__dict__.update(pickle.loads(saved))
    return os.waitstatus_to_exitcode(status), json.loads(text.strip().splitlines()[-1])


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
    assert (m["rounds"], m["comments"], m["final"], m["review_status"]) == (2, 6, "approved", "approved")
    assert fake.inits() == inits  # sweep から打ち直すと init を打たない

    code, out = run_main(["5"], capsys)  # done からの打ち直しも同じ件数を返す
    assert code == 0 and out["metrics"]["rounds"] == 2 and out["metrics"]["comments"] == 6
    assert fake.inits() == inits


def test_progress_lives_in_the_durable_record(tmp_path, monkeypatch, capsys):
    """進みは耐久の記録（種類 review・鍵の元は状態の置き場）にあり、駆動の状態ファイルを書かない。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(cr, "call", FakeReview(tmp_path))
    run_main(["5"], capsys)
    assert not (tmp_path / "drive-pr5.json").exists()
    assert cr.durable.record_path("review", str(tmp_path.resolve())).is_file()


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


@pytest.mark.parametrize("linked", [False, True])
def test_rerun_finds_drive_state_under_default_worktree(tmp_path, monkeypatch, capsys, linked):
    """引数も環境変数も無ければ、`init` と同じ既定の worktree の下を見る。

    システムの一時ディレクトリがシンボリックリンク（macOS の /tmp など）でも同じ置き場と判定する。
    """
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    sys_tmp = tmp_path / "sys"
    sys_tmp.mkdir()
    if linked:
        (tmp_path / "link").symlink_to(sys_tmp)
        sys_tmp = tmp_path / "link"
    monkeypatch.setattr(tempfile, "tempdir", str(sys_tmp))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
    monkeypatch.chdir(repo)
    tmp = sys_tmp / "ndf-worktrees" / "o--r" / "pr5" / ".cross_review"
    tmp.mkdir(parents=True)
    fake = FakeReview(tmp)
    monkeypatch.setattr(cr, "call", fake)
    drive_to_sweep(fake, ["5"], capsys)
    inits = fake.inits()
    code, out = run_main(["5"], capsys)
    assert code == 0 and out["metrics"]["rounds"] == 2 and fake.inits() == inits


def test_rerun_before_sweep_does_not_run_init_again(tmp_path, monkeypatch, capsys):
    """sweep より前の止まりから打ち直しても、1 回の中で init は 1 回だけ流れる（I19）。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    run_main(["5"], capsys)
    run_main(["5"], capsys)
    assert fake.inits() == 1


def test_rerun_without_the_result_pauses_again_with_a_new_number(tmp_path, monkeypatch, capsys):
    """結果ファイルを書かずに打ち直すと、同じ種類の止まりを番号を増やして返し、ラウンドを流し直さない。"""
    import dbos

    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    first = run_main(["5"], capsys)
    again = run_main(["5"], capsys)
    assert first[0] == again[0] == 20 and first[1]["items"][0]["pause"] == again[1]["items"][0]["pause"] == "fix"
    assert sum(1 for c in fake.calls if c[:2] == ("state.py", "start-round")) == 1
    client = dbos.DBOSClient(system_database_url=f"sqlite:///{cr.durable.record_path('review', str(tmp_path.resolve()))}")
    try:
        (wf,) = client.list_workflows(load_input=False, load_output=False)
        assert client.get_event(wf.workflow_id, "pause", timeout_seconds=0)["seq"] == 2
    finally:
        client.destroy()


def test_leftover_drive_state_file_is_not_read(tmp_path, monkeypatch, capsys):
    """移行の前の駆動の状態ファイル（`drive-pr<N>.json`）は読まない。耐久の記録が無ければ init から流す。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeReview(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    (tmp_path / "drive-pr5.json").write_text(json.dumps({"stage": "done", "init_vars": {"TMP_DIR": str(tmp_path)}}))
    code, _ = run_main(["5"], capsys)
    assert code == 20 and fake.inits() == 1


class FakeRotateFails(FakeReview):
    """巻き直しが要ると答え、`rotate-pr.sh prepare` が失敗する。"""

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name == "rotate-pr.sh":
            self.calls.append((name, *cmd[2:]))
            return 1, ""
        if name == "state.py" and cmd[2] == "should-rotate":
            self.calls.append((name, *cmd[2:]))
            return 0, ""
        return super().__call__(cmd, env, cwd)


def test_rerun_after_rotate_failure_does_not_merge_fix_again(tmp_path, monkeypatch, capsys):
    """merge-fix の後の巻き直しで止まっても、打ち直しで merge-fix を 2 度走らせない。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeRotateFails(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    code, out = run_main(["5"], capsys)
    assert code == 20
    Path(out["items"][0]["result_file"]).write_text("{}")
    code, _ = run_main(["5"], capsys)
    assert code != 0
    run_main(["5"], capsys)
    assert sum(1 for c in fake.calls if c[:2] == ("state.py", "merge-fix")) == 1


def test_rerun_after_prepare_failure_retries_rotation(tmp_path, monkeypatch, capsys):
    """prepare で止まった打ち直しは、次のラウンドへ進まずに巻き直しからやり直す。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeRotateFails(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    code, out = run_main(["5"], capsys)
    Path(out["items"][0]["result_file"]).write_text("{}")
    run_main(["5"], capsys)
    starts = sum(1 for c in fake.calls if c[:2] == ("state.py", "start-round"))
    run_main(["5"], capsys)
    assert sum(1 for c in fake.calls if c[:2] == ("state.py", "start-round")) == starts
    assert sum(1 for c in fake.calls if c[:2] == ("rotate-pr.sh", "prepare")) == 2


class FakeSetCurrentFails(FakeReview):
    """squash の巻き直しで execute は PR を作り、1 度目の set-current-pr が失敗する。"""

    def __init__(self, tmp: Path):
        super().__init__(tmp)
        self.set_fails = 1

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name == "rotate-pr.sh":
            self.calls.append((name, *cmd[2:]))
            return 0, ("NEW_PR=9\nNEW_BRANCH=b9\n" if cmd[2] == "execute" else "")
        if name == "state.py" and cmd[2] == "should-rotate":
            self.calls.append((name, *cmd[2:]))
            return 0, ""
        if name == "state.py" and cmd[2] == "set-current-pr" and self.set_fails:
            self.calls.append((name, *cmd[2:]))
            self.set_fails -= 1
            return 1, ""
        return super().__call__(cmd, env, cwd)


def test_rerun_after_set_current_failure_does_not_create_pr_again(tmp_path, monkeypatch, capsys):
    """PR を作った後の set-current-pr で止まっても、打ち直しは execute を再実行せず、作った PR で続ける。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeSetCurrentFails(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    argv = ["5", "--rotate-mode", "squash"]
    code, out = run_main(argv, capsys)
    Path(out["items"][0]["result_file"]).write_text("{}")
    code, _ = run_main(argv, capsys)
    assert code != 0
    run_main(argv, capsys)
    assert sum(1 for c in fake.calls if c[:2] == ("rotate-pr.sh", "execute")) == 1
    sets = [c for c in fake.calls if c[:2] == ("state.py", "set-current-pr")]
    assert len(sets) == 2 and sets[-1][3:] == ("9", "--head-branch", "b9")


class FakeMissingResult(FakeReview):
    """1 度目の read-result が結果なしで 1 を返し、judge が起動し直しを求める。"""

    def __init__(self, tmp: Path):
        super().__init__(tmp)
        self.judges = [7, 0]
        self.reads = [1, 0]

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        if name == "state.py" and cmd[2] == "read-result":
            self.calls.append((name, *cmd[2:]))
            return self.reads.pop(0), ""
        if name == "state.py" and cmd[2] == "judge" and self.judges[0] == 7:
            self.calls.append((name, *cmd[2:]))
            self.judges.pop(0)
            return 7, "RELAUNCH_AGENTS='codex'\n"
        return super().__call__(cmd, env, cwd)


def test_missing_result_skips_verify_and_critique_until_relaunch(tmp_path, monkeypatch, capsys):
    """結果の欠けた担当がいるラウンドでは、検証と反証を起動し直した後の 1 回だけ通す。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    fake = FakeMissingResult(tmp_path)
    monkeypatch.setattr(cr, "call", fake)
    run_main(["5"], capsys)
    assert sum(1 for c in fake.calls if c[:2] == ("state.py", "verify-findings")) == 1
    assert sum(1 for c in fake.calls if c[0] == "critique-round.sh") == 1
    assert sum(1 for c in fake.calls if c[0] == "launch-reviewer.sh") == 2
