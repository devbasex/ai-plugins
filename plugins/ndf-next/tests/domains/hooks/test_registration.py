"""hook の登録（`hooks/<ランタイム>.json`）と、登録の command の 3 つの経路（S1-H1・S1-H2・S1-H3・S1-H5）。

登録の command は、ファイルに書かれた文字をそのまま `sh -c` で打つ（シェルがプラグインの根とキャッシュの根を展開する）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.domains.hooks import budget, cli, env, registration, tables

from ._hooks import AUTO, bash, make_repo, read_sample, records, sample_paths

SH = shutil.which("sh")
# 起動されたスクリプトが自分の argv を書く（環境の python と boot の経路の確かめ）
PROBE = "import json, os, sys\nwith open(os.environ['PROBE_OUT'], 'w') as f:\n    json.dump(sys.argv, f)\n"


def _registered(runtime: str) -> dict:
    return json.loads(registration.path_of(runtime).read_text(encoding="utf-8"))["hooks"]


def _commands() -> list[tuple[str, str, str]]:
    """（ランタイム・事象・ファイルの command）の一覧。"""
    return [(rt, ev, groups[0]["hooks"][0]["command"]) for rt in tables.runtimes() for ev, groups in _registered(rt).items()]


def _run(command: str, runtime: str, root: Path, cache: Path, payload: str = "{}", path: str | None = None):
    root_env = tables.runtimes()[runtime]["plugin_root_env"]
    extra = {root_env: str(root), "XDG_CACHE_HOME": str(cache), "PATH": path if path is not None else os.environ["PATH"]}
    return subprocess.run([SH, "-c", command], input=payload, capture_output=True, text=True, timeout=60, env={**os.environ, **extra})


@pytest.fixture
def probe_root(tmp_path) -> Path:
    """`hooks/entry.py` と `hooks/boot.py` を、argv を書くだけのスクリプトに替えたプラグインの根。"""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    for name in ("entry.py", "boot.py"):
        (root / "hooks" / name).write_text(PROBE, encoding="utf-8")
    return root


def _env_python(cache: Path) -> Path:
    """登録が指す環境の python（`env` と同じ置き場）を、このテストの python へのリンクとして置く。"""
    python = env.venv_python(env.Ident.of(tables.PLUGIN_ROOT, {"XDG_CACHE_HOME": str(cache)}))
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    return python


def test_registration_files_are_what_render_makes():
    # S1-H1: 登録は ndf hooks render が契約表と予算から作り、CI が作り直した結果と照らす
    assert registration.stale() == []


def test_one_registration_per_event_and_every_one_has_the_budget_timeout():
    # S1-H1・IH1・IH3: 事象ごとに 1 行、予算の timeout、予算の順序の破れが無い
    for runtime, rt in tables.runtimes().items():
        got = _registered(runtime)
        assert list(got) == list(rt["events"])
        for event, groups in got.items():
            assert len(groups) == 1 and len(groups[0]["hooks"]) == 1, (runtime, event)
            assert groups[0]["hooks"][0]["timeout"] == budget.budget_for(runtime, event).timeout
    assert budget.check_budget(tables.policies(), budget.registrations_of(tables.PLUGIN_ROOT)) == []


def test_codex_registers_the_events_its_contract_holds_and_names_why_guards_skip():
    # S1-H3: Codex の登録は契約表で Codex が持つ事象（UserPromptSubmit を含む）。働かない guard は理由を持つ
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


@pytest.mark.parametrize(("runtime", "event", "command"), _commands(), ids=lambda v: v if len(v) < 40 else "cmd")
def test_the_three_paths_start_the_script_with_runtime_and_event_in_the_same_place(
    runtime, event, command, probe_root, tmp_path, monkeypatch
):
    # S1-H5・決定 89: 環境の python がある経路は entry.py、無い経路は boot.py（entry.py のパスを外す）、python3 も無ければ何もしない
    out = tmp_path / "argv.json"
    monkeypatch.setenv("PROBE_OUT", str(out))
    cache = tmp_path / "cache"
    done = _run(command, runtime, probe_root, cache)
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == [str(probe_root / "hooks" / "boot.py"), runtime, event]
    out.unlink()
    _env_python(cache)
    done = _run(command, runtime, probe_root, cache)
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == [str(probe_root / "hooks" / "entry.py"), runtime, event]
    out.unlink()
    bare = tmp_path / "bare"
    bare.mkdir()
    done = _run(command, runtime, probe_root, tmp_path / "no-cache", path=str(bare))
    assert (done.returncode, done.stdout, out.exists()) == (0, "", False)


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


def test_without_the_environment_every_registered_event_passes_except_strict(tmp_path):
    # S1-H2: 環境を消した状態で全事象の登録を打つ。strict の guard に当たる入力のほかは出力なし・0（SessionStart は
    # 環境の用意を起動するので、M3-H14 のテスト test_process.py で通す）
    samples = {(p.parts[-3], p.stem): p for p in sample_paths()}
    cache = tmp_path / "no-venv"
    for runtime, event, command in _commands():
        if event == "SessionStart":
            continue
        path = samples.get((runtime, event))
        payload = json.dumps(read_sample(path, tmp_path)) if path else json.dumps({"hook_event_name": event})
        done = _run(command, runtime, tables.PLUGIN_ROOT, cache, payload)
        assert (done.returncode, done.stdout) == (0, ""), (runtime, event, done.stderr)
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    claude_pre = next(c for rt, ev, c in _commands() if (rt, ev) == ("claude", "PreToolUse"))
    denied = _run(claude_pre, "claude", tables.PLUGIN_ROOT, cache, json.dumps(bash(repo, "gh pr merge 1")))
    assert denied.returncode == 0 and "deny" in denied.stdout
    assert {r["path"] for r in records()} == {"boot"}
    assert not cache.exists(), "環境が無い経路の SessionStart 以外は環境を用意しない"


def test_without_python3_every_registered_event_does_nothing(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    for runtime, _, command in _commands():
        done = _run(command, runtime, tables.PLUGIN_ROOT, tmp_path / "no-venv", path=str(bare))
        assert (done.returncode, done.stdout) == (0, "")


def test_hook_scripts_pass_when_the_body_cannot_be_read(monkeypatch):
    # IH4: 本体を読めない環境・python でも、エントリポイントは 0 で通す
    import importlib.util

    for name, body in (("entry", "ndf.domains.hooks.main"), ("boot", "ndf.domains.hooks.boot")):
        spec = importlib.util.spec_from_file_location(f"_hook_{name}_broken", tables.PLUGIN_ROOT / "hooks" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, body, None)
        assert module.main([f"{name}.py", "claude", "Stop"]) == 0
