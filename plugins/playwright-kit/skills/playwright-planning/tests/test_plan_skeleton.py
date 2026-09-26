"""scripts/plan_skeleton.py の振る舞いをテストする。

雛形は同じ Skill の docs/（チェックリストと 03-test-techniques.md §11）から組み立てる。
テストは実物の docs を読み、観点 ID と必須技法が並ぶこと・判定の欄が空であること・
分類の上位 2 件の差が小さいときに exit 1 で人へ渡すことを確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "plan_skeleton.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


def write_classification(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "classifications.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def test_form_lists_checklist_ids_and_required_techniques():
    proc = run("--role", "form", "--data-types", "数値", "ファイル")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["status"] == "ok"
    assert out["role"] == "form"
    ids = [v["id"] for v in out["viewpoints"]]
    assert ids[:3] == ["FM1", "FM2", "FM3"]
    assert "FM15" in ids
    # 共通の観点も並ぶ
    assert "C1.1" in ids
    # 判定の欄は空
    assert all(v["judgement"] == "" for v in out["viewpoints"])
    rules = {(t["role"], t["data_type"]) for t in out["techniques"]}
    assert ("form", "*") in rules
    assert ("*", "数値") in rules
    assert ("*", "ファイル") in rules
    # 指定していないデータ型と別の role の行は入らない
    assert ("*", "日付") not in rules
    assert ("list", "*") not in rules


def test_markdown_skeleton_has_empty_judgement_column(tmp_path: Path):
    plan = tmp_path / "plan.md"
    proc = run("--role", "form", "--output", str(plan))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["output"] == str(plan)
    rows = [
        line for line in plan.read_text(encoding="utf-8").splitlines()
        if line.startswith("| FM1 ")
    ]
    assert len(rows) == 1
    cells = [c.strip() for c in rows[0].strip("|").split("|")]
    # 観点 ID / 観点 / 分類 / 判定 / 理由 のうち判定と理由は空
    assert cells[0] == "FM1"
    assert cells[-2:] == ["", ""]


def test_role_alias_maps_to_combined_checklist():
    proc = run("--role", "checkout", "--data-types", "金額")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["checklist"].endswith("checklist-cart-checkout.md")
    assert ("checkout", "金額") in {(t["role"], t["data_type"]) for t in out["techniques"]}


def test_classification_with_clear_winner_uses_primary_role(tmp_path: Path):
    path = write_classification(tmp_path, [{
        "url": "https://example.com/contact",
        "primary_role": "form",
        "primary_score": 2.3,
        "alternates": [{"role": "auth", "score": 0.5, "evidence": []}],
    }])
    proc = run("--classification", str(path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["role"] == "form"
    assert out["classification"]["gap"] == 1.8


def test_close_top_two_hands_off_to_human(tmp_path: Path):
    path = write_classification(tmp_path, [{
        "url": "https://example.com/signup",
        "primary_role": "form",
        "primary_score": 1.8,
        "alternates": [{"role": "auth", "score": 1.5, "evidence": []}],
    }])
    proc = run("--classification", str(path))
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["status"] == "needs_human"
    assert [c["role"] for c in out["candidates"]] == ["form", "auth"]
    assert "viewpoints" not in out


def test_margin_is_adjustable(tmp_path: Path):
    path = write_classification(tmp_path, [{
        "url": "https://example.com/signup",
        "primary_role": "form",
        "primary_score": 1.8,
        "alternates": [{"role": "auth", "score": 1.5, "evidence": []}],
    }])
    proc = run("--classification", str(path), "--margin", "0.2")
    assert proc.returncode == 0, proc.stderr


def test_unknown_primary_hands_off_to_human(tmp_path: Path):
    path = write_classification(tmp_path, [{
        "url": "https://example.com/x",
        "primary_role": "unknown",
        "primary_score": 0.0,
        "alternates": [],
    }])
    proc = run("--classification", str(path))
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["status"] == "needs_human"


def test_multiple_entries_need_url(tmp_path: Path):
    entries = [
        {"url": "https://example.com/a", "primary_role": "list",
         "primary_score": 2.5, "alternates": []},
        {"url": "https://example.com/b", "primary_role": "form",
         "primary_score": 2.0, "alternates": []},
    ]
    path = write_classification(tmp_path, entries)
    assert run("--classification", str(path)).returncode == 2
    proc = run("--classification", str(path), "--url", "https://example.com/b")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["role"] == "form"


def test_unknown_data_type_is_usage_error():
    proc = run("--role", "form", "--data-types", "色")
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "error"


def test_role_without_checklist_is_usage_error():
    proc = run("--role", "settings")
    assert proc.returncode == 2
