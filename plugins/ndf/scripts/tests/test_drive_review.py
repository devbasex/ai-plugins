"""cross-review の drive.py（駆動の止まりの表を含む）。

駆動が呼ぶスクリプトは `call` を差し替えて模す。gh と claude は PATH の先頭に置いた偽物。実機の claude は起動しない。
"""

from __future__ import annotations

import importlib.util
import json
import os
import pickle
import tempfile
import traceback
import types
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILLS = SCRIPTS.parent / "skills"
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import PAUSE_CODES as STEP_PAUSE_CODES, validate_result  # noqa: E402
import drive_pause  # noqa: E402


def load(name, path):
    """同じ名前で 1 度だけ読む（耐久ワークフローの登録を 1 つのモジュールに保つ）。"""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cr = load("cross_review_drive", SKILLS / "cross-review" / "scripts" / "drive.py")
rf = load("rf_drive", SKILLS / "cross-refactoring" / "scripts" / "drive.py")


def fork_main(mod, argv) -> tuple[int, str]:
    """駆動を fork した子で 1 回打つ。子は止まりのまま `os._exit` で抜けるため、テストのプロセスに耐久の記録と
    待ちのスレッドを残さない。差し替えた `call` の中身（呼び出しの記録ほか）は子が終わる前に書き出して親へ戻す。"""
    fake = mod.call
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

            mod.durable.exit_leaving_pending = leave
            try:
                mod.main(argv)
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
    if saved and hasattr(fake, "__dict__"):
        fake.__dict__.update(pickle.loads(saved))
    return os.waitstatus_to_exitcode(status), text


def run_main(mod, argv, capsys):
    code, text = fork_main(mod, argv)
    out = json.loads(text.strip().splitlines()[-1])
    assert validate_result(out, code) == []
    return code, out


# --- cross-review ---------------------------------------------------------------


class FakeReview:
    """state.py などの応答を模す。judges は判定の終了コードを順に返す。"""

    def __init__(self, tmp: Path, judges, rotate=False):
        self.tmp, self.judges, self.rotate = tmp, list(judges), rotate
        self.calls = []
        self.state = {
            "repo": "o/r",
            "current_pr": 5,
            "worktree_path": str(tmp / "wt"),
            "head_branch": "feat/x",
            "base_branch": "develop",
            "rounds": [],
            "pr_history": [{"pr": 5}],
        }
        self.save()

    def save(self):
        (self.tmp / "cross-review-pr5-state.json").write_text(json.dumps(self.state))

    def __call__(self, cmd, env=None, cwd=None):
        name = Path(cmd[1]).name if cmd[0] in (PY, "bash") else cmd[0]
        args = cmd[2:]
        self.calls.append((name, *args))
        if name == "state.py":
            sub = args[0]
            if sub == "init":
                return 0, f"PR=5\nTMP_DIR={self.tmp}\nWORKTREE={self.tmp / 'wt'}\n"
            if sub == "start-round":
                if not self.judges:
                    return 1, ""
                n = len(self.state["rounds"]) + 1
                self.state["rounds"].append(
                    {
                        "round": n,
                        "reviewers": ["codex", "kiro"],
                        "codex": {"intent": "REQUEST_CHANGES", "comments": 3, "review_url": "https://x/r1"},
                        "kiro": {"intent": "APPROVE", "comments": 1},
                    }
                )
                self.save()
                return 0, f"ROUND={n}\nREVIEWERS='codex kiro'\nREVIEWERS_CSV=codex,kiro\n"
            if sub == "judge":
                rc = self.judges.pop(0)
                rc, out = rc if isinstance(rc, tuple) else (rc, "")
                if rc == 0:
                    self.state["final"] = "approved"
                    self.save()
                return rc, out
            if sub == "check-oscillation":
                return 2, ""
            if sub == "merge-fix":
                self.state["rounds"][-1]["fix"] = {"fixed": 2, "rejected": 1}
                self.save()
                return 0, ""
            if sub == "should-rotate":
                return (0 if self.rotate else 2), ""
            if sub == "verify-sweep":
                self.state["sweep"] = {"remaining_open": 1, "verified": True}
                self.save()
                return 6, ""
            if sub == "report":
                return 0, "## 報告\n"
            return 0, ""
        if name == "rotate-pr.sh" and args[0] == "execute":
            return 0, "NEW_PR=9\nNEW_PR_URL=https://x/9\nNEW_BRANCH=feat/x\n"
        return 0, ""


def test_review_drive_pauses_for_fix_then_sweep_then_finishes(tmp_path, monkeypatch, capsys):
    fake = FakeReview(tmp_path, judges=[2, 0])
    monkeypatch.setattr(cr, "call", fake)
    code, out = run_main(cr, ["5", "--max-rounds", "4"], capsys)
    assert code == 20 and out["status"] == "gate" and out["next"] == "fix"
    item = out["items"][0]
    assert item["pause"] == "fix" and item["round"] == 1
    assert item["cwd"] == str(tmp_path / "wt")  # 直しの worker の作業場所は cross-review の worktree
    prompt = Path(item["prompt_file"]).read_text()
    assert (
        "/ndf:fix 5`" in prompt
        and "--defer-nit" not in prompt
        and "CROSS_REVIEW_STATE=" in prompt
        and str(tmp_path / "wt") in prompt
        and "https://x/r1" in prompt
    )
    for tool in ("pint", "larastan", "phpstan", "ruff", "eslint", "mypy"):
        assert tool not in prompt
    assert "02-fix-and-rotation" not in prompt  # 修正の手順は /ndf:fix が持ち、雛形を読ませない
    assert Path(item["result_file"]).name == "fix-pr5-result.json" and item["result_file"] in prompt
    assert ("state.py", "init", "5", "--max-rounds", "4") in fake.calls

    # 打ち直しても結果ファイルが無ければ同じ pause を返す
    code, out = run_main(cr, ["5"], capsys)
    assert code == 20

    Path(item["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 21 and out["items"][0]["pause"] == "sweep"
    assert out["items"][0]["cwd"] == str(tmp_path / "wt")
    assert any(c[:2] == ("state.py", "merge-fix") for c in fake.calls)

    Path(out["items"][0]["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 0 and out["status"] == "ok"
    m = out["metrics"]
    # 指摘は修正担当の単位（直した 2 + 却下 1）、コメントの数は別の名前で出す（#1317）
    assert (m["rounds"], m["comments"], m["findings"], m["fixed"], m["rejected"], m["unresolved"], m["final"]) == (
        2,
        8,
        3,
        2,
        1,
        1,
        "approved",
    )
    assert any(c[0] == "result_posts.py" for c in fake.calls)
    assert Path(out["items"][0]["report"]).read_text() == "## 報告\n"


def test_review_drive_goes_from_model_stage_to_detail_without_fix(tmp_path, monkeypatch, capsys):
    """設計 PR のモデルの段が承認されたら、修正を挟まずに詳細の段のラウンドへ進む（#1111）。"""
    fake = FakeReview(tmp_path, judges=[(2, "MODEL_CONFIRMED=1\n"), 0])
    monkeypatch.setattr(cr, "call", fake)
    code, out = run_main(cr, ["5"], capsys)
    assert code == 21 and out["items"][0]["pause"] == "sweep"
    assert [c[1] for c in fake.calls if c[0] == "state.py"].count("start-round") == 2
    assert not any(c[:2] in (("state.py", "merge-fix"), ("state.py", "check-oscillation")) for c in fake.calls)


def test_review_drive_light_rotation_pauses_for_newtext(tmp_path, monkeypatch, capsys):
    fake = FakeReview(tmp_path, judges=[2, 0], rotate=True)
    monkeypatch.setattr(cr, "call", fake)
    _, out = run_main(cr, ["5"], capsys)
    Path(out["items"][0]["result_file"]).write_text("{}")
    code, out = run_main(cr, ["5"], capsys)
    assert code == 22 and out["items"][0]["pause"] == "newtext"
    assert "Step 6b" in Path(out["items"][0]["prompt_file"]).read_text()
    assert out["items"][0]["cwd"] == str(tmp_path / "wt")
    Path(out["items"][0]["result_file"]).write_text('{"title": "t", "body": "b"}')
    code, out = run_main(cr, ["5"], capsys)
    assert code == 21
    assert ("state.py", "set-current-pr", "5", "9", "--head-branch", "feat/x") in fake.calls


def test_review_drive_stops_when_init_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cr, "call", lambda cmd, env=None, cwd=None: (3, ""))
    code, out = run_main(cr, ["5"], capsys)
    assert code == 1 and out["status"] == "stopped" and out["metrics"]["exit"] == 3


def test_both_drives_share_the_pause_table():
    assert cr.dp is rf.dp
    assert cr.Stop is cr.dp.Stop and rf.Stop is rf.dp.Stop
    assert not hasattr(cr, "PAUSES") and not hasattr(rf, "PAUSES")


def test_pause_table_gives_exit_codes():
    assert drive_pause.PAUSE_CODES == {"fix": 20, "sweep": 21, "newtext": 22, "cross-review": 23}
    assert all(c in STEP_PAUSE_CODES for c in drive_pause.PAUSE_CODES.values())
    for kind, code in drive_pause.PAUSE_CODES.items():
        out = drive_pause.pause("t", kind, "/p", "/r", 2, {"rounds": 2}, command="x")
        assert validate_result(out, code) == []
        assert drive_pause.exit_code(out) == code
        assert out["items"][0] == {"pause": kind, "prompt_file": "/p", "result_file": "/r", "round": 2, "command": "x"}
    assert drive_pause.exit_code(drive_pause.done("t", "s", "/rp", {})) == 0
    stopped = drive_pause.stopped("t", "s", {"rounds": 1}, 3)
    assert drive_pause.exit_code(stopped) == 1 and stopped["metrics"]["exit"] == 3
    with pytest.raises(ValueError):
        drive_pause.pause("t", "unknown", "/p", "/r", 0, {})


# --- #1483 AC19・AC20: 最終スイープの検証のコマンド ---------------------------------------


def _sweep_prompt(tmp_path, decl):
    wt = tmp_path / "wt"
    (wt / ".ndf").mkdir(parents=True)
    if decl is not None:
        (wt / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    fake = FakeReview(tmp_path, [])
    drive = types.SimpleNamespace(tmp=tmp_path, pr=5, state=lambda: fake.state, path=lambda stem: str(tmp_path / f"{stem}.json"))
    return cr.Drive.sweep_prompt(drive)


def test_the_sweep_names_the_declared_whole_tests(tmp_path):
    """AC19・I13 — 宣言の `test` があれば、全体テスト（テストと静的解析）を名指しし、探し方を使わないと書く。"""
    decl = {
        "test": {
            "suites": [
                {"name": "py", "runner": "pytest", "command": "pytest -q", "scope_command": "pytest {paths}"},
                {"name": "lint", "runner": "sh", "kind": "lint", "command": "bash scripts/check-lint.sh"},
            ]
        }
    }
    prompt = _sweep_prompt(tmp_path, decl)
    assert "  - pytest -q\n  - bash scripts/check-lint.sh\n" in prompt
    assert "Step 7.5 の探し方は使わない" in prompt


def test_the_sweep_keeps_the_search_without_a_declaration(tmp_path):
    """AC20 — 宣言が無ければ、検証のコマンドの段落を足さない（今の探し方のまま）。"""
    prompt = _sweep_prompt(tmp_path, None)
    assert "Step 7.5 の探し方は使わない" not in prompt and ".ndf/project.json の test" not in prompt
