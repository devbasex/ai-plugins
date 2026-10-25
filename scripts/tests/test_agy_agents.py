"""agy に配るエージェント定義の生成（scripts/lib/agy_agents.py。#2087）。

agy 1.4 系は `model` に tier 名（`flash` / `pro` / `inherit`）しか受け付けない。`dev.agy/agents/` には
`plugins/ndf/agents/` の写しを置き、`model:` の 1 行だけを tier 名へ変える。agy の無い環境で、
写しの追従し忘れ・手直し・余り・足りなさと、受け付けられない `model` を見つける。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from agy_build_helpers import agents_dir, agy_agents_dir, build_tree, output_of, run_build

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins/ndf"

_spec = importlib.util.spec_from_file_location("agy_agents", REPO_ROOT / "scripts/lib/agy_agents.py")
assert _spec and _spec.loader
agy_agents = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agy_agents)


def _agent(name: str, model: str | None, body: str = "本文") -> str:
    model_line = f"model: {model}\n" if model is not None else ""
    return f"---\nname: {name}\n{model_line}description: |\n  {name} の説明\n---\n\n{body}\n"


def _tree(tmp_path: Path, agents: dict[str, str | None]) -> Path:
    root = build_tree(tmp_path)
    for name, model in agents.items():
        (agents_dir(root) / f"{name}.md").write_text(_agent(name, model), encoding="utf-8")
    return root


def _generated(root: Path) -> Path:
    result = run_build(root)
    assert result.returncode == 0, output_of(result)
    return agy_agents_dir(root)


def test_repo_copies_use_agy_tiers() -> None:
    """配る写しの `model` は tier 名か欄なし（受け入れ条件 7・I3）。"""
    copies = sorted((PLUGIN_DIR / "dev.agy/agents").glob("*.md"))
    assert copies
    for path in copies:
        model = agy_agents.frontmatter_model(path.read_text(encoding="utf-8"))
        assert model is None or model in agy_agents.AGY_TIERS, f"{path}: {model}"


def test_repo_copies_follow_every_definition() -> None:
    """写しのファイル名の集合は、エージェント定義の集合と等しい（I1）。"""
    assert not (PLUGIN_DIR / "dev.agy/agents").is_symlink()
    sources = sorted(p.name for p in (PLUGIN_DIR / "agents").glob("*.md"))
    copies = sorted(p.name for p in (PLUGIN_DIR / "dev.agy/agents").iterdir())
    assert copies == sources


def test_model_map_values_are_tiers() -> None:
    """対応表の値はすべて tier 名（I4）。"""
    assert set(agy_agents.MODEL_MAP.values()) <= agy_agents.AGY_TIERS


def test_build_converts_model_and_keeps_the_rest(tmp_path: Path) -> None:
    """`model:` の 1 行だけが変わり、ほかは元とバイト列で一致する（受け入れ条件 6・7、I2）。"""
    root = _tree(tmp_path, {"cheap": "sonnet", "strong": "opus", "plain": None})
    out = _generated(root)
    for name, tier in (("cheap", "flash"), ("strong", "pro"), ("plain", None)):
        source = (agents_dir(root) / f"{name}.md").read_text(encoding="utf-8")
        copy = (out / f"{name}.md").read_text(encoding="utf-8")
        assert agy_agents.frontmatter_model(copy) == tier
        assert [line for line in source.splitlines() if not line.startswith("model:")] == [
            line for line in copy.splitlines() if not line.startswith("model:")
        ]
    assert run_build(root, check=True).returncode == 0


def test_build_does_not_touch_the_definitions(tmp_path: Path) -> None:
    """古い symlink のままでも、生成は元の定義を書き換えない。`--check` は symlink を落とす（I6）。"""
    root = _tree(tmp_path, {"cheap": "sonnet"})
    before = (agents_dir(root) / "cheap.md").read_bytes()
    shutil.rmtree(_generated(root))
    os.symlink("../agents", agy_agents_dir(root))

    result = run_build(root, check=True)
    assert result.returncode == 1
    assert "Generated agents must be files, not a symlink" in output_of(result)

    out = _generated(root)
    assert not out.is_symlink() and out.is_dir()
    assert (agents_dir(root) / "cheap.md").read_bytes() == before
    assert run_build(root, check=True).returncode == 0


def test_check_detects_missing_copy(tmp_path: Path) -> None:
    """定義を足して生成し直さないと `--check` が落ち、足りない写しを示す（I1）。"""
    root = _tree(tmp_path, {"cheap": "sonnet"})
    _generated(root)
    (agents_dir(root) / "added.md").write_text(_agent("added", "sonnet"), encoding="utf-8")
    result = run_build(root, check=True)
    assert result.returncode == 1
    assert "Generated agent missing: plugins/ndf/dev.agy/agents/added.md" in output_of(result)
    assert (_generated(root) / "added.md").is_file()


def test_check_detects_stale_copy(tmp_path: Path) -> None:
    """定義を消して生成し直さないと `--check` が落ち、余った写しを示す。生成し直すと消える（I1）。"""
    root = _tree(tmp_path, {"cheap": "sonnet", "gone": "opus"})
    _generated(root)
    (agents_dir(root) / "gone.md").unlink()
    result = run_build(root, check=True)
    assert result.returncode == 1
    assert "Generated agent is stale: plugins/ndf/dev.agy/agents/gone.md" in output_of(result)
    assert not (_generated(root) / "gone.md").exists()


def test_check_detects_body_drift_and_hand_edits(tmp_path: Path) -> None:
    """定義の本文を変えて生成し直さない・写しを手で直す、のどちらも `--check` が落ちる（I2）。"""
    root = _tree(tmp_path, {"cheap": "sonnet"})
    out = _generated(root)
    source = agents_dir(root) / "cheap.md"
    source.write_text(source.read_text(encoding="utf-8").replace("本文", "本文!"), encoding="utf-8")
    result = run_build(root, check=True)
    assert result.returncode == 1
    assert "Generated agent is out of date: plugins/ndf/dev.agy/agents/cheap.md" in output_of(result)

    _generated(root)
    copy = out / "cheap.md"
    copy.write_text(copy.read_text(encoding="utf-8").replace("model: flash", "model: sonnet"), encoding="utf-8")
    assert run_build(root, check=True).returncode == 1


def test_unknown_model_stops_build_and_check(tmp_path: Path) -> None:
    """対応表に無い値は、生成も `--check` も止め、パスと値を示す（I5）。"""
    root = _tree(tmp_path, {"odd": "claude-opus-5-5"})
    for check in (False, True):
        result = run_build(root, check=check)
        assert result.returncode == 1
        assert "Unknown model for agy: plugins/ndf/agents/odd.md: claude-opus-5-5" in output_of(result)
    assert not agy_agents_dir(root).exists()


def test_broken_model_map_stops_build(tmp_path: Path) -> None:
    """対応表の値が tier 名でないと、生成を始めずに止まる（I4）。"""
    root = _tree(tmp_path, {"cheap": "sonnet"})
    plugin_dir = root / "plugins/ndf"
    assert agy_agents.sync(plugin_dir, check=False, model_map={"sonnet": "sonnet"}) is False
    assert not agy_agents_dir(root).exists()
