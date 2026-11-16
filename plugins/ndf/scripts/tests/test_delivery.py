"""lib/delivery.py: マージの宛先の判定とリリースの経路（#1336）。

4 つのサンプルと ai-plugins の値をブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`。#3672）で組み、
判定の表と経路の表を確かめる。行は `release.deliveries` に置く。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch_strategy  # noqa: E402
import delivery  # noqa: E402
import strategy_fixture as sf  # noqa: E402

FORMS = ("package-plugin",)

SAMPLES = {
    "carmo-system-console": (
        ("main", "main"),
        [{"target": "本番（ECS）", "kind": "auto", "trigger": "CodePipeline", "branch": "main", "versioned": False}],
        None,
    ),
    "project-trygroup-prd": (
        ("develop", "main"),
        [
            {"target": "stg", "kind": "auto", "trigger": "develop へのマージ", "branch": "develop", "versioned": False},
            {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "branch": "main", "versioned": False},
        ],
        None,
    ),
    "carmo-contractors-app": (
        ("main", "main"),
        [
            {"target": "web（Amplify）", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False},
            {"target": "api", "kind": "manual", "trigger": "sam deploy --config-env prod", "versioned": False},
        ],
        None,
    ),
    "with-ai-dev": (
        ("main", "main"),
        [{"target": "本番", "kind": "manual", "trigger": "手でデプロイする", "versioned": False}],
        None,
    ),
    "ai-plugins": (
        ("develop", "main"),
        [{"target": "ndf プラグイン", "kind": "manual", "trigger": "release の工程", "branch": "main", "versioned": True}],
        {"form": "package-plugin", "plugin": "ndf"},
    ),
}


def strategy(base: str, production: str) -> branch_strategy.Strategy:
    """ベースブランチと本番ブランチが同じなら trunk の、違えば develop→main の宣言。"""
    kind = "web-trunk" if base == production else "web-develop"
    return branch_strategy.Strategy(sf.section(kind, base=base, production=production))


def decl_of(branches=("main", "main"), rows=..., release=None, problems=None) -> delivery.DeliveryDecl:
    """宣言の `release` を、`release`（形）と `rows`（`deliveries`）だけの値に差し替えて組む（形の無い行の経路を確かめる）。"""
    rel = dict(release or {})
    if rows is not ...:
        rel["deliveries"] = rows
    return delivery.build(strategy(*branches), rel, problems=problems)


def sample(name):
    branches, rows, rel = SAMPLES[name]
    return decl_of(branches, rows, rel)


def make_repo(root: Path, rows=..., raw_pj=None) -> Path:
    """trunk（main）の宣言を置いたリポジトリ。`raw_pj` を渡せば `.ndf/project.json` をその文字のまま置く。"""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(root)], check=True)
    if raw_pj is not None:
        (root / ".ndf").mkdir()
        (root / ".ndf" / "project.json").write_text(raw_pj)
        return root
    sec = sf.section("web-trunk", base="main")
    if rows is not ...:
        sec["release"]["deliveries"] = rows
    sf.write_strategy(root, "web-trunk", base="main", release=sec["release"])
    return root


@pytest.mark.parametrize(
    "name,target,value",
    [
        ("carmo-system-console", "main", "production"),  # AC1
        ("project-trygroup-prd", "main", "production"),  # AC2
        ("project-trygroup-prd", "develop", "not-production"),  # AC2: 検証への反映は止めない
        ("carmo-contractors-app", "main", "production"),
        ("with-ai-dev", "main", "not-production"),  # manual だけ
        ("ai-plugins", "develop", "not-production"),  # AC6: 実装 PR は止めない
        ("ai-plugins", "main", "not-production"),  # 本番の工程のリリース承認ゲートで止める（二重に止めない）
    ],
)
def test_judge_samples(name, target, value):
    v = delivery.judge_target(sample(name), target)
    assert v.value == value, v.reason
    assert v.items and v.items[0]["kind"] == "decl"


def test_judge_undetermined_rows(tmp_path):
    """I2: 宣言が壊れている・無い・本番チャネル宛てで届け先が無いときは止める側へ倒す。"""
    broken = delivery.load_delivery(make_repo(tmp_path / "b", raw_pj="[1,"))
    assert delivery.judge_target(broken, "main").value == "undetermined"
    assert "project.json" in delivery.judge_target(broken, "main").reason
    absent = delivery.load_delivery(make_repo(tmp_path / "c", raw_pj="{}"))  # 宣言が無い（既定ブランチへ倒さない）
    assert delivery.judge_target(absent, "develop").value == "undetermined"
    missing = decl_of()
    assert delivery.judge_target(missing, "main").value == "undetermined"
    # I4: 本番チャネルが決まれば、それ以外の宛先は届け先が無くても止めない
    assert delivery.judge_target(missing, "develop").value == "not-production"


def test_judge_does_not_call_gh(tmp_path, monkeypatch):
    """I3: 判定は宣言と宛先だけで決まり、gh を呼ばない（呼べば印を残す偽の gh を PATH の先頭に置く）。"""
    root = make_repo(tmp_path / "r", SAMPLES["carmo-system-console"][1])
    bin_dir, mark = tmp_path / "bin", tmp_path / "gh-called"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {mark}\nexit 1\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert delivery.judge_target(delivery.load_delivery(root), "main").value == "production"
    assert not mark.exists()


def routes_of(name):
    return [(r.route, r.stage) for r in delivery.routes(sample(name), FORMS)]


def test_routes_samples():
    """AC11: サンプルの宣言から経路が決まる。"""
    assert routes_of("carmo-system-console") == [("merge", "merged-by-check")]
    assert routes_of("project-trygroup-prd") == [("merge", "merged-by-check"), ("merge", "promote")]
    assert routes_of("carmo-contractors-app") == [("merge", "merged-by-check"), ("manual", "manual")]
    assert routes_of("with-ai-dev") == [("manual", "manual")]
    assert routes_of("ai-plugins") == [("template", "template")]


def test_routes_fallbacks():
    """I5: release.form が先に効き、どちらも無ければ manual と理由。[] は none。"""
    none = delivery.routes(decl_of(rows=[]), FORMS)
    assert [(r.route, r.stage) for r in none] == [("none", "none")]
    missing = delivery.routes(decl_of(), FORMS)
    assert [(r.route, r.stage) for r in missing] == [("manual", "manual")] and "release.deliveries" in missing[0].note
    other = delivery.routes(decl_of(release={"form": "service"}), FORMS)
    assert [(r.route, r.stage) for r in other] == [("template", "manual")]
    assert delivery.needs_version(other) is False
    # 雛形の無い形でも、届け先の行があれば行ごとに決める（#3672 の決定 11。移した delivery の行の経路を保つ）
    rows = SAMPLES["carmo-system-console"][1]
    assert [(r.route, r.stage) for r in delivery.routes(decl_of(rows=rows, release={"form": "service"}), FORMS)] == [("merge", "merged-by-check")]
    assert delivery.needs_version(delivery.routes(sample("ai-plugins"), FORMS)) is True


# ---------- 手動反映の本番系（#1454） ----------

STG = {"target": "staging", "kind": "manual", "trigger": "cdk deploy Stg", "branch": "main", "versioned": False, "production": False}
PRD = {"target": "production", "kind": "manual", "trigger": "cdk deploy Prd", "branch": "main", "versioned": False, "production": True}
AUTO_MAIN = {"target": "web", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False}


def channel(rows=..., branches=("main", "main"), problems=None):
    return delivery.dev_channel(decl_of(branches, rows, problems=problems))


@pytest.mark.parametrize("rows", [[STG, PRD], [PRD], [{**AUTO_MAIN, "production": False}, PRD]])
def test_dev_channel_manual_production(rows):
    """AC1・I2: 起点と本番が同じでも、本番系の行がすべて手動なら手動反映の本番系。"""
    c = channel(rows)
    assert c.ok and c.value == delivery.MANUAL_PRODUCTION


@pytest.mark.parametrize(
    "rows, problems, word",
    [
        ([STG, PRD, AUTO_MAIN], None, "deliveries[2]"),  # AC2: 自動反映の本番チャネル
        ([STG, PRD, {**AUTO_MAIN, "production": True}], None, "deliveries[2]"),
        ([STG, {**PRD, "kind": "auto", "branch": "release"}], None, "自動で届く"),  # 表の 6
        (..., None, "無い"),  # AC3
        (..., ["project.json: 読めない"], "読めない"),
        ([STG, {**PRD, "production": None}], None, "production: true"),  # 本番系の行を宣言していない
    ],
)
def test_dev_channel_refuses_when_undetermined(rows, problems, word):
    c = channel(rows, problems=problems)
    assert not c.ok and "開発版のチャネルが無い" in c.reason and word in c.reason, c.reason


@pytest.mark.parametrize("rows, problems", [(..., None), (..., ["読めない"]), ([STG, PRD], None), ([AUTO_MAIN], None)])
def test_dev_channel_separate_branch_does_not_read_deliveries(rows, problems):
    """AC9・I1: 起点と本番が違えば届け先を読まずに separate-branch。"""
    c = channel(rows, ("develop", "main"), problems)
    assert c.value == delivery.SEPARATE_BRANCH


@pytest.mark.parametrize(
    "rows, value",
    [
        ([STG, PRD], "not-production"),  # AC5
        ([{**AUTO_MAIN, "production": False}, PRD], "not-production"),  # I4: 検証の環境への自動反映
        ([AUTO_MAIN], "production"),  # I4: production を書いていない行は今と同じ
    ],
)
def test_judge_target_reads_production(rows, value):
    assert delivery.judge_target(decl_of(rows=rows), "main").value == value


def test_dev_channel_does_not_call_gh(tmp_path, monkeypatch):
    """I3: 開発版のチャネルの判定も gh を呼ばない。"""
    root = make_repo(tmp_path / "r", [STG, PRD])
    bin_dir, mark = tmp_path / "bin", tmp_path / "gh-called"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {mark}\nexit 1\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert delivery.dev_channel(delivery.load_delivery(root)).ok
    assert not mark.exists()


def test_route_rows_carry_production_only_when_written():
    """I8: production を書いていない行の経路は今と同じ 5 キー。"""
    d = decl_of(rows=[STG, {k: v for k, v in PRD.items() if k != "production"}])
    rows = [r.as_dict() for r in delivery.routes(d, FORMS)]
    assert rows[0]["production"] is False and "production" not in rows[1]


# ---- リリースの組み方（#2616） ----

CHANNELS = ("develop", "main")
AUTO_BASE = {"kind": "auto", "channel": "base", "target": "stg"}
AUTO_PROD = {"kind": "auto", "channel": "production", "target": "prod"}
HAND = {"kind": "manual", "target": "prod", "trigger": "手で流す"}
PLUGIN = {"form": "package-plugin"}


@pytest.mark.parametrize(
    "rows, release, way, said",
    [
        ([HAND], PLUGIN, delivery.BUILD, ""),  # release.form の雛形があれば届け先を見ない
        ([AUTO_BASE, AUTO_PROD], None, delivery.SKIP, "stg はマージで反映する"),
        ([AUTO_PROD], None, delivery.SKIP, "リリースの Pull Request で届く"),
        ([], None, delivery.SKIP, "デプロイしない"),
        ([AUTO_BASE, HAND], None, delivery.REFUSE, "手で流す"),
        ([{**AUTO_PROD, "versioned": True}], None, delivery.SKIP, "リリースの Pull Request で届く"),
        ([{"kind": "auto", "branch": "other", "target": "x"}], None, delivery.REFUSE, "手で届ける"),
        (..., None, delivery.REFUSE, "release.deliveries も"),
        (..., {"form": "docker"}, delivery.REFUSE, "雛形が無い"),
    ],
)
def test_release_way_table(rows, release, way, said):
    got = delivery.release_way(decl_of(CHANNELS, rows, release), ("package-plugin",))
    assert got.way == way and said in got.reason
    if way == delivery.REFUSE:
        assert "branch_strategy" in got.reason


def test_release_way_refuses_a_broken_declaration_even_when_a_row_merges():
    got = delivery.release_way(decl_of(CHANNELS, [AUTO_BASE], problems=["project.json: 読めない"]), ())
    assert got.way == delivery.REFUSE and "宣言を読めない" in got.reason
