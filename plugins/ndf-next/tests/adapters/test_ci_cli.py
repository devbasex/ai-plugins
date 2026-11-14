"""副命令 `ndf ci watch`（`ndf.ci_cli`）の引数・GitHub への接続・標準出力の契約。"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from githubkit import GitHub as Client

from ndf import ci_cli
from ndf.adapters import github
from ndf.contracts import causes

from ._ci_helpers import LOG, run

HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"
REPO = "devbasex/ai-plugins"


PULL = {
    "number": 7,
    "state": "open",
    "merged": False,
    "body": "",
    "mergeable_state": "clean",
    "head": {"sha": HEAD, "ref": "x"},
    "base": {"ref": "develop"},
    "user": {"login": "octocat"},
    "author_association": "OWNER",
}


def _serve(monkeypatch: pytest.MonkeyPatch, runs: list[dict], log: str = "", log_status: int = 200, calls: list[str] | None = None) -> None:
    # パスの末尾 → 応答を作る関数
    responses = {
        "/pulls/7": lambda: httpx.Response(200, json=PULL),
        "/check-runs": lambda: httpx.Response(200, json={"total_count": len(runs), "check_runs": runs}),
        "/logs": lambda: httpx.Response(log_status, text=log, headers={"content-type": "text/plain"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append(path)
        respond = next((r for tail, r in responses.items() if path.endswith(tail)), None)
        return respond() if respond else httpx.Response(404, json={"message": "not found"})

    api = Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)
    monkeypatch.setattr(github, "client", lambda **_: api)


ARGS = ["watch", "--pr", "7", "--repo", REPO, "--required", "pytest", "--required", "doc-lint", "--test", "pytest", "--budget", "60"]


def test_cli_watch_test_failure_lists_test_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", status="in_progress", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG)
    code = ci_cli.main(ARGS)
    out = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("test_failed")
    assert (out["status"], out["cause"]["kind"], out["cause"]["instance"]) == ("stopped", "test_failed", HEAD)
    assert out["metrics"]["test_files"] == ["tests/test_other.py", "tests/test_stage_check.py"]
    assert out["items"][0] == {"kind": "check", "name": "pytest (0/2)", "result": "failure", "job": 41, "test": True}


def test_cli_watch_keeps_test_failure_when_logs_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # ログを読めない（権限・Actions 以外のチェック）ときも、test_failed の分類と落ちたチェックの一覧は残す
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log="forbidden", log_status=403)
    assert ci_cli.main(ARGS) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert out["cause"]["kind"] == "test_failed"
    assert (out["metrics"]["failed_tests"], out["metrics"]["test_files"]) == ([], [])
    assert "403" in out["metrics"]["log_error"]
    assert out["items"][0]["name"] == "pytest (0/2)"


def test_cli_watch_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main(ARGS) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"


def test_cli_watch_after_merge_with_failed_shard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "test_failed"


def test_cli_watch_after_merge_with_failed_test_does_not_wait_for_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # マージの段階がテストの落ちを読んでいれば、まとめのチェックが終わっていなくても読み直さずに返す
    calls: list[str] = []
    _serve(monkeypatch, [run("pytest", status="in_progress", rid=1), run("doc-lint", rid=2)], calls=calls)
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert (out["cause"]["kind"], out["metrics"]["rounds"]) == ("test_failed", 1)
    assert sum(p.endswith("/check-runs") for p in calls) == 1


def test_cli_watch_after_merge_without_failures_is_not_ci(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge"]) == causes.exit_code("not_ci")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "not_ci"


def test_cli_watch_rate_limit_is_infra_wait(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    def limited(**_) -> Client:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
        handler = lambda request: httpx.Response(403, json={"message": "rate"}, headers=headers)  # noqa: E731
        return Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)

    monkeypatch.setattr(github, "client", limited)
    assert ci_cli.main(ARGS) == causes.exit_code("infra_wait")
    out = last_result(capsys.readouterr().out)
    assert out["cause"]["kind"] == "infra_wait" and out["cause"]["retry_after"] > 0


def test_cli_watch_lists_missing_and_stale_checks_with_test_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # 現れない必須のチェック（missing）と、結論の無いまま終わったチェック（stale）を、テストのチェックかどうかとともに items へ並べる
    done_at = "2026-11-12T13:33:00Z"
    runs = [
        run("CI / pytest (0/2)", status="in_progress", completed_at=done_at, rid=1),
        run("W / lint", status="in_progress", completed_at=done_at, rid=2),
    ]
    _serve(monkeypatch, runs)
    argv = ["watch", "--pr", "7", "--repo", REPO, "--test", "pytest", "--budget", "60"]
    argv += [a for name in ("pytest", "lint", "pytest (1/2)", "doc-lint") for a in ("--required", name)]
    code = ci_cli.main(argv)
    out = last_result(capsys.readouterr().out)
    assert (code, out["status"], out["cause"]["kind"]) == (causes.exit_code("precondition"), "stopped", "precondition")
    assert out["items"] == [
        {"kind": "check", "name": "doc-lint", "result": "missing", "test": False},
        {"kind": "check", "name": "pytest (1/2)", "result": "missing", "test": True},
        {"kind": "check", "name": "CI / pytest (0/2)", "result": "stale", "test": True},
        {"kind": "check", "name": "W / lint", "result": "stale", "test": False},
    ]
