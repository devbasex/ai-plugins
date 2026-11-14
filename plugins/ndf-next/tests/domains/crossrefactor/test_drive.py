"""cross-refactoring の drive（提案 → 計画 → 項目 → 最終ゲート）を、担当と GitHub・CI の代わりで通す。

- #622: 課題が触らない関数への提案を項目にしない
- #750: 読み取りの工程の担当は読み取り用ディレクトリで動き、コードを書く工程の結果ファイルは作業ディレクトリの中に置く
- #2057: 完了した回を返すとき、持たない引数（`--reopen`）を案内しない
- #3313: テストを足す担当が結果を出さずに終わっても止まらず、起点まで取り消して項目を見送る
- #620: 足したテストが全体の実行に収集されなければ項目を戻す。直す前のコードで通らなければ項目を戻す
- #2680: 衝突している PR は CI を待たずに止まる
- #3038: 最終ゲートの直しが対象範囲の外を変えたら取り込まずに止まる
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.crossrefactor import drive, final_gate, store

from ._drive_world import (
    BRANCH,
    FakeAgents,
    FakeHub,
    Watched,
    World,
    commit_at,
    fake_ci,
    make_world,
    no_plan,
    ports,
    propose_area,
    sh,
)

TESTS = {"tests/test_geo.py": "from src.geo import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n"}
NEW = {"src/geo.py": "def area(w, h):\n    product = w * h\n    return product\n\n\ndef untouched():\n    return 0\n"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return make_world(tmp_path, monkeypatch)


def _acts(**over):
    acts = {
        "propose": propose_area,
        "plan": no_plan,
        "add-tests": commit_at(TESTS, "テストを足す", {"tests": ["tests/test_geo.py"]}),
        "implement": commit_at(NEW, "式に名前を付ける"),
        "fix": no_plan,
        "final-fix": no_plan,
    }
    acts.update(over)
    return acts


def _state(world: World) -> dict:
    return store.load(store.path_of(world.root()))


def test_items_follow_the_touched_function_and_results_stay_inside_each_workdir(world, monkeypatch, capsys) -> None:
    fake = FakeAgents(_acts())
    ci = fake_ci(Watched(None))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    state = _state(world)
    assert [(i["symbol"], i["status"]) for i in state["items"]] == [("area", "adopted")]
    assert [s["symbol"] for s in state["skipped"]] == ["untouched"]
    root = world.root()
    for agent, phase, cwd, submit in fake.seen:
        if phase in ("propose", "plan"):
            assert cwd == root / "read" / agent
        else:
            assert cwd == root / "work"
    # 読み取りの担当は標準出力で返し、コードを書く担当は作業ディレクトリの中へ書く。どちらも起動の側が logs へ回収する
    assert (root / "logs" / "codex-propose-pr7-result.json").is_file()
    assert (root / "logs" / "claude-add-tests-I-001-result.json").is_file()
    pushed = sh(world.origin, "rev-parse", f"refs/heads/{BRANCH}")
    assert ci.waited == [pushed] and "product = w * h" in sh(world.origin, "show", f"{pushed}:src/geo.py")
    assert not (root / "work").exists()
    # 同じ引数で打ち直すと、担当を起動せずに前回の結果を返す。持たない引数を案内しない
    again = drive.drive(world.config(), ports(FakeAgents(_acts(propose=None)), FakeHub("clean"), ci=ci))
    assert again.as_json() == got.as_json()
    assert "--reopen" not in capsys.readouterr().err


def test_add_tests_without_a_result_skips_the_item_instead_of_stopping(world, monkeypatch) -> None:
    def silent(spec, submit):
        commit_at({"tests/test_half.py": "x = 1\n"}, "途中")(spec, submit)
        return None

    ci = fake_ci(Watched(None))
    got = drive.drive(world.config(), ports(FakeAgents(_acts(**{"add-tests": silent})), FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    item = _state(world)["items"][0]
    assert item["status"] == "deferred" and item["reason"].startswith("add-tests")
    assert "tests/test_half.py" not in sh(world.origin, "ls-tree", "-r", "--name-only", f"refs/heads/{BRANCH}")
    assert _state(world)["phases"]["add-tests"]["base_sha"] == item["start"]


def test_added_test_not_collected_by_the_whole_run_reverts_the_item(world, monkeypatch) -> None:
    decl = {
        "test": {
            "suites": [
                {"name": "pytest", "command": "pytest", "collect_command": "printf 'tests/test_geo.py::test_area\\n'", "paths": ["."]}
            ]
        }
    }
    adds = commit_at({**TESTS, "tests/geo_helpers.py": "def test_x():\n    pass\n"}, "テストを足す", {"tests": ["tests/geo_helpers.py"]})

    def with_decl(spec, submit):
        (spec.cwd / ".ndf").mkdir(exist_ok=True)
        (spec.cwd / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
        return adds(spec, submit)

    ci = fake_ci(Watched(None))
    got = drive.drive(world.config(), ports(FakeAgents(_acts(**{"add-tests": with_decl})), FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    item = _state(world)["items"][0]
    assert item["status"] == "reverted" and "tests/geo_helpers.py" in item["reason"]


def test_added_test_that_fails_on_the_code_before_the_change_reverts_the_item(world, monkeypatch) -> None:
    # 足したテストが直す前のコードで通らなければ、今の振る舞いを固定していないので実装へ進まない
    decl = {
        "test": {
            "suites": [
                {
                    "name": "pytest",
                    "command": "pytest",
                    "scope_command": "false {paths}",
                    "collect_command": "printf 'tests/test_geo.py::test_area\\n'",
                    "paths": ["."],
                }
            ]
        }
    }
    adds = commit_at(TESTS, "テストを足す", {"tests": ["tests/test_geo.py"]})

    def with_decl(spec, submit):
        (spec.cwd / ".ndf").mkdir(exist_ok=True)
        (spec.cwd / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
        return adds(spec, submit)

    fake = FakeAgents(_acts(**{"add-tests": with_decl}))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    item = _state(world)["items"][0]
    assert item["status"] == "reverted" and "直す前のコードで通らない" in item["reason"]
    assert "implement" not in [phase for _, phase, _, _ in fake.seen]


def test_conflicting_pull_request_stops_without_waiting_for_ci(world, monkeypatch) -> None:
    ci = fake_ci(Watched(None))
    got = drive.drive(world.config(), ports(FakeAgents(_acts()), FakeHub("unknown", "dirty"), ci=ci))
    assert got.kind == "base_conflict"
    assert ci.waited == []


@pytest.mark.parametrize(("port_field", "module_function"), [("wait", "wait_ci"), ("failed_tests", "_failed_tests")])
def test_default_ci_port_is_the_final_gate_functions(port_field, module_function) -> None:
    # 接続を渡さないとき、最終ゲートは今までどおり final_gate の待ちと落ちたテストの取得を使う
    assert getattr(final_gate.CiPort(), port_field) is getattr(final_gate, module_function)


def test_final_fix_outside_the_plan_scope_is_not_taken(world, monkeypatch) -> None:
    ci = fake_ci(Watched("test_failed", ("pytest",)), Watched(None))
    outside = commit_at({"docs/a.md": "# 直した\n"}, "範囲の外を直す")
    got = drive.drive(world.config(), ports(FakeAgents(_acts(**{"final-fix": outside})), FakeHub("clean"), ci=ci))
    assert got.kind == "refused"
    final = _state(world)["final"]
    assert final["outside_failures"]["outside_files"] == ["docs/a.md"]
    assert final["outside_failures"]["failed_tests"] == ["tests/test_geo.py::test_area"]
    work = world.root() / "work"
    assert sh(work, "show", "HEAD:docs/a.md") == "# a"


def test_final_fix_without_a_result_is_undone_and_not_pushed_on_resume(world, monkeypatch) -> None:
    # 結果を出さずに終わった最後の修正のコミットを残すと、再開の送信が検査せずに PR へ送る
    ci = fake_ci(Watched("test_failed", ("pytest",)))

    def outside_without_result(spec, submit):
        commit_at({"docs/a.md": "# 直した\n"}, "範囲の外を直す")(spec, submit)
        return None

    got = drive.drive(world.config(), ports(FakeAgents(_acts(**{"final-fix": outside_without_result})), FakeHub("clean"), ci=ci))
    assert got.kind == "work_stopped"
    work = world.root() / "work"
    assert sh(work, "rev-parse", "HEAD") == _state(world)["final"]["head"]
    assert sh(work, "show", "HEAD:docs/a.md") == "# a"
