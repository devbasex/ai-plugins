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


def test_example_words_are_skipped_only_in_the_listed_file(plugin, rules):
    glossary(plugin)
    examples = {"skills/a/SKILL.md": {"words": ["旧い語"], "reason": "言い換える前の語の例"}}
    rules = {**rules, "words": {**rules["words"], "examples": examples}}
    plugin.write("skills/a/SKILL.md", "旧い語を例に挙げる\n旧名もある\n")
    plugin.write("skills/b/SKILL.md", "旧い語を書く\n")
    found = words.check(plugin.scope(), rules)
    assert [(f.path.split("/")[-2], f.line) for f in found] == [("a", 2), ("b", 1)]


def standalone_glossary(plugin) -> None:
    (plugin.repo / ".ndf").mkdir()
    (plugin.repo / ".ndf" / "glossary.json").write_text(json.dumps({"source": "g.json", "check": {}}))
    terms = [
        {"term": "サブコマンド", "deprecated": ["サブコマンド"]},
        {"term": "バージョン", "deprecated_standalone": ["バージョン"]},
        {"term": "質問", "deprecated_standalone": ["質問"]},
    ]
    (plugin.repo / "g.json").write_text(json.dumps({"terms": terms}, ensure_ascii=False))


def test_a_newly_written_deprecated_word_is_one_finding(plugin, rules):
    standalone_glossary(plugin)
    plugin.write("skills/a/SKILL.md", "見出し\nサブコマンドを打つ\n")
    found = words.check(plugin.scope(), rules)
    assert [(f.line, f.detail) for f in found] == [(2, "「サブコマンド」: 廃止した語。「サブコマンド」と書く")]


def test_standalone_words_hit_only_outside_kanji(plugin, rules):
    standalone_glossary(plugin)
    plugin.write("skills/a/SKILL.md", "バージョンの記録\n開発版を出す\n出版する\n版数\n問い合わせる\n質問を出す\nバージョン\n")
    found = words.check(plugin.scope(), rules)
    assert [f.line for f in found] == [1, 6, 7]
    assert "単独の出現" in found[0].detail


def test_first_term_wins_for_a_word_listed_twice():
    glossary = {"terms": [{"term": "A", "deprecated": ["旧"]}, {"term": "B", "deprecated": ["旧"]}]}
    assert words._words_from({"check": {}}, glossary) == {"旧": "廃止した語。「A」と書く"}


def test_standalone_condition_applies_before_the_script_rules():
    found = words.pattern({"ab": "x"}, frozenset({"ab"}))
    assert found is not None
    assert found.search("xab") is not None
    assert found.search("字ab") is None
