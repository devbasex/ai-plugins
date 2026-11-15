"""`ndf relay notice` の位置の判定とアナウンス、`ndf relay stop --all`（#3569）。"""

from __future__ import annotations

import math
import os

from ndf import identity
from ndf.domains.relay import cli, hub, notice, registry, stop_signal
from ndf.domains.relay.tmux import Tmux


def _tree(table: dict[int, tuple[int, str]]):
    return lambda pid: table.get(pid)


def _wrapper(tmp_path, hold_lock, pid: int):
    directory = tmp_path / "w"
    directory.mkdir()
    registry.write_state(directory, {"pid": pid, "state": "running"})
    fd = hold_lock(directory / registry.LOCK_FILE)
    return directory, fd


def test_no_dir_and_not_running(tmp_path):
    assert notice.position({}, 10).kind == notice.NO_DIR
    assert notice.position({"NDF_RELAY_DIR": str(tmp_path / "none")}, 10).kind == notice.NO_DIR
    (tmp_path / "w").mkdir()
    assert notice.position({"NDF_RELAY_DIR": str(tmp_path / "w")}, 10).kind == notice.NOT_RUNNING


def test_direct_child_even_through_a_shell(tmp_path, hold_lock):
    directory, fd = _wrapper(tmp_path, hold_lock, 100)
    try:
        env = {"NDF_RELAY_DIR": str(directory)}
        # 呼び手（bash 30）→ claude 20 → ラッパー 100
        tree = _tree({30: (20, "bash"), 20: (100, "claude"), 100: (1, "python3")})
        pos = notice.position(env, 30, tree)
        assert pos == notice.Position(notice.RELAY, 100, 20)
        # conductor が起こした claude -p（孫）が先に当たると直接の子でない
        grand = _tree({40: (30, "claude"), 30: (20, "bash"), 20: (100, "claude")})
        assert notice.position(env, 40, grand).kind == notice.NOT_CHILD
        assert notice.position(env, 50, _tree({})).kind == notice.NOT_CHILD
    finally:
        os.close(fd)


def test_lines_follow_position_and_quiet():
    inside = notice.Position(notice.RELAY, 1, 2)
    assert notice.lines(inside, 5).text.startswith("約 5 秒後に")
    assert notice.lines(inside, 0).text.startswith("まもなく")
    assert notice.lines(inside, math.inf).inside is True
    assert "自動で切り替えない" in notice.lines(inside, math.inf).text
    for kind in (notice.NO_DIR, notice.NOT_RUNNING, notice.NOT_CHILD):
        assert notice.lines(notice.Position(kind), 5).inside is False
    assert len({notice.lines(notice.Position(k), 5).text for k in (notice.NO_DIR, notice.NOT_RUNNING, notice.NOT_CHILD)}) == 3


def test_notice_subcommand_reports_outside(monkeypatch, capsys, last_result):
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    assert cli.main(["notice"]) == 0
    got = last_result(capsys.readouterr().out)
    assert got["metrics"] == {"inside": False}
    assert got["items"][0]["position"] == notice.NO_DIR


def test_stop_all_signals_every_running_wrapper(monkeypatch, capsys, last_result):
    running = [hub.hub_decide.Running(n, "1") for n in ("a", "b", "c", "gone")]
    monkeypatch.setattr(registry, "running", lambda state_dir: running)
    state_dir = identity.load().state_dir
    for n in ("a", "b", "c"):
        registry.wrapper_dir(state_dir, n).mkdir(parents=True)
    monkeypatch.setattr(Tmux, "has", lambda self, name: name != "c")  # c は端末から直接起動したラッパー
    asked: list[str] = []

    def submit(ctx, req):
        asked.append(req["name"])
        return hub.Outcome(req["name"] != "b", "止めた" if req["name"] != "b" else "断った", "refused", [])

    monkeypatch.setattr(hub, "submit", submit)
    monkeypatch.setattr(cli, "plugin_version", lambda root: "0")
    assert cli.main(["stop", "--all"]) == 1
    got = last_result(capsys.readouterr().out)
    assert asked == ["a", "b"]  # tmux の外の c と置き場の無い gone へは名前つきの停止を頼まない
    assert all(stop_signal.has_stop(registry.wrapper_dir(state_dir, n)) for n in ("a", "b", "c"))
    assert got["metrics"] == {"stopped": 2}
    assert [i["name"] for i in got["items"] if not i["ok"]] == ["b", "gone"]
    assert got["cause"]["kind"] == "refused"
    monkeypatch.setattr(registry, "running", lambda state_dir: [])
    assert cli.main(["stop", "--all"]) == 0
    assert last_result(capsys.readouterr().out)["metrics"] == {"stopped": 0}


def test_stop_needs_a_name_or_all_but_not_both():
    assert cli.main(["stop"]) == 2
    assert cli.main(["stop", "x", "--all"]) == 2
