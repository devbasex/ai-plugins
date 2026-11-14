"""worktree の依存の用意・ローカルの動作検証・テスト環境を、一時の git のリポジトリと差し替えた実行系で確かめる。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable, Mapping, Optional

import pytest

from ndf import identity
from ndf.adapters.git import Completed, Git
from ndf.domains.worktree import cli_env, container, create, deps, ledger, localenv, localenv_decide, testenv, testenv_decide, testenv_sweep


def _declare(main: Path, run_git: Callable, extra: dict) -> None:
    decl = {"version": 1, "base_branch": "develop", "production_branch": "main", **extra}
    (main / ".ndf" / "worktree.json").write_text(json.dumps(decl), encoding="utf-8")
    run_git(main, "add", "-A")
    run_git(main, "commit", "-q", "-m", "declare")
    run_git(main, "push", "-q", "origin", "develop")  # worktree は origin のベースブランチから切る（create の起点）


class FakeShell:
    """コマンドを起動せずに記録し、決めた出力を返す（`docker ps` はコンテナの名前、`readlink` はリンク先）。"""

    def __init__(self, outputs: Optional[dict[str, Completed]] = None) -> None:
        self.calls: list[tuple[list[str], Optional[Path], dict]] = []
        self.outputs = outputs if outputs is not None else {}

    def runner(self, argv: list[str], cwd: Optional[Path], env: Optional[Mapping[str, str]]) -> Completed:
        self.calls.append((argv, cwd, dict(env) if env else {}))
        key = " ".join(argv[1:3])
        return self.outputs[key] if key in self.outputs else Completed(0, "", "")

    def attached(self, argv: list[str], cwd: Optional[Path], env: Optional[Mapping[str, str]]) -> int:
        self.calls.append((argv, cwd, dict(env) if env else {}))
        return 3

    def shell(self) -> container.Shell:
        return container.Shell(runner=self.runner, attached=self.attached, docker="true")


def test_deps_prepares_copies_and_runs_then_marks(main_repo: Path, run_git: Callable) -> None:
    (main_repo / ".gitignore").write_text(".worktrees/\nvendor/\ncfg.local\nbuilt\n", encoding="utf-8")
    _declare(main_repo, run_git, {"deps": {"copy_from_main": ["vendor"], "copy_as_real": ["cfg.local"], "run": ["echo ok > built"]}})
    (main_repo / "vendor").mkdir()
    (main_repo / "vendor" / "lib.txt").write_text("v", encoding="utf-8")
    (main_repo / "cfg.local").write_text("c", encoding="utf-8")
    wt = create.create(main_repo, "feat/d").path

    got = deps.prepare(wt)
    assert (got.failed, got.steps) == ("", 3)
    assert (wt / "vendor" / "lib.txt").stat().st_ino == (main_repo / "vendor" / "lib.txt").stat().st_ino  # ハードリンク
    assert (wt / "cfg.local").stat().st_ino != (main_repo / "cfg.local").stat().st_ino  # 実体
    assert (wt / "built").read_text(encoding="utf-8") == "ok\n"
    assert deps.prepare(wt, if_unprepared=True).skipped


def test_deps_fails_when_git_state_changes_or_path_escapes(main_repo: Path, run_git: Callable) -> None:
    _declare(main_repo, run_git, {"deps": {"run": ["echo x > tracked-by-mistake.txt"]}})
    wt = create.create(main_repo, "feat/e").path
    got = deps.prepare(wt)
    assert got.failed == "git の状態" and any("tracked-by-mistake.txt" in line for line in got.tail)
    assert not deps.mark_file(Git(wt)).exists()
    assert not deps.safe_relative("../x") and not deps.safe_relative("/x") and deps.safe_relative("a/b")


def test_deps_refuses_same_entity_in_main_dir(main_repo: Path, run_git: Callable) -> None:
    """メインディレクトリで打つと複製元と複製先が同じ実体になる。消さずに断る。"""
    (main_repo / ".gitignore").write_text(".worktrees/\ncfg.local\n", encoding="utf-8")
    _declare(main_repo, run_git, {"deps": {"copy_as_real": ["cfg.local"]}})
    (main_repo / "cfg.local").write_text("c", encoding="utf-8")
    got = deps.prepare(main_repo)
    assert got.failed.startswith("copy_as_real[0]") and any("同じ実体" in line for line in got.tail)
    assert (main_repo / "cfg.local").read_text(encoding="utf-8") == "c"


def test_deps_keeps_rewritten_real_copy_directory(main_repo: Path, run_git: Callable) -> None:
    """worktree の側で足したファイルがある実体の複製は、打ち直しても消さない。"""
    (main_repo / ".gitignore").write_text(".worktrees/\nconf/\n", encoding="utf-8")
    _declare(main_repo, run_git, {"deps": {"copy_as_real": ["conf"]}})
    (main_repo / "conf").mkdir()
    (main_repo / "conf" / "a.txt").write_text("a", encoding="utf-8")
    wt = create.create(main_repo, "feat/k").path
    assert deps.prepare(wt).failed == ""
    assert deps.prepare(wt).failed == ""  # 中身が同じなら作り直す
    (wt / "conf" / "mine.txt").write_text("mine", encoding="utf-8")
    got = deps.prepare(wt)
    assert got.failed.startswith("copy_as_real[0]") and (wt / "conf" / "mine.txt").read_text(encoding="utf-8") == "mine"


def test_deps_refuses_parent_symlink_out_of_worktree(main_repo: Path, run_git: Callable, tmp_path: Path) -> None:
    (main_repo / ".gitignore").write_text(".worktrees/\nout/\nout\n", encoding="utf-8")
    _declare(main_repo, run_git, {"deps": {"copy_as_real": ["out/cfg"]}})
    (main_repo / "out").mkdir()
    (main_repo / "out" / "cfg").write_text("c", encoding="utf-8")
    wt = create.create(main_repo, "feat/s").path
    outside = tmp_path / "outside"
    outside.mkdir()
    (wt / "out").symlink_to(outside)
    got = deps.prepare(wt)
    assert got.failed.startswith("copy_as_real[0]") and not (outside / "cfg").exists()


def test_deps_refuses_dest_symlink(main_repo: Path, run_git: Callable, tmp_path: Path) -> None:
    (main_repo / ".gitignore").write_text(".worktrees/\ncfg\n", encoding="utf-8")
    _declare(main_repo, run_git, {"deps": {"copy_as_real": ["cfg"]}})
    (main_repo / "cfg").write_text("main", encoding="utf-8")
    wt = create.create(main_repo, "feat/d").path
    outside = tmp_path / "outside.txt"
    outside.write_text("mine", encoding="utf-8")
    (wt / "cfg").symlink_to(outside)
    got = deps.prepare(wt)
    assert got.failed.startswith("copy_as_real[0]") and outside.read_text(encoding="utf-8") == "mine"


def test_deps_rejects_broken_section(main_repo: Path, run_git: Callable) -> None:
    _declare(main_repo, run_git, {"deps": {"run": "make"}})
    wt = create.create(main_repo, "feat/f").path
    with pytest.raises(deps.DepsError):
        deps.prepare(wt)


def test_localenv_verify_mode_and_aim(main_repo: Path, run_git: Callable) -> None:
    local = {"kind": "compose", "branch_probe": "cat loaded", "layout": "indirect", "src_target": "/app", "isolate_when": ["db/*"],
             "app_service": "web", "reload_signal": {"process": "php-fpm", "signal": "USR2"}}  # fmt: skip
    _declare(main_repo, run_git, {"localenv": local})
    wt = create.create(main_repo, "feat/l").path
    t = localenv.target(wt)
    assert t is not None
    fake = FakeShell({"-c cat loaded": Completed(0, "feat/l\n", ""), "ps --filter": Completed(0, "c1\n", ""),
                      "exec c1": Completed(0, f"{t.main_dir}\n", "")})  # fmt: skip
    assert localenv.verify(t, fake.shell()).result == localenv_decide.MATCH
    (wt / "db").mkdir()
    (wt / "db" / "m.sql").write_text("", encoding="utf-8")
    verdict, hits = localenv.mode(t)
    assert verdict.result == localenv_decide.ISOLATE and hits == ["db/m.sql（db/*）"]
    assert localenv.aim(t, fake.shell()) == 1
    assert ["true", "exec", "-u", "root", "c1", "ln", "-sfn", "--", str(t.path), "/app"] in [c[0] for c in fake.calls]


def test_localenv_not_applicable_without_compose(main_repo: Path) -> None:
    assert localenv.target(main_repo) is None


def _testenv(main_repo: Path, run_git: Callable, wt_branch: str) -> testenv.Target:
    (main_repo / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    section = {"port_band": [20000, 21000], "port_roles": {"http": 1}, "test_kinds": {"e2e": {"run": "exit 3", "base_url_env": "BASE_URL"}},
               "expose": {"enabled": True, "public_tag": "t1", "base_domain": "example.test", "open_command": "true",
                          "close_command": "true"}}  # fmt: skip
    _declare(main_repo, run_git, {"localenv": {"kind": "compose", "compose_files": ["compose.yml"]}, "testenv": section})
    wt = create.create(main_repo, wt_branch).path
    t = testenv.target(wt, FakeShell().shell())
    assert t is not None
    return t


def test_testenv_assign_compose_and_down(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell()
    t = _testenv(main_repo, run_git, "feat/t")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    row = testenv.assign(t)
    assert (row["slot"], row["ports"]) == (0, {"http": 20001}) and testenv.assign(t)["slot"] == 0
    values = testenv.compose_env(t)
    assert values["NDF_PORT_HTTP"] == "20001" and values["COMPOSE_FILE"] == str(t.path / "compose.yml")
    assert testenv.run_test(t, "e2e") == 3  # 終了コードをそのまま返す
    assert fake.calls[-1][2]["BASE_URL"] == "http://localhost:20001"
    assert testenv.up(t) == 0
    assert testenv.down(t) == 0 and t.book.current(t.key) is None
    downs = [c[0] for c in fake.calls if "down" in c[0]]
    assert downs and "--volumes" in downs[-1]  # 宣言が無ければボリュームも消す


def test_testenv_down_keeps_slot_when_discard_fails(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell({"compose -p": Completed(1, "", "boom")})
    t = _testenv(main_repo, run_git, "feat/u")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    testenv.assign(t)
    with pytest.raises(testenv.TestenvError):
        testenv.down(t)
    assert t.book.current(t.key) is not None


def test_testenv_reclaim_and_expose(main_repo: Path, run_git: Callable) -> None:
    t = _testenv(main_repo, run_git, "feat/v")
    gone = str(main_repo / ".worktrees" / "feat" / "gone")
    t.book.acquire(gone, "feat/gone", "env-gone")
    testenv.assign(t)
    items = testenv.reclaim(t)
    assert [(i["name"], i["result"]) for i in items] == [("env-gone", "released")]  # コンテナが無ければ解放だけ
    with pytest.raises(testenv.TestenvError):
        testenv_sweep.expose(t)  # 載っている基準が公開を許す基準と違う
    t.book.update(t.key, lambda r: r.update(golden_tag="t1"))
    url = testenv_sweep.expose(t)
    assert url == f"https://wt{t.book.current(t.key)['slot']}.example.test" and testenv_sweep.expose(t) == url
    assert testenv_sweep.unexpose(t) == url and testenv_sweep.unexpose(t) is None


def test_testenv_reach_refuses_container_without_worktree(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell({"compose exec": Completed(0, "other\n", "")})
    t = _testenv(main_repo, run_git, "feat/r")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    testenv.assign(t)
    with pytest.raises(testenv.TestenvError):
        testenv.reach(t, "app")
    assert not list((t.path / testenv.EVIDENCE).glob("reach-*"))  # 印は必ず消す


# 現状固定（I-008）: `testenv.test_values` の各分岐。期待値は今の実装の出力。
def _values_target(main_repo: Path, kinds: dict) -> testenv.Target:
    path = Path(os.path.realpath(main_repo))
    return testenv.Target(path, path, {"test_kinds": kinds}, (), FakeShell().shell(), ledger.Ledger(path / "unused.json"))


ROW = {"environment": "env-a", "ports": {"http": 20001, "api": 20002}}


@pytest.mark.parametrize(
    ("kinds", "want"),
    [
        ({}, {}),  # 種類の宣言が無い
        ({"e2e": "not a dict"}, {}),
        ({"e2e": {"skip_reset": {"A": 1, 2: True}}}, {"A": "1", "2": "True"}),
        ({"e2e": {"skip_reset": ["A"]}}, {}),
        ({"e2e": {"base_url_env": "BASE_URL"}}, {"BASE_URL": "http://localhost:20001"}),  # 既定の役割は http
        ({"e2e": {"base_url_env": "BASE_URL", "port_role": "api"}}, {"BASE_URL": "http://localhost:20002"}),
        ({"e2e": {"base_url_env": "BASE_URL", "port_role": 7}}, {"BASE_URL": "http://localhost:20001"}),
        ({"e2e": {"base_url_env": "BASE_URL", "port_role": "none"}}, {}),  # ポートが無ければ渡さない
        ({"e2e": {"base_url_env": 1}}, {}),
        ({"e2e": {"out_env": 1}}, {}),
    ],
)
def test_test_values_without_evidence(main_repo: Path, kinds: dict, want: dict) -> None:
    t = _values_target(main_repo, kinds)
    assert testenv.test_values(t, "e2e", ROW, None) == want
    assert not (t.path / testenv.EVIDENCE).exists()


def test_test_values_evidence_default_place_and_exclude(main_repo: Path) -> None:
    t = _values_target(main_repo, {"e2e": {"out_env": "OUT", "skip_reset": {"S": "1"}}})
    got = testenv.test_values(t, "e2e", ROW, None)
    out = Path(got.pop("OUT"))
    assert got == {"S": "1"}
    assert out.parent == t.path / testenv.EVIDENCE and out.name.startswith("env-a-") and out.is_dir()
    exclude = t.path / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8").splitlines().count(f"{testenv.EVIDENCE}/") == 1
    testenv.test_values(t, "e2e", ROW, None)
    assert exclude.read_text(encoding="utf-8").splitlines().count(f"{testenv.EVIDENCE}/") == 1  # 2 度目は足さない


def test_test_values_evidence_given_relative_and_absolute(main_repo: Path) -> None:
    t = _values_target(main_repo, {"e2e": {"out_env": "OUT"}})
    assert testenv.test_values(t, "e2e", ROW, Path("ev/x")) == {"OUT": str(t.path / "ev" / "x")}
    assert (t.path / "ev" / "x").is_dir()
    assert testenv.test_values(t, "e2e", ROW, t.path / "abs") == {"OUT": str(t.path / "abs")}


@pytest.mark.parametrize("out", [Path("../outside"), Path("/tmp"), Path(".")])
def test_test_values_refuses_evidence_outside_worktree(main_repo: Path, out: Path) -> None:
    t = _values_target(main_repo, {"e2e": {"out_env": "OUT"}})
    with pytest.raises(testenv.TestenvError, match="証跡の置き場が worktree の外を指す"):
        testenv.test_values(t, "e2e", ROW, out)


# 現状固定（I-010）: `cli_env.run_localenv` の分岐ごとの結果 JSON。期待値は今の実装の出力。
LE_TARGET = localenv.Target(Path("/w"), Path("/m"), {})


def _le_ok(summary: str, items: Optional[list] = None, metrics: Optional[dict] = None) -> dict:
    return {"tool": "t", "status": "ok", "summary": summary, "items": items or [], "metrics": metrics or {}}


def _le_stopped(kind: str, summary: str) -> dict:
    return {"tool": "t", "status": "stopped", "summary": summary, "items": [], "metrics": {}, "cause": {"kind": kind, "detail": summary}}


def _le_run(monkeypatch: pytest.MonkeyPatch, action: str, **fakes: Callable) -> dict:
    monkeypatch.setattr(localenv, "target", lambda p: LE_TARGET)
    monkeypatch.setattr(container.Shell, "from_env", classmethod(lambda cls: "SHELL"))
    for name, fn in fakes.items():
        monkeypatch.setattr(localenv, name, fn)
    return cli_env.run_localenv("t", argparse.Namespace(action=action, worktree=Path("/w"), cwd=Path("/")))


def _le_verdict(r: str) -> localenv_decide.Verdict:
    return localenv_decide.Verdict(r, f"text-{r}")


def _le_raise(*args: object) -> None:
    raise localenv.LocalenvError("boom")


def test_run_localenv_not_applicable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localenv, "target", lambda p: None)
    got = cli_env.run_localenv("t", argparse.Namespace(action="setup", worktree=None, cwd=Path("/")))
    assert got == _le_ok("適用外（宣言か、その節が無い）")


def test_run_localenv_setup_and_aim(monkeypatch: pytest.MonkeyPatch) -> None:
    shells: list = []
    assert _le_run(monkeypatch, "setup", setup=lambda t: 2) == _le_ok("複製した: 2 件")
    got = _le_run(monkeypatch, "aim", aim=lambda t, s: shells.append(s) or 3)
    assert got == _le_ok("ローカル環境が指すコードを /w へ向けた（コンテナ 3 件）") and shells == ["SHELL"]
    assert _le_run(monkeypatch, "setup", setup=_le_raise) == _le_stopped("precondition", "boom")
    assert _le_run(monkeypatch, "aim", aim=_le_raise) == _le_stopped("precondition", "boom")


def test_run_localenv_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _le_run(monkeypatch, "mode", mode=lambda t: (_le_verdict(localenv_decide.ISOLATE), ["a（*）"]))
    assert got == _le_ok("text-isolate", [{"path": "a（*）"}], {"result": "isolate"})
    got = _le_run(monkeypatch, "mode", mode=lambda t: (_le_verdict(localenv_decide.SHARE), []))
    assert got == _le_ok("text-share", [], {"result": "share"})


@pytest.mark.parametrize(
    ("verdict", "want"),
    [
        (localenv_decide.MATCH, _le_ok("text-match", [], {"result": "match"})),
        (localenv_decide.MISMATCH, _le_stopped("check_failed", "text-mismatch")),
        (localenv_decide.NOT_RUNNING, _le_stopped("not_applicable", "text-not_running")),
    ],
)
def test_run_localenv_verify(monkeypatch: pytest.MonkeyPatch, verdict: str, want: dict) -> None:
    assert _le_run(monkeypatch, "verify", verify=lambda t, s: _le_verdict(verdict)) == want


@pytest.mark.parametrize(
    ("verdict", "rc", "want"),
    [
        (localenv_decide.MISMATCH, None, _le_stopped("check_failed", "text-mismatch")),
        (localenv_decide.NOT_RUNNING, None, _le_stopped("not_applicable", "text-not_running")),
        (localenv_decide.MATCH, None, _le_ok("動作確認のコマンドが宣言されていない")),
        (localenv_decide.MATCH, 0, _le_ok("動作確認が通った")),
        (localenv_decide.MATCH, 4, _le_stopped("check_failed", "動作確認のコマンドが失敗した（終了コード 4）")),
    ],
)
def test_run_localenv_healthcheck(monkeypatch: pytest.MonkeyPatch, verdict: str, rc: Optional[int], want: dict) -> None:
    assert _le_run(monkeypatch, "healthcheck", healthcheck=lambda t, s: (_le_verdict(verdict), rc)) == want


def test_run_localenv_does_not_catch_errors_of_mode_verify_healthcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    # NOTE: 現状固定。setup と aim の外の LocalenvError は結果 JSON にせず、そのまま上がる
    for action in ("mode", "verify", "healthcheck"):
        with pytest.raises(localenv.LocalenvError):
            _le_run(monkeypatch, action, **{action: _le_raise})


# 現状固定（I-012）: `ledger.Ledger` の読み書き。期待値は今の実装の出力。
def test_ledger_rows_tolerates_missing_and_broken_shapes(tmp_path: Path) -> None:
    book = ledger.Ledger(tmp_path / "ndf" / "reg.json")
    assert book.rows() == [] and book.current("/w") is None
    book.path.parent.mkdir()
    for text, want in [("[]", []), ('{"assignments": {}}', []), ('{"assignments": [1, {"a": 1}, "x"]}', [{"a": 1}])]:
        book.path.write_text(text, encoding="utf-8")
        assert book.rows() == want


def test_ledger_acquire_current_update_and_release(tmp_path: Path) -> None:
    book = ledger.Ledger(tmp_path / "ndf" / "reg.json")
    row = book.acquire("/w/a", "feat/a", "env-a")
    assert row is not None
    keys = {"id", "worktree", "branch", "environment", "slot", "ports", "assigned_at", "last_used_at", "released_at", "expose"}
    assert set(row) == keys
    assert (row["worktree"], row["branch"], row["environment"], row["slot"], row["ports"], row["released_at"], row["expose"]) == (
        "/w/a", "feat/a", "env-a", 0, {}, None, None
    )  # fmt: skip
    assert book.acquire("/w/a", "other", "other") == row  # 既にあればそれを返す
    assert book.acquire("/w/b", "feat/b", "env-b")["slot"] == 1  # type: ignore[index]
    assert book.current("/w/a") == row and book.current("/w/none") is None

    assert book.update("/w/a", lambda r: r.update(ports={"http": 1})) is True
    assert book.current("/w/a")["ports"] == {"http": 1}  # type: ignore[index]
    assert book.update("/w/none", lambda r: r.update(x=1)) is False

    assert book.update("/w/a", lambda r: r.update(released_at="t1")) is True
    assert book.current("/w/a") is None
    assert book.update("/w/a", lambda r: r.update(x=1)) is False  # 解放済みは既定では選ばない
    assert book.update("/w/a", lambda r: r.update(note="n"), pick=lambda r: r.get("released_at") == "t1") is True

    again = book.acquire("/w/a", "feat/a", "env-a2")
    assert again is not None and again["slot"] == 0  # 解放したスロットを使い直す
    assert [(r["worktree"], r["slot"], r["released_at"], r.get("note")) for r in book.rows()] == [
        ("/w/a", 0, "t1", "n"), ("/w/b", 1, None, None), ("/w/a", 0, None, None)
    ]  # fmt: skip  # 行は消さずに追記する
    saved = json.loads(book.path.read_text(encoding="utf-8"))
    assert list(saved) == ["assignments"] and saved["assignments"] == book.rows()
    assert not list(book.path.parent.glob(f".{book.path.name}.*"))  # 一時ファイルは残らない


def test_ledger_acquire_skips_released_slot_with_open_expose(tmp_path: Path) -> None:
    book = ledger.Ledger(tmp_path / "reg.json")
    rows = [{"worktree": "/w/a", "slot": 0, "released_at": "t1", "expose": {"url": "u", "closed_at": None}}]
    book.path.write_text(json.dumps({"assignments": rows}), encoding="utf-8")
    assert book.acquire("/w/new", "b", "e")["slot"] == 1  # type: ignore[index]  # 公開が閉じるまでスロットを渡さない


def test_ledger_acquire_returns_none_when_slots_are_full(tmp_path: Path) -> None:
    book = ledger.Ledger(tmp_path / "reg.json")
    rows = [{"worktree": f"/w/{i}", "slot": i, "released_at": None} for i in range(testenv_decide.SLOTS)]
    book.path.write_text(json.dumps({"assignments": rows}), encoding="utf-8")
    assert book.acquire("/w/new", "b", "e") is None
    assert len(book.rows()) == testenv_decide.SLOTS


def test_ledger_latest_and_path_of_git(main_repo: Path) -> None:
    rows = [{"worktree": "/w", "n": 1}, {"worktree": "/x", "n": 2}, {"worktree": "/w", "n": 3}]
    assert ledger.latest(rows, "/w", lambda r: True) == {"worktree": "/w", "n": 3}
    assert ledger.latest(rows, "/w", lambda r: r["n"] == 1) == {"worktree": "/w", "n": 1}
    assert ledger.latest(rows, "/z", lambda r: True) is None
    want = Path(os.path.realpath(main_repo)) / ".git" / identity.load().name / "worktree-registry.json"
    assert ledger.ledger_path(Git(main_repo)) == want and ledger.Ledger.of(Git(main_repo)).path == want


# 現状固定（I-020）: `testenv_sweep.expose`・`unexpose` の分岐。期待値は今の実装の出力。
EXPOSE = {"enabled": True, "public_tag": "t1", "base_domain": "example.test", "open_command": "open-it", "close_command": "close-it"}


def _sweep_target(tmp_path: Path, fake: FakeShell, conf: object, make_dir: bool = True) -> testenv.Target:
    wt = tmp_path / "wt"
    if make_dir:
        wt.mkdir()
    return testenv.Target(wt, tmp_path, {"expose": conf}, (), fake.shell(), ledger.Ledger(tmp_path / "reg.json"))


def _assigned(t: testenv.Target, tag: Optional[str] = "t1") -> None:
    t.book.acquire(t.key, "feat/x", "env-x")
    t.book.acquire("/other", "feat/o", "env-o")  # 別の worktree の割り当て（スロット 1）
    if tag is not None:
        t.book.update(t.key, lambda r: r.update(golden_tag=tag))


@pytest.mark.parametrize(
    ("conf", "message"),
    [
        (None, "拒否: testenv.expose.enabled が有効でない"),
        ({**EXPOSE, "enabled": "yes"}, "拒否: testenv.expose.enabled が有効でない"),
        ({**EXPOSE, "public_tag": ""}, "拒否: testenv.expose.public_tag と base_domain が要る"),
        ({**EXPOSE, "base_domain": None}, "拒否: testenv.expose.public_tag と base_domain が要る"),
        ({**EXPOSE, "open_command": ""}, "公開の手段が宣言されていない（testenv.expose.open_command と close_command が要る）"),
        ({**EXPOSE, "close_command": ""}, "公開の手段が宣言されていない（testenv.expose.open_command と close_command が要る）"),
    ],
)
def test_expose_refuses_broken_config(tmp_path: Path, conf: object, message: str) -> None:
    t = _sweep_target(tmp_path, FakeShell(), conf)
    _assigned(t)
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.expose(t)
    assert str(exc.value) == message


def test_expose_refuses_without_assignment_or_with_other_tag(tmp_path: Path) -> None:
    t = _sweep_target(tmp_path, FakeShell(), EXPOSE)
    with pytest.raises(testenv.TestenvError, match="^拒否: 割り当てが無い$"):
        testenv_sweep.expose(t)
    _assigned(t, tag=None)
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.expose(t)
    assert str(exc.value) == "拒否: 載っている基準（なし）が公開を許す基準（t1）と違う"
    t.book.update(t.key, lambda r: r.update(golden_tag="t0"))
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.expose(t)
    assert str(exc.value) == "拒否: 載っている基準（t0）が公開を許す基準（t1）と違う"


def test_expose_opens_once_and_passes_values(tmp_path: Path) -> None:
    fake = FakeShell()
    t = _sweep_target(tmp_path, fake, EXPOSE)
    _assigned(t)
    assert testenv_sweep.expose(t) == "https://wt0.example.test"
    values = {"NDF_EXPOSE_URL": "https://wt0.example.test", "NDF_EXPOSE_HOST": "wt0.example.test", "NDF_EXPOSE_ENVIRONMENT": "env-x",
              "NDF_EXPOSE_SLOT": "0"}  # fmt: skip
    assert fake.calls == [(["sh", "-c", "open-it"], t.path, values)]
    opened = t.book.current(t.key)["expose"]  # type: ignore[index]
    assert (opened["url"], opened["ttl"], opened["closed_at"]) == ("https://wt0.example.test", testenv_sweep.DEFAULT_TTL, None)
    assert testenv_sweep.expose(t) == "https://wt0.example.test" and len(fake.calls) == 1  # 開いていれば打たない


def test_expose_uses_declared_ttl(tmp_path: Path) -> None:
    t = _sweep_target(tmp_path, FakeShell(), {**EXPOSE, "ttl": 2})
    _assigned(t)
    testenv_sweep.expose(t)
    assert t.book.current(t.key)["expose"]["ttl"] == "2"  # type: ignore[index]


def test_expose_refuses_while_other_worktree_is_open(tmp_path: Path) -> None:
    fake = FakeShell()
    t = _sweep_target(tmp_path, fake, EXPOSE)
    _assigned(t)
    t.book.update("/other", lambda r: r.update(expose={"url": "u", "closed_at": None}))
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.expose(t)
    assert str(exc.value) == "拒否: 別のテスト環境が公開中（同時に開けるのは 1 本）" and fake.calls == []


def test_expose_rolls_back_record_when_open_command_fails(tmp_path: Path) -> None:
    fake = FakeShell({"-c open-it": Completed(1, "", "")})
    t = _sweep_target(tmp_path, fake, EXPOSE)
    _assigned(t)
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.expose(t)
    assert str(exc.value) == "公開の手段が失敗した。閉じる手段で閉じて記録を戻した"
    assert fake.calls[-1][0] == ["sh", "-c", "close-it"]  # 開く手段が口を作った後で失敗しても閉じる
    opened = t.book.current(t.key)["expose"]  # type: ignore[index]
    assert opened["closed_at"] is not None
    assert testenv_sweep.unexpose(t) is None  # 閉じた記録は開いている公開として読まない


def test_expose_keeps_record_when_open_and_close_fail(tmp_path: Path) -> None:
    fake = FakeShell({"-c open-it": Completed(1, "", ""), "-c close-it": Completed(2, "", "")})
    t = _sweep_target(tmp_path, fake, EXPOSE)
    _assigned(t)
    with pytest.raises(testenv.TestenvError, match="^公開の手段が失敗した。閉じたかを確かめられないため公開中の記録を残した"):
        testenv_sweep.expose(t)
    assert t.book.current(t.key)["expose"]["closed_at"] is None  # type: ignore[index]


def test_unexpose_runs_close_command_and_closes(tmp_path: Path) -> None:
    fake = FakeShell()
    t = _sweep_target(tmp_path, fake, {**EXPOSE, "close_command": "close-it"})
    _assigned(t)
    assert testenv_sweep.unexpose(t) is None
    testenv_sweep.expose(t)
    assert testenv_sweep.unexpose(t) == "https://wt0.example.test"
    values = {"NDF_EXPOSE_URL": "https://wt0.example.test", "NDF_EXPOSE_HOST": "wt0.example.test", "NDF_EXPOSE_ENVIRONMENT": "env-x",
              "NDF_EXPOSE_SLOT": "0"}  # fmt: skip
    assert fake.calls[-1] == (["sh", "-c", "close-it"], t.path, values)
    assert t.book.current(t.key)["expose"]["closed_at"] is not None  # type: ignore[index]
    assert testenv_sweep.unexpose(t) is None


def test_unexpose_keeps_record_when_close_command_fails(tmp_path: Path) -> None:
    fake = FakeShell({"-c close-it": Completed(2, "", "")})
    t = _sweep_target(tmp_path, fake, {**EXPOSE, "close_command": "close-it"})
    _assigned(t)
    testenv_sweep.expose(t)
    with pytest.raises(testenv.TestenvError) as exc:
        testenv_sweep.unexpose(t)
    assert str(exc.value) == "公開を閉じる手段が失敗した。台帳は閉じていない: env-x https://wt0.example.test"
    assert t.book.current(t.key)["expose"]["closed_at"] is None  # type: ignore[index]


def test_unexpose_after_release_and_without_worktree_dir(tmp_path: Path) -> None:
    fake = FakeShell()
    t = _sweep_target(tmp_path, fake, {**EXPOSE, "close_command": "close-it"}, make_dir=False)
    _assigned(t)
    testenv_sweep.expose(t)
    t.book.update(t.key, lambda r: r.update(released_at="t"))  # 解放の後も閉じるまで残る
    assert testenv_sweep.unexpose(t) == "https://wt0.example.test"
    assert fake.calls[-1][1] == tmp_path  # worktree が無ければメインディレクトリで打つ


def test_unexpose_without_close_command_keeps_record(tmp_path: Path) -> None:
    fake = FakeShell()
    t = _sweep_target(tmp_path, fake, EXPOSE)
    _assigned(t)
    testenv_sweep.expose(t)
    without = {k: v for k, v in EXPOSE.items() if k != "close_command"}
    t2 = testenv.Target(t.path, t.main_dir, {"expose": without}, (), fake.shell(), t.book)
    with pytest.raises(testenv.TestenvError, match="公開を閉じる手段が宣言されていない"):
        testenv_sweep.unexpose(t2)
    assert t.book.current(t.key)["expose"]["closed_at"] is None and len(fake.calls) == 1  # type: ignore[index]


# 現状固定（I-012）: `testenv_sweep.reap` が止める行と残す行。期待値は今の実装の出力。
def test_reap_stops_only_unreleased_rows_idle_long_enough(tmp_path: Path) -> None:
    old, new = "2000-01-01T00:00:00Z", ledger.now()
    rows = [
        {"worktree": "/w/a", "environment": "env-a", "slot": 0, "last_used_at": old, "released_at": None},
        {"worktree": "/w/b", "environment": "env-b", "slot": 1, "last_used_at": old, "released_at": old},  # 解放済み
        {"worktree": "/w/c", "environment": "env-c", "slot": 2, "last_used_at": new, "released_at": None},  # 新しい
        {"worktree": "/w/d", "environment": "env-d", "slot": 3, "last_used_at": "broken", "released_at": None},  # 読めない
        {"worktree": "/w/e", "environment": "env-e", "slot": 4, "released_at": None},  # 時刻が無い
    ]
    book = ledger.Ledger(tmp_path / "reg.json")
    book.path.write_text(json.dumps({"assignments": rows}), encoding="utf-8")
    fake = FakeShell({"compose -p": Completed(0, "", "")})
    t = testenv.Target(tmp_path, tmp_path, {}, (), fake.shell(), book)
    assert testenv_sweep.reap(t, 3600) == [{"kind": "testenv", "name": "env-a", "worktree": "/w/a", "result": "stopped"}]
    assert [c[0] for c in fake.calls] == [["true", "compose", "-p", "env-a", "stop"]]


# 現状固定（I-026）: 公開中の判定。expose が dict で closed_at が無いか None の行だけが公開中（解放の後も閉じるまで）。
@pytest.mark.parametrize(
    ("expose", "want"),
    [
        (None, False),
        ("https://x", False),
        ({}, True),  # closed_at が無ければ公開中として扱う
        ({"url": "u", "closed_at": None}, True),
        ({"url": "u", "closed_at": "2026-01-01T00:00:00Z"}, False),
    ],
)
def test_open_row_reads_expose_shapes(expose: object, want: bool) -> None:
    released = {"worktree": "/w", "released_at": "2026-01-01T00:00:00Z", "expose": expose}
    rows = [{"worktree": "/w", "released_at": None, "expose": None}, released, {"worktree": "/x", "expose": {"closed_at": None}}]
    assert ledger.exposing(released) is want
    assert testenv_sweep._open_row(rows, "/w") == (released if want else None)


def test_testenv_down_keeps_slot_without_container_runtime(main_repo: Path, run_git: Callable) -> None:
    t = _testenv(main_repo, run_git, "feat/w")
    shell = container.Shell(runner=FakeShell().runner, attached=FakeShell().attached, docker="ndf-no-such-docker")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, shell, t.book)
    testenv.assign(t)
    with pytest.raises(testenv.TestenvError):
        testenv.down(t)
    assert t.book.current(t.key) is not None


def test_testenv_reclaim_keeps_slot_when_survey_fails(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell({"ps -a": Completed(1, "", "cannot connect")})
    t = _testenv(main_repo, run_git, "feat/x")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    gone = str(main_repo / ".worktrees" / "feat" / "gone2")
    t.book.acquire(gone, "feat/gone2", "env-gone2")
    assert [(i["name"], i["result"]) for i in testenv.reclaim(t)] == [("env-gone2", "kept")]
    assert t.book.current(gone) is not None


def test_testenv_reclaim_passes_compose_definition(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell({"ps -a": Completed(0, "c1\n", "")})
    t = _testenv(main_repo, run_git, "feat/y")
    gone = str(main_repo / ".worktrees" / "feat" / "gone3")
    fake.outputs["inspect -f"] = Completed(0, f"{gone}\n", "")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    t.book.acquire(gone, "feat/gone3", "env-gone3")
    assert [i["result"] for i in testenv.reclaim(t)] == ["reclaimed"]
    [(argv, cwd, env)] = [c for c in fake.calls if "down" in c[0]]
    assert argv[argv.index("-f") + 1] == os.path.realpath(t.main_dir / "compose.yml") and cwd == t.main_dir
    assert env == testenv.compose_values(t, t.book.rows()[0])  # 起動したときと同じ値を渡す


def test_testenv_up_failure_does_not_record_tag(main_repo: Path, run_git: Callable) -> None:
    fake = FakeShell({"compose -p": Completed(1, "", "boom")})
    t = _testenv(main_repo, run_git, "feat/z")
    t = testenv.Target(t.path, t.main_dir, t.section, t.compose_files, fake.shell(), t.book)
    assert testenv.up(t, value="t1") == 1
    assert "golden_tag" not in t.book.current(t.key)
    with pytest.raises(testenv.TestenvError):
        testenv_sweep.expose(t)


def test_testenv_unexpose_without_close_command_keeps_ledger(main_repo: Path, run_git: Callable) -> None:
    t = _testenv(main_repo, run_git, "feat/q")
    testenv.assign(t)
    t.book.update(t.key, lambda r: r.update(golden_tag="t1"))
    url = testenv_sweep.expose(t)
    section = {**t.section, "expose": {k: v for k, v in t.section["expose"].items() if k != "close_command"}}
    bare = testenv.Target(t.path, t.main_dir, section, t.compose_files, t.shell, t.book)
    with pytest.raises(testenv.TestenvError):
        testenv_sweep.unexpose(bare)
    assert testenv_sweep.unexpose(t) == url


def test_testenv_cli_values_fit_result_schema(main_repo: Path, run_git: Callable) -> None:
    from ndf.contracts import result

    t = _testenv(main_repo, run_git, "feat/o")
    args = argparse.Namespace(golden=None)
    for handler in (cli_env._te_env, cli_env._te_compose_env):
        out = handler("worktree", t, args)
        assert result.errors(out) == [] and "outputs" not in out
