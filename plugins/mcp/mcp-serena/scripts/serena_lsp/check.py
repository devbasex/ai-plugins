"""check: 導入の検査（公式 LSP プラグイン・本体・追加の検査）。導入のコマンドは載せるだけで打たない。"""
import json
import os
import shutil
from pathlib import Path

from . import project_yml as py
from . import table

CLAUDE_CODE = "claude-code"


class Unreadable(Exception):
    """欠けを判定できない（project.yml が無い・対応表が壊れている・installed_plugins.json が壊れている）。"""


def _typescript_major_5(lang: dict):
    """typescript-language-server が解決する typescript の版が 5 か。本体が無ければ本体の欠けに任せる。"""
    binary = shutil.which("typescript-language-server")
    if not binary:
        return None
    for directory in Path(os.path.realpath(binary)).parents:
        pkg = directory / "node_modules/typescript/package.json"
        if pkg.is_file():
            try:
                version = json.loads(pkg.read_text()).get("version", "")
            except (OSError, ValueError):
                version = ""
            if version.split(".")[0] == "5":
                return None
            return {"item": "typescript_major_5", "name": f"typescript {version or '不明'}",
                    "install": "npm install -g typescript@5"}
    return {"item": "typescript_major_5", "name": "typescript（見つからない）",
            "install": "npm install -g typescript@5"}


def _shellcheck(lang: dict):
    if shutil.which("shellcheck"):
        return None
    return {"item": "shellcheck", "name": "shellcheck",
            "install": "apt-get install shellcheck（macOS は brew install shellcheck）"}


# 名前 → (検査の関数, 当てるランタイム)。既にある種類で足りない言語だけ、ここへ関数を 1 つ足す
EXTRA_CHECKS = {
    "typescript_major_5": (_typescript_major_5, {CLAUDE_CODE}),
    "shellcheck": (_shellcheck, {CLAUDE_CODE, "codex"}),
}


def installed_plugins() -> set:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    path = Path(base) / "plugins/installed_plugins.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set((data.get("plugins") or {}).keys())
    except (OSError, ValueError, AttributeError) as exc:
        raise Unreadable(f"installed_plugins.json を読めません: {exc}") from exc


def _load_languages() -> dict:
    """対応表を言語名 → 定義で返す。読めない・知らない extra_checks があれば Unreadable。"""
    try:
        data = table.load()
    except (OSError, ValueError) as exc:
        raise Unreadable(f"対応表を読めません: {exc}") from exc
    unknown = {c for lang in data["languages"] for c in lang["extra_checks"]} - set(EXTRA_CHECKS)
    if unknown:
        raise Unreadable(f"対応表に知らない extra_checks があります: {', '.join(sorted(unknown))}")
    return table.by_language(data)


def _load_state(root) -> dict:
    try:
        state = py.load_state(root)
    except py.UnsupportedShape as exc:
        raise Unreadable(str(exc)) from exc
    if state is None:
        raise Unreadable("project.yml がありません")
    return state


def _missing_for_language(name: str, lang: dict, runtime: str, plugins: set) -> list:
    claude = runtime == CLAUDE_CODE
    missing = []
    if claude and lang["claude_plugin"] and lang["claude_plugin"] not in plugins:
        missing.append({"language": name, "item": "plugin", "name": lang["claude_plugin"],
                        "install": f"claude plugin install {lang['claude_plugin']}"})
    if claude:
        for binary in lang["binaries"]:
            if not shutil.which(binary["command"]):
                missing.append({"language": name, "item": "binary", "name": binary["command"],
                                "install": binary["install"]})
    for check_name in lang["extra_checks"]:
        func, runtimes = EXTRA_CHECKS[check_name]
        if runtime in runtimes:
            found = func(lang)
            if found:
                missing.append({"language": name, **found})
    return missing


def missing_items(root, runtime: str) -> list:
    langs = _load_languages()
    state = _load_state(root)
    plugins = installed_plugins() if runtime == CLAUDE_CODE else set()
    missing = []
    for name in state["languages"]:
        lang = langs.get(name)
        if lang is not None:
            missing += _missing_for_language(name, lang, runtime, plugins)
    return missing


def run(root, runtime=CLAUDE_CODE):
    try:
        missing = missing_items(root, runtime)
    except Unreadable as exc:
        return {"root": str(root), "missing": [], "error": str(exc)}, 2
    return {"root": str(root), "missing": missing}, 1 if missing else 0
