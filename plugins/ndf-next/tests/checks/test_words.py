from __future__ import annotations

import json

from ndf.checks import words


def glossary(plugin) -> None:
    (plugin.repo / ".ndf").mkdir()
    config = {"source": "g.json", "check": {"banned": {"words": ["旧名"]}}}
    (plugin.repo / ".ndf" / "glossary.json").write_text(json.dumps(config, ensure_ascii=False))
    terms = [{"term": "リリース", "deprecated": ["旧い語", "ゲート", "gate"]}, {"term": "x"}]
    (plugin.repo / "g.json").write_text(json.dumps({"terms": terms}, ensure_ascii=False))


def test_deprecated_and_banned_words_are_found(plugin, rules):
    glossary(plugin)
    plugin.write("skills/a/SKILL.md", "旧い語を書く\n旧名もある\n")
    plugin.write("src/ndf/a.py", "# gate を見る\n")
    found = words.check(plugin.scope(), rules)
    assert [(f.path.rsplit("/", 1)[-1], f.line) for f in found] == [("SKILL.md", 1), ("SKILL.md", 2), ("a.py", 1)]
    assert "リリース" in found[0].detail


def test_words_inside_same_script_words_are_not_hits(plugin, rules):
    glossary(plugin)
    plugin.write("src/ndf/a.py", "# 承認ゲートウェイ と gateway と gate_x\n")
    assert words.check(plugin.scope(), rules) == []


def test_no_glossary_config_means_no_words(plugin, rules):
    plugin.write("src/ndf/a.py", "# 旧名\n")
    assert words.check(plugin.scope(), rules) == []
