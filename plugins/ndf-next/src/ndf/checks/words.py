"""禁止語の規則: 用語集が廃止した語（各語の `deprecated`）と禁止語（`check.banned.words`）を書かない。

用語集の置き場はリポジトリの用語集の設定（`.ndf/glossary.json` の `source`）から求める。用語集の設定が無い
リポジトリでは、この規則は何も見ない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ndf.checks.model import CheckError, Finding, Scope

KATAKANA = r"゠-ヿ"
ONLY_KATAKANA = re.compile(rf"^[{KATAKANA}]+$")
ONLY_ASCII = re.compile(r"^[\x00-\x7f]+$")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"用語集を読めない: {path}: {exc}") from exc


def banned_words(repo_root: Path, config_rel: str) -> dict[str, str]:
    """禁止する語 → 理由（替える語）。用語集の設定が無ければ空。"""
    config_path = repo_root / config_rel
    if not config_path.is_file():
        return {}
    config = _read(config_path)
    words: dict[str, str] = {}
    terms = [t for t in _read(repo_root / config["source"])["terms"] if "deprecated" in t]
    for term in terms:
        words.update({w: f"廃止した語。「{term['term']}」と書く" for w in term["deprecated"]})
    banned = config["check"]["banned"]["words"] if "banned" in config["check"] else []
    words.update({w: "禁止語" for w in banned})
    return words


def _part(word: str) -> str:
    escaped = re.escape(word)
    if ONLY_ASCII.match(word):
        return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    if ONLY_KATAKANA.match(word):
        return rf"(?<![{KATAKANA}]){escaped}(?![{KATAKANA}])"
    return escaped


def pattern(words: dict[str, str]) -> re.Pattern[str] | None:
    """長い語から当てる正規表現。ASCII の語とカタカナだけの語は、同じ字種の語の内側に当てない。"""
    ordered = sorted(words, key=len, reverse=True)
    return re.compile("|".join(_part(w) for w in ordered)) if ordered else None


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """プラグインの下のすべての文字のファイルから、禁止する語を行ごとに拾う。"""
    conf = rules["words"]
    words = banned_words(scope.repo_root, conf["glossary_config"])
    found_pattern = pattern(words)
    if found_pattern is None:
        return []
    found = []
    for src in scope.sources:
        if src.path.name in conf["skip_files"]:
            continue
        for no, line in enumerate(src.text.splitlines(), 1):
            found += [Finding("banned-word", src.rel, no, f"「{m.group()}」: {words[m.group()]}") for m in found_pattern.finditer(line)]
    return found
