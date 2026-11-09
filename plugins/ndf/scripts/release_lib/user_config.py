"""利用者の設定ディレクトリを読むだけで控える（`release-verification-steps.py` から分けた。#2401 の I6）。

Claude Code は CLAUDE_CONFIG_DIR か ~/.claude の plugins/installed_plugins.json と known_marketplaces.json、
Codex は CODEX_HOME か ~/.codex の config.toml と plugins/cache を読む。取得元の URL の利用者の情報は伏せる。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

Snapshot = NamedTuple("Snapshot", [("items", dict), ("unreadable", list)])  # 控えの鍵と値・読めないファイル（#2401）
_USERINFO = re.compile(r"([A-Za-z][\w+.-]*://)[^/@\s]+@")


def _mask(v):
    """取得元の URL の利用者の情報（`user:pass@`）を伏せる（#2401 の I2）。"""
    if isinstance(v, dict):
        return {k: _mask(x) for k, x in v.items()}
    return _USERINFO.sub(r"\1***@", v) if isinstance(v, str) else [_mask(x) for x in v] if isinstance(v, list) else v


def config_dirs() -> tuple[Path, Path]:
    """利用者の設定ディレクトリ: (CLAUDE_CONFIG_DIR か ~/.claude, CODEX_HOME か ~/.codex)。"""
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(claude), Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))


def _read_user_config(p: Path, bad: list, parse) -> dict:
    """設定のファイルを表で読む。無ければ空（導入していない）。読めない・形が違えば bad へ足して空。"""
    try:
        obj = parse(p) if p.exists() else {}
        if not isinstance(obj, dict):
            raise ValueError("表でない")
        return obj
    except (OSError, ValueError, ImportError) as e:  # ImportError: Python 3.10 に tomllib が無い
        bad.append(f"{p}: {e}")
        return {}


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _toml(p: Path):
    return __import__("tomllib").loads(p.read_text(encoding="utf-8"))


def _claude_row(r: dict) -> str:
    scope = str(r.get("scope", "-")) + ("" if r.get("scope", "user") == "user" else f"({r.get('projectPath', '-')})")
    return f"{scope}:{r.get('version', '-')}" + (f"@{str(r['gitCommitSha'])[:8]}" if r.get("gitCommitSha") else "")


def _claude_plugins(p: Path):
    rows = _json(p).get("plugins")
    if not isinstance(rows, dict) or not all(isinstance(x, list) and all(isinstance(r, dict) for r in x) for x in rows.values()):
        raise ValueError("plugins の形が違う")
    return {f"claude:plugin:{n}": " ".join(map(_claude_row, x)) for n, x in rows.items()}


def _codex_versions(cache: Path) -> dict:
    found = sorted(x for x in cache.glob("*/*") if x.is_dir())  # plugins/cache/<取得元>/<プラグイン>/<版>
    return {f"{p.name}@{p.parent.name}": ",".join(sorted(v.name for v in p.iterdir() if v.is_dir())) for p in found}


def user_config_snapshot() -> Snapshot:
    """利用者の設定ディレクトリを読むだけで控える。時刻・置き場所・autoUpdate と config.toml のほかの表は写さない（#2401）。"""
    claude, codex = config_dirs()
    bad = []
    items = _read_user_config(claude / "plugins" / "installed_plugins.json", bad, _claude_plugins)
    for n, m in _read_user_config(claude / "plugins" / "known_marketplaces.json", bad, _json).items():
        src = m.get("source") if isinstance(m, dict) else None
        items[f"claude:marketplace:{n}"] = json.dumps(_mask(src), sort_keys=True, ensure_ascii=False)
    conf = _read_user_config(codex / "config.toml", bad, _toml)
    markets, plugins = (x if isinstance(x, dict) else {} for x in (conf.get("marketplaces"), conf.get("plugins")))
    for n, m in markets.items():
        m = m if isinstance(m, dict) else {}
        items[f"codex:marketplace:{n}"] = json.dumps(_mask({k: m.get(k) for k in ("source_type", "source", "ref")}), sort_keys=True)
    versions = _read_user_config(codex / "plugins" / "cache", bad, _codex_versions)
    for n in set(versions) | set(plugins):
        en = plugins[n].get("enabled") if isinstance(plugins.get(n), dict) else None
        items[f"codex:plugin:{n}"] = f"enabled={'-' if en is None else str(en).lower()} versions={versions.get(n, '')}"
    return Snapshot(dict(sorted(items.items())), bad)
