"""lib/delivery.py: マージの宛先の判定とリリースの経路（#1336）。

仮のリポジトリに 4 つのサンプルと ai-plugins の宣言を置き、判定の表と経路の表を確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import delivery  # noqa: E402

FORMS = ("package-plugin",)

SAMPLES = {
    "carmo-system-console": (
        {"base_branch": "main", "production_branch": "main"},
        [{"target": "本番（ECS）", "kind": "auto", "trigger": "CodePipeline", "branch": "main", "versioned": False}],
        None,
    ),
    "project-trygroup-prd": (
        {"base_branch": "develop", "production_branch": "main"},
        [
            {"target": "stg", "kind": "auto", "trigger": "develop へのマージ", "branch": "develop", "versioned": False},
            {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "branch": "main", "versioned": False},
        ],
        None,
    ),
    "carmo-contractors-app": (
        {"base_branch": "main", "production_branch": "main"},
        [
            {"target": "web（Amplify）", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False},
            {"target": "api", "kind": "manual", "trigger": "sam deploy --config-env prod", "versioned": False},
        ],
        None,
    ),
    "with-ai-dev": (
        {"base_branch": "main", "production_branch": "main"},
        [{"target": "本番", "kind": "manual", "trigger": "手でデプロイする", "versioned": False}],
        None,
    ),
    "ai-plugins": (
        {"base_branch": "develop", "production_branch": "main"},
        [{"target": "ndf プラグイン", "kind": "manual", "trigger": "release の工程", "branch": "main", "versioned": True}],
        {"form": "package-plugin", "plugin": "ndf"},
    ),
}


def make_repo(root: Path, wt=None, delivery_rows=..., release=None, raw_wt=None, raw_pj=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(root)], check=True)
    nd = root / ".ndf"
    nd.mkdir()
    if raw_wt is not None:
        (nd / "worktree.json").write_text(raw_wt)
    elif wt is not None:
        (nd / "worktree.json").write_text(json.dumps({"version": 1, **wt}))
    if raw_pj is not None:
        (nd / "project.json").write_text(raw_pj)
    elif delivery_rows is not ...:
        (nd / "project.json").write_text(json.dumps({"delivery": delivery_rows}, ensure_ascii=False))
    if release is not None:
        (nd / "supervise.json").write_text(json.dumps({"release": release}))
    return root


def sample(tmp_path, name):
    wt, rows, rel = SAMPLES[name]
    return delivery.load(make_repo(tmp_path / name, wt, rows, rel))


@pytest.mark.parametrize(
    "name,target,value",
    [
        ("carmo-system-console", "main", "production"),  # AC1
        ("project-trygroup-prd", "main", "production"),  # AC2
        ("project-trygroup-prd", "develop", "not-production"),  # AC2: 検証への反映は止めない
        ("carmo-contractors-app", "main", "production"),
        ("with-ai-dev", "main", "not-production"),  # manual だけ
        ("ai-plugins", "develop", "not-production"),  # AC6: 実装 PR は止めない
        ("ai-plugins", "main", "not-production"),  # 本番の工程のゲート 2 で止める（二重に止めない）
    ],
)
def test_judge_samples(tmp_path, name, target, value):
    v = delivery.judge_target(sample(tmp_path, name), target)
    assert v.value == value, v.reason
    assert v.items and v.items[0]["kind"] == "decl"


def test_judge_undetermined_rows(tmp_path):
    """I2: 宣言が壊れている・本番チャネルが決まらない・本番チャネル宛てで delivery が不明か無いときは止める側へ倒す。"""
    broken_wt = delivery.load(make_repo(tmp_path / "a", raw_wt="{", delivery_rows=[]))
    assert delivery.judge_target(broken_wt, "develop").value == "undetermined"
    assert "worktree.json" in delivery.judge_target(broken_wt, "develop").reason
    broken_pj = delivery.load(make_repo(tmp_path / "b", {"production_branch": "main"}, raw_pj="[1,"))
    assert delivery.judge_target(broken_pj, "main").value == "undetermined"
    assert "project.json" in delivery.judge_target(broken_pj, "main").reason
    no_prod = delivery.load(make_repo(tmp_path / "c", {}, []))  # origin/HEAD も main も無い
    assert delivery.judge_target(no_prod, "develop").value == "undetermined"
    unknown = delivery.load(make_repo(tmp_path / "d", {"production_branch": "main"}, {"unknown": "測れない"}))
    assert delivery.judge_target(unknown, "main").value == "undetermined"
    missing = delivery.load(make_repo(tmp_path / "e", {"production_branch": "main"}))
    assert delivery.judge_target(missing, "main").value == "undetermined"
    # I4: 本番チャネルが決まれば、それ以外の宛先は delivery が無くても止めない
    assert delivery.judge_target(missing, "develop").value == "not-production"


def test_judge_does_not_call_gh(tmp_path, monkeypatch):
    """I3: 判定は宣言と宛先だけで決まり、gh を呼ばない（呼べば印を残す偽の gh を PATH の先頭に置く）。"""
    wt, rows, rel = SAMPLES["carmo-system-console"]
    root = make_repo(tmp_path / "r", wt, rows, rel)
    bin_dir, mark = tmp_path / "bin", tmp_path / "gh-called"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {mark}\nexit 1\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    assert delivery.judge_target(delivery.load(root), "main").value == "production"
    assert not mark.exists()


def routes_of(tmp_path, name):
    return [(r.route, r.stage) for r in delivery.routes(sample(tmp_path, name), FORMS)]


def test_routes_samples(tmp_path):
    """AC11: サンプルの宣言から経路が決まる。"""
    assert routes_of(tmp_path, "carmo-system-console") == [("merge", "merged-by-check")]
    assert routes_of(tmp_path, "project-trygroup-prd") == [("merge", "merged-by-check"), ("merge", "promote")]
    assert routes_of(tmp_path, "carmo-contractors-app") == [("merge", "merged-by-check"), ("manual", "manual")]
    assert routes_of(tmp_path, "with-ai-dev") == [("manual", "manual")]
    assert routes_of(tmp_path, "ai-plugins") == [("template", "template")]


def test_routes_fallbacks(tmp_path):
    """I5: release.form が先に効き、どちらも無ければ manual と理由。[] は none。"""
    none = delivery.routes(delivery.load(make_repo(tmp_path / "n", {"production_branch": "main"}, [])), FORMS)
    assert [(r.route, r.stage) for r in none] == [("none", "none")]
    missing = delivery.routes(delivery.load(make_repo(tmp_path / "m", {"production_branch": "main"})), FORMS)
    assert [(r.route, r.stage) for r in missing] == [("manual", "manual")] and "delivery" in missing[0].note
    unknown = delivery.routes(delivery.load(make_repo(tmp_path / "u", {"production_branch": "main"}, {"unknown": "x"})), FORMS)
    assert unknown[0].stage == "manual" and "不明" in unknown[0].note
    other = delivery.routes(delivery.load(make_repo(tmp_path / "o", {}, [], {"form": "service"})), FORMS)
    assert [(r.route, r.stage) for r in other] == [("template", "manual")]
    assert delivery.needs_version(other) is False
    assert delivery.needs_version(delivery.routes(sample(tmp_path, "ai-plugins"), FORMS)) is True
