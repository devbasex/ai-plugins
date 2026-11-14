from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ndf.testing import isolation

C = isolation.CONTENT
N = isolation.NAME
# 前提（forbid_real_state を持つ `_real_state`）の置き場。`-p _real_state` で読ませる子の pytest へ渡す
HERE = str(Path(__file__).resolve().parent)


def _child_env(src_env: dict[str, str], **extra: str) -> dict[str, str]:
    """子の pytest の環境。src に加えて前提の置き場を PYTHONPATH へ足す。"""
    return {**src_env, **extra, "PYTHONPATH": os.pathsep.join([HERE, src_env["PYTHONPATH"]])}


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


def _run_inner_pytest(cwd: Path, env: dict[str, str], basetemp: list[str]) -> subprocess.CompletedProcess[str]:
    """隔離のプラグインを読ませた別プロセスの pytest で `cwd` の test_inner.py を流し、出力を捕まえる。"""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "_real_state", "-q", "-rfE", "-p", "no:cacheprovider", *basetemp, "test_inner.py"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _inner_dir(tmp_path: Path, conftest: str, test_source: str) -> Path:
    """内側の pytest を流すディレクトリ（conftest.py と test_inner.py を置く）。"""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "conftest.py").write_text(conftest)
    (inner / "test_inner.py").write_text(test_source)
    return inner


@pytest.mark.parametrize("basetemp_under_real_state", [False, True])
def test_real_state_writes_fail_and_redirected_ones_pass(tmp_path, src_env, basetemp_under_real_state):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "kept").write_text("kept")
    (home / ".local" / "state").mkdir(parents=True)
    (tmp_path / "test_inner.py").write_text(INNER)
    env = _child_env(src_env, HOME=str(home))
    # 一時ディレクトリを本物の置き場の下に置いても、その外の本物の置き場は守られたまま
    basetemp = ["--basetemp", str(home / ".local" / "state" / "pytest")] if basetemp_under_real_state else []
    proc = _run_inner_pytest(tmp_path, env, basetemp)
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


def _spied_guard(roots: tuple[str, ...] = ("/nonexistent-root",)) -> tuple[isolation._Guard, list[tuple[object, object, str]]]:
    """監査フックを入れずに _hook を直接呼ぶ守り。_check へ渡った (path, dir_fd, 判定の種類) を記録する。"""
    guard = isolation._Guard()
    guard.roots = roots
    calls: list[tuple[object, object, str]] = []
    guard._check = lambda path, dir_fd=None, kind=isolation.CONTENT: calls.append((path, dir_fd, kind))  # type: ignore[method-assign]
    return guard, calls


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("p", "w", 0), [("p", None, C)]),
        (("p", "a", 0), [("p", None, C)]),
        (("p", "x", 0), [("p", None, C)]),
        (("p", "r+", 0), [("p", None, C)]),
        (("p", "rb", 0), []),
        (("p", "r", 0), []),
        (("p", None, os.O_WRONLY), [("p", None, C)]),
        (("p", None, os.O_RDWR), [("p", None, C)]),
        (("p", None, os.O_CREAT), [("p", None, C)]),
        (("p", None, os.O_APPEND), [("p", None, C)]),
        (("p", None, os.O_TRUNC), [("p", None, C)]),
        (("p", None, os.O_RDONLY), []),
        # mode が読みだけでも、flags に書き込みがあれば見る
        (("p", "r", os.O_WRONLY), [("p", None, C)]),
        # mode が文字列でなく flags が整数でなければ見ない
        (("p", 1, "w"), []),
        # 引数が足りなければ None で埋める
        (("p", "w"), [("p", None, C)]),
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
        ("os.mkdir", ("d", 0o777, 5), [("d", 5, N)]),
        ("os.mkdir", ("d", 0o777), [("d", None, N)]),
        ("os.remove", ("f", 7), [("f", 7, N)]),
        ("os.remove", ("f",), [("f", None, N)]),
        ("os.rmdir", ("d", None), [("d", None, N)]),
        ("os.rename", ("a", "b", 3, 4), [("a", 3, N), ("b", 4, N)]),
        ("os.rename", ("a", "b"), [("a", None, N), ("b", None, N)]),
        ("os.rename", ("a",), [("a", None, N)]),
        ("os.link", ("src", "dst", 8, 9), [("src", 8, C), ("dst", 9, N)]),
        ("os.truncate", ("f", 0), [("f", None, C)]),
        ("os.symlink", ("target", "link", 5), [("link", 5, N)]),
        ("os.symlink", ("target", "link"), [("link", None, N)]),
        ("os.chmod", ("f", 0o600, 6), [("f", 6, C)]),
        ("shutil.rmtree", ("d", 2), [("d", 2, N)]),
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
    assert calls == [("f", None, N)]


def test_hook_accepts_list_args():
    guard, calls = _spied_guard()
    guard._hook("open", ["p", "w", 0])
    guard._hook("os.rename", ["a", "b", 1, 2])
    assert calls == [("p", None, C), ("a", 1, N), ("b", 2, N)]


def test_hook_rename_stops_at_the_first_violation(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = _guard_over(real)
    with pytest.raises(isolation.RealStateWriteError) as raised:
        guard._hook("os.rename", (str(real / "a"), str(real / "b"), None, None))
    assert guard.violations == [os.path.realpath(real / "a")]
    assert str(raised.value) == f"テストが本物の状態の置き場へ書こうとした: {os.path.realpath(real / 'a')}"


def test_hook_rename_into_the_root_from_outside_flags_the_destination(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = _guard_over(real)
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.rename", (str(tmp_path / "outside"), str(real / "b"), None, None))
    assert guard.violations == [os.path.realpath(real / "b")]


def test_hook_resolves_relative_paths_through_dir_fd(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = _guard_over(real)
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
    guard = _guard_over(real)
    guard.allowed = (os.path.realpath(allowed),)
    guard._hook("open", (str(allowed / "f"), "w", 0))
    guard._hook("open", (str(tmp_path / "elsewhere"), "w", 0))
    guard._hook("open", (str(real / "f"), "r", 0))
    guard._hook("open", (str(real / "f").encode(), None, os.O_RDONLY))
    assert guard.violations == []
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("open", (str(real / "f").encode(), "wb", 0))
    assert guard.violations == [os.path.realpath(real / "f")]


def test_hook_resolves_links_before_parent_steps(tmp_path):
    # 一時領域のリンクが守る根の下を指すとき、`link/../f` はリンクの参照先の親（守る根）の f を書く
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "link").symlink_to(real / "sub")
    guard = _guard_over(real)
    guard.allowed = (os.path.realpath(allowed),)
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("open", (str(allowed / "link") + "/../ledger.jsonl", "w", 0))
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.remove", (str(allowed / "link") + "/../ledger.jsonl", None))
    assert guard.violations == [os.path.realpath(real) + "/ledger.jsonl"] * 2


def test_hook_ignores_non_path_targets():
    r, w = os.pipe()
    os.close(r)
    guard = isolation._Guard()
    guard.roots = ("/",)
    try:
        guard._hook("open", (w, "w", 0))  # パスを持たない記述子（パイプ）
    finally:
        os.close(w)
    guard._hook("open", (w, "w", 0))  # 閉じた記述子
    guard._hook("open", (True, "w", 0))
    guard._hook("os.remove", (None, None))
    assert guard.violations == []


def _guard_over(real) -> isolation._Guard:
    guard = isolation._Guard()
    guard.roots = (os.path.realpath(real),)
    return guard


def test_check_rejects_bytes_paths_under_a_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    guard = _guard_over(real)
    with pytest.raises(isolation.RealStateWriteError):
        guard._check(os.fsencode(real / "ledger"), None, N)
    assert guard.violations == [os.path.realpath(real / "ledger")]


def test_hook_resolves_file_descriptors_to_their_paths(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "ledger").write_text("kept")
    guard = _guard_over(real)
    fd = os.open(real / "ledger", os.O_RDONLY)
    try:
        with pytest.raises(isolation.RealStateWriteError):
            guard._hook("os.truncate", (fd, 0))
    finally:
        os.close(fd)
    assert guard.violations == [os.path.realpath(real / "ledger")]


def test_hook_rejects_moving_a_directory_that_holds_a_root(tmp_path):
    real = tmp_path / "home" / ".local" / "state"
    real.mkdir(parents=True)
    guard = _guard_over(real)
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.rename", (str(tmp_path / "home" / ".local"), str(tmp_path / "moved"), None, None))
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("shutil.rmtree", (str(tmp_path / "home"), None))
    # 中身を書き換える操作は、守る根の祖先でも書き換えるのはその場所だけなので通す
    guard._hook("os.chmod", (str(tmp_path / "home"), 0o755, None))
    assert len(guard.violations) == 2


def test_hook_checks_where_links_are_made_and_removed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    guard = _guard_over(real)
    # 守る根の下へのリンクの作成と、守る根の下に置かれたリンクの削除は拒む（参照先が外でも）
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.symlink", (str(outside), str(real / "link"), None))
    (real / "app").symlink_to(outside)
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.remove", (str(real / "app"), None))
    with pytest.raises(isolation.RealStateWriteError):
        guard._hook("os.rename", (str(real / "app"), str(outside / "app"), None, None))
    # 外に置かれたリンクは、参照先が守る根の下でも、リンク自体の削除は通す
    (outside / "to-real").symlink_to(real)
    guard._hook("os.remove", (str(outside / "to-real"), None))
    guard._hook("os.symlink", (str(real), str(outside / "another"), None))
    assert guard.violations == [os.path.realpath(real) + "/link", os.path.realpath(real) + "/app", os.path.realpath(real) + "/app"]


def test_real_roots_include_the_original_state_variables(tmp_path):
    home = tmp_path / "home"
    roots = isolation.real_roots(
        home,
        {"XDG_STATE_HOME": str(tmp_path / "state"), "XDG_CONFIG_HOME": "relative", isolation.slots.SLOTS_DIR_ENV: str(tmp_path / "slots")},
    )
    assert os.path.realpath(tmp_path / "state") in roots
    assert os.path.realpath(tmp_path / "slots") in roots
    assert not any(r.endswith("relative") for r in roots)
    assert os.path.realpath(home / ".claude") in roots


EARLIER_CONFTEST = textwrap.dedent(
    """
    import pytest

    @pytest.fixture(autouse=True)
    def _earlier(monkeypatch, tmp_path):
        # forbid_real_state より先に動き、状態の置き場の環境変数を書き換える（tests/conftest.py の _isolated_env と同じ形）
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "earlier-state"))
    """
)

ORIGINAL_STATE_INNER = textwrap.dedent(
    """
    import os
    from pathlib import Path
    import pytest

    @pytest.fixture(autouse=True)
    def _iso(forbid_real_state):
        return forbid_real_state

    def test_write_to_the_original_state_home():
        Path(os.environ["ORIGINAL_STATE"], "ledger.jsonl").write_text("x")
    """
)


def test_original_state_variable_stays_protected_after_an_earlier_fixture_rewrites_it(tmp_path, src_env):
    original = tmp_path / "original-state"
    original.mkdir()
    inner = _inner_dir(tmp_path, EARLIER_CONFTEST, ORIGINAL_STATE_INNER)
    env = _child_env(src_env, HOME=str(tmp_path / "home"), XDG_STATE_HOME=str(original), ORIGINAL_STATE=str(original))
    basetemp = ["--basetemp", str(tmp_path / "inner-tmp")]
    proc = _run_inner_pytest(inner, env, basetemp)
    assert proc.returncode != 0, proc.stdout
    assert "本物の状態の置き場へ書こうとした" in proc.stdout
    assert not (original / "ledger.jsonl").exists()


EARLIER_HOME_CONFTEST = textwrap.dedent(
    """
    import pytest

    @pytest.fixture(autouse=True)
    def _earlier(monkeypatch, tmp_path):
        # forbid_real_state より先に動き、HOME を一時領域へ向ける
        monkeypatch.setenv("HOME", str(tmp_path / "earlier-home"))
    """
)

ORIGINAL_HOME_INNER = textwrap.dedent(
    """
    import os
    from pathlib import Path
    import pytest

    @pytest.fixture(autouse=True)
    def _iso(forbid_real_state):
        return forbid_real_state

    def test_write_under_the_original_home():
        Path(os.environ["ORIGINAL_HOME"], ".claude", "ledger.jsonl").write_text("x")
    """
)


def test_original_home_stays_protected_after_an_earlier_fixture_rewrites_it(tmp_path, src_env):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    inner = _inner_dir(tmp_path, EARLIER_HOME_CONFTEST, ORIGINAL_HOME_INNER)
    env = _child_env(src_env, HOME=str(home), ORIGINAL_HOME=str(home))
    basetemp = ["--basetemp", str(tmp_path / "inner-tmp")]
    proc = _run_inner_pytest(inner, env, basetemp)
    assert proc.returncode != 0, proc.stdout
    assert "本物の状態の置き場へ書こうとした" in proc.stdout
    assert not (home / ".claude" / "ledger.jsonl").exists()
