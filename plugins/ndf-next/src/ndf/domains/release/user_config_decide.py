"""利用者の設定ディレクトリの控えの行を作り、前後の控えを比べる（純粋な関数）。

リリース後テストは、テストの前後に利用者の設定（Claude Code の導入一覧と取得元、Codex の設定と導入の置き場）を
読むだけで控え、変わっていないことを確かめる（利用者の環境を黙って書き換えない。C6）。

- Claude Code が組織の管理の設定から入れる行（`scope: managed`）は、利用者が自分で変えられず、Claude Code が
  一時的に書くことがあるため控えに入れない（#2896）
- 取得元の URL の利用者の情報（`user:pass@`）は伏せる
- 時刻・置き場所・自動更新の設定は写さない
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# 控えに入れない導入の範囲（組織の管理の設定から入る行。#2896）
UNMANAGED_SKIP = frozenset({"managed"})
# 範囲の既定（導入一覧の行に scope が無いとき）
DEFAULT_SCOPE = "user"
# 取得元の URL の利用者の情報
_USERINFO = re.compile(r"([A-Za-z][\w+.-]*://)[^/@\s]+@")
# Codex の取得元の表から写す鍵
CODEX_SOURCE_KEYS = ("source_type", "source", "ref")
# 控えの鍵の前置き
CLAUDE_PLUGIN, CLAUDE_MARKET = "claude:plugin:", "claude:marketplace:"
CODEX_PLUGIN, CODEX_MARKET = "codex:plugin:", "codex:marketplace:"


class ShapeError(ValueError):
    """設定のファイルの形が違う。"""


def mask(value: Any) -> Any:
    """取得元の URL の利用者の情報を伏せる（入れ子の値もたどる）。"""
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return _USERINFO.sub(r"\1***@", value) if isinstance(value, str) else value


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row[key] if key in row else None
    return str(value) if value not in (None, "") else "-"


def _claude_row(row: Mapping[str, Any]) -> str | None:
    """導入一覧の 1 行の控え。管理の設定から入る行は None（控えに入れない）。"""
    scope = _text(row, "scope") if "scope" in row else DEFAULT_SCOPE
    if scope in UNMANAGED_SKIP:
        return None
    where = "" if scope == DEFAULT_SCOPE else f"({_text(row, 'projectPath')})"
    sha = _text(row, "gitCommitSha")
    return f"{scope}{where}:{_text(row, 'version')}" + ("" if sha == "-" else f"@{sha[:8]}")


def claude_plugins(installed: Any) -> dict[str, str]:
    """Claude Code の導入一覧（`installed_plugins.json`）の控え。管理の行だけのプラグインは鍵ごと入れない。"""
    plugins = installed["plugins"] if isinstance(installed, dict) and "plugins" in installed else None
    if not isinstance(plugins, dict) or not all(isinstance(rows, list) for rows in plugins.values()):
        raise ShapeError("installed_plugins.json の plugins の形が違う")
    out = {}
    for name, rows in plugins.items():
        kept = [r for r in (_claude_row(row) for row in rows if isinstance(row, dict)) if r is not None]
        if kept:
            out[f"{CLAUDE_PLUGIN}{name}"] = " ".join(kept)
    return out


def claude_marketplaces(known: Any) -> dict[str, str]:
    """Claude Code の取得元（`known_marketplaces.json`）の控え。取得元の値だけを写す。"""
    if not isinstance(known, dict):
        raise ShapeError("known_marketplaces.json が表でない")
    out = {}
    for name, entry in known.items():
        source = entry["source"] if isinstance(entry, dict) and "source" in entry else None
        out[f"{CLAUDE_MARKET}{name}"] = json.dumps(mask(source), sort_keys=True, ensure_ascii=False)
    return out


def _table(conf: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = conf[key] if key in conf else None
    return value if isinstance(value, dict) else {}


def _codex_marketplaces(conf: Mapping[str, Any]) -> dict[str, str]:
    """Codex の取得元（`config.toml` の marketplaces）の控え。取得元の鍵だけを写す。"""
    out = {}
    for name, entry in _table(conf, "marketplaces").items():
        fields = {k: entry[k] if isinstance(entry, dict) and k in entry else None for k in CODEX_SOURCE_KEYS}
        out[f"{CODEX_MARKET}{name}"] = json.dumps(mask(fields), sort_keys=True, ensure_ascii=False)
    return out


def _codex_plugins(conf: Mapping[str, Any], cached: Mapping[str, list[str]]) -> dict[str, str]:
    """Codex の導入（`config.toml` の plugins と導入の置き場の版）の控え。"""
    plugins = _table(conf, "plugins")
    out = {}
    for name in sorted(set(plugins) | set(cached)):
        entry = plugins[name] if name in plugins and isinstance(plugins[name], dict) else {}
        enabled = str(entry["enabled"]).lower() if "enabled" in entry else "-"
        versions = ",".join(sorted(cached[name])) if name in cached else ""
        out[f"{CODEX_PLUGIN}{name}"] = f"enabled={enabled} versions={versions}"
    return out


def codex_entries(conf: Mapping[str, Any], cached: Mapping[str, list[str]]) -> dict[str, str]:
    """Codex の設定（`config.toml` の marketplaces・plugins）と導入の置き場の版の控え。"""
    return _codex_marketplaces(conf) | _codex_plugins(conf, cached)


def diff(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """前後の控えの違い（鍵ごとに `<鍵>: <前> → <後>`。無い値は `無し`）。"""
    keys = sorted(set(before) | set(after))
    return [
        f"{k}: {before[k] if k in before else '無し'} → {after[k] if k in after else '無し'}"
        for k in keys
        if (before[k] if k in before else None) != (after[k] if k in after else None)
    ]
