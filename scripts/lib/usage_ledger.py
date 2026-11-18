"""使用量の帳簿を読む部品（#1142・#1953）。帳簿は NDF が起動したエージェントの呼び出し 1 回ごとに 1 行を追記する jsonl である。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の集計（`scripts/token-usage.py`・
`scripts/measure/claude-p-usage.py`）が使う読み取りだけを持ち、書き込みはプラグインの側に残る。

帳簿はランタイムごとに分ける（claude・codex・kiro・agy・jev）。置き場所は `<帳簿のディレクトリ>/<runtime>/<owner>__<repo>.jsonl`。
帳簿のディレクトリは次の順で決める:

1. `NDF_USAGE_DIR`（テストと、置き場所を明示したいとき）
2. `${CLAUDE_PLUGIN_DATA}/usage`
3. `<状態の根>/usage`（根は `user_home`）

帳簿のディレクトリの直下の `<owner>__<repo>.jsonl` は版 1（#1142）の行で、読むときに claude の行とみなす。
使用量を取れなかった行は `usage` が `"unknown"` である。
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterator, Mapping, Optional


RUNTIMES = ("claude", "codex", "kiro", "agy", "jev")  # 帳簿のランタイム（cross-review の席順の ALL_RUNTIMES とは別）
UNKNOWN = "unknown"
GIT_TIMEOUT = 10  # origin の URL を読む git の上限（秒。10 系の上限の表の `usage-ledger.git` と同じ値）


def user_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """状態の根（`NDF_STATE_HOME` → `$XDG_STATE_HOME/ndf` → `~/.claude/ndf/state`）。空の値は無いものとする。"""
    env = os.environ if env is None else env
    if env.get("NDF_STATE_HOME"):
        return Path(env["NDF_STATE_HOME"])
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf"
    return Path(env.get("HOME") or Path.home()) / ".claude" / "ndf" / "state"


def ledger_dir(env: Optional[dict] = None) -> Path:
    """帳簿のディレクトリ（モジュールの docstring の順）。"""
    env = os.environ if env is None else env
    if env.get("NDF_USAGE_DIR"):
        return Path(env["NDF_USAGE_DIR"])
    if env.get("CLAUDE_PLUGIN_DATA"):
        return Path(env["CLAUDE_PLUGIN_DATA"]) / "usage"
    if env.get("NDF_STATE_HOME") or env.get("XDG_STATE_HOME") or env.get("HOME"):
        return user_home(env) / "usage"
    return Path(env.get("TMPDIR") or "/tmp") / "ndf-usage"


@functools.lru_cache(maxsize=32)
def repo_key(root: str) -> str:
    """origin の URL から `<owner>__<repo>`。決められなければ `unknown`。"""
    try:
        url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}__{m.group(2)}" if m else UNKNOWN


def cache_writes(usage: dict) -> tuple[int, int]:
    """usage の書き込みを (5 分, 1 時間) に分ける。内訳が無ければ全部を 5 分に数える。"""
    usage = usage if isinstance(usage, dict) else {}
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    w1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    if "ephemeral_5m_input_tokens" in cc:
        w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
    else:
        w5 = max(0, int(usage.get("cache_creation_input_tokens") or 0) - w1h)
    return w5, w1h


def known_usage(usage) -> object:
    """使用量を行の形にする。dict で空でなければそのまま、ほかは `"unknown"`（0 を書かない。I3）。"""
    return usage if isinstance(usage, dict) and usage else UNKNOWN


def read_rows(path: Path) -> Iterator[dict]:
    """帳簿の 1 ファイルを読む。壊れた行と dict でない行は飛ばす。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def as_v2(row: dict, runtime: str) -> dict:
    """読んだ行を版 2 の形にそろえる。版 1 の行（`schema` が無い）は claude の `ok` の行とみなし、`{}` は `"unknown"`。"""
    out = dict(row)
    out.setdefault("schema", 1)
    out.setdefault("runtime", runtime)
    out.setdefault("status", "ok")
    out["usage"] = known_usage(out.get("usage"))
    return out


def ledger_files(runtime: str, directory: Optional[Path] = None, repo: Optional[str] = None) -> list[Path]:
    """1 つの帳簿のファイル。`repo`（`<owner>__<repo>`）を渡せばそのリポジトリだけ。claude は版 1 の置き場（直下）も含む。"""
    d = Path(directory) if directory is not None else ledger_dir()
    pattern = f"{repo}.jsonl" if repo else "*.jsonl"
    files = sorted((d / runtime).glob(pattern)) if (d / runtime).is_dir() else []
    if runtime == "claude" and d.is_dir():
        files = sorted(d.glob(pattern)) + files
    return files


def read_usage_ledger(runtime: str = "claude", directory: Optional[Path] = None, repo: Optional[str] = None) -> list[dict]:
    """1 つの帳簿のすべての行（版 2 の形にそろえる）。別のランタイムの行は読まない（I7）。"""
    if runtime not in RUNTIMES:
        raise ValueError(f"帳簿の無いランタイム: {runtime}")
    return [as_v2(r, runtime) for p in ledger_files(runtime, directory, repo) for r in read_rows(p)]
