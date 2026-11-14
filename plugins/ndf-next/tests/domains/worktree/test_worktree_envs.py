"""worktree の依存の用意・ローカルの動作検証・テスト環境を、一時の git のリポジトリと差し替えた実行系で確かめる。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable, Mapping, Optional

import pytest

from ndf.adapters.git import Completed, Git
from ndf.domains.worktree import cli_env, container, create, deps, ledger, localenv, localenv_decide, testenv, testenv_sweep


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
               "expose": {"enabled": True, "public_tag": "t1", "base_domain": "example.test", "open_command": "true"}}  # fmt: skip
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
