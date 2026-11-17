"""リリースとマージをブランチ戦略の宣言で動かす（#3672・#3681・#3720）。

代表の 5 種の宣言（`tests/fixtures/branch_strategy/`。10 系のテストも同じファイルを読む）から、版上げの PR の経路と
マージの主体と方式が 10 系（`release_lib/strategy_route.route_of`・`release_lib/land.py`）と同じ値になること、
承認の後に head が動いたら入れないこと（#3661）、証跡の書き手が 1 つで今の head の答えがあれば問わないこと（#3671）を確かめる。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from ndf.adapters.github import Authored, RuleFacts
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import approval_decide, land, route_decide, trail

from ._fakes import KINDS, FakeHub, pull, section, strategy

H1, H2, TIP = "1" * 40, "2" * 40, "c" * 40

# 種類: (スプリントの PR の宛先, 版上げの PR の起点（None は無し）, 宛先, 同期の PR, 主体, 方式)。10 系の test_branch_strategy.EXPECT と同じ
EXPECT = {
    "web-develop": ("develop", "develop", "main", True, "ndf", "direct"),
    "web-trunk": ("main", None, "main", False, "ndf", "merge-queue"),
    "mobile": ("develop", "main", "main", True, "ndf", "direct"),
    "docs": ("main", None, "main", False, "ndf", "strict"),
    "plugin": ("main", "main", "main", False, "ndf", "merge-queue"),
}


@pytest.mark.parametrize("kind", KINDS)
def test_five_kinds_route_and_landing(kind: str) -> None:
    """受け入れ条件 1（#3681）: 5 種の宣言から、リリースの PR とマージの主体と方式を 10 系と同じ値で返す。"""
    s = strategy(kind)
    sprint_to, source, target, sync, actor, method = EXPECT[kind]
    assert s.pr_target("sprint") == sprint_to and s.pr_target("release") == "main"
    r = route_decide.route_of(s)
    assert (r.source if r.release_pr else None) == source and r.target == target and r.sync is sync
    landing = route_decide.landing_of(s)
    assert (landing.actor, landing.method, landing.admin) == (actor, method, False)
    # 人へ問う承認は正式版 1 本につき 1 回（リリース承認ゲートだけ。ruleset の承認を求めない）
    assert s.section["approval"]["via"] == "gate"
    assert trail.trail_writer(s, merged_by_human=False) == trail.WRITER_ANSWER


def test_route_without_version_store_is_the_pr_itself() -> None:
    s = section("web-develop")
    s["release"].update(version_store={"kind": "none"}, tag=None, github_release=False)
    r = route_decide.route_of(Strategy(s))
    assert r.release_pr and not r.bump and (r.source, r.target) == ("develop", "main")


def test_admin_only_with_direct() -> None:
    """I6: 迂回は `method: direct` で `admin: true` のときだけ。"""
    assert route_decide.landing_of(strategy("web-develop", admin=True)).admin
    assert not route_decide.landing_of(strategy("plugin", admin=True)).admin


# --- 本番ブランチの ruleset とセクションの食い違い（I10）と、方式ごとの確かめ（I7） ----------------------------


def facts(**kw: Any) -> RuleFacts:
    base: dict[str, Any] = {"present": True, "approvals": 0, "queue": False, "up_to_date": False, "bypass": None}
    return RuleFacts(**{**base, **kw})


def test_ruleset_mismatch() -> None:
    plugin = strategy("plugin")
    assert land.ruleset_mismatch(plugin, facts(queue=True)) == []
    assert land.ruleset_mismatch(plugin, facts(queue=True, approvals=1))[0]["key"] == "approval.via"
    assert land.ruleset_mismatch(plugin, facts(queue=False))[0]["key"] == "merge.method"
    assert land.ruleset_mismatch(plugin, facts(present=False))[0]["key"] == "merge.method"
    assert land.ruleset_mismatch(plugin, facts(queue=True, queue_merge_method="MERGE")) == []
    assert land.ruleset_mismatch(plugin, facts(queue=True, queue_merge_method="SQUASH"))[0]["key"] == "merge.commit"
    assert land.ruleset_mismatch(strategy("docs"), facts(present=False)) == []
    assert land.ruleset_mismatch(strategy("docs"), facts())[0]["ruleset"] == "最新の取り込みを求めない"
    admin = strategy("web-develop", admin=True)
    assert land.ruleset_mismatch(admin, facts(approvals=1, bypass="pull_requests_only")) == []
    assert land.ruleset_mismatch(admin, facts(approvals=1, bypass="exempt"))[0]["key"] == "merge.admin"


def test_check_production_pr() -> None:
    plugin, docs, dev = strategy("plugin"), strategy("docs"), strategy("web-develop")
    prod = {"tip": TIP, "version": "1.0.0"}
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 1}, prod, []) is None
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "0.9.0", "number": 1}, prod, [])
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 1}, prod, [{"number": 2, "bump": True}])
    assert land.check_production_pr(plugin, {"bump": False}, prod, []) is None
    # 版数を比べられない PR（bump が None）は版上げとして確かめる
    assert land.check_production_pr(plugin, {"bump": None, "cut_version": None, "number": 1}, prod, [])
    # 切った時点か本番の版数を読めなければ、両方 None でも衝突を確かめずに通さない
    unread = land.check_production_pr(plugin, {"bump": None, "cut_version": None, "number": 1}, {"tip": TIP, "version": None}, [])
    assert unread is not None and "版数を読めない" in unread
    # merge-queue は先端を含まなくても、版数が衝突しなければ通る（含むことを求めない）
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 1, "contains_tip": False}, prod, []) is None
    assert land.check_production_pr(docs, {"contains_tip": False}, prod, [])
    assert land.check_production_pr(docs, {"contains_tip": True}, prod, []) is None
    assert land.check_production_pr(dev, {}, prod, []) is None


# --- 承認した head だけを入れる（#3661。テスト設計の 17・19） -----------------------------------------------


def _trail(answer: str, sha: str) -> Authored:
    return Authored("octocat", "OWNER", approval_decide.render_trail(answer, sha, trail.USER_BY))


@dataclass
class StrategyHub(FakeHub):
    """本番ブランチの先端・版数・比べ・キューを答える偽物。`versions` は ref → 版数、`behind` は head が先端に遅れる数。"""

    versions: dict[str, str] = field(default_factory=dict)
    behind: int = 0
    fork: str = TIP  # head と本番の分岐点（版上げの PR を切った時点）

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if method == "GET" and path == "/repos/o/r/branches/main":
            return {"commit": {"sha": TIP}}
        if method == "GET" and path.startswith("/repos/o/r/compare/"):
            return {"merge_base_commit": {"sha": self.fork}, "behind_by": self.behind}
        if method == "GET" and "/contents/" in path:
            ref = params["ref"]
            if ref not in self.versions:
                return None
            raw = json.dumps({"version": self.versions[ref]}).encode("utf-8")
            return {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
        return super().rest(method, path, payload, params)


def _queue_hub(head: str, approved: str, **kw: Any) -> StrategyHub:
    """plugin（trunk・merge-queue）の版上げの PR。本番ブランチは merge queue（merge commit）を持つ。"""
    hub = StrategyHub(pulls={7: pull(head=head)}, comments_of={7: [_trail("approved", approved)]}, **kw)
    hub.versions = {TIP: "1.0.0", head: "1.0.1"}
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [{"type": "merge_queue", "parameters": {"merge_method": "MERGE"}}]
    hub.graphql_data = {"repository": {"mergeQueue": {"entries": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
    return hub


def _lander(hub: FakeHub, s: Strategy) -> land.Lander:
    def wait(reader: Any, budget: float) -> Any:
        from ndf.adapters import ci

        return ci.WatchResult(ci.Reading([], [], [], [], [], [], True), 1, 0.0, 0.0)

    return land.Lander(hub, s, 60.0, wait=wait)


def test_issue_3661_head_moved_after_approval_neither_merges_nor_enqueues() -> None:
    """#3661: 承認の後に head が動いた（承認は H1、今の head は H2）なら、マージもキューへの投入もせずに承認を取り直す依頼で止まる。"""
    hub = _queue_hub(head=H2, approved=H1)
    done = _lander(hub, strategy("plugin")).land(7)
    assert not done.merged and done.kind == "refused" and land.REAPPROVE in done.detail
    assert hub.merged == [] and hub.merge_jumps == []


def test_approved_release_pr_goes_to_the_head_of_the_queue() -> None:
    """承認した head の版上げの PR は、宣言の履歴の作り方で、キューの先頭へ入れる（決定 18）。同期の PR は作らない（trunk）。"""
    hub = _queue_hub(head=H1, approved=H1)
    done = _lander(hub, strategy("plugin")).land(7)
    assert done.merged and hub.merged == [7] and hub.merge_jumps == [True] and hub.merge_methods == ["merge"]
    assert not any(path == "/repos/o/r/pulls" and method == "POST" for method, path, _ in hub.requests)


def test_release_pr_stops_when_the_production_version_moved() -> None:
    """merge-queue: 本番ブランチの版数が切った時点と違えば、キューへ入れずに止まる。"""
    hub = _queue_hub(head=H1, approved=H1)
    hub.fork = "f" * 40
    hub.versions.update({hub.fork: "1.0.0", TIP: "1.0.1", H1: "1.0.1"})
    done = _lander(hub, strategy("plugin")).land(7)
    assert not done.merged and done.kind == "refused" and hub.merged == []


def test_bump_of_treats_no_version_anywhere_as_not_a_bump() -> None:
    """切った時点・head・本番のどれにも版数が無い（定義が任意の `version` を持たない）PR は版上げでない。片側だけ読めなければ None。"""
    assert land.bump_of("1.0.0", "1.0.1", "1.0.0") is True
    assert land.bump_of("1.0.0", "1.0.0", "1.0.0") is False
    assert land.bump_of(None, None, None) is False
    assert land.bump_of(None, "1.0.1", None) is None
    assert land.bump_of(None, None, "1.0.0") is None


def test_pr_passes_when_no_ref_has_a_version() -> None:
    """plugin（merge-queue）でプラグインの定義に版数が無いリポジトリでも、PR を「版数を読めない」で止めず、版上げとして扱わない。"""
    hub = _queue_hub(head=H1, approved=H1)
    hub.versions = {}
    done = _lander(hub, strategy("plugin")).land(7)
    assert done.merged and hub.merged == [7] and hub.merge_jumps == [False]


def test_merge_base_branch_without_a_version_store_takes_the_base_pr() -> None:
    """版数を持たない merge_base_branch はベースブランチから本番への PR そのものを入れ、リリースのブランチ名を求めない。"""
    s = section("web-develop")
    s["release"]["version_store"] = {"kind": "none"}
    hub = FakeHub(pulls={7: pull(ref="develop")}, trusted=set())
    done = _lander(hub, Strategy(s)).land(7)
    assert not done.merged and done.kind == "refused" and "token" in done.detail


def test_strict_reads_no_version_files() -> None:
    """strict は先端を含むかだけを見て、版数の置き場（マーケットプレイスの登録）も版数のファイルも読まない。"""
    s = section("docs")
    s["release"]["version_store"] = {"kind": "plugin"}
    hub = _ReadingHub(pulls={7: pull(head=H1, ref="sprint/x")}, comments_of={7: [_trail("approved", H1)]})
    hub.versions = {TIP: "1.0.0", H1: "1.0.0"}
    hub.responses[("GET", f"/repos/o/r/git/commits/{H1}")] = {"tree": {"sha": "t" * 40}}
    hub.responses[("POST", "/repos/o/r/git/commits")] = {"sha": "d" * 40}
    assert _lander(hub, Strategy(s)).land(7).merged
    assert not any("/contents/" in path for path in hub.paths)


@dataclass
class _ReadingHub(StrategyHub):
    """読んだ REST のパスをすべて `paths` に残す偽物（版数のファイルを読まないことを確かめる）。"""

    paths: list[str] = field(default_factory=list)

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        self.paths.append(path)
        return super().rest(method, path, payload, params)


def test_mismatched_or_unreadable_ruleset_stops_without_merging() -> None:
    """I10: 宣言が merge-queue なのに本番ブランチにキューが無い・ruleset を読めないときは、比べずに入れない。"""
    hub = _queue_hub(head=H1, approved=H1)
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = []
    done = _lander(hub, strategy("plugin")).land(7)
    assert not done.merged and "merge.method" in done.detail and hub.merged == []
    hub = _queue_hub(head=H1, approved=H1, rules_unreadable=True)
    done = _lander(hub, strategy("plugin")).land(7)
    assert not done.merged and "読めない" in done.detail and hub.merged == []


def _strict_hub(head: str, approved: str, behind: int) -> StrategyHub:
    """docs（trunk・production_pr・strict）のスプリントの PR。本番ブランチに ruleset が無い（private の Free）。"""
    hub = StrategyHub(pulls={7: pull(head=head, ref="sprint/x")}, comments_of={7: [_trail("approved", approved)]}, behind=behind)
    hub.responses[("GET", f"/repos/o/r/git/commits/{head}")] = {"tree": {"sha": "t" * 40}}
    hub.responses[("POST", "/repos/o/r/git/commits")] = {"sha": "d" * 40}
    return hub


def test_strict_without_ruleset_fast_forwards_only_a_head_that_contains_the_tip() -> None:
    """I7: ruleset の無い strict は、head が本番の先端を含まなければ止まり、含めば確かめた先端からの早送りで入れる。"""
    hub = _strict_hub(H1, H1, behind=1)
    done = _lander(hub, strategy("docs")).land(7)
    assert not done.merged and done.kind == "refused" and not any(m == "PATCH" for m, _, _ in hub.requests)
    hub = _strict_hub(H1, H1, behind=0)
    done = _lander(hub, strategy("docs")).land(7)
    assert done.merged and done.production_sha == "d" * 40 and hub.merged == []
    made = next(p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/git/commits"))
    assert made["parents"] == [TIP, H1] and f"{land.TRAILER_HEAD}: {H1}" in made["message"]
    ref = next(p for m, path, p in hub.requests if (m, path) == ("PATCH", "/repos/o/r/git/refs/heads/main"))
    assert ref == {"sha": "d" * 40, "force": False}
    # 承認の後に head が動けば早送りもしない（#3661）
    hub = _strict_hub(H2, H1, behind=0)
    assert not _lander(hub, strategy("docs")).land(7).merged and not any(m == "PATCH" for m, _, _ in hub.requests)


def _review(state: str, sha: str, login: str = "octocat", at: str = "2026-01-01T00:00:00Z") -> dict:
    return {"state": state, "commit_id": sha, "user": {"login": login}, "submitted_at": at}


def _by_review(kind: str) -> Strategy:
    s = section(kind)
    s["approval"]["via"] = "review"
    return Strategy(s)


def test_review_approval_is_required_even_for_fast_forward() -> None:
    """`approval.via: review`: ruleset の無い早送りでも、write 以上の人が今の head を承認したレビューが無ければ入れない。"""
    hub = _strict_hub(H1, H1, behind=0)
    hub.comments_of = {}
    done = _lander(hub, _by_review("docs")).land(7)
    assert not done.merged and done.kind == "refused" and "レビュー" in done.detail
    assert not any(m == "PATCH" for m, _, _ in hub.requests)
    # write の権限を持たない人の承認・古い head への承認・承認の後の差し戻しは数えない
    for reviews in (
        [_review("APPROVED", H1, login="mallory")],
        [_review("APPROVED", H2)],
        [_review("APPROVED", H1), _review("CHANGES_REQUESTED", H1, at="2026-01-02T00:00:00Z")],
    ):
        hub = _strict_hub(H1, H1, behind=0)
        hub.reviews_of = {7: reviews}
        assert not _lander(hub, _by_review("docs")).land(7).merged
    hub = _strict_hub(H1, H1, behind=0)
    hub.comments_of = {}
    hub.reviews_of = {7: [_review("COMMENTED", H2), _review("APPROVED", H1)]}
    assert _lander(hub, _by_review("docs")).land(7).merged


def test_queue_wait_reads_the_review_under_review_approval() -> None:
    """`approval.via: review`: キューの待ちの承認の確かめも GitHub のレビューで読み、NDF の証跡を求めない。"""
    hub = _queue_hub(head=H1, approved=H1)
    hub.comments_of = {}
    hub.reviews_of = {7: [_review("APPROVED", H1)]}
    lander = _lander(hub, _by_review("plugin"))
    assert lander._queued_refusal(hub.pull(7), hub.pull(7)) is None
    hub.reviews_of = {}
    refused = lander._queued_refusal(hub.pull(7), hub.pull(7))
    assert refused is not None and refused.kind == "refused"


def test_approved_release_pr_outside_the_queue_holds_other_prs() -> None:
    """決定 18: キューの外の、今の head が承認された版上げの PR を `approved_release` の行で足し、版上げでない PR を保留する。"""
    hub = _queue_hub(head=H1, approved=H1)
    hub.pulls[9] = pull(number=9, head=H2)
    hub.versions[H2] = "1.0.2"
    hub.open_at = [(7, H1), (8, "8" * 40), (9, H2)]
    lander = _lander(hub, strategy("plugin"))
    rows = lander._queue_rows(8, {"tip": TIP, "version": "1.0.0"}, lander._version_rows(TIP))
    assert rows == [{"number": 7, "bump": True, "approved_release": True}]
    why = land.check_production_pr(strategy("plugin"), {"number": 8, "bump": False}, {"tip": TIP, "version": "1.0.0"}, rows)
    assert why is not None and "#7" in why


def test_manifest_path_follows_the_marketplace_source() -> None:
    """版数の読み先は `plugins.load` と同じくマーケットプレイスの `source` から求める（`./tools/kit` の配置も読む）。
    登録を読めない・名前の行が無ければ `plugins/<名前>` と根の候補へ戻す。"""
    s = section("plugin")
    s["release"]["plugin"] = "kit"
    kit = Strategy(s)
    files = {
        ".claude-plugin/marketplace.json": json.dumps({"plugins": [{"name": "kit", "source": "./tools/kit"}]}),
        "tools/kit/.claude-plugin/plugin.json": json.dumps({"version": "2.3.4"}),
    }
    assert land.manifest_paths(kit, files.get) == ["tools/kit/.claude-plugin/plugin.json"]
    assert land.read_version(land.version_rows(kit, files.get), files.get) == "2.3.4"
    assert land.manifest_paths(kit, {}.get) == ["plugins/kit/.claude-plugin/plugin.json", ".claude-plugin/plugin.json"]
    broken = {".claude-plugin/marketplace.json": "{"}
    assert land.manifest_paths(kit, broken.get)[0] == "plugins/kit/.claude-plugin/plugin.json"
    # 根に置いたプラグイン（source が `./`）は根の定義を読む（先頭にスラッシュを付けない）
    root = {
        ".claude-plugin/marketplace.json": json.dumps({"plugins": [{"name": "kit", "source": "./"}]}),
        ".claude-plugin/plugin.json": json.dumps({"version": "3.0.0"}),
    }
    assert land.manifest_paths(kit, root.get) == [".claude-plugin/plugin.json"]
    assert land.read_version(land.version_rows(kit, root.get), root.get) == "3.0.0"


def test_human_actor_is_left_to_the_human() -> None:
    """`merge.actor: human` なら NDF はマージしない。"""
    hub = _queue_hub(head=H1, approved=H1)
    done = _lander(hub, strategy("plugin", actor="human")).land(7)
    assert not done.merged and done.kind == land.HUMAN_KIND and hub.merged == []


# --- 証跡の書き手は 1 つ・今の head の答えがあれば問わない（#3671。テスト設計の 18） ---------------------------


def test_trail_writer_one_per_case_3671() -> None:
    s = section("plugin")
    assert trail.trail_writer(Strategy(s), False) == trail.WRITER_ANSWER
    assert trail.trail_writer(Strategy(s), True) == trail.WRITER_LAND
    s["merge"]["actor"] = "human"
    assert trail.trail_writer(Strategy(s), False) == trail.WRITER_LAND
    s["approval"]["via"] = "review"
    assert trail.trail_writer(Strategy(s), False) == trail.WRITER_GITHUB


def test_issue_3671_present_does_not_ask_again_when_the_head_has_an_answer() -> None:
    """#3671: 今の head への答えが既にあれば、承認資料を提示せず（問いを送らず）その判定を返す。古い head の答えなら問う。"""
    material = f"{approval_decide.HEAD_LINE} `{H1}`\n"
    hub = FakeHub(pulls={7: pull(head=H1)}, comments_of={7: [_trail("approved", H1)]})
    shown = trail.present(hub, 7, material)
    assert not shown.asked and shown.judgment.approved and len(hub.comments(7)) == 1
    hub = FakeHub(pulls={7: pull(head=H1)}, comments_of={7: [_trail("approved", H2)]})
    shown = trail.present(hub, 7, material)
    assert shown.asked and approval_decide.REQUEST_MARK in hub.comments(7)[-1].body


@pytest.mark.parametrize("writer", [trail.WRITER_LAND, trail.WRITER_GITHUB])
def test_issue_3671_only_the_answer_writer_writes_the_answer(writer: str) -> None:
    """#3671: 書き手が答えの手順でない宣言では、答えの証跡を書かない（書き手を 2 つにしない）。"""
    hub = FakeHub(pulls={7: pull(head=H1)})
    with pytest.raises(trail.TrailError, match=writer):
        trail.answer(hub, 7, "approved", trail.USER_BY, presented=H1, writer=writer)
    assert hub.comments(7) == []


def test_human_merge_record_is_written_once_by_the_land_writer() -> None:
    """I12: `merge.actor: human` の宣言では、人がマージした先端の記録を land が 1 回だけ書く。"""
    hub = FakeHub(pulls={7: pull(head=H1, merged=True)})
    lander = _lander(hub, strategy("plugin", actor="human"))
    assert lander.land(7).merged and lander.land(7).merged
    records = [c.body for c in hub.comments(7) if trail.HUMAN_MARK in c.body]
    assert len(records) == 1 and H1 in records[0]
