"""ステップの上限をプランの遷移から決める（#1909 #1676 #1623 の L1〜L5・1909-1〜3・1676-1・1623-3）。

雛形のプランは `supervise.py new` で書き出し、ステップの結果はハンドラーをスタブに替えて決める。遷移は
`Engine` の本物の決め方で流す（実機の claude もコマンドも起動しない）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from merged_lib.base_sync import RETURN_LIMIT  # noqa: E402
from supervise_lib import engine, limits  # noqa: E402
from supervise_lib.plan import ON_FAIL_ONLY  # noqa: E402
from supervise_lib.slow import SlowAction  # noqa: E402
from test_supervise_contract import cli, make_repo, new_argv  # noqa: E402

pytestmark = pytest.mark.usefixtures("issue_bodies")

RETRY = "retry"  # 遅れの見張りの再試行（SlowAction）をスタブで起こす印
DROP = ("記録", "branch", "起点", "実行の条件", "リポジトリ", "スプリント状態")  # 流すときに外の世界へ触る鍵


_PLANS: dict[str, dict] = {}


@pytest.fixture()
def plans(tmp_path_factory, issue_bodies) -> dict[str, dict]:
    """種別ごとに `new` で書き出したプラン（`<種別>` か `<種別>/<ファイル名>` → プラン）。同期とチェックの宣言つき。
    `new sprint` が課題の本文を読むため関数の単位の `issue_bodies` を使い、書き出しは 1 回だけにする。"""
    if _PLANS:
        return _PLANS
    tmp = tmp_path_factory.mktemp("plans")
    root = make_repo(tmp)
    decl = json.loads((root / ".ndf" / "supervise.json").read_text())
    decl["sync_checks"] = [{"name": "sync", "command": "true"}]
    (root / ".ndf" / "supervise.json").write_text(json.dumps(decl) + "\n")
    out = tmp / "out"
    out.mkdir()
    got: dict[str, dict] = {}
    for kind, argv in new_argv(root, out).items():
        p = cli(*argv, cwd=root)
        assert p.returncode == 0, (kind, p.stdout, p.stderr)
        target = Path(argv[argv.index("--out") + 1])
        for f in sorted(target.glob("*.json")) if target.is_dir() else [target]:
            plan = json.loads(f.read_text())
            if "steps" in plan:
                got[f"{kind}/{f.stem}" if target.is_dir() else kind] = plan
    _PLANS.update(got)
    return _PLANS


class _Fake:
    """run・work・drive・pr のスタブ。`decide(id)` の値を終了コードにする。RETRY なら遅れの再試行を起こす。"""

    def __init__(self, kind: str, decide) -> None:
        self.kind, self.decide = kind, decide

    def execute(self, ctx, step: dict):
        v = self.decide(step["id"], step)
        if v == RETRY:
            raise SlowAction("retry", "試験の遅れ")
        ctx.state.cur.update(exit=v, text=f"{step['id']} の出力 exit={v}", seconds=0.0)
        if v and step.get("on_fail_only"):  # 落ちたのは on_fail へ回す種類の失敗（pr の push 前の検査の拒否）
            ctx.state.cur[ON_FAIL_ONLY[step["on_fail_only"]]] = True
        return v == 0, ""


class _FakeJudge:
    kind = "judge"

    def __init__(self, decide) -> None:
        self.decide = decide

    def execute(self, ctx, step: dict) -> dict:
        d = {"decision": self.decide(step["id"], step)}
        ctx.state.cur.update(exit=0, text=json.dumps(d), seconds=0.0)
        return d


def drive(tmp: Path, plan: dict, decide, start: str | None = None) -> tuple[list[str], str, engine.Engine]:
    """プランをスタブの結果で流し、(流したステップの id, 報告, Engine) を返す。"""
    plan = {k: v for k, v in json.loads(json.dumps(plan)).items() if k not in DROP}
    plan["作業場所"] = str(tmp)
    eng = engine.Engine(plan, tmp / "state")
    for kind in ("run", "work", "drive", "pr"):
        eng.handlers[kind] = _Fake(kind, decide)
    eng.handlers["judge"] = _FakeJudge(decide)
    text = eng.run(start)
    return [e["id"] for e in eng.state.log], text, eng


def scripted(route: list[tuple[str, object]]):
    """経路（(ステップ, 終了コードか judge の答え)）のとおりに答える。経路と違うステップが来たら落とす。"""
    it = iter(route)

    def decide(sid, step):
        want, value = next(it)
        assert sid == want, f"経路と違うステップ: {sid}（期待 {want}）"
        return value

    return decide


def reason_of(text: str) -> str:
    return next((ln for ln in text.splitlines() if ln.startswith("- 理由: ")), "")


# --- L1・I1: 雛形ごとに、修正のループのそれぞれを 3 回ずつ通しても上限で止まらない ---


def test_every_template_plan_gets_through_each_fix_loop_three_times(plans, tmp_path):
    for name, plan in plans.items():
        fixes, _ = limits.loops(plan)
        failing = {k.split("→")[0] for k in fixes}
        failing = {c for c in failing if any(s["id"] == c and s.get("on_fail") for s in plan["steps"])}
        visits: dict[str, int] = {}

        def decide(sid, step, failing=failing, visits=visits):
            visits[sid] = visits.get(sid, 0) + 1
            if step["type"] == "judge":
                choices = step.get("choices") or []
                return "fix" if "fix" in choices else "gate" if "gate" in choices else "next"
            return 1 if sid in failing and visits[sid] <= limits.FIX_ROUNDS else 0

        ran, text, _ = drive(tmp_path / name.replace("/", "-"), plan, decide)
        assert "上限" not in reason_of(text), (name, plan["上限"], len(ran), reason_of(text))
        assert "結果: 止まった" not in text, (name, reason_of(text))
        for c in failing:
            assert ran.count(c) > limits.FIX_ROUNDS, (name, c)  # 3 回落ちてから通った


def test_the_longest_path_equals_the_longest_simple_path_in_every_template(plans):
    """決定 2 の前提: 並びで後ろへ行く遷移だけの最長が、すべての単純な経路を数えた最長と一致する。"""
    for name, plan in plans.items():
        steps = plan["steps"]
        ids = {s["id"] for s in steps}
        graph = {s["id"]: [d for _, d, _ in limits._targets(steps, s) if d in ids] for s in steps}
        best = 0
        stack = [(steps[0]["id"], (steps[0]["id"],))]
        while stack:
            sid, path = stack.pop()
            best = max(best, len(path))
            stack += [(d, (*path, d)) for d in graph[sid] if d not in path]
        assert limits.longest_path(plan) == best, name


def test_the_return_limit_fits_in_the_rounds_the_limit_counts():
    """戻りの輪は FIX_ROUNDS 回として上限に入る。base-return が許す戻りの回数がそれを超えると、戻りきる前に上限で止まる。"""
    assert RETURN_LIMIT <= limits.FIX_ROUNDS


# --- L2・I2: ステップを足すと書き出しの計算が上限を増やす ---


def test_a_step_added_inside_a_fix_loop_raises_the_limit(plans):
    plan = json.loads(json.dumps(plans["impl"]))
    before = limits.step_limit(plan)
    steps = plan["steps"]
    i = next(i for i, s in enumerate(steps) if s["id"] == "test-limited")
    steps.insert(i + 1, {"id": "lint-limited", "type": "run", "cmd": "true", "next": steps[i]["next"]})
    steps[i]["next"] = "lint-limited"
    plan["上限"] = 20  # 雛形が数を書いていても、書き出しは計算の値で置き換える
    assert limits.fill(plan)["上限"] >= before + 1 + limits.FIX_ROUNDS
    assert list(plan).index("上限") == list(plan).index("steps") - 1


def test_templates_do_not_keep_a_fixed_limit(plans):
    for name, plan in plans.items():
        assert plan["上限"] == limits.step_limit(plan), name


# --- L3・I5: 上限がループを通らない最長の経路より短ければ流さない ---


def test_run_stops_before_any_step_when_the_limit_is_shorter_than_the_longest_path(tmp_path):
    steps = [{"id": i, "type": "run", "cmd": "true"} for i in ("a", "b", "c")]
    steps[-1]["next"] = "end"
    ran, text, eng = drive(tmp_path / "x", {"フェーズ": "試験", "課題": [1], "上限": 2, "steps": steps}, lambda sid, step: 0)
    assert ran == [] and not list(eng.state.dir.glob("*.out"))
    assert "結果: 止まった" in text and "上限 2 がループを通らない最長の経路 3 本（a から）より短い" in text
    ran, text, _ = drive(tmp_path / "y", {"フェーズ": "試験", "課題": [1], "上限": 2, "steps": steps}, lambda sid, step: 0, start="b")
    assert ran == ["b", "c"] and "結果: 完了" in text


def test_run_does_not_rewrite_the_plan_file(tmp_path, plans):
    """I3: run は書き済みのプランの `上限` を書き換えない。"""
    path = tmp_path / "plan.json"
    plan = {
        "フェーズ": "試験",
        "課題": [1],
        "作業場所": str(tmp_path),
        "上限": 9,
        "steps": [{"id": "a", "type": "run", "cmd": "true", "next": "end"}],
    }
    path.write_text(json.dumps(plan, ensure_ascii=False))
    before = path.read_text()
    p = cli("run", str(path), cwd=tmp_path)
    assert "結果: 完了" in p.stdout and path.read_text() == before


# --- L4・I6: 回り続けるプランは上限で止まる。遅れの再試行は数えない ---


LOOP = [
    {"id": "t", "type": "run", "cmd": "false", "on_fail": "judge", "next": "end"},
    {"id": "judge", "type": "judge", "inputs": ["t"], "question": "直すか", "choices": ["fix", "stop"]},
    {"id": "fix", "type": "work", "kind": "修正", "inputs": ["t"], "prompt": "直す", "next": "t"},
]


def test_a_plan_that_keeps_looping_stops_at_the_limit(tmp_path):
    def decide(sid, step):
        return "fix" if step["type"] == "judge" else 1 if sid == "t" else 0

    ran, text, _ = drive(tmp_path, {"フェーズ": "試験", "課題": [1], "上限": 7, "steps": LOOP}, decide)
    assert len(ran) == 7 and "理由: ステップの数が上限 7 を超えた" in text


def test_slow_retries_are_not_counted_against_the_limit(tmp_path):
    seen: dict[str, int] = {}

    def decide(sid, step):
        seen[sid] = seen.get(sid, 0) + 1
        if sid == "fix" and seen[sid] == 1:
            return RETRY
        return "fix" if step["type"] == "judge" else 1 if sid == "t" else 0

    ran, text, _ = drive(tmp_path, {"フェーズ": "試験", "課題": [1], "上限": 7, "steps": LOOP}, decide)
    assert len(ran) == 8
    assert "理由: ステップの数が上限 7 を超えた（遅れの再試行 1 回は数えていない）" in text


# --- L5・I4: 知らない行き先 ---


def test_an_unknown_target_stops_new_and_run_without_raising(tmp_path):
    steps = [{"id": "a", "type": "run", "cmd": "true", "on_fail": "nowhere", "next": "end"}]
    plan = {"フェーズ": "試験", "課題": [1], "steps": steps}
    with pytest.raises(limits.LimitError, match="a の on_fail → nowhere"):
        limits.fill(plan)
    assert limits.fill_all([plan]) and "上限" not in plan
    ran, text, _ = drive(tmp_path, plan, lambda sid, step: 0)
    assert ran == [] and "ステップ a の on_fail の行き先が知らないステップ: nowhere" in text


# --- 1909-1〜3・1676-1・1623-3: 実測の経路 ---


def _route(*items):
    return [(i, 0) if isinstance(i, str) else i for i in items]


MATCH = ("design-tests", "design-specs", "design-match", "pr")


def test_1909_1_the_route_of_1833_reaches_merge(plans, tmp_path):
    route = _route(
        "impl", "base-sync", "sync", "test-limited", *MATCH, ("test-all", 1), ("judge", "fix"), "fix", "sync",
        "test-limited", *MATCH, "test-all", ("doc-lint", 1), "fix-doc", "doc-lint", "ready", "merge-gate", "merge",
    )  # fmt: skip
    ran, text, _ = drive(tmp_path, plans["sprint/6-impl-16"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 完了" in text


def test_1909_2_the_route_of_1702_reaches_merge(plans, tmp_path):
    fix = (("test-limited", 1), ("judge", "fix"), "fix", "sync")  # 修正の後は範囲テストの前のステップの先頭へ戻る（#1651）
    route = _route(
        "impl", "base-sync", "sync", *fix, *fix, *fix, "test-limited", *MATCH, "test-all", ("doc-lint", 1), "fix-doc", "doc-lint",
        "ready", "merge-gate", "merge",
    )  # fmt: skip
    ran, text, _ = drive(tmp_path, plans["sprint/6-impl-16"], scripted(route))
    assert len(ran) == 27 and ran == [r for r, _ in route] and "結果: 完了" in text


def test_1909_3_the_route_of_m1910_reaches_record(plans, tmp_path):
    route = _route(
        "collect", "pr", "assess", "refactor", "review", "base-sync", ("test-all", 1), ("judge", "fix"), "fix", "test-all",
        "ready", "merge-gate", "merge", "record",
    )  # fmt: skip
    ran, text, _ = drive(tmp_path, plans["sprint/7-check"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 完了" in text


DESIGN_ROUTE = (
    "glossary", ("requirements-check", 1), "requirements", "design", "pr", "sync-body", "review", "sync-review",
    "requirements-recheck", ("glossary-check", 1), "fix-glossary", "glossary-recheck", "push-glossary", "ready", ("gate", "gate"),
)  # fmt: skip


def _with_retries(route: tuple, *sids: str) -> list:
    out = []
    for item in route:
        if (item if isinstance(item, str) else item[0]) in sids:
            out.append((item if isinstance(item, str) else item[0], RETRY))
        out.append(item)
    return _route(*out)


def test_1676_1_the_design_route_with_a_retry_reaches_the_gate(plans, tmp_path):
    route = _with_retries(DESIGN_ROUTE, "design")
    ran, text, _ = drive(tmp_path, plans["sprint/1-design-16"], scripted(route))
    assert ran == [r for r, _ in route] and "結果: 関門" in text


def test_1623_3_two_slow_retries_do_not_use_up_the_limit(plans, tmp_path):
    route = _with_retries(DESIGN_ROUTE, "requirements", "design")
    ran, text, _ = drive(tmp_path, plans["sprint/1-design-16"], scripted(route))
    assert len(ran) == len(DESIGN_ROUTE) + 2 > plans["sprint/1-design-16"]["上限"]
    assert "結果: 関門" in text
