"""対応表（languages.json）を読む。言語を足すときに変えるのはこのファイルの読む先だけである。"""
import json
import os
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parents[1] / "languages.json"


def load(path=None) -> dict:
    """対応表を読む。環境変数 SERENA_LSP_TABLE は差し替え（テストと利用者の上書き）に使う。"""
    target = Path(path or os.environ.get("SERENA_LSP_TABLE") or DEFAULT_TABLE)
    return json.loads(target.read_text(encoding="utf-8"))


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
