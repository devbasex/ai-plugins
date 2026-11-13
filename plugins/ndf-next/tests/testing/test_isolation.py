from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from ndf.testing import isolation

INNER = textwrap.dedent(
    """
    import os
    from pathlib import Path
    import pytest

    @pytest.fixture(autouse=True)
    def _iso(forbid_real_state):
        return forbid_real_state

    def test_writes_to_the_real_state():
        target = Path.home() / ".local" / "state" / "ndf" / "ledger.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{壊れた行")

    def test_swallowed_write_still_fails():
        try:
            open(Path.home() / ".claude" / "x", "w")
        except OSError:
            pass

    def test_symlink_into_the_real_state_fails(tmp_path):
        (tmp_path / "link").symlink_to(Path.home() / ".local" / "state")
        (tmp_path / "link" / "ledger.jsonl").write_text("x")

    def test_dir_fd_removal_in_the_real_state_fails():
        real = Path.home() / ".claude"
        fd = os.open(real, os.O_RDONLY)
        try:
            os.remove("kept", dir_fd=fd)
        finally:
            os.close(fd)

    def test_hard_link_from_the_real_state_fails(tmp_path):
        alias = tmp_path / "alias"
        os.link(Path.home() / ".claude" / "kept", alias)
        alias.write_text("x")

    def test_redirected_state_is_writable():
        Path(os.environ["XDG_STATE_HOME"]).mkdir(parents=True)
        (Path(os.environ["XDG_STATE_HOME"]) / "ok").write_text("ok")
    """
)


FLAGGED = {
    "test_writes_to_the_real_state",
    "test_swallowed_write_still_fails",
    "test_symlink_into_the_real_state_fails",
    "test_dir_fd_removal_in_the_real_state_fails",
    "test_hard_link_from_the_real_state_fails",
}


@pytest.mark.parametrize("basetemp_under_real_state", [False, True])
def test_real_state_writes_fail_and_redirected_ones_pass(tmp_path, src_env, basetemp_under_real_state):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "kept").write_text("kept")
    (home / ".local" / "state").mkdir(parents=True)
    (tmp_path / "test_inner.py").write_text(INNER)
    env = {**src_env, "HOME": str(home)}
    # 一時ディレクトリを本物の置き場の下に置いても、その外の本物の置き場は守られたまま
    basetemp = ["--basetemp", str(home / ".local" / "state" / "pytest")] if basetemp_under_real_state else []
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "ndf.testing.isolation", "-q", "-rfE", "-p", "no:cacheprovider", *basetemp, "test_inner.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    flagged = {line.split("::")[1].split()[0] for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))}
    assert flagged == FLAGGED, proc.stdout
    assert not (home / ".local" / "state" / "ndf").exists()
    assert not (home / ".local" / "state" / "ledger.jsonl").exists()
    assert not (home / ".claude" / "x").exists()
    assert (home / ".claude" / "kept").read_text() == "kept"


def test_state_variables_point_at_the_temporary_directory(forbid_real_state):
    for name, sub in isolation.REDIRECTED_ENV.items():
        assert os.environ[name] == str(forbid_real_state / sub)
    assert "CLAUDE_PLUGIN_ROOT" not in os.environ


# --- 現状固定テスト: _Guard._hook がどのイベントのどの引数を _check へ渡すか（I-002） ---


def _spied_guard(roots: tuple[str, ...] = ("/nonexistent-root",)) -> tuple[isolation._Guard, list[tuple[object, object]]]:
    """監査フックを入れずに _hook を直接呼ぶ守り。_check へ渡った (path, dir_fd) を記録する。"""
    guard = isolation._Guard()
    guard.roots = roots
    calls: list[tuple[object, object]] = []
    guard._check = lambda path, dir_fd=None: calls.append((path, dir_fd))  # type: ignore[method-assign]
    return guard, calls


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("p", "w", 0), [("p", None)]),
        (("p", "a", 0), [("p", None)]),
        (("p", "x", 0), [("p", None)]),
        (("p", "r+", 0), [("p", None)]),
        (("p", "rb", 0), []),
        (("p", "r", 0), []),
        (("p", None, os.O_WRONLY), [("p", None)]),
        (("p", None, os.O_RDWR), [("p", None)]),
        (("p", None, os.O_CREAT), [("p", None)]),
        (("p", None, os.O_APPEND), [("p", None)]),
        (("p", None, os.O_TRUNC), [("p", None)]),
        (("p", None, os.O_RDONLY), []),
        # mode が読みだけでも、flags に書き込みがあれば見る
        (("p", "r", os.O_WRONLY), [("p", None)]),
        # mode が文字列でなく flags が整数でなければ見ない
        (("p", 1, "w"), []),
        # 引数が足りなければ None で埋める
        (("p", "w"), [("p", None)]),
        (("p",), []),
        ((), []),
    ],
)
def test_hook_open_checks_only_writing_modes_and_flags(args, expected):
    guard, calls = _spied_guard()
    guard._hook("open", args)
    assert calls == expected


@pytest.mark.parametrize(
    ("event", "args", "expected"),
    [
        ("os.mkdir", ("d", 0o777, 5), [("d", 5)]),
        ("os.mkdir", ("d", 0o777), [("d", None)]),
        ("os.remove", ("f", 7), [("f", 7)]),
        ("os.remove", ("f",), [("f", None)]),
        ("os.rmdir", ("d", None), [("d", None)]),
        ("os.rename", ("a", "b", 3, 4), [("a", 3), ("b", 4)]),
        ("os.rename", ("a", "b"), [("a", None), ("b", None)]),
        ("os.rename", ("a",), [("a", None)]),
        ("os.link", ("src", "dst", 8, 9), [("src", 8), ("dst", 9)]),
        ("os.truncate", ("f", 0), [("f", None)]),
        ("os.chmod", ("f", 0o600, 6), [("f", 6)]),
        ("shutil.rmtree", ("d", 2), [("d", 2)]),
        ("os.mkdir", (), []),
        ("os.replace", ("a", "b", 3, 4), []),
        ("os.unlink", ("f", 7), []),
        ("subprocess.Popen", ("ls", ["ls"], None, None), []),
    ],
)
def test_hook_write_events_pass_path_and_dir_fd_positions(event, args, expected):
    guard, calls = _spied_guard()
    guard._hook(event, args)
    assert calls == expected


def test_hook_does_nothing_without_roots():
    guard, calls = _spied_guard(roots=())
    guard._hook("open", ("p", "w", 0))
    guard._hook("os.rename", ("a", "b", None, None))
    assert calls == []


def test_hook_does_nothing_while_busy_in_this_thread():
    guard, calls = _spied_guard()
    guard.local.busy = True
    guard._hook("open", ("p", "w", 0))
    guard._hook("os.remove", ("f", None))
    assert calls == []
    guard.local.busy = False
    guard._hook("os.remove", ("f", None))
    assert calls == [("f", None)]


def test_hook_accepts_list_args():
    guard, calls = _spied_guard()
    guard._hook("open", ["p", "w", 0])
    guard._hook("os.rename", ["a", "b", 1, 2])
    assert calls == [("p", None), ("a", 1), ("b", 2)]


def test_hook_rename_stops_at_the_first_violation(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = isolation._Guard()
    guard.roots = (os.path.realpath(real),)
    with pytest.raises(isolation.RealStateWriteError) as raised:
        guard._hook("os.rename", (str(real / "a"), str(real / "b"), None, None))
    assert guard.violations == [os.path.realpath(real / "a")]
    assert str(raised.value) == f"テストが本物の状態の置き場へ書こうとした: {os.path.realpath(real / 'a')}"


def test_hook_rename_into_the_root_from_outside_flags_the_destination(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = isolation._Guard()
    guard.roots = (os.path.realpath(real),)
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.rename", (str(tmp_path / "outside"), str(real / "b"), None, None))
    assert guard.violations == [os.path.realpath(real / "b")]


def test_hook_resolves_relative_paths_through_dir_fd(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = isolation._Guard()
    guard.roots = (os.path.realpath(real),)
    fd = os.open(real, os.O_RDONLY)
    try:
        with pytest.raises(isolation.RealStateWriteError):
            guard._hook("os.remove", ("kept", fd))
    finally:
        os.close(fd)
    assert guard.violations == [os.path.realpath(real / "kept")]


def test_hook_lets_writes_under_allowed_and_outside_roots_through(tmp_path):
    real = tmp_path / "real"
    allowed = real / "pytest"
    allowed.mkdir(parents=True)
    guard = isolation._Guard()
    guard.roots = (os.path.realpath(real),)
    guard.allowed = (os.path.realpath(allowed),)
    guard._hook("open", (str(allowed / "f"), "w", 0))
    guard._hook("open", (str(tmp_path / "elsewhere"), "w", 0))
    guard._hook("open", (str(real / "f"), "r", 0))
    guard._hook("open", (str(real / "f").encode(), None, os.O_RDONLY))
    assert guard.violations == []
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("open", (str(real / "f").encode(), "wb", 0))
    assert guard.violations == [os.path.realpath(real / "f")]


def test_hook_ignores_non_path_targets():
    guard = isolation._Guard()
    guard.roots = ("/",)
    guard._hook("open", (3, "w", 0))
    guard._hook("os.remove", (None, None))
    assert guard.violations == []
