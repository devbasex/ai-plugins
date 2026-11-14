"""hook の登録（`hooks/<ランタイム>.json`）を、ランタイムの契約表と予算から作る（#3345 の決定 88〜90・IH1〜IH3）。

    ndf hooks render [--check]

- 事象ごとに登録 1 行。事象はランタイムの契約表（`contracts/hooks/runtimes.json`）の並びで、どの登録にも予算
  （`limits.json` の `hook.budget.*`）の timeout を付ける。matcher は付けない（どの Tool の呼び出しでも起動し、guard の表が
  Tool で絞る）
- 登録の command は `SCRIPT` を `sh -c` で打つ。引数は環境の python・`hooks/entry.py`・ランタイム・事象（決定 89）
- 環境の python のパスは、`env.venv_python` と同じ置き場（プラグイン名と `uv.lock` の hash で決まる）を指す。キャッシュの根は
  起動の時点の `XDG_CACHE_HOME`（無ければ `$HOME/.cache`）をシェルが展開する
- プラグインの根は、ランタイムが hook の command へ渡す環境変数（契約表の `plugin_root_env`）をシェルが展開する

登録はプラグイン名と lock の hash を文字で持つ生成物である。名前か lock を変えたら作り直す（CI のテストが作り直した結果と
照らす）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ndf.domains.hooks import budget, env, tables

# 登録の command が `sh -c` で打つ文。$0 が環境の python、$1 が hooks/entry.py、その後ろがランタイムと事象
SCRIPT = '[ -x "$0" ] && exec "$0" "$@"; command -v python3 >/dev/null 2>&1 || exit 0; b="${1%/*}/boot.py"; shift; exec python3 "$b" "$@"'
# 環境の python の入口（プラグインの根から）
ENTRY = "hooks/entry.py"
# キャッシュの根（env.Ident の cache_home と同じ決め方を、起動の時点のシェルが展開する）
CACHE_HOME_WORD = "${XDG_CACHE_HOME:-$HOME/.cache}"


def path_of(runtime: str, plugin_root: Path = tables.PLUGIN_ROOT) -> Path:
    """ランタイムの登録のファイル。"""
    return plugin_root / "hooks" / f"{runtime}.json"


def python_word(plugin_root: Path) -> str:
    """環境の python のパス（シェルが展開する 1 語）。"""
    ident = env.Ident.of(plugin_root, environ={})
    rel = env.venv_python(ident).relative_to(ident.cache_home)
    return f'"{CACHE_HOME_WORD}/{rel.as_posix()}"'


def command(runtime: str, event: str, plugin_root: Path = tables.PLUGIN_ROOT) -> str:
    """事象 1 つの登録の command。"""
    root_word = "${" + tables.runtimes()[runtime]["plugin_root_env"] + "}"
    return f"sh -c '{SCRIPT}' {python_word(plugin_root)} \"{root_word}/{ENTRY}\" {runtime} {event}"


def render(runtime: str, plugin_root: Path = tables.PLUGIN_ROOT) -> dict[str, Any]:
    """ランタイムの登録の中身。予算の行が無い事象は `tables.TableError`。"""
    hooks: dict[str, Any] = {}
    for event in tables.runtimes()[runtime]["events"]:
        found = budget.budget_for(runtime, event)
        if found is None:
            raise tables.TableError(f"{runtime} の {event} の予算が limits.json に無い")
        entry = {"type": "command", "command": command(runtime, event, plugin_root), "timeout": found.timeout}
        hooks[event] = [{"hooks": [entry]}]
    return {"hooks": hooks}


def text(runtime: str, plugin_root: Path = tables.PLUGIN_ROOT) -> str:
    """登録のファイルに書く文字（JSON・字下げ 2・末尾の改行）。"""
    return json.dumps(render(runtime, plugin_root), ensure_ascii=False, indent=2) + "\n"


def stale(plugin_root: Path = tables.PLUGIN_ROOT) -> list[Path]:
    """作り直した結果と違う（か無い）登録のファイル。"""
    found = []
    for runtime in tables.runtimes():
        path = path_of(runtime, plugin_root)
        if not path.is_file() or path.read_text(encoding="utf-8") != text(runtime, plugin_root):
            found.append(path)
    return found


def write(plugin_root: Path = tables.PLUGIN_ROOT) -> list[Path]:
    """違う登録のファイルを作り直し、書いたファイルを返す。"""
    written = stale(plugin_root)
    for path in written:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text(path.stem, plugin_root), encoding="utf-8")
    return written
