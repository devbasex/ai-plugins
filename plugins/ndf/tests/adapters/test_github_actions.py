"""GitHub のアダプター（`ndf.adapters.github`）の Actions の読み取り（ジョブ・実行・ログ・チェック・成果物）。
githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from ndf.adapters import github

from ._github_helpers import RouteServer as Server
from ._github_helpers import hub

REPO = "devbasex/ai-plugins"
HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"


def _hub(handler: Callable[[httpx.Request], httpx.Response]) -> github.GitHub:
    return hub(REPO, handler)


def test_job_log_returns_text() -> None:
    server = Server(
        {("GET", f"/repos/{REPO}/actions/jobs/5/logs"): [httpx.Response(200, text="line\n", headers={"content-type": "text/plain"})]}
    )
    assert _hub(server).job_log(5) == "line\n"


@pytest.mark.parametrize(
    ("read", "path"),
    [
        (lambda h: h.job(5), f"/repos/{REPO}/actions/jobs/5"),
        (lambda h: h.workflow_run(9), f"/repos/{REPO}/actions/runs/9"),
    ],
)
def test_job_and_workflow_run_return_the_body_and_raise_on_404(read: Callable[[github.GitHub], dict], path: str) -> None:
    assert read(_hub(Server({("GET", path): [httpx.Response(200, json={"id": 1, "run_id": 9})]}))) == {"id": 1, "run_id": 9}
    with pytest.raises(github.GitHubError):
        read(_hub(Server({("GET", path): [httpx.Response(404, json={"message": "Not Found"})]})))


def test_run_artifacts_reads_pages_until_total_and_stops_over_the_limit() -> None:
    path = f"/repos/{REPO}/actions/runs/9/artifacts"
    pages = [httpx.Response(200, json={"total_count": 150, "artifacts": [{"id": i} for i in range(n)]}) for n in (100, 50)]
    server = Server({("GET", path): pages})
    assert len(_hub(server).run_artifacts(9)) == 150
    assert [r.url.params["page"] for r in server.seen] == ["1", "2"]
    assert _hub(Server({("GET", path): [httpx.Response(200, json={"total_count": 0, "artifacts": []})]})).run_artifacts(9) == []
    full = httpx.Response(200, json={"total_count": 100000, "artifacts": [{"id": 1}] * 100})
    with pytest.raises(github.GitHubError, match="github.list-pages"):
        _hub(Server({("GET", path): [full]})).run_artifacts(9)


def test_run_artifacts_current_behavior_keeps_order_and_sends_per_page() -> None:
    # 現状固定（I-001）: 各ページを順に連結し、要求は per_page=100 と 1 始まりの page を送る
    path = f"/repos/{REPO}/actions/runs/9/artifacts"
    pages = [
        httpx.Response(200, json={"total_count": 150, "artifacts": [{"id": i} for i in range(100)]}),
        httpx.Response(200, json={"total_count": 150, "artifacts": [{"id": i} for i in range(100, 150)]}),
    ]
    server = Server({("GET", path): pages})
    rows = _hub(server).run_artifacts(9)
    assert [r["id"] for r in rows] == list(range(150))
    assert [(r.url.params["per_page"], r.url.params["page"]) for r in server.seen] == [("100", "1"), ("100", "2")]


def test_run_artifacts_current_behavior_stops_on_an_empty_page_before_total() -> None:
    # 現状固定（I-001）: total_count に届かなくても、空のページで読むのをやめて読んだ分を返す
    path = f"/repos/{REPO}/actions/runs/9/artifacts"
    pages = [
        httpx.Response(200, json={"total_count": 300, "artifacts": [{"id": i} for i in range(100)]}),
        httpx.Response(200, json={"total_count": 300, "artifacts": []}),
    ]
    server = Server({("GET", path): pages})
    assert len(_hub(server).run_artifacts(9)) == 100
    assert [r.url.params["page"] for r in server.seen] == ["1", "2"]


def test_run_artifacts_current_behavior_reads_a_string_total_count() -> None:
    # 現状固定（I-001）: total_count は int() で読む（文字列でも数として比べる）
    path = f"/repos/{REPO}/actions/runs/9/artifacts"
    server = Server({("GET", path): [httpx.Response(200, json={"total_count": "2", "artifacts": [{"id": 1}, {"id": 2}]})]})
    assert _hub(server).run_artifacts(9) == [{"id": 1}, {"id": 2}]
    assert len(server.seen) == 1


def test_run_artifacts_current_behavior_over_the_limit_message() -> None:
    # 現状固定（I-001）: 上限を超えたときの文言・状態 0・届いたマーカー、読んだページ数
    path = f"/repos/{REPO}/actions/runs/9/artifacts"
    full = httpx.Response(200, json={"total_count": 100000, "artifacts": [{"id": 1}] * 100})
    server = Server({("GET", path): [full]})
    with pytest.raises(github.GitHubError) as caught:
        _hub(server).run_artifacts(9)
    pages = github._page_cap("github.list-pages")
    assert str(caught.value) == f"実行 9 の成果物が {pages} ページを超える（上限 github.list-pages）"
    assert (caught.value.status, caught.value.unreached) == (0, False)
    assert len(server.seen) == pages


def test_check_runs_current_behavior_stops_on_an_empty_page_before_total() -> None:
    # 現状固定（I-001 の重複の相手）: check_runs も空のページで読むのをやめ、上限の文言は github.check-runs を名指す
    path = f"/repos/{REPO}/commits/{HEAD}/check-runs"
    pages = [
        httpx.Response(200, json={"total_count": 300, "check_runs": [{"id": i} for i in range(100)]}),
        httpx.Response(200, json={"total_count": 300, "check_runs": []}),
    ]
    server = Server({("GET", path): pages})
    assert [r["id"] for r in _hub(server).check_runs(HEAD)] == list(range(100))
    assert [(r.url.params["per_page"], r.url.params["page"]) for r in server.seen] == [("100", "1"), ("100", "2")]
    full = httpx.Response(200, json={"total_count": 100000, "check_runs": [{"id": 1}] * 100})
    with pytest.raises(github.GitHubError) as caught:
        _hub(Server({("GET", path): [full]})).check_runs(HEAD)
    pages_cap = github._page_cap("github.check-runs")
    assert str(caught.value) == f"{HEAD} のチェックが {pages_cap} ページを超える（上限 github.check-runs）"


def test_artifact_zip_follows_the_redirect_and_keeps_bytes() -> None:
    blob = b"PK\x03\x04\xff\x00binary"
    redirect = httpx.Response(302, headers={"location": "https://blob.example.test/zip/1"})
    server = Server(
        {
            ("GET", f"/repos/{REPO}/actions/artifacts/3/zip"): [redirect],
            ("GET", "/zip/1"): [httpx.Response(200, content=blob, headers={"content-type": "application/zip"})],
        }
    )
    assert _hub(server).artifact_zip(3) == blob


def test_artifact_zip_raises_gone_for_an_expired_artifact() -> None:
    server = Server({("GET", f"/repos/{REPO}/actions/artifacts/3/zip"): [httpx.Response(410, json={"message": "Gone"})]})
    with pytest.raises(github.GitHubError) as caught:
        _hub(server).artifact_zip(3)
    assert caught.value.status == 410
