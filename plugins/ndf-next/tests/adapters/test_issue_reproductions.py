"""AD1+ が閉じる証拠（改善の証拠）を持つ Issue の場面を、ndf-next で再現するテスト。

1 つの関数が 1 つの Issue の場面を、今の NDF で起きた入力の形のまま通す。ネットワークへ出ない。
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ndf import ci_cli
from ndf.adapters import github, publish
from ndf.contracts import causes

from ._ci_helpers import run
from ._github_helpers import hub, offline_client, pull_json

REPO = "devbasex/ai-plugins"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_issue_2041_merge_with_the_short_sha_git_prints_for_the_same_head_merges(tmp_path: Path) -> None:
    # #2041: `--expect-head "$(git rev-parse --short HEAD)"` が同じ先端なのに head_moved で止まった
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    full, short = _git(tmp_path, "rev-parse", "HEAD"), _git(tmp_path, "rev-parse", "--short", "HEAD")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PUT":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"merged": True})
        if path.endswith("/rules/branches/develop"):
            return httpx.Response(200, json=[])
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": True})
        return httpx.Response(200, json=pull_json(full))

    out = hub(REPO, handler).merge(7, expect_head=short)
    assert len(short) < len(full)
    assert (out.merged, out.kind) == (True, None)
    assert sent[0]["sha"] == full


def _serve_checks(monkeypatch: pytest.MonkeyPatch, runs: list[dict], calls: list[str]) -> None:
    head = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        return httpx.Response(200, json=pull_json(head))

    monkeypatch.setattr(github, "client", lambda **_: offline_client(handler))


def test_issue_2317_watch_stops_naming_a_required_check_the_pr_removed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # #2317: PR が `runtime-smoke (kiro)` をワークフローから外したのに、待つ側がまだ必須として渡した。
    # head のチェックの一覧が出そろった後も現れなければ、上限まで黙って待たずにその名前を挙げて止まる
    calls: list[str] = []
    runs = [run("pytest", rid=1), run("doc-lint", rid=2), run("runtime-smoke (claude)", rid=3)]
    _serve_checks(monkeypatch, runs, calls)
    args = ["watch", "--pr", "7", "--repo", REPO, "--budget", "2766", "--test", "pytest"]
    for name in ("pytest", "doc-lint", "runtime-smoke (claude)", "runtime-smoke (kiro)"):
        args += ["--required", name]
    code = ci_cli.main(args)
    out = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("precondition")
    assert (out["status"], out["cause"]["kind"], out["metrics"]["rounds"]) == ("stopped", "precondition", 1)
    assert {"kind": "check", "name": "runtime-smoke (kiro)", "result": "missing", "test": False} in out["items"]
    assert sum(p.endswith("/check-runs") for p in calls) == 1
    # 待つ間は、待っているチェックの名前と経過を出す（遅れの見張りが「出力なし」で打ち切らない）
    ci_cli._progress(ci_cli.Reading([], [], ["pytest"], [], [], ["runtime-smoke (kiro)"], False), 61.0)
    assert "CI を待っている（61 秒）: pytest, runtime-smoke (kiro)" in capsys.readouterr().err


def test_issue_3077_cross_repo_origin_points_at_the_issue_of_the_source_repo() -> None:
    # #3077: `--repo example-owner/other-repo --origin "issue #12"` の由来の行が番号だけになり、起票先の #12 を指した
    source, target = "example-owner/example-repo", "example-owner/other-repo"
    written: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            aliases = re.findall(r"(a\d+): repository", json.loads(request.content)["query"])
            return httpx.Response(200, json={"data": {a: {"visibility": "PUBLIC"} for a in aliases}})
        written.append(json.loads(request.content))
        return httpx.Response(201, json={"number": 1, "html_url": f"https://github.com/{target}/issues/1"})

    writer = publish.Publisher(hub(target, handler), frozenset())
    line = writer.origin("issue #12", source)
    writer.create_issue("試し", f"## 由来\n\n{line}\n", ["enhancement"])
    assert line == f"issue {source}#12"
    body_lines = written[0]["body"].splitlines()
    assert f"issue {source}#12" in body_lines and "issue #12" not in body_lines


def test_issue_3153_failed_shard_with_a_pending_aggregate_after_merge_is_a_test_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # #3153: マージが `Python tests / pytest (0/2)` の落ちで落ちた 2 秒後、まとめの `Python tests / pytest` はまだ
    # pending だった。CI 以外の理由（not_ci）と読まず、落ちたテストとして CI の修正へ進める
    calls: list[str] = []
    runs = [
        run("Python tests / pytest (0/2)", conclusion="failure", rid=1),
        run("Python tests / pytest (1/2)", conclusion="failure", rid=2),
        run("Python tests / pytest", status="in_progress", rid=3),
    ]
    _serve_checks(monkeypatch, runs, calls)
    args = ["watch", "--pr", "7", "--repo", REPO, "--required", "pytest", "--test", "pytest", "--budget", "60"]
    code = ci_cli.main([*args, "--after-merge", "--merge-failed", "Python tests / pytest (0/2)"])
    out = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("test_failed")
    assert (out["cause"]["kind"], out["metrics"]["rounds"]) == ("test_failed", 1)
    assert [i["name"] for i in out["items"] if i["test"]] == ["Python tests / pytest (0/2)", "Python tests / pytest (1/2)"]
