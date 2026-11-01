"""配布の版を流す時点に決める（#2164）: 版の判定（lib/release_version.py）とキューの版の差し込み（queue_fill.fill_release_version）。

一時の bare の origin にベースブランチ develop・本番のブランチ main・タグ `ndf--v<版>` を作り、プランを作った後に
別のスプリントが版を出した状況を、origin へ版を送って再現する。自分の配布のマージ済みの PR の照会は差し替える。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import release_version as rv  # noqa: E402
from supervise_lib import queue, queue_fill  # noqa: E402
from supervise_lib.paths import state_dir_of  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def obs(base, latest=None, prod=None, tags=None) -> rv.Observed:
    tags = tags if tags is not None else ([latest] if latest else [])
    return rv.Observed("ndf", "origin/develop", base, "origin/main", prod, latest, tags)


# ---- 版の判定 ----


@pytest.mark.parametrize(
    ("base", "latest", "prod", "level", "want"),
    [
        ("10.27.5", "10.27.5", "10.27.5", "patch", "10.27.6-dev.1"),  # 受け入れ条件 2 の例 1
        ("10.27.6-dev.1", "10.27.5", "10.27.5", "patch", "10.27.6-dev.2"),  # 例 2（10.27.6 のタグが無い）
        ("10.27.6", "10.27.6", "10.27.6", "patch", "10.27.7-dev.1"),  # 受け入れ条件 3（別のスプリントが 10.27.6 を出した）
        ("10.27.6-dev.1", "10.27.6", "10.27.6", "patch", "10.27.7-dev.1"),  # ベースブランチの系列のタグがもうある
        ("10.27.5", "10.27.5", "10.28.0", "patch", "10.28.1-dev.1"),  # 本番のブランチが先にある
        ("10.27.5", "10.27.5", "10.27.5", "minor", "10.28.0-dev.1"),
    ],
)
def test_decide_dev_takes_the_next_free_version_above_the_base(base, latest, prod, level, want):
    d = rv.decide_dev(obs(base, latest, prod), level)
    assert d.version == want and d.base == want.split("-")[0]
    assert rv.versions.compare_versions(d.version, base) > 0  # I2


def test_decide_dev_does_not_decide_when_something_is_unreadable():
    o = obs("10.27.5", "10.27.5")
    o.unreadable = ["origin/main"]
    assert rv.decide_dev(o).version is None and rv.decide_dev(o).unreadable == ["origin/main"]


def test_taken_compares_dev_versions_with_the_base_and_releases_with_tags():
    o = obs("10.27.5", "10.27.5", "10.27.5")
    assert rv.taken(o, "10.27.5-dev.1").existing == "10.27.5"
    assert rv.taken(o, "10.27.5-dev.1").next_free == "10.27.6-dev.1"
    assert not rv.taken(o, "10.27.6-dev.1").is_taken
    assert rv.taken(o, "10.27.5").existing == "ndf--v10.27.5" and rv.taken(o, "10.27.5").next_free == "10.27.6"
    assert not rv.taken(o, "10.27.6").is_taken


# ---- 一時の origin ----


class World:
    """bare の origin と、版を送る作業用の clone。`repo` はプランのリポジトリ（版を読む側）。"""

    def __init__(self, tmp: Path, version: str = "10.27.5"):
        self.origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(self.origin)], check=True)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "develop")
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.repo, "config", k, v)
        (self.repo / ".ndf").mkdir()
        (self.repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
        git(self.repo, "remote", "add", "origin", str(self.origin))
        self.publish(version, "develop", "main", tag=True)

    def publish(self, version: str, *branches: str, tag: bool = False) -> None:
        """別のスプリントの配布を模す: plugin.json を version にしたコミットを branches へ送り、tag なら正式版のタグも送る。"""
        f = self.repo / "plugins" / "ndf" / ".claude-plugin" / "plugin.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": version}))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", version)
        for b in branches:
            git(self.repo, "push", "-q", "-f", "origin", f"HEAD:refs/heads/{b}")
        if tag:
            git(self.repo, "tag", f"ndf--v{version}")
            git(self.repo, "push", "-q", "origin", f"ndf--v{version}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    return World(tmp_path)


def write_plan(tmp: Path, world: World, name: str = "7-release", channel: str = "dev", explicit=None, series=None, level="patch") -> Path:
    v = explicit or "{release_version}"
    base = explicit.split("-")[0] if explicit else "{release_base}"
    plan = {
        "フェーズ": "配布",
        "作業場所": f"{world.repo}/.worktrees/release/v{v}",
        "branch": f"release/v{v}",
        "起点": "origin/develop",
        "版": {"プラグイン": "ndf", "チャネル": channel, "上げ幅": level, "明示": explicit, "系列": series},
        "リポジトリ": str(world.repo),
        "steps": [
            {"id": "bump", "type": "run", "cmd": f"bump --to {v}"},
            {"id": "facts", "type": "run", "cmd": f"approval-facts --version {base} --approval issues/approval-ndf-v{base}.md"},
        ],
    }
    d = tmp / "plans"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(plan, ensure_ascii=False))
    return p


def record(plan: Path) -> dict:
    return json.loads((state_dir_of(str(plan.absolute())) / queue_fill.VERSION_RECORD).read_text())


def own(monkeypatch, answer):
    calls = []

    def fake(repo, branch, base):
        calls.append(branch)
        return answer

    monkeypatch.setattr(rv, "own_released", fake)
    return calls


def fill(plan: Path, items=None):
    return queue_fill.fill_release_version(str(plan), items or [])


# ---- 開発版 ----


def test_dev_plan_takes_the_version_that_is_free_when_it_runs(tmp_path, world):
    """受け入れ条件 3: 作った後に別のスプリントが 10.27.6 を本番へ出しても、作り直さずに 10.27.7-dev.1 で埋まる。"""
    plan = write_plan(tmp_path, world, level="patch")
    world.publish("10.27.6", "develop", "main", tag=True)
    assert fill(plan) == (None, None)
    got = json.loads(plan.read_text())
    assert got["branch"] == "release/v10.27.7-dev.1" and got["作業場所"].endswith("/release/v10.27.7-dev.1")
    assert got["steps"][0]["cmd"] == "bump --to 10.27.7-dev.1"  # 受け入れ条件 9: release の --version と同じ差し込み
    assert "approval-ndf-v10.27.7.md" in got["steps"][1]["cmd"]
    rec = record(plan)
    assert rec["version"] == "10.27.7-dev.1" and rec["base"] == "10.27.7" and rec["how"] == "decided" and rec["level"] == "patch"
    assert rec["observed"] == {
        "base_ref": "origin/develop",
        "base_version": "10.27.6",
        "production_ref": "origin/main",
        "production_version": "10.27.6",
        "latest_tag": "10.27.6",
    }
    assert rec["history"] == []


def test_explicit_dev_version_that_is_already_out_stops_before_the_plan_runs(tmp_path, world):
    """受け入れ条件 4・I5: 明示の版がベースブランチの版以下なら、記録を書かず、プランを書き換えずに止める。"""
    plan = write_plan(tmp_path, world, explicit="10.27.5-dev.1")
    before = plan.read_text()
    err, info = fill(plan)
    assert err and info["cause"] == "taken" and info["existing"] == "10.27.5" and info["next_free"] == "10.27.6-dev.1"
    assert plan.read_text() == before
    assert not (state_dir_of(str(plan.absolute())) / queue_fill.VERSION_RECORD).exists()


def test_explicit_dev_version_that_is_free_runs_unchanged(tmp_path, world):
    """受け入れ条件 10・I8: 明示の版がまだ出ていなければ、プランはそのまま流れる。"""
    plan = write_plan(tmp_path, world, explicit="10.27.6-dev.1")
    before = plan.read_text()
    assert fill(plan) == (None, None)
    assert plan.read_text() == before and record(plan)["how"] == "explicit"


def test_rerun_keeps_the_recorded_version(tmp_path, world, monkeypatch):
    """受け入れ条件 7・I3: 流し直しは記録の版を使う。ベースブランチがまだ小さければ照会せず、自分の配布の後なら照会して同じ版。"""
    plan = write_plan(tmp_path, world)
    fill(plan)
    rec = record(plan)
    calls = own(monkeypatch, True)
    assert fill(plan) == (None, None) and calls == []
    world.publish("10.27.6-dev.1", "develop")  # 自分の release/v10.27.6-dev.1 がマージされた
    assert fill(plan) == (None, None) and calls == ["release/v10.27.6-dev.1"]
    assert record(plan) == rec and json.loads(plan.read_text())["branch"] == "release/v10.27.6-dev.1"


def test_rerun_redecides_when_another_sprint_took_the_version_first(tmp_path, world, monkeypatch):
    """決定 8・I9: 記録の版が自分の配布のマージより前に別のスプリントに出ていたら、写しから次の版で埋め直す。"""
    plan = write_plan(tmp_path, world)
    fill(plan)
    sd = state_dir_of(str(plan.absolute()))
    (sd / "state.json").write_text('{"log": [{"id": "bump", "exit": 3}]}')
    world.publish("10.27.6", "develop", "main", tag=True)
    own(monkeypatch, False)
    assert fill(plan) == (None, None)
    got = json.loads(plan.read_text())
    assert got["branch"] == "release/v10.27.7-dev.1" and got["steps"][0]["cmd"] == "bump --to 10.27.7-dev.1"
    rec = record(plan)
    assert rec["version"] == "10.27.7-dev.1"
    [h] = rec["history"]
    assert (h["from"], h["to"], h["reason"], h["existing"]) == ("10.27.6-dev.1", "10.27.7-dev.1", "taken", "10.27.6")
    assert h["left"]["branch"] == "release/v10.27.6-dev.1" and h["left"]["作業場所"].endswith("/release/v10.27.6-dev.1")
    assert (sd / "superseded" / "v10.27.6-dev.1" / "state.json").is_file() and not (sd / "state.json").exists()


def test_redecide_interrupted_after_the_record_refills_once(tmp_path, world, monkeypatch):
    """決定 8（落ちたときの戻り）: 記録を書き換えた後、プランを書き直す前に落ちても、流し直しで同じ版に埋まり履歴は 1 件。"""
    plan = write_plan(tmp_path, world)
    fill(plan)
    world.publish("10.27.6", "develop", "main", tag=True)
    own(monkeypatch, False)
    real = queue_fill._Fill.ensure_filled

    def crash(self, rec):
        raise RuntimeError("落ちた")

    monkeypatch.setattr(queue_fill._Fill, "ensure_filled", crash)
    with pytest.raises(RuntimeError):
        fill(plan)
    assert json.loads(plan.read_text())["branch"] == "release/v10.27.6-dev.1"
    monkeypatch.setattr(queue_fill._Fill, "ensure_filled", real)
    assert fill(plan) == (None, None)
    assert json.loads(plan.read_text())["branch"] == "release/v10.27.7-dev.1" and len(record(plan)["history"]) == 1


def test_explicit_record_is_not_redecided(tmp_path, world, monkeypatch):
    """決定 8・受け入れ条件 4: 明示の版の記録は、別のスプリントに出ていても決め直さずに止める。"""
    plan = write_plan(tmp_path, world, explicit="10.27.6-dev.1")
    fill(plan)
    world.publish("10.27.6", "develop", "main", tag=True)
    own(monkeypatch, False)
    err, info = fill(plan)
    assert err and info["cause"] == "taken" and record(plan)["version"] == "10.27.6-dev.1" and record(plan)["history"] == []


def test_unreadable_origin_stops_without_a_record(tmp_path, world, monkeypatch):
    """受け入れ条件 8・I6: origin を読めなければ記録を書かずに止め、読めなかったものを載せる。記録がある流し直しも変えない。"""
    plan = write_plan(tmp_path, world)
    git(world.repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    err, info = fill(plan)
    assert err and info["cause"] == "unreadable" and "origin" in info["unreadable"]
    assert not (state_dir_of(str(plan.absolute())) / queue_fill.VERSION_RECORD).exists()
    git(world.repo, "remote", "set-url", "origin", str(world.origin))
    fill(plan)
    rec, text = record(plan), plan.read_text()
    world.publish("10.27.6", "develop", "main", tag=True)
    own(monkeypatch, None)  # 自分の配布の照会ができない
    err, info = fill(plan)
    assert info["cause"] == "unreadable" and info["unreadable"] == ["pr"] and record(plan) == rec and plan.read_text() == text


def test_plan_without_the_version_field_is_left_alone(tmp_path, world):
    """非機能（移行性）: 「版」の欄を持たない、この変更より前のプランには触らない。"""
    plan = write_plan(tmp_path, world, explicit="10.27.5-dev.1")
    body = json.loads(plan.read_text())
    del body["版"]
    plan.write_text(json.dumps(body))
    before = plan.read_text()
    assert fill(plan) == (None, None) and plan.read_text() == before


# ---- 本番 ----


def dev_and_prod(tmp_path, world):
    dev = write_plan(tmp_path, world)
    fill(dev)
    (state_dir_of(str(dev.absolute())) / "report.md").write_text("- 結果: 完了\n")
    prod = write_plan(tmp_path, world, "8-release-prod", "prod", series="release")
    return dev, prod


def test_prod_plan_uses_the_base_of_the_dev_record(tmp_path, world):
    """受け入れ条件 6・I4: 本番は開発版の記録の base で埋まる。"""
    _, prod = dev_and_prod(tmp_path, world)
    assert fill(prod) == (None, None)
    assert json.loads(prod.read_text())["branch"] == "release/v10.27.6" and record(prod)["how"] == "series"


def test_prod_plan_stops_when_the_release_is_already_tagged(tmp_path, world):
    """受け入れ条件 6: 本番の版のタグがすでにあれば、次に空いている版へ移らずに止め、既にある版と次に空いている版を載せる。"""
    _, prod = dev_and_prod(tmp_path, world)
    world.publish("10.27.6", "develop", "main", tag=True)
    before = prod.read_text()
    err, info = fill(prod)
    assert err and info["cause"] == "taken" and info["existing"] == "ndf--v10.27.6" and info["next_free"] == "10.27.7"
    assert prod.read_text() == before


def test_prod_plan_without_the_dev_record_stops(tmp_path, world):
    prod = write_plan(tmp_path, world, "8-release-prod", "prod", series="release")
    err, info = fill(prod)
    assert err and info["cause"] == "no_series"


def test_prod_follows_a_changed_dev_record_only_before_gate_2(tmp_path, world):
    """決定 8・I4: 本番の記録の後に開発版の記録の base が変わったら、承認ゲート 2 の前は追い、後は止める。"""
    dev, prod = dev_and_prod(tmp_path, world)
    fill(prod)
    rec_path = state_dir_of(str(dev.absolute())) / queue_fill.VERSION_RECORD
    rec = json.loads(rec_path.read_text())
    rec_path.write_text(json.dumps({**rec, "version": "10.27.7-dev.1", "base": "10.27.7"}))
    assert fill(prod) == (None, None)
    assert json.loads(prod.read_text())["branch"] == "release/v10.27.7" and record(prod)["history"][0]["reason"] == "series_changed"
    (state_dir_of(str(prod.absolute())) / "state.json").write_text('{"log": [{"id": "approve", "exit": 0}]}')
    rec_path.write_text(json.dumps({**rec, "version": "10.27.8-dev.1", "base": "10.27.8"}))
    err, info = fill(prod)
    assert err and info["cause"] == "series_changed" and json.loads(prod.read_text())["branch"] == "release/v10.27.7"


# ---- キューのステージ ----


def test_fill_stage_reports_the_version_of_a_plan_it_did_not_run(tmp_path, world):
    plan = write_plan(tmp_path, world, explicit="10.27.5-dev.1")
    skipped, runnable = queue.fill_stage(True, [str(plan)], [])
    assert runnable == [] and skipped[0]["unfilled"] is True and skipped[0]["version"]["cause"] == "taken"
