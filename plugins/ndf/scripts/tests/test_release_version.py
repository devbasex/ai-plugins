"""デプロイの版を流す時点に決める（#2164）: 版の判定（lib/release_version.py）とキューの版の差し込み（queue_fill.fill_release_version）。

一時の bare の origin にベースブランチ develop・本番のブランチ main・タグ `ndf--v<版>` を作り、プランを作った後に
別のスプリントが版を出した状況を、origin へ版を送って再現する。自分のデプロイのマージ済みの PR の照会は差し替える。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import release_version as rv  # noqa: E402
import sprint_loop as sl  # noqa: E402
import sprint_loop_record as sl_record  # noqa: E402
from supervise_lib import queue, queue_fill  # noqa: E402
from supervise_lib.paths import state_dir_of  # noqa: E402
import strategy_fixture  # noqa: E402


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
        strategy_fixture.declare(self.repo, "develop", "main", {"form": "package-plugin", "plugin": "ndf"})
        git(self.repo, "remote", "add", "origin", str(self.origin))
        self.publish(version, "develop", "main", tag=True)

    def publish(self, version: str, *branches: str, tag: bool = False) -> None:
        """別のスプリントのリリースを模す: plugin.json を version にしたコミットを branches へ送り、tag なら正式版のタグも送る。"""
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
        "フェーズ": "デプロイ",
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
    """受け入れ条件 7・I3: 流し直しは記録の版を使う。ベースブランチがまだ小さければ照会せず、自分のデプロイの後なら照会して同じ版。"""
    plan = write_plan(tmp_path, world)
    fill(plan)
    rec = record(plan)
    calls = own(monkeypatch, True)
    assert fill(plan) == (None, None) and calls == []
    world.publish("10.27.6-dev.1", "develop")  # 自分の release/v10.27.6-dev.1 がマージされた
    assert fill(plan) == (None, None) and calls == ["release/v10.27.6-dev.1"]
    assert record(plan) == rec and json.loads(plan.read_text())["branch"] == "release/v10.27.6-dev.1"


def test_rerun_redecides_when_another_sprint_took_the_version_first(tmp_path, world, monkeypatch):
    """決定 8・I9: 記録の版が自分のデプロイのマージより前に別のスプリントに出ていたら、写しから次の版で埋め直す。"""
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
    own(monkeypatch, None)  # 自分のデプロイの照会ができない
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
    # World の宣言はタグを作らない（release.tag が null）ため、出済みの版はタグの名前でなく本番ブランチの版として示す
    assert err and info["cause"] == "taken" and info["existing"] == "origin/main の版 10.27.6" and info["next_free"] == "10.27.7"
    assert prod.read_text() == before


def test_prod_plan_without_the_dev_record_stops(tmp_path, world):
    prod = write_plan(tmp_path, world, "8-release-prod", "prod", series="release")
    err, info = fill(prod)
    assert err and info["cause"] == "no_series"


@pytest.mark.parametrize(("level", "want"), [("patch", "10.27.77"), ("minor", "10.28.0"), ("major", "11.0.0")])
def test_decide_release_raises_the_largest_of_tag_production_and_base(level, want):
    """#3703 の決定 5: 系列の無い本番の正式版は、最新のタグ・本番ブランチ・ベースブランチの基底のうち最も大きいものを上げる。"""
    d = rv.decide_release(obs("10.27.76-dev.2", "10.27.75", "10.27.75"), level)
    assert (d.version, d.base, d.how) == (want, want, "decided")


def test_decide_release_does_not_decide_when_something_is_unreadable():
    o = obs("10.27.5", "10.27.5", "10.27.5")
    o.unreadable = ["origin"]
    assert rv.decide_release(o).version is None and rv.decide_release(o).unreadable == ["origin"]


def test_prod_plan_without_series_decides_once_from_origin(tmp_path, world):
    """#3703 の受け入れ条件 9・I7: trunk の本番のプラン（明示の版も系列も無い）は止まらずに origin から版を決めて記録し、
    2 回目の埋めは別の版が出ていても記録の版を使う。"""
    prod = write_plan(tmp_path, world, "8-release-prod", "prod")
    assert fill(prod) == (None, None)
    assert json.loads(prod.read_text())["branch"] == "release/v10.27.6"
    assert (record(prod)["version"], record(prod)["how"]) == ("10.27.6", "decided")
    world.publish("10.27.6", "develop", "main", tag=True)
    assert fill(prod) == (None, None) and record(prod)["version"] == "10.27.6"


def test_prod_plan_without_series_stops_when_origin_is_unreadable(tmp_path, world, monkeypatch):
    prod = write_plan(tmp_path, world, "8-release-prod", "prod")
    monkeypatch.setattr(rv, "fetch_origin", lambda repo: "boom")
    err, info = fill(prod)
    assert err and info["cause"] == "unreadable" and not (state_dir_of(str(prod.absolute())) / queue_fill.VERSION_RECORD).exists()


def test_prod_follows_a_changed_dev_record_only_before_gate_2(tmp_path, world):
    """決定 8・I4: 本番の記録の後に開発版の記録の base が変わったら、リリース承認ゲートの前は追い、後は止める。"""
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


@pytest.mark.parametrize(("code", "runs"), [(3, True), (0, False)])
def test_fill_stage_asks_the_condition_before_the_version(tmp_path, world, code, runs):
    """実行の条件がデプロイ不要なら、明示の版が既出でも版を照会せずに流す（engine が条件で完了にする）。要るなら従来どおり止める。"""
    plan = write_plan(tmp_path, world, explicit="10.27.5-dev.1")
    info = json.loads(plan.read_text())
    plan.write_text(json.dumps({**info, "実行の条件": {"cmd": f"exit {code}", "skip_code": 3}}, ensure_ascii=False))
    skipped, runnable = queue.fill_stage(True, [str(plan)], [])
    assert (runnable == [str(plan)] and skipped == []) if runs else (runnable == [] and skipped[0]["unfilled"] is True)


# ---- 開発ループが組むプラン（#2354） ----


def looped(tmp: Path, world: World, dev: str = "", prod: str = "", extra: tuple[str, ...] = ()) -> dict:
    """開発ループと同じコマンドで実物の開発版のプランを組み、ループの 1 回の行を返す（dev_plan・prod_plan を持つ）。"""
    ndf = world.repo / ".ndf"
    # リリースの形は World が宣言に置いた
    sup = json.loads((SCRIPTS.parents[2] / ".ndf" / "supervise.json").read_text())
    (ndf / "supervise.json").write_text(json.dumps(sup))
    auto = {"enabled": True, "modes": ["standard"], "verify": "true"}
    (ndf / "pace.json").write_text(json.dumps({"version": 1, "auto": auto}))
    d = tmp / "lap"
    d.mkdir()
    (d / "mvv.md").write_text("## Mission\n速く\n\n## Vision\n回る\n\n## Value\n実測\n")
    lap = {
        "versions": {"dev": dev, "prod": prod},
        "state_path": str(d / "state.json"),
        "sprint": "s1",
        "dir": str(d),
        "out": str(d / "sprint"),
        "mode": "standard",
        "milestone": "01 近い",
        "selection": [{"number": 11, "decision": sl_record.SELECTED}],
    }
    rec = {"name": "l1", "waiting": []}
    root = str(world.repo)
    gate = [sys.executable, str(sl.SPRINT_STATE), "gate", lap["state_path"], "MVV", "--what", "MVV を承認"]
    for cmd in (sl.sprint_state_init_argv(root, rec, lap, [11]), gate, sl.new_sprint_argv(root, rec, lap, list(extra))):
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr
    index = json.loads((Path(lap["out"]) / "sprint.json").read_text())["ステージ"]
    return {**lap, "dev_plan": sl.stage_plan(index, "開発版"), "prod_plan": sl.stage_plan(index, "本番")}


def version_field(lap: dict) -> dict:
    return json.loads(Path(lap["dev_plan"]).read_text())["版"]


def test_loop_plan_without_dev_holds_no_version_number(tmp_path, world):
    """受け入れ条件 1: --dev なしで組むとプランにも sprint.json にも版数が無い。"""
    lap = looped(tmp_path, world)
    assert version_field(lap)["明示"] is None
    for f in (lap["dev_plan"], lap["prod_plan"], Path(lap["out"]) / "sprint.json"):
        assert not re.search(r"\d+\.\d+\.\d+", Path(f).read_text())


def test_loop_plan_without_dev_takes_the_next_free_version(tmp_path, world):
    """受け入れ条件 3: 組んだ後に別のスプリントが版を出しても、作り直さずに次に空いている版で埋まる。"""
    lap = looped(tmp_path, world)
    world.publish("10.27.6", "develop", "main", tag=True)
    plan = Path(lap["dev_plan"])
    assert fill(plan) == (None, None)
    assert record(plan)["version"] == "10.27.7-dev.1" and record(plan)["how"] == "decided"
    assert queue_fill.recorded_version(lap["dev_plan"]) == "10.27.7-dev.1"


def test_loop_plan_without_dev_redecides_on_a_rerun(tmp_path, world, monkeypatch):
    """受け入れ条件 4: 記録の版を別のスプリントが先に出すと、ループの 1 回を止めずに決め直す。"""
    lap = looped(tmp_path, world)
    plan = Path(lap["dev_plan"])
    fill(plan)
    (state_dir_of(str(plan.absolute())) / "state.json").write_text('{"log": [{"id": "bump", "exit": 3}]}')
    world.publish("10.27.6", "develop", "main", tag=True)
    own(monkeypatch, False)
    assert fill(plan) == (None, None)
    assert record(plan)["version"] == "10.27.7-dev.1" and len(record(plan)["history"]) == 1


def test_loop_plan_with_dev_stays_explicit_and_stops_when_taken(tmp_path, world):
    """受け入れ条件 5: --dev の版が既出なら流さず taken になる。"""
    lap = looped(tmp_path, world, dev="10.27.5-dev.1", prod="10.27.5")
    assert version_field(lap)["明示"] == "10.27.5-dev.1"
    why, info = fill(Path(lap["dev_plan"]))
    assert why and info["cause"] == "taken"


def test_loop_plan_takes_the_level_after_the_separator(tmp_path, world):
    """受け入れ条件 8: `--` より後の --level が開発版のプランの上げ幅になる。"""
    assert version_field(looped(tmp_path, world, extra=("--level", "minor")))["上げ幅"] == "minor"


def test_recorded_version_reads_only_a_usable_record(tmp_path, world):
    """読み取りは記録が無い・壊れている・版が文字列でないときも例外を投げず、書かない。"""
    plan = write_plan(tmp_path, world)
    assert queue_fill.recorded_version(None) is None and queue_fill.recorded_version(str(plan)) is None
    fill(plan)
    f = state_dir_of(str(plan.absolute())) / queue_fill.VERSION_RECORD
    kept = f.read_text()
    assert queue_fill.recorded_version(str(plan)) == "10.27.6-dev.1" and f.read_text() == kept
    for broken in ("{", "[]", '{"version": 1}'):
        f.write_text(broken)
        assert queue_fill.recorded_version(str(plan)) is None


@pytest.mark.parametrize(
    "report, want",
    [("- 結果: 完了\n", True), ("- 結果: 関門\n", False), ("- 結果: 止まった\n", False), ("本文だけ\n", False), (None, False)],
)
def test_plan_finished_reads_the_result_of_the_report(tmp_path, world, report, want):
    plan = write_plan(tmp_path, world)
    if report is not None:
        sd = state_dir_of(str(plan.absolute()))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "report.md").write_text(report)
    assert queue_fill.plan_finished(str(plan)) is want
    assert queue_fill.plan_finished(None) is False


# ---- 並列の計画のまとめたリリースの版（#3551） ----


@pytest.mark.parametrize(
    ("base", "latest", "want"),
    [
        ("10.27.67-dev.1", "10.27.66", "10.27.67"),  # 開発版の版上げが入った後（今と同じ）
        ("10.27.66", "10.27.66", "10.27.67"),  # 同期の PR が出した版へ揃えた後（#3551 の事象）
        ("10.27.66-dev.3", "10.27.66", "10.27.67"),  # 系列のタグがもうある
        ("10.27.65", "10.27.66", "10.27.67"),  # ベースブランチが最新のタグより古い
        ("10.27.5", None, "10.27.5"),  # タグが 1 つも無い
    ],
)
def test_collected_release_is_above_the_latest_tag(base, latest, want):
    assert rv.collected_release(obs(base, latest)) == want


def test_collected_release_does_not_decide_when_something_is_unreadable():
    o = obs("10.27.66", "10.27.66")
    o.unreadable = ["tags"]
    assert rv.collected_release(o) is None


def test_dev_version_of_the_parallel_plan_skips_a_released_version(tmp_path, world):
    """同期の PR で develop が出した版（タグあり）へ揃った後でも、まとめたリリースは次の版で組む（#3551）。"""
    import parallel_repo

    world.publish("10.27.6", "develop", "main", tag=True)
    assert parallel_repo.dev_version(world.repo) == "10.27.7"
    world.publish("10.27.7-dev.1", "develop")
    assert parallel_repo.dev_version(world.repo) == "10.27.7"


def test_dev_version_without_fetch_does_not_touch_origin(tmp_path, world, monkeypatch):
    """起動の前の検査は origin を取り直さず、手元の追跡ブランチとタグで版を決める。"""
    import parallel_repo

    world.publish("10.27.6", "develop", "main", tag=True)
    real = rv._git_run
    seen = []
    monkeypatch.setattr(rv, "_git_run", lambda repo, *a: seen.append(a[0]) or real(repo, *a))
    assert parallel_repo.dev_version(world.repo, fetch=False) == "10.27.7" and "fetch" not in seen


def test_dev_version_names_an_origin_fetch_failure_apart_from_an_unreadable_version(tmp_path, world, monkeypatch):
    """取り直しに落ちたら、版を読めないという理由ではなく git の stderr を添えた取り直せない理由で止める。"""
    import parallel_repo

    world.publish("10.27.6", "develop", "main", tag=True)
    real = rv._git_run
    failed = subprocess.CompletedProcess(["git", "fetch"], 128, "", "fatal: unable to access origin")
    monkeypatch.setattr(rv, "_git_run", lambda repo, *a: failed if a[0] == "fetch" else real(repo, *a))
    with pytest.raises(parallel_repo.OriginUnfetchable, match="origin を取り直せない: fatal: unable to access origin"):
        parallel_repo.dev_version(world.repo)
    assert parallel_repo.dev_version(world.repo, fetch=False) == "10.27.7"


# ---- observe の現状固定（I-002） ----


def test_observe_reads_base_production_and_release_tags(world):
    """正式版のタグだけを拾い、接尾辞つき・semver でない・別のプラグインのタグは外す。"""
    world.publish("10.27.6", "develop", "main", tag=True)
    for t in ("ndf--v10.27.7-dev.1", "ndf--vbroken", "other--v99.0.0"):
        git(world.repo, "tag", t)
    git(world.repo, "push", "-q", "origin", "--tags")
    o = rv.observe(world.repo, "ndf", "develop", "main")
    assert (o.plugin, o.base_ref, o.base_version, o.production_ref, o.production_version) == (
        "ndf",
        "origin/develop",
        "10.27.6",
        "origin/main",
        "10.27.6",
    )
    assert sorted(o.tags) == ["10.27.5", "10.27.6"] and o.latest_tag == "10.27.6"
    assert o.unreadable == []


def test_untagged_declaration_counts_the_production_version_as_released(world):
    """タグを作らない宣言（World の宣言は CD が無く release.tag が null）では、本番ブランチの正式版を出済みの版に数え、
    出した版をもう一度出す計画を組まない（タグが無くても、まとめたリリースは次の PATCH を出す）。"""
    import parallel_repo

    world.publish("10.27.6", "develop", "main")
    o = rv.observe(world.repo, "ndf", "develop", "main")
    assert "10.27.6" in o.tags and o.latest_tag == "10.27.6"
    assert rv.taken(o, "10.27.6").is_taken and rv.collected_release(o) == "10.27.7"
    assert parallel_repo.dev_version(world.repo) == "10.27.7"


def test_observe_counts_released_versions_by_the_given_declaration(world):
    """#3701 の round 15: 出済みの版の数え方は渡した宣言（プランの開始時のスナップショット）のタグの有無で決め、今の宣言を
    読み直さない。タグを作らない宣言で本番ブランチの版から数えた版は、作られていないタグの名前でなく本番ブランチの版として示す。"""
    world.publish("10.27.6", "develop", "main")
    tagged = rv.observe(world.repo, "ndf", "develop", "main", tagged=True)
    assert "10.27.6" not in tagged.tags and not rv.taken(tagged, "10.27.6").is_taken
    untagged = rv.observe(world.repo, "ndf", "develop", "main", tagged=False)
    assert rv.taken(untagged, "10.27.6").existing == "origin/main の版 10.27.6"
    assert "tagged" not in untagged.as_dict()


def test_observe_and_taken_follow_the_declared_tag_shape(world):
    """宣言の release.tag が `v{version}` なら、その形のタグを出済みの版に数え、既定の形のタグは数えない（前版の検索・公開と同じ形）。"""
    for t in ("v10.28.0", "v10.28.1-dev.1", "ndf--v10.29.0"):
        git(world.repo, "tag", t)
    git(world.repo, "push", "-q", "origin", "--tags")
    o = rv.observe(world.repo, "ndf", "develop", None, template="v{version}")
    assert o.tags == ["10.28.0"] and o.latest_tag == "10.28.0"
    d = rv.taken(o, "10.28.0")
    assert d.existing == "v10.28.0" and d.next_free == "10.28.1"


def test_observe_without_production_does_not_read_it(world):
    o = rv.observe(world.repo, "ndf", "develop", None)
    assert o.production_ref == "" and o.production_version is None
    assert o.base_version == "10.27.5" and o.unreadable == []


def test_observe_counts_missing_branches_as_unreadable(world):
    o = rv.observe(world.repo, "ndf", "nosuch", "gone")
    assert (o.base_ref, o.production_ref) == ("origin/nosuch", "origin/gone")
    assert o.base_version is None and o.production_version is None
    assert o.unreadable == ["origin/nosuch", "origin/gone"]
    assert o.tags == ["10.27.5"]


def test_observe_uses_the_given_rel_and_counts_a_wrong_one_as_unreadable(world):
    ok = rv.observe(world.repo, "ndf", "develop", "main", rel="plugins/ndf/.claude-plugin/plugin.json")
    assert (ok.base_version, ok.production_version, ok.unreadable) == ("10.27.5", "10.27.5", [])
    bad = rv.observe(world.repo, "ndf", "develop", "main", rel="nope/plugin.json")
    assert bad.base_version is None and bad.production_version is None
    assert bad.unreadable == ["origin/develop", "origin/main"]


def test_observe_without_tags_is_not_unreadable(world):
    git(world.repo, "tag", "-d", "ndf--v10.27.5")
    # タグを作る宣言で読む（World の宣言はタグを作らず、本番ブランチの版を出済みの版に数える）
    o = rv.observe(world.repo, "ndf", "develop", "main", fetch=False, tagged=True)
    assert o.tags == [] and o.latest_tag is None and o.unreadable == []


def test_observe_counts_a_failed_fetch_as_origin_unreadable(world):
    """取り直しに落ちても手元の origin/* は読む。fetch=False なら取り直さず、読めないにも数えない。"""
    git(world.repo, "fetch", "-q", "origin")
    git(world.repo, "remote", "set-url", "origin", str(world.origin) + "-missing")
    o = rv.observe(world.repo, "ndf", "develop", "main")
    assert o.unreadable == ["origin"] and o.base_version == "10.27.5" and o.tags == ["10.27.5"]
    assert rv.observe(world.repo, "ndf", "develop", "main", fetch=False).unreadable == []
