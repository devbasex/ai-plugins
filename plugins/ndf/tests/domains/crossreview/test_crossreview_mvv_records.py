"""cross-review の結果 JSON と MVV の記録に、プロジェクト MVV に反するとした指摘が残ること（#4076・#4090）。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import publish
from ndf.adapters.github import PUBLIC, GitHubError
from ndf.domains.crossreview import cli, drive, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.prior_decide import Remark
from ndf.domains.records import jsonl, mvv

from ._repos import Repos, sh
from .test_crossreview_citations import RIGHT, WRONG, Fixer, _base, _git_fails, _push_pr
from .test_crossreview_mvv import REDLINE_BODY, _push_base
from .test_crossreview_run import IMPACT, PR, _state

# 別のリポジトリへの参照を含む自由記述（書く前に、参照先の公開範囲を照会する）
VALUE, REASON, REDLINE, CITED = "方針に反する指摘", "o/other#1 で扱う", "o/other#2 を消す操作", "o/other#3"
HELD = [{"text": VALUE, "mvv": "Value 1"}, {"text": REDLINE, "mvv": "P1"}]
LEAK = "o/hidden-repository"


@pytest.fixture(autouse=True)
def _mvv_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))


class Visibility:
    """参照先の公開範囲の照会の偽物。`error` があれば断る。`asked` は書き先のほかのリポジトリを照会した回ごとの並び。"""

    repo = "o/r"

    def __init__(self, error: Optional[Exception] = None):
        self.error = error
        self.calls = 0
        self.asked: list[set[str]] = []

    def visibilities(self, repos: Iterable[str]) -> dict[str, str]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if set(repos) != {self.repo}:
            self.asked.append(set(repos))
        return dict.fromkeys(repos, PUBLIC)


class Review:
    """MVV に反する指摘の見送り・人の承認が要る操作の指摘・根拠の記述の誤りを 1 件ずつ残して収束するレビュー。"""

    def __init__(self, repos: Repos, wt: Path, tmp_path: Path, capsys: Any, monkeypatch: Any, hub: Any):
        _push_base(repos, text=REDLINE_BODY)
        _push_pr(repos, "doc.md", "# 設計", f"- 根拠: {CITED}（MVV バージョン 11）")
        self.wt, self.state, self.capsys, self.monkeypatch, self.hub = wt, tmp_path / "st", capsys, monkeypatch, hub

    def run(self, seen: Visibility, *extra: str) -> dict[str, Any]:
        """照会に `seen` を使って起動し、結果 JSON を返す。`asked` に、記録のコメントを書き始める前の照会の数を残す。"""
        script = {"codex": [[{"severity": "major", "impact": IMPACT, **h} for h in HELD]], "kiro": [[]]}
        fake = Fixer(self.state, self.wt, ("doc.md", f"# 設計\n{RIGHT}\n"), script=script)
        fake.said = {"outcome": fin.SKIPPED, "reason": REASON}
        publisher = publish.Publisher(seen, accounts=frozenset())  # type: ignore[arg-type]
        self.hub.clean = lambda texts: publisher.guarded(*texts)[0]
        report = cli._report

        def counted(*args: Any) -> Any:
            self.asked = (seen.calls, list(seen.asked))
            return report(*args)

        with self.monkeypatch.context() as m:
            m.setattr(cli, "_report", counted)
            code, got = _base(self.capsys, m, fake, self.wt, *extra)
        self.calls = fake.calls
        assert (code, got["status"]) == (0, "ok")
        return got


@pytest.fixture
def review(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> Review:
    return Review(repos, wt, tmp_path, capsys, monkeypatch, hub)


def _records(kind: str) -> list[dict[str, Any]]:
    return jsonl.read_rows(mvv.record_path(kind), kind=kind)[0]


def _reviews() -> list[dict[str, Any]]:
    return _records("mvv-validation")


def test_result_and_records_hold_the_findings_judged_by_the_mvv(review: Review, repos: Repos, monkeypatch: pytest.MonkeyPatch) -> None:
    """結果と記録に、指摘の項目・終わり方・理由・本文が残る。別リポジトリの公開範囲の照会は、結果と記録を合わせて 1 回で済む。
    記録のリポジトリは起動の始めに解決した主 worktree で、解決し直さない。"""
    monkeypatch.setattr(mvv, "repo_key", lambda root: pytest.fail("主 worktree を解決し直した"))
    out = review.run(Visibility())["outputs"]["mvv"]
    head = sh(review.wt, "rev-parse", "HEAD").strip()
    assert review.asked[1] == [{"o/other"}]
    citation = {"id": "r1-n1", "path": "doc.md", "line": 2, "cited": CITED, "unknown": [CITED], "outcome": "fixed"}
    assert {k: out[k] for k in ("version", "head", "findings", "citations", "redlines", "disputed")} == {
        "version": 11,
        "head": head,
        "findings": [],
        "citations": [citation],
        "redlines": [{"id": "r1-2", "item": "P1", "text": REDLINE}],
        "disputed": [{"id": "r1-1", "item": "Value 1", "reason": REASON}],
    }
    got = {r["id"]: (r["pr"], r["head"], r["sha256"], r["item"], r["outcome"], r.get("reason"), r.get("text")) for r in _reviews()}
    shared = (PR, head, out["sha256"])
    assert got == {
        "r1-n1": (*shared, CITED, "fixed", None, None),
        "r1-1": (*shared, "Value 1", "deferred", REASON, None),
        "r1-2": (*shared, "P1", "redline", None, REDLINE),
    }
    assert [(s["kind"], s["id"]) for s in _records("mvv-signals")] == [("review_disputed", "r1-1")]
    assert {r["repo"] for r in _reviews()} == {str(repos.main.resolve())}
    # 前回の収束を返す打ち直しは、担当を起動せず、同じ結果を返し、記録を足さない
    seen = Visibility()
    assert review.run(seen)["outputs"]["mvv"] == out
    assert (review.calls, seen.asked, len(_reviews()), len(_records("mvv-signals"))) == ([], [{"o/other"}], 3, 1)


def test_fixed_finding_is_listed_without_a_reason(review: Review) -> None:
    script = {"codex": [[{"severity": "major", "impact": IMPACT, **HELD[0]}]], "kiro": [[]]}
    fake = Fixer(review.state, review.wt, ("doc.md", f"# 設計\n{RIGHT}\n"), script=script)
    _, got = _base(review.capsys, review.monkeypatch, fake, review.wt)
    out = got["outputs"]["mvv"]
    assert out["findings"] == [{"id": "r1-1", "item": "Value 1", "by": "codex", "outcome": "fixed"}]
    assert out["redlines"] == out["disputed"] == [] and _records("mvv-signals") == []


def test_issue_4090_7_unreachable_github_hides_the_free_text_and_the_records_are_completed_later(review: Review) -> None:
    """公開範囲を照会できない起動も、収束した結果を返す。自由記述は結果で固定の文になり、記録には書かない。照会できる打ち直しが記録を埋める。"""
    seen = Visibility(GitHubError(f"{LEAK} を読めない", unreached=True))
    got = review.run(seen)
    out, hidden = got["outputs"]["mvv"], publish.WITHHELD
    assert review.asked == (1, [])
    assert [(d["id"], d["reason"]) for d in out["disputed"]] == [("r1-1", hidden)]
    assert [(r["id"], r["item"], r["text"]) for r in out["redlines"]] == [("r1-2", "P1", hidden)]
    [citation] = out["citations"]
    assert [citation[k] for k in ("path", "cited", "unknown")] == [hidden, hidden, [hidden]]
    told = [w for w in got["warnings"] if w.startswith("MVV の")]
    assert len(told) == 2 and all("通信の障害" in w for w in told) and "o/other" not in json.dumps(out, ensure_ascii=False)
    assert LEAK not in json.dumps([out, told], ensure_ascii=False)
    written = _reviews()
    assert [(r["outcome"], r["withheld"]) for r in written] == [("fixed", True), ("deferred", True), ("redline", True)]
    assert "o/other" not in json.dumps(written) and hidden not in json.dumps(written, ensure_ascii=False)
    assert len(_records("mvv-signals")) == 1
    # 照会できないまま打ち直しても足さない。照会できれば、担当を起動せずに伏せない記録を足し、その後は照会できなくても足さない
    for error, count in ((seen.error, 3), (None, 6), (seen.error, 6)):
        review.run(Visibility(error))
        assert (review.calls, len(_reviews()), len(_records("mvv-signals"))) == ([], count, 1)
    assert not any("withheld" in r for r in _reviews()[3:])


FULL = "/mnt/held/records.jsonl に空きが無い"


def _refuse(*_: Any) -> None:
    raise OSError(FULL)


def test_records_that_cannot_be_written_do_not_change_the_result(review: Review, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録のファイルへ書けなくても、終わり方と結果は変えず、注意に載せる。保存先のパスを含む原因は、状態ファイルだけに残し、書けたら消す。"""
    path = mvv.record_path("mvv-validation")
    path.mkdir(parents=True)
    got = review.run(Visibility())
    assert len(got["outputs"]["mvv"]["citations"]) == 1
    assert got["warnings"].count(drive.RECORDS_UNWRITTEN) == 1
    body = review.hub.comments[-1][2]
    assert drive.RECORDS_UNWRITTEN in body and str(path) not in body + json.dumps(got, ensure_ascii=False)
    assert str(path) in _state(review.state)[store.MVV_RECORD_ERROR]
    # 同じ head の打ち直しは、失敗が続く間は PR のコメントを書き直さず、書けたら失敗の注意を消す。また失敗したら注意を戻す
    written = len(review.hub.comments)
    review.run(Visibility())
    assert (review.calls, len(review.hub.comments)) == ([], written)
    path.rmdir()
    assert drive.RECORDS_UNWRITTEN not in review.run(Visibility()).get("warnings", [])
    assert store.MVV_RECORD_ERROR not in _state(review.state) and len(_reviews()) == 3
    assert (review.calls, len(review.hub.comments)) == ([], written + 1)
    assert drive.RECORDS_UNWRITTEN not in review.hub.comments[-1][2]
    with monkeypatch.context() as broken:
        broken.setattr(mvv, "record_review", _refuse)
        review.run(Visibility())
    assert (review.calls, len(review.hub.comments)) == ([], written + 2)
    assert drive.RECORDS_UNWRITTEN in review.hub.comments[-1][2]


def test_cause_that_cannot_be_saved_does_not_change_the_result(review: Review, monkeypatch: pytest.MonkeyPatch) -> None:
    """MVV の記録と状態ファイルの両方に書けなくても、結果を返し、固定の注意を 2 つ載せる。同じ head の打ち直しで初めて失敗しても、
    PR のコメントに注意を載せる（載せた後は、同じ失敗で書き直さない）。"""
    review.run(Visibility())
    written, save = len(review.hub.comments), drive._save
    monkeypatch.setattr(mvv, "record_review", _refuse)
    monkeypatch.setattr(drive, "_save", lambda run: _refuse() if store.MVV_RECORD_ERROR in run.data else save(run))
    got = review.run(Visibility())
    assert len(got["outputs"]["mvv"]["citations"]) == 1
    assert {drive.RECORDS_UNWRITTEN, drive.CAUSE_UNSAVED} <= set(got["warnings"])
    body = review.hub.comments[-1][2]
    assert drive.RECORDS_UNWRITTEN in body and FULL not in json.dumps(got, ensure_ascii=False) + body
    review.run(Visibility())
    assert (review.calls, len(review.hub.comments)) == ([], written + 1)


def test_findings_of_an_earlier_mvv_are_left_out(review: Review, repos: Repos) -> None:
    """新しい MVV が承認された後の起動は、前の MVV で判定した指摘を結果と記録に載せない。"""
    review.run(Visibility())
    _push_base(repos, 12)
    _, got = _base(review.capsys, review.monkeypatch, Fixer(review.state, review.wt), review.wt)
    out = got["outputs"]["mvv"]
    assert (out["version"], out["findings"], out["redlines"], out["disputed"]) == (12, [], [], [])
    assert [r["id"] for r in _reviews() if r["mvv_version"] == 12] == [c["id"] for c in out["citations"]] == ["r3-n1"]


def test_issue_4090_5_citation_error_reaches_the_result_and_the_records_after_starting_over(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """未解決のまま残った根拠の記述の誤りは、空の状態ディレクトリから始め直しても、同じ MVV の指摘として結果と MVV の記録に残る。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    _, got = _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt, "--max-rounds", "1")
    [first] = got["outputs"]["mvv"]["citations"]
    assert (first["id"], first["cited"], first["outcome"]) == ("r1-n1", "Value 1", fin.UNRESOLVED)
    hub.port.existing = [Remark("comment", "ndf", hub.comments[-1][2], trusted=True)]
    hub.port.made[PR] = []
    # 確かめられなかった起動では、引いた項目を持たない引き継いだ指摘だけが未解決で残る
    again = Fixer(tmp_path / "st2", wt)
    again._fixes = 1
    with monkeypatch.context() as broken:
        _git_fails(broken, "_diff_files")
        _, got = _base(capsys, broken, again, wt, "--max-rounds", "1")
    [carried] = got["outputs"]["mvv"]["citations"]
    assert carried == {"id": "p-r1-n1", "path": "doc.md", "line": 2, "cited": None, "unknown": [], "outcome": fin.UNRESOLVED}
    # 同じ誤りが見つかれば、引き継いだ指摘は直したになり、同じ誤りの新しい指摘が 1 件だけ残る
    last = Fixer(tmp_path / "st2", wt)
    last._fixes = 2
    _, got = _base(capsys, monkeypatch, last, wt, "--max-rounds", "1")
    found = got["outputs"]["mvv"]["citations"]
    assert [(c["id"], c["cited"], c["outcome"]) for c in found] == [("p-r1-n1", None, fin.FIXED), ("r2-n1", "Value 1", fin.UNRESOLVED)]
    records = _reviews()
    assert len({r["key"] for r in records}) == 1
    assert [(r["id"], r.get("item"), r["outcome"]) for r in records] == [
        ("r1-n1", "Value 1", fin.UNRESOLVED),
        ("p-r1-n1", None, fin.UNRESOLVED),
        ("r2-n1", "Value 1", fin.UNRESOLVED),
    ]
    assert _records("mvv-signals") == []
