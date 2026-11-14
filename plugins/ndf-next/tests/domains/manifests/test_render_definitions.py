"""表から作るランタイムの定義と policy の生成・不要な policy の削除と、Skill の frontmatter の読み取り。"""

from __future__ import annotations

import json
import os

import pytest

from ndf.domains.manifests import front_matter, render

from ._manifests import _skill, _write_table


def test_render_lists_present_codex_skills_and_writes_policies_for_explicit_only_skills(plugin):
    _write_table(
        plugin,
        [
            {"name": "explicit", "runtimes": ["claude", "codex"]},
            {"name": "implicit", "runtimes": ["claude", "codex"]},
            {"name": "claude-only", "runtimes": ["claude"], "why": "Claude Code だけ"},
            {"name": "not-moved", "runtimes": ["claude", "codex"]},
        ],
    )
    _skill(plugin, "explicit", **{"disable-model-invocation": True, "argument-hint": '[PR 番号] "x"'})
    _skill(plugin, "implicit", description="a: b")
    _skill(plugin, "claude-only", **{"disable-model-invocation": True})

    written = render.write(plugin)

    codex = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert codex["skills"] == ["./skills/explicit", "./skills/implicit"]
    policy = plugin / "skills" / "explicit" / "agents" / "openai.yaml"
    assert (
        policy.read_text(encoding="utf-8")
        == 'policy:\n  allow_implicit_invocation: false\ninterface:\n  default_prompt: "[PR 番号] \\"x\\""\n'
    )
    assert not (plugin / "skills" / "implicit" / "agents").exists()
    assert not (plugin / "skills" / "claude-only" / "agents").exists()
    assert policy in written
    assert render.stale(plugin) == []


def test_render_removes_a_policy_no_longer_wanted_and_check_reports_it(plugin):
    _write_table(plugin, [{"name": "s", "runtimes": ["claude", "codex"]}])
    _skill(plugin, "s", **{"disable-model-invocation": True})
    render.write(plugin)
    _skill(plugin, "s", description="もう明示だけではない")
    policy = plugin / "skills" / "s" / "agents" / "openai.yaml"

    assert render.stale(plugin) == [policy]
    assert render.write(plugin) == [policy]
    assert not policy.parent.exists()


def test_render_write_returns_what_stale_found_and_leaves_matching_files_untouched(plugin):
    # 現状固定（I-018）: write が書く集合は stale が見つけた集合と同じ。中身が同じファイルは書き直さず、空を返す
    _write_table(plugin, [{"name": "s", "runtimes": ["claude", "codex"]}, {"name": "t", "runtimes": ["claude", "codex"]}])
    _skill(plugin, "s", **{"disable-model-invocation": True})
    _skill(plugin, "t")
    definition = plugin / ".codex-plugin" / "plugin.json"
    policy = plugin / "skills" / "s" / "agents" / "openai.yaml"

    assert render.stale(plugin) == [definition, policy]
    assert render.write(plugin) == [definition, policy]

    old = 1_000_000_000
    for path in (definition, policy):
        os.utime(path, ns=(old, old))
    assert render.stale(plugin) == []
    assert render.write(plugin) == []
    assert [p.stat().st_mtime_ns for p in (definition, policy)] == [old, old]


def test_a_skill_outside_the_table_stops_the_render(plugin):
    _write_table(plugin, [])
    _skill(plugin, "stray")
    with pytest.raises(render.RenderError, match="stray"):
        render.expected(plugin)


def test_front_matter_refuses_missing_or_unclosed_fences():
    for text in ("本文だけ\n", "---\nname: a\n", "---\n- a\n---\n", "---\nname: [\n---\n"):
        with pytest.raises(front_matter.FrontMatterError):
            front_matter.read(text, "t")
    assert front_matter.read("---\n---\n", "t") == {}
