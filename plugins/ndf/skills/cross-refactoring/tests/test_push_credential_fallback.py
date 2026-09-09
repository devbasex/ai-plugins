"""credential helper が応答しない環境での push の退避（#524）。

**退避の本体は空の値を先に置くことである。** `credential.helper` は複数の値を持てる
設定で、`git` は宣言された順に問い合わせる。空の値だけが一覧を空へ戻す。
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from crossref_helpers import make_state, read_state


LIB = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts" / "lib" / "git-credential.sh"
)


# ---------- 共通層 ----------

def test_the_shared_library_exists():
    assert LIB.is_file(), f"共通層が無い: {LIB}"


def test_the_fallback_arguments_reset_the_helper_first():
    """空の値を先に置かないと、応答しない helper が先に当たり続ける。"""
    out = subprocess.run(
        ["bash", "-c", f'. "{LIB}"; ndf_git_credential_fallback_args'],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    args = [a for a in out if a]
    assert args == [
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
    ]


def test_the_reset_actually_changes_which_helper_answers(tmp_path):
    """`git` の実挙動で確かめる。順序が逆だと後の helper へ届かない。"""
    first = "!f(){ [ \"$1\" = get ] && echo username=FIRST && echo password=x; }; f"
    second = "!g(){ [ \"$1\" = get ] && echo username=SECOND && echo password=y; }; g"
    request = "protocol=https\nhost=example.invalid\n\n"

    without_reset = subprocess.run(
        ["git", "-c", f"credential.helper={first}",
         "-c", f"credential.helper={second}", "credential", "fill"],
        input=request, capture_output=True, text=True, cwd=tmp_path,
    ).stdout
    assert "username=FIRST" in without_reset

    with_reset = subprocess.run(
        ["git", "-c", f"credential.helper={first}",
         "-c", "credential.helper=",
         "-c", f"credential.helper={second}", "credential", "fill"],
        input=request, capture_output=True, text=True, cwd=tmp_path,
    ).stdout
    assert "username=SECOND" in with_reset


# ---------- 進行側の push ----------

def test_missing_shared_library_returns_no_fallback_args(gitfacts, monkeypatch, tmp_path):
    """現状固定: 共通層が無い環境では例外を出さず退避を省く。"""
    monkeypatch.setattr(gitfacts, "_CREDENTIAL_LIB", tmp_path / "missing.sh")

    assert gitfacts.credential_fallback_args() == []


@pytest.fixture
def failing_first_push(patch_lib, monkeypatch, gitfacts):
    """1 度目の `git push` だけを失敗させ、呼ばれた引数を記録する。"""
    calls: list[list[str]] = []
    attempts = {"push": 0}

    def fake_sh(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        if "push" in cmd:
            attempts["push"] += 1
            if attempts["push"] == 1:
                raise RuntimeError("fatal: Authentication failed")
        return ""

    patch_lib("sh", fake_sh)
    patch_lib("_sync_generated", lambda state: None)
    patch_lib("publish_plan_comment", lambda state: None)
    monkeypatch.setattr(gitfacts, "gh_available", lambda: True, raising=False)
    return calls


def _state(tmp_path):
    state_path = make_state(tmp_path)
    return read_state(state_path)


def test_the_push_is_retried_with_the_fallback(
    gitfacts, failing_first_push, tmp_path
):
    gitfacts.push_head(_state(tmp_path))

    pushes = [c for c in failing_first_push if "push" in c]
    assert len(pushes) == 2, "退避して 1 度だけ再試行する"
    assert "-c" in pushes[1]
    idx = pushes[1].index("-c")
    assert pushes[1][idx:idx + 4] == [
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
    ]


def test_the_first_attempt_uses_the_default_route(
    gitfacts, failing_first_push, tmp_path
):
    """helper が正しく動く環境の振る舞いを変えない。"""
    gitfacts.push_head(_state(tmp_path))

    pushes = [c for c in failing_first_push if "push" in c]
    assert "credential.helper=" not in " ".join(pushes[0])


def test_a_working_helper_is_not_retried(gitfacts, patch_lib, tmp_path):
    calls: list[list[str]] = []
    patch_lib("sh", lambda cmd, cwd=None, check=True: calls.append(list(cmd)) or "")
    patch_lib("_sync_generated", lambda state: None)
    patch_lib("publish_plan_comment", lambda state: None)

    gitfacts.push_head(_state(tmp_path))

    assert len([c for c in calls if "push" in c]) == 1


def test_the_retry_happens_only_once(gitfacts, patch_lib, monkeypatch, tmp_path):
    """認証以外の理由で失敗したとき、同じ失敗を繰り返さない。"""
    calls: list[list[str]] = []

    def always_fails(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        if "push" in cmd:
            raise RuntimeError("fatal: Authentication failed")
        return ""

    patch_lib("sh", always_fails)
    patch_lib("_sync_generated", lambda state: None)
    patch_lib("publish_plan_comment", lambda state: None)
    monkeypatch.setattr(gitfacts, "gh_available", lambda: True, raising=False)

    with pytest.raises(RuntimeError):
        gitfacts.push_head(_state(tmp_path))

    assert len([c for c in calls if "push" in c]) == 2


def test_without_gh_the_failure_is_returned_as_is(
    gitfacts, patch_lib, monkeypatch, tmp_path
):
    """`gh` が無ければ退避しても通らない。失敗として扱う。"""
    calls: list[list[str]] = []

    def fails(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        if "push" in cmd:
            raise RuntimeError("fatal: Authentication failed")
        return ""

    patch_lib("sh", fails)
    patch_lib("_sync_generated", lambda state: None)
    patch_lib("publish_plan_comment", lambda state: None)
    monkeypatch.setattr(gitfacts, "gh_available", lambda: False, raising=False)

    with pytest.raises(RuntimeError):
        gitfacts.push_head(_state(tmp_path))

    assert len([c for c in calls if "push" in c]) == 1
