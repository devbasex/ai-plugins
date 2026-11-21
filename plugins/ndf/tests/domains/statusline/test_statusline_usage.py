"""NDF の status line が子の環境の `NDF_ACCOUNT_USAGE_FILE` へ使用率を書く（#3864 の 1・2・11、I6・I7）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from ndf.domains.relay import accounts
from ndf.domains.relay.account_decide import Usage
from ndf.domains.statusline import settings

if shutil.which("jq") is None:
    pytest.skip("jq が無い", allow_module_level=True)

LATER = 4_000_000_000
NEEDED = (
    "bash",
    "cat",
    "date",
    "sed",
    "mkdir",
    "mv",
    "rm",
    "rmdir",
    "stat",
    "basename",
    "dirname",
    "find",
    "tail",
    "sort",
    "flock",
    "mktemp",
)


def _input(rate_limits: Optional[object], **extra) -> str:
    data: dict = {"workspace": {"project_dir": "/w/p"}, "model": {"display_name": "Opus 5"}, **extra}
    if rate_limits is not None:
        data["rate_limits"] = rate_limits
    return json.dumps(data)


def _limits(five: Optional[float] = 42.0, seven: Optional[float] = 18.0, resets: int = LATER) -> dict:
    got: dict = {}
    if five is not None:
        got["five_hour"] = {"used_percentage": five, "resets_at": resets}
    if seven is not None:
        got["seven_day"] = {"used_percentage": seven, "resets_at": resets + 86400}
    return got


def _env(tmp_path: Path, usage: Optional[Path], path: Optional[str] = None) -> dict[str, str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": path or os.environ["PATH"],
        "NDF_STATE_HOME": str(tmp_path / "state"),
        "NDF_STATUSLINE_AUTH_CACHE": str(tmp_path / "auth.json"),
    }
    if usage is not None:
        env["NDF_ACCOUNT_USAGE_FILE"] = str(usage)
    return env


def _run(tmp_path: Path, text: str, usage: Optional[Path], path: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(settings.BODY_SOURCE)], input=text, env=_env(tmp_path, usage, path), capture_output=True, text=True, check=False
    )


def _windows(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["windows"]


def test_3864_1_status_line_writes_the_two_windows_of_the_account(tmp_path):
    usage = tmp_path / "state" / "relay" / "accounts" / "usage" / "work1" / "g1.json"
    other = usage.with_name("g0.json")
    other.parent.mkdir(parents=True)
    other.write_text('{"windows": {}}', encoding="utf-8")
    got = _run(tmp_path, _input(_limits()), usage)
    assert got.returncode == 0
    assert _windows(usage) == {
        "five_hour": {"percent": 42, "resets_at": LATER},
        "seven_day": {"percent": 18, "resets_at": LATER + 86400},
    }
    assert other.read_text(encoding="utf-8") == '{"windows": {}}'
    # 残るのは記録と錠のファイルだけ（一時ファイルを残さない）
    assert not [p for p in usage.parent.iterdir() if p.name not in ("g1.json", "g1.json.lock", "g0.json")]


def test_3864_1_a_late_old_input_does_not_lower_the_value(tmp_path):
    usage = tmp_path / "u" / "work1.json"
    _run(tmp_path, _input(_limits(five=100.0)), usage)
    data = json.loads(usage.read_text(encoding="utf-8"))
    data["windows"]["seven_day_scoped"] = {"claude-opus-4": {"percent": 31.0, "resets_at": LATER}}
    usage.write_text(json.dumps(data), encoding="utf-8")
    _run(tmp_path, _input(_limits(five=40.0, seven=None)), usage)
    got = _windows(usage)
    assert got["five_hour"]["percent"] == 100 and got["seven_day"]["percent"] == 18
    assert got["seven_day_scoped"] == {"claude-opus-4": {"percent": 31.0, "resets_at": LATER}}
    before = usage.stat().st_mtime_ns
    _run(tmp_path, _input(_limits(five=100.0)), usage)  # 同じ値なら書かない
    assert usage.stat().st_mtime_ns == before


def test_3864_1_five_hour_window_may_be_missing(tmp_path):
    usage = tmp_path / "u" / "work1.json"
    _run(tmp_path, _input(_limits(five=None)), usage)
    assert set(_windows(usage)) == {"seven_day"}


@pytest.mark.parametrize(
    "rate_limits",
    [None, {}, {"spend_limit": {"used_percentage": 50, "resets_at": LATER}}, {"five_hour": {"used_percentage": "x", "resets_at": LATER}}],
)
def test_3864_1_no_valid_window_writes_nothing(tmp_path, rate_limits):
    usage = tmp_path / "u" / "work1.json"
    assert _run(tmp_path, _input(rate_limits), usage).returncode == 0
    assert not usage.parent.exists()


def test_3864_2_i6_two_accounts_at_the_same_time_both_remain(tmp_path):
    files = [tmp_path / "u" / f"work{i}" / "g.json" for i in (1, 2)]
    procs = [
        subprocess.Popen(
            ["bash", str(settings.BODY_SOURCE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            env=_env(tmp_path, f),
            text=True,
        )
        for f in files
    ]
    for i, p in enumerate(procs):
        p.communicate(_input(_limits(five=10.0 * (i + 1))))
    assert [_windows(f)["five_hour"]["percent"] for f in files] == [10, 20]


def test_3864_11_i7_unwritable_place_or_missing_jq_keeps_the_output(tmp_path):
    text = _input(_limits())
    plain = _run(tmp_path, text, None)
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    got = _run(tmp_path, text, blocker / "sub" / "u.json")
    assert (got.returncode, got.stdout, got.stderr) == (0, plain.stdout, "")
    assert _run(tmp_path, text, Path("relative.json")).stdout == plain.stdout
    # jq の無い PATH（必要なコマンドだけを並べる）
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in NEEDED:
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    bare = _run(tmp_path, text, None, str(bin_dir))
    usage = tmp_path / "u" / "work1.json"
    got = _run(tmp_path, text, usage, str(bin_dir))
    assert (got.returncode, got.stdout, got.stderr) == (0, bare.stdout, "") and not usage.exists()


def test_status_line_and_usages_use_the_same_path(tmp_path):
    state = tmp_path / "state"
    usage = accounts.usage_path(state, "work1", "g1")
    _run(tmp_path, _input(_limits(five=42.0, seven=18.0)), usage)
    assert accounts.usages(state, tmp_path / "store", LATER - 10, {"work1": "g1"}) == [Usage("work1", 42.0, False)]


def test_status_line_never_takes_a_lock_another_writer_holds(tmp_path):
    import fcntl

    usage = tmp_path / "u" / "work1.json"
    lock = usage.with_name(usage.name + ".lock")
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        os.utime(lock, (1, 1))  # 古い錠のファイルでも、オーナーが生きている間は奪わない
        assert _run(tmp_path, _input(_limits()), usage).returncode == 0
        assert not usage.exists() and lock.exists()
    finally:
        os.close(fd)
    _run(tmp_path, _input(_limits()), usage)
    assert _windows(usage)["five_hour"]["percent"] == 42


def test_an_unchanged_redraw_starts_only_jq(tmp_path):
    """値が前と同じ再描画は、錠・一時ファイル・時刻のコマンドを起動しない（jq の 1 回で比べて終える）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    watched = ("date", "mkdir", "rmdir", "dirname", "flock", "mktemp", "mv")
    for name in (*NEEDED, "jq"):
        found = shutil.which(name)
        if not found:
            continue
        if name in watched:
            (bin_dir / name).write_text(f'#!/bin/sh\necho {name} >> "{log}"\nexec "{found}" "$@"\n', encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        else:
            (bin_dir / name).symlink_to(found)
    usage = tmp_path / "u" / "work1.json"
    text = _input(_limits())
    _run(tmp_path, text, usage, str(bin_dir))
    assert _windows(usage)["five_hour"]["percent"] == 42
    log.unlink()
    assert _run(tmp_path, text, usage, str(bin_dir)).returncode == 0
    assert not log.exists()
