"""現状固定テスト: skill-stats のタイムライン構築と集計ロジック。

R6-001 のリファクタリング (extract_method) に先立ち、
build_timeline および aggregate_by_project の振る舞いを固定する。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "skill-stats.py"
)


@pytest.fixture(scope="module")
def skill_stats():
    spec = importlib.util.spec_from_file_location("skill_stats", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sample_skills():
    return [
        {
            "name": "refactoring",
            "qualified": "ndf:refactoring",
            "triggers": ["リファクタリング", "構造改善"],
            "triggers_source": "explicit",
            "dir": "refactoring",
        },
        {
            "name": "pr-review",
            "qualified": "ndf:pr-review",
            "triggers": ["レビュー"],
            "triggers_source": "explicit",
            "dir": "pr-review",
        },
    ]


def _write_jsonl(path: Path, events: list[dict]) -> Path:
    lines = [json.dumps(ev, ensure_ascii=False) for ev in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_build_timeline(skill_stats, tmp_path: Path):
    events = [
        {"type": "user", "cwd": "/work/my-project", "message": {"content": "リファクタリングをお願い"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ndf:refactoring"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": "<command-name>/ndf:pr-review</command-name>"
            },
        },
    ]
    path = _write_jsonl(tmp_path / "session.jsonl", events)
    timeline, project = skill_stats.build_timeline(
        path, {"refactoring", "pr-review"}
    )

    assert project == "my-project"
    assert timeline == [
        ("user", "リファクタリングをお願い"),
        ("skill", "ndf:refactoring"),
        ("slash", "ndf:pr-review"),
    ]


def test_aggregate_by_project_hit(skill_stats, sample_skills, tmp_path: Path):
    """trigger -> auto invocation で hits がカウントされる。"""
    events = [
        {"type": "user", "cwd": "/work/proj", "message": {"content": "コードの構造改善をしたい"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ndf:refactoring"},
                    }
                ]
            },
        },
    ]
    path = _write_jsonl(tmp_path / "t1.jsonl", events)
    result = skill_stats.aggregate_by_project([path], sample_skills)

    auto, explicit, trig_h, hits = result["proj"]
    assert auto["ndf:refactoring"] == 1
    assert explicit["ndf:refactoring"] == 0
    assert trig_h["ndf:refactoring"] == 1
    assert hits["ndf:refactoring"] == 1


def test_aggregate_by_project_window_closed_by_slash(
    skill_stats, sample_skills, tmp_path: Path
):
    """trigger -> slash で窓が閉じ、その後の auto invocation は hit にならない。"""
    events = [
        {"type": "user", "cwd": "/work/proj", "message": {"content": "リファクタリングして"}},
        {
            "type": "user",
            "message": {
                "content": "<command-name>/ndf:pr-review</command-name>"
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ndf:refactoring"},
                    }
                ]
            },
        },
    ]
    path = _write_jsonl(tmp_path / "t2.jsonl", events)
    result = skill_stats.aggregate_by_project([path], sample_skills)

    auto, explicit, trig_h, hits = result["proj"]
    assert auto["ndf:refactoring"] == 1
    assert explicit["ndf:pr-review"] == 1
    assert trig_h["ndf:refactoring"] == 1
    assert hits["ndf:refactoring"] == 0


def test_aggregate_by_project_trigger_only(
    skill_stats, sample_skills, tmp_path: Path
):
    """trigger のみで auto invocation が無い場合は hit にならない。"""
    events = [
        {"type": "user", "cwd": "/work/proj", "message": {"content": "リファクタリングが必要かも"}},
    ]
    path = _write_jsonl(tmp_path / "t3.jsonl", events)
    result = skill_stats.aggregate_by_project([path], sample_skills)

    auto, explicit, trig_h, hits = result["proj"]
    assert auto["ndf:refactoring"] == 0
    assert explicit["ndf:refactoring"] == 0
    assert trig_h["ndf:refactoring"] == 1
    assert hits["ndf:refactoring"] == 0


def test_aggregate_by_project_window_closed_by_user(
    skill_stats, sample_skills, tmp_path: Path
):
    """trigger -> 次の user メッセージで窓が閉じ、その後の auto invocation は hit にならない。"""
    events = [
        {"type": "user", "cwd": "/work/proj", "message": {"content": "リファクタリングして"}},
        {"type": "user", "message": {"content": "あ、やっぱりやめた"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ndf:refactoring"},
                    }
                ]
            },
        },
    ]
    path = _write_jsonl(tmp_path / "t4.jsonl", events)
    result = skill_stats.aggregate_by_project([path], sample_skills)

    auto, explicit, trig_h, hits = result["proj"]
    assert auto["ndf:refactoring"] == 1
    assert trig_h["ndf:refactoring"] == 1
    assert hits["ndf:refactoring"] == 0
