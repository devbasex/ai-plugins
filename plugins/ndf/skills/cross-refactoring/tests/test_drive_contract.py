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
        ("window", "要る下限 61 分 > 想定最大時間 30 分（着手前のテスト 495.0 秒）"),
        ("init-timeout", "着手前のテストが上限 180 秒で終わらなかった"),
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


def test_a_floor_budget_extends_the_step(tmp_path, monkeypatch):
    """決定 4: 下限まで上げた想定最大時間（40 分）の秒を延長のファイルへ書く。"""
    target = _started_drive(tmp_path, monkeypatch, "floor")
    assert drive_pause.read_extension(target) == (2400.0, "想定最大時間を下限の 40 分へ上げた")


@pytest.mark.parametrize("source", ["default", "explicit"])
def test_other_budget_sources_do_not_extend_the_step(tmp_path, monkeypatch, source):
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
