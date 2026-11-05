"""`upkeep.py report --depth` と、性質の違う 4 つのプロジェクトの材料の宣言（受け入れ条件 16。#2315）。gh と claude -p は使わない。

宣言は `fixtures/materials-four-projects.json`（ネット小説の制作・販売店の業務システム・社内 BI の基盤・このリポジトリ）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts"))
sys.path.insert(0, str(SCRIPTS))
import upkeep_owner as O  # noqa: E402
import upkeep_research as R  # noqa: E402
from project_lib.model import Materials  # noqa: E402

PY = sys.executable
FOUR = json.loads((HERE / "fixtures" / "materials-four-projects.json").read_text())
REPO = "o/r"

# 宣言ごとの調査の項目と飛ばした側面（期待値は宣言から手で数えた値）
EXPECTED = {
    "novel": (
        [
            "needs.market:カクヨム",
            "needs.market:小説家になろう",
            "seeds.library:faster-whisper",
            "seeds.alternatives:faster-whisper",
            "method.hotspot:git",
            "method.failures:loop",
        ],
        {"needs.usage", "seeds.environment", "method.unused"},
    ),
    "carul": (
        [
            "seeds.library:react",
            "seeds.alternatives:react",
            "seeds.environment:個人情報保護法",
            "method.hotspot:git",
            "method.failures:loop",
        ],
        {"needs.usage", "needs.market", "method.unused"},
    ),
    "bi-tools": (
        ["seeds.library:dbt-bigquery", "seeds.alternatives:dbt-bigquery", "method.hotspot:git", "method.failures:loop"],
        {"needs.usage", "needs.market", "seeds.environment", "method.unused"},
    ),
    "ai-plugins": (
        [
            "needs.usage:commit-kinds",
            "seeds.library:pytest",
            "seeds.alternatives:pytest",
            "method.unused:usage",
            "method.hotspot:git",
            "method.failures:loop",
        ],
        {"needs.market", "seeds.environment"},
    ),
}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def _project(root, materials):
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "materials": materials}, ensure_ascii=False))


def _state(tmp_path, name, data):
    d = tmp_path / "state" / REPO.replace("/", "--")
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(data, ensure_ascii=False))


def _upkeep(root, tmp_path, *args):
    env = {**os.environ, "NDF_UPKEEP_STATE_DIR": str(tmp_path / "state")}
    p = subprocess.run(
        [PY, str(SCRIPTS / "upkeep.py"), *args, "--root", str(root), "--repo", REPO], capture_output=True, text=True, env=env
    )
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def _candidates(tmp_path, open_=23):
    _state(tmp_path, "candidates.json", {"items": [], "metrics": {"candidates": 0, "by_route": {}, "deferred": [], "open": open_}})


def _record(root, item_id, run, n_sources):
    path = R.item_path(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    src = [{"kind": "url", "value": f"https://example.com/{i}"} for i in range(n_sources)]
    path.write_text(json.dumps({"id": item_id, "checked_at": "2026-11-02T00:00:00Z", "sources": src, "findings": [], "by": {"run": run}}))


@pytest.mark.parametrize("name", sorted(FOUR))
def test_four_declarations_give_items_skipped_aspects_and_the_report(name, repo, tmp_path):
    Materials.model_validate(FOUR[name])  # 宣言が材料の形を満たす
    _project(repo, FOUR[name])
    want_items, want_skipped = EXPECTED[name]
    code, due = _upkeep(repo, tmp_path, "depth", "due", "review", "--manual", "--open", "23")
    assert code == 0 and due["metrics"]["trigger"] == ["manual"]
    code, claim = _upkeep(repo, tmp_path, "owner", "claim", "--what", "review", "--pid", str(os.getpid()))
    run = claim["metrics"]["run"]
    code, plan = _upkeep(repo, tmp_path, "research", "plan")
    assert plan["metrics"]["items"] == want_items and {s["aspect"] for s in plan["metrics"]["skipped_aspects"]} == want_skipped
    _record(repo, want_items[0], run, 2)  # この実行が 1 項目を調べた
    _record(repo, want_items[1], "前の実行", 1)  # 前の実行の記録は「この回に調べた」に数えない
    _candidates(tmp_path)
    code, rep = _upkeep(repo, tmp_path, "report", "--depth", "review", "--order", "needs,seeds,methods")
    m = rep["metrics"]
    assert code == 0 and m["depth"] == "review" and m["run"] == run and m["trigger"] == ["manual"]
    assert m["order"] == ["needs", "seeds", "methods"] and m["open_before"] == 23 and m["open_after"] is None
    assert m["research"]["items"] == want_items
    assert m["research"]["researched"] == [{"id": want_items[0], "checked_at": "2026-11-02T00:00:00Z", "sources": 2}]
    assert m["research"]["sources"] == 2
    assert {s["aspect"] for s in m["skipped_aspects"]} == want_skipped
    # apply を打っていない回の閉じた・起票した・根本原因・戻すコマンドは取れない値（null）
    assert m["closed"] is None and m["created"] is None and m["root_cause"] is None and m["revert_command"] is None
    # 報告を深さの記録へ渡すと、調べた材料と飛ばした側面が行に写る
    out = tmp_path / "report.json"
    out.write_text(json.dumps(rep, ensure_ascii=False))
    code, rec = _upkeep(repo, tmp_path, "depth", "record", "--depth", "review", "--run", run, "--report", str(out))
    row = rec["items"][0]
    assert code == 0 and row["research"] == m["research"] and row["skipped_aspects"] == m["skipped_aspects"]
    assert row["trigger"] == ["manual"] and row["open_before"] == 23


def test_report_without_depth_keeps_its_output(repo, tmp_path):
    _project(repo, FOUR["novel"])
    _candidates(tmp_path)
    code, rep = _upkeep(repo, tmp_path, "report")
    assert code == 0 and not {"depth", "research", "skipped_aspects", "trigger"} & set(rep["metrics"])
    assert [i["name"] for i in rep["items"]] == ["対象"]


def test_report_depth_reads_apply_created_closed_root_cause_and_revert(repo, tmp_path):
    _project(repo, FOUR["bi-tools"])
    run, _, _ = O.claim_review(repo, "overhaul", pid=os.getpid())
    _candidates(tmp_path, open_=40)
    applied = {
        "metrics": {
            "verdicts": {"閉じてよい": 2},
            "applied": [3, 5],
            "closed": [5],
            "skipped_changed": [],
            "needs_approval": [],
            "failed": [],
            "pending": [],
            "partial": False,
            "returned": [],
            "round": {
                "applied": [3, 5],
                "closed": [3, 5],
                "waits": [],
                "runs": 2,
                "verdict_of": {"3": "閉じてよい", "5": "閉じてよい"},
                "result_of": {"3": "applied", "5": "applied"},
            },
            "created": [
                {"number": 101, "title": "原因 A をまとめて直す", "kind": "policy"},
                {"number": 102, "title": "x", "kind": "seeds"},
            ],
            "not_created": [{"title": "y", "reason": "上限 5 件を超えた"}],
            "revert_command": None,
        }
    }
    _state(tmp_path, "apply.json", applied)
    before = repo / ".ndf" / "backlog" / "overhaul" / run / "before.json"
    before.parent.mkdir(parents=True)
    before.write_text("[]")
    # 起動の判定より後に大整理の ran の行があれば、その判定は前の回のもの（起動した条件は取れない）
    rows = [{"depth": "overhaul", "event": "due", "trigger": ["count"]}, {"depth": "overhaul", "event": "ran"}]
    (repo / ".ndf" / "backlog" / "depths.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    code, rep = _upkeep(repo, tmp_path, "report", "--depth", "overhaul")
    m = rep["metrics"]
    assert m["closed"] == [3, 5] and m["created"] == [101, 102] and m["not_created"] == applied["metrics"]["not_created"]
    assert m["root_cause"] == {"number": 101, "title": "原因 A をまとめて直す"}
    assert m["revert_command"].endswith(f"revert --run {run}") and m["trigger"] is None and m["order"] is None
    assert m["research"]["researched"] == [] and m["research"]["sources"] == 0
