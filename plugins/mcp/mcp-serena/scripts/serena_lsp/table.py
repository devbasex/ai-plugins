"""対応表（languages.json）を読む。言語を足すときに変えるのはこのファイルの読む先だけである。"""
import json
import os
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parents[1] / "languages.json"


SUPPORTED_VERSION = 1


class InvalidTable(ValueError):
    """対応表の形が version 1 の約束と違う。JSON の壊れと同じく ValueError として扱える。"""


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require(ok: bool, what: str) -> None:
    if not ok:
        raise InvalidTable(what)


def validate(data) -> dict:
    """version 1 の最上位・threshold・各言語の形を確かめ、そのまま返す。"""
    _require(isinstance(data, dict), "最上位が object ではありません")
    _require(data.get("version") == SUPPORTED_VERSION, f"対応していない version です: {data.get('version')!r}")
    threshold = data.get("threshold")
    _require(isinstance(threshold, dict) and _is_number(threshold.get("min_files"))
             and _is_number(threshold.get("min_share")), "threshold の形が違います")
    _require(isinstance(data.get("languages"), list), "languages が配列ではありません")
    for lang in data["languages"]:
        _require(isinstance(lang, dict) and isinstance(lang.get("serena"), str), "言語の serena がありません")
        name = lang["serena"]
        _require(_is_str_list(lang.get("extensions")), f"{name} の extensions の形が違います")
        _require(lang.get("claude_plugin") is None or isinstance(lang["claude_plugin"], str),
                 f"{name} の claude_plugin の形が違います")
        _require(isinstance(lang.get("binaries"), list) and all(
            isinstance(b, dict) and isinstance(b.get("command"), str) and isinstance(b.get("install"), str)
            for b in lang["binaries"]), f"{name} の binaries の形が違います")
        _require(_is_str_list(lang.get("extra_checks")), f"{name} の extra_checks の形が違います")
    return data


def load(path=None) -> dict:
    """対応表を読み、検証済みの辞書を返す。環境変数 SERENA_LSP_TABLE は差し替え（テストと利用者の上書き）に使う。"""
    target = Path(path or os.environ.get("SERENA_LSP_TABLE") or DEFAULT_TABLE)
    return validate(json.loads(target.read_text(encoding="utf-8")))


def extension_map(data: dict) -> dict:
    """拡張子（小文字）→ Serena の言語。"""
    return {ext: lang["serena"] for lang in data["languages"] for ext in lang["extensions"]}


def by_language(data: dict) -> dict:
    return {lang["serena"]: lang for lang in data["languages"]}


def extensions_of(data: dict, languages) -> set:
    langs = by_language(data)
    return {ext for name in languages if name in langs for ext in langs[name]["extensions"]}


def suffix(path: str) -> str:
    """最後の `.` 以降を小文字で返す。拡張子が無ければ空文字。"""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""
