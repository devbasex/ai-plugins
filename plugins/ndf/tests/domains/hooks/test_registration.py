"""hook の登録（`hooks/<ランタイム>.json`）と、登録の command の 3 つの経路（S1-H1・S1-H2・S1-H3・S1-H5）。

登録の command は、ファイルに書かれた文字をそのまま `sh -c` で打つ（シェルがプラグインの根とキャッシュの根を展開する）。
"""

from __future__ import annotations

import json
import shutil
import sys

from ndf.domains.hooks import budget, cli, registration, tables


def _registered(runtime: str) -> dict:
    return json.loads(registration.path_of(runtime).read_text(encoding="utf-8"))["hooks"]


def _commands() -> list[tuple[str, str, str]]:
    """（ランタイム・事象・ファイルの command）の一覧。"""
    return [(rt, ev, groups[0]["hooks"][0]["command"]) for rt in tables.runtimes() for ev, groups in _registered(rt).items()]


def test_registration_files_are_what_render_makes():
    # S1-H1: 登録は ndf hooks render がコントラクト表と予算から作り、CI が作り直した結果と照らす
    assert registration.stale() == []


def test_one_registration_per_event_and_every_one_has_the_budget_timeout():
    # S1-H1・IH1・IH3: 事象ごとに 1 行、予算の timeout、予算の順序の破れが無い
    for runtime, rt in tables.runtimes().items():
        got = _registered(runtime)
        assert list(got) == list(rt["events"])
        for event, groups in got.items():
            assert len(groups) == 1 and len(groups[0]["hooks"]) == 1, (runtime, event)
            assert groups[0]["hooks"][0]["timeout"] == budget.budget_for(runtime, event).timeout
    assert budget.check_budget(budget.registrations_of(tables.PLUGIN_ROOT)) == []


def test_codex_registers_the_events_its_contract_holds_and_names_why_guards_skip():
    # S1-H3: Codex の登録はコントラクト表で Codex が持つ事象（UserPromptSubmit を含む）。働かない guard は理由を持つ
    codex = tables.runtimes()["codex"]
    assert list(_registered("codex")) == list(codex["events"])
    assert "UserPromptSubmit" in _registered("codex")
    assert codex["skipped"] and all(reason.strip() for reason in codex["skipped"].values())


def test_render_check_passes_and_reports_a_stale_file(tmp_path, capsys, last_result):
    assert cli.main(["render", "--check"]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"
    copy = tmp_path / "plugin"
    shutil.copytree(tables.PLUGIN_ROOT / ".claude-plugin", copy / ".claude-plugin")
    shutil.copy(tables.PLUGIN_ROOT / "uv.lock", copy / "uv.lock")
    assert sorted(p.name for p in registration.stale(copy)) == ["claude.json", "codex.json"]
    assert sorted(p.name for p in registration.write(copy)) == ["claude.json", "codex.json"]
    assert registration.stale(copy) == []


def test_boot_and_entry_read_the_same_runtime_and_event(monkeypatch):
    # S1-H5: hooks/boot.py の main と hooks/entry.py の main は同じ位置のランタイムと事象を本体へ渡す
    import importlib.util

    from ndf.domains.hooks import boot, main

    seen = []
    monkeypatch.setattr(main, "run_entry", lambda argv: seen.append(("entry", argv[1:])) or 0)
    monkeypatch.setattr(boot, "run_boot", lambda argv: seen.append(("boot", argv[1:])) or 0)
    for name in ("entry", "boot"):
        spec = importlib.util.spec_from_file_location(f"_hook_{name}", tables.PLUGIN_ROOT / "hooks" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for runtime, event, _ in _commands():
            assert module.main([f"{name}.py", runtime, event]) == 0
    entry_args = [a for k, a in seen if k == "entry"]
    assert entry_args == [a for k, a in seen if k == "boot"] == [[rt, ev] for rt, ev, _ in _commands()]


def test_hook_scripts_pass_when_the_body_cannot_be_read(monkeypatch):
    # IH4: 本体を読めない環境・python でも、エントリポイントは 0 で通す
    import importlib.util

    for name, body in (("entry", "ndf.domains.hooks.main"), ("boot", "ndf.domains.hooks.boot")):
        spec = importlib.util.spec_from_file_location(f"_hook_{name}_broken", tables.PLUGIN_ROOT / "hooks" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, body, None)
        assert module.main([f"{name}.py", "claude", "Stop"]) == 0


def test_plugin_registration_has_no_async_rewake():
    # #4068 の受け入れ条件 3: 起床の hook はプラグインの登録（render と hooks/claude.json）に載せない
    assert "asyncRewake" not in json.dumps(registration.render("claude"))
    assert "asyncRewake" not in registration.path_of("claude").read_text(encoding="utf-8")
