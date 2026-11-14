"""配る Skill の表（`manifests/skills.json`）と、その表から作るランタイムの定義（3341-7: Codex の定義の並び・版数）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ndf import identity
from ndf.domains.hooks import registration
from ndf.domains.manifests import cli, front_matter, render, table
from ndf.parity import table as parity

ROOT = identity.ROOT
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
# 根の検査（scripts/lib/version_pattern.py）が description に求める版の形
VERSION_MARK = "(v{})"


def _skill(root: Path, name: str, **fields) -> Path:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in {"name": name, **fields}.items())
    path.write_text(f"---\n{head}\n---\n\n本文\n", encoding="utf-8")
    return path


@pytest.fixture
def plugin(tmp_path) -> Path:
    """表と Codex の定義だけを写したプラグインの根。"""
    root = tmp_path / "plugin"
    for rel in (str(table.TABLE_REL), ".codex-plugin/plugin.json"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, root / rel)
    return root


def _write_table(root: Path, skills: list, runtimes=("claude", "codex")) -> None:
    (root / table.TABLE_REL).write_text(json.dumps({"runtimes": list(runtimes), "skills": skills}), encoding="utf-8")


def test_the_table_lists_the_skills_s1a_moves():
    # 配る Skill の表は、S1a で移す Skill の名前の一覧（機能の対応表の Skill の行）から作った
    moved = {r["id"].split(":", 1)[1] for r in parity.load("features")["rows"] if r["kind"] == "skill" and r["to"] == "S1a"}
    assert {r.name for r in table.load(ROOT)} == moved


def test_skills_not_sent_to_every_runtime_say_why():
    rows = table.load(ROOT)
    partial = [r for r in rows if set(r.runtimes) != {"claude", "codex"}]
    assert partial and all(r.why for r in partial)
    assert set(table.for_runtime(rows, "codex")) == {r.name for r in rows} - {r.name for r in partial}


@pytest.mark.parametrize(
    ("skills", "word"),
    [
        ([{"name": "a", "runtimes": ["codex"], "why": "x"}], "claude"),
        ([{"name": "a", "runtimes": ["claude"]}], "why"),
        ([{"name": "a", "runtimes": ["claude", "codex"], "why": "x"}], "why"),
        ([{"name": "a", "runtimes": ["claude", "kiro"], "why": "x"}], "runtimes"),
        ([{"name": "a", "runtimes": ["claude", "codex"]}, {"name": "a", "runtimes": ["claude", "codex"]}], "2 回"),
        ([{"name": "../a", "runtimes": ["claude", "codex"]}], "名前"),
        ([{"runtimes": ["claude"]}], "形"),
    ],
)
def test_a_broken_table_is_refused(plugin, skills, word):
    _write_table(plugin, skills)
    with pytest.raises(table.TableError, match=word):
        table.load(plugin)


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
    assert policy.read_text(encoding="utf-8") == 'policy:\n  allow_implicit_invocation: false\ninterface:\n  default_prompt: "[PR 番号] \\"x\\""\n'
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


def test_the_plugin_definitions_are_what_render_makes(capsys, last_result):
    # CI で作り直した結果と照らす
    assert render.stale(ROOT) == []
    assert cli.main(["render", "--check"]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"


def test_render_check_stops_on_a_difference(plugin, monkeypatch, capsys, last_result):
    _write_table(plugin, [{"name": "s", "runtimes": ["claude", "codex"]}])
    _skill(plugin, "s")
    monkeypatch.setattr(identity, "ROOT", plugin)
    assert cli.main(["render", "--check"]) != 0
    report = last_result(capsys.readouterr().out)
    assert report["cause"]["kind"] == "check_failed"
    assert report["items"] == [{"path": ".codex-plugin/plugin.json"}]
    _write_table(plugin, [{"name": "s", "runtimes": ["codex"], "why": "x"}])
    assert cli.main(["render"]) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "config_broken"


def test_both_definitions_carry_the_same_name_and_version_and_the_version_in_the_description():
    # 3341-7: Codex の定義の版数は Claude Code の定義と同じ。根の検査（validate-runtime-plugins.sh）は push でだけ
    # 走るので、description の版の形もここで確かめる
    data = [json.loads((ROOT / rel).read_text(encoding="utf-8")) for rel in MANIFESTS]
    assert data[0]["name"] == data[1]["name"] == identity.load().name
    assert data[0]["version"] == data[1]["version"]
    for d in data:
        assert VERSION_MARK.format(d["version"]) in d["description"]


def test_both_definitions_point_at_their_hook_registration():
    for rel, runtime in zip(MANIFESTS, ("claude", "codex")):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert (ROOT / data["hooks"]).resolve() == registration.path_of(runtime).resolve()
