"""使用量の帳簿: `claude -p` の 1 回の呼び出しごとに 1 行を追記する jsonl（#1142 の不足 f）。

置き場所は `<帳簿のディレクトリ>/<owner>__<repo>.jsonl`。帳簿のディレクトリは検査の記録
（`check-trigger.py`）と同じ順で決める:

1. `NDF_USAGE_DIR`（テストと、置き場所を明示したいとき）
2. `${CLAUDE_PLUGIN_DATA}/usage`
3. `${XDG_STATE_HOME}/ndf/usage`
4. `~/.local/state/ndf/usage`

**1 行 1 呼び出しの事象の記録にする。** 既存の行は書き換えない（I8）。合計は読む側が導く。
`usage` と `model_usage` は `claude -p` の結果の `usage` と `modelUsage` をそのまま持つ。5 分と 1 時間の
書き込みの区別は `usage.cache_creation` にしか無く、合計にした時点で戻らないためである。

ライブラリなので、標準ライブラリとライブラリだけを import する（I1）。
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

LIB = Path(__file__).resolve().parent
KINDS = ("work", "full", "judge", "slow", "pr", "mvv")
SOURCES = ("supervise", "mvv-gate")
UNKNOWN = "unknown"


def ledger_dir(env: Optional[dict] = None) -> Path:
    """帳簿のディレクトリ（モジュールの docstring の順）。"""
    env = os.environ if env is None else env
    if env.get("NDF_USAGE_DIR"):
        return Path(env["NDF_USAGE_DIR"])
    if env.get("CLAUDE_PLUGIN_DATA"):
        return Path(env["CLAUDE_PLUGIN_DATA"]) / "usage"
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf" / "usage"
    if env.get("HOME"):
        return Path(env["HOME"]) / ".local" / "state" / "ndf" / "usage"
    return Path(env.get("TMPDIR") or "/tmp") / "ndf-usage"


@functools.lru_cache(maxsize=32)
def repo_key(root: str) -> str:
    """origin の URL から `<owner>__<repo>`。決められなければ `unknown`。"""
    try:
        url = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=root,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}__{m.group(2)}" if m else UNKNOWN


@functools.lru_cache(maxsize=1)
def ndf_version() -> str:
    """このライブラリを持つプラグインの `plugin.json` の版。読めなければ `unknown`。"""
    try:
        data = json.loads((LIB.parents[1] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else UNKNOWN


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def main_model(model_usage: Optional[dict]) -> str:
    """`modelUsage` の中で費用（無ければ出力）の最も大きいモデル。無ければ空。"""
    if not isinstance(model_usage, dict) or not model_usage:
        return ""

    def weight(item: tuple) -> tuple:
        v = item[1] if isinstance(item[1], dict) else {}
        return (float(v.get("costUSD") or 0), int(v.get("outputTokens") or 0))

    return max(model_usage.items(), key=weight)[0]


@dataclass
class UsageRecord:
    """帳簿の 1 行。空を許さない列は `at`・`ndf_version`・`source`・`kind`・`usage`。"""
    source: str
    kind: str
    usage: dict = field(default_factory=dict)
    plan: str = ""
    step: str = ""
    model: str = ""
    model_usage: Optional[dict] = None
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    seconds: Optional[float] = None
    session_id: Optional[str] = None
    at: str = field(default_factory=now_utc)
    ndf_version: str = field(default_factory=ndf_version)

    def __post_init__(self) -> None:
        if not isinstance(self.usage, dict):
            self.usage = {}
        if not self.model:
            self.model = main_model(self.model_usage)

    @classmethod
    def from_claude(cls, data: dict, *, source: str, kind: str, plan: str = "", step: str = "",
                    seconds: Optional[float] = None) -> "UsageRecord":
        """`claude -p --output-format json` の結果の dict から作る。"""
        data = data if isinstance(data, dict) else {}
        mu = data.get("modelUsage")
        return cls(source=source, kind=kind, usage=data.get("usage") or {}, plan=plan, step=step,
                   model_usage=mu if isinstance(mu, dict) else None, cost_usd=data.get("total_cost_usd"),
                   turns=data.get("num_turns"), seconds=seconds, session_id=data.get("session_id"))

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


class UsageLedger:
    """1 つのリポジトリの帳簿。追記と読み取りだけを持つ（書き換えない）。"""

    def __init__(self, root: str | os.PathLike, directory: Optional[Path] = None):
        self.dir = Path(directory) if directory is not None else ledger_dir()
        self.path = self.dir / f"{repo_key(str(root))}.jsonl"

    def append(self, record: UsageRecord) -> Path:
        """1 行を追記する。失敗は OSError のまま投げる。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(record.to_json() + "\n")
        return self.path

    def records(self) -> Iterator[dict]:
        return read_rows(self.path)


def append_safely(root: str | os.PathLike, record: UsageRecord, directory: Optional[Path] = None) -> bool:
    """追記する。失敗しても投げず、stderr に 1 行を出して False を返す（I8。呼び出しの結果は返し続ける）。"""
    try:
        UsageLedger(root, directory).append(record)
        return True
    except Exception as e:  # noqa: BLE001  帳簿の失敗で呼び出しの結果を失わない
        print(f"usage_ledger: 使用量の帳簿へ追記できない: {e}", file=sys.stderr)
        return False


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


def read_all(directory: Optional[Path] = None) -> list[dict]:
    """帳簿のディレクトリのすべての行（ファイル名の順・ファイルの中は追記の順）。"""
    d = Path(directory) if directory is not None else ledger_dir()
    if not d.is_dir():
        return []
    return [r for p in sorted(d.glob("*.jsonl")) for r in read_rows(p)]
