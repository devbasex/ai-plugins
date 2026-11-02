"""棚卸しの深さ・見直しの持ち主・調査・大整理の集計（#2315）。gh と claude -p は使わない（差し替える）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
sys.path.insert(0, str(SCRIPTS))
import run_owner  # noqa: E402
import upkeep_depth as D  # noqa: E402
import upkeep_overhaul as V  # noqa: E402
import upkeep_owner as O  # noqa: E402
import upkeep_research as R  # noqa: E402

PY = sys.executable


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def _decl(root, name, data):
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / name).write_text(json.dumps(data, ensure_ascii=False))


def _rows(*rows):
    return [{"event": "ran", **r} for r in rows]


def _upkeep(root, *args):
    p = subprocess.run([PY, str(SCRIPTS / "upkeep.py"), *args, "--root", str(root)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


# ---------------- 起動の判定 ----------------


def test_review_fires_on_count_and_counts_from_the_last_review(root):
    cfg = D.settings(root)
    rows = _rows({"depth": "diff"}, {"depth": "review"}, {"depth": "diff"}, {"depth": "diff"})
    assert D.due("review", rows, cfg, open_now=None)[0] == []
    rows += _rows({"depth": "diff"})
    assert D.due("review", rows, cfg, open_now=None)[0] == ["count"]
    # 回数は前の大整理からではなく前の見直しから数える
    assert D.diffs_since(rows, "overhaul") == 4


def test_review_fires_on_growth_carry_no_issue_and_manual(root):
    _decl(root, "backlog.json", {"depths": {"review": {"every": 99}}})
    cfg = D.settings(root)
    rows = _rows({"depth": "review", "open_after": 100}, {"depth": "diff", "values": {"carry": 101}})
    trig, values = D.due("review", rows, cfg, open_now=130, no_issue=True, manual=True)
    assert trig == ["manual", "no_issue", "open_growth", "carry"] and values["open_growth"] == 0.3
    rows[-1]["values"]["carry"] = 100  # 上限 10 の 10 倍ちょうどは当たらない
    assert D.due("review", rows, cfg, open_now=129)[0] == []


def test_overhaul_fires_on_count_streak_and_cause_share(root):
    cfg = D.settings(root)
    rows = _rows(*[{"depth": "diff"}] * 12)
    assert D.due("overhaul", rows, cfg, open_now=None)[0] == ["count"]
    rows = _rows(*[{"depth": "review", "created": 5, "closed": 1, "open_before": 10, "open_after": 12}] * 3)
    assert D.due("overhaul", rows, cfg, open_now=None)[0] == ["created_over_closed", "open_rising"]
    causes = [{"number": n, "type": "A"} for n in range(4)] + [{"number": n, "type": "B"} for n in range(4, 10)]
    causes += [{"number": n, "type": "G"} for n in range(10, 30)]  # 不具合でない型は分母に入れない
    trig, values = D.due("overhaul", [], cfg, open_now=None, causes=causes, types=D.DEFAULT_CAUSE_TYPES, open_numbers=set(range(30)))
    assert trig == ["cause_share"] and values["cause_share"] == {"type": "B", "share": 0.6}


def test_depth_due_records_not_due_and_returns_skip_code(root):
    code, out = _upkeep(root, "depth", "due", "review", "--open", "3")
    rows = [json.loads(x) for x in (root / ".ndf" / "backlog" / "depths.jsonl").read_text().splitlines()]
    assert code == 3 and rows[-1]["event"] == "not_due" and rows[-1]["trigger"] == []
    code, out = _upkeep(root, "depth", "due", "review", "--open", "3", "--manual")  # ループの外の単独の起動（受け入れ条件 5）
    assert code == 0 and out["metrics"]["trigger"] == ["manual"]


def test_depth_due_without_declarations_counts_only(root):
    # 宣言の無いリポジトリでも止まらず、回数でだけ起動する（受け入れ条件 24）
    for _ in range(3):
        code, _ = _upkeep(root, "depth", "record", "--depth", "diff")
        assert code == 0
    code, out = _upkeep(root, "depth", "due", "review", "--open", "3")
    assert code == 0 and out["metrics"]["trigger"] == ["count"]
    plan = R.plan(root, None)
    assert plan["items"] == [] and {s["aspect"] for s in plan["skipped_aspects"]} == set(R.ASPECTS)


def test_causes_record_rejects_unknown_types(root, tmp_path):
    f = tmp_path / "classes.json"
    f.write_text(json.dumps([{"number": 1, "type": "Z"}]))
    code, _ = _upkeep(root, "causes", "record", "--classes", str(f))
    assert code == 2
    f.write_text(json.dumps([{"number": 1, "type": "A", "sub": ["B"]}]))
    code, _ = _upkeep(root, "causes", "record", "--classes", str(f))
    assert code == 0 and json.loads((root / ".ndf" / "backlog" / "causes.jsonl").read_text())["type"] == "A"


# ---------------- 持ち主 ----------------


def test_only_one_of_two_concurrent_claims_wins(root):
    # 2 つのプロセスが同時に宣言を取ろうとしても片方だけが取れる（I1・受け入れ条件 8）
    args = [PY, str(SCRIPTS / "upkeep.py"), "owner", "claim", "--what", "research", "--pid", str(os.getpid()), "--root", str(root)]
    procs = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    codes = sorted(p.wait() for p in procs)
    assert codes == [0, 10]


def test_dead_or_expired_owner_is_taken_over_with_taken_from(root):
    run, _, _ = O.claim_review(root, "research", "l1", pid=os.getpid())
    path = O.owner_file(root)
    assert O.claim_review(root, "review")[0] is None  # 生きていて窓の内なら引き取らない（受け入れ条件 9a）
    rec = json.loads(path.read_text())
    rec["pid"] = 2**22 + 12345  # 同じホストで生きていない pid
    path.write_text(json.dumps(rec))
    run2, taken, _ = O.claim_review(root, "review", "l2", pid=os.getpid())
    assert run2 and taken == {"run": run, "loop": "l1", "seen_at": rec["seen_at"]}
    rec = json.loads(path.read_text())
    rec["seen_at"] = run_owner.utc_iso(time.time() - 901)  # 寿命の窓（既定 900 秒）を過ぎた
    path.write_text(json.dumps(rec))
    run3, taken, _ = O.claim_review(root, "review", pid=os.getpid())
    assert run3 and taken["run"] == run2
    assert O.touch(root, run2)  # 引き取られた側は印を書けない
    assert (
        O.release_review(root, run3) is None and O.claim_review(root, "review", pid=os.getpid())[1] is None
    )  # 終わった宣言は引き取りでない


def test_other_host_owner_is_judged_by_the_window_only(root):
    O.claim_review(root, "research", pid=os.getpid())
    path = O.owner_file(root)
    rec = json.loads(path.read_text())
    rec.update(host="other-host", pid=2**22 + 12345)
    path.write_text(json.dumps(rec))
    assert O.claim_review(root, "review")[0] is None


# ---------------- 調査 ----------------

MATERIALS = {
    "product": {"kind": "Web サービス", "users": "業務の担当者"},
    "needs": {
        "usage": [
            {
                "name": "pv",
                "command": "echo 'page=/report count=40 user=alice@example.com'",
                "scope": {"aggregate_only": True, "days": 30, "mask": [r"\S+@\S+"]},
            },
            {"name": "raw", "command": "echo rows", "scope": {"aggregate_only": False}},
        ]
    },
    "seeds": {"libraries": [{"name": "pytest", "ecosystem": "pypi", "alternatives": ["ward"]}]},
}


def test_plan_lists_items_from_the_declaration_only(root):
    _decl(root, "project.json", {"materials": MATERIALS})
    got = R.plan(root, None)
    assert got["items"] == [
        "needs.usage:pv",
        "seeds.library:pytest",
        "seeds.alternatives:pytest",
        "method.unused:usage",
        "method.hotspot:git",
        "method.failures:loop",
    ]
    skipped = {s["aspect"] for s in got["skipped_aspects"]}
    assert {"needs.market", "seeds.environment"} <= skipped and "needs.usage" in skipped  # 個票の行は読まない
    assert "変化" in R.prompt_of(R.plan(root, None)["by_id"]["seeds.library:pytest"], None)
    assert "競合" in R.prompt_of(R.plan(root, None)["by_id"]["seeds.alternatives:pytest"], None)


class _Fake:
    def __init__(self, data=None, cost=0.1):
        self.calls, self.data, self.cost = [], data, cost

    def __call__(self, root, prompt, timeout, tick, tools):
        self.calls.append((prompt, tools))
        data = self.data if self.data is not None else {"sources": [{"kind": "url", "value": "https://example.com"}], "findings": []}
        return {"ok": True, "data": data, "cost": self.cost, "seconds": 1.0}


def test_run_writes_only_sourced_items_and_masks_usage(root, tmp_path, monkeypatch):
    _decl(root, "project.json", {"materials": MATERIALS})
    fake = _Fake()
    monkeypatch.setattr(R, "call_llm", fake)
    m = R.run_research(root, str(tmp_path / "done.json"), None, 6, 2, lap=1)
    assert m["items"] == ["needs.usage:pv", "seeds.library:pytest"] and m["cost"] == 0.2
    prompt, tools = fake.calls[0]
    assert "count=40" in prompt and "alice@example.com" not in prompt and tools == "Read,Grep,Glob"
    assert fake.calls[1][1] == "WebFetch,WebSearch"
    rec = json.loads(R.item_path(root, "needs.usage:pv").read_text())
    assert rec["sources"] and rec["checked_at"] and rec["lap"] == 1
    assert R.plan(root, None)["todo"][0] == "seeds.alternatives:pytest"  # 次の起動は残りの項目から
    assert json.loads(O.owner_file(root).read_text())["finished_at"]


def test_run_discards_unsourced_or_over_budget_results(root, tmp_path, monkeypatch):
    _decl(root, "project.json", {"materials": MATERIALS})
    monkeypatch.setattr(R, "call_llm", _Fake(data={"sources": [], "findings": []}))
    assert R.run_research(root, str(tmp_path / "done.json"), None, 6, 3)["items"] == []
    monkeypatch.setattr(R, "call_llm", _Fake(cost=5))
    m = R.run_research(root, str(tmp_path / "done.json"), None, 6, 3)
    assert m["items"] == [] and not list((root / ".ndf" / "backlog" / "research").glob("*.item.json"))
    # 結果を捨てた呼び出しも、支払った費用を呼び出しの記録に残す（周回の調査費用はここから集める）
    calls = [json.loads(x) for x in R.calls_path(root).read_text().splitlines()]
    assert calls and not any(c["ok"] for c in calls) and sum(c["cost"] for c in calls) == m["cost"] + 0.3


def test_run_stops_a_call_after_the_grace_once_the_plan_ends(root, tmp_path, monkeypatch):
    # 調査中の tick でも待っていたプランの完了を見て、research.grace を過ぎた呼び出しを止める
    _decl(root, "project.json", {"materials": MATERIALS})
    done = tmp_path / "done.json"
    clock = {"t": 1000.0}
    monkeypatch.setattr(R.time, "time", lambda: clock["t"])

    def slow(root_, prompt, timeout, tick, tools):
        done.write_text("{}")
        tick()
        clock["t"] += 301
        tick()
        raise AssertionError("猶予を過ぎても止まらない")

    monkeypatch.setattr(R, "call_llm", slow)
    m = R.run_research(root, str(done), None, 6, 3)
    assert m["items"] == [] and "猶予" in m["stopped"]
    assert json.loads(O.owner_file(root).read_text())["finished_at"]  # 持ち主を手放す


def test_run_starts_nothing_after_the_plan_ends_or_without_a_slot(root, tmp_path, monkeypatch):
    _decl(root, "project.json", {"materials": MATERIALS})
    fake = _Fake()
    monkeypatch.setattr(R, "call_llm", fake)
    done = tmp_path / "done.json"
    done.write_text("{}")
    assert R.run_research(root, str(done), None, 6, 3)["items"] == [] and fake.calls == []
    # 空きが無い間は始めない（受け入れ条件 6a）。待っていたプランが終わったら抜ける
    plan = tmp_path / "p1.json"
    (tmp_path / "p1-state").mkdir()
    listing = tmp_path / "q.plans.json"
    listing.write_text(json.dumps({"plans": [str(plan)]}))
    done.unlink()
    m = R.run_research(root, str(done), str(listing), 1, 3, sleep=lambda s: done.write_text("{}"))
    assert m["items"] == [] and fake.calls == [] and m["stopped"] == "待っていたプランが終わった"


def test_run_does_nothing_when_another_owner_is_alive(root, tmp_path, monkeypatch):
    _decl(root, "project.json", {"materials": MATERIALS})
    O.claim_review(root, "review", pid=os.getpid())
    fake = _Fake()
    monkeypatch.setattr(R, "call_llm", fake)
    assert R.run_research(root, str(tmp_path / "done.json"), None, 6, 3)["reason"] == "not_owner" and fake.calls == []


def test_review_owner_researches_stale_items_without_releasing(root, monkeypatch):
    # 見直しの中では持ち主の実行のまま、前の見直しより古い項目と足りない項目だけを調べる（受け入れ条件 6）
    _decl(root, "project.json", {"materials": MATERIALS})
    run, _, _ = O.claim_review(root, "review", pid=os.getpid())
    fake = _Fake()
    monkeypatch.setattr(R, "call_llm", fake)
    assert R.run_research(root, None, None, 6, 1, owner=run)["items"] == ["needs.usage:pv"]
    assert R.run_research(root, None, None, 6, 9, owner=run, since="2000-01-01T00:00:00Z")["items"][0] == "seeds.library:pytest"
    assert R.run_research(root, None, None, 6, 9, owner=run, since="2999-01-01T00:00:00Z")["items"][0] == "needs.usage:pv"  # 古い項目
    assert not json.loads(O.owner_file(root).read_text())["finished_at"]
    assert R.run_research(root, None, None, 6, 1, owner="other")["reason"] == "not_owner"


# ---------------- 大整理の集計 ----------------

FIXTURE = HERE / "fixtures" / "overhaul-20261101.json"


def test_count_types_components_milestones_and_months():
    snap = {
        "open": [
            {"number": 1, "title": "supervise.py が落ちる", "body": "", "milestone": "28", "created_at": "2026-10-02T00:00:00Z"},
            {"number": 2, "title": "x", "body": "supervise.py の待ち", "milestone": None, "created_at": "2026-10-03T00:00:00Z"},
            {"number": 3, "title": "文書", "body": "", "milestone": "28", "created_at": "2026-09-03T00:00:00Z"},
        ],
        "closed": [{"number": 9, "created_at": "2026-10-01T00:00:00Z", "closed_at": "2026-10-05T00:00:00Z"}],
    }
    got = V.count_types(snap, [{"number": 1, "type": "A"}, {"number": 3, "type": "H"}], D.DEFAULT_CAUSE_TYPES)
    assert got["by_type"]["A"] == 1 and got["by_type"]["I"] == 1 and got["by_type"]["H"] == 1 and got["unclassified"] == 1
    assert got["defects"] == 2 and got["components"][0] == ("supervise.py", 2)
    assert got["milestones"]["28"] == {"A": 1, "H": 1}
    assert got["months"]["2026-10"] == {"created": 3, "closed": 1}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="固定の入力が無い")
def test_count_reads_the_20261101_snapshot():
    # 受け入れ条件 22: 2026-11-01 の未完了の一覧（固定の入力）から型ごとの件数を数える。分類は大整理の手順 1 でやり直す
    snap = json.loads(FIXTURE.read_text())
    got = V.count_types(snap, [], D.DEFAULT_CAUSE_TYPES)
    assert got["issues"] == len(snap["open"]) and got["by_type"]["I"] == got["issues"]
    assert got["components"] and got["months"]
