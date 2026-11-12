"""cross-refactoring の drive.py がライブラリの pause の表（`scripts/lib/drive_pause.py`）で止まること。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
LIB = SKILL.parents[1] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import drive_pause  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rf = _load("rf_drive_contract", SKILL / "scripts" / "drive.py")


@pytest.fixture(autouse=True)
def durable_in_process(monkeypatch):
    """止まりで抜けるところを SystemExit に替え、同じプロセスの打ち直しが開いたままの耐久の記録を続ける。"""

    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(rf.durable, "exit_leaving_pending", leave)
    yield
    rf.durable.close()


def test_drive_reads_the_shared_table():
    assert rf.dp is drive_pause
    assert rf.Stop is drive_pause.Stop
    assert not hasattr(rf, "PAUSES")


def test_final_gate_pause_exits_with_the_shared_code(tmp_path, monkeypatch, capsys):
    (tmp_path / "cross-refactoring-rf7-state.json").write_text(json.dumps({"items": []}))

    def fake(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py":
            sub = cmd[2]
            if sub == "init":
                return 0, f"ID=7\nTMP_DIR={tmp_path}\nPHASE=final\nWORK={tmp_path}\n"
            if sub == "readopt":
                return 2, ""  # 計画の無い状態の採り直し（最終ゲートへ）
            if sub == "final-gate":
                return 0, "FINAL_GATE=cross-review\n"
        return 0, ""

    monkeypatch.setattr(rf, "call", fake)
    with pytest.raises(SystemExit) as e:
        rf.main(["5", "--scope", "src", "--baseline-test", "pytest"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == drive_pause.PAUSE_CODES["cross-review"]
    assert out["items"][0]["pause"] == "cross-review" and out["next"] == "cross-review"
    # 続きを待つ耐久ワークフローを残すと、テストのプロセスが抜けられない。結果を書いて終わらせる
    Path(out["items"][0]["result_file"]).write_text('{"review_status": "approved"}')
    with pytest.raises(SystemExit):
        rf.main(["5", "--scope", "src", "--baseline-test", "pytest"])


def test_abort_keeps_the_original_code_in_metrics(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rf, "call", lambda cmd, env=None, cwd=None: (4, ""))
    with pytest.raises(SystemExit) as e:
        rf.main(["5"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == drive_pause.EXIT_STOPPED and out["metrics"]["exit"] == 4


def _readopt_drive(statuses_after_merge_tests: list[str]):
    """採り直しの 1 巡を、子の手順を呼ばずに打つ駆動（打った手順の名前を `calls` に積む）。"""
    d = object.__new__(rf.Drive)
    d.v = {"ID": "7", "TESTS_NEEDED": "1"}
    calls: list[str] = []
    rounds = iter([0, rf.GO_FINAL])  # 1 巡だけ採り、次の採り直しで最終ゲートへ

    def fake_rf(sub, *args, ok=(0,)):
        calls.append(sub)
        return (next(rounds) if sub == "readopt" else 0), {}

    d.rf = fake_rf
    d.impl_phase = lambda phase, *a: calls.append(f"cli:{phase}")
    d.verify_rounds = lambda: calls.append("verify")
    d.state = lambda: {"items": [{"status": s} for s in statuses_after_merge_tests]}
    return d, calls


def test_readopt_round_skips_the_implement_cli_when_nothing_is_left_to_implement():
    """前の巡の検証済みの項目だけが残ったら実装担当を起動せず、取り消しの後の検証は行う（#1825 の指摘）。"""
    d, calls = _readopt_drive(["verified", "carried", "deferred"])
    d.readopt_rounds()
    assert calls == ["readopt", "cli:add-tests", "merge-tests", "verify", "readopt"]


def test_readopt_round_launches_the_implement_cli_for_tested_items():
    d, calls = _readopt_drive(["verified", "tested"])
    d.readopt_rounds()
    assert calls == ["readopt", "cli:add-tests", "merge-tests", "cli:implement", "merge-implement", "verify", "readopt"]


# ---------- #2047: 時間の枠の不足は飛ばした欠けの残る結果、下限まで上げたらステップの延長 ----------


@pytest.mark.parametrize(
    "kind, detail",
    [
        ("init-timeout", "着手前のテストが上限 1800 秒で終わらなかった"),
    ],
)
def test_a_window_shortage_returns_the_skipped_gap(tmp_path, monkeypatch, capsys, kind, detail):
    """I5: init の SKIP だけを、gaps 1 件の stopped・終了コード 6 で返す。summary と理由に「落ちた」「失敗」を入れない。"""

    def fake(cmd, env=None, cwd=None):
        return 4, f"SKIP={kind}\nSKIP_DETAIL='{detail}'\n"

    monkeypatch.setattr(rf, "call", fake)
    with pytest.raises(SystemExit) as e:
        rf.main(["5"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == 6
    assert out["status"] == "stopped"
    assert out["metrics"] == {"skipped": {"kind": kind, "reason": detail}, "exit": 4}
    assert out["gaps"] == [{"kind": "refactor", "name": kind, "reason": f"時間の枠に収まらず飛ばした: {detail}"}]
    for text in (out["summary"], out["gaps"][0]["reason"]):
        assert "落ちた" not in text and "失敗" not in text


def _started_drive(tmp_path, monkeypatch, source: str):
    """init が BUDGET_SOURCE を返す start を、子を呼ばずに打つ。延長のファイルのパスを返す。"""
    target = tmp_path / "01-refactor.extend.json"
    monkeypatch.setenv(drive_pause.EXTEND_ENV, str(target))
    d = rf.Drive(5, [])
    out = f"ID=7\nTMP_DIR={tmp_path}\nBUDGET_MINUTES=40\nBUDGET_SOURCE={source}\n"
    monkeypatch.setattr(rf, "call", lambda cmd, env=None, cwd=None: (0, out if "init" in cmd else ""))
    d.start()
    return target


@pytest.mark.parametrize("source", ["default", "explicit"])
def test_the_budget_source_does_not_extend_the_step(tmp_path, monkeypatch, source):
    assert not _started_drive(tmp_path, monkeypatch, source).exists()


def test_the_extension_never_moves_back(tmp_path, monkeypatch):
    """I7: 後から小さい秒を書いても、延長のファイルの秒は前へ戻らない。"""
    target = tmp_path / "x.extend.json"
    monkeypatch.setenv(drive_pause.EXTEND_ENV, str(target))
    assert drive_pause.write_extension(6174, "着手前のテスト")
    assert drive_pause.write_extension(2400, "下限")
    assert drive_pause.read_extension(target) == (6174.0, "着手前のテスト")
    monkeypatch.delenv(drive_pause.EXTEND_ENV)
    assert not drive_pause.write_extension(9000, "エンジンの外")


# ---------- #2457: 安全網で止まったら取り消さずに gate で人へ渡す ----------


def test_a_safety_net_halt_returns_a_gate_with_the_item_states(tmp_path, monkeypatch, capsys):
    """受け入れ条件 18・I8: コーディングの CLI の前に安全網が尽きていたら、起動せずに gate・終了コード 10 で止める。
    項目は取り消さず、項目ごとの状態・落ちているテスト・修正の履歴が `items[0].refactor_items` に載り、実績の行を書く。"""
    state = {
        "items": [
            {
                "id": "I-001",
                "path": "src/a.py",
                "symbol": "f",
                "status": "failing",
                "failed_check": {"tests": ["tests/test_a.py::t"]},
                "fix_history": [{"round": 0, "failing": ["tests/test_a.py::t"], "progress": "first"}],
                "estimate": {"test": 0, "implement": 1.3},
            }
        ],
        "budget": {"minutes": 60, "extension": None},
        "safety_net": {"used_seconds": 7200.0},
    }
    (tmp_path / "cross-refactoring-rf7-state.json").write_text(json.dumps(state))
    seen: list = []

    def fake(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py":
            sub = cmd[2]
            seen.append(sub)
            if sub == "init":
                return 0, f"ID=7\nTMP_DIR={tmp_path}\nPHASE=verify\nWORK={tmp_path}\n"
            if sub == "verify":
                return 0, "VERIFY=fix\nFIX_IMPL=codex\n"
            if sub == "start-phase":
                return 0, "PHASE_TIMEOUT=''\nHALT=1\nHALT_REASON='安全網の上限 120 分に当たった'\n"
        if cmd[0] == "bash" and "launch-cli.sh" in cmd[1]:
            seen.append("launch")
        return 0, ""

    monkeypatch.setattr(rf, "call", fake)
    with pytest.raises(SystemExit) as e:
        rf.main(["5", "--scope", "src"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == 10
    assert out["status"] == "gate" and "安全網の上限" in out["next"] and "--safety-net-minutes" in out["next"]
    assert "launch" not in seen, "止めた後に新しいコーディングの CLI を起動しない"
    assert "item-actuals" in seen
    item = out["items"][0]["refactor_items"][0]
    assert item["status"] == "failing" and item["failing"] == ["tests/test_a.py::t"] and item["fix_history"]
    assert out["metrics"]["budget_minutes"] == 60 and out["metrics"]["safety_net_minutes"] == 120.0


def test_coding_time_at_the_limit_lets_the_merge_run_and_the_next_start_halts():
    """安全網の上限に届いた CLI の後も取り込みまで進め、止めるのは次のコーディングの CLI の起動の前（I8）。

    CLI の直後に止めると、最後の CLI のコミットが項目に結ばれずに HEAD に残り、打ち直しで同じ手順の CLI をもう 1 度起動する。"""
    import assignee_launch

    calls: list[str] = []

    class Fake(assignee_launch.AssigneeLaunch):
        def __init__(self):
            self.v = {"ID": "7", "IMPL": "claude"}

        def rf(self, *args, ok=(0,)):
            calls.append(args[0])
            out = {"coding-time": {"HALT": "1", "HALT_REASON": "安全網の上限 120 分に当たった"}}.get(args[0], {})
            if args[0] == "start-phase" and "coding-time" in calls:
                out = {"HALT": "1", "HALT_REASON": "安全網の上限 120 分に当たった"}
            self.v.update(out)
            return 0, out

        def launch(self, impl, phase):
            calls.append(f"launch:{phase}")
            return 0

        def monitor(self, impl, phase, stem):
            return 0, []

    d = Fake()
    d.impl_phase("implement")
    calls.append("merge-implement")
    with pytest.raises(assignee_launch.Halt):
        d.impl_phase("fix")
    assert calls == ["start-phase", "launch:implement", "coding-time", "merge-implement", "start-phase"]
