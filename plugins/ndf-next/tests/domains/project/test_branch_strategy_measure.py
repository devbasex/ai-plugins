"""`ndf project branch-strategy`（解析のサブコマンド）の単体テスト: 履歴から測る（#3672 のテスト設計の 9・10・11。#3718）。

入力と期待値は 10 系の `plugins/ndf/scripts/tests/test_branch_strategy.py` の同じ観点のテストと同じにする。
5 種の設定は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテストも同じ fixture を読む。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import contracts
from ndf.core import branch_strategy as bs
from ndf.domains.project import strategy_measure, strategy_rules
from ndf.domains.project import strategy_write as bw

from ._branch_strategy import RESULT_SCHEMA, C, fake_api, git, history_repo, make_repo


def assert_result_shape(res: dict) -> None:
    """作成の結果の形（`branch-strategy.result.schema.json`）に通る。"""
    assert contracts.schema_errors(res, RESULT_SCHEMA) == []


# --- 測る（受け入れ条件 9・10・11） ------------------------------------------------------------


def test_measure_develop_main(tmp_path):
    root = history_repo(
        tmp_path,
        4,
        {
            "package.json": '{"version": "1.0.0"}',
            "CONTRIBUTING.md": "PR は `gh pr create --base develop` で出す\n",
            ".github/workflows/release.yml": "run: gh release create v1\n",
        },
    )
    prs = [(i, "develop", f"sprint/{i}") for i in range(1, 5)] + [(9, "main", "develop")]
    api = fake_api(prs, tags=["v1.0.0", "v1.1.0", "v1.2.0"], releases=["v1.0.0", "v1.1.0", "v1.2.0"])
    res = strategy_measure.measure_strategy(root, api)
    # 本番と別のブランチへ入る事例は、base を丸ごと出す方式と本番から組んで出す方式を見分けられないため、リリース方式を問う
    assert res["status"] == "ask" and [q["pointer"] for q in res["questions"]] == ["/release_strategy"]
    assert res["questions"][0]["candidates"] == ["merge_base_branch", "release_branch"]
    assert_result_shape(res)
    assert res["values"]["merge"]["method"] == "direct"
    got = strategy_measure.complete_answers(res, {"/release_strategy": "merge_base_branch"})
    assert got["status"] == "ok", got["questions"]
    v = got["values"]
    assert got["kind"] == "web-develop" and v["release_strategy"] == "merge_base_branch" and v["branches"]["base"] == "develop"
    assert v["sprint"]["start"] == "base"
    assert v["merge"]["method"] == "direct" and v["release"]["tag"] == "v{version}" and v["release"]["cd"] == {"workflow": "release.yml"}
    refs = {(e["pointer"], e["source"]) for e in got["evidence"]}
    assert ("/release/tag", "tag") in refs and ("/release/form", "preset") in refs
    assert bs.validate(v) == []
    mobile = strategy_measure.complete_answers(res, {"/release_strategy": "release_branch"})
    assert mobile["status"] == "ok" and mobile["kind"] == "mobile" and mobile["values"]["sprint"]["start"] == "production"


def test_measure_asks_cd_when_a_push_workflow_has_no_release_marks(tmp_path):
    """タグ・Release を作らないが本番への push で動くワークフローは、デプロイだけの CD かを測れないため問う。"""
    root = history_repo(tmp_path, 4, {".github/workflows/deploy.yml": "on:\n  push:\n    branches: [main]\njobs: {}\n"})
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    q = [q for q in res["questions"] if q["pointer"] == "/release/cd"]
    assert res["status"] == "ask" and q and q[0]["candidates"] == [{"workflow": "deploy.yml"}, None]


def test_measure_asks_the_plugin_and_version_store_with_several_plugins(tmp_path):
    """版数付きのプラグインが複数あるときは「版数のファイルが無い」と扱わず、対象のプラグインと版数の保存先を問う。"""
    manifests = {f"plugins/{n}/.claude-plugin/plugin.json": json.dumps({"name": n, "version": "1.0.0"}) for n in ("a", "b")}
    root = history_repo(tmp_path, 4, manifests)
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    asked = {q["pointer"]: q["candidates"] for q in res["questions"]}
    assert res["status"] == "ask" and asked["/release/plugin"] == ["a", "b"] and asked["/release/version_store"] == [{"kind": "plugin"}]
    assert "version_store" not in res["values"].get("release", {})


def test_measure_does_not_take_a_sprint_branch_as_the_base(tmp_path):
    """課題の PR → sprint/s1 → develop → main の履歴では、一時的なスプリントブランチを長く残るブランチに数えない。"""
    root = history_repo(tmp_path, 4, {"package.json": '{"version": "1.0.0"}'})
    prs = [(i, "sprint/s1", f"issue/{i}") for i in range(1, 4)]
    prs += [(10 + i, "develop", f"sprint/s{i}") for i in range(1, 5)] + [(20, "main", "develop")]
    res = strategy_measure.measure_strategy(root, fake_api(prs))
    assert res["values"]["branches"]["base"] == "develop"
    assert not any(q["pointer"] == "/branches/base" for q in res["questions"])
    base_refs = {e["ref"] for e in res["evidence"] if e["pointer"] == "/branches/base"}
    assert base_refs and base_refs <= {f"#{10 + i}" for i in range(1, 5)}


def test_measure_trunk_without_tags(tmp_path):
    root = history_repo(tmp_path, 4)
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["status"] == "ok", res["questions"]
    assert_result_shape(res)
    v = res["values"]
    assert res["kind"] == "docs" and v["release_strategy"] == "trunk" and v["merge"]["method"] == "strict"
    assert v["release"]["tag"] is None and v["release"]["github_release"] is False
    assert any(e["pointer"] == "/release/tag" and e["ref"] == "0 件" for e in res["evidence"])
    assert ("/release/form", "preset") in {(e["pointer"], e["source"]) for e in res["evidence"]}


def test_measure_doc_conflict_and_insufficient(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["status"] == "ask" and any(q["why"] == "conflict" and q["pointer"] == "/branches/base" for q in res["questions"])
    assert_result_shape(res)
    root2 = history_repo(tmp_path / "b", 0)
    res2 = strategy_measure.measure_strategy(root2, fake_api([]))
    assert res2["status"] == "ask" and {q["why"] for q in res2["questions"]} >= {"insufficient"}
    # PR が 0 件の既存のリポジトリは、種類を渡しても新規とみなさずに問う
    res3 = strategy_measure.measure_strategy(root2, fake_api([]), prefer="docs")
    assert res3["status"] == "ask" and {q["why"] for q in res3["questions"]} >= {"insufficient"}


def test_answering_the_asked_questions_completes_the_section(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["values"]["release_strategy"] == "trunk" and res["evidence"]
    got = strategy_measure.complete_answers(res, {"/branches/base": "main"})
    assert got["status"] == "ok" and got["kind"] == "docs" and bs.validate(got["values"]) == []


def test_answering_other_questions_still_asks_the_required_review(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 1}, "enforce_admins": {"enabled": True}}
    api = fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)], rules=[], protection=protection)
    res = strategy_measure.measure_strategy(root, api)
    assert res["status"] == "ask" and res["facts"]["required_approvals"] == 1
    got = strategy_measure.complete_answers(res, {"/branches/base": "main"})
    assert got["status"] == "ask" and [q["pointer"] for q in got["questions"]] == ["/approval/via"]
    done = strategy_measure.complete_answers(res, {"/branches/base": "main", "/approval/via": "review"})
    assert done["status"] == "ok" and done["values"]["approval"]["via"] == "review"


def test_measure_tags_without_cd_asks_r10(tmp_path):
    root = history_repo(tmp_path, 4)
    prs = [(i, "main", f"sprint/{i}") for i in range(1, 5)]
    res = strategy_measure.measure_strategy(
        root, fake_api(prs, tags=["v1.0.0", "v1.0.1", "v1.0.2"], releases=["v1.0.0", "v1.0.1", "v1.0.2"])
    )
    assert res["status"] == "ask" and res["questions"][0]["pointer"] == "/release/cd"


def test_count_key_thresholds():
    rule = {"min_samples": 3, "min_ratio": 0.9}
    assert strategy_measure.count_key("/x", [("a", "1"), ("a", "2")], rule).why == "insufficient"
    assert strategy_measure.count_key("/x", [("a", str(i)) for i in range(3)], rule).value == "a"
    nine = [("a", str(i)) for i in range(9)] + [("b", "9")]
    assert strategy_measure.count_key("/x", nine, rule).value == "a"
    eight = [("a", str(i)) for i in range(8)] + [("b", "8"), ("b", "9")]
    m = strategy_measure.count_key("/x", eight, rule)
    assert m.why == "conflict" and m.candidates == ["a", "b"]


RF = strategy_rules.RuleFacts


@pytest.mark.parametrize(
    ("facts", "rs", "want"),
    [
        (RF(True, 0, True, False), "trunk", "merge-queue"),
        (RF(True, 0, True, False), "merge_base_branch", "merge-queue"),
        (RF(True, 0, False, True), "trunk", "strict"),
        (RF(True, 0, False, True), "merge_base_branch", "strict"),
        (RF(True, 0, False, False), "trunk", "conflict"),
        (RF(True, 0, False, False), "merge_base_branch", "direct"),
        (RF(False, 0, False, False), "trunk", "strict"),
        (RF(False, 0, False, False), "merge_base_branch", "direct"),
        (None, "trunk", "insufficient"),
    ],
)
def test_decide_merge_method(facts, rs, want):
    m = strategy_measure.decide_merge_method(facts, rs)
    assert (m.why or m.value) == want


def test_pick_kind_ambiguous_and_queue_matches_strict():
    presets = bw.load_presets()
    m = {
        "/release_strategy": strategy_measure.Measured("trunk"),
        "/sprint/start": strategy_measure.Measured("production"),
        "/release/version_store": strategy_measure.Measured({"kind": "plugin"}),
        "/merge/commit": strategy_measure.Measured("merge"),
        "/merge/method": strategy_measure.Measured("strict"),
    }
    assert strategy_measure.pick_kind(m, presets) == ("plugin", [])
    m["/release/version_store"] = strategy_measure.Measured({"kind": "json", "path": "p", "pointer": "/v"})
    kind, cands = strategy_measure.pick_kind(m, presets)
    assert kind is None and cands


def _commit(root: Path, subject: str, trailers: dict | None = None) -> str:
    body = ["-m", "\n".join(f"{k}: {v}" for k, v in trailers.items())] if trailers else []
    git(root, *C, "commit", "-q", "--allow-empty", "-m", subject, *body)
    return git(root, "rev-parse", "HEAD").strip()


def landed_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """本番ブランチの最初の親の履歴に、マージの記録（trailer）の各形を並べた木。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}, "worktree.json": {"remote": "up"}})
    pr, tip = strategy_measure.TRAILER_PR, strategy_measure.TRAILER_TIP
    sha = {"init": git(root, "rev-parse", "HEAD").strip()}
    sha["plain"] = _commit(root, "plain")
    sha["five"] = _commit(root, "landed five", {pr: "5", tip: sha["plain"]})
    sha["known"] = _commit(root, "landed known", {pr: "1", tip: sha["five"]})
    sha["wrong_tip"] = _commit(root, "landed wrong tip", {pr: "7", tip: sha["plain"]})
    sha["not_digit"] = _commit(root, "landed not digit", {pr: "abc", tip: sha["wrong_tip"]})
    sha["five_again"] = _commit(root, "landed five again", {pr: "5", tip: sha["not_digit"], strategy_measure.TRAILER_HEAD: "h"})
    sha["zero"] = _commit(root, "landed zero", {pr: "0", tip: sha["five_again"]})
    git(root, "checkout", "-qb", "side")
    _commit(root, "side work")
    git(root, "checkout", "-q", "main")
    git(root, *C, "merge", "-q", "--no-ff", "side", "-m", "Merge side")
    sha["merge"] = git(root, "rev-parse", "HEAD").strip()
    return root, sha


def test_collect_production_commits_reads_the_first_parent_history_and_landed_prs(tmp_path):
    """現状固定: 最初の親の履歴を新しい順に読み、記録の PR は親が NDF-Tip と同じ・数字・0 でない・未知のときだけ事例に足す。"""
    root, sha = landed_repo(tmp_path)
    s = strategy_measure.collect_samples(root, fake_api([(1, "main", "sprint/0")]), float("inf"), 30)
    assert s.production == "main" and "history" not in s.unreadable
    order = ["merge", "zero", "five_again", "not_digit", "wrong_tip", "known", "five", "plain", "init"]
    landed = {"zero": "0", "five_again": "5", "not_digit": "abc", "wrong_tip": "7", "known": "1", "five": "5"}
    assert [c["sha"] for c in s.commits] == [sha[k] for k in order]
    assert [c["parents"] for c in s.commits] == [2, 1, 1, 1, 1, 1, 1, 1, 0]
    assert [c["landed"] for c in s.commits] == [landed.get(k) for k in order]
    assert [c["subject"] for c in s.commits] == [
        "Merge side",
        "landed zero",
        "landed five again",
        "landed not digit",
        "landed wrong tip",
        "landed known",
        "landed five",
        "plain",
        "init",
    ]
    assert all(set(c) == {"sha", "parents", "subject", "landed"} for c in s.commits)
    # 新しい順に読むため、同じ PR は新しい方の記録が残る
    assert s.prs == [
        {"number": 1, "base": "main", "head": "sprint/0"},
        {"number": 5, "base": "main", "head": None, "landed": sha["five_again"]},
    ]


def test_collect_production_commits_prefers_the_remote_branch_and_honours_the_limit(tmp_path):
    """現状固定: リモートの本番ブランチがあればそちらを読み、件数は limit で切る。"""
    root, sha = landed_repo(tmp_path)
    git(root, "update-ref", "refs/remotes/up/main", sha["known"])
    s = strategy_measure.collect_samples(root, fake_api([]), float("inf"), 30)
    assert [c["sha"] for c in s.commits] == [sha[k] for k in ("known", "five", "plain", "init")]
    assert [p["number"] for p in s.prs] == [1, 5]
    assert s.prs[1] == {"number": 5, "base": "main", "head": None, "landed": sha["five"]}
    s = strategy_measure.collect_samples(root, fake_api([]), float("inf"), 2)
    assert [c["sha"] for c in s.commits] == [sha["known"], sha["five"]]


def test_collect_production_commits_marks_history_unreadable_without_the_branch(tmp_path):
    """現状固定: 本番ブランチがリモートにも手元にも無ければ `history` を読めないとし、履歴は空のまま。"""
    root, _ = landed_repo(tmp_path)
    s = strategy_measure.collect_samples(root, fake_api([], default="gone"), float("inf"), 30)
    assert s.production == "gone"
    assert s.commits == []
    assert "history" in s.unreadable


def test_default_api_reads_the_repository_of_the_declared_remote(tmp_path, monkeypatch):
    """`{owner}/{repo}` は設定したリモートの URL から求めたリポジトリで埋める（gh の選ぶ取得元に任せない）。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    git(root, "remote", "add", "origin", "https://github.com/volare/target.git")
    read: list[str] = []

    def get(self, path):
        read.append(path)
        return {"default_branch": "main"}

    monkeypatch.setattr(strategy_measure.Gh, "get", get)
    api = strategy_measure.default_api(root, 1e18)
    assert api("repos/{owner}/{repo}/rules/branches/main") == ({"default_branch": "main"}, "")
    assert read == ["repos/volare/target/rules/branches/main"]


def test_default_api_without_a_github_remote_reads_nothing(tmp_path, monkeypatch):
    """リモートから測るリポジトリを求められなければ、gh を呼ばずに読めなかったとして返す。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    monkeypatch.setattr(strategy_measure.Gh, "get", lambda self, path: pytest.fail("gh を呼んだ"))
    got, why = strategy_measure.default_api(root, 1e18)("repos/{owner}/{repo}")
    assert got is None and why


def test_unreadable_kinds_on_questions_follow_the_result_schema(tmp_path):
    """読めなかった種類（権限の不足・通信の失敗）を添えた質問も、作成の結果の形に通る。"""
    root = history_repo(tmp_path, 0)
    got = strategy_measure.measure_strategy(root, api=lambda path: (None, "gh が失敗した: HTTP 403"))
    assert got["questions"] and any("unreadable" in q for q in got["questions"])
    assert_result_shape(got)
