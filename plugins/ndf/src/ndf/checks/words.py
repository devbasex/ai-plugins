"""禁止語の規則: 用語集が廃止した語（各語の `deprecated` と `deprecated_standalone`）と禁止語（`check.banned.words`）を書かない。

単独でマッチする廃止した語（`deprecated_standalone`。1 文字の語など）は、前後が漢字の出現に当てない（「開発版」「出版」に当てない）。

用語集の保存先はリポジトリの用語集の設定（`.ndf/glossary.json` の `source`）から求める。
避ける語の例として廃止した語を挙げる文書は、規則のデータ `words.examples` にファイルと語を書いて外す。用語集の設定が無い
リポジトリでは、この規則は何も見ない。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.checks.model import Finding, Scope, Source, read_json

KATAKANA = r"゠-ヿ"
ONLY_KATAKANA = re.compile(rf"^[{KATAKANA}]+$")
ONLY_ASCII = re.compile(r"^[\x00-\x7f]+$")
# 漢字の範囲（CJK 統合漢字・拡張 A・互換漢字と「々」）。単独でマッチする語の前後に来てはならない字
KANJI = "㐀-䶿一-鿿豈-﫿々"


@dataclass(frozen=True)
class BannedWords:
    """禁止する語 → 理由と、単独でマッチする語（前後が漢字の出現に当てない語）の集合。"""

    reasons: dict[str, str]
    standalone: frozenset[str]


def _read(path: Path) -> Any:
    return read_json(path, "用語集を読めない")


def banned_words(repo_root: Path, config_name: str) -> BannedWords:
    """禁止する語 → 理由（替える語）と単独でマッチする語。用語集の設定（設定の保存先の下の `config_name`）が無ければ空。"""
    config_path = repo_root / identity.FIXED_WORDS["ndf_settings_dir"] / config_name
    if not config_path.is_file():
        return BannedWords({}, frozenset())
    config = _read(config_path)
    glossary = _read(repo_root / config["source"])
    standalone = _standalone_from(glossary)
    reasons = {**standalone, **_words_from(config, glossary)}
    return BannedWords(reasons, frozenset(standalone))


def _words_from(config: dict[str, Any], glossary: dict[str, Any]) -> dict[str, str]:
    """用語集の設定と用語集から、禁止する語 → 理由（廃止した語は替える語、禁止語は「禁止語」）を作る。

    同じ廃止した語を複数の項目が持つときは、用語集で先に出る項目の語を理由に書く。
    """
    words: dict[str, str] = {}
    for term in (t for t in glossary["terms"] if "deprecated" in t):
        for w in term["deprecated"]:
            words.setdefault(w, f"廃止した語。「{term['term']}」と書く")
    banned = config["check"]["banned"]["words"] if "banned" in config["check"] else []
    words.update({w: "禁止語" for w in banned})
    return words


def _standalone_from(glossary: dict[str, Any]) -> dict[str, str]:
    """単独でマッチする廃止した語（`deprecated_standalone`）→ 理由。同じ語は先に出る項目の語を理由に書く。"""
    words: dict[str, str] = {}
    for term in (t for t in glossary["terms"] if "deprecated_standalone" in t):
        for w in term["deprecated_standalone"]:
            words.setdefault(w, f"廃止した語（単独の出現）。「{term['term']}」と書く")
    return words


def _part(word: str, standalone: bool) -> str:
    escaped = re.escape(word)
    if standalone:
        return rf"(?<![{KANJI}]){escaped}(?![{KANJI}])"
    if ONLY_ASCII.match(word):
        return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    if ONLY_KATAKANA.match(word):
        return rf"(?<![{KATAKANA}]){escaped}(?![{KATAKANA}])"
    return escaped


def pattern(words: Mapping[str, str], standalone: Set[str] = frozenset()) -> re.Pattern[str] | None:
    """長い語から当てる正規表現。ASCII の語とカタカナだけの語は同じ字種の語の内側に、単独の語は漢字の間に当てない。"""
    ordered = sorted(words, key=len, reverse=True)
    return re.compile("|".join(_part(w, w in standalone) for w in ordered)) if ordered else None


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """プラグインの下のすべての文字のファイルから、禁止する語を行ごとに拾う。"""
    conf = rules["words"]
    banned = banned_words(scope.repo_root, conf["glossary_config"])
    words = banned.reasons
    found_pattern = pattern(words, banned.standalone)
    if found_pattern is None:
        return []
    found = []
    for src in scope.sources:
        if src.path.name in conf["skip_files"]:
            continue
        found += _check_source(src, found_pattern, words, _examples(conf["examples"], scope.plugin_rel(src)))
    return found


def _check_source(src: Source, found_pattern: re.Pattern[str], words: dict[str, str], allowed: set[str]) -> list[Finding]:
    """1 つのファイルから、例として許した語（`allowed`）を除いて禁止する語を行ごとに拾う。"""
    found = []
    for no, line in enumerate(src.text.splitlines(), 1):
        hits = [m.group() for m in found_pattern.finditer(line) if m.group() not in allowed]
        found += [Finding("banned-word", src.rel, no, f"「{w}」: {words[w]}") for w in hits]
    return found


def _examples(examples: dict[str, dict[str, Any]], rel: str) -> set[str]:
    """そのファイルが言い換える前の語の例として挙げてよい語（規則のデータ `words.examples`。プラグインの根からのパス）。"""
    return set(examples[rel]["words"]) if rel in examples else set()
